"""Clustering-quality metrics for the Leiden/CPM citation-network communities.

``get_network_communities_and_stats.py`` runs Leiden with the constant Potts
model (CPM) across several resolutions and writes the resulting community
assignments as per-vertex ``cpm_communities_at_res=<resolution>`` columns, but
computes no quality metrics at all -- not even the CPM objective it is
optimizing. This module reads that graph and adds, per resolution:

  - partition-level scalars: modularity, the constant Potts model score,
    surprise, significance, mean internal edge density, the intra-community
    edge fraction (coverage), community count, cross-seed stability, and
    resolution-plateau detection;
  - per-community metrics: size, conductance (+ its directed out/in
    components), internal edge density, internal/boundary edge counts.

Edge direction is a first-class property of a citation network (a paper can
only cite already-published work), so every metric uses a directed
definition where one exists. The sole exception is significance, which has
no standard directed generalization; it falls back to the undirected
projection, which is safe because a citation network is a temporal DAG and
should have ~zero reciprocal edges (checked and recorded below).

Outputs (data/graph_level_data/):
  citation_network_with_community_metrics.graphml   per-community metrics as
    node columns, partition scalars as typed graph attributes
  community_quality_metrics/community_quality_metrics_per_community.parquet
    long form: resolution x community_id x {size, conductance, ...}
  community_quality_metrics/community_quality_metrics_per_partition.parquet
    long form: resolution x {modularity, constant_potts_model_score, ...}
"""

import sys
import math
import logging
from pathlib import Path
from typing import Final
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import igraph as ig
import leidenalg
import networkx as nx
from scipy.stats import hypergeom
from scipy.special import comb
from cdlib import evaluation, NodeClustering

from hamilton.function_modifiers import dataloader, datasaver, value, source, group, parameterize
from hamilton.io import utils
from hamilton_sdk import adapters
from hamilton import driver
import hamilton.log_setup

from motor_learning_network.constants import (
    GRAPH_LEVEL_DATA_PATH,
    FIGURES_PATH,
    DEFAULT_UI_PROJECT_ID,
    DEFAULT_UI_USERNAME,
    TEAM_NAME,
)

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

# Must match the resolution sweep in get_network_communities_and_stats.py --
# these select which `cpm_communities_at_res=<r>` columns to read, they do not
# re-run Leiden at the base resolution (only the cross-seed check does).
RESOLUTIONS: Final[list[float]] = [round(i * 0.001, 3) for i in range(1, 10)]

# Extra Leiden re-runs (beyond the graph's existing seed=0 assignment) used to
# measure how much a resolution's partition changes under reseeding.
STABILITY_SEEDS: Final[tuple[int, ...]] = (1, 2, 3, 4, 5)

# Adjacent resolutions whose partitions agree (NMI) at least this much are
# flagged as sitting on the same "natural" community scale.
PLATEAU_NMI_THRESHOLD: Final[float] = 0.9

INPUT_GRAPHML: Final[Path] = GRAPH_LEVEL_DATA_PATH / "citation_network_full_low_res.graphml"
OUTPUT_GRAPHML: Final[Path] = GRAPH_LEVEL_DATA_PATH / "citation_network_with_community_metrics.graphml"
OUTPUT_DIR: Final[Path] = GRAPH_LEVEL_DATA_PATH / "community_quality_metrics"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PER_COMMUNITY_PARQUET: Final[Path] = OUTPUT_DIR / "community_quality_metrics_per_community.parquet"
PER_PARTITION_PARQUET: Final[Path] = OUTPUT_DIR / "community_quality_metrics_per_partition.parquet"


#####################
##  Aux Functions  ##
#####################
def _community_attribute_name(resolution: float) -> str:
    return f"cpm_communities_at_res={resolution}"


