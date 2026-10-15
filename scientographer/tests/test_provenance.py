# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import igraph as ig
import pytest
from typer.testing import CliRunner

from scientographer import __version__, _provenance
from scientographer._provenance import (
    PROVENANCE_ATTRIBUTE,
    exact_version,
    file_md5,
    history,
    history_from_text,
    step_record,
    version_for,
    with_step,
    write_graphml_with_step,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), "-c", "user.name=Test", "-c", "user.email=test@example.org",
         "-c", "commit.gpgsign=false", *arguments],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repository(root: Path, project_name: str) -> Path:
    """A git repository with a pyproject naming `project_name` and a package folder
    `scientographer/` at its top; returns the package folder."""
    package = root / "scientographer"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "9.9.9"\n')
    (root / "pyproject.toml").write_text(f'[project]\nname = "{project_name}"\ndynamic = ["version"]\n')
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return package


def _is_pep440(version: str) -> bool:
    packaging_version = pytest.importorskip("packaging.version")
    try:
        packaging_version.Version(version)
    except packaging_version.InvalidVersion:
        return False
    return True


# ── The exact version ─────────────────────────────────────────────────────────
@needs_git
def test_a_checkout_of_scientographer_adds_the_commit_and_dirty(tmp_path):
    package = _repository(tmp_path / "scientographer-repo", "scientographer")
    commit = _git(package.parent, "rev-parse", "--short=7", "HEAD")
    clean = version_for(package, "0.2.0.dev0")
    assert clean == f"0.2.0.dev0+g{commit}"
    assert _is_pep440(clean)
    (package / "__init__.py").write_text('__version__ = "9.9.10"\n')  # a tracked file changed
    dirty = version_for(package, "0.2.0.dev0")
    assert dirty == f"0.2.0.dev0+g{commit}.dirty"
    assert _is_pep440(dirty)


@needs_git
def test_an_untracked_file_does_not_make_a_checkout_dirty(tmp_path):
    package = _repository(tmp_path / "repo", "scientographer")
    (package / "notes.txt").write_text("scratch")
    assert not version_for(package, "0.2.0").endswith(".dirty")


@needs_git
def test_a_worktree_of_scientographer_counts_as_a_checkout(tmp_path):
    package = _repository(tmp_path / "repo", "scientographer")
    worktree = tmp_path / "worktree"
    _git(package.parent, "worktree", "add", "-q", "-b", "other", str(worktree))
    assert (worktree / ".git").is_file()
    commit = _git(worktree, "rev-parse", "--short=7", "HEAD")
    assert version_for(worktree / "scientographer", "0.2.0") == f"0.2.0+g{commit}"


@needs_git
def test_another_projects_repository_never_lends_its_commit(tmp_path):
    # A pyproject that names another project.
    package = _repository(tmp_path / "user-project", "my-literature-map")
    assert version_for(package, "0.2.0") == "0.2.0"
    # Scientographer installed in an environment inside a user's repository.
    project = tmp_path / "study"
    _repository(project, "scientographer")  # even one that is named scientographer
    site_packages = project / ".pixi" / "envs" / "default" / "lib" / "site-packages" / "scientographer"
    site_packages.mkdir(parents=True)
    assert version_for(site_packages, "0.2.0") == "0.2.0"


def test_outside_git_or_without_git_the_release_version(tmp_path, monkeypatch):
    package = tmp_path / "scientographer"
    package.mkdir()
    assert version_for(package, "0.2.0") == "0.2.0"
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries-here"))
    assert version_for(package, "0.2.0") == "0.2.0"


def test_a_failing_or_slow_git_gives_the_release_version(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=_provenance.GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(_provenance.subprocess, "run", timeout)
    assert version_for(tmp_path, "0.2.0") == "0.2.0"


def test_exact_version_is_a_valid_version_of_this_release():
    version = exact_version()
    assert version == __version__ or version.startswith(__version__ + "+g")
    assert _is_pep440(version)


def test_cli_prints_the_version():
    from scientographer.cli import app

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"scientographer {exact_version()}"


# ── Step records ──────────────────────────────────────────────────────────────
def test_step_record_has_settings_inputs_with_md5_and_date(tmp_path):
    table = tmp_path / "papers.csv"
    table.write_bytes(b"doi\n10.1/a\n")
    record = step_record(
        "build_citation_network",
        {"papers_table": tmp_path / "papers.csv", "resolutions": (0.1, 0.2), "keep": True},
        [table, None, tmp_path / "missing.csv"],
    )
    assert record["stage"] == "build_citation_network"
    assert record["scientographer"] == exact_version()
    assert record["settings"] == {"papers_table": (tmp_path / "papers.csv").as_posix(),
                                  "resolutions": [0.1, 0.2], "keep": True}
    assert record["inputs"] == [
        {"path": table.as_posix(), "md5": hashlib.md5(b"doi\n10.1/a\n").hexdigest()},
        {"path": (tmp_path / "missing.csv").as_posix(), "md5": None},
    ]
    assert len(record["date"]) == len("2026-10-15T09:30:00Z") and record["date"].endswith("Z")
    json.dumps(record)  # plain JSON throughout


def test_a_directory_md5_follows_its_files(tmp_path):
    folder = tmp_path / "store"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / "sub" / "b.txt").write_text("b")
    before = file_md5(folder)
    assert before is not None and before == file_md5(folder)
    (folder / "sub" / "b.txt").write_text("changed")
    assert file_md5(folder) != before


