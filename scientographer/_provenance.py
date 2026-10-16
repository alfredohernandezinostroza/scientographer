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
settings (the params.yaml sections it reads), on which input files (each with its
md5, the checksum ``dvc.lock`` keeps, so the two can be compared), which outputs it
wrote (paths only) and when (UTC). A missing input is recorded with ``md5: null``. A
directory input also gets its number of files and bytes, and one md5 over its
files: the sorted lines ``<relative path> <md5 of the file>``, which identifies the
contents but is not the ``.dir`` hash DVC writes. Hashing reads every byte (about
0.35 s for a 120 MB GraphML, 1.2 s for a 380 MB embedding store), so a directory
larger than ``DIRECTORY_HASH_LIMIT_BYTES`` (2 GB) is recorded by its files and bytes
only, with ``md5: null``.

**The record travels with the data.** A stage's records are its *lineage*: the steps
its inputs carry, merged (a step reached through two inputs counts once) and in
order of date, followed by its own step (``steps_for``). Where they are kept:

- a GraphML output: the graph-level attribute ``provenance``, a JSON list (igraph
  keeps graph attributes; Gephi drops them on import);
- a parquet output: the key-value metadata ``scientographer_provenance``, the same
  JSON list;
- an output folder: ``provenance.json`` inside it, ``{"steps": [...]}``, which DVC
  tracks with the folder. A CSV cannot carry metadata, so a CSV (or any file with
  no record of its own) is described by the ``provenance.json`` of its folder.

A stage that reads an output keeps the records it finds and adds its own step, so
the steps run from the search or table import all the way to the site, and a map
processed by one version and then another keeps both. Outputs made before
Scientographer kept records have none; reading them never fails.
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
import xml.etree.ElementTree as ET

from scientographer import __version__

# The graph-level GraphML attribute that holds a graph's step records.
PROVENANCE_ATTRIBUTE = "provenance"
# The parquet key-value metadata key that holds a table's step records.
PARQUET_METADATA_KEY = "scientographer_provenance"
# The file in an output folder that holds the folder's step records.
FOLDER_RECORD = "provenance.json"
# A directory input larger than this is recorded by its files and bytes, without an md5.
DIRECTORY_HASH_LIMIT_BYTES = 2 * 1024**3
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


def _unique_paths(paths: Iterable[Union[str, Path, None]]) -> list[Path]:
    seen, unique = set(), []
    for p in paths:
        if p is None or str(p) == "":
            continue
        key = Path(p).as_posix()
        if key not in seen:
            seen.add(key)
            unique.append(Path(p))
    return unique


def _input_entry(path: Path) -> dict:
    """``{"path", "md5"}`` for a file; a directory adds ``files`` and ``bytes`` and,
    above DIRECTORY_HASH_LIMIT_BYTES, has no md5."""
    entry = {"path": path.as_posix(), "md5": None}
    try:
        if path.is_dir():
            files = [child for child in path.rglob("*") if child.is_file()]
            total = sum(child.stat().st_size for child in files)
            entry.update(files=len(files), bytes=total)
            if total <= DIRECTORY_HASH_LIMIT_BYTES:
                entry["md5"] = file_md5(path)
            return entry
    except OSError:
        return entry
    entry["md5"] = file_md5(path)
    return entry


def step_record(
    stage: str,
    settings: Optional[dict],
    inputs: Iterable[Union[str, Path, None]],
    outputs: Iterable[Union[str, Path, None]] = (),
) -> dict:
    """One step: the stage, this Scientographer's exact version, the stage's
    settings, its inputs with their md5 (None entries skipped; a missing file gets
    ``md5: null``), the paths of its outputs and the date."""
    return {
        "stage": stage,
        "scientographer": exact_version(),
        "settings": _json_safe(dict(settings or {})),
        "inputs": [_input_entry(p) for p in _unique_paths(inputs)],
        "outputs": [p.as_posix() for p in _unique_paths(outputs)],
        "date": utc_now(),
    }


def stage_settings(*sections: str) -> dict:
    """The params.yaml sections a stage reads, as ``{section: values}`` (a section
    the file lacks is left out)."""
    from scientographer.config import PARAMS

    return {section: PARAMS[section] for section in sections if section in PARAMS}


