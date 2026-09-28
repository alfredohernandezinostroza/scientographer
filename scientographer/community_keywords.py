"""Name every Leiden/CPM community by its most distinguishing keywords, and write
the names onto the graph.

Generalises ``neighbor_community_keywords.py`` (which did this for the neighbor
graph only) to ANY graphml carrying ``cpm_communities_at_res=<r>`` vertex columns:
the resolutions are read from the graph, community sizes are counted from the
assignment itself (no ``community_size_at_res`` column needed), and the graph is
otherwise left untouched.

Method -- the "modified TF-IDF" of ``find_keywords_per_cluster_noverlap.py``:
each community's synonym-canonicalised keyword list is one document; a
``TfidfVectorizer`` is fit per resolution; a corrected IDF strips sklearn's ``+1``
smoothing constant; the top-N terms by corrected score become the community's
label. Only communities with at least ``MIN_COMMUNITY_SIZE`` papers get a label
(the same cutoff the website uses to decide which communities are worth naming).

Outputs (``data/graph_level_data/community_keywords/``):

  citation_network_with_community_keywords.graphml
      the input graph plus one ``top_keywords_at_res=<r>`` string column per
      resolution (the label broadcast to every member; "" for unlabelled).
      Per-vertex rather than graph-level because Gephi drops graph-level
      attributes on import.
  community_keywords_per_resolution.parquet
      one row per (resolution, community_id): size + label. Human-readable.
  community_keyword_scores.parquet
      long form, one row per (resolution, community_id, keyword): the corrected
      TF-IDF score and rank, top ``TOP_N_SCORED_KEYWORDS`` per community. This is
      what the website draws its per-community keyword bars from.

Gotcha carried over from the neighbor version: igraph's GraphML writer turns a
Python ``None`` in a string column into the literal text "None", and a column
mixing ``str`` and ``float`` (``NaN``) is silently dropped on write -- so missing
labels are always the empty string.
"""

import json
import logging
from pathlib import Path
import re
import sys
from typing import Final, Optional

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import numpy as np
import pandas as pd
import scipy.sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from motor_learning_network.constants import (
    FIGURES_PATH,
    RAW_DATA_PATH,
    params,
    tracker_adapters,
)

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

# Every knob comes from params.yaml `community_keywords` (one value, one place).
_cfg = params("community_keywords")
SYNONYMS_THRESHOLD: Final[float] = float(_cfg["synonyms_threshold"])
TFIDF_NORM: Final[str] = str(_cfg["tfidf_norm"])
IDF_BIAS: Final[float] = float(_cfg["idf_bias"])
TOP_N_KEYWORDS: Final[int] = int(_cfg["top_n_keywords"])  # terms that make up the label
TOP_N_SCORED_KEYWORDS: Final[int] = int(_cfg["top_n_scored_keywords"])  # terms kept with scores
KEYWORD_DIVIDING_CHARACTER: Final[str] = str(_cfg["keyword_dividing_character"])
MIN_COMMUNITY_SIZE: Final[int] = int(_cfg["min_community_size"])
REQUESTED_RESOLUTIONS: Final[Optional[list[float]]] = (
    [float(r) for r in _cfg["resolutions"]] if _cfg.get("resolutions") else None
)

COMMUNITY_ATTRIBUTE_PREFIX: Final[str] = "cpm_communities_at_res="
LABEL_ATTRIBUTE_PREFIX: Final[str] = "top_keywords_at_res="

OUTPUT_DIR: Final[Path] = Path(params("graph")["analysis_output_dir"]) / "community_keywords"
INPUT_GRAPHML: Final[Path] = Path(_cfg["input_graphml"])
SYNONYM_DICT_PATH: Final[Path] = (
    RAW_DATA_PATH / f"keyword_synonyms_{SYNONYMS_THRESHOLD}_with_transitivity.json"
)
OUTPUT_GRAPHML: Final[Path] = OUTPUT_DIR / "citation_network_with_community_keywords.graphml"
COMMUNITY_KEYWORDS_PARQUET: Final[Path] = OUTPUT_DIR / "community_keywords_per_resolution.parquet"
COMMUNITY_KEYWORD_SCORES_PARQUET: Final[Path] = OUTPUT_DIR / "community_keyword_scores.parquet"