def _reciprocal_edge_pair_count(graph: ig.Graph) -> int:
    """Count reciprocal (A->B and B->A) edge pairs. A citation can only
    reference already-published work, so the citation graph is a temporal DAG
    and this should be ~0; any nonzero count is a data anomaly, not signal."""
    edge_set = {(e.source, e.target) for e in graph.es}
    return sum(1 for u, v in edge_set if u != v and (v, u) in edge_set) // 2


def _directed_community_edge_counts(graph: ig.Graph, membership: np.ndarray) -> dict[int, dict]:
    """Per-community directed edge/degree bookkeeping: size, internal edges,
    and boundary edges split into outward (this community citing out) and
    inward (this community being cited from outside)."""
    out_degree = graph.degree(mode="out")
    in_degree = graph.degree(mode="in")

    sizes: Counter = Counter()
    out_volume: Counter = Counter()
    in_volume: Counter = Counter()
    for v in range(graph.vcount()):
        c = int(membership[v])
        sizes[c] += 1
        out_volume[c] += out_degree[v]
        in_volume[c] += in_degree[v]

    internal: Counter = Counter()
    out_boundary: Counter = Counter()
    in_boundary: Counter = Counter()
    for e in graph.es:
        cu, cv = int(membership[e.source]), int(membership[e.target])
        if cu == cv:
            internal[cu] += 1
        else:
            out_boundary[cu] += 1
            in_boundary[cv] += 1

    return {
        c: {
            "size": sizes[c],
            "internal_directed_edge_count": internal[c],
            "out_boundary_edge_count": out_boundary[c],
            "in_boundary_edge_count": in_boundary[c],
            "boundary_edge_count": out_boundary[c] + in_boundary[c],
            "out_degree_volume": out_volume[c],
            "in_degree_volume": in_volume[c],
        }
        for c in sizes
    }


def _directed_conductance(counts: dict, total_directed_edges: int) -> dict:
    """Headline conductance (cut/min(volume, 2M-volume), the directed
    generalization using total in+out volume) plus its directed decomposition
    into outward conductance (share of the community's own citations that
    leave it) and inward conductance (share of citations into the community
    that come from outside) -- a genuinely direction-aware breakdown that
    a symmetrized/undirected conductance collapses away."""
    volume = counts["out_degree_volume"] + counts["in_degree_volume"]
    two_m = 2 * total_directed_edges
    denominator = min(volume, two_m - volume)
    conductance = counts["boundary_edge_count"] / denominator if denominator > 0 else 0.0
    conductance_out = (
        counts["out_boundary_edge_count"] / counts["out_degree_volume"]
        if counts["out_degree_volume"] > 0 else 0.0
    )
    conductance_in = (
        counts["in_boundary_edge_count"] / counts["in_degree_volume"]
        if counts["in_degree_volume"] > 0 else 0.0
    )
    return {"conductance": conductance, "conductance_out": conductance_out, "conductance_in": conductance_in}


def _directed_internal_edge_density(counts: dict) -> float:
    """Internal edges over possible ORDERED pairs n(n-1) (no /2, since a
    directed edge u->v is distinct from v->u)."""
    n = counts["size"]
    return counts["internal_directed_edge_count"] / (n * (n - 1)) if n > 1 else 0.0


def _directed_surprise(n_vertices: int, total_directed_edges: int,
                        community_sizes: list[int], total_internal_edges: int) -> float:
    """Hypergeometric-tail surprise (Aldecoa & Marin), generalized to ordered
    (directed) pairs: M = |V|(|V|-1) possible directed edges, M_intra =
    sum |c|(|c|-1). Computed in log space (scipy's logsf) to avoid overflow
    on graphs with tens of thousands of possible edges."""
    m_total = n_vertices * (n_vertices - 1)
    m_intra = sum(n * (n - 1) for n in community_sizes)
    if m_total <= 0 or m_intra <= 0:
        return 0.0
    log_p = hypergeom.logsf(total_internal_edges - 1, m_total, m_intra, total_directed_edges)
    return float(-log_p)