# ── A graph's history ─────────────────────────────────────────────────────────
def test_history_tolerates_absent_and_invalid_attributes():
    graph = ig.Graph(2)
    assert history(graph) == []
    graph[PROVENANCE_ATTRIBUTE] = "not json"
    assert history(graph) == []
    graph[PROVENANCE_ATTRIBUTE] = '{"stage": "a dict, not a list"}'
    assert history(graph) == []
    assert history_from_text('[{"stage": "a"}, 3, "x"]') == [{"stage": "a"}]
    assert history_from_text(None) == []


def test_history_survives_graphml_and_each_stage_adds_its_step(tmp_path):
    graph = ig.Graph(3, [(0, 1), (1, 2)], directed=True)
    graph["leiden_seed"] = 42
    first = tmp_path / "first.graphml"
    write_graphml_with_step(graph, first, {"stage": "build_citation_network"})
    assert PROVENANCE_ATTRIBUTE not in graph.attributes()  # the graph in memory is unchanged

    read = ig.Graph.Read_GraphML(str(first))
    assert history(read) == [{"stage": "build_citation_network"}]
    second = tmp_path / "second.graphml"
    write_graphml_with_step(read, second, {"stage": "detect_communities"})
    assert history(read) == [{"stage": "build_citation_network"}]  # restored after writing

    again = ig.Graph.Read_GraphML(str(second))
    assert [s["stage"] for s in history(again)] == ["build_citation_network", "detect_communities"]
    assert again["leiden_seed"] == 42  # other graph attributes untouched
    assert [s["stage"] for s in history(with_step(again, {"stage": "layout_graph"}))][-1] == "layout_graph"


def test_detect_communities_saver_keeps_the_history_and_adds_its_step(tmp_path):
    from scientographer.detect_communities import save_citation_network_with_communities

    source = tmp_path / "in.graphml"
    graph = ig.Graph(2, [(0, 1)], directed=True)
    write_graphml_with_step(graph, source, {"stage": "build_citation_network"})
    loaded = ig.Graph.Read_GraphML(str(source))
    out = tmp_path / "out.graphml"
    save_citation_network_with_communities(loaded, out, source)
    steps = history(ig.Graph.Read_GraphML(str(out)))
    assert [s["stage"] for s in steps] == ["build_citation_network", "detect_communities"]
    assert steps[-1]["inputs"] == [{"path": source.as_posix(), "md5": file_md5(source)}]
    assert "seed" in steps[-1]["settings"]


def test_the_site_ignores_the_history_when_reading_metrics(tmp_path):
    from scientographer.build_website import _parse_graphml, _partition_records_from_graph_attributes

    graph = ig.Graph(2, [(0, 1)], directed=True)
    graph["modularity_at_res=0.01"] = 0.5
    path = tmp_path / "g.graphml"
    write_graphml_with_step(graph, path, {"stage": "s", "settings": {"x_at_res=1": 2}})
    _, _, graph_attributes, _ = _parse_graphml(path)
    assert history_from_text(graph_attributes[PROVENANCE_ATTRIBUTE])[0]["stage"] == "s"
    assert _partition_records_from_graph_attributes(graph_attributes) == [
        {"resolution": 0.01, "modularity": 0.5}]


# ── The website's provenance.json ─────────────────────────────────────────────
def _website_provenance(tmp_path, graph_attributes: dict) -> dict:
    from scientographer.build_website import provenance_records, save_provenance_json, website_step

    graphml = tmp_path / "site.graphml"
    graphml.write_text("<graphml/>")
    step = website_step(graphml, [], *([None] * 8))
    saved = save_provenance_json(provenance_records(graph_attributes, step), tmp_path / "website")
    written = json.loads((tmp_path / "website" / "network_data" / "provenance.json").read_text())
    assert saved["n_steps"] == len(written["steps"])
    return written


def test_website_writes_provenance_with_the_graphs_steps_and_its_own(tmp_path):
    earlier = [{"stage": "build_citation_network", "scientographer": "0.1.0"},
               {"stage": "detect_communities", "scientographer": "0.2.0"}]
    written = _website_provenance(tmp_path, {PROVENANCE_ATTRIBUTE: json.dumps(earlier)})
    assert written["scientographer"] == exact_version()
    assert written["built"].endswith("Z")
    assert written["steps"][:2] == earlier
    website = written["steps"][2]
    assert website["stage"] == "build_website"
    assert website["inputs"] == [{"path": (tmp_path / "site.graphml").as_posix(),
                                  "md5": hashlib.md5(b"<graphml/>").hexdigest()}]


def test_website_provenance_for_a_graph_without_history(tmp_path):
    written = _website_provenance(tmp_path, {"modularity_at_res=0.01": "0.5"})
    assert [s["stage"] for s in written["steps"]] == ["build_website"]


def test_the_page_shows_the_stamp_from_provenance_json():
    from scientographer.build_website import ASSETS_DIR

    page = (ASSETS_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="provenance-stamp" hidden' in page and 'id="loading-version" hidden' in page
    script = (ASSETS_DIR / "main.js").read_text(encoding="utf-8")
    assert "/provenance.json`" in script