#####################
##  Aux Functions  ##
#####################
def _normalize_keyword(keyword: str) -> str:
    return keyword.lower()


def community_attribute_name(resolution: float) -> str:
    return f"{COMMUNITY_ATTRIBUTE_PREFIX}{resolution}"


def label_attribute_name(resolution: float) -> str:
    return f"{LABEL_ATTRIBUTE_PREFIX}{resolution}"


def _resolutions_in_graph(graph: ig.Graph) -> list[float]:
    """Every resolution the graph carries a community assignment for, ascending,
    parsed from the ``cpm_communities_at_res=<r>`` vertex attribute names."""
    found = []
    for name in graph.vs.attributes():
        if name.startswith(COMMUNITY_ATTRIBUTE_PREFIX):
            try:
                found.append(float(name[len(COMMUNITY_ATTRIBUTE_PREFIX) :]))
            except ValueError:
                continue
    return sorted(set(found))


def _community_id(value) -> Optional[int]:
    """Community ids come back from GraphML as float ("5.0"), int or str; NaN /
    None / negative means "no community"."""
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(as_float) or as_float < 0:
        return None
    return int(as_float)


def _split_keyword_field(value, keyword_dividing_character: str) -> list[str]:
    """A raw keyword field -> clean terms: split on the dividing character, then
    on stray ``&`` / ``,`` separators (matches the original script's splitting)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    terms = []
    for chunk in str(value).split(keyword_dividing_character):
        for part in re.split(r"\s*[&,]\s*", chunk):
            part = part.strip()
            if part:
                terms.append(part)
    return terms


def _correct_tfidf(
    X: scipy.sparse.csr_matrix, vectorizer: TfidfVectorizer, idf_bias: float
) -> scipy.sparse.csr_matrix:
    """Strip sklearn's ``+1`` IDF smoothing constant (see
    find_keywords_per_cluster_noverlap.py's ``_correct_tfidf``)."""
    X_array = X.toarray()
    wrong_idf = vectorizer.idf_
    corrected_idf = wrong_idf - 1.0 + idf_bias
    tf = np.divide(X_array, wrong_idf)
    return scipy.sparse.csr_matrix(np.multiply(tf, corrected_idf))


def _build_synonym_map(synonym_dict: dict) -> dict[str, str]:
    canonical_map: dict[str, str] = {}
    for key, values in synonym_dict.items():
        canonical_name = _normalize_keyword(key)
        for variant in [key] + list(values):
            norm_variant = _normalize_keyword(variant)
            if norm_variant not in canonical_map:
                canonical_map[norm_variant] = canonical_name
    return canonical_map


def _community_sizes(graph: ig.Graph, resolution: float) -> dict[int, int]:
    sizes: dict[int, int] = {}
    for value in graph.vs[community_attribute_name(resolution)]:
        community_id = _community_id(value)
        if community_id is not None:
            sizes[community_id] = sizes.get(community_id, 0) + 1
    return sizes


def _filtered_keywords_df(
    graph: ig.Graph,
    resolution: float,
    keyword_dividing_character: str,
    min_community_size: int,
) -> pd.DataFrame:
    """One row per vertex in a community of at least ``min_community_size``
    members, with its keyword list already split into clean terms."""
    sizes = _community_sizes(graph, resolution)
    keywords = (
        graph.vs["keywords"] if "keywords" in graph.vs.attributes() else [""] * graph.vcount()
    )
    rows = []
    for raw_keywords, raw_community in zip(
        keywords, graph.vs[community_attribute_name(resolution)]
    ):
        community_id = _community_id(raw_community)
        if community_id is None or sizes[community_id] < min_community_size:
            continue
        rows.append(
            {
                "keywords": _split_keyword_field(raw_keywords, keyword_dividing_character),
                "community_id": community_id,
                "community_size": sizes[community_id],
            }
        )
    return pd.DataFrame(rows, columns=["keywords", "community_id", "community_size"])


