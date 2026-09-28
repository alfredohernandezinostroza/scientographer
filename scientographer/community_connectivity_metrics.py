"""Well-connectedness (minimum-edge-cut) diagnostic for the Leiden/CPM communities.

``detect_communities`` runs Leiden with the constant Potts model (CPM) across
several resolutions and writes the resulting community assignments as
per-vertex ``cpm_communities_at_res=<resolution>`` columns.
``community_quality_metrics.py`` scores those communities on *density* (internal
edge density, internal edge surprise) and *boundary tightness* (conductance).

This module adds an orthogonal structural test those metrics miss:
**well-connectedness**. Following Park, Tabatabaee, ... Warnow, "Identifying
Well-Connected Communities in Real-World and Synthetic Networks" (COMPLEX
NETWORKS 2023), a community of ``n`` nodes is *poorly connected* if its global
minimum edge cut is ``<= f(n)`` with ``f(n) = log10(n)`` -- i.e. deleting a
handful of edges splits it in two. A community can be dense (high surprise) yet
still be poorly connected: two dense blobs joined by a single citation have a
minimum cut of 1. The paper's central empirical finding is that Leiden-CPM at
*small* resolutions -- the regime citation networks usually need -- produces
the fewest well-connected communities, so this is where the test bites.

This is a diagnostic only: it measures well-connectedness, it does not re-cluster
(no Connectivity Modifier remediation). Our network is tiny (~23k nodes), so the
global minimum cut of every community at every resolution is cheap via igraph's
native ``Graph.mincut`` (Stoer-Wagner); no external tool is needed.

Directedness: minimum edge cut / well-connectedness is defined on an undirected
simple graph. Each community's induced subgraph is projected to undirected and
simplified before cutting -- matching the paper, which strips self-loops and
parallel edges. In this network that collapse also merges the handful of
reciprocal citation pairs (a temporal-DAG anomaly) into single undirected edges.

Outputs (data/graph_level_data/community_connectivity_metrics/):
  community_connectivity_metrics_per_community.parquet
    long form: resolution x community_id x {size, minimum_edge_cut_size,
    well_connectedness_threshold, is_well_connected, ...}
  community_connectivity_metrics_per_partition.parquet
    long form: resolution x {number_of_communities, fraction_well_connected_*, ...}
"""

from collections import defaultdict
import logging
import math
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver, group, parameterize, source, value
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import numpy as np
import pandas as pd

from scientographer.community_resolution_bands import (
    CANONICAL_RESOLUTION,
    LOW_RES_GRAPHML,
    RESOLUTIONS,
    merge_higher_band_communities,
)
from scientographer.config import (
    FIGURES_PATH,
    draw_dag,
    ensure_dirs,
    params,
    tracker_adapters,
)

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

# RESOLUTIONS (the full three-band sweep 0.001-0.9) is imported from
# community_resolution_bands so every analysis DAG sweeps the identical set.

# A community this size or larger is treated as "substantive" when summarizing
# per-community metrics. Matches community_quality_metrics.py and the cutoff the
# website uses to decide which communities are worth naming, so the well-
# connected fractions reported here describe the communities a reader actually
# sees on the map, not the singleton artifact mass.
SUBSTANTIVE_COMMUNITY_MIN_SIZE: Final[int] = int(params("communities")["substantive_min_size"])

