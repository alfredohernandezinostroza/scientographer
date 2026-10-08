# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search PubMed and download the matching records (params.yaml ``pubmed_search``).

The quickest way to a corpus: a PubMed query in, MEDLINE records out, ready for
``ingest``. Uses NCBI's E-utilities (https://www.ncbi.nlm.nih.gov/books/NBK25497/):

- **Search** with ESearch. ESearch returns at most 10,000 PMIDs per query, so a
  larger result is collected by splitting the query by publication date (halving
  the date range until every part fits). A query matching more than
  ``max_records`` papers stops with an error instead of keeping an arbitrary
  subset, which would bias the map: narrow the query or raise the limit.
- **Download** the records with EFetch in MEDLINE format, ``batch_size`` PMIDs per
  request. Records already downloaded are kept, so re-running after widening the
  query downloads only the new ones; records no longer matched are dropped.

NCBI allows 3 requests per second, 10 with an API key (free, from an NCBI
account): put it in the project's ``.env`` under the name ``api_key_env`` gives
(NCBI_API_KEY). NCBI also asks for a contact ``email``.

Outputs (``output_dir``):
  records.txt    the MEDLINE records, one per PMID matched
  search.json    the query, when it ran, how many PMIDs matched, and those PMIDs
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import datasaver
from hamilton.io import utils
import hamilton.log_setup

from scientographer import _medline
from scientographer._http import RateLimiter, request, request_json
from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("pubmed_search")
QUERY: Final[str] = str(_cfg.get("query") or "")
MAX_RECORDS: Final[int] = int(_cfg.get("max_records") or 20000)
EMAIL: Final[str] = str(_cfg.get("email") or "")
API_KEY_ENV: Final[str] = str(_cfg.get("api_key_env") or "NCBI_API_KEY")
OUTPUT_DIR: Final[Path] = Path(_cfg.get("output_dir", "data/raw/pubmed"))
BATCH_SIZE: Final[int] = int(_cfg.get("batch_size", 500))

EUTILS: Final[str] = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_LIMIT: Final[int] = 9999       # ESearch returns at most this many ids per query
EARLIEST: Final[date] = date(1800, 1, 1)
RECORDS_FILE: Final[str] = "records.txt"
SEARCH_FILE: Final[str] = "search.json"


#####################
##  Aux Functions  ##
#####################
def _dated(query: str, start: date, end: date) -> str:
    return f'({query}) AND ("{start:%Y/%m/%d}"[dp] : "{end:%Y/%m/%d}"[dp])'


def _collect_pmids(query: str, search: Callable[[str, int], tuple[int, list[str]]],
                   start: date = EARLIEST, end: date | None = None) -> list[str]:
    """Every PMID the query matches. `search(term, retmax)` returns (count, ids).
    A query over the ESearch limit is split by publication date, recursively."""
    count, ids = search(query, ESEARCH_LIMIT)
    if count <= ESEARCH_LIMIT:
        return ids
    return _split(query, search, start, end or date.today())


def _split(query: str, search, start: date, end: date) -> list[str]:
    count, ids = search(_dated(query, start, end), ESEARCH_LIMIT)
    if count <= ESEARCH_LIMIT:
        return ids
    if start == end:
        raise RuntimeError(f"{count} records were published on {start}, more than one search can return; "
                           "narrow the query")
    middle = date.fromordinal((start.toordinal() + end.toordinal()) // 2)
    return _split(query, search, start, middle) + _split(query, search, date.fromordinal(middle.toordinal() + 1), end)


def _identity(email: str) -> dict:
    out = {"tool": "scientographer"}
    if email:
        out["email"] = email
    api_key = os.getenv(API_KEY_ENV)
    if api_key:
        out["api_key"] = api_key
    return out


def _search_function(identity: dict, limiter: RateLimiter):
    def search(term: str, retmax: int) -> tuple[int, list[str]]:
        result = request_json(f"{EUTILS}/esearch.fcgi", data={
            "db": "pubmed", "term": term, "retmax": retmax, "retmode": "json", **identity}, limiter=limiter)
        body = result["esearchresult"]
        if "ERROR" in body:
            raise RuntimeError(f"PubMed rejected the query: {body['ERROR']}")
        return int(body["count"]), list(body.get("idlist", []))
    return search


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_DIR)
    inputs = dict(query=QUERY, max_records=MAX_RECORDS, email=EMAIL, output_dir=OUTPUT_DIR, batch_size=BATCH_SIZE)
    outputs = ["save_pubmed_records"]
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
def eutils_identity(email: str) -> dict:
    if not email:
        logger.warning("pubmed_search.email is empty: NCBI asks for a contact address with every request")
    return _identity(email)