def _intra_community_edge_fraction(total_internal_edges: int, total_directed_edges: int) -> float:
    """Coverage: share of all (directed) edges that stay within a community."""
    return total_internal_edges / total_directed_edges if total_directed_edges > 0 else 0.0


def _constant_potts_model_score(graph: ig.Graph, membership: np.ndarray, resolution: float) -> float:
    """The exact constant-Potts-model objective leidenalg optimizes, evaluated
    on the graph's existing membership (no re-optimization)."""
    partition = leidenalg.CPMVertexPartition(
        graph, resolution_parameter=resolution, initial_membership=[int(m) for m in membership])
    return partition.quality()


def _directed_modularity(graph: ig.Graph, membership: np.ndarray) -> float:
    return graph.modularity([int(m) for m in membership], directed=True)


def _kl_divergence_term(community_density: float, baseline_density: float) -> float:
    """Binary KL divergence of community_density from baseline_density, with
    the x*log(x) -> 0 limit applied at the boundaries (density exactly 0 or
    1) so a clique or edge-free community contributes a finite term instead
    of NaN."""
    term = 0.0
    if community_density > 0:
        term += community_density * math.log(community_density / baseline_density)
    if community_density < 1:
        term += (1 - community_density) * math.log((1 - community_density) / (1 - baseline_density))
    return term


def _significance(undirected_graph: nx.Graph, membership: np.ndarray) -> float:
    """Significance (Traag, Aldecoa & Delvenne 2015): how much denser each
    community is than the graph's overall density would predict by chance,
    weighted by how many internal pairs the community could have. Undirected
    only -- no standard directed generalization exists, and a citation
    network's temporal-DAG structure makes the undirected projection
    unambiguous (reciprocal edges shouldn't exist; see
    _reciprocal_edge_pair_count).

    Hand-rolled rather than using cdlib.evaluation.significance: that
    implementation has two confirmed bugs on this repo's real network --
    (1) it computes the baseline density as edges/C(edges, 2) instead of
    edges/C(nodes, 2) (a node/edge-count mixup), and (2) it has no x*log(x)
    -> 0 boundary guard, so any fully-dense (clique) community makes the
    whole score NaN. This network has hundreds of small clique communities
    (2-3 mutually-citing papers), so cdlib's version returns NaN here.
    """
    n = undirected_graph.number_of_nodes()
    m = undirected_graph.number_of_edges()
    if n < 2 or m == 0:
        return 0.0
    baseline_density = m / comb(n, 2, exact=True)

    communities: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(membership):
        communities[int(community_id)].append(node_index)

    score = 0.0
    for nodes in communities.values():
        community_size = len(nodes)
        if community_size < 2:
            continue
        internal_edges = undirected_graph.subgraph(nodes).number_of_edges()
        possible_internal_pairs = comb(community_size, 2, exact=True)
        community_density = internal_edges / possible_internal_pairs
        score += possible_internal_pairs * _kl_divergence_term(community_density, baseline_density)
    return float(score)


def _label_clustering(membership: np.ndarray) -> NodeClustering:
    """Wrap a membership vector as a cdlib NodeClustering with no backing
    graph, for the pure label-comparison metrics (NMI, variation of
    information) that don't need edges, only the partition."""
    communities: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(membership):
        communities[int(community_id)].append(node_index)
    return NodeClustering(list(communities.values()), graph=None, method_name="partition")


