# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
import subprocess
import sys

import pytest


def test_provision_creates_project_and_user_idempotently(tmp_path):
    pytest.importorskip("hamilton_ui", reason="needs the `ui` extra")
    base_dir = tmp_path / ".hamilton" / "db"
    cmd = [sys.executable, "-m", "scientographer.tracker", str(base_dir), "someone", "scientographer"]
    first = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    second = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    assert first == second == "1"
    assert (base_dir / "db.sqlite3").exists()
