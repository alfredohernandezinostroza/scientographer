# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a map was made: the exact Scientographer version, and one record per step.

**The exact version** (``exact_version()``). Installed as a release (from PyPI) it is
``__version__``, e.g. ``0.2.0``. Run from a git checkout of Scientographer itself it
is the version plus the commit, as a PEP 440 local version --
``0.2.0.dev0+g1a2b3c4`` -- with ``.dirty`` added when tracked files have uncommitted
changes (``0.2.0.dev0+g1a2b3c4.dirty``). It is read at run time with ``git`` in the
package's folder, so releasing stays as it is. Only a checkout *of Scientographer*
counts: the package folder's parent must be the top of the git tree and that
tree's ``pyproject.toml`` must name the project ``scientographer``. Scientographer
installed into an environment inside a user's own git repository (a pixi or venv
folder under the project) therefore reports its release, never the user's commit.
When git is missing, fails or is slow, the answer is plain ``__version__``; this
module never raises over it.

**A step record** (``step_record()``): which stage ran, on which version, with which
settings (its params.yaml section), on which input files (each with its md5, the
checksum ``dvc.lock`` keeps, so the two can be compared), and when (UTC). A
directory input gets one md5 over its files: the sorted lines ``<relative path>
<md5 of the file>``, which identifies the contents but is not the ``.dir`` hash
DVC writes. A missing input is recorded with ``md5: null``.

**A graph's history**: a GraphML output keeps the records of every step that made
it in the graph-level attribute ``provenance``, a JSON list, oldest first. A stage
that reads a graph keeps the history it finds and adds its own step
(``write_graphml_with_step``), so a map processed by one version and then another
keeps both steps. Gephi drops graph-level attributes on import; igraph keeps them.
"""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Iterable, Optional, Union

from scientographer import __version__

# The graph-level GraphML attribute that holds a graph's step records.
PROVENANCE_ATTRIBUTE = "provenance"
# How long a git call may take before the plain version is used instead.
GIT_TIMEOUT_SECONDS = 5
PACKAGE_DIR = Path(__file__).resolve().parent
_CHUNK_BYTES = 1 << 20
_PROJECT_NAME = re.compile(r"""^\s*name\s*=\s*["']scientographer["']\s*$""", re.MULTILINE)


# ── The exact version ─────────────────────────────────────────────────────────
def _git(directory: Path, *arguments: str) -> Optional[str]:
    """The output of ``git -C <directory> <arguments>``, or None when git is
    missing, fails or times out."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _is_scientographer_checkout(package_dir: Path) -> bool:
    """True when ``package_dir`` sits at the top of a git tree whose
    ``pyproject.toml`` names the project ``scientographer`` (works for git
    worktrees too, where ``.git`` is a file)."""
    toplevel = _git(package_dir, "rev-parse", "--show-toplevel")
    if not toplevel or not toplevel.strip():
        return False
    try:
        if Path(toplevel.strip()).resolve() != package_dir.resolve().parent:
            return False
        pyproject = (package_dir.resolve().parent / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    project_table = pyproject.split("[project]", 1)
    return len(project_table) == 2 and bool(_PROJECT_NAME.search(project_table[1].split("\n[", 1)[0]))


def version_for(package_dir: Path, base_version: str) -> str:
    """The exact version of the package in ``package_dir`` whose release version is
    ``base_version`` (see the module docstring). Never raises."""
    try:
        if not _is_scientographer_checkout(package_dir):
            return base_version
        commit = _git(package_dir, "rev-parse", "--short=7", "HEAD")
        if not commit or not commit.strip():
            return base_version
        status = _git(package_dir, "status", "--porcelain", "--untracked-files=no")
        if status is None:
            return base_version
        local = "g" + commit.strip() + (".dirty" if status.strip() else "")
        # A version that already has a local part gets ours appended to it.
        return f"{base_version}.{local}" if "+" in base_version else f"{base_version}+{local}"
    except Exception:  # the version must never stop a run
        return base_version


@lru_cache(maxsize=1)
def exact_version() -> str:
    """This Scientographer's exact version: the release, or the release plus the
    commit (and ``.dirty``) when run from a git checkout of Scientographer."""
    return version_for(PACKAGE_DIR, __version__)


# ── Step records ──────────────────────────────────────────────────────────────
def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_md5(path: Union[str, Path]) -> Optional[str]:
    """The md5 of a file's contents (what dvc.lock records); for a directory, the
    md5 of its sorted ``<relative path> <md5>`` lines; None when it is missing."""
    path = Path(path)
    try:
        if path.is_file():
            return _file_md5(path)
        if path.is_dir():
            lines = (
                f"{child.relative_to(path).as_posix()} {_file_md5(child)}\n"
                for child in sorted(path.rglob("*"))
                if child.is_file()
            )
            digest = hashlib.md5()
            for line in lines:
                digest.update(line.encode("utf-8"))
            return digest.hexdigest()
    except OSError:
        return None
    return None


def _json_safe(value):
    """``value`` with paths, tuples, sets and numpy scalars turned into plain JSON
    values (anything else unknown becomes its text)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(v) for v in value), key=str)
    if hasattr(value, "item"):  # numpy scalars
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def utc_now() -> str:
    """The current time in UTC, ISO 8601 to the second (``2026-10-15T09:30:00Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def step_record(stage: str, settings: Optional[dict], inputs: Iterable[Union[str, Path, None]]) -> dict:
    """One step: the stage, this Scientographer's exact version, the stage's
    settings (its params.yaml section), its input files with their md5 (None
    entries skipped; a missing file gets ``md5: null``) and the date."""
    return {
        "stage": stage,
        "scientographer": exact_version(),
        "settings": _json_safe(dict(settings or {})),
        "inputs": [
            {"path": Path(p).as_posix(), "md5": file_md5(p)} for p in inputs if p is not None
        ],
        "date": utc_now(),
    }


# ── A graph's history ─────────────────────────────────────────────────────────
def history_from_text(text: Optional[str]) -> list[dict]:
    """The step records in a ``provenance`` attribute's text; [] when it is absent
    or not a JSON list (anything in it that is not a record is dropped)."""
    if not text:
        return []
    try:
        steps = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def history(graph) -> list[dict]:
    """The step records an igraph graph carries (oldest first); [] when it has none."""
    if PROVENANCE_ATTRIBUTE not in graph.attributes():
        return []
    text = graph[PROVENANCE_ATTRIBUTE]
    return history_from_text(text if isinstance(text, str) else None)


def with_step(graph, record: dict):
    """Add ``record`` to the graph's history (in place) and return the graph."""
    graph[PROVENANCE_ATTRIBUTE] = json.dumps(history(graph) + [record], ensure_ascii=False)
    return graph


def write_graphml_with_step(graph, path: Union[str, Path], record: dict) -> None:
    """Write the graph to GraphML with ``record`` added to its history, leaving the
    in-memory graph as it was (other nodes of the DAG may still read it)."""
    had_history = PROVENANCE_ATTRIBUTE in graph.attributes()
    previous = graph[PROVENANCE_ATTRIBUTE] if had_history else None
    try:
        with_step(graph, record)
        graph.write_graphml(str(path))
    finally:
        if had_history:
            graph[PROVENANCE_ATTRIBUTE] = previous
        else:
            del graph[PROVENANCE_ATTRIBUTE]
