# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import os
from pathlib import Path
import subprocess
import sys

from typer.testing import CliRunner
import yaml

from scientographer.cli import _stage_modules, app
from scientographer.synonyms import load_synonym_groups

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def _run_python(code: str, cwd: Path, env_extra: dict | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": str(PACKAGE_DIR.parent), **(env_extra or {})}
    env.pop("SCIENTOGRAPHER_PARAMS", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


# ── config ────────────────────────────────────────────────────────────────────
def test_params_lookup_order(tmp_path):
    code = "from scientographer.config import PARAMS_PATH; print(PARAMS_PATH)"
    # No local file, no env var -> packaged defaults.
    assert Path(_run_python(code, tmp_path)) == PACKAGE_DIR / "params.yaml"
    # A params.yaml in the working directory wins over the packaged defaults.
    (tmp_path / "params.yaml").write_text("website: {}\n")
    assert _run_python(code, tmp_path) == "params.yaml"
    # The environment variable wins over both.
    other = tmp_path / "other.yaml"
    other.write_text("website: {}\n")
    assert _run_python(code, tmp_path, {"SCIENTOGRAPHER_PARAMS": str(other)}) == str(other)


def test_importing_the_package_creates_nothing_on_disk(tmp_path):
    _run_python(
        "import scientographer.config, scientographer.community_quality_metrics, "
        "scientographer.community_connectivity_modifier, scientographer.build_website",
        tmp_path,
    )
    assert list(tmp_path.iterdir()) == []


def test_packaged_defaults_have_every_section_the_stages_read():
    params = yaml.safe_load((PACKAGE_DIR / "params.yaml").read_text())
    for section in ["citation_network", "resolutions", "detect_communities", "layout", "graph",
                    "communities", "community_keywords", "wordclouds", "website", "tracker"]:
        assert section in params, section
    assert params["tracker"]["enabled"] is False


# ── synonyms ──────────────────────────────────────────────────────────────────
def test_synonyms_file_is_optional_and_extras_merge(tmp_path):
    assert load_synonym_groups(None) == {}
    assert load_synonym_groups(tmp_path / "missing.json", {"A": ["a1"]}) == {"A": ["a1"]}
    path = tmp_path / "syn.json"
    path.write_text(json.dumps({"Purkinje Cell": ["purkinje cells"]}))
    groups = load_synonym_groups(path, {"Purkinje Cell": ["Purkinje Cell ( PC )"], "B": ["b"]})
    assert groups == {"Purkinje Cell": ["purkinje cells", "Purkinje Cell ( PC )"], "B": ["b"]}


# ── cli ───────────────────────────────────────────────────────────────────────
def test_stage_list_excludes_shared_modules():
    stages = _stage_modules()
    assert "detect_communities" in stages and "build_website" in stages
    assert not {"config", "cli", "synonyms", "tracker", "community_resolution_bands"} & set(stages)


def test_init_writes_a_project_whose_dvc_stages_all_exist(tmp_path):
    result = CliRunner().invoke(app, ["init", str(tmp_path / "proj")])
    assert result.exit_code == 0, result.output
    proj = tmp_path / "proj"
    assert (proj / "params.yaml").exists() and (proj / "data").is_dir() and (proj / ".gitignore").exists()
    dvc = yaml.safe_load((proj / "dvc.yaml").read_text())
    for stage in dvc["stages"].values():
        module = stage["cmd"].split()[-1]
        assert module in _stage_modules(), module
    # A second init keeps the user's edits unless forced.
    (proj / "params.yaml").write_text("edited: true\n")
    CliRunner().invoke(app, ["init", str(proj)])
    assert (proj / "params.yaml").read_text() == "edited: true\n"


def test_init_adds_its_ignores_to_an_existing_gitignore(tmp_path):
    # `pixi init` writes a .gitignore before `scientographer init` runs.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text("# pixi environments\n.pixi/*\n!.pixi/config.toml\n.env")
    result = CliRunner().invoke(app, ["init", str(proj)])
    assert result.exit_code == 0, result.output
    lines = (proj / ".gitignore").read_text().splitlines()
    assert lines[:4] == ["# pixi environments", ".pixi/*", "!.pixi/config.toml", ".env"]
    for wanted in ["/reports/", ".hamilton/", ".venv/", ".env", ".pixi/*", "!.pixi/config.toml"]:
        assert lines.count(wanted) == 1, wanted
    # A bare `.pixi/` would make git ignore config.toml despite pixi's exception.
    assert ".pixi/" not in lines
    # Running init again adds nothing.
    before = (proj / ".gitignore").read_text()
    CliRunner().invoke(app, ["init", str(proj)])
    assert (proj / ".gitignore").read_text() == before