def _cross_seed_stability(graph: ig.Graph, resolution: float, n_iterations: int,
                           base_membership: np.ndarray, extra_seeds: tuple[int, ...]) -> dict:
    """Re-run Leiden/CPM at this resolution with different seeds and compare
    each result to the graph's existing (seed=0) partition via normalized
    mutual information and variation of information, averaged across seeds.
    High NMI / low VI = the resolution gives a reproducible partition, not an
    artifact of one seed."""
    base_clustering = _label_clustering(base_membership)

    nmi_scores = []
    vi_scores = []
    for seed in extra_seeds:
        partition = leidenalg.find_partition(
            graph, leidenalg.CPMVertexPartition, resolution_parameter=resolution,
            seed=seed, n_iterations=n_iterations)
        seed_clustering = NodeClustering(list(partition), graph=None, method_name=f"seed_{seed}")
        nmi_scores.append(evaluation.normalized_mutual_information(base_clustering, seed_clustering).score)
        vi_scores.append(evaluation.variation_of_information(base_clustering, seed_clustering).score)

    return {
        "cross_seed_normalized_mutual_information": float(np.mean(nmi_scores)),
        "cross_seed_variation_of_information": float(np.mean(vi_scores)),
    }


def _resolution_plateau_flags(memberships: list[np.ndarray], resolutions: list[float],
                               threshold: float) -> dict[float, dict]:
    """Adjacent-resolution NMI; a resolution is 'on a plateau' if it's nearly
    identical (NMI >= threshold) to its neighbor on either side -- signalling
    a stable natural community scale rather than an arbitrary sweep point."""
    nmi_with_next: list[float] = [float("nan")] * len(resolutions)
    for i in range(len(resolutions) - 1):
        nmi_with_next[i] = float(evaluation.normalized_mutual_information(
            _label_clustering(memberships[i]), _label_clustering(memberships[i + 1])).score)

    result = {}
    for i, r in enumerate(resolutions):
        nmi_prev = nmi_with_next[i - 1] if i > 0 else float("nan")
        nmi_next = nmi_with_next[i] if i < len(resolutions) - 1 else float("nan")
        is_plateau = (not math.isnan(nmi_prev) and nmi_prev >= threshold) or \
                     (not math.isnan(nmi_next) and nmi_next >= threshold)
        result[r] = {
            "resolution_plateau_nmi_with_previous": nmi_prev,
            "resolution_plateau_nmi_with_next": nmi_next,
            "is_on_resolution_plateau": is_plateau,
        }
    return result