def eutils_limiter(eutils_identity: dict) -> RateLimiter:
    return RateLimiter(9.0 if "api_key" in eutils_identity else 2.5)


def matched_pmids(query: str, max_records: int, eutils_identity: dict, eutils_limiter: RateLimiter) -> list[str]:
    if not query.strip():
        raise ValueError("pubmed_search.query is empty: set it in params.yaml "
                         "(PubMed syntax, e.g. '\"motor learning\"[tiab] AND 2015:2025[dp]')")
    search = _search_function(eutils_identity, eutils_limiter)
    count, _ = search(query, 0)
    logger.info("PubMed: %d records match %s", count, query)
    if count > max_records:
        raise RuntimeError(f"the query matches {count} PubMed records, more than pubmed_search.max_records "
                           f"({max_records}); narrow the query or raise max_records")
    pmids = sorted(set(_collect_pmids(query, search)), key=int)
    if len(pmids) != count:
        logger.warning("collected %d PMIDs for a count of %d (records without a publication date are "
                       "missed when a search is split by date)", len(pmids), count)
    return pmids


def pubmed_records(matched_pmids: list[str], output_dir: Path, batch_size: int,
                   eutils_identity: dict, eutils_limiter: RateLimiter) -> dict[str, str]:
    """{pmid: MEDLINE record}: those already downloaded, plus the missing ones."""
    path = Path(output_dir) / RECORDS_FILE
    have = _medline.split_by_pmid(path.read_text(encoding="utf-8")) if path.exists() else {}
    wanted = set(matched_pmids)
    records = {p: r for p, r in have.items() if p in wanted}
    missing = [p for p in matched_pmids if p not in records]
    logger.info("%d records already downloaded, %d to download", len(records), len(missing))
    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        text = request(f"{EUTILS}/efetch.fcgi", data={
            "db": "pubmed", "id": ",".join(batch), "rettype": "medline", "retmode": "text", **eutils_identity},
            limiter=eutils_limiter)
        records.update({p: r for p, r in _medline.split_by_pmid(text).items() if p in wanted})
        logger.info("downloaded %d of %d", min(start + batch_size, len(missing)), len(missing))
    unavailable = wanted - set(records)
    if unavailable:
        logger.warning("%d PMIDs returned no record (withdrawn or not yet available)", len(unavailable))
    return records


@datasaver()
def save_pubmed_records(pubmed_records: dict[str, str], matched_pmids: list[str], query: str,
                        output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / RECORDS_FILE
    tmp = output_dir / f".{RECORDS_FILE}.tmp"
    tmp.write_text("\n".join(pubmed_records[p] for p in sorted(pubmed_records, key=int)), encoding="utf-8")
    os.replace(tmp, path)
    (output_dir / SEARCH_FILE).write_text(json.dumps({
        "query": query,
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matched": len(matched_pmids),
        "downloaded": len(pubmed_records),
        "pmids": matched_pmids,
    }, indent=1), encoding="utf-8")
    logger.info("%d MEDLINE records in %s", len(pubmed_records), path)
    return utils.get_file_metadata(path)


if __name__ == "__main__":
    sys.exit(_main())
