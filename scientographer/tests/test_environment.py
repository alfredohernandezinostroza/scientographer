# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The environment in every step record, and the check against the project's lock."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from scientographer import _environment, cli
from scientographer._environment import (
    OVERRIDE_VARIABLE,
    EnvironmentMismatch,
    check_locked_environment,
    compare,
    lock_status,
    normalise_name,
    parse_pixi_lock,
    parse_requirements,
    parse_uv_lock,
    pixi_platform,
    require_locked_environment,
)
from scientographer._provenance import step_record

PLATFORM = pixi_platform()

# A pixi.lock in the real format (version 6/7): the environments list each platform's
# packages by URL; the `packages` section describes them. Version 7 conda entries
# carry no name or version (they come from the file name) and `purls` names the
# Python distribution, if any.
PIXI_LOCK = f"""\
version: 6
environments:
  default:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    indexes:
    - https://pypi.org/simple
    packages:
      {PLATFORM}:
      - conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-3.12.4-h194c7f8_0_cpython.conda
      - conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/graphviz-14.1.2-h8b86629_0.conda
      - conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-igraph-0.11.8-py312h1a2b3c4_0.conda
      - conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/numpy-2.1.0-py312h58c1407_0.conda
      - pypi: https://files.pythonhosted.org/packages/aa/bb/leidenalg-0.10.2-cp38-abi3-manylinux_2_28_x86_64.whl
      - pypi: https://files.pythonhosted.org/packages/cc/dd/Scikit_Learn-1.5.0-cp312-cp312-manylinux.whl
      - pypi: https://files.pythonhosted.org/packages/ee/ff/tqdm-4.66.0-py3-none-any.whl
      - pypi: ../scientographer
      - pypi: git+https://github.com/example/somepackage.git#abc123
  other:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    packages:
      {PLATFORM}:
      - conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-3.11.9-h194c7f8_0_cpython.conda
packages:
- conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-3.12.4-h194c7f8_0_cpython.conda
  sha256: 0000
  md5: 0000
  license: Python-2.0
  purls: []
- conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-3.11.9-h194c7f8_0_cpython.conda
  purls: []
- conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/graphviz-14.1.2-h8b86629_0.conda
  depends:
  - cairo >=1.18
  license: EPL-1.0
  purls: []
- conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/python-igraph-0.11.8-py312h1a2b3c4_0.conda
  purls:
  - pkg:pypi/igraph?source=hash-mapping
- conda: https://conda.anaconda.org/conda-forge/{PLATFORM}/numpy-2.1.0-py312h58c1407_0.conda
  name: numpy
  version: 2.1.0
- pypi: https://files.pythonhosted.org/packages/aa/bb/leidenalg-0.10.2-cp38-abi3-manylinux_2_28_x86_64.whl
  name: leidenalg
  version: 0.10.2
  requires_dist:
  - igraph>=0.10.0,<0.12
- pypi: https://files.pythonhosted.org/packages/cc/dd/Scikit_Learn-1.5.0-cp312-cp312-manylinux.whl
  name: Scikit_Learn
  version: 1.5.0
- pypi: https://files.pythonhosted.org/packages/ee/ff/tqdm-4.66.0-py3-none-any.whl
  name: tqdm
  version: 4.66.0
- pypi: ../scientographer
  name: scientographer
  requires_dist:
  - leidenalg>=0.10
- pypi: git+https://github.com/example/somepackage.git#abc123
  name: somepackage
  version: 1.0.0
"""

UV_LOCK = """\
version = 1
requires-python = ">=3.10"

[[package]]
name = "leidenalg"
version = "0.10.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "Scikit_Learn"
version = "1.5.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "my-project"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "scientographer"
version = "0.2.0.dev0"
source = { path = "../scientographer" }
"""

# What is "installed" in these tests: a key package can differ from the lock, an
# ordinary one too, and scientographer is the path dependency.
INSTALLED = {"igraph": "0.11.8", "leidenalg": "0.10.2", "numpy": "2.1.0", "scikit-learn": "1.5.0",
             "tqdm": "4.66.0", "graphviz": "0.20.3", "scientographer": "0.2.0.dev0"}


