# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import igraph as ig
import numpy as np
import pandas as pd
import pytest

from scientographer._forceatlas2 import forceatlas2, numba_available
from scientographer.citation_layouts import (
    citation_layouts,
    layout_specs,
    oriented,
    save_citation_layouts,
)


def _two_groups(n=12) -> ig.Graph:
    """Two dense groups joined by one citation, with a community column."""
    g = ig.Graph(directed=True)
    g.add_vertices(2 * n)
    g.vs["name"] = [f"10.1/{i}" for i in range(2 * n)]
    edges = [(i, j) for i in range(n) for j in range(i + 1, n)]
    edges += [(n + i, n + j) for i in range(n) for j in range(i + 1, n)]
    g.add_edges(edges + [(0, n)])
    g.vs["cpm_communities_at_res=0.005"] = [0] * n + [1] * n
    return g


@pytest.mark.parametrize("dimensions", [2, 3])
@pytest.mark.parametrize("lin_log", [False, True])
def test_fa2_engine_separates_the_two_groups(dimensions, lin_log):
    g = _two_groups()
    pos, engine = forceatlas2(g, dimensions=dimensions, iterations=300, lin_log=lin_log, engine="fa2")
    assert engine == "fa2" and pos.shape == (g.vcount(), dimensions)
    a, b = pos[:12].mean(0), pos[12:].mean(0)
    spread = max(np.linalg.norm(pos[:12] - a, axis=1).mean(), np.linalg.norm(pos[12:] - b, axis=1).mean())
    assert np.linalg.norm(a - b) > spread


@pytest.mark.skipif(not numba_available(), reason="numba (the fast-layout extra) is not installed")
@pytest.mark.parametrize("lin_log", [False, True])
def test_numba_engine_matches_exact_fa2_before_the_layouts_drift_apart(lin_log):
    # fa2 without its Barnes-Hut approximation, from the same start: equal to
    # rounding for a few iterations (force layouts are chaotic, so the two drift
    # apart in detail afterwards).
    from fa2 import ForceAtlas2
    import scipy.sparse

    g = _two_groups()
    exact, _ = forceatlas2(g, dimensions=3, iterations=3, lin_log=lin_log, seed=4, engine="numba")
    simple = g.as_undirected(mode="collapse")
    adjacency = scipy.sparse.lil_matrix(np.array(simple.get_adjacency().data, dtype=float))
    start = np.random.default_rng(4).random((g.vcount(), 3))
    reference = np.array(ForceAtlas2(verbose=False, dim=3, linLogMode=lin_log, barnesHutOptimize=False)
                         .forceatlas2(adjacency, pos=start, iterations=3))
    assert np.abs(exact - reference).max() < 1e-9 * np.abs(reference).max()


def test_orientation_centres_and_turns_to_principal_axes():
    rng = np.random.default_rng(0)
    pos = rng.normal(size=(500, 3)) * [1.0, 5.0, 2.0]  # long along y, then z
    pos = pos @ np.linalg.qr(rng.normal(size=(3, 3)))[0] + [10, -3, 7]
    turned = oriented(pos, np.ones(500))
    spread = turned.std(axis=0)
    assert spread[0] > spread[1] > spread[2]
    assert np.abs(np.median(turned, axis=0)).max() < 0.5


def test_layouts_write_tables_and_leave_the_graph_alone(tmp_path):
    g = _two_groups()
    before = g.vs["cpm_communities_at_res=0.005"]
    specs = layout_specs([{"key": "linlog", "lin_log": True, "iterations": 50, "engine": "fa2"},
                          {"key": "linlog_3d", "lin_log": True, "dimensions": 3, "iterations": 50, "engine": "fa2"}])
    result = citation_layouts(g, specs)
    save_citation_layouts(result, tmp_path)

    flat = pd.read_csv(tmp_path / "linlog.csv")
    deep = pd.read_csv(tmp_path / "linlog_3d.csv")
    assert list(flat.columns) == ["doi", "x", "y"] and list(deep.columns) == ["doi", "x", "y", "z"]
    assert flat["doi"].tolist() == g.vs["name"]
    manifest = json.loads((tmp_path / "citation_layouts.json").read_text())
    assert manifest["linlog_3d"]["dimensions"] == 3 and manifest["linlog"]["engine"] == "fa2"
    # Only positions are produced: the communities are not touched.
    assert g.vs["cpm_communities_at_res=0.005"] == before
    assert "x" not in g.vs.attributes()


def test_no_layouts_still_writes_the_output_directory(tmp_path):
    save_citation_layouts(citation_layouts(_two_groups(), layout_specs([])), tmp_path / "out")
    assert json.loads((tmp_path / "out" / "citation_layouts.json").read_text()) == {}


def test_layout_specs_are_checked():
    with pytest.raises(ValueError, match="key"):
        layout_specs([{"lin_log": True}])
    with pytest.raises(ValueError, match="dimensions"):
        layout_specs([{"key": "a", "dimensions": 4}])
    with pytest.raises(ValueError, match="duplicate"):
        layout_specs([{"key": "a"}, {"key": "a"}])
