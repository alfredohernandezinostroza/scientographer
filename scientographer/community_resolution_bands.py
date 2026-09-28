"""Single source of truth for the Leiden/CPM resolution sweep and the graph it lives on.

``get_network_communities_and_stats.py`` ran Leiden with the constant Potts model
(CPM) across a *three-band* resolution sweep and stored the resulting community
assignments as per-vertex ``cpm_communities_at_res=<resolution>`` columns. The three
bands were written to three separate graphml files that share identical topology
(same 22,982 nodes / 183,926 edges, same vertex names) and differ only in which
``cpm_communities_at_res=*`` columns they carry:

    citation_network_full_low_res.graphml   0.001 .. 0.009   (LOW_BAND)
    citation_network_full.graphml           0.01  .. 0.19    (MID_BAND)
    citation_network_full_high_res.graphml  0.2   .. 0.9     (HIGH_BAND)

The downstream analysis DAGs (community_quality_metrics, community_connectivity_metrics,
community_connectivity_modifier, community_quality_metrics_after_connectivity_modifier)
used to hardcode the low band and read only the low-res graph, so their outputs -- and
the website plots built from them -- stopped at 0.009. This module consolidates the
resolution list into one place (so the DAGs can never silently disagree on which
resolutions they sweep) and provides ``merge_higher_band_communities`` to graft the
mid/high community columns onto the low-res graph, giving every DAG one graph carrying
all ``RESOLUTIONS`` at once. Because topology is identical across bands, the minimum-cut
/ modularity computations only ever need this single merged topology.

Note: the frozen Track-A ``get_network_communities_and_stats.py`` keeps its own
``resolutions`` list (its low-band detection run) and is intentionally NOT wired to this
module -- it is frozen. This module is the shared source for the *analysis* DAGs only.
"""

from pathlib import Path
from typing import Final

import igraph as ig

from motor_learning_network.constants import params

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
