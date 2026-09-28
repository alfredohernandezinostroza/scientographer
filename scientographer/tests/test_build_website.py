import struct

import pandas as pd

from scientographer.build_website import (
    MIN_NAMED_GROUP_SIZE,
    OUTLIER_COLOR,
    PALETTE,
    _build_csr,
    _cluster_color,
    _coerce_scalar,
    _parse_graphml,
    _partition_records_from_graph_attributes,
    _resolutions_from_attribute_names,
    _to_float,
    _to_int,
    _top_lists_by_group,
    _write_csr,
    clusters_legend,
    communities_legend_by_resolution,
    community_keywords_records,
    community_labels_from_graph,
    community_resolution,
    discover_wordcloud_figures,
    figure_entries,
    node_records,
    resolution_metrics_records,
)

RES = 0.005
COMMUNITY_ATTR = f"cpm_communities_at_res={RES}"


# ── coercion + colour helpers ─────────────────────────────────────────────────
def test_to_int_and_float():
    assert _to_int("5.0") == 5
    assert _to_int(None, default=-1) == -1
    assert _to_int("nope", default=0) == 0
    assert _to_float("2.5") == 2.5
    assert _to_float(None) == 0.0


def test_cluster_color_outlier_and_cycle():
    assert _cluster_color(-1) == OUTLIER_COLOR
    assert _cluster_color(0) == PALETTE[0]
    assert _cluster_color(len(PALETTE)) == PALETTE[0]  # wraps


def test_coerce_scalar_handles_bools_ints_floats_and_text():
    assert _coerce_scalar("True") is True
    assert _coerce_scalar("12") == 12
    assert _coerce_scalar("0.53") == 0.53
    assert _coerce_scalar("nan") is None
    assert _coerce_scalar("directed") == "directed"


# ── CSR build + binary roundtrip ──────────────────────────────────────────────
def test_build_csr_out_and_in():
    edges = [(0, 1), (0, 2), (1, 2)]
    off, tgt = _build_csr(3, edges, "out")
    assert off == [0, 2, 3, 3]
    assert tgt == [1, 2, 2]
    off_in, tgt_in = _build_csr(3, edges, "in")
    assert off_in == [0, 0, 1, 3]
    assert sorted(tgt_in) == [0, 0, 1]


def test_write_csr_binary_roundtrip(tmp_path):
    off, tgt = _build_csr(3, [(0, 1), (0, 2), (1, 2)], "out")
    p = tmp_path / "e.bin"
    _write_csr(p, off, tgt)
    buf = p.read_bytes()
    n = struct.unpack("<I", buf[:4])[0]
    assert n == 3
    read_off = list(struct.unpack(f"<{n + 1}I", buf[4:4 + 4 * (n + 1)]))
    read_tgt = list(struct.unpack(f"<{len(tgt)}I", buf[4 + 4 * (n + 1):]))
    assert read_off == off and read_tgt == tgt


# ── GraphML parsing: nodes, edges, graph-level attributes, resolutions ────────
GRAPHML = """<?xml version="1.0" encoding="UTF-8"?>
<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
  <key id="g0" for="graph" attr.name="modularity_at_res=0.005" attr.type="double"/>
  <key id="g1" for="graph" attr.name="modularity_at_res=0.01" attr.type="double"/>
  <key id="g2" for="graph" attr.name="is_on_resolution_plateau_at_res=0.005" attr.type="string"/>
  <key id="g3" for="graph" attr.name="self_loop_count" attr.type="double"/>
  <key id="v0" for="node" attr.name="title" attr.type="string"/>
  <key id="v1" for="node" attr.name="cpm_communities_at_res=0.005" attr.type="double"/>
  <key id="v2" for="node" attr.name="cpm_communities_at_res=0.01" attr.type="double"/>
  <key id="v3" for="node" attr.name="top_keywords_at_res=0.005" attr.type="string"/>
  <graph id="G" edgedefault="directed">
    <data key="g0">0.53</data>
    <data key="g1">0.41</data>
    <data key="g2">True</data>
    <data key="g3">118</data>
    <node id="a"><data key="v0">A</data><data key="v1">0</data><data key="v2">3</data><data key="v3">Cerebellum; Reaching</data></node>
    <node id="b"><data key="v0">B</data><data key="v1">0</data><data key="v2">4</data><data key="v3">Cerebellum; Reaching</data></node>
    <node id="c"><data key="v0">C</data><data key="v1">1</data><data key="v2">4</data><data key="v3"></data></node>
    <edge source="a" target="b"/>
    <edge source="b" target="c"/>
  </graph>
</graphml>
"""


