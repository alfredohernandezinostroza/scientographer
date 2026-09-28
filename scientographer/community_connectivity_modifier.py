"""Connectivity Modifier (CM) remediation of the Leiden/CPM communities.

Where community_connectivity_metrics.py *measures* well-connectedness (does each
community's minimum edge cut exceed f(n) = log10(n)?), this module *enforces* it.
It implements the Connectivity Modifier of Park, Tabatabaee, ... Warnow,
"Identifying Well-Connected Communities in Real-World and Synthetic Networks"
(COMPLEX NETWORKS 2023): a post-processing algorithm that turns any partition
into one whose every community is well-connected.

Per the paper's pipeline (their Fig. 2), for each resolution's existing partition:
  1. pre-process: drop communities smaller than B, and tree communities;
  2. for each community, compute its global minimum edge cut. If the cut is
     <= f(n) = log10(n), delete the cut edges (splitting the community) and
     re-cluster each resulting piece with Leiden-CPM at the same resolution;
  3. recurse on the pieces until no community has a cut <= f(n);
  4. post-process: drop any community left smaller than B.

Every original community is processed independently -- CM only ever cuts and
re-clusters *within* a community, never merges across them -- so each surviving
well-connected community carries the id of the original community it came from,
and each original community is classified by what CM did to it (the paper's Fig 3
taxonomy):
  extant   -> unchanged (one surviving piece, all nodes kept)
  reduced  -> one surviving piece, but some nodes shed
  split    -> two or more surviving well-connected pieces
  degraded -> dissolved entirely (no piece survived the size-B floor)

This is the remediation counterpart to the diagnostic in
community_connectivity_metrics.py and reuses its helpers. Like that module it
works on the undirected, simplified graph the well-connectedness bound is defined
on (self-loops dropped, reciprocal/parallel citations collapsed to single edges).
The cost of remediation is node coverage: weakly-attached papers fall into
sub-threshold pieces and are dropped (reported as coverage before/after).

Outputs (data/graph_level_data/community_connectivity_modifier/):
  connectivity_modifier_membership.parquet
    node_name x connectivity_modified_community_at_res=<r>  (-1 = removed by CM)
  connectivity_modifier_per_community.parquet
    long form: one row per surviving well-connected community, with its origin
    community and a re-checked minimum edge cut (is_well_connected is True by
    construction)
  connectivity_modifier_per_partition.parquet
    long form: resolution x {community counts, node coverage before/after,
    extant/reduced/split/degraded taxonomy}
"""

import sys
import math
import logging
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import igraph as ig
import leidenalg

from hamilton.function_modifiers import dataloader, datasaver, value, source, group, parameterize
from hamilton.io import utils
from hamilton_sdk import adapters
from hamilton import driver
import hamilton.log_setup

from motor_learning_network.constants import (
    GRAPH_LEVEL_DATA_PATH,
    FIGURES_PATH,
    params,
    tracker_adapters,
)
from motor_learning_network.community_connectivity_metrics import (
    RESOLUTIONS,
    SUBSTANTIVE_COMMUNITY_MIN_SIZE,
    _community_attribute_name,
    _community_vertex_groups,
    _well_connectedness_threshold,
)
from motor_learning_network.community_resolution_bands import (
    LOW_RES_GRAPHML,
    merge_higher_band_communities,
)

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

# Minimum allowed community size (the paper's B, default 11). Communities smaller
# than this are dropped both before CM runs and after it finishes cutting, so B is
# also what makes CM shed node coverage. Communities that are trees are dropped
# too (a tree's minimum cut is 1, poorly connected for any tree of ten+ nodes).
CM_MIN_CLUSTER_SIZE: Final[int] = int(params("communities")["connectivity_modifier_min_cluster_size"])

# Seed for the Leiden-CPM re-clustering of cut pieces, so remediation is
# reproducible. Matches the deterministic re-clustering the paper's CM performs.
CM_RECLUSTER_SEED: Final[int] = 0
CM_RECLUSTER_ITERATIONS: Final[int] = 2

