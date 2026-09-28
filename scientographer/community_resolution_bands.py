"""Single source of truth for the Leiden/CPM resolution sweep and the graph it lives on.

A partition is stored on the graph as one ``cpm_communities_at_res=<resolution>``
vertex column per resolution (``detect_communities`` writes them). Every analysis
stage imports ``RESOLUTIONS`` from here, so they can never silently disagree on
which resolutions they sweep.

The sweep is declared in three bands (params.yaml ``resolutions.low/mid/high``)
because partitions are sometimes produced in separate runs and written to separate
graphml files with identical topology (same vertices, same edges), each carrying
only its own band's columns. ``graph.low_band_graphml`` is the base every stage
loads; ``merge_higher_band_communities`` grafts the other bands' columns onto it by
vertex ``name``, giving one graph that carries every resolution. A graph written by
``detect_communities`` already carries all bands, and the merge is then a no-op --
point all three band paths at the same file.
"""

from pathlib import Path
from typing import Final

import igraph as ig

from scientographer.config import params

_resolutions = params("resolutions")
_graph = params("graph")

# The three resolution bands (params.yaml `resolutions`), exactly matching the stored
# `cpm_communities_at_res=<r>` column names (Python's float repr of each value is what
# the column is keyed on: 0.01 -> "0.01", 0.1 -> "0.1", 0.2 -> "0.2", etc.).
LOW_BAND: Final[list[float]] = [float(r) for r in _resolutions["low"]]     # 0.001 .. 0.009
MID_BAND: Final[list[float]] = [float(r) for r in _resolutions["mid"]]     # 0.01  .. 0.19
HIGH_BAND: Final[list[float]] = [float(r) for r in _resolutions["high"]]   # 0.2   .. 0.9

# The full sweep, ascending. Every analysis DAG imports this so they stay in lockstep.
RESOLUTIONS: Final[list[float]] = LOW_BAND + MID_BAND + HIGH_BAND

# The resolution single-resolution analyses and the website default to.
CANONICAL_RESOLUTION: Final[float] = float(_resolutions["canonical"])

# The low-res graph is the base every DAG already loads; the mid/high graphs supply the
# additional community columns that get grafted onto it by merge_higher_band_communities
# (params.yaml `graph`).
LOW_RES_GRAPHML: Final[Path] = Path(_graph["low_band_graphml"])
_HIGHER_BAND_GRAPHML: Final[dict[Path, list[float]]] = {
    Path(_graph["higher_band_graphml"]["mid"]): MID_BAND,
    Path(_graph["higher_band_graphml"]["high"]): HIGH_BAND,
}


def community_attribute_name(resolution: float) -> str:
    """The vertex-attribute / column name a resolution's community assignment is stored under."""
    return f"cpm_communities_at_res={resolution}"


def merge_higher_band_communities(base_graph: ig.Graph) -> ig.Graph:
    """Graft the mid- and high-band ``cpm_communities_at_res=*`` columns onto ``base_graph``.

    ``base_graph`` is the low-res graph (already carrying the LOW_BAND columns). For each
    higher-band graph, this reads its per-vertex community assignment for every resolution
    in that band and copies it onto ``base_graph`` in ``base_graph``'s own vertex order,
    matched by the ``name`` attribute -- the three graphs share an identical vertex set, so
    the reindex is exact. Mutates and returns ``base_graph`` so that afterwards it carries
    all of ``RESOLUTIONS``.
    """
    base_names = base_graph.vs["name"]
    for graphml_path, band_resolutions in _HIGHER_BAND_GRAPHML.items():
        # A graph written by detect_communities.py already carries every band, so
        # there is nothing to graft (and the band path may just be the same file).
        if all(community_attribute_name(r) in base_graph.vs.attributes() for r in band_resolutions):
            continue
        other = ig.Graph.Read_GraphML(str(graphml_path))
        other_index_by_name = {name: index for index, name in enumerate(other.vs["name"])}
        for resolution in band_resolutions:
            attribute_name = community_attribute_name(resolution)
            other_values = other.vs[attribute_name]
            base_graph.vs[attribute_name] = [
                other_values[other_index_by_name[name]] for name in base_names
            ]
    return base_graph
