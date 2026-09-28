# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import math

import igraph as ig
import numpy as np
import pytest

from scientographer.community_connectivity_metrics import (
    SUBSTANTIVE_COMMUNITY_MIN_SIZE,
    _community_vertex_groups,
    _is_well_connected,
    _minimum_edge_cut_of_community,
    _summarize_connectivity_metrics,
    _well_connectedness_threshold,
)


def _directed_clique(n: int) -> ig.Graph:
    """A directed graph on n nodes with every ordered pair present. Its
    undirected simplification is the complete graph K_n (min cut = n - 1)."""
    g = ig.Graph(directed=True)
    g.add_vertices(n)
    g.add_edges([(i, j) for i in range(n) for j in range(n) if i != j])
    return g


# ── f(n) = log10(n) threshold ────────────────────────────────────────────────
def test_well_connectedness_threshold_is_log10():
    assert _well_connectedness_threshold(100) == pytest.approx(2.0)
    assert _well_connectedness_threshold(1000) == pytest.approx(3.0)


def test_well_connectedness_threshold_nan_for_singleton():
    assert math.isnan(_well_connectedness_threshold(1))


# ── community vertex grouping ────────────────────────────────────────────────
def test_community_vertex_groups_partitions_all_vertices():
    groups = _community_vertex_groups(np.array([0, 0, 1, 1, 1]))
    assert groups[0] == [0, 1]
    assert groups[1] == [2, 3, 4]


# ── minimum edge cut of a community ──────────────────────────────────────────
def test_well_connected_clique():
    """A single clique of 100 nodes has min cut 99 > log10(100) = 2."""
    g = _directed_clique(100)
    cut = _minimum_edge_cut_of_community(g, list(range(100)))
    assert cut["community_size"] == 100
    assert cut["minimum_edge_cut_size"] == pytest.approx(99.0)
    threshold = _well_connectedness_threshold(cut["community_size"])
    assert _is_well_connected(cut["minimum_edge_cut_size"], threshold) is True


def test_poorly_connected_thin_bridge():
    """Two 50-cliques joined by a single edge: dense, but a single cut edge
    splits it (min cut 1 <= log10(100) = 2). This is the whole point of the
    feature -- a community that density metrics call solid yet a min cut
    dissolves."""
    g = ig.Graph(directed=True)
    g.add_vertices(100)
    edges = [(i, j) for i in range(50) for j in range(50) if i != j]
    edges += [(i, j) for i in range(50, 100) for j in range(50, 100) if i != j]
    edges.append((0, 50))  # the lone bridge between the two cliques
    g.add_edges(edges)
    cut = _minimum_edge_cut_of_community(g, list(range(100)))
    assert cut["minimum_edge_cut_size"] == pytest.approx(1.0)
    threshold = _well_connectedness_threshold(cut["community_size"])
    assert _is_well_connected(cut["minimum_edge_cut_size"], threshold) is False


def test_disconnected_community_has_zero_cut():
    """Two components with no edge between them -> min cut 0 -> not well-connected."""
    g = ig.Graph(directed=True)
    g.add_vertices(6)
    g.add_edges([(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)])
    cut = _minimum_edge_cut_of_community(g, list(range(6)))
    assert cut["minimum_edge_cut_size"] == pytest.approx(0.0)
    threshold = _well_connectedness_threshold(cut["community_size"])
    assert _is_well_connected(cut["minimum_edge_cut_size"], threshold) is False


def test_singleton_community_cut_is_nan_and_not_well_connected():
    g = _directed_clique(3)
    cut = _minimum_edge_cut_of_community(g, [0])
    assert cut["community_size"] == 1
    assert math.isnan(cut["minimum_edge_cut_size"])
    threshold = _well_connectedness_threshold(cut["community_size"])
    assert _is_well_connected(cut["minimum_edge_cut_size"], threshold) is False


def test_reciprocal_pair_collapses_to_one_undirected_edge():
    """A reciprocal citation pair A<->B is a temporal-DAG anomaly; the undirected
    collapse must merge it into a single undirected edge (count 1, not 2), so it
    is not double-counted toward connectivity."""
    g = ig.Graph(directed=True)
    g.add_vertices(2)
    g.add_edges([(0, 1), (1, 0)])
    cut = _minimum_edge_cut_of_community(g, [0, 1])
    assert cut["internal_undirected_edge_count"] == 1
    # A single undirected edge between two nodes: min cut 1 > log10(2) = 0.301.
    assert cut["minimum_edge_cut_size"] == pytest.approx(1.0)