INPUT_GRAPHML: Final[Path] = LOW_RES_GRAPHML
OUTPUT_DIR: Final[Path] = Path(params("graph")["analysis_output_dir"]) / "community_connectivity_modifier"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MEMBERSHIP_PARQUET: Final[Path] = OUTPUT_DIR / "connectivity_modifier_membership.parquet"
PER_COMMUNITY_PARQUET: Final[Path] = OUTPUT_DIR / "connectivity_modifier_per_community.parquet"
PER_PARTITION_PARQUET: Final[Path] = OUTPUT_DIR / "connectivity_modifier_per_partition.parquet"


#####################
##  Aux Functions  ##
#####################
def _undirected_simple_graph(graph: ig.Graph) -> ig.Graph:
    """Undirected, simplified copy on the same vertex set (indices preserved):
    reciprocal and parallel citations collapse to single undirected edges and
    self-loops are dropped, so minimum cut and re-clustering both run on the
    simple undirected structure f(n) = log10(n) is defined for."""
    undirected = graph.copy()
    undirected.to_undirected(mode="collapse")
    undirected.simplify(multiple=True, loops=True)
    return undirected


def _leiden_cpm_subclusters(subgraph: ig.Graph, resolution: float) -> list[list[int]]:
    """Re-cluster an (undirected) subgraph with Leiden-CPM at this resolution;
    return the communities as lists of subgraph-local vertex indices."""
    partition = leidenalg.find_partition(
        subgraph,
        leidenalg.CPMVertexPartition,
        resolution_parameter=resolution,
        seed=CM_RECLUSTER_SEED,
        n_iterations=CM_RECLUSTER_ITERATIONS,
    )
    return [list(community) for community in partition]


def _connectivity_modifier_on_cluster(
    undirected_graph: ig.Graph,
    vertex_ids: list[int],
    resolution: float,
    min_cluster_size: int,
) -> list[list[int]]:
    """Run the Connectivity Modifier on a single community and return the surviving
    well-connected pieces as lists of original vertex indices.

    Iteratively (an explicit stack replaces the paper's recursion): a piece with
    fewer than min_cluster_size nodes, or one that is a tree, is dropped; a piece
    whose minimum edge cut exceeds f(n) = log10(n) is well-connected and kept; a
    piece with a cut <= f(n) has that cut removed and each side re-clustered with
    Leiden-CPM, pushing the results back on the stack. Each cut splits a piece into
    two strictly smaller sides, so sizes strictly decrease down every branch and
    the process terminates."""
    surviving: list[list[int]] = []
    stack: list[list[int]] = [list(vertex_ids)]
    while stack:
        piece = stack.pop()
        size = len(piece)
        if size < min_cluster_size:
            continue  # dropped by the size-B floor
        subgraph = undirected_graph.induced_subgraph(piece)
        if subgraph.is_connected() and subgraph.ecount() == size - 1:
            continue  # a tree: minimum cut 1, poorly connected -> dropped

        cut = subgraph.mincut()
        if cut.value > _well_connectedness_threshold(size):
            surviving.append(piece)  # well-connected -> keep
            continue

        # Poorly connected: remove the minimum cut (the two sides of cut.partition)
        # and re-cluster each side, then re-examine the resulting pieces.
        for side in cut.partition:
            side_vertices = [piece[local_index] for local_index in side]
            side_subgraph = undirected_graph.induced_subgraph(side_vertices)
            for local_community in _leiden_cpm_subclusters(side_subgraph, resolution):
                stack.append([side_vertices[local_index] for local_index in local_community])
    return surviving


def _classify_transformation(original_size: int, surviving_pieces: int, surviving_nodes: int) -> str:
    """The paper's Fig. 3 taxonomy of what CM did to an original community."""
    if surviving_pieces == 0:
        return "degraded"
    if surviving_pieces == 1 and surviving_nodes == original_size:
        return "extant"
    if surviving_pieces == 1:
        return "reduced"
    return "split"


