# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fill the per-resolution stores (see _resolution_store.py) from a project's
existing outputs, so a project whose outputs predate the stores does not have
to recompute every resolution the first time it adds one.

Each stage's fingerprints are computed by the stage's own code from its current
inputs; the stored results are read back from its current output tables. This
trusts that those outputs were produced from the current inputs and settings:
run it only when ``dvc status`` reports the stages up to date.
"""

import logging
from pathlib import Path

from hamilton import driver
import igraph as ig
import numpy as np
import pandas as pd

from scientographer._resolution_store import array_fingerprint

logger = logging.getLogger(__name__)

STAGES = (
    "detect_communities",
    "community_quality_metrics",
    "community_connectivity_metrics",
    "community_connectivity_modifier",
    "community_quality_metrics_after_connectivity_modifier",
)


def _execute(module, nodes: list[str], inputs: dict) -> dict:
    return driver.Builder().with_modules(module).build().execute(nodes, inputs=inputs)


def _rows(table: pd.DataFrame, resolution: float) -> list[dict]:
    return table[table["resolution"] == resolution].to_dict("records")


def _partition(table: pd.DataFrame, resolution: float, drop: tuple[str, ...] = ()) -> dict:
    (row,) = _rows(table, resolution)
    return {k: v for k, v in row.items() if k not in drop}


def _seed_detect_communities() -> int:
    from scientographer import detect_communities as m

    graph = _execute(m, ["filtered_citation_network"],
                     {"input_graphml_path": m.INPUT_GRAPHML, "min_degree": m.MIN_DEGREE})["filtered_citation_network"]
    output = ig.Graph.Read_GraphML(str(m.OUTPUT_GRAPHML))
    if output.vs["name"] != graph.vs["name"]:
        raise ValueError(f"{m.OUTPUT_GRAPHML} does not have the vertices of the current input graph")
    store = m._store(graph, m.SEED, m.ITERATIONS, m.STORE_DIR)
    stored = 0
    for resolution in m.RESOLUTIONS:
        attribute = m.community_attribute_name(resolution)
        if attribute not in output.vs.attributes():
            continue
        membership = np.asarray([int(float(c)) for c in output.vs[attribute]], dtype=np.int64)
        store.put(resolution, store.fingerprint(resolution), {"membership": membership})
        stored += 1
    return stored


def _seed_metrics_stage(m, memberships_node: str, extra_nodes: list[str], inputs: dict,
                        per_community_path: Path, per_partition_path: Path, store,
                        drop: tuple[str, ...] = ()) -> int:
    values = _execute(m, [*extra_nodes, memberships_node], inputs)
    per_community = pd.read_parquet(per_community_path)
    per_partition = pd.read_parquet(per_partition_path)
    done = set(per_partition["resolution"])
    stored = 0
    for resolution, membership in zip(m.RESOLUTIONS, values[memberships_node]):
        if membership is None or resolution not in done:
            continue
        bundle = {"resolution": resolution, "per_community": _rows(per_community, resolution),
                  "per_partition": _partition(per_partition, resolution, drop)}
        store.put(resolution, store.fingerprint(resolution, membership=array_fingerprint(membership)), bundle)
        stored += 1
    return stored


def _seed_community_quality_metrics() -> int:
    from scientographer import community_quality_metrics as m

    graph = _execute(m, ["citation_network"], {"citation_network_path": m.INPUT_GRAPHML})["citation_network"]
    plateau = ("resolution_plateau_nmi_with_previous", "resolution_plateau_nmi_with_next", "is_on_resolution_plateau")
    return _seed_metrics_stage(
        m, "community_memberships_by_resolution", [], {"citation_network_path": m.INPUT_GRAPHML},
        m.PER_COMMUNITY_PARQUET, m.PER_PARTITION_PARQUET, m._store(graph, m.OUTPUT_DIR / m.STORE_SUBDIR), plateau)


def _seed_community_connectivity_metrics() -> int:
    from scientographer import community_connectivity_metrics as m

    graph = _execute(m, ["citation_network"], {"citation_network_path": m.INPUT_GRAPHML})["citation_network"]
    return _seed_metrics_stage(
        m, "community_memberships_by_resolution", [], {"citation_network_path": m.INPUT_GRAPHML},
        m.PER_COMMUNITY_PARQUET, m.PER_PARTITION_PARQUET, m._store(graph, m.OUTPUT_DIR / m.STORE_SUBDIR))


def _seed_community_connectivity_modifier() -> int:
    from scientographer import community_connectivity_modifier as m

    values = _execute(m, ["undirected_simple_graph", "community_memberships_by_resolution"],
                      {"citation_network_path": m.INPUT_GRAPHML})
    graph = values["undirected_simple_graph"]
    store = m._store(graph, m.CM_MIN_CLUSTER_SIZE, m.OUTPUT_DIR / m.STORE_SUBDIR)
    membership_table = pd.read_parquet(m.MEMBERSHIP_PARQUET)
    if list(membership_table["node_name"]) != list(graph.vs["name"]):
        raise ValueError(f"{m.MEMBERSHIP_PARQUET} does not have the vertices of the current graph")
    per_community = pd.read_parquet(m.PER_COMMUNITY_PARQUET)
    per_partition = pd.read_parquet(m.PER_PARTITION_PARQUET)
    stored = 0
    for resolution, membership in zip(m.RESOLUTIONS, values["community_memberships_by_resolution"]):
        column = f"connectivity_modified_community_at_res={resolution}"
        if column not in membership_table.columns:
            continue
        bundle = {"resolution": resolution,
                  "new_membership": membership_table[column].to_numpy().astype(int),
                  "per_community": _rows(per_community, resolution),
                  "per_partition": _partition(per_partition, resolution)}
        store.put(resolution, store.fingerprint(resolution, membership=array_fingerprint(membership)), bundle)
        stored += 1
    return stored


def _seed_community_quality_metrics_after_connectivity_modifier() -> int:
    from scientographer import community_quality_metrics_after_connectivity_modifier as m

    inputs = {"citation_network_path": m.INPUT_GRAPHML, "cm_membership_path": m.CM_MEMBERSHIP_PARQUET}
    graph = _execute(m, ["citation_network"], inputs)["citation_network"]
    return _seed_metrics_stage(
        m, "after_memberships_by_resolution", [], inputs,
        m.PER_COMMUNITY_PARQUET, m.PER_PARTITION_PARQUET, m._store(graph, m.OUTPUT_DIR / m.STORE_SUBDIR))


def seed_stores(stages=STAGES) -> dict[str, int]:
    """Store every resolution each stage's current outputs hold; returns how many
    resolutions were stored per stage."""
    seeders = {
        "detect_communities": _seed_detect_communities,
        "community_quality_metrics": _seed_community_quality_metrics,
        "community_connectivity_metrics": _seed_community_connectivity_metrics,
        "community_connectivity_modifier": _seed_community_connectivity_modifier,
        "community_quality_metrics_after_connectivity_modifier":
            _seed_community_quality_metrics_after_connectivity_modifier,
    }
    return {stage: seeders[stage]() for stage in stages}
