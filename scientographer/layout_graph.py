"""Give a citation graph x/y coordinates -- the stage that used to be a manual
Gephi step.

Two modes (params.yaml ``layout.mode``):

- ``forceatlas2``: run ForceAtlas2 (the ``fa2`` package, the same call
  ``build_citation_network.py`` and the keyword DAGs make) on the undirected
  graph, in-pipeline, ``forceatlas2_iterations`` steps.
- ``import``: copy x/y from a file exported by Gephi -- a graphml (Gephi writes
  ``x``/``y`` vertex attributes) or a node CSV with ``id``/``name`` + ``x``/``y``
  columns -- matched by the vertex ``name`` (the DOI). This is how the study's
  ``*_with_layout.graphml`` files were made; the stage makes that hand-off a
  declared dependency instead of an undocumented rename.

An input that already carries a complete layout is passed through unchanged
unless ``overwrite_existing`` is true, so re-running the pipeline never
silently re-lays-out a graph someone has tuned by hand. The mode and iteration
count are recorded as graph-level attributes (``layout_mode``,
``layout_iterations``).
"""

import logging
from pathlib import Path
import sys
from typing import Final, Optional

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import pandas as pd

from motor_learning_network.constants import FIGURES_PATH, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = params("layout")
INPUT_GRAPHML: Final[Path] = Path(_cfg["input_graphml"])
OUTPUT_GRAPHML: Final[Path] = Path(_cfg["output_graphml"])
MODE: Final[str] = str(_cfg.get("mode", "forceatlas2"))
FORCEATLAS2_ITERATIONS: Final[int] = int(_cfg.get("forceatlas2_iterations", 500))
IMPORT_PATH: Final[Optional[Path]] = Path(_cfg["import_path"]) if _cfg.get("import_path") else None
OVERWRITE_EXISTING: Final[bool] = bool(_cfg.get("overwrite_existing", False))

MODES: Final[tuple[str, ...]] = ("forceatlas2", "import")


#####################
##  Aux Functions  ##
#####################
def _has_complete_layout(graph: ig.Graph) -> bool:
    names = graph.vs.attributes()
    if "x" not in names or "y" not in names:
        return False
    return all(v is not None for v in graph.vs["x"]) and all(v is not None for v in graph.vs["y"])


def _forceatlas2_positions(graph: ig.Graph, iterations: int) -> tuple[list[float], list[float]]:
    from fa2 import ForceAtlas2  # optional dependency (`[layout]` extra)

    layout = ForceAtlas2(verbose=False).forceatlas2_igraph_layout(
        graph.as_undirected(), iterations=iterations
    )
    coords = list(layout)
    return [float(c[0]) for c in coords], [float(c[1]) for c in coords]


def _positions_from_file(import_path: Path) -> dict[str, tuple[float, float]]:
    """{vertex name: (x, y)} from a Gephi graphml export or a node CSV."""
    import_path = Path(import_path)
    if import_path.suffix.lower() == ".graphml":
        other = ig.Graph.Read_GraphML(str(import_path))
        if not _has_complete_layout(other):
            raise ValueError(f"{import_path} has no complete x/y layout to import")
        return {
            name: (float(x), float(y))
            for name, x, y in zip(other.vs["name"], other.vs["x"], other.vs["y"])
        }
    table = pd.read_csv(import_path)
    columns = {c.lower(): c for c in table.columns}
    key = columns.get("name") or columns.get("id")
    if key is None or "x" not in columns or "y" not in columns:
        raise ValueError(
            f"{import_path} needs id/name, x and y columns; has {list(table.columns)}"
        )
    return {
        str(row[key]): (float(row[columns["x"]]), float(row[columns["y"]]))
        for _, row in table.iterrows()
    }


def _imported_positions(graph: ig.Graph, import_path: Path) -> tuple[list[float], list[float]]:
    positions = _positions_from_file(import_path)
    missing = [name for name in graph.vs["name"] if name not in positions]
    if missing:
        raise ValueError(
            f"{len(missing)} of {graph.vcount()} vertices have no position in {import_path} "
            f"(first: {missing[:3]}); the layout must cover the whole graph"
        )
    xs = [positions[name][0] for name in graph.vs["name"]]
    ys = [positions[name][1] for name in graph.vs["name"]]
    return xs, ys


def _with_positions(
    graph: ig.Graph, xs: list[float], ys: list[float], mode: str, iterations: Optional[int]
) -> ig.Graph:
    graph.vs["x"] = xs
    graph.vs["y"] = ys
    graph["layout_mode"] = mode
    graph["layout_iterations"] = int(iterations) if iterations is not None else -1
    return graph


##################
##     Main     ##
##################
def _main() -> int:
    if MODE not in MODES:
        raise ValueError(f"layout.mode must be one of {MODES}, got {MODE!r}")
    inputs = dict(
        input_graphml_path=INPUT_GRAPHML,
        mode=MODE,
        forceatlas2_iterations=FORCEATLAS2_ITERATIONS,
        import_path=IMPORT_PATH,
        overwrite_existing=OVERWRITE_EXISTING,
        output_graphml_path=OUTPUT_GRAPHML,
    )
    outputs = ["save_citation_network_with_layout"]

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


def citation_network_with_layout(
    citation_network: ig.Graph,
    mode: str,
    forceatlas2_iterations: int,
    import_path: Optional[Path],
    overwrite_existing: bool,
) -> ig.Graph:
    if _has_complete_layout(citation_network) and not overwrite_existing:
        logger.info(
            "graph already has a complete x/y layout; passing it through (layout.overwrite_existing is false)"
        )
        return citation_network
    if mode == "forceatlas2":
        logger.info(
            "ForceAtlas2, %d iterations, %d vertices",
            forceatlas2_iterations,
            citation_network.vcount(),
        )
        xs, ys = _forceatlas2_positions(citation_network, forceatlas2_iterations)
        return _with_positions(citation_network, xs, ys, mode, forceatlas2_iterations)
    if mode == "import":
        if import_path is None:
            raise ValueError("layout.mode is 'import' but layout.import_path is not set")
        logger.info("importing x/y from %s", import_path)
        xs, ys = _imported_positions(citation_network, import_path)
        return _with_positions(citation_network, xs, ys, mode, None)
    raise ValueError(f"unknown layout mode {mode!r}; use one of {MODES}")


@datasaver()
def save_citation_network_with_layout(
    citation_network_with_layout: ig.Graph, output_graphml_path: Path
) -> dict:
    Path(output_graphml_path).parent.mkdir(parents=True, exist_ok=True)
    citation_network_with_layout.write_graphml(str(output_graphml_path))
    return utils.get_file_metadata(output_graphml_path)


if __name__ == "__main__":
    sys.exit(_main())
