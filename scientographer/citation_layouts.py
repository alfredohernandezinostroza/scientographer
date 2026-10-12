# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""More layouts of the citation graph, for the site's View menu (params.yaml
``citation_layouts``).

Every entry of ``citation_layouts.layouts`` is one ForceAtlas2 run on the
citation graph (read undirected):

- ``lin_log``: false = standard ForceAtlas2 (the attraction along a citation
  grows with the distance, as in the main map); true = LinLog (it grows with
  log(1 + distance)), which lets communities drift apart into separate islands.
  On the motor-learning corpus (22,982 papers, γ = 0.0015), the share of each
  paper's 15 nearest papers in its own community goes from 30% (standard, 2D)
  to 50% (LinLog, 2D) and 56% (LinLog, 3D); the share of its citation
  neighbours among its nearest papers from 4.5% to 28% and 31%.
- ``dimensions``: 2, or 3 for a 3D view (the site opens it in its 3D renderer).
- ``iterations`` (default 2000) and ``seed`` (default 1).

The run uses _forceatlas2.py: exact and multi-core when numba is installed
(``pip install "scientographer[fast-layout]"``), otherwise the fa2 package
(``engine`` forces one). Each layout is then turned to a default orientation like
the main map (layout_graph.py ``principal``): its main body's long axis along x,
long tail down, most-cited side left; in 3D the remaining axis is the body's
third principal axis. Coordinates are centred on the main body.

Only positions are written; the graph and its communities are not touched.

Outputs (``citation_layouts.output_dir``), in the format
``website.extra_layouts`` reads:
  <key>.csv                    doi, x, y (and z in 3D)
  citation_layouts.json        what was run for each key (engine, seconds, ...)
"""

import json
import logging
from pathlib import Path
import sys
import time
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import dataloader
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import numpy as np
import pandas as pd
from scipy.stats import skew

from scientographer._forceatlas2 import ENGINES, forceatlas2
from scientographer.config import FIGURES_PATH, PARAMS, draw_dag, ensure_dirs, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = PARAMS.get("citation_layouts", {})
INPUT_GRAPHML: Final[Path] = Path(_cfg.get("input_graphml", "data/citation_network_with_communities.graphml"))
OUTPUT_DIR: Final[Path] = Path(_cfg.get("output_dir", "data/citation_layouts"))
LAYOUTS: Final[list[dict]] = list(_cfg.get("layouts") or [])
MANIFEST_NAME: Final[str] = "citation_layouts.json"

DEFAULTS: Final[dict] = {"lin_log": False, "dimensions": 2, "iterations": 2000, "seed": 1, "engine": "auto"}
# The main body used to orient a layout: the papers closest to its median position
# (as layout_graph.py's ORIENTATION_CORE_SHARE).
ORIENTATION_CORE_SHARE: Final[float] = 0.9


#####################
##  Aux Functions  ##
#####################
def _spec(entry: dict) -> dict:
    spec = {**DEFAULTS, **entry}
    key = str(spec.get("key") or "")
    if not key or not key.replace("_", "").isalnum() or key != key.lower():
        raise ValueError(f"citation_layouts: each layout needs a `key` of lower-case letters, digits and _ (got {key!r})")
    if int(spec["dimensions"]) not in (2, 3):
        raise ValueError(f"citation_layouts {key}: dimensions must be 2 or 3")
    if spec["engine"] not in ENGINES:
        raise ValueError(f"citation_layouts {key}: engine must be one of {ENGINES}")
    return {**spec, "key": key, "dimensions": int(spec["dimensions"]), "iterations": int(spec["iterations"]),
            "seed": int(spec["seed"]), "lin_log": bool(spec["lin_log"])}


def oriented(positions: np.ndarray, citations: np.ndarray) -> np.ndarray:
    """Centre a layout on its main body and turn it to its principal axes (longest
    along x), with the long tail down (y) and the most-cited side left (x); in 3D
    the z sign puts the bulk of the body in front (negative skewness)."""
    distance = np.linalg.norm(positions - np.median(positions, axis=0), axis=1)
    core = distance <= np.quantile(distance, ORIENTATION_CORE_SHARE)
    centre = positions[core].mean(axis=0)
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov((positions[core] - centre).T))
    turned = (positions - centre) @ eigenvectors[:, np.argsort(eigenvalues)[::-1]]
    signs = np.ones(positions.shape[1])
    weights = np.asarray(citations, dtype=float)[core]
    if weights.sum() > 0 and np.average(turned[core, 0], weights=weights) > turned[core, 0].mean():
        signs[0] = -1.0
    for axis in range(1, positions.shape[1]):
        if skew(turned[core, axis]) > 0:
            signs[axis] = -1.0
    return turned * signs


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH)
    inputs = {"input_graphml_path": INPUT_GRAPHML, "layouts": LAYOUTS, "output_dir": OUTPUT_DIR}
    outputs = ["save_citation_layouts"]

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
    logger.info("read %s: %d vertices, %d edges", input_graphml_path, graph.vcount(), graph.ecount())
    return graph, utils.get_file_metadata(input_graphml_path)


def layout_specs(layouts: list) -> list[dict]:
    specs = [_spec(entry) for entry in layouts]
    keys = [s["key"] for s in specs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"citation_layouts: duplicate keys in {keys}")
    return specs


def citation_layouts(citation_network: ig.Graph, layout_specs: list[dict]) -> dict[str, tuple[pd.DataFrame, dict]]:
    """key -> (doi/x/y[/z] table, what was run)."""
    dois = [str(name) for name in citation_network.vs["name"]]
    cited = np.array(citation_network.degree(mode="in"))
    results = {}
    for spec in layout_specs:
        logger.info("%s: ForceAtlas2%s, %dD, %d iterations, %d papers", spec["key"],
                    " (LinLog)" if spec["lin_log"] else "", spec["dimensions"], spec["iterations"],
                    citation_network.vcount())
        start = time.time()
        positions, engine = forceatlas2(citation_network, dimensions=spec["dimensions"],
                                        iterations=spec["iterations"], lin_log=spec["lin_log"],
                                        seed=spec["seed"], engine=spec["engine"])
        seconds = time.time() - start
        positions = oriented(positions, cited)
        table = pd.DataFrame({"doi": dois, "x": positions[:, 0], "y": positions[:, 1]})
        if spec["dimensions"] == 3:
            table["z"] = positions[:, 2]
        logger.info("%s: done in %.0f s (engine %s)", spec["key"], seconds, engine)
        results[spec["key"]] = (table.round({"x": 4, "y": 4, "z": 4}),
                                {**spec, "engine": engine, "seconds": round(seconds, 1), "papers": len(dois)})
    return results


def save_citation_layouts(citation_layouts: dict, output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, (table, _) in citation_layouts.items():
        table.to_csv(output_dir / f"{key}.csv", index=False)
    manifest = {key: run for key, (_, run) in citation_layouts.items()}
    (output_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return {"output_dir": str(output_dir), "layouts": sorted(manifest)}


if __name__ == "__main__":
    sys.exit(_main())
