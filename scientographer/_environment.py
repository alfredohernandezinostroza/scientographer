# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The environment a step ran in, and whether it is the one the project locked.

Leiden's partitions, the layouts and the keyword scores change between library
versions even with the same seed, so a step record (``_provenance.step_record``)
carries its environment:

- ``python``: the full version (``3.12.4``); ``platform``: ``Linux-x86_64``;
- ``packages``: the installed versions of ``KEY_PACKAGES``, the libraries whose
  version changes a result (only those installed);
- ``lock``: how the installed packages compare with the project's lock file.

**The project's lock** is looked for in the project directory (the working
directory, where ``dvc.yaml`` and ``params.yaml`` are and the stages run), in this
order:

1. ``pixi.lock``: the packages of the environment ``default`` (or the one named by
   ``PIXI_ENVIRONMENT_NAME``, which ``pixi run`` sets) for this platform
   (``linux-64``, ``osx-arm64``, ...), conda and PyPI packages alike;
2. ``uv.lock``: every ``[[package]]`` from a registry (it needs ``tomllib``, in
   Python 3.11 and later, or the ``tomli`` package on 3.10; without either it is
   skipped, with a note in the record);
3. ``requirements.txt``, only when every requirement in it is pinned with ``==`` (a
   ``pip freeze``); a file with ranges is not a lock and is ignored.

**The comparison** takes every locked package that has an installed Python
distribution and compares the versions; names are compared normalised (PEP 503),
and conda names are mapped to their distribution names (from the lock's ``purls``,
else ``CONDA_TO_DISTRIBUTION``). The lock's Python (pixi only: uv and pip locks do
not pin it) is compared with the running one. Editable, path and git dependencies
are skipped: Scientographer itself is often one, and its exact version is in the
record anyway. A locked package that is not installed is a difference only for a
key package. Conda packages that are not Python distributions (the Graphviz
program, compilers, C libraries) cannot be checked from Python and are ignored.