def test_self_loop_is_dropped_by_simplify():
    """A self-citation loop is not real connectivity and must be simplified away."""
    g = ig.Graph(directed=True)
    g.add_vertices(2)
    g.add_edges([(0, 1), (0, 0)])
    cut = _minimum_edge_cut_of_community(g, [0, 1])
    assert cut["internal_undirected_edge_count"] == 1


def test_minimum_cut_balance_single_node_split():
    """A star (hub + 3 leaves) is cut most cheaply by isolating one leaf, so the
    smaller side is a single node -> balance 1/4."""
    g = ig.Graph(directed=True)
    g.add_vertices(4)
    g.add_edges([(0, 1), (0, 2), (0, 3)])
    cut = _minimum_edge_cut_of_community(g, list(range(4)))
    assert cut["minimum_edge_cut_size"] == pytest.approx(1.0)
    assert cut["minimum_cut_balance"] == pytest.approx(1 / 4)


# ── is_well_connected edge handling ──────────────────────────────────────────
def test_is_well_connected_requires_strict_inequality():
    # min cut exactly equal to f(n) is poorly connected (paper uses <=).
    assert _is_well_connected(2.0, 2.0) is False
    assert _is_well_connected(3.0, 2.0) is True


def test_is_well_connected_false_on_nan():
    assert _is_well_connected(float("nan"), 2.0) is False
    assert _is_well_connected(5.0, float("nan")) is False


# ── partition-level summary (population-explicit) ────────────────────────────
def _community_row(community_id, size, cut_size, is_well_connected):
    return {
        "community_id": community_id,
        "community_size": size,
        "minimum_edge_cut_size": cut_size,
        "is_well_connected": is_well_connected,
    }


def test_summary_fractions_exclude_singletons_that_cannot_be_well_connected():
    """Three singletons (never well-connected) plus two substantive communities,
    one well-connected. The fraction over >= 2-node communities must see only the
    two real communities, not report the singleton mass."""
    per_community = [
        _community_row(0, 1, float("nan"), False),
        _community_row(1, 1, float("nan"), False),
        _community_row(2, 1, float("nan"), False),
        _community_row(3, 40, 5.0, True),
        _community_row(4, 60, 1.0, False),
    ]
    summary = _summarize_connectivity_metrics(per_community)

    assert summary["number_of_communities"] == 5
    assert summary["number_of_communities_with_at_least_2_nodes"] == 2
    assert summary["number_of_substantive_communities"] == 2
    assert summary["number_of_well_connected_communities"] == 1
    assert summary["fraction_well_connected_over_communities_with_at_least_2_nodes"] == pytest.approx(0.5)
    assert summary["fraction_well_connected_over_substantive_communities"] == pytest.approx(0.5)


def test_summary_node_weighting_counts_papers_in_well_connected_communities():
    """99 singletons plus one big well-connected community: the node-weighted
    fraction follows the papers, not the community count."""
    per_community = [_community_row(i, 1, float("nan"), False) for i in range(99)]
    per_community.append(_community_row(99, 901, 50.0, True))
    summary = _summarize_connectivity_metrics(per_community)

    assert summary["node_weighted_fraction_well_connected"] == pytest.approx(901 / 1000)


def test_summary_substantive_uses_size_cutoff():
    small = _community_row(0, SUBSTANTIVE_COMMUNITY_MIN_SIZE - 1, 5.0, True)
    big = _community_row(1, SUBSTANTIVE_COMMUNITY_MIN_SIZE, 1.0, False)
    summary = _summarize_connectivity_metrics([small, big])

    assert summary["number_of_substantive_communities"] == 1
    # Only `big` qualifies as substantive, and it is poorly connected.
    assert summary["fraction_well_connected_over_substantive_communities"] == pytest.approx(0.0)
    assert summary["median_minimum_edge_cut_size_over_substantive_communities"] == pytest.approx(1.0)


def test_summary_empty_substantive_population_is_nan():
    summary = _summarize_connectivity_metrics([_community_row(0, 2, 1.0, False)])
    assert math.isnan(summary["fraction_well_connected_over_substantive_communities"])
    assert math.isnan(summary["median_minimum_edge_cut_size_over_substantive_communities"])
