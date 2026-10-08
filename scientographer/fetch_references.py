# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fetch the citations between the corpus's papers (params.yaml ``fetch_references``).

For when the exports have no usable reference lists (PubMed's have none): each
paper's references are looked up by DOI in OpenAlex (https://openalex.org, CC0),
``dois_per_request`` DOIs per request (at most 100). A reference counts when it
is another paper of the corpus, which is all the citation graph uses.

What was fetched is kept in a store (``store_dir``), DOI by DOI, so a re-run
asks only for papers that are new, and a run stopped by the daily quota resumes
where it stopped. OpenAlex gives every user a daily budget: enough without a key
for about 100,000 papers a day; a free API key (an openalex.org account) raises it
tenfold. Put the key in the project's ``.env`` under the name ``api_key_env``
gives (OPENALEX_API_KEY). Papers OpenAlex does not know, or knows without
references, are counted in the report: their citations are missing from the map.

Outputs:
  citation_network.references_table   citing_doi, cited_dois (corpus papers only)
  <store_dir>/works.parquet           per DOI: found, OpenAlex id, referenced works
  <store_dir>/report.json             papers asked, found, with references, citations
"""

from collections.abc import Callable
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
import pandas as pd

from scientographer._http import HttpError, RateLimiter, request_json
from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("fetch_references")
_network = params("citation_network")
PAPERS_TABLE: Final[Path] = Path(_network["papers_table"])
REFERENCES_TABLE: Final[Path] = Path(_network["references_table"])
SOURCE: Final[str] = str(_cfg.get("source", "openalex"))
EMAIL: Final[str] = str(_cfg.get("email") or "")
API_KEY_ENV: Final[str] = str(_cfg.get("api_key_env") or "OPENALEX_API_KEY")
STORE_DIR: Final[Path] = Path(_cfg.get("store_dir", "data/references_store"))
DOIS_PER_REQUEST: Final[int] = min(100, int(_cfg.get("dois_per_request", 100)))

OPENALEX: Final[str] = "https://api.openalex.org/works"
STORE_FILE: Final[str] = "works.parquet"
STORE_COLUMNS: Final[list[str]] = ["doi", "found", "openalex_id", "referenced_works"]


#####################
##  Aux Functions  ##
#####################
def _normalize_doi(value) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        text = text.removeprefix(prefix)
    return text


def _short_id(openalex_id: str) -> str:
    return str(openalex_id).rsplit("/", 1)[-1]


def _parse_works(dois: list[str], results: list[dict]) -> list[dict]:
    """One store row per DOI asked: found or not, its OpenAlex id and references.
    A DOI OpenAlex lists more than once keeps the record with most references."""
    by_doi: dict[str, dict] = {}
    for work in results:
        doi = _normalize_doi(work.get("doi"))
        refs = [_short_id(w) for w in work.get("referenced_works") or []]
        if doi and (doi not in by_doi or len(refs) > len(by_doi[doi]["referenced_works"])):
            by_doi[doi] = {"doi": doi, "found": True, "openalex_id": _short_id(work.get("id", "")),
                           "referenced_works": refs}
    return [by_doi.get(d, {"doi": d, "found": False, "openalex_id": "", "referenced_works": []}) for d in dois]


def _openalex_get(url: str, query: dict, limiter: RateLimiter) -> dict:
    try:
        return request_json(url, params=query, limiter=limiter, give_up_on=(401, 403, 404))
    except HttpError as err:
        if err.status == 429:
            raise RuntimeError(
                "OpenAlex's daily budget is used up. What was fetched is kept: run again tomorrow, or "
                f"add a free API key as {API_KEY_ENV} in .env to raise the budget tenfold.") from err
        if err.status in (401, 403):
            raise RuntimeError(f"OpenAlex rejected the API key in {API_KEY_ENV}: check it, or remove it "
                               "to run within the keyless budget.") from err
        raise


def _fetch_openalex(dois: list[str], email: str, api_key: str, per_request: int,
                    save: Callable[[list[dict]], None]) -> list[dict]:
    """Store rows for `dois`, `per_request` at a time; `save` is called after each
    request, so a stop keeps what was fetched. A DOI with a comma or a pipe (which
    the filter syntax reserves) is looked up on its own."""
    limiter = RateLimiter(8.0)
    identity = {k: v for k, v in (("mailto", email), ("api_key", api_key)) if v}
    select = "id,doi,referenced_works"
    batchable = [d for d in dois if "," not in d and "|" not in d]
    single = [d for d in dois if d not in set(batchable)]
    rows: list[dict] = []
    for start in range(0, len(batchable), per_request):
        batch = batchable[start:start + per_request]
        results, page = [], 1
        while True:
            answer = _openalex_get(OPENALEX, {"filter": "doi:" + "|".join(batch), "select": select,
                                              "per-page": 100, "page": page, **identity}, limiter)
            results += answer.get("results", [])
            if len(results) >= answer.get("meta", {}).get("count", 0) or not answer.get("results"):
                break
            page += 1
        new = _parse_works(batch, results)
        rows.extend(new)
        save(new)
        logger.info("OpenAlex: %d of %d papers looked up", min(start + per_request, len(batchable)), len(dois))
    for doi in single:
        try:
            work = _openalex_get(f"{OPENALEX}/https://doi.org/{doi}", {"select": select, **identity}, limiter)
            new = _parse_works([doi], [work])
        except HttpError as err:
            if err.status != 404:
                raise
            new = _parse_works([doi], [])
        rows.extend(new)
        save(new)
    return rows


def _references(store: pd.DataFrame) -> pd.DataFrame:
    """citing_doi, cited_dois: each found paper's references that are corpus papers."""
    found = store[store["found"]]
    doi_of = dict(zip(found["openalex_id"], found["doi"]))
    rows = []
    for doi, refs in zip(found["doi"], found["referenced_works"]):
        cited = sorted({doi_of[w] for w in refs if w in doi_of and doi_of[w] != doi})
        rows.append({"citing_doi": doi, "cited_dois": cited})
    return pd.DataFrame(rows, columns=["citing_doi", "cited_dois"])


def _load_store(store_dir: Path) -> pd.DataFrame:
    path = Path(store_dir) / STORE_FILE
    if not path.exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    store = pd.read_parquet(path)
    store["referenced_works"] = store["referenced_works"].map(list)
    return store


def _save_store(store: pd.DataFrame, store_dir: Path) -> None:
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    tmp = store_dir / f".{STORE_FILE}.tmp"
    store.to_parquet(tmp, index=False)
    os.replace(tmp, store_dir / STORE_FILE)


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, STORE_DIR, REFERENCES_TABLE.parent)
    inputs = dict(papers_table_path=PAPERS_TABLE, references_table_path=REFERENCES_TABLE, source=SOURCE,
                  email=EMAIL, store_dir=STORE_DIR, dois_per_request=DOIS_PER_REQUEST)
    outputs = ["save_references_table"]
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
def corpus_dois(papers_table_path: Path) -> list[str]:
    papers = pd.read_parquet(papers_table_path) if Path(papers_table_path).suffix == ".parquet" \
        else pd.read_csv(papers_table_path)
    return sorted({d for d in papers["doi"].map(_normalize_doi) if d})