##################
##     Main     ##
##################
def _main() -> int:
    # Building the HamiltonTracker validates against a local UI server; only
    # construct it when the UI adapter below is actually enabled.
    # UI_CONFIG = adapters.HamiltonTracker(
    #     project_id=DEFAULT_UI_PROJECT_ID,
    #     username=DEFAULT_UI_USERNAME,
    #     dag_name=CURRENT_FILE_NAME,
    #     tags={"environment": "DEV", "team": TEAM_NAME, "version": "0.1"},
    # )
    inputs = dict(
        citation_network_path=INPUT_GRAPHML,
        n_iterations=10,
        stability_seeds=list(STABILITY_SEEDS),
    )
    outputs = [
        "save_citation_network_with_community_metrics",
        "save_per_community_quality_metrics",
        "save_per_partition_quality_metrics",
    ]
    import __main__
    dr = (
        driver.Builder()
        .with_modules(__main__)
        # .with_adapters(UI_CONFIG)
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
    graph = ig.Graph.Read_GraphML(str(citation_network_path))
    metadata = utils.get_file_metadata(citation_network_path)
    return graph, metadata


def undirected_networkx_graph(citation_network: ig.Graph) -> nx.Graph:
    """Undirected projection, used only by significance (no directed
    definition exists). Collapse vs. multi-edge-preserving projection is moot
    here: a citation network is a temporal DAG, so reciprocal edges
    shouldn't exist (see reciprocal_edge_pair_count)."""
    return citation_network.to_networkx().to_undirected()


def reciprocal_edge_pair_count(citation_network: ig.Graph) -> int:
    count = _reciprocal_edge_pair_count(citation_network)
    if count:
        logger.warning(
            "citation network has %d reciprocal edge pairs; a citation network "
            "should be a temporal DAG with none", count)
    else:
        logger.info("citation network has zero reciprocal edge pairs, as expected")
    return count


@parameterize(**{
    f"community_membership_at_resolution_{r}": {"resolution": value(r)} for r in RESOLUTIONS
})
def community_membership_for_resolution(citation_network: ig.Graph, resolution: float) -> np.ndarray:
    """Per-vertex community id at this resolution, read from the graph's
    existing `cpm_communities_at_res=<r>` attribute (stored as floats)."""
    attribute_name = _community_attribute_name(resolution)
    return np.array([int(float(v)) for v in citation_network.vs[attribute_name]])


@parameterize(community_memberships_by_resolution={
    "memberships": group(*[source(f"community_membership_at_resolution_{r}") for r in RESOLUTIONS])
})
def community_memberships_by_resolution(memberships: list[np.ndarray]) -> list[np.ndarray]:
    return memberships


def resolution_plateau_flags(community_memberships_by_resolution: list[np.ndarray]) -> dict[float, dict]:
    return _resolution_plateau_flags(community_memberships_by_resolution, RESOLUTIONS, PLATEAU_NMI_THRESHOLD)


@parameterize(**{
    f"community_quality_metrics_at_resolution_{r}": {
        "resolution": value(r),
        "community_membership": source(f"community_membership_at_resolution_{r}"),
    } for r in RESOLUTIONS
})
def community_quality_metrics_for_resolution(
    citation_network: ig.Graph,
    undirected_networkx_graph: nx.Graph,
    resolution: float,
    community_membership: np.ndarray,
    resolution_plateau_flags: dict[float, dict],
    n_iterations: int,
    stability_seeds: list[int],
) -> dict:
    """All quality metrics for one resolution's existing partition: per-
    community edge/conductance/density metrics, plus partition-level
    modularity, constant Potts model score, surprise, significance,
    coverage, cross-seed stability, and plateau status."""
    total_directed_edges = citation_network.ecount()
    n_vertices = citation_network.vcount()
    edge_counts = _directed_community_edge_counts(citation_network, community_membership)
    total_internal_edges = sum(c["internal_directed_edge_count"] for c in edge_counts.values())

    per_community = []
    for community_id, counts in edge_counts.items():
        conductances = _directed_conductance(counts, total_directed_edges)
        per_community.append({
            "resolution": resolution,
            "community_id": community_id,
            "community_size": counts["size"],
            "internal_directed_edge_count": counts["internal_directed_edge_count"],
            "boundary_edge_count": counts["boundary_edge_count"],
            "out_boundary_edge_count": counts["out_boundary_edge_count"],
            "in_boundary_edge_count": counts["in_boundary_edge_count"],
            "conductance": conductances["conductance"],
            "conductance_out": conductances["conductance_out"],
            "conductance_in": conductances["conductance_in"],
            "internal_edge_density": _directed_internal_edge_density(counts),
        })

    stability = _cross_seed_stability(
        citation_network, resolution, n_iterations, community_membership, tuple(stability_seeds))
    plateau = resolution_plateau_flags[resolution]

    per_partition = {
        "resolution": resolution,
        "number_of_communities": len(edge_counts),
        "modularity": _directed_modularity(citation_network, community_membership),
        "constant_potts_model_score": _constant_potts_model_score(
            citation_network, community_membership, resolution),
        "surprise": _directed_surprise(
            n_vertices, total_directed_edges,
            [c["size"] for c in edge_counts.values()], total_internal_edges),
        "significance": _significance(undirected_networkx_graph, community_membership),
        "mean_internal_edge_density": float(np.mean(
            [m["internal_edge_density"] for m in per_community])) if per_community else 0.0,
        "intra_community_edge_fraction": _intra_community_edge_fraction(
            total_internal_edges, total_directed_edges),
        **stability,
        **plateau,
    }
    logger.info(
        "resolution=%s: %d communities, modularity=%.4f, constant_potts_model_score=%.2f, "
        "surprise=%.2f, significance=%.2f",
        resolution, len(edge_counts), per_partition["modularity"],
        per_partition["constant_potts_model_score"], per_partition["surprise"],
        per_partition["significance"],
    )
    return {"resolution": resolution, "per_community": per_community, "per_partition": per_partition}


@parameterize(community_quality_metrics_all_resolutions={
    "bundles": group(*[source(f"community_quality_metrics_at_resolution_{r}") for r in RESOLUTIONS])
})
def community_quality_metrics_all_resolutions(bundles: list[dict]) -> list[dict]:
    return bundles


def per_community_quality_metrics_df(community_quality_metrics_all_resolutions: list[dict]) -> pd.DataFrame:
    rows = [row for bundle in community_quality_metrics_all_resolutions for row in bundle["per_community"]]
    return pd.DataFrame(rows)


def per_partition_quality_metrics_df(community_quality_metrics_all_resolutions: list[dict]) -> pd.DataFrame:
    rows = [bundle["per_partition"] for bundle in community_quality_metrics_all_resolutions]
    return pd.DataFrame(rows)


def citation_network_with_community_metrics(
    citation_network: ig.Graph,
    community_quality_metrics_all_resolutions: list[dict],
    community_memberships_by_resolution: list[np.ndarray],
    reciprocal_edge_pair_count: int,
) -> ig.Graph:
    """Copy of the input graph with per-community metrics broadcast onto
    node columns and partition-level scalars stored as typed graph
    attributes, one set per resolution."""
    graph = citation_network.copy()
    graph["metrics_edge_directedness"] = "directed"
    graph["significance_edge_directedness"] = "undirected_no_standard_directed_definition"
    graph["reciprocal_edge_pair_count"] = int(reciprocal_edge_pair_count)

    node_metric_names = [
        "community_size", "conductance", "conductance_out", "conductance_in",
        "internal_edge_density", "internal_directed_edge_count", "boundary_edge_count",
    ]

    for bundle, membership in zip(community_quality_metrics_all_resolutions, community_memberships_by_resolution):
        resolution = bundle["resolution"]
        suffix = f"_at_res={resolution}"

        for key, metric_value in bundle["per_partition"].items():
            if key == "resolution":
                continue
            if isinstance(metric_value, float) and math.isnan(metric_value):
                continue  # e.g. plateau-neighbor NMI at the first/last resolution
            if isinstance(metric_value, bool):
                metric_value = int(metric_value)
            # igraph's GraphML writer always serializes graph-level (not
            # vertex-level) numeric attributes as attr.type="double", even
            # for a plain Python int -- so is_on_resolution_plateau and
            # number_of_communities round-trip as 0.0/1.0 and 1949.0 rather
            # than int. Verified exact (no precision loss); not worth
            # fighting an igraph limitation for these magnitudes.
            graph[f"{key}{suffix}"] = metric_value

        lookup = {row["community_id"]: row for row in bundle["per_community"]}
        for metric_name in node_metric_names:
            graph.vs[f"{metric_name}{suffix}"] = [lookup[int(c)][metric_name] for c in membership]

    return graph


@datasaver()
def save_citation_network_with_community_metrics(citation_network_with_community_metrics: ig.Graph) -> dict:
    citation_network_with_community_metrics.write(OUTPUT_GRAPHML)
    return utils.get_file_metadata(OUTPUT_GRAPHML)


@datasaver()
def save_per_community_quality_metrics(per_community_quality_metrics_df: pd.DataFrame) -> dict:
    per_community_quality_metrics_df.to_parquet(PER_COMMUNITY_PARQUET)
    return utils.get_file_metadata(PER_COMMUNITY_PARQUET)


@datasaver()
def save_per_partition_quality_metrics(per_partition_quality_metrics_df: pd.DataFrame) -> dict:
    per_partition_quality_metrics_df.to_parquet(PER_PARTITION_PARQUET)
    return utils.get_file_metadata(PER_PARTITION_PARQUET)


if __name__ == "__main__":
    sys.exit(_main())
