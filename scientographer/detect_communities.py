# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Leiden/CPM community detection across the whole resolution sweep, written onto
one graph.

Leiden with the constant Potts model (``leidenalg.CPMVertexPartition``,
unweighted, one seed, ``n_iterations`` refinement passes) at every resolution of
``community_resolution_bands.RESOLUTIONS``; all of them land on ONE output graphml
as ``cpm_communities_at_res=<r>`` vertex columns. The optional pre-detection
degree filter is a parameter and off by default: detect on the full graph and
filter *communities* afterwards, which commutes with detection, whereas a
pre-detection filter changes what is detected. The run settings are recorded as
graph-level attributes (``leiden_seed``, ``leiden_iterations``,
``leiden_quality_function``) so the graph itself says how its communities were
made.

Community ids within a resolution are numbered by size, largest = 0 (leidenalg's
convention), which every downstream DAG relies on.

Outputs (paths from params.yaml ``detect_communities``):
  <output_graphml>          the input graph + one community column per resolution
  <summary_parquet>         one row per resolution: number of communities, singletons,
                            largest community, nodes in communities of >= 2 members
"""

import logging
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import leidenalg
import numpy as np
import pandas as pd

from scientographer._resolution_store import (
    STORE_SUBDIR,
    ResolutionStore,
    library_versions,
    reuse_or_compute,
    structure_fingerprint,
)
from scientographer.community_resolution_bands import RESOLUTIONS, community_attribute_name
from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("detect_communities")
INPUT_GRAPHML: Final[Path] = Path(_cfg["input_graphml"])
OUTPUT_GRAPHML: Final[Path] = Path(_cfg["output_graphml"])
SUMMARY_PARQUET: Final[Path] = Path(_cfg["summary_parquet"])
SEED: Final[int] = int(_cfg["seed"])
ITERATIONS: Final[int] = int(_cfg["iterations"])
MIN_DEGREE: Final[int] = int(_cfg.get("min_degree", 0) or 0)
# Per-resolution memberships kept between runs (see _resolution_store.py): adding a
# resolution to the sweep runs Leiden for that resolution only. A DVC output with
# `persist: true`.
STORE_DIR: Final[Path] = Path(_cfg.get("store_dir") or Path(_cfg["summary_parquet"]).parent / STORE_SUBDIR)
# Bump when a change alters the memberships this stage computes.
RESULTS_VERSION: Final[int] = 1



#####################
##  Aux Functions  ##
#####################
def _filter_by_degree(graph: ig.Graph, min_degree: int) -> ig.Graph:
    """Drop vertices whose total degree (in + out, loops ignored) is below
    ``min_degree``. ``min_degree <= 1`` keeps every vertex. Returns a copy."""
    if min_degree <= 1:
        return graph.copy()
    degrees = graph.degree(mode="all", loops=False)
    keep = [v.index for v, d in zip(graph.vs, degrees) if d >= min_degree]
    return graph.induced_subgraph(keep)


def _leiden_cpm_membership(
    graph: ig.Graph, resolution: float, seed: int, n_iterations: int
) -> list[int]:
    """One Leiden/CPM run; community ids numbered by size, largest first."""
    partition = leidenalg.find_partition(
        graph,
        leidenalg.CPMVertexPartition,
        resolution_parameter=resolution,
        initial_membership=None,
        weights=None,
        node_sizes=None,
        seed=seed,
        n_iterations=n_iterations,
    )
    return list(partition.membership)


def _attach_memberships(graph: ig.Graph, memberships: dict[float, list[int]]) -> ig.Graph:
    for resolution, membership in memberships.items():
        if len(membership) != graph.vcount():
            raise ValueError(
                f"membership length {len(membership)} != vertex count {graph.vcount()} at resolution {resolution}"
            )
        graph.vs[community_attribute_name(resolution)] = membership
    return graph


def _partition_summary(graph: ig.Graph, resolutions: list[float]) -> pd.DataFrame:
    rows = []
    for resolution in resolutions:
        sizes = pd.Series(graph.vs[community_attribute_name(resolution)]).value_counts()
        rows.append(
            {
                "resolution": resolution,
                "number_of_communities": int(len(sizes)),
                "number_of_singleton_communities": int((sizes == 1).sum()),
                "largest_community_size": int(sizes.max()) if len(sizes) else 0,
                "nodes_in_communities_with_at_least_2_members": int(sizes[sizes >= 2].sum())
                if len(sizes)
                else 0,
            }
        )
    return pd.DataFrame(rows)


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH)
    inputs = dict(
        input_graphml_path=INPUT_GRAPHML,
        min_degree=MIN_DEGREE,
        seed=SEED,
        n_iterations=ITERATIONS,
        output_graphml_path=OUTPUT_GRAPHML,
        summary_parquet_path=SUMMARY_PARQUET,
        store_dir=STORE_DIR,
    )
    outputs = ["save_citation_network_with_communities", "save_partition_summary"]

    import __main__

    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
        .build()
    )
    dr.validate_execution(outputs, inputs=inputs)
    draw_dag(dr, CURRENT_FILE_NAME, outputs, inputs)
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@dataloader()
def citation_network(input_graphml_path: Path) -> tuple[ig.Graph, dict]:
    graph = ig.Graph.Read_GraphML(str(input_graphml_path))
    logger.info(
        "read %s: %d vertices, %d edges", input_graphml_path, graph.vcount(), graph.ecount()
    )
    return graph, utils.get_file_metadata(input_graphml_path)


def filtered_citation_network(citation_network: ig.Graph, min_degree: int) -> ig.Graph:
    """The graph Leiden runs on. With the default ``min_degree`` of 0 this is the
    input graph unchanged; a pre-detection degree cut is the one non-idempotent
    step in the pipeline, so it is off unless asked for."""
    filtered = _filter_by_degree(citation_network, min_degree)
    if filtered.vcount() != citation_network.vcount():
        logger.info(
            "degree filter (< %d) dropped %d vertices",
            min_degree,
            citation_network.vcount() - filtered.vcount(),
        )
    return filtered


def _store(graph: ig.Graph, seed: int, n_iterations: int, store_dir: Path) -> ResolutionStore:
    return ResolutionStore(
        store_dir, "detect_communities", RESULTS_VERSION,
        {"seed": seed, "n_iterations": n_iterations, "graph": structure_fingerprint(graph),
         "libraries": library_versions("leidenalg", "igraph")})


def memberships_by_resolution(
    filtered_citation_network: ig.Graph, seed: int, n_iterations: int, store_dir: Path
) -> dict[float, list[int]]:
    """Leiden/CPM membership at every resolution of the sweep; memberships stored
    by an earlier run for the same graph and settings are reused."""
    store = _store(filtered_citation_network, seed, n_iterations, store_dir)

    def compute(missing: list[float]) -> dict[float, dict]:
        computed = {}
        for resolution in missing:
            membership = _leiden_cpm_membership(filtered_citation_network, resolution, seed, n_iterations)
            logger.info("[res=%s] %d communities", resolution, len(set(membership)))
            computed[resolution] = {"membership": np.asarray(membership, dtype=np.int64)}
        return computed

    results = reuse_or_compute(store, RESOLUTIONS, {r: store.fingerprint(r) for r in RESOLUTIONS}, compute)
    return {r: [int(c) for c in results[r]["membership"]] for r in RESOLUTIONS}


def citation_network_with_communities(
    filtered_citation_network: ig.Graph,
    memberships_by_resolution: dict[float, list[int]],
    seed: int,
    n_iterations: int,
) -> ig.Graph:
    graph = _attach_memberships(filtered_citation_network, memberships_by_resolution)
    # The graph records how its communities were made (graph-level attributes;
    # igraph round-trips them, Gephi ignores them).
    graph["leiden_quality_function"] = "CPM"
    graph["leiden_seed"] = seed
    graph["leiden_iterations"] = n_iterations
    graph["leiden_resolutions"] = ",".join(str(r) for r in memberships_by_resolution)
    return graph


def partition_summary_df(citation_network_with_communities: ig.Graph) -> pd.DataFrame:
    return _partition_summary(citation_network_with_communities, RESOLUTIONS)


@datasaver()
def save_citation_network_with_communities(
    citation_network_with_communities: ig.Graph, output_graphml_path: Path
) -> dict:
    Path(output_graphml_path).parent.mkdir(parents=True, exist_ok=True)
    citation_network_with_communities.write_graphml(str(output_graphml_path))
    return utils.get_file_metadata(output_graphml_path)


@datasaver()
def save_partition_summary(partition_summary_df: pd.DataFrame, summary_parquet_path: Path) -> dict:
    Path(summary_parquet_path).parent.mkdir(parents=True, exist_ok=True)
    partition_summary_df.to_parquet(summary_parquet_path, index=False)
    return utils.get_file_metadata(summary_parquet_path)


if __name__ == "__main__":
    sys.exit(_main())
