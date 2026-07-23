import json
import struct

import pytest

from motor_learning_network.build_website import (
    _to_int,
    _to_float,
    _cluster_color,
    _build_csr,
    _write_csr,
    _top_lists_by_group,
    node_records,
    clusters_legend,
    communities_legend,
    PALETTE,
    OUTLIER_COLOR,
    MIN_NAMED_GROUP_SIZE,
    COMMUNITY_ATTR,
)


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


# ── CSR build + binary roundtrip ──────────────────────────────────────────────
def test_build_csr_out_and_in():
    edges = [(0, 1), (0, 2), (1, 2)]
    off, tgt = _build_csr(3, edges, "out")
    assert off == [0, 2, 3, 3]      # node0 -> 2 targets, node1 -> 1, node2 -> 0
    assert tgt == [1, 2, 2]
    off_in, tgt_in = _build_csr(3, edges, "in")
    assert off_in == [0, 0, 1, 3]   # node0 cited by none, node1 by {0}, node2 by {0,1}
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


# ── TF-IDF group top-lists ────────────────────────────────────────────────────
def test_top_lists_distinctive_keywords_and_authors():
    # "shared" spans all groups (generic -> negative TF-IDF, dropped); "alpha" is
    # distinctive to group 0. Three groups so idf = log(n/(1+df)) is well-defined.
    records = [
        {"cluster": 0, "keywords": "shared|alpha", "authors": "A", "title": "t1", "indegree": 9, "year": 2000},
        {"cluster": 0, "keywords": "shared|alpha", "authors": "A", "title": "t2", "indegree": 3, "year": 2001},
        {"cluster": 1, "keywords": "shared|beta", "authors": "B", "title": "t3", "indegree": 5, "year": 2002},
        {"cluster": 2, "keywords": "shared|gamma", "authors": "C", "title": "t4", "indegree": 1, "year": 2003},
    ]
    out = _top_lists_by_group(records, "cluster")
    kw0 = [k["keyword"] for k in out[0]["top_keywords"]]
    # The distinctive keyword is kept and ranks first; the generic one is dropped.
    assert kw0[0] == "alpha"
    assert "shared" not in kw0
    # Top paper by in-degree, and author counts.
    assert out[0]["top_papers"][0]["title"] == "t1"
    assert out[0]["top_authors"][0] == {"name": "A", "papers": 2}


# ── node_records: community colour gating ─────────────────────────────────────
def _mk_node(nid, topic, community, **over):
    a = {"topic": str(topic), COMMUNITY_ATTR: str(community),
         "x": "1.0", "y": "2.0", "title": f"T{nid}", "keywords": "kw",
         "authors": "Au", "year": "2000", "journal": "J", "name": f"doi{nid}",
         "size": "1", "Eingangsgrad": "0", "Grad": "0"}
    a.update(over)
    return (nid, a)


def test_node_records_small_community_greyed():
    # Community 5 is large (>= MIN_NAMED_GROUP_SIZE), community 9 is tiny.
    big = [_mk_node(f"b{i}", topic=3, community=5) for i in range(MIN_NAMED_GROUP_SIZE)]
    small = [_mk_node("s0", topic=3, community=9)]
    recs = node_records(big + small)
    by_id = {r["id"]: r for r in recs}
    assert by_id["b0"]["community_color"] == _cluster_color(5)      # named -> coloured
    assert by_id["s0"]["community_color"] == OUTLIER_COLOR          # too small -> grey
    # Topic colour is always assigned from the topic id.
    assert by_id["b0"]["color"] == _cluster_color(3)
    assert by_id["b0"]["cluster"] == 3 and by_id["b0"]["community"] == 5
    assert by_id["b0"]["x"] == 1.0 and by_id["b0"]["y"] == 2.0


def test_legends_filtering_and_metrics_merge():
    big = [_mk_node(f"b{i}", topic=3, community=5) for i in range(MIN_NAMED_GROUP_SIZE)]
    small = [_mk_node("s0", topic=3, community=9)]
    outlier = [_mk_node("o0", topic=-1, community=9)]
    recs = node_records(big + small + outlier)

    clusters = clusters_legend(recs, topic_metrics={"3": {"n_papers": 31}})
    assert "3" in clusters and "-1" not in clusters          # outlier topic dropped
    assert clusters["3"]["community_metrics"] == {"n_papers": 31}
    assert clusters["3"]["size"] == MIN_NAMED_GROUP_SIZE + 1  # b* + s0 share topic 3

    comms = communities_legend(recs)
    assert "5" in comms                                       # large community kept
    assert "9" not in comms                                   # below MIN size dropped
    assert comms["5"]["size"] == MIN_NAMED_GROUP_SIZE