**Refusal.** The entry points (``scientographer run`` / ``pipeline`` and every
stage's ``python -m scientographer.<stage>``) call ``require_locked_environment``:
when a lock exists and the environment does not match it, the stage does not run;
the message lists the differences and how to fix them. Setting
``SCIENTOGRAPHER_ALLOW_ENVIRONMENT_MISMATCH=1`` (or ``--allow-environment-mismatch``)
runs it anyway with a loud warning, and the record keeps ``override: true`` with
the differences. Without a lock, stages run as before and record ``{"kind": null}``.

The lock is parsed once per process (pixi.lock with libyaml when PyYAML has it:
about 40 ms for a 200 KB lock).
"""

from functools import lru_cache
import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import re
import sys
from typing import Optional

# The libraries whose version can change a result: graph construction and Leiden
# (igraph, leidenalg, cdlib, networkx), the numbers underneath (numpy, scipy, pandas,
# pyarrow), the layouts (fa2, numba), the keyword scores (scikit-learn), word clouds
# and figures (wordcloud, matplotlib, shapely), and the embedding maps (torch,
# transformers, adapters, umap-learn, hdbscan, bertopic). Distribution names.
KEY_PACKAGES = (
    "igraph", "leidenalg", "cdlib", "networkx",
    "numpy", "scipy", "pandas", "pyarrow",
    "fa2", "numba", "scikit-learn",
    "wordcloud", "matplotlib", "shapely",
    "torch", "transformers", "adapters", "umap-learn", "hdbscan", "bertopic",
)
# Older installs of igraph are the distribution "python-igraph".
DISTRIBUTION_ALIASES = {"igraph": ("igraph", "python-igraph")}
# conda-forge names that differ from the Python distribution, for locks whose conda
# entries carry no ``purls``.
CONDA_TO_DISTRIBUTION = {
    "python-igraph": "igraph",
    "pytorch": "torch",
    "matplotlib-base": "matplotlib",
    "umap-learn": "umap-learn",
    "py-opencv": "opencv-python",
}
# conda packages that share a name with a Python distribution but are something else
# (the Graphviz program vs the `graphviz` Python bindings).
CONDA_NOT_PYTHON = {"graphviz", "python", "libpython", "python_abi"}
# The variable that lets a stage run on an environment that differs from the lock.
OVERRIDE_VARIABLE = "SCIENTOGRAPHER_ALLOW_ENVIRONMENT_MISMATCH"
LOCK_FILES = ("pixi.lock", "uv.lock", "requirements.txt")
# How many differences the refusal message lists (the record keeps them all).
MESSAGE_DIFFERENCES = 25

_OVERRIDE_WARNED = False


# ── Names and versions ────────────────────────────────────────────────────────
def normalise_name(name: str) -> str:
    """A distribution name as PEP 503 compares it: lower case, runs of ``-_.`` as ``-``."""
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def same_version(installed: str, locked: str) -> bool:
    """True when two versions are the same release (``1.0`` and ``1.0.0`` are)."""
    if installed == locked:
        return True
    try:
        from packaging.version import Version

        return Version(installed) == Version(locked)
    except Exception:  # packaging missing or a version it cannot read
        return str(installed).strip() == str(locked).strip()


@lru_cache(maxsize=1)
def installed_distributions() -> dict:
    """Every installed distribution, ``{normalised name: version}``, sorted by name
    (the first one found wins, as Python's imports do)."""
    found = {}
    for distribution in metadata.distributions():
        try:
            name = distribution.metadata["Name"]
            version = distribution.version
        except Exception:
            continue
        if name and version and normalise_name(name) not in found:
            found[normalise_name(name)] = version
    return dict(sorted(found.items()))


def _installed_version(name: str) -> Optional[str]:
    installed = installed_distributions()
    for candidate in DISTRIBUTION_ALIASES.get(name, (name,)):
        version = installed.get(normalise_name(candidate))
        if version:
            return version
    return None


def key_packages() -> dict:
    """``{name: version}`` of the KEY_PACKAGES that are installed."""
    versions = {}
    for name in KEY_PACKAGES:
        version = _installed_version(name)
        if version:
            versions[name] = version
    return versions


def python_version() -> str:
    return platform.python_version()


def platform_name() -> str:
    """The operating system and machine, e.g. ``Linux-x86_64``."""
    return f"{platform.system()}-{platform.machine()}"


def pixi_platform() -> str:
    """This machine as pixi names platforms: ``linux-64``, ``osx-arm64``, ``win-64``..."""
    machine = platform.machine().lower()
    bits = {"x86_64": "64", "amd64": "64", "arm64": "arm64", "aarch64": "aarch64",
            "ppc64le": "ppc64le", "i386": "32", "i686": "32"}.get(machine, machine)
    if sys.platform.startswith("linux"):
        return f"linux-{bits}"
    if sys.platform == "darwin":
        return "osx-arm64" if bits in ("arm64", "aarch64") else "osx-64"
    if sys.platform.startswith("win"):
        return "win-arm64" if bits in ("arm64", "aarch64") else "win-64"
    return f"{sys.platform}-{bits}"


# ── Reading the locks: {"python": version or None, "packages": {name: version}} ──
def _conda_name_version(url: str) -> tuple[str, str]:
    """``(name, version)`` from a conda package's file name, ``name-version-build.conda``."""
    filename = url.rsplit("/", 1)[-1]
    for extension in (".conda", ".tar.bz2"):
        filename = filename.removesuffix(extension)
    parts = filename.rsplit("-", 2)
    return (parts[0], parts[1]) if len(parts) == 3 else (filename, "")


def _is_pinnable_source(source: str) -> bool:
    """A package from an index or a URL to an archive, not a path, editable or git one."""
    return source.startswith(("http://", "https://")) and not source.startswith("git+")


def parse_pixi_lock(text: str, environment: str, platform_key: str) -> dict:
    """The packages of one environment and platform in a pixi.lock text. Raises
    ValueError when the lock has no such environment or platform."""
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    document = yaml.load(text, Loader=loader) or {}
    environments = document.get("environments") or {}
    if environment not in environments:
        raise ValueError(f"pixi.lock has no environment {environment!r}")
    listed = ((environments[environment] or {}).get("packages") or {}).get(platform_key)
    if listed is None:
        raise ValueError(f"pixi.lock has no packages for {platform_key} in environment {environment!r}")
    entries = {}
    for entry in document.get("packages") or []:
        if not isinstance(entry, dict):
            continue
        kind = "conda" if "conda" in entry else "pypi" if "pypi" in entry else entry.get("kind")
        source = entry.get("conda") or entry.get("pypi") or entry.get("url")
        if kind and source:
            entries.setdefault((kind, str(source)), entry)
    python, packages = None, {}
    for item in listed:
        if not isinstance(item, dict):
            continue
        kind = "conda" if "conda" in item else "pypi"
        source = str(item.get(kind) or "")
        entry = entries.get((kind, source), {})
        if kind == "conda":
            name, version = _conda_name_version(source)
            name, version = entry.get("name") or name, str(entry.get("version") or version)
            if name == "python":
                python = version
                continue
            if "purls" in entry:
                names = [p.split("/", 1)[1].split("@", 1)[0].split("?", 1)[0]
                         for p in entry.get("purls") or [] if str(p).startswith("pkg:pypi/")]
            elif name in CONDA_NOT_PYTHON:
                names = []
            else:
                names = [CONDA_TO_DISTRIBUTION.get(name, name)]
            for distribution in names:
                packages[normalise_name(distribution)] = version
        else:
            if not _is_pinnable_source(source) or entry.get("editable") or not entry.get("version"):
                continue
            packages[normalise_name(entry.get("name", ""))] = str(entry["version"])
    return {"python": python, "packages": packages}


def _toml_loads(text: str) -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib  # raises ImportError when it is not installed either
    return tomllib.loads(text)


def parse_uv_lock(text: str) -> dict:
    """The registry packages in a uv.lock text (uv does not pin Python itself)."""
    packages = {}
    for package in _toml_loads(text).get("package") or []:
        source = package.get("source") or {}
        if "registry" not in source and "url" not in source:
            continue  # editable, virtual, path and git packages
        if package.get("name") and package.get("version"):
            packages[normalise_name(package["name"])] = str(package["version"])
    return {"python": None, "packages": packages}


_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*==\s*([^\s;,]+)\s*(;.*)?$")


def parse_requirements(text: str) -> Optional[dict]:
    """The packages of a requirements.txt in which every requirement is pinned with
    ``==`` (a ``pip freeze``); None when any is not. Editable, path, URL and git
    requirements are skipped, as are options and comments."""
    packages = {}
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        line = re.sub(r"\s+--hash=\S+", "", line).rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            continue  # -e/--editable, -r, --index-url and other options
        if " @ " in line or "://" in line or line.startswith((".", "/")):
            continue  # a direct reference: path, URL or git
        match = _REQUIREMENT.match(line)
        if not match:
            return None
        packages[normalise_name(match.group(1))] = match.group(3)
    return {"python": None, "packages": packages} if packages else None


@lru_cache(maxsize=8)
def _read_lock(path: str, modified: int, size: int, environment: str, platform_key: str) -> dict:
    """Parse one lock file (cached by path, modification time and size)."""
    lock_path = Path(path)
    data = lock_path.read_bytes()
    text = data.decode("utf-8")
    result = {"md5": hashlib.md5(data).hexdigest(), "note": None, "parsed": None, "error": None}
    try:
        if lock_path.name == "pixi.lock":
            result["parsed"] = parse_pixi_lock(text, environment, platform_key)
        elif lock_path.name == "uv.lock":
            try:
                result["parsed"] = parse_uv_lock(text)
            except ImportError:
                result["note"] = "uv.lock not checked: reading it needs Python 3.11 or the tomli package"
        else:
            result["parsed"] = parse_requirements(text)
            if result["parsed"] is None:
                result["note"] = "requirements.txt is not a lock: not every requirement is pinned with =="
    except ValueError as error:
        result["error"] = str(error)
    return result


def project_lock(root: Optional[Path] = None) -> Optional[dict]:
    """The project's lock: ``{"kind", "path", "md5", "packages", "python"}`` for the
    first usable one of LOCK_FILES; for a pixi.lock without this environment or
    platform, ``error`` says so (that counts as a mismatch); None without a lock
    (``note`` then may say why a file there was not used)."""
    root = Path.cwd() if root is None else Path(root)
    environment = os.environ.get("PIXI_ENVIRONMENT_NAME") or "default"
    platform_key = pixi_platform()
    notes = []
    for filename in LOCK_FILES:
        path = root / filename
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        read = _read_lock(str(path.resolve()), stat.st_mtime_ns, stat.st_size, environment, platform_key)
        kind = {"pixi.lock": "pixi", "uv.lock": "uv"}.get(filename, "requirements")
        if read["parsed"] is None and read["error"] is None:
            notes.append(read["note"])
            continue
        lock = {"kind": kind, "path": filename, "md5": read["md5"],
                "python": None, "packages": {}, "error": read["error"]}
        if kind == "pixi":
            lock["environment"], lock["platform"] = environment, platform_key
        if read["parsed"]:
            lock.update(python=read["parsed"]["python"], packages=read["parsed"]["packages"])
        return lock
    return {"kind": None, "note": "; ".join(n for n in notes if n)} if notes else None


# ── The comparison ────────────────────────────────────────────────────────────
def compare(lock: dict, installed: dict, python: str) -> list[dict]:
    """The differences between a lock and the installed distributions, as
    ``{"package", "installed", "locked"}`` (``installed`` None: not installed):
    Python first, then key packages, then the rest, each by name."""
    differences = []
    if lock.get("error"):
        running = f"{lock.get('platform')} ({lock.get('environment')})"
        differences.append({"package": "platform", "installed": running, "locked": lock["error"]})
    if lock.get("python") and not same_version(python, lock["python"]):
        differences.append({"package": "python", "installed": python, "locked": lock["python"]})
    key_names = {normalise_name(alias): name for name in KEY_PACKAGES
                 for alias in DISTRIBUTION_ALIASES.get(name, (name,))}
    rest = []
    for name, locked in sorted(lock.get("packages", {}).items()):
        version = installed.get(name)
        if version is None:
            if name in key_names and _installed_version(key_names[name]) is None:
                differences.append({"package": name, "installed": None, "locked": locked})
            continue
        if not same_version(version, locked):
            (differences if name in key_names else rest).append(
                {"package": name, "installed": version, "locked": locked})
    return differences + rest


def override_requested() -> bool:
    """True when ``SCIENTOGRAPHER_ALLOW_ENVIRONMENT_MISMATCH`` is set to a true value."""
    return os.environ.get(OVERRIDE_VARIABLE, "").strip().lower() in ("1", "true", "yes", "on")


def lock_status(root: Optional[Path] = None) -> dict:
    """The ``lock`` part of a record: ``{"kind", "path", "md5", "matches",
    "differences", "override"}``, or ``{"kind": None}`` without a lock."""
    lock = project_lock(root)
    if lock is None or lock.get("kind") is None:
        status = {"kind": None}
        if lock and lock.get("note"):
            status["note"] = lock["note"]
        return status
    differences = compare(lock, installed_distributions(), python_version())
    status = {"kind": lock["kind"], "path": lock["path"], "md5": lock["md5"],
              "matches": not differences, "differences": differences,
              "override": bool(differences) and override_requested()}
    if lock["kind"] == "pixi":
        status.update(environment=lock["environment"], platform=lock["platform"])
    return status


def step_environment(root: Optional[Path] = None) -> dict:
    """The ``environment`` of a step record: Python, platform, key packages, lock."""
    return {"python": python_version(), "platform": platform_name(),
            "packages": key_packages(), "lock": lock_status(root)}


def full_environment(root: Optional[Path] = None) -> dict:
    """The environment that built a site: Python, platform, every installed
    distribution (sorted) and the lock."""
    return {"python": python_version(), "platform": platform_name(),
            "packages": dict(installed_distributions()), "lock": lock_status(root)}


# ── Refusing to run on an environment that is not the locked one ──────────────
def _describe(difference: dict, lock_path: str) -> str:
    installed = difference.get("installed")
    if difference.get("package") == "platform":
        return f"{lock_path}: {difference.get('locked')} (running on {installed})"
    have = f"{installed} installed" if installed else "not installed"
    return f"{difference['package']} {have}, {lock_path} says {difference['locked']}"


def _how_to_fix(status: dict, command: str) -> str:
    if status["kind"] == "pixi":
        return (f"Run inside the project's environment: `pixi run {command}` (or `pixi shell` first); "
                "`pixi install` installs exactly what pixi.lock says.")
    if status["kind"] == "uv":
        return (f"Run inside the project's environment: `uv run {command}`; "
                "`uv sync` installs what uv.lock says.")
    return ("Install what requirements.txt says into this environment (`pip install -r requirements.txt`), "
            "or activate the environment it was written from.")


def mismatch_message(status: dict, command: str = "scientographer run <stage>") -> str:
    """The refusal: which packages differ from the lock, how to fix it (``command``
    is what was run), and the override."""
    differences = status.get("differences") or []
    where = status["path"]
    if status["kind"] == "pixi":
        where += f" (environment {status.get('environment')}, {status.get('platform')})"
    lines = [f"This environment does not match the project's {where}:"]
    lines += [f"  {_describe(d, status['path'])}" for d in differences[:MESSAGE_DIFFERENCES]]
    if len(differences) > MESSAGE_DIFFERENCES:
        lines.append(f"  ... and {len(differences) - MESSAGE_DIFFERENCES} more")
    lines += [
        "Results depend on these versions, so nothing was run.",
        _how_to_fix(status, command),
        f"To run anyway and record the differences, set {OVERRIDE_VARIABLE}=1 "
        "(or pass --allow-environment-mismatch to `scientographer run` / `pipeline`).",
    ]
    return "\n".join(lines)


class EnvironmentMismatch(RuntimeError):
    """The installed packages differ from the project's lock and no override was given."""


def check_locked_environment(stage: Optional[str] = None, root: Optional[Path] = None,
                             command: Optional[str] = None) -> dict:
    """The lock status; raises EnvironmentMismatch when a lock exists, differs and
    no override is set. With the override, warns (once per process) and returns."""
    global _OVERRIDE_WARNED
    status = lock_status(root)
    if status.get("kind") is None or status.get("matches"):
        return status
    if not status.get("override"):
        command = command or (f"scientographer run {stage}" if stage else "scientographer run <stage>")
        raise EnvironmentMismatch(mismatch_message(status, command))
    if not _OVERRIDE_WARNED:
        _OVERRIDE_WARNED = True
        listed = "; ".join(_describe(d, status["path"]) for d in status["differences"][:MESSAGE_DIFFERENCES])
        warning = (f"WARNING: running outside the project's {status['path']} because {OVERRIDE_VARIABLE} "
                   f"is set. The step record keeps the differences ({len(status['differences'])}): {listed}")
        print(warning, file=sys.stderr)
    return status


def require_locked_environment(stage: Optional[str] = None) -> None:
    """For a stage's entry point: exit with status 1 and the refusal message when
    the environment does not match the project's lock (see the module docstring)."""
    try:
        check_locked_environment(stage)
    except EnvironmentMismatch as error:
        print(f"scientographer: {error}", file=sys.stderr)
        sys.exit(1)
