# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Connectivity Modifier gives identical results in worker processes and in-process."""

import math

import igraph as ig
import leidenalg
import numpy as np

from scientographer.community_connectivity_modifier import (
    _largest_community_size,
    _resolutions_to_modify,
    _run_connectivity_modifier,
    _undirected_simple_graph,
)


def _same(a, b) -> bool:
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, np.ndarray):
        return np.array_equal(a, b)
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True  # NaN
    return a == b


def test_parallel_and_in_process_runs_give_identical_results():
    # Planted communities joined by a few bridges, so CM has cuts to make.
    graph = ig.Graph.SBM([[0.25, 0.01, 0.0], [0.01, 0.25, 0.01], [0.0, 0.01, 0.25]],
                         [80, 80, 80], directed=True)
    undirected = _undirected_simple_graph(graph)
    resolutions = [0.01, 0.05, 0.2]
    memberships = [
        np.array(leidenalg.find_partition(undirected, leidenalg.CPMVertexPartition,
                                          resolution_parameter=r, seed=0).membership)
        for r in resolutions
    ]
    serial = _run_connectivity_modifier(undirected, memberships, resolutions, 11, workers=1)
    parallel = _run_connectivity_modifier(undirected, memberships, resolutions, 11, workers=3)
    assert [bundle["resolution"] for bundle in parallel] == resolutions
    assert _same(serial, parallel)
    assert any(bundle["per_community"] for bundle in serial)  # CM kept something


def test_largest_community_size():
    assert _largest_community_size(np.array([0, 0, 1, 2, 2, 2])) == 3
    assert _largest_community_size(np.array([5, 5, 7])) == 2


def test_resolutions_with_a_too_large_community_are_skipped():
    memberships = [np.array([0] * 5 + [1]), np.array([0, 0, 1, 1, 2, 2]), np.array([0] * 3 + [1] * 3)]
    resolutions = [0.001, 0.01, 0.1]
    assert _resolutions_to_modify(resolutions, memberships, None) == (resolutions, memberships)
    kept, kept_memberships = _resolutions_to_modify(resolutions, memberships, 3)
    assert kept == [0.01, 0.1]
    assert [m.tolist() for m in kept_memberships] == [memberships[1].tolist(), memberships[2].tolist()]


def test_after_cm_stage_skips_resolutions_without_a_modifier_result():
    import pandas as pd

    from scientographer.community_quality_metrics_after_connectivity_modifier import (
        _quality_after_cm_for_resolution,
        after_membership_for_resolution,
    )

    graph = ig.Graph(n=3, edges=[(0, 1)], directed=True)
    graph.vs["name"] = ["a", "b", "c"]
    cm = pd.DataFrame({"node_name": ["a", "b", "c"], "connectivity_modified_community_at_res=0.01": [0, 0, -1]})
    assert after_membership_for_resolution(graph, cm, 0.001) is None
    assert after_membership_for_resolution(graph, cm, 0.01) is not None
    assert _quality_after_cm_for_resolution(graph, None, 0.001, None) is None
