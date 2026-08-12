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
    _community_surprise,
    _parallel_edge_count,
    _self_loop_count,
    _statistical_density_surprise_threshold,
    _percentile_summary,
    _summarize_community_metrics,
    SUBSTANTIVE_COMMUNITY_MIN_SIZE,
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


# ── per-community surprise (the "real community or artifact?" score) ──────────
def test_community_surprise_singleton_is_zero():
    """A singleton has no internal pairs, so nothing to be surprised about."""
    assert _community_surprise(0, 1, n_vertices=1000, total_directed_edges=5000) == 0.0


def test_community_surprise_zero_without_internal_edges():
    assert _community_surprise(0, 10, n_vertices=1000, total_directed_edges=5000) == 0.0


def test_community_surprise_rewards_density_at_equal_size():
    """Same size, more internal edges -> strictly harder to explain by chance."""
    sparse = _community_surprise(5, 20, n_vertices=1000, total_directed_edges=5000)
    dense = _community_surprise(50, 20, n_vertices=1000, total_directed_edges=5000)
    assert 0 < sparse < dense


def test_community_surprise_ranks_big_dense_above_small_saturated():
    """The whole point: a normal 2-paper community (a single A->B citation) sits
    at density 0.5 -- the maximum any community can reach in a citation DAG, so
    it looks maximally dense -- yet must score far below a large well-connected
    community, because one edge landing in one of two slots is unremarkable."""
    small_saturated = _community_surprise(2, 1, n_vertices=1000, total_directed_edges=5000)
    large_community = _community_surprise(200, 100, n_vertices=1000, total_directed_edges=5000)
    # A lone citation already saturates a 2-node community's density ceiling.
    assert _directed_internal_edge_density(
        {"size": 2, "internal_directed_edge_count": 1}) == 0.5
    assert small_saturated < large_community


def test_community_surprise_stays_finite_with_parallel_edges():
    """A parallel (duplicate) edge can push a community's internal edge count
    above the number of ordered pairs it has -- the real network has a 2-paper
    community with 3 internal edges. Unclamped this asks the hypergeometric for
    P(X >= impossible) = 0 and yields inf, which is also unrepresentable in JSON
    and would break the whole metrics payload."""
    surprise = _community_surprise(3, 2, n_vertices=1000, total_directed_edges=5000)
    assert math.isfinite(surprise)
    # Clamped to the 2 available ordered pairs, so it matches the saturated case.
    assert surprise == pytest.approx(
        _community_surprise(2, 2, n_vertices=1000, total_directed_edges=5000))


def test_directed_surprise_stays_finite_when_internal_exceeds_possible():
    s = _directed_surprise(
        n_vertices=10, total_directed_edges=40, community_sizes=[3, 3], total_internal_edges=99)
    assert math.isfinite(s)


def test_parallel_edge_count_detects_duplicate_citation():
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (0, 1), (1, 2)])  # 0->1 recorded twice
    assert _parallel_edge_count(g) == 1


def test_self_loop_count_detects_self_citation():
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (1, 1), (1, 2)])  # 1->1 is a paper citing itself
    assert _self_loop_count(g) == 1


def test_self_loop_makes_internal_edges_exceed_ordered_pairs():
    """The real shape behind the impossible density 1.5: a 2-node community with
    a reciprocal pair plus a self-loop holds 3 internal edges but only 2 ordered
    pairs exist."""
    g = ig.Graph(directed=True)
    g.add_vertices(2)
    g.add_edges([(0, 1), (1, 0), (0, 0)])
    counts = _directed_community_edge_counts(g, np.array([0, 0]))
    assert counts[0]["internal_directed_edge_count"] == 3
    assert _directed_internal_edge_density(counts[0]) == pytest.approx(1.5)


def test_parallel_edge_count_zero_for_simple_graph():
    g = ig.Graph(directed=True)
    g.add_vertices(3)
    g.add_edges([(0, 1), (1, 2), (0, 2)])
    assert _parallel_edge_count(g) == 0


def test_statistical_density_threshold_grows_with_number_of_communities():
    """Bonferroni correction: more communities tested -> stricter per-community bar."""
    few = _statistical_density_surprise_threshold(10)
    many = _statistical_density_surprise_threshold(10_000)
    assert few < many
    assert few == pytest.approx(math.log(10 / 0.05))


