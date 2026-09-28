# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import igraph as ig

from scientographer.community_connectivity_modifier import (
    _classify_transformation,
    _connectivity_modifier_on_cluster,
    _undirected_simple_graph,
    _well_connectedness_threshold,
)


def _undirected_clique(n: int) -> ig.Graph:
    g = ig.Graph(directed=True)
    g.add_vertices(n)
    g.add_edges([(i, j) for i in range(n) for j in range(n) if i != j])
    return _undirected_simple_graph(g)


# ── undirected simplification ────────────────────────────────────────────────
def test_undirected_simple_graph_collapses_reciprocal_and_drops_self_loops():
    g = ig.Graph(directed=True)
    g.add_vertices(2)
    g.add_edges([(0, 1), (1, 0), (0, 0)])  # reciprocal pair + a self-loop
    undirected = _undirected_simple_graph(g)
    assert not undirected.is_directed()
    assert undirected.ecount() == 1  # collapsed to a single undirected edge, loop gone


# ── the invariant: every surviving piece is well-connected ───────────────────
def test_well_connected_clique_is_kept_extant():
    """A single clique is already well-connected, so CM returns it unchanged."""
    g = _undirected_clique(30)
    surviving = _connectivity_modifier_on_cluster(g, list(range(30)), resolution=0.005, min_cluster_size=11)
    assert len(surviving) == 1
    assert sorted(surviving[0]) == list(range(30))


def test_thin_bridge_is_split_into_two_well_connected_pieces():
    """Two cliques joined by one edge: the min cut (1) is removed and each clique
    survives as its own well-connected community -- a `split`."""
    g = ig.Graph(directed=True)
    g.add_vertices(40)
    edges = [(i, j) for i in range(20) for j in range(20) if i != j]
    edges += [(i, j) for i in range(20, 40) for j in range(20, 40) if i != j]
    edges.append((0, 20))  # the lone bridge
    g.add_edges(edges)
    undirected = _undirected_simple_graph(g)

    surviving = _connectivity_modifier_on_cluster(
        undirected, list(range(40)), resolution=0.005, min_cluster_size=11)

    assert len(surviving) == 2
    sides = sorted(sorted(piece) for piece in surviving)
    assert sides[0] == list(range(20))
    assert sides[1] == list(range(20, 40))
    # And the invariant holds: each surviving piece exceeds f(n).
    for piece in surviving:
        cut = undirected.induced_subgraph(piece).mincut().value
        assert cut > _well_connectedness_threshold(len(piece))


def test_every_surviving_piece_exceeds_threshold_general():
    """A hub-and-spokes cluster (a star) is poorly connected everywhere; whatever
    survives must still clear the well-connectedness bar."""
    g = ig.Graph(directed=True)
    g.add_vertices(50)
    g.add_edges([(0, i) for i in range(1, 50)])  # star: every leaf peels off at cut 1
    undirected = _undirected_simple_graph(g)
    surviving = _connectivity_modifier_on_cluster(
        undirected, list(range(50)), resolution=0.005, min_cluster_size=11)
    for piece in surviving:
        cut = undirected.induced_subgraph(piece).mincut().value
        assert cut > _well_connectedness_threshold(len(piece))


def test_small_cluster_is_dropped_by_size_floor():
    """A clique below B never survives, however well-connected it is."""
    g = _undirected_clique(8)
    surviving = _connectivity_modifier_on_cluster(g, list(range(8)), resolution=0.005, min_cluster_size=11)
    assert surviving == []


def test_tree_is_dropped():
    """A path (tree) of 15 nodes has minimum cut 1 <= log10(15); it is removed."""
    g = ig.Graph(directed=True)
    g.add_vertices(15)
    g.add_edges([(i, i + 1) for i in range(14)])  # a path = a tree
    undirected = _undirected_simple_graph(g)
    surviving = _connectivity_modifier_on_cluster(
        undirected, list(range(15)), resolution=0.005, min_cluster_size=11)
    assert surviving == []


def test_disconnected_cluster_is_separated():
    """Two disjoint cliques wrongly grouped as one community (min cut 0) are
    separated into their two well-connected components."""
    g = ig.Graph(directed=True)
    g.add_vertices(30)
    edges = [(i, j) for i in range(15) for j in range(15) if i != j]
    edges += [(i, j) for i in range(15, 30) for j in range(15, 30) if i != j]
    g.add_edges(edges)  # no edge between the two halves
    undirected = _undirected_simple_graph(g)
    surviving = _connectivity_modifier_on_cluster(
        undirected, list(range(30)), resolution=0.005, min_cluster_size=11)
    assert len(surviving) == 2


# ── transformation taxonomy ──────────────────────────────────────────────────
def test_classify_transformation_labels():
    assert _classify_transformation(original_size=50, surviving_pieces=0, surviving_nodes=0) == "degraded"
    assert _classify_transformation(original_size=50, surviving_pieces=1, surviving_nodes=50) == "extant"
    assert _classify_transformation(original_size=50, surviving_pieces=1, surviving_nodes=42) == "reduced"
    assert _classify_transformation(original_size=50, surviving_pieces=3, surviving_nodes=48) == "split"
