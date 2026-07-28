import math

import numpy as np
import pytest
import igraph as ig

from motor_learning_network.community_quality_metrics import (
    _reciprocal_edge_pair_count,
    _directed_community_edge_counts,
    _directed_conductance,
    _directed_internal_edge_density,
    _directed_surprise,
    _intra_community_edge_fraction,
    _kl_divergence_term,
    _significance,
    _resolution_plateau_flags,
)


# ── reciprocal edges ──────────────────────────────────────────────────────────
def test_reciprocal_edge_pair_count_zero_for_dag():
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (1, 2)])
    assert _reciprocal_edge_pair_count(g) == 0


def test_reciprocal_edge_pair_count_detects_mutual_citation():
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (1, 0), (1, 2)])  # 0<->1 reciprocal, 1->2 not
    assert _reciprocal_edge_pair_count(g) == 1


# ── directed community edge counts / conductance ─────────────────────────────
@pytest.fixture
def two_community_directed_graph():
    """Two triangles (each a fully-connected community, cyclic so in=out
    degree within each) joined by one directed bridge 2->3 from community 0
    to community 1."""
    g = ig.Graph(directed=True)
    g.add_vertices(6)
    g.add_edges([
        (0, 1), (1, 2), (2, 0),      # community 0 triangle
        (3, 4), (4, 5), (5, 3),      # community 1 triangle
        (2, 3),                      # single directed bridge, 0 -> 1
    ])
    membership = np.array([0, 0, 0, 1, 1, 1])
    return g, membership


def test_directed_community_edge_counts(two_community_directed_graph):
    g, membership = two_community_directed_graph
    counts = _directed_community_edge_counts(g, membership)

    assert counts[0]["size"] == 3
    assert counts[1]["size"] == 3
    # Each community has 3 internal (triangle) edges.
    assert counts[0]["internal_directed_edge_count"] == 3
    assert counts[1]["internal_directed_edge_count"] == 3
    # The single bridge 2->3 is outward for community 0, inward for community 1.
    assert counts[0]["out_boundary_edge_count"] == 1
    assert counts[0]["in_boundary_edge_count"] == 0
    assert counts[1]["out_boundary_edge_count"] == 0
    assert counts[1]["in_boundary_edge_count"] == 1
    assert counts[0]["boundary_edge_count"] == 1
    assert counts[1]["boundary_edge_count"] == 1


def test_directed_conductance_decomposition(two_community_directed_graph):
    g, membership = two_community_directed_graph
    counts = _directed_community_edge_counts(g, membership)
    total_edges = g.ecount()  # 7

    c0 = _directed_conductance(counts[0], total_edges)
    # Community 0: out_degree_volume = out-degree sum = each node has out-degree
    # 1 in the triangle plus node 2 has the extra bridge -> 1+1+2 = 4.
    # in_degree_volume = each node has in-degree 1 from the triangle -> 3.
    assert counts[0]["out_degree_volume"] == 4
    assert counts[0]["in_degree_volume"] == 3
    # Outward conductance: 1 outward edge / 4 outgoing stubs.
    assert c0["conductance_out"] == pytest.approx(1 / 4)
    # No inward-boundary edges into community 0.
    assert c0["conductance_in"] == pytest.approx(0.0)

    c1 = _directed_conductance(counts[1], total_edges)
    assert c1["conductance_out"] == pytest.approx(0.0)
    # Community 1: in_degree_volume = 3 (triangle) + 1 (bridge into node 3) = 4.
    assert counts[1]["in_degree_volume"] == 4
    assert c1["conductance_in"] == pytest.approx(1 / 4)


def test_directed_conductance_isolated_community_is_zero():
    # A community with no edges leaving it at all -> conductance 0.
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (1, 2), (2, 0)])
    membership = np.array([0, 0, 0])
    counts = _directed_community_edge_counts(g, membership)
    c = _directed_conductance(counts[0], g.ecount())
    assert c["conductance"] == 0.0
    assert c["conductance_out"] == 0.0
    assert c["conductance_in"] == 0.0


