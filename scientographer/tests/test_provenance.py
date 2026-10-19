# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import igraph as ig
import pandas as pd
import pytest
from typer.testing import CliRunner

from scientographer import __version__, _provenance
from scientographer._provenance import (
    PARQUET_METADATA_KEY,
    PROVENANCE_ATTRIBUTE,
    exact_version,
    file_md5,
    folder_history,
    graphml_history,
    history,
    history_from_text,
    input_history,
    merge_steps,
    parquet_history,
    step_record,
    steps_for,
    version_for,
    write_folder_record,
    write_graphml_with_steps,
    write_parquet_with_steps,
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
def test_step_record_has_settings_inputs_outputs_and_date(tmp_path):
    table = tmp_path / "papers.csv"
    table.write_bytes(b"doi\n10.1/a\n")
    record = step_record(
        "build_citation_network",
        {"papers_table": tmp_path / "papers.csv", "resolutions": (0.1, 0.2), "keep": True},
        [table, None, tmp_path / "missing.csv", table],
        [tmp_path / "graph.graphml", None],
    )
    assert record["stage"] == "build_citation_network"
    assert record["scientographer"] == exact_version()
    assert record["settings"] == {"papers_table": (tmp_path / "papers.csv").as_posix(),
                                  "resolutions": [0.1, 0.2], "keep": True}
    assert record["inputs"] == [
        {"path": table.as_posix(), "md5": hashlib.md5(b"doi\n10.1/a\n").hexdigest()},
        {"path": (tmp_path / "missing.csv").as_posix(), "md5": None},
    ]
    assert record["outputs"] == [(tmp_path / "graph.graphml").as_posix()]
    assert len(record["date"]) == len("2026-10-15T09:30:00Z") and record["date"].endswith("Z")
    json.dumps(record)  # plain JSON throughout


def test_a_directory_input_counts_its_files_and_skips_the_md5_when_large(tmp_path, monkeypatch):
    folder = tmp_path / "store"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / "sub" / "b.txt").write_text("bb")
    before = file_md5(folder)
    assert before is not None and before == file_md5(folder)
    entry = step_record("s", {}, [folder])["inputs"][0]
    assert entry == {"path": folder.as_posix(), "md5": before, "files": 2, "bytes": 3}
    (folder / "sub" / "b.txt").write_text("changed")
    assert file_md5(folder) != before
    monkeypatch.setattr(_provenance, "DIRECTORY_HASH_LIMIT_BYTES", 5)
    assert step_record("s", {}, [folder])["inputs"][0] == {
        "path": folder.as_posix(), "md5": None, "files": 2, "bytes": 8}


# ── Where the records are kept, and reading them back ─────────────────────────
def test_history_tolerates_absent_and_invalid_records(tmp_path):
    graph = ig.Graph(2)
    assert history(graph) == []
    graph[PROVENANCE_ATTRIBUTE] = "not json"
    assert history(graph) == []
    assert history_from_text('{"stage": "a dict without steps"}') == []
    assert history_from_text('[{"stage": "a"}, 3, "x"]') == [{"stage": "a"}]
    assert history_from_text('{"steps": [{"stage": "a"}]}') == [{"stage": "a"}]
    assert history_from_text(None) == []
    # Files made before records were kept, or not readable as such.
    (tmp_path / "old.parquet").write_bytes(b"not a parquet file")
    (tmp_path / "old.graphml").write_text("<graphml>broken")
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "plain.parquet")
    for name in ("old.parquet", "old.graphml", "plain.parquet", "missing.parquet"):
        assert input_history(tmp_path / name) == []
    assert input_history(None) == [] and folder_history(tmp_path) == []


def test_graphml_keeps_the_steps_and_the_graph_in_memory_is_unchanged(tmp_path):
    graph = ig.Graph(3, [(0, 1), (1, 2)], directed=True)
    graph.vs["name"] = ["a", "b", "c"]
    graph["leiden_seed"] = 42
    path = tmp_path / "g.graphml"
    write_graphml_with_steps(graph, path, [{"stage": "build_citation_network"}])
    assert PROVENANCE_ATTRIBUTE not in graph.attributes()
    assert graphml_history(path) == [{"stage": "build_citation_network"}]  # read without loading the graph
    read = ig.Graph.Read_GraphML(str(path))
    assert history(read) == [{"stage": "build_citation_network"}]
    assert read["leiden_seed"] == 42
    write_graphml_with_steps(read, tmp_path / "h.graphml", history(read) + [{"stage": "detect_communities"}])
    assert history(read) == [{"stage": "build_citation_network"}]  # restored after writing
    assert [s["stage"] for s in input_history(tmp_path / "h.graphml")] == ["build_citation_network",
                                                                           "detect_communities"]


