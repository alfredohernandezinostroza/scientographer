# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Scopus, Web of Science and EBSCO readers of `ingest`. The records are made
up; only the column headers and field tags are those of the real exports."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest
import yaml

from scientographer import _database_exports

SCOPUS_HEADER = [
    "Authors", "Author full names", "Author(s) ID", "Title", "Year", "Source title", "Volume", "Issue",
    "Art. No.", "Page start", "Page end", "Cited by", "DOI", "Link", "Affiliations", "Authors with affiliations",
    "Abstract", "Author Keywords", "Index Keywords", "Molecular Sequence Numbers", "Chemicals/CAS", "Tradenames",
    "Manufacturers", "Funding Details", "Funding Texts", "References", "Correspondence Address", "Editors",
    "Publisher", "Sponsors", "Conference name", "Conference date", "Conference location", "Conference code",
    "ISSN", "ISBN", "CODEN", "PubMed ID", "Language of Original Document", "Abbreviated Source Title",
    "Document Type", "Publication Stage", "Open Access", "Source", "EID",
]
# Web of Science Core Collection's tab-delimited header ...
WOS_CORE_TAGS = [
    "PT", "AU", "BA", "BE", "GP", "AF", "BF", "CA", "TI", "SO", "SE", "BS", "LA", "DT", "CT", "CY", "CL",
    "SP", "HO", "DE", "ID", "AB", "C1", "C3", "RP", "EM", "RI", "OI", "FU", "FP", "FX", "CR", "NR", "TC",
    "Z9", "U1", "U2", "PU", "PI", "PA", "SN", "EI", "BN", "J9", "JI", "PD", "PY", "VL", "IS", "PN", "SU",
    "SI", "MA", "BP", "EP", "AR", "DI", "DL", "D2", "EA", "PG", "WC", "WE", "SC", "GA", "PM", "OA", "HC",
    "HP", "DA", "UT",
]
# ... and BIOSIS Citation Index's: no AF (full names) and no DE (author keywords).
WOS_BIOSIS_TAGS = [
    "PT", "PM", "DT", "LT", "TI", "FT", "AU", "CA", "BA", "BE", "PN", "AE", "DG", "PC", "PL", "SO", "SE",
    "PY", "PD", "VL", "IS", "PS", "BP", "EP", "LA", "ME", "CT", "CL", "CY", "SP", "AB", "C1", "EM", "RI",
    "OI", "U1", "U2", "CR", "NR", "TC", "Z9", "PU", "PA", "SC", "SN", "EI", "DI", "BN", "MC", "CC", "TA",
    "DS", "CH", "GN", "SD", "GE", "TM", "MQ", "PR", "MI", "OA", "HC", "HP", "DA", "UT",
]
EBSCO_HEADER = [
    "longDBName", "shortDBName", "an", "title", "abstract", "publicationDate", "contributors", "docTypes",
    "pubTypes", "coverDate", "peerReviewed", "source", "subjects", "isiType", "doids", "isbns", "issns",
    "publisherLocations", "identifiers", "isOpenAccess", "bookEdition", "language", "publisher", "pageEnd",
    "pageStart", "pageCount", "volume", "issue", "doi", "notes", "mid", "degreeLevel", "plink",
]