@pytest.fixture
def installed(monkeypatch):
    """Pretend these distributions and Python 3.12.4 are installed."""
    packages = dict(INSTALLED)
    monkeypatch.setattr(_environment, "installed_distributions", lambda: packages)
    monkeypatch.setattr(_environment, "python_version", lambda: "3.12.4")
    monkeypatch.delenv(OVERRIDE_VARIABLE, raising=False)
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    return packages


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── Reading the locks ─────────────────────────────────────────────────────────
def test_pixi_lock_gives_the_environments_platform_packages_and_python():
    lock = parse_pixi_lock(PIXI_LOCK, "default", PLATFORM)
    assert lock["python"] == "3.12.4"
    assert lock["packages"] == {
        "igraph": "0.11.8",         # conda python-igraph, through its purl
        "numpy": "2.1.0",           # name and version fields
        "leidenalg": "0.10.2",
        "scikit-learn": "1.5.0",    # normalised name
        "tqdm": "4.66.0",
    }
    # The Graphviz program (no purl), the path and the git dependency are not checked.
    assert "graphviz" not in lock["packages"] and "scientographer" not in lock["packages"]
    assert "somepackage" not in lock["packages"]
    assert parse_pixi_lock(PIXI_LOCK, "other", PLATFORM)["python"] == "3.11.9"
    with pytest.raises(ValueError, match="no environment"):
        parse_pixi_lock(PIXI_LOCK, "missing", PLATFORM)
    with pytest.raises(ValueError, match="no packages for"):
        parse_pixi_lock(PIXI_LOCK, "default", "plan9-64")


def test_conda_names_map_to_distributions_without_purls():
    text = PIXI_LOCK.replace("  purls:\n  - pkg:pypi/igraph?source=hash-mapping\n", "")
    lock = parse_pixi_lock(text, "default", PLATFORM)
    assert lock["packages"]["igraph"] == "0.11.8"  # CONDA_TO_DISTRIBUTION


def test_uv_lock_keeps_registry_packages():
    try:
        import tomllib  # noqa: F401
    except ImportError:
        pytest.importorskip("tomli")
    lock = parse_uv_lock(UV_LOCK)
    assert lock == {"python": None, "packages": {"leidenalg": "0.10.2", "scikit-learn": "1.5.0"}}


def test_requirements_count_as_a_lock_only_when_every_line_is_pinned():
    pinned = """\
# pip freeze
--index-url https://pypi.org/simple
-e git+https://github.com/alfredohernandezinostroza/scientographer.git@abc#egg=scientographer
leidenalg==0.10.2
Scikit_Learn[extra]==1.5.0 ; python_version >= "3.10"
numpy==2.1.0 \\
    --hash=sha256:abc
local-thing @ file:///home/me/local-thing
"""
    assert parse_requirements(pinned) == {
        "python": None, "packages": {"leidenalg": "0.10.2", "scikit-learn": "1.5.0", "numpy": "2.1.0"}}
    assert parse_requirements(pinned + "pandas>=2\n") is None
    assert parse_requirements("scientographer\n") is None
    assert parse_requirements("# nothing\n") is None


def test_names_are_normalised():
    assert normalise_name("Scikit_Learn") == normalise_name("scikit.learn") == "scikit-learn"


# ── The comparison ────────────────────────────────────────────────────────────
def test_a_matching_environment_has_no_differences(installed):
    lock = parse_pixi_lock(PIXI_LOCK, "default", PLATFORM)
    assert compare(lock, installed, "3.12.4") == []


def test_differences_list_python_then_key_packages_then_the_rest(installed):
    lock = parse_pixi_lock(PIXI_LOCK, "default", PLATFORM)
    installed.update(leidenalg="0.11.0", tqdm="4.67.0")
    del installed["numpy"]
    assert compare(lock, installed, "3.10.12") == [
        {"package": "python", "installed": "3.10.12", "locked": "3.12.4"},
        {"package": "leidenalg", "installed": "0.11.0", "locked": "0.10.2"},
        {"package": "numpy", "installed": None, "locked": "2.1.0"},   # a key package, missing
        {"package": "tqdm", "installed": "4.67.0", "locked": "4.66.0"},
    ]
    del installed["tqdm"]  # an ordinary package that is not installed is not a difference
    assert {d["package"] for d in compare(lock, installed, "3.12.4")} == {"leidenalg", "numpy"}


