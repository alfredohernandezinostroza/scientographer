# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paths and parameters shared by every Scientographer stage.

- **Parameters** (``PARAMS``) come from one ``params.yaml``, found in this order:
  the file named by the ``SCIENTOGRAPHER_PARAMS`` environment variable; else
  ``params.yaml`` in the current directory (a project created by
  ``scientographer init``); else the documented defaults shipped with the
  package. ``dvc.yaml`` reads the same file, so ``dvc repro`` and
  ``dvc exp run -S key=value`` re-run exactly the stages a key affects.
- **Paths** are relative to the project directory, the working directory every
  stage runs from. Importing this module never creates anything on disk; each
  stage creates its own output directories when it runs (``ensure_dirs``).
- **Secrets**: none of the stages in this package call an external service. A
  ``.env`` next to ``params.yaml`` is still loaded when present, for the Hamilton
  UI tracker's user name and for any stage a project adds on top.
"""

import os
from pathlib import Path

import dotenv
import yaml

PACKAGE_DEFAULT_PARAMS = Path(__file__).parent / "params.yaml"

RAW_DATA_PATH = Path("data", "raw")
PROCESSED_DATA_PATH = Path("data", "processed")
GRAPH_LEVEL_DATA_PATH = Path("data", "graph_level_data")
KEYWORDS_LEVEL_DATA_PATH = Path("data", "keywords_level_data")
FIGURES_PATH = Path("reports", "figures")


def ensure_dirs(*paths: Path) -> None:
    """Create output directories. Called by each stage when it runs, never at import."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


# ── Parameters ────────────────────────────────────────────────────────────────
def find_params_path() -> Path:
    explicit = os.getenv("SCIENTOGRAPHER_PARAMS")
    if explicit:
        return Path(explicit)
    local = Path("params.yaml")
    if local.exists():
        return local
    return PACKAGE_DEFAULT_PARAMS


PARAMS_PATH = find_params_path()


def load_params(path: Path = PARAMS_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must hold a mapping of sections, got {type(loaded).__name__}")
    return loaded


PARAMS: dict = load_params()


def params(section: str) -> dict:
    """One top-level section of params.yaml (``params("website")``), with a clear
    error naming the file when the section is missing."""
    try:
        return PARAMS[section]
    except KeyError:
        raise KeyError(f"{PARAMS_PATH} has no '{section}' section") from None


# ── Environment (optional) ────────────────────────────────────────────────────
ENV_PATH = PARAMS_PATH.parent / ".env"
read_dotenv = dotenv.load_dotenv(ENV_PATH) if ENV_PATH.exists() else False


def require_secret(name: str) -> str:
    """The value of an environment secret, or a clear error saying where to put
    it. For stages a project adds on top; call it at the point of use."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set: add it to {ENV_PATH} (never commit it) or export it.")
    return value


# ── Hamilton UI tracker ───────────────────────────────────────────────────────
def tracker_adapters(dag_name: str, tags: dict | None = None) -> list:
    """The Hamilton UI tracker adapter when ``tracker.enabled`` is true in
    params.yaml, else an empty list, so every stage can write
    ``.with_adapters(*tracker_adapters(CURRENT_FILE_NAME))`` and run offline by
    default. Start the server with ``scientographer ui`` (the project with
    ``tracker.project_id`` must exist there first)."""
    cfg = PARAMS.get("tracker", {})
    if not cfg.get("enabled", False):
        return []
    from hamilton_sdk import adapters  # validates against the server at construction

    url = cfg.get("url", "http://localhost:8241")
    return [adapters.HamiltonTracker(
        project_id=int(cfg.get("project_id", 1)),
        username=os.getenv("HAMILTON_UI_USERNAME", cfg.get("username", "scientographer")),
        dag_name=dag_name,
        tags={"environment": "DEV", "version": "0.1", **(tags or {})},
        hamilton_api_url=url,
        hamilton_ui_url=url,
    )]


# ── DAG figures ───────────────────────────────────────────────────────────────
def draw_dag(dr, dag_name: str, outputs: list | None = None, inputs: dict | None = None) -> None:
    """Draw a stage's Hamilton DAG to reports/figures/ (all nodes, and the executed
    path when ``outputs`` are given). Needs the `graphviz` Python package and the
    Graphviz `dot` program (``pip install "scientographer[figures]"``); without them
    the stage runs as usual and just skips the drawing."""
    import importlib.util
    import logging
    import shutil

    log = logging.getLogger(dag_name)
    if importlib.util.find_spec("graphviz") is None or shutil.which("dot") is None:
        log.info("graphviz not installed; skipping the DAG figure for %s", dag_name)
        return
    ensure_dirs(FIGURES_PATH)
    try:
        dr.display_all_functions(
            FIGURES_PATH / f"{dag_name}_all_functions.png", keep_dot=True, deduplicate_inputs=True)
        if outputs:
            dr.visualize_execution(
                outputs, inputs=inputs or {},
                output_file_path=FIGURES_PATH / f"{dag_name}.png", keep_dot=False, deduplicate_inputs=True)
    except Exception as error:  # a figure must never fail a stage
        log.warning("could not draw the DAG figure for %s: %s", dag_name, error)