##################
##     Main     ##
##################
def _main() -> int:
    inputs = dict(
        citation_network_path=INPUT_GRAPHML,
        min_cluster_size=CM_MIN_CLUSTER_SIZE,
    )
    outputs = [
        "save_connectivity_modifier_membership",
        "save_connectivity_modifier_per_community_metrics",
        "save_connectivity_modifier_per_partition_metrics",
    ]
    import __main__
    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
        .build()
    )
    dr.validate_execution(outputs, inputs=inputs)
    dr.display_all_functions(
        FIGURES_PATH / f"{CURRENT_FILE_NAME}_all_functions.png",
        keep_dot=True, deduplicate_inputs=True,
    )
    dr.visualize_execution(
        outputs, inputs=inputs,
        output_file_path=FIGURES_PATH / f"{CURRENT_FILE_NAME}.png",
        keep_dot=False, deduplicate_inputs=True,
    )
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@dataloader()
def citation_network(citation_network_path: Path) -> tuple[ig.Graph, dict]:
    # citation_network_path is the low-res base; graft on the mid/high
    # `cpm_communities_at_res=*` columns so this graph carries all of RESOLUTIONS.
    graph = ig.Graph.Read_GraphML(str(citation_network_path))
    merge_higher_band_communities(graph)
    metadata = utils.get_file_metadata(citation_network_path)
    return graph, metadata


def undirected_simple_graph(citation_network: ig.Graph) -> ig.Graph:
    return _undirected_simple_graph(citation_network)


@parameterize(**{
    f"community_membership_at_resolution_{r}": {"resolution": value(r)} for r in RESOLUTIONS
})
def community_membership_for_resolution(citation_network: ig.Graph, resolution: float) -> np.ndarray:
    """Per-vertex community id at this resolution, read from the graph's
    existing `cpm_communities_at_res=<r>` attribute (stored as floats)."""
    attribute_name = _community_attribute_name(resolution)
    return np.array([int(float(v)) for v in citation_network.vs[attribute_name]])


@parameterize(**{
    f"connectivity_modifier_at_resolution_{r}": {
        "resolution": value(r),
        "community_membership": source(f"community_membership_at_resolution_{r}"),
    } for r in RESOLUTIONS
})
def connectivity_modifier_for_resolution(
    undirected_simple_graph: ig.Graph,
    resolution: float,
    community_membership: np.ndarray,
    min_cluster_size: int,
) -> dict:
    """Run the Connectivity Modifier on every community of this resolution's
    partition, producing a new partition in which each surviving community is
    well-connected. Returns the new per-vertex membership (-1 = removed), one row
    per surviving community, and a partition-level before/after summary."""
    groups = _community_vertex_groups(community_membership)
    total_nodes = undirected_simple_graph.vcount()

    new_membership = np.full(total_nodes, -1, dtype=int)
    per_community: list[dict] = []
    transformation_counts = {"extant": 0, "reduced": 0, "split": 0, "degraded": 0}
    substantive_transformation_counts = {"extant": 0, "reduced": 0, "split": 0, "degraded": 0}
    nodes_in_original_communities_at_least_min_size = 0
    next_community_id = 0

    for origin_community_id, vertex_ids in groups.items():
        original_size = len(vertex_ids)
        if original_size >= min_cluster_size:
            nodes_in_original_communities_at_least_min_size += original_size

        surviving = _connectivity_modifier_on_cluster(
            undirected_simple_graph, vertex_ids, resolution, min_cluster_size)
        surviving_nodes = sum(len(piece) for piece in surviving)
        classification = _classify_transformation(original_size, len(surviving), surviving_nodes)
        transformation_counts[classification] += 1
        if original_size >= SUBSTANTIVE_COMMUNITY_MIN_SIZE:
            substantive_transformation_counts[classification] += 1

        for piece in surviving:
            for vertex_index in piece:
                new_membership[vertex_index] = next_community_id
            cut_value = undirected_simple_graph.induced_subgraph(piece).mincut().value
            piece_size = len(piece)
            per_community.append({
                "resolution": resolution,
                "connectivity_modified_community_id": next_community_id,
                "origin_community_id": origin_community_id,
                "community_size": piece_size,
                "minimum_edge_cut_size": float(cut_value),
                "well_connectedness_threshold": _well_connectedness_threshold(piece_size),
                "is_well_connected": True,  # guaranteed by the keep condition
            })
            next_community_id += 1

    surviving_total = int((new_membership >= 0).sum())
    substantive_after = sum(1 for c in per_community if c["community_size"] >= SUBSTANTIVE_COMMUNITY_MIN_SIZE)
    per_partition = {
        "resolution": resolution,
        "number_of_original_communities": len(groups),
        "number_of_original_substantive_communities": sum(
            1 for v in groups.values() if len(v) >= SUBSTANTIVE_COMMUNITY_MIN_SIZE),
        "number_of_well_connected_communities_after": len(per_community),
        "number_of_substantive_communities_after": substantive_after,
        "node_coverage_before_in_communities_at_least_min_size": (
            nodes_in_original_communities_at_least_min_size / total_nodes),
        "node_coverage_after": surviving_total / total_nodes,
        "number_of_communities_extant": transformation_counts["extant"],
        "number_of_communities_reduced": transformation_counts["reduced"],
        "number_of_communities_split": transformation_counts["split"],
        "number_of_communities_degraded": transformation_counts["degraded"],
        "number_of_substantive_communities_extant": substantive_transformation_counts["extant"],
        "number_of_substantive_communities_reduced": substantive_transformation_counts["reduced"],
        "number_of_substantive_communities_split": substantive_transformation_counts["split"],
        "number_of_substantive_communities_degraded": substantive_transformation_counts["degraded"],
    }
    logger.info(
        "resolution=%s: %d original communities -> %d well-connected after CM; "
        "node coverage %.1f%% -> %.1f%%; substantive originals: %d extant, %d reduced, "
        "%d split, %d degraded",
        resolution, per_partition["number_of_original_communities"],
        per_partition["number_of_well_connected_communities_after"],
        100 * per_partition["node_coverage_before_in_communities_at_least_min_size"],
        100 * per_partition["node_coverage_after"],
        substantive_transformation_counts["extant"], substantive_transformation_counts["reduced"],
        substantive_transformation_counts["split"], substantive_transformation_counts["degraded"],
    )
    return {
        "resolution": resolution,
        "new_membership": new_membership,
        "per_community": per_community,
        "per_partition": per_partition,
    }