def _canonical_corpus(filtered_keywords_df: pd.DataFrame, synonym_map: dict) -> dict[int, str]:
    """{community_id: "kw1\\tkw2\\t..."} -- every raw keyword rewritten to its
    canonical synonym, then joined per community into one tab-separated
    document for TF-IDF."""
    corpus: dict[int, list[str]] = {}
    for keywords, community_id in zip(
        filtered_keywords_df["keywords"], filtered_keywords_df["community_id"]
    ):
        bucket = corpus.setdefault(int(community_id), [])
        for raw_term in keywords:
            norm_term = _normalize_keyword(raw_term)
            bucket.append(synonym_map.get(norm_term, norm_term))
    return {community_id: "\t".join(terms) for community_id, terms in sorted(corpus.items())}


def _scored_keywords_per_community(
    corpus: dict[int, str], top_n: int, tfidf_norm: str = TFIDF_NORM, idf_bias: float = IDF_BIAS
) -> dict[int, list[tuple[str, float]]]:
    """{community_id: [(keyword, corrected_tfidf), ...]} ranked, top-N per
    community, empty-document communities dropped."""
    community_ids = [community_id for community_id, doc in corpus.items() if doc]
    documents = [corpus[community_id] for community_id in community_ids]
    if not documents:
        return {}

    vectorizer = TfidfVectorizer(
        tokenizer=lambda x: x.split("\t"), token_pattern=None, lowercase=False, norm=tfidf_norm
    )
    X = vectorizer.fit_transform(documents)
    X = _correct_tfidf(X, vectorizer, idf_bias)
    feature_names = vectorizer.get_feature_names_out()

    result: dict[int, list[tuple[str, float]]] = {}
    for i, community_id in enumerate(community_ids):
        scores = pd.Series(X[i].toarray().flatten(), index=feature_names)
        scores = scores[scores > 0].sort_values(ascending=False).head(top_n)
        result[community_id] = [(str(keyword), float(score)) for keyword, score in scores.items()]
    return result


def _labels_from_scores(
    scored: dict[int, list[tuple[str, float]]], top_n_label: int
) -> dict[int, str]:
    """{community_id: "Keyword A; Keyword B; Keyword C"}."""
    return {
        community_id: "; ".join(keyword.title() for keyword, _ in ranked[:top_n_label])
        for community_id, ranked in scored.items()
        if ranked
    }


def _attach_top_keywords(
    graph: ig.Graph, resolution: float, labels_by_community: dict[int, str]
) -> ig.Graph:
    """Broadcast each community's label to every member vertex; "" (never None)
    where there is no label."""
    labels = []
    for value in graph.vs[community_attribute_name(resolution)]:
        community_id = _community_id(value)
        labels.append(
            labels_by_community.get(community_id, "") if community_id is not None else ""
        )
    graph.vs[label_attribute_name(resolution)] = labels
    return graph


def _community_keywords_df(
    graph: ig.Graph, labels_per_resolution: dict[float, dict[int, str]]
) -> pd.DataFrame:
    rows = []
    for resolution, labels_by_community in labels_per_resolution.items():
        sizes = _community_sizes(graph, resolution)
        for community_id, label in sorted(labels_by_community.items()):
            rows.append(
                {
                    "resolution": resolution,
                    "community_id": community_id,
                    "community_size": sizes.get(community_id),
                    "top_keywords": label,
                }
            )
    return pd.DataFrame(
        rows, columns=["resolution", "community_id", "community_size", "top_keywords"]
    )


