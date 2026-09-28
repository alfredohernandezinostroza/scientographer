"""``mln`` -- the command line for the citation-network pipeline.

Every stage is a Hamilton DAG module under ``motor_learning_network/``; the
pipeline as a whole is declared in ``dvc.yaml`` with its knobs in
``motor_learning_network/params.yaml``. This CLI is a thin front for those, so
a pip-installed user without pixi gets the same commands as ``pixi run <task>``::

    mln stages                       list the DAG modules (and which are in dvc.yaml)
    mln run detect_communities       run one stage, exactly as `python .../detect_communities.py`
    mln pipeline [stage]             `dvc repro` (the whole pipeline, or up to one stage)
    mln website [--port 8123]        serve the built site
    mln ui [--port 8241]             start the Hamilton UI tracker for this checkout
    mln params                       show which params.yaml is in effect
"""

from pathlib import Path
import runpy
import subprocess
import sys
from typing import Optional

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

PACKAGE_DIR = Path(__file__).resolve().parent
# Modules that are not pipeline stages (shared code, scaffolds, this file).
NOT_STAGES = {"__init__", "cli", "constants", "dag_template", "community_resolution_bands"}


def _stage_modules() -> list[str]:
    return sorted(
        p.stem
        for p in PACKAGE_DIR.glob("*.py")
        if p.stem not in NOT_STAGES and not p.stem.startswith("_")
    )


def _dvc_stage_names() -> set[str]:
    dvc_yaml = Path("dvc.yaml")
    if not dvc_yaml.exists():
        return set()
    import yaml

    with open(dvc_yaml, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return set((doc.get("stages") or {}).keys())


@app.command()
def stages() -> None:
    """List the pipeline's DAG modules; * marks those declared as dvc.yaml stages."""
    declared = _dvc_stage_names()
    for name in _stage_modules():
        typer.echo(f"{'*' if name in declared else ' '} {name}")


@app.command()
def run(stage: str = typer.Argument(..., help="module name, e.g. detect_communities")) -> None:
    """Run one stage in this working directory (reads/writes the paths in params.yaml)."""
    if stage not in _stage_modules():
        typer.echo(f"unknown stage '{stage}'. Known: {', '.join(_stage_modules())}", err=True)
        raise typer.Exit(code=2)
    # Modules build their driver from `__main__`, so run them as a script would.
    runpy.run_module(f"motor_learning_network.{stage}", run_name="__main__", alter_sys=True)


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
    """Serve the site built by build_website.py (params.yaml `website.output_dir`)."""
    from motor_learning_network.constants import params

    directory = Path(params("website")["output_dir"])
    if not (directory / "index.html").exists():
        typer.echo(f"no site at {directory}; run `mln run build_website` first", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"serving {directory} at http://localhost:{port}")
    raise typer.Exit(
        code=subprocess.call(
            [sys.executable, "-m", "http.server", str(port), "--directory", str(directory)]
        )
    )


@app.command()
def ui(port: int = typer.Option(8241, help="port for the Hamilton UI")) -> None:
    """Start the Hamilton UI tracker for THIS checkout (its own .hamilton/db)."""
    base_dir = Path(".hamilton", "db")
    base_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(
        f"Hamilton UI on http://localhost:{port} (base dir {base_dir}); "
        "set tracker.enabled: true in params.yaml for runs to register"
    )
    raise typer.Exit(
        code=subprocess.call(
            ["hamilton", "ui", "--base-dir", str(base_dir), "--port", str(port), "--no-open"]
        )
    )


@app.command(name="params")
def params_command() -> None:
    """Show which params.yaml is in effect and its sections."""
    from motor_learning_network.constants import PARAMS, PARAMS_PATH

    typer.echo(f"{PARAMS_PATH}")
    for section in PARAMS:
        typer.echo(f"  {section}: {len(PARAMS[section])} keys")


if __name__ == "__main__":
    app()