def test_parquet_keeps_the_steps_in_its_metadata(tmp_path):
    table = pd.DataFrame({"doi": ["10.1/a", "10.1/b"], "year": [2001, 2002]}, index=[5, 6])
    steps = [{"stage": "ingest", "date": "2026-10-16T08:00:00Z"}]
    write_parquet_with_steps(table, tmp_path / "t.parquet", steps, index=False)
    assert parquet_history(tmp_path / "t.parquet") == steps
    assert input_history(tmp_path / "t.parquet") == steps
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "t.parquet"), table.reset_index(drop=True))
    write_parquet_with_steps(table, tmp_path / "i.parquet", steps)  # the index kept, as to_parquet does
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "i.parquet"), table)
    import pyarrow.parquet as pq

    assert PARQUET_METADATA_KEY.encode() in pq.read_schema(tmp_path / "t.parquet").metadata


def test_a_folder_record_describes_the_folder_and_its_csv_files(tmp_path):
    folder = tmp_path / "layouts"
    steps = [{"stage": "embedding_maps"}]
    write_folder_record(folder, steps)
    (folder / "gemini.csv").write_text("doi,x,y\n")
    assert json.loads((folder / "provenance.json").read_text()) == {"steps": steps}
    assert input_history(folder) == steps
    assert input_history(folder / "gemini.csv") == steps  # a CSV has no metadata: its folder's record
    write_folder_record(folder, [{"stage": "embedding_maps", "date": "later"}])  # a re-run replaces it
    assert folder_history(folder) == [{"stage": "embedding_maps", "date": "later"}]


def test_merge_keeps_each_step_once_in_order_of_date():
    ingest = {"stage": "ingest", "scientographer": "0.2.0", "date": "2026-10-16T08:00:00Z"}
    fetch = {"stage": "fetch_references", "scientographer": "0.2.0", "date": "2026-10-16T08:05:00Z"}
    rerun = {**ingest, "date": "2026-10-16T09:00:00Z"}
    merged = merge_steps([ingest, fetch], [ingest], [rerun, {"stage": "undated"}], ["junk"])
    assert merged == [{"stage": "undated"}, ingest, fetch, rerun]


# ── Every kind of stage writes its record ─────────────────────────────────────
def _ingest_and_fetch(tmp_path):
    """ingest writes data/papers.parquet (metadata) + data/ingest/provenance.json;
    fetch_references reads the papers table and writes data/references.parquet."""
    from scientographer import fetch_references, ingest

    export = tmp_path / "exports" / "pubmed.txt"
    export.parent.mkdir()
    export.write_text("PMID- 1\n")
    papers = pd.DataFrame({"doi": ["10.1/a", "10.1/b"], "title": ["A", "B"]})
    read = {"table": papers, "files": [{"format": "medline", "file": str(export), "records": 2}]}
    ingest_steps = ingest.provenance_steps([{"format": "medline", "path": str(export.parent)}], read,
                                           tmp_path / "papers.parquet", tmp_path / "ingest")
    ingest.save_papers_table(papers, tmp_path / "papers.parquet", ingest_steps)
    ingest.save_ingest_report({"papers": 2}, read, tmp_path / "ingest", ingest_steps)

    references = pd.DataFrame({"citing_doi": ["10.1/a"], "cited_dois": [["10.1/b"]]})
    works = pd.DataFrame({"found": [True, True], "referenced_works": [["W2"], []]})
    fetch_steps = fetch_references.provenance_steps(tmp_path / "papers.parquet", tmp_path / "references.parquet",
                                                    tmp_path / "store")
    fetch_references.save_references_table(references, works, tmp_path / "references.parquet", tmp_path / "store",
                                           fetch_steps)
    return ingest_steps, fetch_steps


def test_folder_and_table_stages_write_their_records(tmp_path):
    ingest_steps, fetch_steps = _ingest_and_fetch(tmp_path)
    assert [s["stage"] for s in ingest_steps] == ["ingest"]
    assert ingest_steps[0]["inputs"][0]["path"] == (tmp_path / "exports").as_posix()
    assert ingest_steps[0]["outputs"] == [(tmp_path / "papers.parquet").as_posix(), (tmp_path / "ingest").as_posix()]
    assert parquet_history(tmp_path / "papers.parquet") == ingest_steps
    assert folder_history(tmp_path / "ingest") == ingest_steps
    # fetch_references carries ingest's step (read from the table's metadata) before its own.
    assert [s["stage"] for s in fetch_steps] == ["ingest", "fetch_references"]
    assert parquet_history(tmp_path / "references.parquet") == fetch_steps
    assert folder_history(tmp_path / "store") == fetch_steps