def test_equal_releases_written_differently_match(installed):
    installed["numpy"] = "2.1"
    lock = parse_pixi_lock(PIXI_LOCK, "default", PLATFORM)
    assert compare(lock, installed, "3.12.4") == []


# ── The lock status and the refusal ───────────────────────────────────────────
def test_no_lock_records_kind_null_and_runs(project, installed):
    assert lock_status() == {"kind": None}
    assert check_locked_environment("detect_communities") == {"kind": None}
    (project / "requirements.txt").write_text("pandas>=2\n")
    status = lock_status()
    assert status["kind"] is None and "not every requirement is pinned" in status["note"]


def test_the_pixi_lock_wins_and_is_recorded_with_its_md5(project, installed):
    import hashlib

    (project / "pixi.lock").write_text(PIXI_LOCK)
    (project / "requirements.txt").write_text("leidenalg==9.9.9\n")
    status = lock_status()
    assert status == {"kind": "pixi", "path": "pixi.lock", "md5": hashlib.md5(PIXI_LOCK.encode()).hexdigest(),
                      "matches": True, "differences": [], "override": False,
                      "environment": "default", "platform": PLATFORM}


def test_pixi_environment_name_is_followed(project, installed, monkeypatch):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "other")
    status = lock_status()
    assert status["environment"] == "other"
    assert status["differences"][0] == {"package": "python", "installed": "3.12.4", "locked": "3.11.9"}


def test_a_requirements_lock_is_compared(project, installed):
    (project / "requirements.txt").write_text("leidenalg==0.11.0\nnumpy==2.1.0\n")
    status = lock_status()
    assert status["kind"] == "requirements" and status["matches"] is False
    assert status["differences"] == [{"package": "leidenalg", "installed": "0.10.2", "locked": "0.11.0"}]


def test_a_mismatch_refuses_with_the_differences_and_the_fix(project, installed):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    installed["leidenalg"] = "0.11.0"
    with pytest.raises(EnvironmentMismatch) as refused:
        check_locked_environment("detect_communities")
    message = str(refused.value)
    assert "leidenalg 0.11.0 installed, pixi.lock says 0.10.2" in message
    assert "`pixi run scientographer run detect_communities`" in message
    assert OVERRIDE_VARIABLE in message and "--allow-environment-mismatch" in message


def test_a_stage_entry_point_exits_on_a_mismatch(project, installed, capsys):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    installed["leidenalg"] = "0.11.0"
    with pytest.raises(SystemExit) as exited:
        require_locked_environment("detect_communities")
    assert exited.value.code == 1
    assert "leidenalg 0.11.0 installed" in capsys.readouterr().err


def test_every_stage_checks_the_environment_when_run_as_a_script():
    for stage in cli._stage_modules():
        source = (cli.PACKAGE_DIR / f"{stage}.py").read_text(encoding="utf-8")
        main_block = source.split('if __name__ == "__main__":', 1)[1]
        assert "require_locked_environment(CURRENT_FILE_NAME)" in main_block, stage


def _fake_stage(recorded: list):
    def run_module(name, run_name=None, alter_sys=False):
        recorded.append(step_record(name.rsplit(".", 1)[-1], {}, []))
    return run_module


def test_cli_refuses_a_mismatch_with_exit_status_1(project, installed, monkeypatch):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    installed["leidenalg"] = "0.11.0"
    recorded = []
    monkeypatch.setattr(cli.runpy, "run_module", _fake_stage(recorded))
    result = CliRunner().invoke(cli.app, ["run", "detect_communities"])
    assert result.exit_code == 1 and recorded == []
    output = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "leidenalg 0.11.0 installed, pixi.lock says 0.10.2" in output
    result = CliRunner().invoke(cli.app, ["pipeline"])
    assert result.exit_code == 1


