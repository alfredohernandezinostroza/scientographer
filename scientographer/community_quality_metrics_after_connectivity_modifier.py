# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quality metrics recomputed on the Connectivity-Modifier-remediated partition.

community_quality_metrics.py scores the *original* Leiden/CPM partitions;
community_connectivity_modifier.py produces a *remediated* partition per
resolution (every surviving community well-connected). This module scores the
remediated partition with the **exact same** structural code
(community_quality_metrics._structural_partition_metrics), so the website can show
a before/after comparison of every whole-graph statistic (modularity, constant
Potts model score, surprise, significance, coverage, and the per-community
summaries) at each resolution.

The Connectivity Modifier drops weakly-attached papers (they fall into
sub-threshold pieces). To keep the "after" partition comparable to "before" on the
*same* graph, each removed paper is scored as its **own singleton community**: the
whole vertex set stays in play so denominators match exactly, and a singleton
contributes ~0 to modularity / CPM / surprise (just as the many singletons already
in the "before" partition do). So the before/after difference reflects the real
communities changing, not the node set changing.

Cross-seed stability and resolution-plateau are deliberately omitted: they are
properties of Leiden reseeding at a resolution and have no meaning for a CM
partition (see _structural_partition_metrics).

Outputs (data/graph_level_data/community_quality_metrics_after_connectivity_modifier/):
  community_quality_metrics_after_cm_per_community.parquet
  community_quality_metrics_after_cm_per_partition.parquet