def test_the_graph_starts_with_its_tables_lineage(tmp_path):
    from scientographer.build_citation_network import provenance_steps, save_citation_network

    ingest_steps, fetch_steps = _ingest_and_fetch(tmp_path)
    steps = provenance_steps(tmp_path / "papers.parquet", tmp_path / "references.parquet", tmp_path / "g.graphml")
    # ingest's step reached through both tables counts once.
    assert [s["stage"] for s in steps] == ["ingest", "fetch_references", "build_citation_network"]
    assert steps[:2] == fetch_steps
    assert "citation_network" in steps[-1]["settings"]
    save_citation_network(ig.Graph(2, [(0, 1)], directed=True), tmp_path / "g.graphml", steps)
    assert graphml_history(tmp_path / "g.graphml") == steps


def test_a_graph_stage_keeps_the_history_and_adds_its_step(tmp_path):
    from scientographer.detect_communities import (
        provenance_steps,
        save_citation_network_with_communities,
        save_partition_summary,
    )

    source = tmp_path / "in.graphml"
    write_graphml_with_steps(ig.Graph(2, [(0, 1)], directed=True), source, [{"stage": "build_citation_network"}])
    steps = provenance_steps(source, tmp_path / "out.graphml", tmp_path / "summary.parquet")
    save_citation_network_with_communities(ig.Graph.Read_GraphML(str(source)), tmp_path / "out.graphml", steps)
    save_partition_summary(pd.DataFrame({"resolution": [0.1]}), tmp_path / "summary.parquet", steps)
    assert [s["stage"] for s in graphml_history(tmp_path / "out.graphml")] == ["build_citation_network",
                                                                               "detect_communities"]
    assert parquet_history(tmp_path / "summary.parquet") == steps
    assert steps[-1]["inputs"] == [{"path": source.as_posix(), "md5": file_md5(source)}]
    assert set(steps[-1]["settings"]) == {"detect_communities", "resolutions"}


def test_an_old_graph_without_history_gives_only_the_new_step(tmp_path):
    old = tmp_path / "old.graphml"
    ig.Graph(2, [(0, 1)]).write_graphml(str(old))
    assert [s["stage"] for s in steps_for("layout_graph", {}, [old], [tmp_path / "new.graphml"])] == ["layout_graph"]


def test_the_site_ignores_the_history_when_reading_metrics(tmp_path):
    from scientographer.build_website import _parse_graphml, _partition_records_from_graph_attributes

    graph = ig.Graph(2, [(0, 1)], directed=True)
    graph["modularity_at_res=0.01"] = 0.5
    path = tmp_path / "g.graphml"
    write_graphml_with_steps(graph, path, [{"stage": "s", "settings": {"x_at_res=1": 2}}])
    _, _, graph_attributes, _ = _parse_graphml(path)
    assert history_from_text(graph_attributes[PROVENANCE_ATTRIBUTE])[0]["stage"] == "s"
    assert _partition_records_from_graph_attributes(graph_attributes) == [
        {"resolution": 0.01, "modularity": 0.5}]


# ── The website gathers every input's records ─────────────────────────────────
def _step(stage, date, version="0.2.0", inputs=(), outputs=()):
    return {"stage": stage, "scientographer": version, "date": date, "settings": {},
            "inputs": [{"path": p, "md5": "0" * 32} for p in inputs], "outputs": list(outputs)}


def _website(tmp_path, graph_steps, other_inputs):
    """Builds provenance.json for a site whose graph carries `graph_steps` and which
    also reads `other_inputs` (paths); returns the written file."""
    from scientographer.build_website import (
        _parse_graphml,
        provenance_records,
        save_provenance_json,
        website_inputs,
        website_step,
    )

    graphml = tmp_path / "data" / "graph.graphml"
    graphml.parent.mkdir(parents=True, exist_ok=True)
    graph = ig.Graph(2, [(0, 1)], directed=True)
    if graph_steps:
        write_graphml_with_steps(graph, graphml, graph_steps)
    else:
        graph.write_graphml(str(graphml))
    _, _, graph_attributes, _ = _parse_graphml(graphml)
    paths = [graphml, *other_inputs, None, None, None, None, None, None, None, None, None]
    inputs = website_inputs(paths[0], [], *paths[1:10])
    step = website_step(inputs, tmp_path / "website")
    saved = save_provenance_json(provenance_records(graph_attributes, graphml, inputs, step), tmp_path / "website")
    written = json.loads((tmp_path / "website" / "network_data" / "provenance.json").read_text())
    assert saved["n_steps"] == len(written["steps"])
    return written


