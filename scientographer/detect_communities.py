"""Leiden/CPM community detection across the whole resolution sweep, written onto
one graph.

This is the package's (Track-B) counterpart of the frozen Track-A script
``get_network_communities_and_stats.py``, which produced the study's stored
partitions in three band files. Same algorithm and settings (Leiden with the
constant Potts model, ``leidenalg.CPMVertexPartition``, unweighted, one seed,
``n_iterations`` refinement passes), but: every resolution of
``community_resolution_bands.RESOLUTIONS`` lands on ONE output graphml as
``cpm_communities_at_res=<r>`` vertex columns, the optional pre-detection degree
filter is a parameter (default: none -- detect on the full giant component and
filter *communities* afterwards, which commutes; see docs/RESOLUTION_SELECTION.md),
and the run settings are recorded as graph-level attributes (``leiden_seed``,
``leiden_iterations``, ``leiden_quality_function``) so the graph itself says how
its communities were made.

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
from hamilton.function_modifiers import dataloader, datasaver, group, parameterize, source, value
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import leidenalg
import pandas as pd

from motor_learning_network.community_resolution_bands import RESOLUTIONS, community_attribute_name
from motor_learning_network.constants import FIGURES_PATH, params, tracker_adapters

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

_res_node_names: Final[list[str]] = [f"res_{str(r).replace('.', '_')}" for r in RESOLUTIONS]


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
    inputs = dict(
        input_graphml_path=INPUT_GRAPHML,
        min_degree=MIN_DEGREE,
        seed=SEED,
        n_iterations=ITERATIONS,
        output_graphml_path=OUTPUT_GRAPHML,
        summary_parquet_path=SUMMARY_PARQUET,
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
    dr.display_all_functions(
        FIGURES_PATH / f"{CURRENT_FILE_NAME}_all_functions.png",
        keep_dot=True,
        deduplicate_inputs=True,
    )
    dr.visualize_execution(
        outputs,
        inputs=inputs,
        output_file_path=FIGURES_PATH / f"{CURRENT_FILE_NAME}.png",
        keep_dot=False,
        deduplicate_inputs=True,
    )
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


@parameterize(
    **{
        f"membership_{name}": {"resolution": value(r)}
        for name, r in zip(_res_node_names, RESOLUTIONS)
    }
)
def membership(
    filtered_citation_network: ig.Graph, resolution: float, seed: int, n_iterations: int
) -> list[int]:
    result = _leiden_cpm_membership(filtered_citation_network, resolution, seed, n_iterations)
    logger.info("[res=%s] %d communities", resolution, len(set(result)))
    return result


@parameterize(
    memberships_by_resolution={
        "results": group(*[source(f"membership_{name}") for name in _res_node_names])
    }
)
def memberships_by_resolution(results: list[list[int]]) -> dict[float, list[int]]:
    return dict(zip(RESOLUTIONS, results))


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
