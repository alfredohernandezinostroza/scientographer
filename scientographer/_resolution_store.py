# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-resolution results kept between runs, so changing the resolution sweep
computes only the resolutions that are new.

Each stage that sweeps resolutions stores what it computed for every resolution
under ``<store directory>/<resolution>/`` (by default ``by_resolution/`` inside the
stage's output directory), stamped with a
fingerprint of everything that result depends on: the stage and its
``RESULTS_VERSION``, the graph's structure, that resolution's partition, the
stage's settings and the versions of the libraries that compute it. On the next
run a stored result is reused only when the fingerprint matches; anything else
is recomputed. The store sits inside the stage's DVC output (declared
``persist: true`` in dvc.yaml), so it is versioned and shared with the data.

A stage's ``RESULTS_VERSION`` must be bumped whenever a code change alters its
results; cosmetic changes (logging, refactoring) leave stored results valid.

Results are bundles: dicts whose values are lists of row dicts (stored as
Parquet), dicts of scalars (JSON), numpy arrays (.npy) or scalars.
"""

from collections.abc import Iterable
import hashlib
from importlib import metadata
import json
import logging
from pathlib import Path
import shutil
from typing import Any, Optional

import igraph as ig
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

STORE_SUBDIR = "by_resolution"
_FINGERPRINT_FILE = "fingerprint.json"
_SCALARS_FILE = "scalars.json"


def _sha256(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "little"))
        digest.update(part)
    return digest.hexdigest()


def structure_fingerprint(graph: ig.Graph) -> str:
    """The graph's vertices (names, in order), edges and direction."""
    edges = np.asarray(graph.get_edgelist(), dtype=np.int64).tobytes()
    names = "\n".join(str(v) for v in graph.vs["name"]).encode() if "name" in graph.vs.attributes() else b""
    return _sha256(str(graph.vcount()).encode(), str(graph.is_directed()).encode(), edges, names)


def array_fingerprint(values) -> str:
    return _sha256(np.asarray(values, dtype=np.int64).tobytes())


def library_versions(*packages: str) -> dict[str, str]:
    versions = {}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "absent"
    return versions


def result_settings(section: dict, ignore: Iterable[str] = ()) -> dict:
    """A params.yaml section minus the settings that cannot change a stage's
    results (`workers`, plus `ignore`), for a store's context."""
    skipped = {"workers", *ignore}
    return {k: v for k, v in section.items() if k not in skipped}


def _to_json_value(value):
    if isinstance(value, np.generic):
        return value.item()
    return value


class ResolutionStore:
    """The stored per-resolution results of one stage."""

    def __init__(self, directory: Path, stage: str, results_version: int, context: dict):
        self.directory = Path(directory)
        self.stage = stage
        self._context = {"stage": stage, "results_version": results_version, **context}

    def fingerprint(self, resolution: float, **inputs: str) -> str:
        payload = json.dumps({**self._context, "resolution": float(resolution), "inputs": inputs},
                             sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _entry(self, resolution: float) -> Path:
        return self.directory / str(resolution)

    def get(self, resolution: float, fingerprint: str) -> Optional[dict]:
        entry = self._entry(resolution)
        try:
            stored = json.loads((entry / _FINGERPRINT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if stored.get("fingerprint") != fingerprint:
            return None
        bundle: dict[str, Any] = json.loads((entry / _SCALARS_FILE).read_text(encoding="utf-8"))
        for name, kind in stored["fields"].items():
            if kind == "rows":
                bundle[name] = pd.read_parquet(entry / f"{name}.parquet").to_dict("records")
            elif kind == "empty_rows":
                bundle[name] = []
            elif kind == "dict":
                bundle[name] = json.loads((entry / f"{name}.json").read_text(encoding="utf-8"))
            elif kind == "array":
                bundle[name] = np.load(entry / f"{name}.npy", allow_pickle=False)
        return bundle

    def put(self, resolution: float, fingerprint: str, bundle: dict) -> None:
        entry = self._entry(resolution)
        if entry.exists():
            shutil.rmtree(entry)
        entry.mkdir(parents=True)
        fields, scalars = {}, {}
        for name, value in bundle.items():
            if isinstance(value, np.ndarray):
                np.save(entry / f"{name}.npy", value, allow_pickle=False)
                fields[name] = "array"
            elif isinstance(value, list):
                if value:
                    pd.DataFrame(value).to_parquet(entry / f"{name}.parquet")
                    fields[name] = "rows"
                else:
                    fields[name] = "empty_rows"
            elif isinstance(value, dict):
                (entry / f"{name}.json").write_text(
                    json.dumps({k: _to_json_value(v) for k, v in value.items()}), encoding="utf-8")
                fields[name] = "dict"
            else:
                scalars[name] = _to_json_value(value)
        (entry / _SCALARS_FILE).write_text(json.dumps(scalars), encoding="utf-8")
        # Written last: an entry without it (an interrupted write) is never reused.
        (entry / _FINGERPRINT_FILE).write_text(
            json.dumps({"fingerprint": fingerprint, "stage": self.stage, "resolution": float(resolution),
                        "fields": fields}), encoding="utf-8")


def reuse_or_compute(
    store: ResolutionStore,
    resolutions: Iterable[float],
    fingerprints: dict[float, str],
    compute,
) -> dict[float, dict]:
    """Stored results where the fingerprints match, `compute(missing)` (a function
    returning {resolution: bundle}) for the rest, which are then stored."""
    resolutions = list(resolutions)
    results = {}
    for resolution in resolutions:
        bundle = store.get(resolution, fingerprints[resolution])
        if bundle is not None:
            results[resolution] = bundle
    missing = [r for r in resolutions if r not in results]
    logger.info("%s: %d resolution(s) reused from %s, %d to compute%s", store.stage,
                len(results), store.directory, len(missing), f": {missing}" if missing else "")
    if missing:
        computed = compute(missing)
        for resolution in missing:
            store.put(resolution, fingerprints[resolution], computed[resolution])
            results[resolution] = computed[resolution]
    return results