def test_website_merges_dedupes_orders_and_links_the_steps(tmp_path):
    build = _step("build_citation_network", "2026-10-16T08:00:00Z", inputs=["data/papers.parquet"],
                  outputs=["data/graph0.graphml"])
    detect = _step("detect_communities", "2026-10-16T08:10:00Z", inputs=["data/graph0.graphml"],
                   outputs=["data/graph1.graphml"])
    table = tmp_path / "data" / "analysis" / "community_quality_metrics" / "per_partition.parquet"
    metrics = _step("community_quality_metrics", "2026-10-16T08:20:00Z", version="0.2.0+gabc1234.dirty",
                    inputs=["./data/graph1.graphml"], outputs=[table.parent.as_posix()])
    keywords = _step("community_keywords", "2026-10-16T08:30:00Z", inputs=["data/graph1.graphml"],
                     outputs=[(tmp_path / "data" / "graph.graphml").as_posix()])
    table.parent.mkdir(parents=True)
    write_parquet_with_steps(pd.DataFrame({"resolution": [0.1]}), table, [build, detect, metrics])
    written = _website(tmp_path, [build, detect, keywords], [table])

    stages = [s["stage"] for s in written["steps"]]
    assert stages == ["build_citation_network", "detect_communities", "community_quality_metrics",
                      "community_keywords", "build_website"]  # each once, in order of date
    assert written["scientographer"] == exact_version() and written["built"].endswith("Z")
    assert written["versions"][:2] == ["0.2.0", "0.2.0+gabc1234.dirty"]
    assert written["unrecorded_inputs"] == []
    steps = written["steps"]
    assert steps[1]["inputs"][0]["from_step"] == 0          # detect reads build's graph
    assert steps[2]["inputs"][0]["from_step"] == 1          # "./" paths match too
    assert "from_step" not in steps[0]["inputs"][0]         # the papers table: no recorded step wrote it
    website = steps[-1]
    assert [i["path"] for i in website["inputs"]] == [(tmp_path / "data" / "graph.graphml").as_posix(),
                                                      table.as_posix()]
    assert website["inputs"][1]["from_step"] == 2           # a file inside the metrics stage's folder
    assert website["outputs"] == [(tmp_path / "website").as_posix()]


def test_website_works_on_old_inputs_without_records(tmp_path):
    old_table = tmp_path / "metrics.parquet"
    pd.DataFrame({"resolution": [0.1]}).to_parquet(old_table)
    written = _website(tmp_path, [], [old_table])
    assert [s["stage"] for s in written["steps"]] == ["build_website"]
    assert written["unrecorded_inputs"] == [(tmp_path / "data" / "graph.graphml").as_posix(), old_table.as_posix()]
    assert written["versions"] == [exact_version()]


def test_website_records_the_full_environment_and_its_warnings(tmp_path, monkeypatch):
    from scientographer._environment import installed_distributions

    monkeypatch.chdir(tmp_path)  # a project without a lock file
    old = _step("build_citation_network", "2026-10-16T08:00:00Z", inputs=[], outputs=["data/graph.graphml"])
    assert "environment" not in old  # a step recorded before environments were kept
    written = _website(tmp_path, [old], [])
    environment = written["environment"]
    assert set(environment) == {"python", "platform", "packages", "lock"}
    assert environment["packages"] == installed_distributions()
    assert list(environment["packages"]) == sorted(environment["packages"])
    assert "numpy" in environment["packages"] and "igraph" in environment["packages"]
    assert environment["lock"] == {"kind": None}
    # The site's own step has its environment; the old step still reads.
    assert "environment" in written["steps"][-1] and "environment" not in written["steps"][0]
    assert written["environment_warnings"] == [
        {"kind": "no_lock", "text": "No lock file: the environment is recorded but was not pinned."}]


def test_the_page_has_the_stamp_and_the_about_panel():
    from scientographer.build_website import ASSETS_DIR

    page = (ASSETS_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="provenance-stamp"' in page and 'id="loading-version" hidden' in page
    # About is a panel that opens out of the badge, not a tab.
    assert 'id="about-panel"' in page and 'aria-controls="about-panel"' in page
    assert 'id="tab-about"' not in page and 'id="view-about"' not in page
    assert '<button id="scientographer-badge"' in page
    # The panel's wordmark is the link to Scientographer on GitHub.
    assert '<a id="about-logo" href="https://github.com/alfredohernandezinostroza/scientographer"' in page
    script = (ASSETS_DIR / "main.js").read_text(encoding="utf-8")
    assert "/provenance.json`" in script
    for field in ("from_step", "unrecorded_inputs", "versions", "outputs", "environment_warnings",
                  "environment"):
        assert field in script
