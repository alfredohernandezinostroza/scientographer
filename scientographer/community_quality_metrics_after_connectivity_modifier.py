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

import sys
import logging
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import igraph as ig
import networkx as nx

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
from motor_learning_network.community_resolution_bands import LOW_RES_GRAPHML
from motor_learning_network.community_quality_metrics import (
    RESOLUTIONS,
    _structural_partition_metrics,
)
from motor_learning_network.community_connectivity_modifier import (
    MEMBERSHIP_PARQUET as CM_MEMBERSHIP_PARQUET,
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
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
    inputs = dict(
        citation_network_path=INPUT_GRAPHML,
        cm_membership_path=CM_MEMBERSHIP_PARQUET,
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
) -> np.ndarray:
    return _after_membership(citation_network, cm_membership_df, resolution)


@parameterize(**{
    f"quality_after_cm_at_resolution_{r}": {
        "resolution": value(r),
        "after_membership": source(f"after_membership_at_resolution_{r}"),
    } for r in RESOLUTIONS
})
def quality_after_cm_for_resolution(
    citation_network: ig.Graph,
    undirected_networkx_graph: nx.Graph,
    resolution: float,
    after_membership: np.ndarray,
) -> dict:
    """Structural quality metrics for one resolution's CM-remediated partition,
    computed with the same code that scores the original partition."""
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


@parameterize(quality_after_cm_all_resolutions={
    "bundles": group(*[source(f"quality_after_cm_at_resolution_{r}") for r in RESOLUTIONS])
})
def quality_after_cm_all_resolutions(bundles: list[dict]) -> list[dict]:
    return bundles


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
