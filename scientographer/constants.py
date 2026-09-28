"""Paths, parameters and secrets shared by every DAG module.

- **Paths** are relative to the repository root, which is the working directory every
  DAG runs from (``pixi run python motor_learning_network/<module>.py``).
- **Parameters** (``PARAMS``) come from ``params.yaml`` next to this file -- or the file
  named by the ``MLN_PARAMS`` environment variable -- and are the one place a knob lives.
  ``dvc.yaml`` points at the same file, so ``dvc repro`` / ``dvc exp run -S key=value``
  re-run exactly the stages a key affects.
- **Secrets** are read from a ``.env`` at the repository root when one exists. Nothing
  here asserts on them: only the stages that call an external API need one, and they
  ask for it with ``require_secret`` at the point of use, so the graph/community/website
  stages run on a machine with no credentials at all.
"""

import os
from pathlib import Path

import dotenv
import yaml

RAW_DATA_PATH = Path("data", "raw")
RAW_DATA_PATH.mkdir(parents=True, exist_ok=True)

PROCESSED_DATA_PATH = Path("data", "processed")
PROCESSED_DATA_PATH.mkdir(parents=True, exist_ok=True)

GRAPH_LEVEL_DATA_PATH = Path("data", "graph_level_data")
GRAPH_LEVEL_DATA_PATH.mkdir(parents=True, exist_ok=True)

KEYWORDS_LEVEL_DATA_PATH = Path("data", "keywords_level_data")
KEYWORDS_LEVEL_DATA_PATH.mkdir(parents=True, exist_ok=True)

FIGURES_PATH = Path("reports", "figures")
FIGURES_PATH.mkdir(parents=True, exist_ok=True)

# Root for the retrieved full-text corpus (PDFs / XML). This lives OUTSIDE the repo
# because it runs to tens of GB and the repo volume is nearly full, while /raid has
# room. Small derived tables (manifests, parquet) still live under data/ so they can
# be DVC-tracked normally. Override with FULL_TEXT_DATA_ROOT in .env if the corpus
# needs to move; nothing here is created eagerly, since the volume may not be mounted
# on every machine that imports this module.
EXTERNAL_DATA_ROOT = Path(
    os.getenv("FULL_TEXT_DATA_ROOT", "/raid/fredi_dbs/papers-motor-learning-network")
)


# ── Parameters ────────────────────────────────────────────────────────────────
PARAMS_PATH = Path(os.getenv("MLN_PARAMS", str(Path(__file__).parent / "params.yaml")))


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
        raise KeyError(f"params.yaml ({PARAMS_PATH}) has no '{section}' section") from None


# ── Secrets (optional) ────────────────────────────────────────────────────────
ENV_PATH = Path(__file__).parent.parent / ".env"
read_dotenv = dotenv.load_dotenv(ENV_PATH) if ENV_PATH.exists() else False

EMAIL = os.getenv("MY_EMAIL")
DEFAULT_UI_USERNAME = os.getenv("DEFAULT_UI_USERNAME", "user")
TEAM_NAME = os.getenv("TEAM_NAME", "team")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
OPENCITATIONS_ACCESS_TOKEN = os.getenv("OPENCITATIONS_ACCESS_TOKEN")
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY")
SCOPUS_API_KEY = os.getenv("SCOPUS_API_KEY")

# Publisher text-and-data-mining credentials for the full-text stage
# (retrieve-full-text). All optional: the free routes work without any of them.
# Elsevier / ScienceDirect: the key only *identifies* the caller -- subscribed content
# needs entitlement via an institutional IP or InstToken.
ELSEVIER_API_KEY = os.getenv("ELSEVIER_API_KEY")
ELSEVIER_INSTITUTIONAL_TOKEN = os.getenv("ELSEVIER_INSTITUTIONAL_TOKEN")
# Wiley: sent as the Wiley-TDM-Client-Token header; issued by the library, normally
# still IP-restricted.
WILEY_TDM_TOKEN = os.getenv("WILEY_TDM_TOKEN")
# Springer Nature issues separate keys per API product: the Metadata API covers the
# whole catalogue, the Open Access API returns full text for open content only.
SPRINGER_NATURE_METADATA = os.getenv("SPRINGER_NATURE_METADATA")
SPRINGER_NATURE_OPEN_ACCESS = os.getenv("SPRINGER_NATURE_OPEN_ACCESS")
# Taylor & Francis: no self-served TDM key known; access is an institutional
# agreement. Measured 2026-08-14: 7 of 8 sampled T&F papers declare no Crossref TDM
# link, and the one that does returns 403 from this network.
TAYLOR_AND_FRANCIS_TDM_TOKEN = os.getenv("TAYLOR_AND_FRANCIS_TDM_TOKEN")

# SOCKS5 endpoint of the institutional (JHU) tunnel, when one is running
# (see docs/ENTITLEMENT_SURVEY.md).
# Empty means "go direct" -- every module using it must still work unproxied,
# just with less publisher entitlement. Set it inline per run rather than in
# .env, because the tunnel's underlying DSID cookie expires after a few hours:
#   INSTITUTIONAL_PROXY_URL=socks5h://127.0.0.1:11080 pixi run python -m ...
# Use socks5h:// rather than socks5://: the trailing "h" resolves DNS *through*
# the tunnel. With plain socks5:// the lookup happens locally, which both leaks
# it and can return an address that only makes sense on this side.
INSTITUTIONAL_PROXY_URL = os.getenv("INSTITUTIONAL_PROXY_URL", "")


def require_secret(name: str) -> str:
    """The value of an environment secret, or a clear error saying which stage
    needs it and where to put it. Call at the point of use, never at import."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. This stage calls an external service that needs it: "
            f"add it to {ENV_PATH} (never committed) or export it in the environment."
        )
    return value


# ── Hamilton UI tracker ───────────────────────────────────────────────────────
DEFAULT_UI_PROJECT_ID = int(PARAMS.get("tracker", {}).get("project_id", 1))


def tracker_adapters(dag_name: str, tags: dict | None = None) -> list:
    """The Hamilton UI tracker adapter when ``tracker.enabled`` is true in
    params.yaml, else an empty list -- so every ``_main`` can write
    ``.with_adapters(*tracker_adapters(CURRENT_FILE_NAME))`` and run offline by
    default. The study turns it on and runs ``hamilton ui --base-dir ./.hamilton/db``
    (the project with ``tracker.project_id`` must exist there first)."""
    cfg = PARAMS.get("tracker", {})
    if not cfg.get("enabled", False):
        return []
    from hamilton_sdk import adapters  # validates against the server at construction

    url = cfg.get("url", "http://localhost:8241")
    return [
        adapters.HamiltonTracker(
            project_id=int(cfg.get("project_id", 1)),
            username=DEFAULT_UI_USERNAME,
            dag_name=dag_name,
            tags={"environment": "DEV", "team": TEAM_NAME, "version": "0.1", **(tags or {})},
            hamilton_api_url=url,
            hamilton_ui_url=url,
        )
    ]
