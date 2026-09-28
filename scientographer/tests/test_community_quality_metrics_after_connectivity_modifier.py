# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import igraph as ig
import numpy as np
import pandas as pd

from scientographer.community_quality_metrics_after_connectivity_modifier import (
    _after_membership,
    _cm_community_attribute_name,
)


def _graph_with_names(names: list[str]) -> ig.Graph:
    g = ig.Graph(directed=True)
    g.add_vertices(len(names))
    g.vs["name"] = names
    return g


def test_after_membership_aligns_by_name_not_position():
    """The parquet rows are in a different order than the graph vertices; the
    join must be by node name, not row position."""
    g = _graph_with_names(["a", "b", "c"])
    df = pd.DataFrame({
        "node_name": ["c", "a", "b"],
        _cm_community_attribute_name(0.005): [7, 5, 5],
    })
    membership = _after_membership(g, df, 0.005)
    # a->5, b->5, c->7  (aligned to graph order a,b,c)
    assert membership.tolist() == [5, 5, 7]


def test_after_membership_turns_each_removed_node_into_a_distinct_singleton():
    """Every -1 (removed by CM) must become its own unique id above the max CM
    id, so the whole vertex set is partitioned and removed papers are singletons."""
    g = _graph_with_names(["a", "b", "c", "d"])
    df = pd.DataFrame({
        "node_name": ["a", "b", "c", "d"],
        _cm_community_attribute_name(0.005): [3, -1, 3, -1],
    })
    membership = _after_membership(g, df, 0.005)
    assert membership[0] == 3 and membership[2] == 3          # kept community
    assert membership[1] != membership[3]                     # two distinct singletons
    assert membership[1] > 3 and membership[3] > 3            # above the max CM id
    assert len(set(membership.tolist())) == 3                 # {3, s1, s2}


def test_after_membership_all_removed_still_distinct():
    g = _graph_with_names(["a", "b"])
    df = pd.DataFrame({
        "node_name": ["a", "b"],
        _cm_community_attribute_name(0.001): [-1, -1],
    })
    membership = _after_membership(g, df, 0.001)
    assert len(set(membership.tolist())) == 2  # two distinct singletons


def test_after_cm_partition_has_lower_coverage_than_before():
    """End-to-end sanity on a hand-built graph: two triangles joined by a bridge
    node. If CM 'removed' the bridge node, the after-partition (bridge as its own
    singleton) has fewer internal edges -> lower coverage than treating all seven
    nodes as one community."""
    from scientographer.community_quality_metrics import _structural_partition_metrics

    g = ig.Graph(directed=True)
    g.add_vertices(7)
    g.vs["name"] = [f"n{i}" for i in range(7)]
    g.add_edges([
        (0, 1), (1, 2), (2, 0),        # triangle A
        (4, 5), (5, 6), (6, 4),        # triangle B
        (2, 3), (3, 4),                # node 3 bridges A and B
    ])
    undirected = g.to_networkx().to_undirected()

    before = np.array([0, 0, 0, 0, 1, 1, 1])          # bridge (3) lumped with A
    _, before_partition = _structural_partition_metrics(g, undirected, before, 0.005)

    df = pd.DataFrame({
        "node_name": [f"n{i}" for i in range(7)],
        _cm_community_attribute_name(0.005): [0, 0, 0, -1, 1, 1, 1],  # CM removed the bridge
    })
    after = _after_membership(g, df, 0.005)
    _, after_partition = _structural_partition_metrics(g, undirected, after, 0.005)

    assert after_partition["intra_community_edge_fraction"] < before_partition["intra_community_edge_fraction"]
    assert after_partition["number_of_communities"] == 3  # A, B, and the removed bridge singleton