def works(corpus_dois: list[str], source: str, email: str, store_dir: Path, dois_per_request: int) -> pd.DataFrame:
    """The store rows of the corpus's papers, fetching those not stored yet."""
    if source != "openalex":
        raise ValueError(f"fetch_references.source: only 'openalex' is supported, not {source!r}")
    store = _load_store(store_dir)
    missing = sorted(set(corpus_dois) - set(store["doi"]))
    logger.info("%d papers: %d already looked up, %d to look up in OpenAlex",
                len(corpus_dois), len(corpus_dois) - len(missing), len(missing))
    if missing:
        if not email and not os.getenv(API_KEY_ENV):
            logger.info("tip: set fetch_references.email (OpenAlex's polite pool) or an API key")
        fetched: list[dict] = []

        def save(new_rows: list[dict]) -> None:
            fetched.extend(new_rows)
            _save_store(pd.concat([store, pd.DataFrame(fetched, columns=STORE_COLUMNS)], ignore_index=True),
                        store_dir)

        _fetch_openalex(missing, email, os.getenv(API_KEY_ENV, ""), dois_per_request, save)
        store = _load_store(store_dir)
    return store[store["doi"].isin(set(corpus_dois))].reset_index(drop=True)


def references_table(works: pd.DataFrame) -> pd.DataFrame:
    table = _references(works)
    found = int(works["found"].sum())
    with_refs = int(works["referenced_works"].map(len).gt(0).sum())
    citations = int(table["cited_dois"].map(len).sum())
    logger.info("OpenAlex knows %d of %d papers, %d of them with references; %d citations within the corpus",
                found, len(works), with_refs, citations)
    return table


@datasaver()
def save_references_table(references_table: pd.DataFrame, works: pd.DataFrame, references_table_path: Path,
                          store_dir: Path) -> dict:
    path = Path(references_table_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    references_table.to_parquet(path, index=False)
    report = {
        "source": "openalex",
        "papers": len(works),
        "found": int(works["found"].sum()),
        "found_with_references": int(works["referenced_works"].map(len).gt(0).sum()),
        "citations_within_corpus": int(references_table["cited_dois"].map(len).sum()),
        "papers_citing_another_corpus_paper": int(references_table["cited_dois"].map(len).gt(0).sum()),
    }
    Path(store_dir).mkdir(parents=True, exist_ok=True)
    (Path(store_dir) / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return utils.get_file_metadata(path)


if __name__ == "__main__":
    sys.exit(_main())
