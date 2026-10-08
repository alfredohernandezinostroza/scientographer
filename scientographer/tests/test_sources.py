# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The corpus-building stages: PubMed search, MEDLINE ingest, OpenAlex references.
No network: searches and API answers are faked."""

from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner
import yaml

from scientographer import _medline
from scientographer.cli import _stage_modules, app

MEDLINE = """\
PMID- 111
DP  - 2020 Feb 1
TI  - Motor adaptation in a
      changing world.
LID - S000 [pii]
LID - 10.1000/ABC.1 [doi]
AB  - First line of the abstract
      continues here.
FAU - Doe, Jane
AU  - Doe J
FAU - Roe, Rick
JT  - Journal of Motor Behavior
PT  - Journal Article
MH  - Humans
MH  - *Motor Skills/physiology
MH  - Adaptation, Physiological/*physiology
OT  - motor adaptation
OT  - cerebellum

PMID- 222
DP  - 2019 Winter
TI  - A record without author keywords.
AID - 10.1000/xyz.2 [doi]
MH  - *Learning
MH  - Male

PMID- 333
DP  - 2018
TI  - No DOI here.
"""


def test_medline_records_parse_tags_lists_and_continuations():
    recs = list(_medline.records(MEDLINE.splitlines()))
    assert [r["PMID"] for r in recs] == [["111"], ["222"], ["333"]]
    first = _medline.to_paper(recs[0])
    assert first["doi"] == "10.1000/abc.1"
    assert first["title"] == "Motor adaptation in a changing world."
    assert first["abstract"] == "First line of the abstract continues here."
    assert first["authors"] == ["Doe, Jane", "Roe, Rick"]
    assert first["keywords"] == ["motor adaptation", "cerebellum"]
    assert first["mesh"] == ["Humans", "Motor Skills", "Adaptation, Physiological"]
    assert first["year"] == 2020 and first["journal"] == "Journal of Motor Behavior"
    second = _medline.to_paper(recs[1])
    assert second["doi"] == "10.1000/xyz.2" and second["year"] == 2019
    assert second["keywords"] == ["Learning"]  # no author keywords: MeSH major topics
    assert _medline.mesh_major_topics(recs[0]) == ["Motor Skills", "Physiological Adaptation"]
    assert _medline.natural_order("Lymphoma, Large B-Cell, Diffuse") == "Diffuse Large B-Cell Lymphoma"
    assert _medline.natural_order("1,2-Dihydroxybenzene") == "1,2-Dihydroxybenzene"
    assert _medline.to_paper(recs[1], "author")["keywords"] == []
    assert _medline.to_paper(recs[2])["doi"] == ""


def test_split_by_pmid_keeps_whole_records():
    parts = _medline.split_by_pmid(MEDLINE)
    assert list(parts) == ["111", "222", "333"]
    assert parts["111"].startswith("PMID- 111") and "OT  - cerebellum" in parts["111"]
    assert list(_medline.records(parts["222"].splitlines()))[0]["TI"] == ["A record without author keywords."]


def test_pubmed_search_splits_by_date_until_each_part_fits(monkeypatch):
    from scientographer import search_pubmed as sp

    monkeypatch.setattr(sp, "ESEARCH_LIMIT", 3)
    # Seven papers, one per year 2000..2006; a search returns at most 3 ids.
    published = {str(i): date(2000 + i, 6, 1) for i in range(7)}
    calls = []

    def search(term, retmax):
        calls.append(term)
        if '"[dp]' not in term:
            hits = sorted(published)
        else:
            lo, hi = [date(*map(int, part.split('"')[1].split("/"))) for part in term.split(" : ")]
            hits = [p for p, d in published.items() if lo <= d <= hi]
        return len(hits), hits[:retmax]

    pmids = sp._collect_pmids("q", search, start=date(2000, 1, 1), end=date(2006, 12, 31))
    assert sorted(pmids) == sorted(published)
    assert len(calls) > 2


def test_ingest_merges_by_doi_and_reports_records_without_one(tmp_path):
    from scientographer import ingest

    (tmp_path / "a.txt").write_text(MEDLINE)
    # The same paper again from a second file, with an abstract the first lacks.
    (tmp_path / "b.txt").write_text("PMID- 222\nTI  - Duplicate\nAB  - Filled in.\nAID - 10.1000/XYZ.2 [doi]\n")
    read = ingest.read_sources([{"format": "medline", "path": str(tmp_path)}], "author_then_mesh")
    papers = ingest.papers_table(read)
    report = ingest.ingest_report(read, papers)
    assert sorted(papers["doi"]) == ["10.1000/abc.1", "10.1000/xyz.2"]
    merged = papers.set_index("doi").loc["10.1000/xyz.2"]
    assert merged["title"] == "A record without author keywords."  # first source wins
    assert merged["abstract"] == "Filled in."                        # empty field filled
    assert merged["sources"] == ["medline:a.txt", "medline:b.txt"]
    assert report == {**report, "records": 4, "records_without_doi": 1, "duplicates_merged": 1, "papers": 2}
    with pytest.raises(ValueError, match="format"):
        ingest.read_sources([{"format": "scopus_csv", "path": str(tmp_path)}], "author")


def test_openalex_works_parse_and_references_stay_inside_the_corpus():
    from scientographer import fetch_references as fr

    results = [
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/A",
         "referenced_works": ["https://openalex.org/W2", "https://openalex.org/W99"]},
        {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1/b", "referenced_works": []},
    ]
    rows = fr._parse_works(["10.1/a", "10.1/b", "10.1/c"], results)
    assert [r["found"] for r in rows] == [True, True, False]
    assert rows[0]["referenced_works"] == ["W2", "W99"]
    refs = fr._references(pd.DataFrame(rows))
    assert refs.set_index("citing_doi")["cited_dois"].to_dict() == {"10.1/a": ["10.1/b"], "10.1/b": []}


def test_openalex_fetch_batches_pages_and_saves_as_it_goes(monkeypatch):
    from scientographer import fetch_references as fr

    asked, saved = [], []

    def fake_get(url, query, limiter):
        if "filter" not in query:  # a DOI with a comma, looked up on its own
            asked.append(url)
            return {"id": "W9", "doi": "https://doi.org/10.1/x,y", "referenced_works": []}
        dois = query["filter"][len("doi:"):].split("|")
        asked.append(len(dois))
        works = [{"id": f"W{d}", "doi": d, "referenced_works": []} for d in dois]
        page = query["page"]
        return {"meta": {"count": len(works)}, "results": works[(page - 1) * 100: page * 100]}

    monkeypatch.setattr(fr, "_openalex_get", fake_get)
    dois = [f"10.1/{i}" for i in range(5)] + ["10.1/x,y"]
    rows = fr._fetch_openalex(dois, "", "", 2, saved.append)
    assert [r["found"] for r in rows] == [True] * 6
    assert asked[:3] == [2, 2, 1] and asked[3].endswith("10.1/x,y")
    assert sum(len(s) for s in saved) == 6  # every request's rows saved before the next


def test_init_pubmed_puts_the_search_stages_in_front(tmp_path):
    proj = tmp_path / "pm"
    result = CliRunner().invoke(app, ["init", str(proj), "--pubmed", '"motor learning"[tiab]', "--email",
                                      "someone@example.org", "--max-records", "500"])
    assert result.exit_code == 0, result.output
    dvc = yaml.safe_load((proj / "dvc.yaml").read_text())
    names = list(dvc["stages"])
    assert names[:4] == ["search_pubmed", "ingest", "fetch_references", "citation_network"]
    for stage in dvc["stages"].values():
        assert stage["cmd"].split()[-1] in _stage_modules()
    params = yaml.safe_load((proj / "params.yaml").read_text())
    assert params["pubmed_search"]["query"] == '"motor learning"[tiab]'
    assert params["pubmed_search"]["max_records"] == 500
    assert params["pubmed_search"]["email"] == params["fetch_references"]["email"] == "someone@example.org"
    assert params["communities"]["substantive_min_size"] == 15
    # Running it again does not add the stages twice.
    CliRunner().invoke(app, ["init", str(proj), "--pubmed", "x"])
    assert (proj / "dvc.yaml").read_text().count("search_pubmed:") == 1
