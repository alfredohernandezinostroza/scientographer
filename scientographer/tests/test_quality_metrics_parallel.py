# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import math

import igraph as ig
import leidenalg
import numpy as np

from scientographer.community_quality_metrics import (
    _resolution_plateau_flags,
    _resolve_workers,
    _run_quality_metrics,
)


def _graph_and_memberships():
    g = ig.Graph.Erdos_Renyi(n=120, m=420, directed=True, loops=False)
    g.vs["title"] = [f"paper {i}" for i in range(g.vcount())]   # attributes the workers never get
    resolutions = [0.01, 0.05, 0.2]
    memberships = [
        np.array(leidenalg.find_partition(g, leidenalg.CPMVertexPartition,
                                          resolution_parameter=r, seed=0, n_iterations=5).membership)
        for r in resolutions
    ]
    return g, resolutions, memberships


def _same(a, b) -> bool:
    """Deep equality where NaN equals NaN (NaN summaries are legitimate: no
    substantive communities; after crossing a process boundary they are distinct
    float objects, and nan == nan is False)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def test_parallel_and_in_process_runs_give_identical_results():
    g, resolutions, memberships = _graph_and_memberships()
    plateau = _resolution_plateau_flags(memberships, resolutions, 0.9)
    serial = _run_quality_metrics(g, memberships, resolutions, plateau, 3, (1, 2), workers=1)
    parallel = _run_quality_metrics(g, memberships, resolutions, plateau, 3, (1, 2), workers=3)
    assert [b["resolution"] for b in parallel] == resolutions        # input order kept
    assert _same(parallel, serial)                                   # every number identical


def test_resolve_workers():
    assert _resolve_workers(1, 36) == 1
    assert _resolve_workers(64, 36) == 36          # never more workers than resolutions
    assert 1 <= _resolve_workers("auto", 36) <= 36
    assert _resolve_workers(None, 5) <= 5