def test_parse_graphml_returns_graph_attributes_and_declared_node_attributes(tmp_path):
    p = tmp_path / "g.graphml"
    p.write_text(GRAPHML, encoding="utf-8")
    nodes, edges, graph_attrs, node_attr_names = _parse_graphml(p)
    assert [nid for nid, _ in nodes] == ["a", "b", "c"]
    assert edges == [(0, 1), (1, 2)]
    assert graph_attrs["modularity_at_res=0.005"] == "0.53"
    assert graph_attrs["self_loop_count"] == "118"
    assert "cpm_communities_at_res=0.01" in node_attr_names
    assert _resolutions_from_attribute_names(node_attr_names) == [0.005, 0.01]


def test_partition_records_from_graph_attributes_groups_by_resolution():
    records = _partition_records_from_graph_attributes({
        "modularity_at_res=0.005": "0.53", "modularity_at_res=0.01": "0.41",
        "is_on_resolution_plateau_at_res=0.005": "True", "self_loop_count": "118"})
    assert [r["resolution"] for r in records] == [0.005, 0.01]
    assert records[0]["modularity"] == 0.53 and records[0]["is_on_resolution_plateau"] is True
    assert "self_loop_count" not in records[0]


def test_resolution_metrics_prefer_parquet_then_graph_attributes():
    df = pd.DataFrame({"resolution": [0.01, 0.005], "modularity": [0.41, 0.53]})
    from_parquet = resolution_metrics_records(df, {"modularity_at_res=0.005": "0.99"})
    assert [r["resolution"] for r in from_parquet] == [0.005, 0.01]
    from_graph = resolution_metrics_records(None, {"modularity_at_res=0.005": "0.99"})
    assert from_graph == [{"resolution": 0.005, "modularity": 0.99}]
    assert resolution_metrics_records(None, {}) == []


def test_community_resolution_falls_back_to_lowest_available():
    assert community_resolution([0.001, 0.005], 0.005) == 0.005
    assert community_resolution([0.001, 0.002], 0.005) == 0.001
    assert community_resolution([], 0.005) is None


# ── TF-IDF group top-lists ────────────────────────────────────────────────────
def test_top_lists_distinctive_keywords_and_authors():
    records = [
        {"cluster": 0, "keywords": "shared|alpha", "authors": "A", "title": "t1", "indegree": 9, "year": 2000},
        {"cluster": 0, "keywords": "shared|alpha", "authors": "A", "title": "t2", "indegree": 3, "year": 2001},
        {"cluster": 1, "keywords": "shared|beta", "authors": "B", "title": "t3", "indegree": 5, "year": 2002},
        {"cluster": 2, "keywords": "shared|gamma", "authors": "C", "title": "t4", "indegree": 1, "year": 2003},
    ]
    out = _top_lists_by_group(records, "cluster")
    kw0 = [k["keyword"] for k in out[0]["top_keywords"]]
    assert kw0[0] == "alpha"
    assert "shared" not in kw0
    assert out[0]["top_papers"][0]["title"] == "t1"
    assert out[0]["top_authors"][0] == {"name": "A", "papers": 2}


# ── node_records / legends ────────────────────────────────────────────────────
def _mk_node(nid, topic, community, **over):
    a = {"topic": str(topic), COMMUNITY_ATTR: str(community), "cpm_communities_at_res=0.01": str(community + 10),
         "x": "1.0", "y": "2.0", "title": f"T{nid}", "keywords": "kw",
         "authors": "Au", "year": "2000", "journal": "J", "name": f"doi{nid}",
         "size": "1", "Eingangsgrad": "0", "Grad": "0"}
    a.update(over)
    return (nid, a)


def test_node_records_small_community_greyed_and_all_resolutions_carried():
    big = [_mk_node(f"b{i}", topic=3, community=5) for i in range(MIN_NAMED_GROUP_SIZE)]
    small = [_mk_node("s0", topic=3, community=9)]
    recs = node_records(big + small, resolutions=[RES, 0.01], community_resolution=RES)
    by_id = {r["id"]: r for r in recs}
    assert by_id["b0"]["community_color"] == _cluster_color(5)
    assert by_id["s0"]["community_color"] == OUTLIER_COLOR
    assert by_id["b0"]["color"] == _cluster_color(3)
    assert by_id["b0"]["cluster"] == 3 and by_id["b0"]["community"] == 5
    assert by_id["b0"]["communities"] == {"0.005": 5, "0.01": 15}
    assert by_id["b0"]["x"] == 1.0 and by_id["b0"]["y"] == 2.0


