# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the citation graph from two tables: the papers and their references.

This is where a corpus enters Scientographer. Whatever databases the papers came
from, bring them to two tables (parquet or CSV):

- **papers** -- one row per paper, with a ``doi`` column and any metadata you want
  on the map. The stages downstream use ``title``, ``authors``, ``keywords``,
  ``abstract``, ``journal`` and ``year`` when present. ``authors`` and
  ``keywords`` may be lists or ``|``-separated strings.
- **references** -- one row per citing paper: ``citing_doi`` and ``cited_dois``
  (a list, or a ``|``-separated string).

The graph keeps only citations between two papers of the corpus, drops papers
with no such citation, and keeps the largest weakly connected component
(community detection needs a connected graph). DOIs are lower-cased on both
sides. Every column of the papers table becomes a vertex attribute; the DOI is
the vertex ``name``, which is how every later stage matches papers.

Output: ``citation_network.output_graphml`` from params.yaml, the input of
``detect_communities``.
"""

import logging
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import pandas as pd

from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("citation_network")
PAPERS_TABLE: Final[Path] = Path(_cfg["papers_table"])
REFERENCES_TABLE: Final[Path] = Path(_cfg["references_table"])
OUTPUT_GRAPHML: Final[Path] = Path(_cfg["output_graphml"])
LIST_SEPARATOR: Final[str] = str(_cfg.get("list_separator", "|"))
KEEP_GIANT_COMPONENT: Final[bool] = bool(_cfg.get("keep_giant_component", True))


#####################
##  Aux Functions  ##
#####################
def _read_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in (".csv", ".tsv"):
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    raise ValueError(f"{path}: use a .parquet, .csv or .tsv table")


def _as_list(value, separator: str) -> list[str]:
    """A list-like cell (list, tuple, numpy array, or separated string) as a list
    of stripped, non-empty strings."""
    if value is None:
        return []
    if isinstance(value, float) and pd.isna(value):
        return []
    if isinstance(value, str):
        items = value.split(separator)
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    return [str(item).strip() for item in items if item is not None and str(item).strip()]


def _is_list_like(value) -> bool:
    """A list cell (Python list or tuple, or the NumPy array Parquet gives back)."""
    return isinstance(value, (list, tuple)) or (hasattr(value, "ndim") and getattr(value, "ndim", 0) == 1)


def _normalize_doi(doi) -> str:
    return str(doi).strip().lower() if doi is not None and not (isinstance(doi, float) and pd.isna(doi)) else ""


def _citation_edges(references: pd.DataFrame, valid_dois: set[str], separator: str) -> list[tuple[str, str]]:
    """(citing, cited) pairs with both ends in the corpus; duplicates and self-citations dropped."""
    edges = set()
    for citing, cited_dois in zip(references["citing_doi"], references["cited_dois"]):
        citing = _normalize_doi(citing)
        if citing not in valid_dois:
            continue
        for cited in _as_list(cited_dois, separator):
            cited = _normalize_doi(cited)
            if cited in valid_dois and cited != citing:
                edges.add((citing, cited))
    return sorted(edges)


def _build_graph(
    papers: pd.DataFrame, edges: list[tuple[str, str]], separator: str, keep_giant_component: bool
) -> ig.Graph:
    papers = papers.assign(doi=papers["doi"].map(_normalize_doi))
    papers = papers[papers["doi"] != ""].drop_duplicates("doi").set_index("doi")
    dois = sorted(papers.index)
    index = {doi: i for i, doi in enumerate(dois)}

    graph = ig.Graph(directed=True)
    graph.add_vertices(len(dois))
    graph.vs["name"] = dois
    graph.add_edges([(index[a], index[b]) for a, b in edges])

    graph.delete_vertices(graph.vs.select(_degree=0))
    if keep_giant_component and graph.vcount():
        graph = graph.connected_components(mode="weak").giant()

    rows = papers.loc[graph.vs["name"]]
    for column in rows.columns:
        values = rows[column].tolist()
        if column in ("authors", "keywords") or any(_is_list_like(v) for v in values):
            values = [separator.join(_as_list(v, separator)) for v in values]
        elif pd.api.types.is_object_dtype(rows[column]) or pd.api.types.is_string_dtype(rows[column]):
            # GraphML string columns must not mix str and NaN (igraph drops them).
            # pandas 3 gives text columns the "str" dtype rather than object.
            values = ["" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v) for v in values]
        graph.vs[column] = values
    return graph


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_GRAPHML.parent)
    inputs = dict(
        papers_table_path=PAPERS_TABLE,
        references_table_path=REFERENCES_TABLE,
        list_separator=LIST_SEPARATOR,
        keep_giant_component=KEEP_GIANT_COMPONENT,
        output_graphml_path=OUTPUT_GRAPHML,
    )
    outputs = ["save_citation_network"]

    import __main__

    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))
        .build()
    )
    dr.validate_execution(outputs, inputs=inputs)
    draw_dag(dr, CURRENT_FILE_NAME, outputs, inputs)
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@dataloader()
def papers(papers_table_path: Path) -> tuple[pd.DataFrame, dict]:
    table = _read_table(papers_table_path)
    if "doi" not in table.columns:
        raise ValueError(f"{papers_table_path} needs a 'doi' column; has {list(table.columns)}")
    return table, utils.get_file_metadata(papers_table_path)


@dataloader()
def references(references_table_path: Path) -> tuple[pd.DataFrame, dict]:
    table = _read_table(references_table_path)
    missing = {"citing_doi", "cited_dois"} - set(table.columns)
    if missing:
        raise ValueError(f"{references_table_path} is missing columns {sorted(missing)}")
    return table, utils.get_file_metadata(references_table_path)


def valid_dois(papers: pd.DataFrame) -> set[str]:
    return {d for d in papers["doi"].map(_normalize_doi) if d}


def citation_edges(references: pd.DataFrame, valid_dois: set[str], list_separator: str) -> list[tuple[str, str]]:
    edges = _citation_edges(references, valid_dois, list_separator)
    logger.info("%d citations between %d corpus papers", len(edges), len(valid_dois))
    return edges


def citation_network(
    papers: pd.DataFrame, citation_edges: list[tuple[str, str]], list_separator: str, keep_giant_component: bool
) -> ig.Graph:
    graph = _build_graph(papers, citation_edges, list_separator, keep_giant_component)
    logger.info("citation network: %d vertices, %d edges", graph.vcount(), graph.ecount())
    return graph


@datasaver()
def save_citation_network(citation_network: ig.Graph, output_graphml_path: Path) -> dict:
    Path(output_graphml_path).parent.mkdir(parents=True, exist_ok=True)
    citation_network.write_graphml(str(output_graphml_path))
    return utils.get_file_metadata(output_graphml_path)


if __name__ == "__main__":
    sys.exit(_main())