def _keyword_scores_df(
    scored_per_resolution: dict[float, dict[int, list[tuple[str, float]]]],
) -> pd.DataFrame:
    rows = []
    for resolution, scored in scored_per_resolution.items():
        for community_id, ranked in sorted(scored.items()):
            for rank, (keyword, score) in enumerate(ranked, start=1):
                rows.append(
                    {
                        "resolution": resolution,
                        "community_id": community_id,
                        "rank": rank,
                        "keyword": keyword,
                        "corrected_tfidf_score": score,
                    }
                )
    return pd.DataFrame(
        rows, columns=["resolution", "community_id", "rank", "keyword", "corrected_tfidf_score"]
    )


##################
##     Main     ##
##################
def _main() -> int:
    inputs = dict(
        input_graphml_path=INPUT_GRAPHML,
        synonym_dict_path=SYNONYM_DICT_PATH,
        requested_resolutions=REQUESTED_RESOLUTIONS,  # None = every resolution the graph carries
        keyword_dividing_character=KEYWORD_DIVIDING_CHARACTER,
        min_community_size=MIN_COMMUNITY_SIZE,
        top_n_keywords=TOP_N_KEYWORDS,
        top_n_scored_keywords=TOP_N_SCORED_KEYWORDS,
        tfidf_norm=TFIDF_NORM,
        idf_bias=IDF_BIAS,
        output_graphml_path=OUTPUT_GRAPHML,
        community_keywords_parquet_path=COMMUNITY_KEYWORDS_PARQUET,
        community_keyword_scores_parquet_path=COMMUNITY_KEYWORD_SCORES_PARQUET,
    )
    outputs = [
        "save_graph_with_community_keywords",
        "save_community_keywords_parquet",
        "save_community_keyword_scores_parquet",
    ]

    import __main__

    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
        .build()
    )

    dr.validate_execution(outputs, inputs=inputs)
    dr.display_all_functions(
        FIGURES_PATH / f"{CURRENT_FILE_NAME}_all_functions.png",
        keep_dot=True,
        deduplicate_inputs=True,
    )
    dr.visualize_execution(
        outputs,
        inputs=inputs,
        output_file_path=FIGURES_PATH / f"{CURRENT_FILE_NAME}.png",
        keep_dot=False,
        deduplicate_inputs=True,
    )
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@dataloader()
def citation_network(input_graphml_path: Path) -> tuple[ig.Graph, dict]:
    graph = ig.Graph.Read_GraphML(str(input_graphml_path))
    logger.info(
        "read %s: %d vertices, %d edges", input_graphml_path, graph.vcount(), graph.ecount()
    )
    return graph, utils.get_file_metadata(input_graphml_path)


