# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read database exports into the papers table (params.yaml ``ingest``).

Each entry of ``ingest.sources`` names a ``format`` and a ``path`` (a file, a
directory or a glob). Records are merged across sources by DOI: the first source
listing a paper gives each field, later sources fill the fields it left empty.
Records without a DOI cannot be matched to citations, so they are left out and
listed in the report.

Formats:

- ``medline``: PubMed's MEDLINE text (what ``search_pubmed`` downloads, and
  PubMed's "Save > Format: PubMed" export). Keywords per ``keywords_from``:
  ``author`` (the authors' own keywords), ``mesh`` (MeSH major topics) or
  ``author_then_mesh`` (the default: the authors' keywords, else the MeSH major
  topics, since many PubMed records have no author keywords).

Outputs:
  citation_network.papers_table   doi, title, abstract, authors, keywords, journal,
                                  year, plus the format's own columns (pmid, mesh, ...)
                                  and `sources`
  <report_dir>/report.json        records read per source, left out (no DOI),
                                  duplicates merged, papers written
  <report_dir>/without_doi.csv    the records left out for lacking a DOI
"""

from collections.abc import Callable
import glob
import json
import logging
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import datasaver
from hamilton.io import utils
import hamilton.log_setup
import pandas as pd

from scientographer import _medline
from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("ingest")
SOURCES: Final[list[dict]] = list(_cfg.get("sources") or [])
KEYWORDS_FROM: Final[str] = str(_cfg.get("keywords_from", "author_then_mesh"))
REPORT_DIR: Final[Path] = Path(_cfg.get("report_dir", "data/ingest"))
PAPERS_TABLE: Final[Path] = Path(params("citation_network")["papers_table"])


#####################
##  Aux Functions  ##
#####################
def _read_medline(path: Path, keywords_from: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8-sig") as f:
        rows = [_medline.to_paper(record, keywords_from) for record in _medline.records(f)]
    return pd.DataFrame(rows)


# format -> (reader(path, keywords_from) -> one row per record, file patterns in a directory)
READERS: Final[dict[str, tuple[Callable[[Path, str], pd.DataFrame], tuple[str, ...]]]] = {
    "medline": (_read_medline, ("*.txt", "*.nbib", "*.medline")),
}


def _files(path: str, patterns: tuple[str, ...]) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted({f for pattern in patterns for f in p.glob(pattern) if f.is_file()})
    return sorted(Path(f) for f in glob.glob(path) if Path(f).is_file())


def _read_sources(sources: list[dict], keywords_from: str) -> tuple[list[pd.DataFrame], list[dict]]:
    frames, report = [], []
    for i, source in enumerate(sources):
        fmt = source.get("format")
        if fmt not in READERS:
            raise ValueError(f"ingest.sources[{i}]: format must be one of {sorted(READERS)}, not {fmt!r}")
        reader, patterns = READERS[fmt]
        files = _files(str(source.get("path", "")), patterns)
        if not files:
            raise FileNotFoundError(f"ingest.sources[{i}]: no {fmt} files at {source.get('path')!r}")
        for file in files:
            table = reader(file, keywords_from)
            table["sources"] = [[f"{fmt}:{file.name}"] for _ in range(len(table))]
            if "doi" not in table:
                table["doi"] = ""
            table["doi"] = table["doi"].fillna("").astype(str).str.strip().str.lower()
            frames.append(table)
            report.append({"format": fmt, "file": str(file), "records": len(table),
                           "without_doi": int((table["doi"] == "").sum())})
            logger.info("%s: %d records (%d without a DOI)", file, len(table), report[-1]["without_doi"])
    return frames, report


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return pd.isna(value)
    if isinstance(value, (str, list, tuple)):
        return len(value) == 0
    return False


def _merge_by_doi(table: pd.DataFrame) -> pd.DataFrame:
    """One row per DOI: the first record's fields, empty ones filled from later
    records, and the union of their sources."""
    merged: dict[str, dict] = {}
    for row in table.to_dict("records"):
        current = merged.get(row["doi"])
        if current is None:
            merged[row["doi"]] = dict(row)
            continue
        for column, value in row.items():
            if column == "sources":
                current["sources"] = current["sources"] + [s for s in value if s not in current["sources"]]
            elif _is_empty(current.get(column)) and not _is_empty(value):
                current[column] = value
    return pd.DataFrame(list(merged.values()), columns=table.columns)


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, REPORT_DIR, PAPERS_TABLE.parent)
    inputs = dict(sources=SOURCES, keywords_from=KEYWORDS_FROM, report_dir=REPORT_DIR,
                  papers_table_path=PAPERS_TABLE)
    outputs = ["save_papers_table", "save_ingest_report"]
    import __main__
    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
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
def read_sources(sources: list, keywords_from: str) -> dict:
    if not sources:
        raise ValueError("ingest.sources is empty: list the exports to read in params.yaml")
    frames, report = _read_sources(sources, keywords_from)
    return {"table": pd.concat(frames, ignore_index=True), "files": report}


def papers_table(read_sources: dict) -> pd.DataFrame:
    table = read_sources["table"]
    with_doi = table[table["doi"] != ""]
    papers = _merge_by_doi(with_doi)
    logger.info("%d records with a DOI -> %d papers (%d duplicates merged)",
                len(with_doi), len(papers), len(with_doi) - len(papers))
    return papers


def ingest_report(read_sources: dict, papers_table: pd.DataFrame) -> dict:
    table = read_sources["table"]
    with_doi = int((table["doi"] != "").sum())
    return {
        "files": read_sources["files"],
        "records": len(table),
        "records_without_doi": len(table) - with_doi,
        "duplicates_merged": with_doi - len(papers_table),
        "papers": len(papers_table),
    }


@datasaver()
def save_papers_table(papers_table: pd.DataFrame, papers_table_path: Path) -> dict:
    path = Path(papers_table_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    papers_table.to_parquet(path, index=False)
    return utils.get_file_metadata(path)


@datasaver()
def save_ingest_report(ingest_report: dict, read_sources: dict, report_dir: Path) -> dict:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(json.dumps(ingest_report, indent=1), encoding="utf-8")
    table = read_sources["table"]
    columns = [c for c in ("pmid", "title", "year", "journal", "sources") if c in table.columns]
    table.loc[table["doi"] == "", columns].to_csv(report_dir / "without_doi.csv", index=False)
    logger.info("ingest: %s", {k: v for k, v in ingest_report.items() if k != "files"})
    return utils.get_file_metadata(report_dir / "report.json")


if __name__ == "__main__":
    sys.exit(_main())
