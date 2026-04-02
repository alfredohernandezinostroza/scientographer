from fa2 import ForceAtlas2
import sys
import pickle
from hamilton_sdk import adapters
from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
from pathlib import Path
import logging
import pandas as pd
import igraph as ig
from motor_learning_network.constants import (
    PROCESSED_DATA_PATH,
    FIGURES_PATH,
    EMAIL,
    OPENCITATIONS_ACCESS_TOKEN,
    DEFAULT_UI_PROJECT_ID,
    DEFAULT_UI_USERNAME,
    TEAM_NAME,
)
import hamilton.log_setup

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)

logger = logging.getLogger(__name__)

EXECUTE = True
if EXECUTE:
    logger.info("Executing the DAG!")

CURRENT_FILE_NAME = Path(__file__).stem
UI_CONFIG = adapters.HamiltonTracker(
    project_id=DEFAULT_UI_PROJECT_ID,
    username=DEFAULT_UI_USERNAME,
    dag_name=CURRENT_FILE_NAME,
    tags={"environment": "DEV", "team": TEAM_NAME, "version": "0.1"},
)

##################
##     Main     ##
##################
def _main() -> int:
    ########################
    ## Inputs and Outputs ##
    ########################
    inputs = dict(
        unified_database_path=PROCESSED_DATA_PATH / "clean_unified_database.parquet",
        references_path=PROCESSED_DATA_PATH / "references_opencitations.parquet",
        citation_network_path=PROCESSED_DATA_PATH / "citation_network", #format will be added later
        citation_network_plot_path=FIGURES_PATH / "citation_network", # will be added later
    )
    outputs = [
        # "save_citation_network_as_pickle",
        "save_citation_network_as_graphml",
        # "plot_citation_network",
    ]

    import __main__

    dr = (
        driver.Builder()
        .with_modules(__main__)
        # .with_config()
        # .with_cache()
        .with_adapters(UI_CONFIG)
        .build()
    )

    #######################
    ##   Sanity checks   ##
    #######################
    dr.validate_execution(outputs, inputs=inputs)
    dr.display_all_functions(
        FIGURES_PATH / f"{CURRENT_FILE_NAME}_all_functions.png", keep_dot=True
    )
    dr.visualize_execution(
        outputs,
        inputs=inputs,
        output_file_path=FIGURES_PATH / f"{CURRENT_FILE_NAME}.png",
        keep_dot=False,
    )

    ###################
    ##   Execution   ##
    ###################
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#####################
##  Aux Functions  ##
#####################

def _build_edges_from_references(
    references_df: pd.DataFrame, valid_dois: set[str]
) -> list[tuple[str, str]]:
    """
    Build directed edges (citing_doi -> cited_doi) keeping only edges where
    both endpoints are in the unified database (i.e., in valid_dois).
    """
    edges = []
    for _, row in references_df.iterrows():
        citing = row.get("citing_doi")
        cited_dois = row.get("cited_dois", ())
        if not citing or citing not in valid_dois:
            continue
        if cited_dois is None:
            continue
        for cited in cited_dois:
            if cited and cited in valid_dois:
                edges.append((citing, cited))
    return edges

#########################
##    DAG Definition   ##
#########################

@dataloader()
def unified_database(unified_database_path: Path) -> tuple[pd.DataFrame, dict]:
    """Load the unified (cleaned) database of papers."""
    db = pd.read_parquet(unified_database_path)
    return db, utils.get_file_metadata(unified_database_path)


@dataloader()
def references_df(references_path: Path) -> tuple[pd.DataFrame, dict]:
    """Load the OpenCitations references dataframe.

    Expected columns: citing_doi, cited_dois (tuple/list of DOIs).
    """
    df = pd.read_parquet(references_path)
    return df, utils.get_file_metadata(references_path)


def valid_dois(unified_database: pd.DataFrame) -> set[str]:
    """Extract the set of lowercase DOIs present in the unified database."""
    dois = unified_database["doi"].dropna()
    dois = dois[dois != ""].str.lower()
    return set(dois.tolist())


def citation_edges(references_df: pd.DataFrame, valid_dois: set[str]) -> list[tuple[str, str]]:
    """Build the list of directed citation edges (citing -> cited) restricted
    to papers that exist in the unified database."""
    edges = _build_edges_from_references(references_df, valid_dois)
    logger.info(f"Built {len(edges)} citation edges from {len(valid_dois)} valid DOIs.")
    return edges


def citation_network(citation_edges: list[tuple[str, str]], valid_dois: set[str]) -> ig.Graph:
    """Build a directed igraph citation network.

    Every paper in the unified database gets a vertex (isolated papers
    included). Vertex index matches the sorted position of the DOI.
    """
    all_dois = sorted(valid_dois)
    doi_to_idx = {doi: i for i, doi in enumerate(all_dois)}

    g = ig.Graph(directed=True)
    g.add_vertices(len(all_dois))
    g.vs["name"] = all_dois  # vertex attribute: DOI string

    int_edges = [(doi_to_idx[src], doi_to_idx[dst]) for src, dst in citation_edges]
    g.add_edges(int_edges)

    logger.info(
        f"Citation network: {g.vcount()} vertices, {g.ecount()} edges."
    )

    #delete isolated nodes. Note that this is not necessary if we later retrieve only the giant component, but we get nice information of the amount of isolated papers
    g.delete_vertices(g.vs.select(_degree=0))
    logger.info(
        f"Citation network after deleting isolated nodes: {g.vcount()} vertices, {g.ecount()} edges."
    )

    #get giant component
    components = g.connected_components(mode="weak") #weak is to ignore direction, which is what we want for a citation network
    giant = components.giant()
    logger.info(
        f"Citation network after taking giant components: {giant.vcount()} vertices, {giant.ecount()} edges."
    )
    return giant

def citation_network_with_layout(citation_network: ig.Graph) -> ig.Graph:
    forceatlas2 = ForceAtlas2(verbose=True)
    layout = forceatlas2.forceatlas2_igraph_layout(citation_network.as_undirected(), iterations=500)
    citation_network.vs["x"] = [coord[0] for coord in layout]
    citation_network.vs["y"] = [coord[1] for coord in layout]
    return citation_network

@datasaver()
def plot_citation_network(citation_network_with_layout: ig.Graph, citation_network_plot_path: Path) -> dict:
    citation_network_plot_path = citation_network_plot_path.with_suffix("png")
    ig.plot(citation_network_with_layout, target=citation_network_plot_path)
    metadata = utils.get_file_metadata(citation_network_plot_path)
    return metadata

@datasaver()
def save_citation_network_as_pickle(citation_network_with_layout: ig.Graph, citation_network_path: Path) -> dict:
    """Save the igraph citation network as a pickle file."""
    path = citation_network_path.with_suffix(".pickle")
    with open(path, "wb") as f:
        pickle.dump(citation_network_with_layout, f, protocol=pickle.HIGHEST_PROTOCOL)
    metadata = utils.get_file_metadata(path)
    return metadata

@datasaver()
def save_citation_network_as_graphml(citation_network_with_layout: ig.Graph, citation_network_path: Path) -> dict:
    """Persist the igraph citation network as a pickle file."""
    path = citation_network_path.with_suffix(".graphml")
    citation_network_with_layout.write_graphml(path)
    citation_network_with_layout.write
    metadata = utils.get_file_metadata(path)
    return metadata

if __name__ == "__main__":
    sys.exit(_main())