@dataloader()
def synonym_dict(synonym_dict_path: Path) -> tuple[dict, dict]:
    """The keyword synonym groups ({canonical: [variants]}). Optional: a corpus
    without one gets no canonicalisation (every keyword is its own term).
    Applies the same hard-coded Purkinje Cell alias patch as
    find_keywords_per_cluster_noverlap.py's `synonym_dict` node when that entry
    exists, so the study's file behaves exactly as before."""
    synonym_dict_path = Path(synonym_dict_path)
    if not synonym_dict_path.exists():
        logger.warning("no synonym file at %s; keywords are used verbatim", synonym_dict_path)
        return {}, {"path": str(synonym_dict_path), "exists": False}
    with open(synonym_dict_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "Purkinje Cell" in data:
        data["Purkinje Cell"].extend(["Purkinje Cell ( PC )"])
    return data, utils.get_file_metadata(synonym_dict_path)


def synonym_map(synonym_dict: dict) -> dict[str, str]:
    mapping = _build_synonym_map(synonym_dict)
    logger.info("built synonym map with %d variant entries", len(mapping))
    return mapping


def resolutions(
    citation_network: ig.Graph, requested_resolutions: Optional[list[float]]
) -> list[float]:
    """The resolutions to label: every one the graph carries, or the requested
    subset of those (a requested resolution the graph lacks is an error --
    silently skipping it is how mismatched outputs happen)."""
    available = _resolutions_in_graph(citation_network)
    if not available:
        raise ValueError(f"no '{COMMUNITY_ATTRIBUTE_PREFIX}*' vertex attributes on the graph")
    if requested_resolutions is None:
        return available
    missing = sorted(set(requested_resolutions) - set(available))
    if missing:
        raise ValueError(
            f"requested resolutions not on the graph: {missing}; available: {available}"
        )
    return sorted(requested_resolutions)


def scored_keywords_per_resolution(
    citation_network: ig.Graph,
    synonym_map: dict[str, str],
    resolutions: list[float],
    keyword_dividing_character: str,
    min_community_size: int,
    top_n_scored_keywords: int,
    tfidf_norm: str,
    idf_bias: float,
) -> dict[float, dict[int, list[tuple[str, float]]]]:
    """Ranked (keyword, corrected TF-IDF) lists per community, per resolution.
    A plain loop rather than a @parameterize fan-out because the resolution
    list is read from the graph at run time, not fixed at import."""
    result = {}
    for resolution in resolutions:
        df = _filtered_keywords_df(
            citation_network, resolution, keyword_dividing_character, min_community_size
        )
        corpus = _canonical_corpus(df, synonym_map)
        result[resolution] = _scored_keywords_per_community(
            corpus, top_n_scored_keywords, tfidf_norm, idf_bias
        )
        logger.info(
            "[res=%s] %d vertices in %d communities >= %d members; %d labelled",
            resolution,
            len(df),
            df["community_id"].nunique() if len(df) else 0,
            min_community_size,
            len(result[resolution]),
        )
    return result


def labels_per_resolution(
    scored_keywords_per_resolution: dict[float, dict[int, list[tuple[str, float]]]],
    top_n_keywords: int,
) -> dict[float, dict[int, str]]:
    return {
        resolution: _labels_from_scores(scored, top_n_keywords)
        for resolution, scored in scored_keywords_per_resolution.items()
    }


def citation_network_with_keywords(
    citation_network: ig.Graph, labels_per_resolution: dict[float, dict[int, str]]
) -> ig.Graph:
    for resolution, labels_by_community in labels_per_resolution.items():
        citation_network = _attach_top_keywords(citation_network, resolution, labels_by_community)
    return citation_network


def community_keywords_df(
    citation_network: ig.Graph, labels_per_resolution: dict[float, dict[int, str]]
) -> pd.DataFrame:
    return _community_keywords_df(citation_network, labels_per_resolution)


def keyword_scores_df(
    scored_keywords_per_resolution: dict[float, dict[int, list[tuple[str, float]]]],
) -> pd.DataFrame:
    return _keyword_scores_df(scored_keywords_per_resolution)


@datasaver()
def save_graph_with_community_keywords(
    citation_network_with_keywords: ig.Graph, output_graphml_path: Path
) -> dict:
    Path(output_graphml_path).parent.mkdir(parents=True, exist_ok=True)
    citation_network_with_keywords.write_graphml(str(output_graphml_path))
    return utils.get_file_metadata(output_graphml_path)


@datasaver()
def save_community_keywords_parquet(
    community_keywords_df: pd.DataFrame, community_keywords_parquet_path: Path
) -> dict:
    Path(community_keywords_parquet_path).parent.mkdir(parents=True, exist_ok=True)
    community_keywords_df.to_parquet(community_keywords_parquet_path, index=False)
    return utils.get_file_metadata(community_keywords_parquet_path)


@datasaver()
def save_community_keyword_scores_parquet(
    keyword_scores_df: pd.DataFrame, community_keyword_scores_parquet_path: Path
) -> dict:
    Path(community_keyword_scores_parquet_path).parent.mkdir(parents=True, exist_ok=True)
    keyword_scores_df.to_parquet(community_keyword_scores_parquet_path, index=False)
    return utils.get_file_metadata(community_keyword_scores_parquet_path)


if __name__ == "__main__":
    sys.exit(_main())
