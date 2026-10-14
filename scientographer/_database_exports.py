# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bibliographic database exports other than MEDLINE: Scopus CSV, Web of Science
tab-delimited and EBSCO CSV. Each reader gives one papers-table row per record.

The field choices follow the motor-learning study these readers come from:

- Scopus ("Export > CSV", all fields): authors from "Author full names" (the
  Scopus author ids in brackets removed), keywords from "Author Keywords" (the
  authors' own; "Index Keywords", Elsevier's controlled terms, are kept in
  their own column), journal from "Source title".
- Web of Science ("Export > Tab delimited file", e.g. ``savedrecs.txt``): a header
  line of two-letter field tags, then one record per line, fields separated by
  tabs. Quotes are ordinary characters, not CSV quoting. Authors from AF (full
  names; AU, the short form, when AF is absent, as in BIOSIS and KCI exports),
  keywords from DE (the authors' own; Keywords Plus, ID, in its own column),
  journal from SO, year from PY, DOI from DI. A line with more fields than the
  header (a stray tab inside a field) cannot be split reliably and is skipped,
  as pandas does.
- EBSCO (Academic Search Ultimate and other EBSCOhost databases, CSV export):
  authors from "contributors", keywords from "subjects" (EBSCO's subject terms,
  the only keywords the export has), journal from "source", year from the first
  four digits of "publicationDate".

Lists in every format are separated by "; " (EBSCO pads it as " ; "); the parts
are stripped. DOIs are left as given here; `ingest` lower-cases them and removes
``https://doi.org/``-style prefixes for every format.
"""

from pathlib import Path
import re

import pandas as pd

_BRACKETED = re.compile(r" \(.*?\)")  # " (57200000000)" after a Scopus name, " (a@b.org)" in BIOSIS
_HTML_TAG = re.compile(r"<(.*?)>")  # some Web of Science keywords carry tags such as <i>
_YEAR = re.compile(r"\s*(\d{4})")


def split_list(value: str, remove: re.Pattern | None = None) -> list[str]:
    """ "a; b ; c" -> ["a", "b", "c"]: split on "; ", parts stripped, empty parts
    dropped. `remove` (a pattern) is deleted from the text first."""
    text = remove.sub("", value) if remove is not None else value
    return [part.strip() for part in text.split("; ") if part.strip()]


def split_keywords(value: str, remove: re.Pattern | None = None) -> list[str]:
    """`split_list` with repeated keywords dropped (two authors may share a name;
    a keyword listed twice is one keyword)."""
    return list(dict.fromkeys(split_list(value, remove)))


def year(value: str):
    """The year from "2020", "20250801" or "2019-2020", or None."""
    match = _YEAR.match(value or "")
    return int(match.group(1)) if match else None


def _check_columns(table: pd.DataFrame, required: tuple[str, ...], fmt: str, path: Path) -> None:
    missing = [c for c in required if c not in table.columns]
    if missing:
        raise ValueError(f"{path}: not a {fmt} export, columns {missing} are missing "
                         f"(found {list(table.columns)[:8]}...)")


def _read_csv(path: Path) -> pd.DataFrame:
    """Every cell as text, empty cells as ''. utf-8-sig drops the byte order mark."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def read_scopus(path: Path) -> pd.DataFrame:
    """A Scopus CSV export, one row per record."""
    table = _read_csv(path)
    _check_columns(table, ("Title", "DOI"), "Scopus CSV", path)
    column = lambda name: table[name] if name in table else pd.Series("", index=table.index)
    names = column("Author full names").where(column("Author full names") != "", column("Authors"))
    return pd.DataFrame({
        "doi": column("DOI"),
        "scopus_id": column("EID"),
        "pmid": column("PubMed ID"),
        "title": column("Title"),
        "abstract": column("Abstract"),
        "authors": [split_list(v, _BRACKETED) for v in names],
        "keywords": [split_keywords(v) for v in column("Author Keywords")],
        "index_keywords": [split_keywords(v) for v in column("Index Keywords")],
        "journal": column("Source title"),
        "year": [year(v) for v in column("Year")],
        "document_type": column("Document Type"),
    })


def web_of_science_records(text: str) -> tuple[pd.DataFrame, int]:
    """(one row per record with the field tags as columns, lines skipped). A line with
    fewer fields than the header is padded with empty ones; one with more is skipped."""
    lines = text.lstrip("﻿").split("\n")
    header = lines[0].rstrip("\r").split("\t")
    rows, skipped = [], 0
    for line in lines[1:]:
        line = line.rstrip("\r")
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) > len(header):
            skipped += 1
            continue
        rows.append(fields + [""] * (len(header) - len(fields)))
    table = pd.DataFrame(rows, columns=header)
    return table.loc[:, [c for c in table.columns if c]], skipped


def read_web_of_science(path: Path) -> pd.DataFrame:
    """A Web of Science tab-delimited export, one row per record. The number of
    lines that could not be read is in ``attrs["unreadable_records"]``."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        table, skipped = web_of_science_records(f.read())
    _check_columns(table, ("TI", "DI"), "Web of Science tab-delimited", path)
    column = lambda tag: table[tag] if tag in table else pd.Series("", index=table.index)
    names = column("AF").where(column("AF") != "", column("AU"))
    papers = pd.DataFrame({
        "doi": column("DI"),
        "wos_id": column("UT"),
        "pmid": column("PM"),
        "title": column("TI"),
        "abstract": column("AB"),
        "authors": [split_list(v, _BRACKETED) for v in names],
        "keywords": [split_keywords(v, _HTML_TAG) for v in column("DE")],
        "keywords_plus": [split_keywords(v, _HTML_TAG) for v in column("ID")],
        "journal": column("SO"),
        "year": [year(v) for v in column("PY")],
        "document_type": column("DT"),
    })
    papers.attrs["unreadable_records"] = skipped
    return papers


def read_ebsco(path: Path) -> pd.DataFrame:
    """An EBSCOhost CSV export (e.g. Academic Search Ultimate), one row per record."""
    table = _read_csv(path)
    _check_columns(table, ("title", "doi", "an"), "EBSCO CSV", path)
    column = lambda name: table[name] if name in table else pd.Series("", index=table.index)
    return pd.DataFrame({
        "doi": column("doi"),
        "ebsco_id": column("an"),
        "title": column("title"),
        "abstract": column("abstract"),
        "authors": [split_list(v) for v in column("contributors")],
        "keywords": [split_keywords(v) for v in column("subjects")],
        "journal": column("source"),
        "year": [year(v) for v in column("publicationDate")],
        "document_type": column("docTypes"),
        "database": column("longDBName"),
    })