def write_csv(path, header, records):
    """A CSV export: every field quoted, as Scopus writes it, with a byte order mark."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for record in records:
            writer.writerow({column: record.get(column, "") for column in header})


def write_wos(path, tags, records, extra_lines=()):
    """A Web of Science tab-delimited file: BOM, CRLF line ends, quotes as plain text."""
    lines = ["\t".join(tags)] + ["\t".join(record.get(tag, "") for tag in tags) for record in records]
    path.write_bytes(("﻿" + "\r\n".join(list(lines) + list(extra_lines)) + "\r\n").encode("utf-8"))


SCOPUS_RECORDS = [
    {"Authors": "Doe J.; Roe R.", "Author full names": "Doe, Jane (11111111111); Roe, Rick (22222222222)",
     "Title": "Practice schedules and retention", "Year": "2021", "Source title": "Journal of Made-Up Studies",
     "DOI": "10.1000/ABC.1", "Abstract": "An invented abstract.",
     "Author Keywords": "contextual interference; retention; retention", "Index Keywords": "Humans; Learning",
     "PubMed ID": "12345", "Document Type": "Article", "Source": "Scopus", "EID": "2-s2.0-000000001"},
    {"Authors": "Poe E.", "Author full names": "", "Title": "A record without a DOI", "Year": "2019",
     "Source title": "Imaginary Letters", "DOI": "", "Source": "Scopus", "EID": "2-s2.0-000000002"},
    {"Author full names": "Moe, Max (33333333333)", "Title": "A DOI given as a link", "Year": "2020",
     "DOI": "https://doi.org/10.1000/Link.3", "Author Keywords": "", "Source": "Scopus", "EID": "2-s2.0-000000003"},
]

WOS_RECORDS = [
    {"PT": "J", "AU": "Doe, J; Roe, R", "AF": "Doe, Jane; Roe, Rick", "TI": "PRACTICE SCHEDULES AND RETENTION",
     "SO": "JOURNAL OF MADE-UP STUDIES", "DT": "Article", "DE": "contextual interference; <i>retention</i>",
     "ID": "MOTOR; SKILL", "AB": 'An invented "abstract" with quotes.', "PY": "2021", "DI": "10.1000/abc.1",
     "PM": "12345", "UT": "WOS:000000000000001"},
    {"PT": "J", "AU": "Lo, K", "AF": "Lo, Kim", "TI": "Second file record", "SO": "FICTIONAL REVIEWS",
     "PY": "2018", "DI": "", "UT": "WOS:000000000000002"},
]


def test_scopus_reader_maps_columns(tmp_path):
    write_csv(tmp_path / "scopus.csv", SCOPUS_HEADER, SCOPUS_RECORDS)
    papers = _database_exports.read_scopus(tmp_path / "scopus.csv")
    first = papers.iloc[0]
    assert first["doi"] == "10.1000/ABC.1"  # ingest lower-cases and strips prefixes
    assert first["authors"] == ["Doe, Jane", "Roe, Rick"]  # Scopus author ids removed
    assert first["keywords"] == ["contextual interference", "retention"]  # Author Keywords, repeats dropped
    assert first["index_keywords"] == ["Humans", "Learning"]
    assert (first["title"], first["journal"], first["year"]) == (
        "Practice schedules and retention", "Journal of Made-Up Studies", 2021)
    assert (first["pmid"], first["scopus_id"], first["document_type"]) == ("12345", "2-s2.0-000000001", "Article")
    assert papers.iloc[1]["authors"] == ["Poe E."]  # no full names: the short ones
    assert papers.iloc[2]["keywords"] == []


def test_web_of_science_reader_maps_tags_and_skips_lines_it_cannot_split(tmp_path):
    broken = "\t".join(["J", "Bad, B"] + ["x"] * (len(WOS_CORE_TAGS) + 3))  # stray tabs inside a field
    write_wos(tmp_path / "savedrecs.txt", WOS_CORE_TAGS, WOS_RECORDS, extra_lines=[broken, "J\tShort, S"])
    papers = _database_exports.read_web_of_science(tmp_path / "savedrecs.txt")
    assert len(papers) == 3 and papers.attrs["unreadable_records"] == 1
    first = papers.iloc[0]
    assert first["authors"] == ["Doe, Jane", "Roe, Rick"]  # AF, the full names
    assert first["keywords"] == ["contextual interference", "retention"]  # DE, HTML tags removed
    assert first["keywords_plus"] == ["MOTOR", "SKILL"]
    assert first["abstract"] == 'An invented "abstract" with quotes.'  # quotes are plain text
    assert (first["journal"], first["year"], first["wos_id"], first["pmid"]) == (
        "JOURNAL OF MADE-UP STUDIES", 2021, "WOS:000000000000001", "12345")
    assert papers.iloc[2]["authors"] == ["Short, S"]  # a short line is padded with empty fields
    assert papers.iloc[2]["doi"] == "" and pd.isna(papers.iloc[2]["year"])


def test_web_of_science_biosis_falls_back_to_short_author_names(tmp_path):
    record = {"PT": "J", "AU": "Lo, Kim (kim@example.org) ; Ng, Al", "TI": "A BIOSIS record", "SO": "Neuro Fiction",
              "PY": "2005", "DI": "10.1000/BIO.5", "UT": "BCI:BCI000000000001"}
    write_wos(tmp_path / "savedrecs.txt", WOS_BIOSIS_TAGS, [record])
    papers = _database_exports.read_web_of_science(tmp_path / "savedrecs.txt")
    assert papers.iloc[0]["authors"] == ["Lo, Kim", "Ng, Al"]  # e-mail removed, " ; " handled
    assert papers.iloc[0]["keywords"] == []  # BIOSIS has no author keywords


def test_ebsco_reader_maps_columns(tmp_path):
    record = {"longDBName": "Academic Search Ultimate", "shortDBName": "asn", "an": "100000001",
              "title": "Practice schedules and retention", "abstract": "Invented.", "publicationDate": "20210315",
              "contributors": "Doe, Jane ; Roe, Rick", "docTypes": "Article", "source": "Journal of Made-Up Studies",
              "subjects": "Motor learning ; Retention", "doi": "10.1000/abc.1"}
    write_csv(tmp_path / "ebsco.csv", EBSCO_HEADER, [record])
    paper = _database_exports.read_ebsco(tmp_path / "ebsco.csv").iloc[0]
    assert paper["authors"] == ["Doe, Jane", "Roe, Rick"]
    assert paper["keywords"] == ["Motor learning", "Retention"]
    assert (paper["year"], paper["ebsco_id"], paper["database"]) == (2021, "100000001", "Academic Search Ultimate")


def test_reader_refuses_a_file_of_another_format(tmp_path):
    write_csv(tmp_path / "ebsco.csv", EBSCO_HEADER, [])
    with pytest.raises(ValueError, match="not a Scopus CSV export"):
        _database_exports.read_scopus(tmp_path / "ebsco.csv")


def test_ingest_reads_wos_folders_and_merges_across_formats(tmp_path):
    from scientographer import ingest

    write_csv(tmp_path / "scopus.csv", SCOPUS_HEADER, SCOPUS_RECORDS)
    wos = tmp_path / "wos"
    wos.mkdir()
    write_wos(wos / "savedrecs.txt", WOS_CORE_TAGS, WOS_RECORDS[:1])
    write_wos(wos / "savedrecs(1).txt", WOS_CORE_TAGS, WOS_RECORDS[1:])
    sources = [{"format": "scopus", "path": str(tmp_path / "scopus.csv")}, {"format": "wos", "path": str(wos)}]
    read = ingest.read_sources(sources, "author_then_mesh")
    papers = ingest.papers_table(read)
    report = ingest.ingest_report(read, papers)
    assert [f["records"] for f in report["files"]] == [3, 1, 1]  # both WoS files of the folder
    assert sorted(papers["doi"]) == ["10.1000/abc.1", "10.1000/link.3"]  # lower-cased, prefix removed
    merged = papers.set_index("doi").loc["10.1000/abc.1"]
    assert merged["sources"] == ["scopus:scopus.csv", "wos:savedrecs.txt"]
    assert merged["title"] == "Practice schedules and retention"  # the first source wins
    assert merged["wos_id"] == "WOS:000000000000001"  # and fills what it lacks
    assert merged["keywords_plus"] == ["MOTOR", "SKILL"]
    assert report == {**report, "records": 5, "records_without_doi": 2, "duplicates_merged": 1, "papers": 2}


def test_ingest_stage_runs_on_the_three_formats(tmp_path):
    """`scientographer run ingest` in a project with one export of each format."""
    raw = tmp_path / "data" / "raw"
    (raw / "wos").mkdir(parents=True)
    write_csv(raw / "scopus.csv", SCOPUS_HEADER, SCOPUS_RECORDS)
    write_wos(raw / "wos" / "savedrecs.txt", WOS_CORE_TAGS, WOS_RECORDS)
    write_csv(raw / "ebsco.csv", EBSCO_HEADER, [{"an": "1", "title": "Only in EBSCO", "doi": "doi:10.1000/E.9",
                                                 "publicationDate": "20200101", "subjects": "Balance"}])
    params = {"ingest": {"sources": [{"format": "scopus", "path": "data/raw/scopus.csv"},
                                     {"format": "wos", "path": "data/raw/wos"},
                                     {"format": "ebsco", "path": "data/raw/ebsco.csv"}],
                         "report_dir": "data/ingest"},
              "citation_network": {"papers_table": "data/papers.parquet"},
              "tracker": {"enabled": False}}
    (tmp_path / "params.yaml").write_text(yaml.safe_dump(params))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    env.pop("SCIENTOGRAPHER_PARAMS", None)
    run = subprocess.run([sys.executable, "-c", "from scientographer.cli import app; app(['run', 'ingest'])"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr[-2000:]
    papers = pd.read_parquet(tmp_path / "data" / "papers.parquet").set_index("doi")
    assert sorted(papers.index) == ["10.1000/abc.1", "10.1000/e.9", "10.1000/link.3"]
    assert list(papers.loc["10.1000/e.9", "keywords"]) == ["Balance"]
    report = json.loads((tmp_path / "data" / "ingest" / "report.json").read_text())
    assert (report["records"], report["records_without_doi"], report["papers"]) == (6, 2, 3)
    without_doi = pd.read_csv(tmp_path / "data" / "ingest" / "without_doi.csv")
    assert sorted(without_doi["title"]) == ["A record without a DOI", "Second file record"]