# ── internal edge density (directed, ordered pairs) ──────────────────────────
def test_directed_internal_edge_density_full_clique():
    # 3 nodes, all 6 ordered pairs present -> density 1.0.
    counts = {"size": 3, "internal_directed_edge_count": 6}
    assert _directed_internal_edge_density(counts) == pytest.approx(1.0)


def test_directed_internal_edge_density_singleton_is_zero():
    counts = {"size": 1, "internal_directed_edge_count": 0}
    assert _directed_internal_edge_density(counts) == 0.0


# ── surprise ──────────────────────────────────────────────────────────────────
def test_directed_surprise_zero_when_no_internal_edges():
    # Observing zero internal edges is never surprising (upper-tail p-value 1).
    assert _directed_surprise(
        n_vertices=10, total_directed_edges=20, community_sizes=[5, 5], total_internal_edges=0
    ) == pytest.approx(0.0)


def test_directed_surprise_positive_when_internal_edges_concentrated():
    # All observed edges land inside communities that could only hold a small
    # fraction of all possible edges -> should be surprising (> 0).
    s = _directed_surprise(
        n_vertices=20, total_directed_edges=30, community_sizes=[5, 5], total_internal_edges=25
    )
    assert s > 0.0


# ── coverage ──────────────────────────────────────────────────────────────────
def test_intra_community_edge_fraction():
    assert _intra_community_edge_fraction(30, 100) == pytest.approx(0.3)
    assert _intra_community_edge_fraction(0, 0) == 0.0


# ── significance (hand-rolled, boundary-safe KL) ─────────────────────────────
def test_kl_divergence_term_handles_density_one_without_nan():
    # community_density == 1.0 must not produce 0 * log(0) = NaN.
    term = _kl_divergence_term(1.0, 0.2)
    assert not math.isnan(term)
    assert term > 0


def test_kl_divergence_term_handles_density_zero_without_nan():
    term = _kl_divergence_term(0.0, 0.2)
    assert not math.isnan(term)


def test_significance_positive_for_dense_communities_including_cliques():
    import networkx as nx
    # Two dense triangles (clique communities, density == 1.0) with a single
    # bridge -- this is exactly the shape that made cdlib.evaluation
    # .significance return NaN on the real network (clique density hits the
    # x*log(x) boundary).
    g = nx.Graph()
    g.add_edges_from([(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5), (2, 3)])
    membership = np.array([0, 0, 0, 1, 1, 1])
    score = _significance(g, membership)
    assert not math.isnan(score)
    assert score > 0


def test_significance_zero_when_no_edges():
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(range(4))
    membership = np.array([0, 0, 1, 1])
    assert _significance(g, membership) == 0.0


# ── resolution plateau detection ─────────────────────────────────────────────
def test_resolution_plateau_flags_detects_identical_partitions():
    identical = np.array([0, 0, 1, 1])
    memberships = [identical, identical, np.array([0, 1, 2, 3])]  # last one differs a lot
    resolutions = [0.1, 0.2, 0.3]
    flags = _resolution_plateau_flags(memberships, resolutions, threshold=0.9)

    assert flags[0.1]["is_on_resolution_plateau"] is True   # matches 0.2 exactly
    assert flags[0.2]["is_on_resolution_plateau"] is True   # matches 0.1 exactly
    assert math.isnan(flags[0.1]["resolution_plateau_nmi_with_previous"])  # no previous
    assert math.isnan(flags[0.3]["resolution_plateau_nmi_with_next"])      # no next


def test_resolution_plateau_flags_false_when_partitions_differ():
    memberships = [np.array([0, 0, 1, 1]), np.array([0, 1, 2, 3])]
    resolutions = [0.1, 0.2]
    flags = _resolution_plateau_flags(memberships, resolutions, threshold=0.9)
    assert flags[0.1]["is_on_resolution_plateau"] is False
    assert flags[0.2]["is_on_resolution_plateau"] is False