def test_node_records_without_any_community_resolution():
    recs = node_records([_mk_node("a", topic=1, community=2)], resolutions=[], community_resolution=None)
    assert recs[0]["community"] == -1 and recs[0]["communities"] == {}


def test_legends_filtering_labels_and_metrics_merge():
    big = [_mk_node(f"b{i}", topic=3, community=5, **{"top_keywords_at_res=0.005": "Cerebellum; Reaching"})
           for i in range(MIN_NAMED_GROUP_SIZE)]
    small = [_mk_node("s0", topic=3, community=9)]
    outlier = [_mk_node("o0", topic=-1, community=9)]
    raw = big + small + outlier
    recs = node_records(raw, resolutions=[RES, 0.01], community_resolution=RES)

    clusters = clusters_legend(recs, topic_metrics={"3": {"n_papers": 31}})
    assert "3" in clusters and "-1" not in clusters
    assert clusters["3"]["community_metrics"] == {"n_papers": 31}
    assert clusters["3"]["size"] == MIN_NAMED_GROUP_SIZE + 1

    labels = community_labels_from_graph(raw, [RES, 0.01])
    assert labels == {"0.005": {"5": "Cerebellum; Reaching"}}

    quality = {"0.005": {"5": {"community_size": 40, "conductance": 0.2}}}
    comms = communities_legend_by_resolution(recs, quality, labels, [RES, 0.01])
    assert set(comms) == {"0.005", "0.01"}
    assert "5" in comms["0.005"] and "9" not in comms["0.005"]
    assert comms["0.005"]["5"]["name"] == "Cerebellum; Reaching"
    assert comms["0.005"]["5"]["name_source"] == "pipeline"
    assert comms["0.005"]["5"]["quality"]["conductance"] == 0.2
    assert comms["0.005"]["5"]["true_size"] == 40
    # At 0.01 there is no graph label -> the site's own TF-IDF label, still named.
    assert comms["0.01"]["15"]["name_source"] == "site"


# ── community keyword bars ────────────────────────────────────────────────────
def test_community_keywords_records_prefer_pipeline_scores_else_site_tfidf():
    legend = {"0.005": {"5": {"top_keywords": [{"keyword": "kw", "tfidf": 0.3}]},
                        "6": {"top_keywords": [{"keyword": "other", "tfidf": 0.1}]}}}
    scores = pd.DataFrame({"resolution": [0.005, 0.005], "community_id": [5, 5], "rank": [2, 1],
                           "keyword": ["reaching", "cerebellum"], "corrected_tfidf_score": [0.4, 0.9]})
    out = community_keywords_records(legend, scores)
    assert out["source"] == "pipeline"
    assert [k["keyword"] for k in out["by_resolution"]["0.005"]["5"]] == ["cerebellum", "reaching"]
    assert out["by_resolution"]["0.005"]["6"] == [{"keyword": "other", "score": 0.1}]  # fallback per community
    fallback = community_keywords_records(legend, None)
    assert fallback["source"] == "site"
    assert fallback["by_resolution"]["0.005"]["5"] == [{"keyword": "kw", "score": 0.3}]


# ── figures ───────────────────────────────────────────────────────────────────
def test_discover_wordcloud_figures_parses_graph_and_resolution(tmp_path):
    d = tmp_path / "until_2026_wordcloud_noverlap" / "wordclouds"
    d.mkdir(parents=True)
    (d / "tdidf_until_2026_wordcloud_at_0.001_15_clusters.svg").write_text("<svg/>")
    (d / "frequency_until_2026_wordcloud_at_0.0004_15_clusters.svg").write_text("<svg/>")
    entries = discover_wordcloud_figures(tmp_path)
    by_kind = {e["kind"]: e for e in entries}
    assert by_kind["tfidf-wordcloud"]["resolution"] == 0.001
    assert by_kind["frequency-wordcloud"]["resolution"] == 0.0004
    assert by_kind["tfidf-wordcloud"]["graph"] == "until_2026"


def test_figure_entries_skip_missing_and_deduplicate_names(tmp_path):
    a = tmp_path / "a" / "cloud.svg"
    b = tmp_path / "b" / "cloud.svg"
    a.parent.mkdir(); b.parent.mkdir()
    a.write_text("<svg/>"); b.write_text("<svg/>")
    manifest = [{"path": str(a), "kind": "wordcloud", "resolution": 0.001},
                {"path": str(b), "kind": "wordcloud", "resolution": 0.002},
                {"path": str(tmp_path / "missing.svg")}]
    entries = figure_entries(manifest, tmp_path / "graph.graphml")
    assert [e["file"] for e in entries] == ["cloud.svg", "b__cloud.svg"]
    assert entries[0]["title"] == "cloud"