INPUT_GRAPHML: Final[Path] = LOW_RES_GRAPHML
OUTPUT_DIR: Final[Path] = Path(params("graph")["analysis_output_dir"]) / "community_connectivity_metrics"
PER_COMMUNITY_PARQUET: Final[Path] = OUTPUT_DIR / "community_connectivity_metrics_per_community.parquet"
PER_PARTITION_PARQUET: Final[Path] = OUTPUT_DIR / "community_connectivity_metrics_per_partition.parquet"
# DVC-facing summaries (see community_quality_metrics.py): scalars at the canonical
# resolution for `dvc metrics` / `dvc exp show`, the per-partition table for `dvc plots`.
DVC_METRICS_JSON: Final[Path] = OUTPUT_DIR.parent / "metrics" / "community_connectivity_metrics.json"
DVC_PLOTS_CSV: Final[Path] = OUTPUT_DIR.parent / "plots" / "community_connectivity_metrics_per_partition.csv"
DVC_METRIC_FIELDS: Final[tuple[str, ...]] = (
    "number_of_substantive_communities",
    "number_of_well_connected_communities",
    "fraction_well_connected_over_substantive_communities",
    "node_weighted_fraction_well_connected",
    "median_minimum_edge_cut_size_over_substantive_communities",
)


#####################
##  Aux Functions  ##
#####################
def _community_attribute_name(resolution: float) -> str:
    return f"cpm_communities_at_res={resolution}"


def _well_connectedness_threshold(community_size: int) -> float:
    """f(n) = log10(n), the paper's mild well-connectedness bar: a community is
    well-connected only if its minimum edge cut *exceeds* this. Undefined for a
    community with fewer than two nodes (no cut exists), reported as NaN."""
    if community_size < 2:
        return float("nan")
    return math.log10(community_size)


def _community_vertex_groups(membership: np.ndarray) -> dict[int, list[int]]:
    """Map each community id to the list of vertex indices assigned to it."""
    groups: dict[int, list[int]] = defaultdict(list)
    for vertex_index, community_id in enumerate(membership):
        groups[int(community_id)].append(vertex_index)
    return groups


def _minimum_edge_cut_of_community(graph: ig.Graph, vertex_ids: list[int]) -> dict:
    """Global minimum edge cut of one community's induced subgraph.

    The subgraph is projected to undirected and simplified first, so the cut is
    computed on the simple undirected structure the paper's f(n) is defined for:
    ``to_undirected(mode="collapse")`` merges each reciprocal citation pair
    (A->B and B->A) and any parallel edge into a single undirected edge, and
    ``simplify`` then drops self-citation loops. Both are rare data anomalies in
    this temporal-DAG citation network, not real connectivity.

    Returns the community size, its internal undirected edge count, the minimum
    edge cut size (``igraph.Graph.mincut`` -- unweighted global min cut,
    Stoer-Wagner), and the cut's balance (smaller-side node fraction: ~1/n for a
    single-node cut, ~0.5 for an even split). A community with fewer than two
    nodes has no cut, reported as NaN. A disconnected community has a minimum cut
    of 0 (correctly poorly connected)."""
    subgraph = graph.induced_subgraph(vertex_ids)
    subgraph.to_undirected(mode="collapse")
    subgraph.simplify(multiple=True, loops=True)

    community_size = subgraph.vcount()
    internal_undirected_edge_count = subgraph.ecount()
    if community_size < 2:
        return {
            "community_size": community_size,
            "internal_undirected_edge_count": internal_undirected_edge_count,
            "minimum_edge_cut_size": float("nan"),
            "minimum_cut_balance": float("nan"),
        }

    cut = subgraph.mincut()
    smaller_side = min(len(side) for side in cut.partition)
    return {
        "community_size": community_size,
        "internal_undirected_edge_count": internal_undirected_edge_count,
        "minimum_edge_cut_size": float(cut.value),
        "minimum_cut_balance": smaller_side / community_size,
    }


def _is_well_connected(minimum_edge_cut_size: float, well_connectedness_threshold: float) -> bool:
    """A community is well-connected iff its minimum edge cut strictly exceeds
    f(n). Communities with no defined cut (fewer than two nodes) are not
    well-connected."""
    if math.isnan(minimum_edge_cut_size) or math.isnan(well_connectedness_threshold):
        return False
    return minimum_edge_cut_size > well_connectedness_threshold


