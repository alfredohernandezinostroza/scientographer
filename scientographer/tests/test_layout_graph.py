# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import igraph as ig
import pandas as pd
import pytest

from scientographer.layout_graph import (
    _has_complete_layout,
    _imported_positions,
    _positions_from_file,
    _with_positions,
    citation_network_with_layout,
)


def _ring(n=6) -> ig.Graph:
    g = ig.Graph.Ring(n, directed=True)
    g.vs["name"] = [f"doi{i}" for i in range(n)]
    return g


def test_has_complete_layout_requires_both_columns_without_nulls():
    g = _ring()
    assert not _has_complete_layout(g)
    g.vs["x"] = [1.0] * 6
    assert not _has_complete_layout(g)
    g.vs["y"] = [1.0] * 5 + [None]
    assert not _has_complete_layout(g)
    g.vs["y"] = [2.0] * 6
    assert _has_complete_layout(g)


def test_positions_from_graphml_and_csv(tmp_path):
    src = _ring()
    src.vs["x"] = [float(i) for i in range(6)]
    src.vs["y"] = [float(-i) for i in range(6)]
    gml = tmp_path / "layout.graphml"
    src.write_graphml(str(gml))
    assert _positions_from_file(gml)["doi3"] == (3.0, -3.0)

    csv = tmp_path / "nodes.csv"
    pd.DataFrame({"Id": ["doi0", "doi1"], "X": [1.5, 2.5], "Y": [0.0, 1.0]}).to_csv(csv, index=False)
    assert _positions_from_file(csv)["doi1"] == (2.5, 1.0)


def test_imported_positions_follow_graph_vertex_order_and_reject_gaps(tmp_path):
    src = _ring()
    src.vs["x"] = [float(i) for i in range(6)]
    src.vs["y"] = [0.0] * 6
    gml = tmp_path / "layout.graphml"
    src.write_graphml(str(gml))
    target = _ring()
    target = target.permute_vertices([5, 4, 3, 2, 1, 0])  # different vertex order, same names
    xs, _ = _imported_positions(target, gml)
    assert xs == [float(int(n[3:])) for n in target.vs["name"]]

    smaller = ig.Graph(directed=True)
    smaller.add_vertices(2)
    smaller.vs["name"] = ["doi0", "unknown"]
    with pytest.raises(ValueError, match="no position"):
        _imported_positions(smaller, gml)


def test_with_positions_records_mode_on_the_graph():
    g = _with_positions(_ring(), [0.0] * 6, [1.0] * 6, "import", None)
    assert g.vs["y"] == [1.0] * 6
    assert g["layout_mode"] == "import" and g["layout_iterations"] == -1


def test_existing_layout_is_passed_through_unless_overwrite(tmp_path):
    g = _ring()
    g.vs["x"] = [1.0] * 6
    g.vs["y"] = [2.0] * 6
    out = citation_network_with_layout(g, "import", 10, None, overwrite_existing=False)
    assert out.vs["x"] == [1.0] * 6 and "layout_mode" not in out.attributes()
    with pytest.raises(ValueError, match="import_path"):
        citation_network_with_layout(g, "import", 10, None, overwrite_existing=True)


def test_forceatlas2_mode_lays_out_a_small_graph():
    pytest.importorskip("fa2")
    g = citation_network_with_layout(_ring(), "forceatlas2", 20, None, overwrite_existing=False)
    assert _has_complete_layout(g) and g["layout_mode"] == "forceatlas2"
    assert len(set(zip(g.vs["x"], g.vs["y"]))) > 1  # not every vertex on one point