@parameterize(connectivity_modifier_all_resolutions={
    "bundles": group(*[source(f"connectivity_modifier_at_resolution_{r}") for r in RESOLUTIONS])
})
def connectivity_modifier_all_resolutions(bundles: list[dict]) -> list[dict]:
    return bundles


def connectivity_modifier_membership_df(
    connectivity_modifier_all_resolutions: list[dict],
    undirected_simple_graph: ig.Graph,
) -> pd.DataFrame:
    """One row per vertex: its node name and its CM community at each resolution
    (-1 = the vertex was removed by CM at that resolution)."""
    data: dict = {"node_name": list(undirected_simple_graph.vs["name"])}
    for bundle in connectivity_modifier_all_resolutions:
        column = f"connectivity_modified_community_at_res={bundle['resolution']}"
        data[column] = bundle["new_membership"]
    return pd.DataFrame(data)


def per_community_connectivity_modifier_df(
    connectivity_modifier_all_resolutions: list[dict],
) -> pd.DataFrame:
    rows = [row for bundle in connectivity_modifier_all_resolutions for row in bundle["per_community"]]
    return pd.DataFrame(rows)


def per_partition_connectivity_modifier_df(
    connectivity_modifier_all_resolutions: list[dict],
) -> pd.DataFrame:
    rows = [bundle["per_partition"] for bundle in connectivity_modifier_all_resolutions]
    return pd.DataFrame(rows)


@datasaver()
def save_connectivity_modifier_membership(connectivity_modifier_membership_df: pd.DataFrame) -> dict:
    connectivity_modifier_membership_df.to_parquet(MEMBERSHIP_PARQUET)
    return utils.get_file_metadata(MEMBERSHIP_PARQUET)


@datasaver()
def save_connectivity_modifier_per_community_metrics(per_community_connectivity_modifier_df: pd.DataFrame) -> dict:
    per_community_connectivity_modifier_df.to_parquet(PER_COMMUNITY_PARQUET)
    return utils.get_file_metadata(PER_COMMUNITY_PARQUET)


@datasaver()
def save_connectivity_modifier_per_partition_metrics(per_partition_connectivity_modifier_df: pd.DataFrame) -> dict:
    per_partition_connectivity_modifier_df.to_parquet(PER_PARTITION_PARQUET)
    return utils.get_file_metadata(PER_PARTITION_PARQUET)


if __name__ == "__main__":
    sys.exit(_main())