def _summarize_connectivity_metrics(per_community: list[dict]) -> dict:
    """Summarize per-community well-connectedness up to partition level.

    Each statistic names its population or weighting, for the same reason as
    community_quality_metrics._summarize_community_metrics: most communities at
    every resolution are singletons, which can never be well-connected (no cut
    exists), so a bare "fraction well-connected over all communities" would just
    report the singleton share. The reported fractions therefore range over
    communities with at least two nodes, over substantive communities (size
    >= SUBSTANTIVE_COMMUNITY_MIN_SIZE), or weight each community by its size
    (node-weighted = the share of *papers* that sit in a well-connected
    community, so thousands of singletons can't outvote the corpus)."""
    total_nodes = sum(c["community_size"] for c in per_community) or 1

    non_trivial = [c for c in per_community if c["community_size"] >= 2]
    substantive = [c for c in per_community if c["community_size"] >= SUBSTANTIVE_COMMUNITY_MIN_SIZE]

    def fraction_well_connected(population: list[dict]) -> float:
        if not population:
            return float("nan")
        return sum(1 for c in population if c["is_well_connected"]) / len(population)

    substantive_cut_sizes = [c["minimum_edge_cut_size"] for c in substantive]

    return {
        "number_of_communities": len(per_community),
        "number_of_communities_with_at_least_2_nodes": len(non_trivial),
        "number_of_substantive_communities": len(substantive),
        # Only communities with >= 2 nodes can be well-connected, so counting
        # over that population equals counting over all communities.
        "number_of_well_connected_communities": sum(1 for c in per_community if c["is_well_connected"]),
        "fraction_well_connected_over_communities_with_at_least_2_nodes": fraction_well_connected(non_trivial),
        "fraction_well_connected_over_substantive_communities": fraction_well_connected(substantive),
        "node_weighted_fraction_well_connected": (
            sum(c["community_size"] for c in per_community if c["is_well_connected"]) / total_nodes),
        "median_minimum_edge_cut_size_over_substantive_communities": (
            float(np.median(substantive_cut_sizes)) if substantive_cut_sizes else float("nan")),
    }


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_DIR)
    inputs = dict(
        citation_network_path=INPUT_GRAPHML,
    )
    outputs = [
        "save_per_community_connectivity_metrics",
        "save_per_partition_connectivity_metrics",
        "save_dvc_metrics_and_plots",
    ]
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
def citation_network(citation_network_path: Path) -> tuple[ig.Graph, dict]:
    # citation_network_path is the low-res base; merge_higher_band_communities grafts
    # the mid/high `cpm_communities_at_res=*` columns on so this one graph carries all
    # of RESOLUTIONS (the three bands share identical topology).
    graph = ig.Graph.Read_GraphML(str(citation_network_path))
    merge_higher_band_communities(graph)
    metadata = utils.get_file_metadata(citation_network_path)
    return graph, metadata


@parameterize(**{
    f"community_membership_at_resolution_{r}": {"resolution": value(r)} for r in RESOLUTIONS
})
def community_membership_for_resolution(citation_network: ig.Graph, resolution: float) -> np.ndarray:
    """Per-vertex community id at this resolution, read from the graph's
    existing `cpm_communities_at_res=<r>` attribute (stored as floats)."""
    attribute_name = _community_attribute_name(resolution)
    return np.array([int(float(v)) for v in citation_network.vs[attribute_name]])