@pytest.mark.parametrize("how", ["variable", "flag"])
def test_the_override_runs_and_records_the_differences(project, installed, monkeypatch, how):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    installed["leidenalg"] = "0.11.0"
    monkeypatch.setattr(_environment, "_OVERRIDE_WARNED", False)
    recorded = []
    monkeypatch.setattr(cli.runpy, "run_module", _fake_stage(recorded))
    arguments = ["run", "detect_communities"]
    if how == "variable":
        monkeypatch.setenv(OVERRIDE_VARIABLE, "1")
    else:
        monkeypatch.delenv(OVERRIDE_VARIABLE, raising=False)
        arguments.append("--allow-environment-mismatch")
    result = CliRunner().invoke(cli.app, arguments)
    if how == "flag":
        monkeypatch.delenv(OVERRIDE_VARIABLE, raising=False)  # the flag set it for the process
    assert result.exit_code == 0, result.output
    lock = recorded[0]["environment"]["lock"]
    assert lock["override"] is True and lock["matches"] is False
    assert lock["differences"] == [{"package": "leidenalg", "installed": "0.11.0", "locked": "0.10.2"}]


def test_a_matching_environment_runs_through_the_cli(project, installed, monkeypatch):
    (project / "pixi.lock").write_text(PIXI_LOCK)
    recorded = []
    monkeypatch.setattr(cli.runpy, "run_module", _fake_stage(recorded))
    result = CliRunner().invoke(cli.app, ["run", "detect_communities"])
    assert result.exit_code == 0, result.output
    assert recorded[0]["environment"]["lock"]["matches"] is True


# ── The records ───────────────────────────────────────────────────────────────
def test_every_step_record_carries_its_environment(project):
    environment = step_record("detect_communities", {}, [])["environment"]
    assert set(environment) == {"python", "platform", "packages", "lock"}
    assert environment["python"].count(".") == 2 and "-" in environment["platform"]
    assert set(environment["packages"]) <= set(_environment.KEY_PACKAGES)
    assert "igraph" in environment["packages"] and "leidenalg" in environment["packages"]
    assert environment["lock"] == {"kind": None}


def _step(stage, python="3.12.4", packages=None, lock=None):
    step = {"stage": stage, "scientographer": "0.2.0", "date": "2026-10-16T08:00:00Z"}
    if packages is not None:
        step["environment"] = {"python": python, "platform": "Linux-x86_64", "packages": packages,
                               "lock": lock or {"kind": "pixi", "path": "pixi.lock", "matches": True,
                                                "differences": [], "override": False}}
    return step


def test_environment_warnings():
    from scientographer.build_website import environment_warnings

    override = {"kind": "pixi", "path": "pixi.lock", "matches": False, "override": True,
                "differences": [{"package": "leidenalg", "installed": "0.11.0", "locked": "0.10.2"}]}
    steps = [
        _step("build_citation_network"),  # recorded before environments were kept
        _step("detect_communities", packages={"leidenalg": "0.10.2", "numpy": "2.1.0"}),
        _step("community_quality_metrics", packages={"leidenalg": "0.11.0", "numpy": "2.1.0"}, lock=override),
        _step("build_website", python="3.12.5", packages={"numpy": "2.1.0"}),
    ]
    warnings = environment_warnings(steps)
    assert warnings == [
        {"kind": "versions", "text": "Steps ran on different Python versions: 3.12.4 in detect_communities "
                                     "and community_quality_metrics; 3.12.5 in build_website."},
        {"kind": "versions", "text": "Steps ran on different leidenalg versions: 0.10.2 in detect_communities; "
                                     "0.11.0 in community_quality_metrics."},
        {"kind": "lock", "text": "community_quality_metrics ran outside the project's pixi.lock (override): "
                                 "leidenalg 0.11.0 installed, pixi.lock says 0.10.2."},
    ]
    assert environment_warnings([_step("a")]) == []  # old records: nothing to say


def test_no_lock_is_a_quiet_note():
    from scientographer.build_website import environment_warnings

    none = {"kind": None}
    assert environment_warnings([_step("a", packages={}, lock=none), _step("b", packages={}, lock=none)]) == [
        {"kind": "no_lock", "text": "No lock file: the environment is recorded but was not pinned."}]
    assert environment_warnings([_step("a", packages={}, lock=none), _step("b", packages={})]) == [
        {"kind": "no_lock", "text": "No lock file when a ran: their environment is recorded but was not pinned."}]
