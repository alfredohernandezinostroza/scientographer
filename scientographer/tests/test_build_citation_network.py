import igraph as ig
import pandas as pd
import pytest

from scientographer.build_citation_network import (
    _as_list,
    _build_graph,
    _citation_edges,
    _read_table,
)


def test_as_list_accepts_lists_arrays_strings_and_missing():
    assert _as_list(["a ", "", "b"], "|") == ["a", "b"]
    assert _as_list("a| b |", "|") == ["a", "b"]
    assert _as_list(None, "|") == []
    assert _as_list(float("nan"), "|") == []


def test_citation_edges_keep_only_corpus_pairs_lowercase_and_dedupe():
    refs = pd.DataFrame({
        "citing_doi": ["10.1/A", "10.1/b", "10.1/x"],
        "cited_dois": [["10.1/B", "10.1/b", "10.9/outside", "10.1/a"], "10.1/c", ["10.1/a"]],
    })
    edges = _citation_edges(refs, {"10.1/a", "10.1/b", "10.1/c"}, "|")
    assert edges == [("10.1/a", "10.1/b"), ("10.1/b", "10.1/c")]  # no self-citation, no outsiders


def test_build_graph_keeps_giant_component_and_carries_metadata():
    papers = pd.DataFrame({
        "doi": ["10.1/A", "10.1/b", "10.1/c", "10.1/d", "10.1/e"],
        "title": ["A", "B", None, "D", "E"],
        "keywords": [["x", "y"], "z", [], None, "w"],
        "year": [2000, 2001, 2002, 2003, 2004],
    })
    edges = [("10.1/a", "10.1/b"), ("10.1/b", "10.1/c"), ("10.1/d", "10.1/e")]
    g = _build_graph(papers, edges, "|", keep_giant_component=True)
    assert sorted(g.vs["name"]) == ["10.1/a", "10.1/b", "10.1/c"]
    by_name = {v["name"]: v for v in g.vs}
    assert by_name["10.1/a"]["keywords"] == "x|y"
    assert by_name["10.1/c"]["title"] == ""          # missing string stays a string
    assert by_name["10.1/b"]["year"] == 2001
    assert g.ecount() == 2


def test_graph_roundtrips_through_graphml(tmp_path):
    papers = pd.DataFrame({"doi": ["a", "b"], "title": ["A", None], "authors": [["P", "Q"], "R"]})
    g = _build_graph(papers, [("a", "b")], "|", keep_giant_component=True)
    p = tmp_path / "g.graphml"
    g.write_graphml(str(p))
    back = ig.Graph.Read_GraphML(str(p))
    assert back.vs["title"] == ["A", ""] and back.vs["authors"] == ["P|Q", "R"]


def test_read_table_rejects_unknown_formats(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_text("x")
    with pytest.raises(ValueError, match="parquet"):
        _read_table(p)