@parameterize(**{
    f"community_connectivity_metrics_at_resolution_{r}": {
        "resolution": value(r),
        "community_membership": source(f"community_membership_at_resolution_{r}"),
    } for r in RESOLUTIONS
})
def community_connectivity_metrics_for_resolution(
    citation_network: ig.Graph,
    resolution: float,
    community_membership: np.ndarray,
) -> dict:
    """Well-connectedness metrics for one resolution's existing partition: the
    minimum edge cut of every community versus f(n) = log10(n), plus the
    partition-level fractions of communities that are well-connected."""
    groups = _community_vertex_groups(community_membership)

    per_community = []
    for community_id, vertex_ids in groups.items():
        cut = _minimum_edge_cut_of_community(citation_network, vertex_ids)
        threshold = _well_connectedness_threshold(cut["community_size"])
        per_community.append({
            "resolution": resolution,
            "community_id": community_id,
            "community_size": cut["community_size"],
            "internal_undirected_edge_count": cut["internal_undirected_edge_count"],
            "minimum_edge_cut_size": cut["minimum_edge_cut_size"],
            "well_connectedness_threshold": threshold,
            "is_well_connected": _is_well_connected(cut["minimum_edge_cut_size"], threshold),
            "minimum_cut_balance": cut["minimum_cut_balance"],
        })

    per_partition = {
        "resolution": resolution,
        **_summarize_connectivity_metrics(per_community),
    }
    logger.info(
        "resolution=%s: %d communities, %d/%d substantive are well-connected "
        "(%.1f%%), node-weighted %.1f%% of papers sit in a well-connected community",
        resolution, per_partition["number_of_communities"],
        sum(1 for c in per_community
            if c["community_size"] >= SUBSTANTIVE_COMMUNITY_MIN_SIZE and c["is_well_connected"]),
        per_partition["number_of_substantive_communities"],
        100 * per_partition["fraction_well_connected_over_substantive_communities"],
        100 * per_partition["node_weighted_fraction_well_connected"],
    )
    return {"resolution": resolution, "per_community": per_community, "per_partition": per_partition}


@parameterize(community_connectivity_metrics_all_resolutions={
    "bundles": group(*[source(f"community_connectivity_metrics_at_resolution_{r}") for r in RESOLUTIONS])
})
def community_connectivity_metrics_all_resolutions(bundles: list[dict]) -> list[dict]:
    return bundles


def per_community_connectivity_metrics_df(
    community_connectivity_metrics_all_resolutions: list[dict],
) -> pd.DataFrame:
    rows = [row for bundle in community_connectivity_metrics_all_resolutions for row in bundle["per_community"]]
    return pd.DataFrame(rows)


def per_partition_connectivity_metrics_df(
    community_connectivity_metrics_all_resolutions: list[dict],
) -> pd.DataFrame:
    rows = [bundle["per_partition"] for bundle in community_connectivity_metrics_all_resolutions]
    return pd.DataFrame(rows)


@datasaver()
def save_per_community_connectivity_metrics(per_community_connectivity_metrics_df: pd.DataFrame) -> dict:
    per_community_connectivity_metrics_df.to_parquet(PER_COMMUNITY_PARQUET)
    return utils.get_file_metadata(PER_COMMUNITY_PARQUET)


@datasaver()
def save_per_partition_connectivity_metrics(per_partition_connectivity_metrics_df: pd.DataFrame) -> dict:
    per_partition_connectivity_metrics_df.to_parquet(PER_PARTITION_PARQUET)
    return utils.get_file_metadata(PER_PARTITION_PARQUET)


@datasaver()
def save_dvc_metrics_and_plots(per_partition_connectivity_metrics_df: pd.DataFrame) -> dict:
    """See DVC_METRICS_JSON / DVC_PLOTS_CSV: what `dvc metrics` and `dvc plots` read."""
    import json

    from scientographer.community_quality_metrics import _dvc_metrics_at_canonical_resolution

    DVC_METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    DVC_PLOTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    summary = _dvc_metrics_at_canonical_resolution(
        per_partition_connectivity_metrics_df, CANONICAL_RESOLUTION, DVC_METRIC_FIELDS)
    with open(DVC_METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    per_partition_connectivity_metrics_df.sort_values("resolution").to_csv(DVC_PLOTS_CSV, index=False)
    return {"metrics": str(DVC_METRICS_JSON), "plots": str(DVC_PLOTS_CSV), **summary}


if __name__ == "__main__":
    sys.exit(_main())
