# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scientographer`` -- the command line.

Every stage is a Hamilton DAG module of the package; a project wires them together
in its ``dvc.yaml`` and keeps every setting in its ``params.yaml``::

    scientographer init my-project        create a project for your papers (citations fetched)
    scientographer init my-project --pubmed QUERY   ... whose corpus is a PubMed search
    scientographer example [dir]          create the example project, data included
    scientographer stages                 list the stages (* = declared in ./dvc.yaml)
    scientographer run detect_communities run one stage in the current project
    scientographer pipeline [stage]       `dvc repro`: everything out of date, or up to one stage
    scientographer website [--port 8123]  serve the built site
    scientographer ui [--port 8241]       start the Hamilton UI tracker for this project
    scientographer params                 show which params.yaml is in effect
    scientographer store-existing-results fill the per-resolution stores from current outputs
"""

from importlib import resources
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from typing import Final, Optional

import typer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Map a scientific literature from its citation network. Start with `scientographer init`.",
)

PACKAGE_DIR = Path(__file__).resolve().parent
# Modules that are shared code rather than pipeline stages.
NOT_STAGES = {"__init__", "cli", "config", "synonyms", "tracker", "community_resolution_bands"}

PROJECT_GITIGNORE = """\
# Scientographer outputs are versioned by DVC, not git.
/reports/
.env
.hamilton/
.venv/
# pixi environments (the same lines `pixi init` writes, so they merge with its file;
# a bare `.pixi/` would hide config.toml from the exception below)
.pixi/*
!.pixi/config.toml
"""


def _write_gitignore(gitignore: Path) -> None:
    """Create the project's .gitignore, or add the lines it lacks to an existing one
    (``pixi init`` and ``git init`` tooling often write one first)."""
    if not gitignore.exists():
        gitignore.write_text(PROJECT_GITIGNORE, encoding="utf-8")
        return
    text = gitignore.read_text(encoding="utf-8")
    present = {line.strip() for line in text.splitlines()}
    missing = [
        line for line in PROJECT_GITIGNORE.splitlines()
        if line and not line.startswith("#") and line not in present
    ]
    if missing:
        separator = "" if text.endswith("\n") or not text else "\n"
        gitignore.write_text(
            text + separator + "\n# Scientographer\n" + "\n".join(missing) + "\n", encoding="utf-8"
        )


def _stage_modules() -> list[str]:
    return sorted(
        p.stem
        for p in PACKAGE_DIR.glob("*.py")
        if p.stem not in NOT_STAGES and not p.stem.startswith("_")
    )


def _dvc_stage_commands() -> set[str]:
    """Module names run by ./dvc.yaml's stages (`scientographer run <module>`)."""
    dvc_yaml = Path("dvc.yaml")
    if not dvc_yaml.exists():
        return set()
    import yaml

    with open(dvc_yaml, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    modules = set()
    for stage in (doc.get("stages") or {}).values():
        parts = str(stage.get("cmd", "")).split()
        if "run" in parts and parts.index("run") + 1 < len(parts):
            modules.add(parts[parts.index("run") + 1])
    return modules


EXAMPLE: Final[str] = "motor_learning_open_access"


def _copy_files(directory: Path, targets: dict, force: bool) -> None:
    """Copy packaged files into the project, keeping existing ones unless `force`."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, source in targets.items():
        target = directory / name
        if target.exists() and not force:
            typer.echo(f"kept existing {target} (use --force to overwrite)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(source) as src:
            shutil.copyfile(src, target)
        typer.echo(f"wrote {target}")
    (directory / "data").mkdir(exist_ok=True)
    _write_gitignore(directory / ".gitignore")


def _cd(directory: Path) -> str:
    return "" if directory == Path(".") else f"cd {directory} && "


# Size thresholds for a first try on a few thousand papers (the defaults suit 10,000+).
SMALL_CORPUS_THRESHOLDS: Final[dict[str, int]] = {
    "substantive_min_size": 15, "min_community_size": 15, "min_named_group_size": 15}


def _set_value(text: str, section: str, key: str, value) -> str:
    """Replace `key: ...` inside one top-level section of a params.yaml text,
    keeping its comments (the value is written as JSON, which YAML reads)."""
    import json
    import re

    start = re.search(rf"^{re.escape(section)}:\s*$", text, flags=re.M)
    if not start:
        raise ValueError(f"params.yaml has no section {section!r}")
    following = re.search(r"^\S", text[start.end():], flags=re.M)
    end = start.end() + following.start() if following else len(text)
    body, count = re.subn(rf"^(\s+{re.escape(key)}:)[^\n#]*", lambda m: f"{m.group(1)} {json.dumps(value)}",
                          text[start.end():end], count=1, flags=re.M)
    if not count:
        raise ValueError(f"params.yaml section {section!r} has no key {key!r}")
    return text[:start.end()] + body + text[end:]


def _insert_stages(directory: Path, template: str, first_stage: str) -> None:
    """Put a packaged template's stages at the front of the project's dvc.yaml (once)."""
    stages = (resources.files("scientographer") / "templates" / template).read_text(encoding="utf-8")
    stages = "\n".join(line for line in stages.splitlines() if not line.startswith("#")).strip("\n")
    dvc_yaml = directory / "dvc.yaml"
    text = dvc_yaml.read_text(encoding="utf-8")
    if f"\n  {first_stage}:" not in text:
        dvc_yaml.write_text(text.replace("\nstages:\n", "\nstages:\n" + stages + "\n\n", 1), encoding="utf-8")


def _set_params(directory: Path, values: list[tuple[str, str, object]]) -> None:
    params_yaml = directory / "params.yaml"
    text = params_yaml.read_text(encoding="utf-8")
    for section, key, value in values:
        text = _set_value(text, section, key, value)
    params_yaml.write_text(text, encoding="utf-8")


def _pubmed_project(directory: Path, query: str, email: str, max_records: int) -> None:
    """Turn a project into one whose corpus is a PubMed search: the search and ingest
    stages in front of fetch_references, the query in params.yaml."""
    _insert_stages(directory, "dvc_pubmed.yaml", "search_pubmed")
    values = [("pubmed_search", "query", query), ("pubmed_search", "max_records", max_records)]
    if email:
        values.append(("pubmed_search", "email", email))
    sections = {"substantive_min_size": "communities", "min_community_size": "community_keywords",
                "min_named_group_size": "website"}
    values += [(sections[key], key, value) for key, value in SMALL_CORPUS_THRESHOLDS.items()]
    _set_params(directory, values)


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="project directory (created if missing)"),
    force: bool = typer.Option(False, "--force", help="overwrite an existing params.yaml / dvc.yaml"),
    pubmed: Optional[str] = typer.Option(None, "--pubmed", metavar="QUERY",
                                         help="build the corpus from this PubMed search"),
    own_references: bool = typer.Option(False, "--own-references",
                                        help="your corpus comes with its references table: do not fetch citations"),
    email: str = typer.Option("", "--email", help="contact address sent to OpenAlex (and PubMed)"),
    max_records: int = typer.Option(20000, "--max-records", help="stop if the search matches more (with --pubmed)"),
) -> None:
    """Create a project: params.yaml (every setting, documented), dvc.yaml (the
    pipeline), data/ for your papers table, and a .gitignore. The citations between
    the papers are fetched from OpenAlex unless --own-references. With --pubmed, the
    corpus is a PubMed search instead: no table to prepare."""
    if pubmed is not None and own_references:
        typer.echo("--own-references does not apply to --pubmed (PubMed records carry no references)", err=True)
        raise typer.Exit(code=2)
    # An existing project's dvc.yaml and params.yaml are the user's: only a new one
    # (or --force) gets the source stages and their settings.
    fresh = force or not (directory / "dvc.yaml").exists()
    package = resources.files("scientographer")
    _copy_files(directory, {
        "params.yaml": package / "params.yaml",
        "dvc.yaml": package / "templates" / "dvc.yaml",
    }, force)
    if not fresh:
        typer.echo("kept the existing project as it is; use --force to start it again")
        return
    if not own_references:
        _insert_stages(directory, "dvc_fetch_references.yaml", "fetch_references")
        if email:
            _set_params(directory, [("fetch_references", "email", email)])
    if pubmed is not None:
        _pubmed_project(directory, pubmed, email, max_records)
        typer.echo(
            "\nA PubMed project: `dvc repro` searches PubMed, reads the records, fetches the citations\n"
            "between them from OpenAlex, then runs the pipeline. Size thresholds are set for a few\n"
            "thousand papers (15); raise them in params.yaml for a larger search.\n"
            "Next:\n"
            f"  {_cd(directory)}git init && dvc init && dvc repro\n"
            "  scientographer website\n"
            "(in a pixi project, prefix commands with `pixi run`, or start a `pixi shell`)"
        )
        return
    references = (
        f"     and your references at {directory / 'data/references.parquet'} (citing_doi, cited_dois)\n"
        if own_references else
        "     (the citations between them are fetched from OpenAlex by DOI)\n")
    typer.echo(
        "\nNext:\n"
        f"  1. put your papers table at {directory / 'data/papers.parquet'} (a `doi` column + metadata)\n"
        + references +
        "  2. adjust params.yaml (size thresholds scale with the corpus)\n"
        f"  3. {_cd(directory)}git init && dvc init && dvc repro\n"
        "  4. scientographer website\n"
        "(in a pixi project, prefix commands with `pixi run`, or start a `pixi shell`)"
    )


@app.command()
def example(
    directory: Path = typer.Argument(Path("scientographer-example"), help="project directory (created if missing)"),
    force: bool = typer.Option(False, "--force", help="overwrite existing files"),
) -> None:
    """Create the example project: 3,925 open-access papers on motor learning (CC BY,
    Europe PMC) and the citations between them (OpenAlex, CC0), ready to run."""
    package = resources.files("scientographer")
    source = package / "examples" / EXAMPLE
    _copy_files(directory, {
        "params.yaml": source / "params.yaml",
        "dvc.yaml": package / "templates" / "dvc.yaml",
        **{f"data/{name}": source / "data" / name
           for name in ("papers.parquet", "references.parquet", "ATTRIBUTION.md")},
    }, force)
    typer.echo(
        "\nNext (a few minutes; data/ATTRIBUTION.md credits the sources):\n"
        f"  {_cd(directory)}git init && dvc init && dvc repro\n"
        "  scientographer website\n"
        "(in a pixi project, prefix commands with `pixi run`, or start a `pixi shell`)"
    )


@app.command()
def stages() -> None:
    """List the stages; * marks those run by ./dvc.yaml."""
    declared = _dvc_stage_commands()
    for name in _stage_modules():
        typer.echo(f"{'*' if name in declared else ' '} {name}")


@app.command()
def run(stage: str = typer.Argument(..., help="stage name, e.g. detect_communities")) -> None:
    """Run one stage in the current project (reads/writes the paths in params.yaml)."""
    if stage not in _stage_modules():
        typer.echo(f"unknown stage '{stage}'. Known: {', '.join(_stage_modules())}", err=True)
        raise typer.Exit(code=2)
    # Stages build their Hamilton driver from `__main__`, so run them as a script would.
    runpy.run_module(f"scientographer.{stage}", run_name="__main__", alter_sys=True)


@app.command()
def pipeline(
    stage: Optional[str] = typer.Argument(None, help="reproduce up to this dvc.yaml stage only"),
    force: bool = typer.Option(False, "--force", help="re-run even when nothing changed"),
) -> None:
    """`dvc repro`: run every stage whose inputs or params changed, in order."""
    cmd = ["dvc", "repro"]
    if force:
        cmd.append("--force")
    if stage:
        cmd.append(stage)
    raise typer.Exit(code=subprocess.call(cmd))


@app.command()
def website(port: int = typer.Option(8123, help="port to serve on")) -> None:
    """Serve the site built by build_website (params.yaml `website.output_dir`)."""
    from scientographer.config import params

    directory = Path(params("website")["output_dir"])
    if not (directory / "index.html").exists():
        typer.echo(f"no site at {directory}; run `scientographer run build_website` first", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"serving {directory} at http://localhost:{port}")
    raise typer.Exit(
        code=subprocess.call(
            [sys.executable, "-m", "http.server", str(port), "--directory", str(directory)]
        )
    )


def _wait_for(url: str, timeout_seconds: float) -> bool:
    import time
    import urllib.request

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


@app.command()
def ui(port: int = typer.Option(8241, help="port for the Hamilton UI")) -> None:
    """Start the Hamilton UI for this project (its own .hamilton/db), create the
    project and user tracked runs report to, and keep serving until Ctrl-C."""
    import os

    from scientographer.config import PARAMS

    tracker = PARAMS.get("tracker", {})
    username = os.getenv("HAMILTON_UI_USERNAME", tracker.get("username", "scientographer"))
    wanted_id = int(tracker.get("project_id", 1))
    base_dir = Path(".hamilton", "db")
    base_dir.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        ["hamilton", "ui", "--base-dir", str(base_dir), "--port", str(port), "--no-open"]
    )
    try:
        if not _wait_for(f"http://localhost:{port}/api/v0/health", timeout_seconds=120):
            typer.echo("the Hamilton UI did not start within 2 minutes", err=True)
            raise typer.Exit(code=1)
        result = subprocess.run(
            [sys.executable, "-m", "scientographer.tracker", str(base_dir), username, "scientographer"],
            capture_output=True, text=True, check=True,
        )
        project_id = int(result.stdout.strip().splitlines()[-1])
        typer.echo(f"Hamilton UI on http://localhost:{port}; runs are tracked as '{username}' in project {project_id}")
        if project_id != wanted_id:
            typer.echo(f"note: set tracker.project_id: {project_id} in params.yaml (it says {wanted_id})")
        if not tracker.get("enabled", False):
            typer.echo("note: set tracker.enabled: true in params.yaml for runs to be recorded")
        if f":{port}" not in str(tracker.get("url", "http://localhost:8241")):
            typer.echo(f"note: set tracker.url: http://localhost:{port} in params.yaml")
        raise typer.Exit(code=server.wait())
    finally:
        if server.poll() is None:
            server.terminate()


@app.command(name="store-existing-results")
def store_existing_results(
    stage: Optional[list[str]] = typer.Option(None, "--stage", help="only this stage (repeatable)"),
) -> None:
    """Fill the per-resolution result stores from the project's current outputs.

    Stages reuse stored results, so adding a resolution to the sweep computes only
    that resolution. Outputs made before the stores existed are not in them; run
    this once (with `dvc status` up to date) instead of recomputing everything."""
    from scientographer._seed_stores import STAGES, seed_stores

    unknown = set(stage or ()) - set(STAGES)
    if unknown:
        typer.echo(f"unknown stage(s) {sorted(unknown)}. Known: {', '.join(STAGES)}", err=True)
        raise typer.Exit(code=2)
    for name, stored in seed_stores(tuple(stage) if stage else STAGES).items():
        typer.echo(f"{name}: stored {stored} resolution(s)")


@app.command(name="params")
def params_command() -> None:
    """Show which params.yaml is in effect and its sections."""
    from scientographer.config import PARAMS, PARAMS_PATH

    typer.echo(f"{PARAMS_PATH}")
    for section in PARAMS:
        typer.echo(f"  {section}: {len(PARAMS[section])} keys")


if __name__ == "__main__":
    app()