# ── Lineage: the records an input carries ────────────────────────────────────
def history_from_text(text: Optional[str]) -> list[dict]:
    """The step records in a JSON text (a list, or ``{"steps": [...]}``); [] when it
    is absent or not one (anything in it that is not a record is dropped)."""
    if not text:
        return []
    try:
        steps = json.loads(text)
    except (TypeError, ValueError):
        return []
    if isinstance(steps, dict):
        steps = steps.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def graphml_history(path: Union[str, Path]) -> list[dict]:
    """The records in a GraphML file's ``provenance`` graph attribute, read without
    loading the graph (graph-level data come before the first node)."""
    key_id = None
    try:
        for event, element in ET.iterparse(str(path), events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start" and tag in ("node", "edge"):
                break
            if event != "end":
                continue
            if tag == "key" and element.get("for") == "graph" and element.get("attr.name") == PROVENANCE_ATTRIBUTE:
                key_id = element.get("id")
            elif tag == "data" and key_id is not None and element.get("key") == key_id:
                return history_from_text(element.text)
    except (OSError, ET.ParseError):
        return []
    return []


def parquet_history(path: Union[str, Path]) -> list[dict]:
    """The records in a parquet file's ``scientographer_provenance`` metadata."""
    try:
        import pyarrow.parquet as pq

        metadata = pq.read_schema(str(path)).metadata or {}
    except Exception:  # not a parquet file, unreadable, or pyarrow missing
        return []
    text = metadata.get(PARQUET_METADATA_KEY.encode("utf-8"))
    return history_from_text(text.decode("utf-8") if text else None)


def folder_history(folder: Union[str, Path]) -> list[dict]:
    """The records in a folder's ``provenance.json``."""
    try:
        return history_from_text((Path(folder) / FOLDER_RECORD).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def input_history(path: Union[str, Path, None]) -> list[dict]:
    """The records an input carries: a folder's ``provenance.json``, a GraphML's
    attribute or a parquet's metadata, else its own folder's ``provenance.json``;
    [] for an input made without records."""
    if path is None or str(path) == "":
        return []
    path = Path(path)
    if path.is_dir():
        return folder_history(path)
    steps = []
    if path.suffix.lower() == ".graphml":
        steps = graphml_history(path)
    elif path.suffix.lower() == ".parquet":
        steps = parquet_history(path)
    if not steps and path.exists():
        steps = folder_history(path.parent)
    return steps


def _step_key(step: dict) -> tuple:
    return (step.get("stage"), step.get("date"), step.get("scientographer"))


def merge_steps(*histories: Iterable[dict]) -> list[dict]:
    """Several inputs' records as one lineage: each step once (same stage, date and
    version), in order of date (steps without a date first, in the order found)."""
    seen, merged = set(), []
    for steps in histories:
        for step in steps or ():
            if not isinstance(step, dict):
                continue
            key = _step_key(step)
            if key in seen:
                continue
            seen.add(key)
            merged.append(step)
    return sorted(merged, key=lambda step: str(step.get("date") or ""))


def lineage(inputs: Iterable[Union[str, Path, None]]) -> list[dict]:
    """The merged records of every input."""
    return merge_steps(*(input_history(p) for p in _unique_paths(inputs)))


def steps_for(
    stage: str,
    settings: Optional[dict],
    inputs: Iterable[Union[str, Path, None]],
    outputs: Iterable[Union[str, Path, None]] = (),
) -> list[dict]:
    """A stage's records: its inputs' lineage, then its own step."""
    inputs = list(inputs)
    return lineage(inputs) + [step_record(stage, settings, inputs, outputs)]


# ── Writing the records next to the data ──────────────────────────────────────
def history(graph) -> list[dict]:
    """The step records an igraph graph carries (oldest first); [] when it has none."""
    if PROVENANCE_ATTRIBUTE not in graph.attributes():
        return []
    text = graph[PROVENANCE_ATTRIBUTE]
    return history_from_text(text if isinstance(text, str) else None)


def write_graphml_with_steps(graph, path: Union[str, Path], steps: list[dict]) -> None:
    """Write the graph to GraphML carrying ``steps`` as its history, leaving the
    in-memory graph as it was (other nodes of the DAG may still read it)."""
    had_history = PROVENANCE_ATTRIBUTE in graph.attributes()
    previous = graph[PROVENANCE_ATTRIBUTE] if had_history else None
    try:
        graph[PROVENANCE_ATTRIBUTE] = json.dumps(steps, ensure_ascii=False)
        graph.write_graphml(str(path))
    finally:
        if had_history:
            graph[PROVENANCE_ATTRIBUTE] = previous
        else:
            del graph[PROVENANCE_ATTRIBUTE]


def write_parquet_with_steps(table, path: Union[str, Path], steps: list[dict], index: Optional[bool] = None) -> None:
    """``table.to_parquet(path, index=index)``, with ``steps`` in the file's
    key-value metadata."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrow = pa.Table.from_pandas(table, preserve_index=index)
    metadata = dict(arrow.schema.metadata or {})
    metadata[PARQUET_METADATA_KEY.encode("utf-8")] = json.dumps(steps, ensure_ascii=False).encode("utf-8")
    pq.write_table(arrow.replace_schema_metadata(metadata), str(path))


def write_folder_record(folder: Union[str, Path], steps: list[dict]) -> Path:
    """Write ``<folder>/provenance.json`` (``{"steps": [...]}``), replacing any older one."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FOLDER_RECORD
    tmp = folder / f".{FOLDER_RECORD}.tmp"
    tmp.write_text(json.dumps({"steps": steps}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
