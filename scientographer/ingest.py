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
- ``scopus``: Scopus's CSV export (all fields). Keywords: the authors' own
  ("Author Keywords"); Scopus's index keywords are kept as ``index_keywords``.
- ``wos``: Web of Science's tab-delimited export (``savedrecs.txt``; a folder of
  them, as Web of Science exports at most 1,000 records per file). Keywords: the
  authors' own (DE); Keywords Plus are kept as ``keywords_plus``.
- ``ebsco``: an EBSCOhost CSV export (e.g. Academic Search Ultimate). Keywords:
  EBSCO's subject terms.

DOIs are lower-cased and stripped of ``https://doi.org/`` and ``doi:`` prefixes; a
field holding two DOIs joined by "; " (as Web of Science sometimes writes) keeps the
first. A journal name ending in its ISSN in brackets, "Behavioral Sciences
(2076-328X)" (as EBSCO writes), loses it; the ISSN goes to an ``issn`` column.
`keywords_from` applies to ``medline`` only. Field choices for the other formats
are described in ``_database_exports``.

Outputs:
  citation_network.papers_table   doi, title, abstract, authors, keywords, journal,
                                  year, plus the formats' own columns (pmid, mesh,
                                  scopus_id, wos_id, ebsco_id, ...) and `sources`
  <report_dir>/report.json        records read per source, left out (no DOI, or a
                                  Web of Science line that could not be read),
                                  duplicates merged, papers written
  <report_dir>/without_doi.csv    the records left out for lacking a DOI
"""

from collections.abc import Callable
import glob
import json
import logging
from pathlib import Path
import re
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import datasaver
from hamilton.io import utils
import hamilton.log_setup
import pandas as pd

from scientographer import _database_exports, _medline
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


def _read_scopus(path: Path, keywords_from: str) -> pd.DataFrame:
    return _database_exports.read_scopus(path)


def _read_web_of_science(path: Path, keywords_from: str) -> pd.DataFrame:
    return _database_exports.read_web_of_science(path)


def _read_ebsco(path: Path, keywords_from: str) -> pd.DataFrame:
    return _database_exports.read_ebsco(path)


# format -> (reader(path, keywords_from) -> one row per record, file patterns in a directory)
READERS: Final[dict[str, tuple[Callable[[Path, str], pd.DataFrame], tuple[str, ...]]]] = {
    "medline": (_read_medline, ("*.txt", "*.nbib", "*.medline")),
    "scopus": (_read_scopus, ("*.csv",)),
    "wos": (_read_web_of_science, ("*.txt",)),
    "ebsco": (_read_ebsco, ("*.csv",)),
}
_DOI_PREFIXES: Final[tuple[str, ...]] = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                                         "http://dx.doi.org/", "doi:")


# Two DOIs in one field are joined by a semicolon AND a space; a semicolon alone can
# be part of a DOI (old Wiley ones: "10.1002/(sici)1097-0177(199601)205:1<1::aid-aja1>3.0.co;2-#").
_DOI_SEPARATOR: Final[re.Pattern] = re.compile(r";\s+")
# A journal name that ends in its ISSN in brackets: "Behavioral Sciences (2076-328X)".
_TRAILING_ISSN: Final[re.Pattern] = re.compile(r"\s*\((\d{4}-\d{3}[\dXx])\)\s*$")


def _normalize_doi(doi: str) -> str:
    """Lower-cased, without a resolver or ``doi:`` prefix: "https://doi.org/10.1/AB" -> "10.1/ab".
    Of two DOIs joined by "; ", the first."""
    doi = _DOI_SEPARATOR.split(doi.strip())[0].strip().lower()
    for prefix in _DOI_PREFIXES:
        doi = doi.removeprefix(prefix)
    return doi.strip()


def _split_trailing_issn(journal: str) -> tuple[str, str]:
    """("Behavioral Sciences (2076-328X)") -> ("Behavioral Sciences", "2076-328X"); a name
    without a trailing ISSN is returned unchanged, with an empty ISSN."""
    match = _TRAILING_ISSN.search(journal)
    if not match:
        return journal, ""
    return journal[: match.start()].strip(), match.group(1).upper()


def _without_trailing_issn(table: pd.DataFrame) -> pd.DataFrame:
    """Move an ISSN written at the end of the journal name to an ``issn`` column (kept
    when the format already gives one)."""
    if "journal" not in table:
        return table
    split = [_split_trailing_issn(j) if isinstance(j, str) else (j, "") for j in table["journal"]]
    table["journal"] = [name for name, _ in split]
    issns = [issn for _, issn in split]
    if any(issns):
        existing = table["issn"].fillna("").astype(str) if "issn" in table else pd.Series([""] * len(table), index=table.index)
        table["issn"] = [old or new for old, new in zip(existing, issns)]
    return table


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
            table["doi"] = [_normalize_doi(doi) for doi in table["doi"].fillna("").astype(str)]
            table = _without_trailing_issn(table)
            frames.append(table)
            report.append({"format": fmt, "file": str(file), "records": len(table),
                           "without_doi": int((table["doi"] == "").sum())})
            logger.info("%s: %d records (%d without a DOI)", file, len(table), report[-1]["without_doi"])
            unreadable = int(table.attrs.get("unreadable_records", 0))
            if unreadable:
                report[-1]["unreadable_records"] = unreadable
                logger.warning("%s: %d lines could not be read (more fields than the header) and are left out",
                               file, unreadable)
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
        "unreadable_records": sum(f.get("unreadable_records", 0) for f in read_sources["files"]),
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
    columns = [c for c in ("pmid", "scopus_id", "wos_id", "ebsco_id", "title", "year", "journal", "sources")
               if c in table.columns]
    table.loc[table["doi"] == "", columns].to_csv(report_dir / "without_doi.csv", index=False)
    logger.info("ingest: %s", {k: v for k, v in ingest_report.items() if k != "files"})
    return utils.get_file_metadata(report_dir / "report.json")


if __name__ == "__main__":
    sys.exit(_main())