# ── summarizing per-community metrics up to partition level ───────────────────
def test_percentile_summary_empty_population_is_nan():
    summary = _percentile_summary([])
    assert all(math.isnan(v) for v in summary.values())


def test_percentile_summary_orders_percentiles():
    summary = _percentile_summary([1.0, 2.0, 3.0, 4.0])
    assert summary["p25"] <= summary["median"] <= summary["p75"]


def _community_row(community_id, size, internal_edges, conductance, density, surprise):
    return {
        "community_id": community_id, "community_size": size,
        "internal_directed_edge_count": internal_edges, "conductance": conductance,
        "internal_edge_density": density, "internal_edge_surprise": surprise,
    }


def test_summary_excludes_singletons_from_population_statistics():
    """The bug this guards against: singletons have conductance 1.0 by definition,
    so a median over ALL communities reports the artifact mass rather than the
    partition. Here 3 singletons outnumber 2 real communities."""
    per_community = [
        _community_row(0, 1, 0, 1.0, 0.0, 0.0),
        _community_row(1, 1, 0, 1.0, 0.0, 0.0),
        _community_row(2, 1, 0, 1.0, 0.0, 0.0),
        _community_row(3, 40, 400, 0.2, 0.25, 500.0),
        _community_row(4, 60, 900, 0.3, 0.25, 900.0),
    ]
    summary = _summarize_community_metrics(per_community)

    # Median over the non-trivial population sees only the two real communities.
    assert summary["conductance_median_over_communities_with_at_least_2_nodes"] == pytest.approx(0.25)
    assert summary["number_of_singleton_communities"] == 3
    # 3 of 5 communities (60%) but only 3 of 103 nodes (~2.9%) -- the contrast
    # that makes count-weighted summaries misleading.
    assert summary["share_of_nodes_in_singleton_communities"] == pytest.approx(3 / 103)


def test_summary_node_weighting_keeps_singletons_from_dominating():
    """One big clean community plus many singletons: the node-weighted mean must
    follow the big community, not the singleton count."""
    per_community = [_community_row(i, 1, 0, 1.0, 0.0, 0.0) for i in range(99)]
    per_community.append(_community_row(99, 901, 9000, 0.1, 0.5, 5000.0))
    summary = _summarize_community_metrics(per_community)

    # 99 of 100 communities are singletons with conductance 1.0, yet they hold
    # under 10% of the nodes, so the node-weighted mean stays near 0.1.
    assert summary["node_weighted_mean_conductance"] == pytest.approx(
        (99 * 1.0 * 1 + 0.1 * 901) / 1000)
    assert summary["node_weighted_mean_conductance"] < 0.2


def test_summary_substantive_population_uses_size_cutoff():
    small = _community_row(0, SUBSTANTIVE_COMMUNITY_MIN_SIZE - 1, 10, 0.9, 0.1, 20.0)
    big = _community_row(1, SUBSTANTIVE_COMMUNITY_MIN_SIZE, 100, 0.1, 0.2, 300.0)
    summary = _summarize_community_metrics([small, big])

    assert summary["number_of_substantive_communities"] == 1
    # Only `big` qualifies, so the substantive median is its own value.
    assert summary["conductance_median_over_substantive_communities"] == pytest.approx(0.1)
    assert summary["share_of_internal_edges_in_substantive_communities"] == pytest.approx(100 / 110)


def test_summary_counts_statistically_dense_communities():
    threshold = _statistical_density_surprise_threshold(3)
    per_community = [
        _community_row(0, 10, 5, 0.5, 0.05, threshold - 1.0),   # below the bar
        _community_row(1, 20, 200, 0.2, 0.5, threshold + 1.0),  # above
        _community_row(2, 30, 500, 0.1, 0.6, threshold + 100),  # well above
    ]
    summary = _summarize_community_metrics(per_community)

    assert summary["number_of_statistically_dense_communities"] == 2
    assert summary["share_of_communities_that_are_statistically_dense"] == pytest.approx(2 / 3)
    assert summary["share_of_nodes_in_statistically_dense_communities"] == pytest.approx(50 / 60)
