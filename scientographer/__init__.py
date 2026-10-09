# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scientographer: map a scientific literature from its citation network.

From two tables -- the papers and their references -- to Leiden/CPM communities at
every resolution of a sweep, their quality and well-connectedness, keyword labels,
word clouds, and an interactive map. Every stage is an Apache Hamilton DAG; DVC
wires the stages together and one params.yaml holds every setting.

Start a project with ``scientographer init``; see the README for the full walk-through.
"""

# The one place the version is written (pyproject.toml reads it). Between releases
# main carries the next version with a .dev0 suffix; see RELEASING.md.
__version__ = "0.1.0"
