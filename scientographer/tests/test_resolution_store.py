# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-resolution results kept between runs (scientographer/_resolution_store.py)."""

import math

import igraph as ig
import numpy as np

from scientographer._resolution_store import (
    ResolutionStore,
    array_fingerprint,
    result_settings,
    reuse_or_compute,
    structure_fingerprint,
)


def _bundle():
    return {
        "resolution": 0.01,
        "new_membership": np.array([0, 0, -1, 1], dtype=np.int64),
        "per_community": [{"resolution": 0.01, "community_id": 0, "size": 2, "density": 0.5, "flag": True},
                          {"resolution": 0.01, "community_id": 1, "size": 1, "density": float("nan"), "flag": False}],
        "per_partition": {"resolution": 0.01, "modularity": 0.1234567890123, "number": np.int64(2),
                          "nmi": float("nan")},
        "empty": [],
    }


def _same(a, b) -> bool:
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, np.ndarray):
        return np.array_equal(a, b) and a.dtype == b.dtype
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    return a == b


def test_round_trip_is_exact_and_needs_the_same_fingerprint(tmp_path):
    store = ResolutionStore(tmp_path / "by_resolution", "stage", 1, {"graph": "g"})
    fp = store.fingerprint(0.01, membership="m")
    store.put(0.01, fp, _bundle())
    assert _same(store.get(0.01, fp), _bundle())
    assert store.get(0.01, store.fingerprint(0.01, membership="other")) is None
    assert store.get(0.02, fp) is None


def test_fingerprint_covers_version_context_resolution_and_inputs(tmp_path):
    a = ResolutionStore(tmp_path, "stage", 1, {"graph": "g"})
    assert a.fingerprint(0.01, membership="m") == ResolutionStore(tmp_path, "stage", 1, {"graph": "g"}).fingerprint(
        0.01, membership="m")
    assert a.fingerprint(0.01, membership="m") != ResolutionStore(tmp_path, "stage", 2, {"graph": "g"}).fingerprint(
        0.01, membership="m")
    assert a.fingerprint(0.01, membership="m") != ResolutionStore(tmp_path, "stage", 1, {"graph": "h"}).fingerprint(
        0.01, membership="m")
    assert a.fingerprint(0.01, membership="m") != a.fingerprint(0.02, membership="m")


def test_an_interrupted_write_is_never_reused(tmp_path):
    store = ResolutionStore(tmp_path, "stage", 1, {})
    fp = store.fingerprint(0.01)
    store.put(0.01, fp, _bundle())
    (tmp_path / "0.01" / "fingerprint.json").unlink()  # as if the run died mid-write
    assert store.get(0.01, fp) is None


def test_reuse_or_compute_computes_only_the_missing_resolutions(tmp_path):
    store = ResolutionStore(tmp_path, "stage", 1, {})
    calls = []

    def compute(missing):
        calls.append(list(missing))
        return {r: {"resolution": r, "value": [{"x": r}]} for r in missing}

    fps = {r: store.fingerprint(r) for r in (0.1, 0.2, 0.3)}
    reuse_or_compute(store, [0.1, 0.2], fps, compute)
    results = reuse_or_compute(store, [0.1, 0.2, 0.3], fps, compute)
    assert calls == [[0.1, 0.2], [0.3]]
    assert [results[r]["value"][0]["x"] for r in (0.1, 0.2, 0.3)] == [0.1, 0.2, 0.3]


def test_structure_and_membership_fingerprints():
    g = ig.Graph(n=3, edges=[(0, 1), (1, 2)], directed=True)
    g.vs["name"] = ["a", "b", "c"]
    h = g.copy()
    h.vs["title"] = ["x", "y", "z"]  # attributes other than names do not matter
    assert structure_fingerprint(g) == structure_fingerprint(h)
    h.add_edges([(2, 0)])
    assert structure_fingerprint(g) != structure_fingerprint(h)
    assert array_fingerprint([0, 1, 1]) == array_fingerprint(np.array([0, 1, 1], dtype=np.int32))
    assert array_fingerprint([0, 1, 1]) != array_fingerprint([0, 1, 2])
    assert result_settings({"workers": 8, "a": 1, "b": 2}, ignore=("b",)) == {"a": 1}