(same column schema as the community_quality_metrics parquets, minus the
stability/plateau columns).
"""

import logging
from pathlib import Path
import sys
from typing import Final, Optional

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver, group, parameterize, source, value
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import networkx as nx
import numpy as np
import pandas as pd

from scientographer._resolution_store import (
    STORE_SUBDIR,
    ResolutionStore,
    array_fingerprint,
    library_versions,
    result_settings,
    reuse_or_compute,
    structure_fingerprint,
)
from scientographer.community_connectivity_modifier import (
    MEMBERSHIP_PARQUET as CM_MEMBERSHIP_PARQUET,
)
from scientographer.community_quality_metrics import (
    RESOLUTIONS,
    _structural_partition_metrics,
)
from scientographer.community_resolution_bands import LOW_RES_GRAPHML
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

INPUT_GRAPHML: Final[Path] = LOW_RES_GRAPHML
OUTPUT_DIR: Final[Path] = Path(params("graph")["analysis_output_dir"]) / "community_quality_metrics_after_connectivity_modifier"
# Per-resolution results kept between runs (see _resolution_store.py), inside the
# stage's output directory. Bump RESULTS_VERSION when a change alters the results.
RESULTS_VERSION: Final[int] = 1
PER_COMMUNITY_PARQUET: Final[Path] = OUTPUT_DIR / "community_quality_metrics_after_cm_per_community.parquet"
PER_PARTITION_PARQUET: Final[Path] = OUTPUT_DIR / "community_quality_metrics_after_cm_per_partition.parquet"


#####################
##  Aux Functions  ##
#####################
def _cm_community_attribute_name(resolution: float) -> str:
    return f"connectivity_modified_community_at_res={resolution}"


def _after_membership(graph: ig.Graph, cm_membership_df: pd.DataFrame, resolution: float) -> np.ndarray:
    """Build the after-CM membership aligned to the graph's vertex order.

    Reads the CM community id per vertex from the membership parquet (joined on
    node name), then replaces each removed vertex (-1) with a fresh unique
    singleton id above the largest CM id, so the whole vertex set is partitioned
    and removed papers score as their own communities."""
    column = _cm_community_attribute_name(resolution)
    name_to_cm_id = dict(zip(cm_membership_df["node_name"], cm_membership_df[column]))
    cm_ids = np.array([int(name_to_cm_id[name]) for name in graph.vs["name"]])

    membership = cm_ids.copy()
    removed = np.where(cm_ids < 0)[0]
    if removed.size:
        max_cm_id = int(cm_ids.max()) if (cm_ids >= 0).any() else -1
        membership[removed] = np.arange(max_cm_id + 1, max_cm_id + 1 + removed.size)
    return membership


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_DIR)
    inputs = dict(
        citation_network_path=INPUT_GRAPHML,
        cm_membership_path=CM_MEMBERSHIP_PARQUET,
        store_dir=OUTPUT_DIR / STORE_SUBDIR,
    )
    outputs = [
        "save_per_community_quality_after_cm",
        "save_per_partition_quality_after_cm",
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
    graph = ig.Graph.Read_GraphML(str(citation_network_path))
    metadata = utils.get_file_metadata(citation_network_path)
    return graph, metadata


def undirected_networkx_graph(citation_network: ig.Graph) -> nx.Graph:
    """Undirected projection, used only by significance (no directed definition
    exists) -- same construction as community_quality_metrics.py."""
    return citation_network.to_networkx().to_undirected()


def cm_membership_df(cm_membership_path: Path) -> pd.DataFrame:
    """The Connectivity Modifier's per-vertex membership: node_name +
    connectivity_modified_community_at_res=<r> columns (-1 = removed)."""
    return pd.read_parquet(cm_membership_path)


@parameterize(**{
    f"after_membership_at_resolution_{r}": {"resolution": value(r)} for r in RESOLUTIONS
})
def after_membership_for_resolution(
    citation_network: ig.Graph, cm_membership_df: pd.DataFrame, resolution: float
) -> Optional[np.ndarray]:
    """None when the Connectivity Modifier skipped this resolution
    (`communities.connectivity_modifier_max_community_size`)."""
    if _cm_community_attribute_name(resolution) not in cm_membership_df.columns:
        logger.info("resolution=%s: no Connectivity Modifier result, skipped", resolution)
        return None
    return _after_membership(citation_network, cm_membership_df, resolution)


def _quality_after_cm_for_resolution(
    citation_network: ig.Graph,
    undirected_networkx_graph: nx.Graph,
    resolution: float,
    after_membership: Optional[np.ndarray],
) -> Optional[dict]:
    """Structural quality metrics for one resolution's CM-remediated partition,
    computed with the same code that scores the original partition (None when
    the Connectivity Modifier skipped this resolution)."""
    if after_membership is None:
        return None
    per_community, per_partition = _structural_partition_metrics(
        citation_network, undirected_networkx_graph, after_membership, resolution)
    logger.info(
        "resolution=%s after CM: %d communities (incl. removed-as-singletons), "
        "modularity=%.4f, coverage=%.3f, surprise=%.2f, significance=%.2f",
        resolution, per_partition["number_of_communities"], per_partition["modularity"],
        per_partition["intra_community_edge_fraction"], per_partition["surprise"],
        per_partition["significance"],
    )
    return {"resolution": resolution, "per_community": per_community, "per_partition": per_partition}


@parameterize(after_memberships_by_resolution={
    "memberships": group(*[source(f"after_membership_at_resolution_{r}") for r in RESOLUTIONS])
})
def after_memberships_for_all_resolutions(memberships: list[Optional[np.ndarray]]) -> list[Optional[np.ndarray]]:
    return memberships


def _store(citation_network: ig.Graph, store_dir: Path) -> ResolutionStore:
    return ResolutionStore(
        store_dir, "community_quality_metrics_after_connectivity_modifier", RESULTS_VERSION,
        {"graph": structure_fingerprint(citation_network),
         "settings": result_settings(params("communities"), ignore=(
             "plateau_nmi_threshold", "connectivity_modifier_min_cluster_size",
             "connectivity_modifier_max_community_size")),
         "libraries": library_versions("igraph", "networkx", "cdlib", "scipy")})


def quality_after_cm_all_resolutions(
    citation_network: ig.Graph,
    undirected_networkx_graph: nx.Graph,
    after_memberships_by_resolution: list[Optional[np.ndarray]],
    store_dir: Path,
) -> list[dict]:
    """Quality of the CM-remediated partition at every resolution the Connectivity
    Modifier covered; results stored by an earlier run for the same graph and
    remediated partition are reused."""
    membership_of = {r: m for r, m in zip(RESOLUTIONS, after_memberships_by_resolution) if m is not None}
    resolutions = [r for r in RESOLUTIONS if r in membership_of]
    store = _store(citation_network, store_dir)

    def compute(missing: list[float]) -> dict[float, dict]:
        return {r: _quality_after_cm_for_resolution(citation_network, undirected_networkx_graph, r, membership_of[r])
                for r in missing}

    fingerprints = {r: store.fingerprint(r, membership=array_fingerprint(membership_of[r])) for r in resolutions}
    results = reuse_or_compute(store, resolutions, fingerprints, compute)
    return [results[r] for r in resolutions]


def per_community_quality_after_cm_df(quality_after_cm_all_resolutions: list[dict]) -> pd.DataFrame:
    rows = [row for bundle in quality_after_cm_all_resolutions for row in bundle["per_community"]]
    return pd.DataFrame(rows)


def per_partition_quality_after_cm_df(quality_after_cm_all_resolutions: list[dict]) -> pd.DataFrame:
    rows = [bundle["per_partition"] for bundle in quality_after_cm_all_resolutions]
    return pd.DataFrame(rows)


@datasaver()
def save_per_community_quality_after_cm(per_community_quality_after_cm_df: pd.DataFrame) -> dict:
    per_community_quality_after_cm_df.to_parquet(PER_COMMUNITY_PARQUET)
    return utils.get_file_metadata(PER_COMMUNITY_PARQUET)


@datasaver()
def save_per_partition_quality_after_cm(per_partition_quality_after_cm_df: pd.DataFrame) -> dict:
    per_partition_quality_after_cm_df.to_parquet(PER_PARTITION_PARQUET)
    return utils.get_file_metadata(PER_PARTITION_PARQUET)


if __name__ == "__main__":
    sys.exit(_main())
