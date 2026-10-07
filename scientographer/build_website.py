# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the interactive citation-network website from a GraphML plus optional
analysis outputs -- the last stage of the pipeline.

Produces a self-contained sigma.js site (frontend vendored under
``website_assets/``) that renders the citation network as a pannable map: each
dot is a paper, positioned by the graph's own layout (``x``/``y``), colourable
by semantic **topic** (the ``topic`` attribute) or by Leiden/CPM citation
**community** at any resolution the graph carries
(``cpm_communities_at_res=<r>`` attributes), with citation edges, search,
filters, per-group detail panels, a Metrics tab (partition quality vs.
resolution, well-connectedness, per-community health, per-community keywords)
and a Figures tab (word clouds and other pipeline figures).

Inputs are a **manifest of paths**, not imports from sibling DAG modules:
only the GraphML is required; every other input is optional and, when its
file is absent, its panel is simply not emitted (the frontend hides panels
whose JSON is missing). This is what lets the same builder serve a corpus that
ran only the graph stages, and what makes it a clean ``dvc.yaml`` stage.

What comes from where:

- **resolutions** -- read from the GraphML (``cpm_communities_at_res=*``), never
  from a constant, so the dropdown offers exactly what the graph carries.
- **whole-graph metrics vs. resolution** -- the per-partition parquet from
  ``community_quality_metrics.py`` when given; otherwise the graph-level
  ``<metric>_at_res=<r>`` GraphML attributes that DAG also writes.
- **community names** -- the ``top_keywords_at_res=<r>`` vertex attribute
  written by ``community_keywords.py`` when present (the pipeline's
  synonym-aware corrected-IDF labels); otherwise a quick TF-IDF over the
  nodes' own keyword fields, computed here.
- **per-community keyword bars** -- ``community_keyword_scores.parquet`` from
  ``community_keywords.py`` when given; otherwise the same quick TF-IDF.
- **figures** -- any list of files (word-cloud SVGs, PNGs) tagged with the graph
  and resolution they belong to; copied into the site with a ``figures.json``.

Output (default ``reports/website/``)::

    index.html  main.js  styles.css  tour.js        (vendored frontend, copied)
    network_data/
      nodes.json                     per-paper records (+ community id at every resolution)
      clusters.json                  topic legend
      communities_by_resolution.json citation-community legend per resolution (+ quality)
      resolution_metrics.json        whole-graph metrics vs. resolution
      resolution_metrics_after_cm.json, connectivity_metrics.json,
      community_distributions.json   (optional panels)
      community_keywords.json        per-resolution, per-community ranked keywords
      figures.json                   the figure gallery index
      abstracts.json, edges_out.bin, edges_in.bin
    figures/                         copied figure files

Serve with ``python -m http.server 8123 --directory reports/website``.
"""

from collections import Counter, defaultdict
import html
import json
import logging
import math
from pathlib import Path
import re
import shutil
import struct
import sys
from typing import Final, Optional
import xml.etree.ElementTree as ET

from hamilton import driver
from hamilton.function_modifiers import datasaver, unpack_fields
import hamilton.log_setup
import pandas as pd

from scientographer.config import FIGURES_PATH, draw_dag, ensure_dirs, params, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

GRAPHML_NS: Final[str] = "http://graphml.graphdrawing.org/xmlns"
NS = {"g": GRAPHML_NS}

TOPIC_ATTR: Final[str] = "topic"
COMMUNITY_ATTRIBUTE_PREFIX: Final[str] = "cpm_communities_at_res="
LABEL_ATTRIBUTE_PREFIX: Final[str] = "top_keywords_at_res="
OUTLIER: Final[int] = -1

# params.yaml `website`: the input manifest and the display knobs.
_cfg = params("website")
DEFAULT_COMMUNITY_RESOLUTION: Final[float] = float(_cfg["default_resolution"])  # see docs/RESOLUTION_SELECTION.md

# Communities below this size are left uncoloured/unnamed ("No community").
MIN_NAMED_GROUP_SIZE: Final[int] = int(_cfg["min_named_group_size"])

# How many entries to keep per group for the detail panels.
TOP_PAPERS: Final[int] = 5
TOP_AUTHORS: Final[int] = 5
TOP_KEYWORDS: Final[int] = 10
TOP_KEYWORD_BARS: Final[int] = 12   # keywords per community in community_keywords.json

PALETTE: Final[list[str]] = [
    "#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9A6324", "#808000", "#6f6fff", "#a9a9a9",
    "#ff7f50", "#aaffc3", "#ffd8b1", "#ffe119", "#e6beff",
]
OUTLIER_COLOR: Final[str] = "#cccccc"

ASSETS_DIR: Final[Path] = Path(__file__).resolve().parent / "website_assets"
FRONTEND_FILES: Final[tuple[str, ...]] = ("index.html", "main.js", "styles.css", "tour.js")
DATA_SUBDIR: Final[str] = "network_data"
# Extra views of the same papers on other 2-D layouts (e.g. text-embedding maps);
# each becomes `<key>_data/` next to network_data/ and an entry in views.json.
EXTRA_LAYOUTS: Final[list[dict]] = list(_cfg.get("extra_layouts") or [])
BASE_VIEW_LABEL: Final[str] = str(_cfg.get("view_label") or "Citation network")
VIEW_KEY_PATTERN: Final[re.Pattern] = re.compile(r"^[a-z][a-z0-9_]*$")
FIGURES_SUBDIR: Final[str] = "figures"
WORDCLOUDS_DIR: Final[Path] = Path(_cfg["wordclouds_dir"])


def _optional_path(key: str) -> Optional[Path]:
    value = _cfg.get(key)
    return Path(value) if value else None


# The input manifest from params.yaml `website`. Everything but the graphml is
# optional: point `graphml` at any graphml with a layout to build a site for it.
DEFAULT_INPUTS: Final[dict] = dict(
    graphml_path=Path(_cfg["graphml"]),
    topic_metrics_path=_optional_path("topic_metrics"),
    per_community_metrics_path=_optional_path("per_community_metrics"),
    per_partition_metrics_path=_optional_path("per_partition_metrics"),
    after_cm_partition_metrics_path=_optional_path("after_cm_partition_metrics"),
    connectivity_diagnostic_partition_path=_optional_path("connectivity_diagnostic_partition"),
    connectivity_modifier_partition_path=_optional_path("connectivity_modifier_partition"),
    community_keyword_scores_path=_optional_path("community_keyword_scores"),
    default_resolution=DEFAULT_COMMUNITY_RESOLUTION,
    website_dir=Path(_cfg["output_dir"]),
    site_title=str(_cfg.get("title") or "Citation map"),
    extra_layouts=EXTRA_LAYOUTS,
    base_view_label=BASE_VIEW_LABEL,
)


#####################
##  Aux Functions  ##
#####################
def _to_int(value, default=None):
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cluster_color(cid: int) -> str:
    return OUTLIER_COLOR if cid < 0 else PALETTE[cid % len(PALETTE)]


def _parse_graphml(graphml_file: Path):
    """Return (nodes, edges, graph_attributes, node_attribute_names):
    nodes = [(graphml_id, attrs_dict)], edges = [(src_idx, tgt_idx)] over the
    dense node index, graph_attributes = the ``<data>`` children of ``<graph>``
    (igraph writes graph-level attributes there), node_attribute_names = every
    declared ``<key for="node">`` name (so absent-on-some-nodes attributes are
    still known)."""
    tree = ET.parse(graphml_file)
    root = tree.getroot()
    keys = {k.attrib["id"]: k.attrib["attr.name"] for k in root.findall("g:key", NS)}
    node_attribute_names = [
        k.attrib["attr.name"] for k in root.findall("g:key", NS) if k.attrib.get("for") == "node"]

    graph_el = root.find("g:graph", NS)
    graph_attributes = {
        keys.get(d.attrib["key"], d.attrib["key"]): d.text
        for d in graph_el.findall("g:data", NS)
    } if graph_el is not None else {}

    nodes, id_to_idx = [], {}
    for node_el in root.findall("g:graph/g:node", NS):
        nid = node_el.attrib["id"]
        attrs = {keys.get(d.attrib["key"], d.attrib["key"]): d.text
                 for d in node_el.findall("g:data", NS)}
        id_to_idx[nid] = len(nodes)
        nodes.append((nid, attrs))

    edges = []
    for edge_el in root.findall("g:graph/g:edge", NS):
        s = id_to_idx.get(edge_el.attrib["source"])
        t = id_to_idx.get(edge_el.attrib["target"])
        if s is not None and t is not None:
            edges.append((s, t))
    return nodes, edges, graph_attributes, node_attribute_names


def _resolutions_from_attribute_names(names) -> list[float]:
    found = set()
    for name in names:
        if name.startswith(COMMUNITY_ATTRIBUTE_PREFIX):
            try:
                found.add(float(name[len(COMMUNITY_ATTRIBUTE_PREFIX):]))
            except ValueError:
                continue
    return sorted(found)


_AT_RES = re.compile(r"^(?P<metric>.+)_at_res=(?P<resolution>[0-9.eE+-]+)$")


def _coerce_scalar(text):
    if text is None:
        return None
    t = text.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        v = float(t)
    except ValueError:
        return t
    if not math.isfinite(v):
        return None
    return int(v) if v.is_integer() and "." not in t and "e" not in t.lower() else v


def _partition_records_from_graph_attributes(graph_attributes: dict) -> list[dict]:
    """Graph-level ``<metric>_at_res=<r>`` attributes -> one record per
    resolution (same shape as the per-partition parquet's rows)."""
    by_resolution: dict[float, dict] = defaultdict(dict)
    for key, text in graph_attributes.items():
        m = _AT_RES.match(key)
        if not m:
            continue
        try:
            resolution = float(m.group("resolution"))
        except ValueError:
            continue
        by_resolution[resolution][m.group("metric")] = _coerce_scalar(text)
    return [{"resolution": r, **by_resolution[r]} for r in sorted(by_resolution)]


def _build_csr(num_nodes: int, edges: list, direction: str):
    """CSR adjacency for the given direction. Returns (offsets, targets)."""
    buckets = [[] for _ in range(num_nodes)]
    if direction == "out":
        for s, t in edges:
            buckets[s].append(t)
    elif direction == "in":
        for s, t in edges:
            buckets[t].append(s)
    else:
        raise ValueError(direction)
    offsets, targets = [0], []
    for b in buckets:
        targets.extend(b)
        offsets.append(len(targets))
    return offsets, targets


def _write_csr(path: Path, offsets: list, targets: list) -> None:
    """Binary CSR (little-endian uint32): [N][offsets N+1][targets]."""
    n = len(offsets) - 1
    with open(path, "wb") as f:
        f.write(struct.pack("<I", n))
        f.write(struct.pack(f"<{len(offsets)}I", *offsets))
        f.write(struct.pack(f"<{len(targets)}I", *targets))


def _top_lists_by_group(records: list[dict], group_field: str) -> dict[int, dict]:
    """Per-group top papers (by in-degree), top authors (by paper count), and top
    *distinctive* keywords (TF-IDF over groups, so generic terms are down-weighted).
    Used for both the topic and community legends (and as the keyword fallback
    when community_keywords.py has not run)."""
    papers: dict[int, list] = defaultdict(list)
    authors: dict[int, Counter] = defaultdict(Counter)
    kw_in_group: dict[int, Counter] = defaultdict(Counter)
    kw_n_groups: Counter = Counter()
    group_size: Counter = Counter()

    for r in records:
        gid = r.get(group_field)
        if gid is None:
            continue
        group_size[gid] += 1
        papers[gid].append((r.get("indegree", 0) or 0, r.get("title", ""), r.get("year")))
        for a in (r.get("authors") or "").split("|"):
            a = a.strip()
            if a:
                authors[gid][a] += 1
        seen = set()
        for k in (r.get("keywords") or "").split("|"):
            kl = k.strip().lower()
            if kl and kl not in seen:
                seen.add(kl)
                kw_in_group[gid][kl] += 1
    for gid, kws in kw_in_group.items():
        for kl in kws:
            kw_n_groups[kl] += 1
    n_groups = len(group_size) or 1

    out: dict[int, dict] = {}
    for gid, size in group_size.items():
        top_papers = [
            {"title": t, "year": y, "in_degree": int(d)}
            for d, t, y in sorted(papers[gid], key=lambda x: x[0], reverse=True)[:TOP_PAPERS] if t
        ]
        top_authors = [{"name": a, "papers": c} for a, c in authors[gid].most_common(TOP_AUTHORS)]
        scored = []
        for kl, df in kw_in_group[gid].items():
            score = (df / size) * math.log(n_groups / (1 + kw_n_groups[kl]))
            if score > 0:
                scored.append((score, kl))
        scored.sort(reverse=True)
        top_keywords = [{"keyword": kl, "tfidf": round(s, 4)} for s, kl in scored[:TOP_KEYWORDS]]
        out[gid] = {"top_papers": top_papers, "top_authors": top_authors, "top_keywords": top_keywords}
    return out


def _label_from_keywords(top_keywords: list[dict], fallback: str) -> str:
    return ", ".join(k["keyword"] for k in top_keywords[:3]) if top_keywords else fallback


def _resolution_communities(a: dict, resolutions: list[float]) -> dict[str, int]:
    """This paper's community id at every resolution the graph carries."""
    return {str(r): _to_int(a.get(f"{COMMUNITY_ATTRIBUTE_PREFIX}{r}"), OUTLIER) for r in resolutions}


def _read_table(path: Path) -> pd.DataFrame:
    """A Parquet, CSV or TSV table, by file extension."""
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, sep="\t" if path.suffix in (".tsv", ".tab") else ",")


def _view_groupings(topic_label: str) -> list[dict]:
    """The frontend's grouping config for a view: the resolution-indexed citation
    communities, then the layout's own topics/clusters (if any)."""
    return [
        {"key": "community", "label": "Community", "legendLabel": "Communities", "nodeField": "community",
         "colorField": "community_color", "noneLabel": "No community", "citation": True},
        {"key": "cluster", "label": topic_label, "legendLabel": f"{topic_label}s", "file": "clusters.json",
         "nodeField": "cluster", "colorField": "color", "noneLabel": f"No {topic_label.lower()}",
         "semantic": True},
    ]


def _extra_view(
    spec: dict,
    node_records: list[dict],
    edges: list,
    community_quality_lookup: dict,
    community_labels_from_graph: dict,
    resolutions: list[float],
    community_resolution: Optional[float],
) -> dict:
    """One extra view: the papers of `spec["positions"]` (a table with `doi`, `x`,
    `y`, optionally `topic` (integer, -1 = none) and `topic_name`) placed on that
    layout, with the citation communities of every resolution recomputed for
    those papers, the citations among them, and optional time snapshots
    (`spec["snapshots"]`: {"cutoffs": [...], "snapshots": {"<year>": {"<doi>": [x, y]}}}).
    Papers of the layout that are not in the citation graph are left out."""
    key = str(spec["key"])
    if not VIEW_KEY_PATTERN.match(key) or key == DATA_SUBDIR.removesuffix("_data"):
        raise ValueError(f"extra layout key {key!r}: use lower-case letters, digits and _ (not 'network')")
    table = _read_table(Path(spec["positions"]))
    missing = {"doi", "x", "y"} - set(table.columns)
    if missing:
        raise ValueError(f"extra layout {key!r}: {spec['positions']} lacks column(s) {sorted(missing)}")
    table = table.assign(doi=table["doi"].astype(str).str.strip().str.lower()).drop_duplicates("doi")
    by_doi = table.set_index("doi")
    has_topics = "topic" in table.columns

    keep = [i for i, r in enumerate(node_records) if r["doi"].lower() in by_doi.index]
    new_index = {old: new for new, old in enumerate(keep)}
    records = []
    for i in keep:
        row = by_doi.loc[node_records[i]["doi"].lower()]
        topic = _to_int(row["topic"], OUTLIER) if has_topics and pd.notna(row["topic"]) else OUTLIER
        records.append({
            **node_records[i],
            "x": round(float(row["x"]), 3),
            "y": round(float(row["y"]), 3),
            "cluster": topic,
            "color": _cluster_color(topic),
        })
    logger.info("extra layout %s: %d of %d papers of %s are in the citation graph", key, len(records),
                len(table), spec["positions"])

    clusters = clusters_legend(records, {})
    if has_topics and "topic_name" in table.columns:
        names = table.dropna(subset=["topic_name"]).groupby("topic")["topic_name"].agg(lambda v: v.mode().iat[0])
        for cid, name in names.items():
            entry = clusters.get(str(_to_int(cid, OUTLIER)))
            if entry is not None and str(name).strip():
                entry["name"] = str(name).strip()

    communities = communities_legend_by_resolution(
        records, community_quality_lookup, community_labels_from_graph, resolutions)
    # The baked community colours follow this view's legend (a community can be
    # named on the full graph but fall below MIN_NAMED_GROUP_SIZE here).
    named_here = communities.get(str(community_resolution), {})
    for r in records:
        r["community_color"] = (named_here[str(r["community"])]["color"]
                                if str(r["community"]) in named_here else OUTLIER_COLOR)

    view_edges = [(new_index[s], new_index[t]) for s, t in edges if s in new_index and t in new_index]

    snapshots = None
    if spec.get("snapshots"):
        with open(spec["snapshots"], "r", encoding="utf-8") as f:
            raw = json.load(f)
        id_by_doi = {r["doi"].lower(): r["id"] for r in records}
        snapshots = {"cutoffs": list(raw["cutoffs"]), "snapshots": {}}
        for cutoff, coords in raw["snapshots"].items():
            snapshots["snapshots"][str(cutoff)] = {"coords": {
                id_by_doi[doi.strip().lower()]: [round(float(x), 3), round(float(y), 3)]
                for doi, (x, y) in coords.items() if doi.strip().lower() in id_by_doi}}
    return {
        "key": key,
        "label": str(spec.get("label") or key),
        "subtitle": str(spec.get("subtitle") or ""),
        "topic_label": str(spec.get("topic_label") or "Topic"),
        "records": records,
        "edges": view_edges,
        "clusters": clusters,
        "communities": communities,
        "snapshots": snapshots,
    }


def _optional_parquet(path) -> Optional[pd.DataFrame]:
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        logger.info("optional input absent, its panel is skipped: %s", path)
        return None
    return pd.read_parquet(path)


def _partition_records(df: pd.DataFrame) -> list[dict]:
    """A per-partition parquet as a resolution-sorted list of records, NaN/inf →
    null so it survives json.dump/JSON.parse."""
    records = []
    for row in df.sort_values("resolution").itertuples(index=False):
        records.append({
            k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in row._asdict().items()
        })
    return records


_WORDCLOUD_RESOLUTION = re.compile(r"_at_(?P<resolution>[0-9.]+)_")
_WORDCLOUD_GRAPH = re.compile(r"^(?:tdidf|frequency)_(?P<graph>.+?)_wordcloud_at_")


def discover_wordcloud_figures(keywords_level_data_path: Path = WORDCLOUDS_DIR) -> list[dict]:
    """The word-cloud SVGs written by wordclouds.py, as
    figure-manifest entries. The graph and resolution are parsed from the path
    (``<run>/wordclouds/<kind>_[<graph_label>_]wordcloud_at_<resolution>_...svg``)."""
    root = Path(keywords_level_data_path)
    # Either a tree of per-run dirs (<run>/wordclouds/*.svg) or one run dir on its own.
    found = sorted(set(root.glob("*/wordclouds/*.svg")) | set(root.glob("wordclouds/*.svg")))
    entries = []
    for svg in found:
        stem = svg.stem
        res_match = _WORDCLOUD_RESOLUTION.search(stem)
        graph_match = _WORDCLOUD_GRAPH.search(stem)
        kind = "frequency-wordcloud" if stem.startswith("frequency") else "tfidf-wordcloud"
        graph = graph_match.group("graph") if graph_match else None
        resolution = float(res_match.group("resolution")) if res_match else None
        title = ("Keyword frequency" if kind == "frequency-wordcloud" else "Distinguishing keywords (TF-IDF)")
        if graph:
            title += f" · {graph.replace('_', ' ')}"
        if resolution is not None:
            title += f" · resolution {resolution}"
        entries.append({"path": str(svg), "kind": kind, "graph": graph, "resolution": resolution, "title": title})
    return entries


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH)
    inputs = dict(DEFAULT_INPUTS)
    inputs["figure_manifest"] = discover_wordcloud_figures()
    outputs = ["assembled_website"]
    import __main__
    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
        .build()
    )
    dr.validate_execution(outputs, inputs=inputs)
    draw_dag(dr, CURRENT_FILE_NAME, outputs, inputs)
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@unpack_fields("raw_nodes", "edges", "graph_attributes", "node_attribute_names")
def parsed_graphml(graphml_path: Path) -> tuple[list, list, dict, list]:
    raw_nodes, edges, graph_attributes, node_attribute_names = _parse_graphml(graphml_path)
    logger.info("parsed graphml: %d nodes, %d edges, %d graph-level attributes",
                len(raw_nodes), len(edges), len(graph_attributes))
    return raw_nodes, edges, graph_attributes, node_attribute_names


def resolutions(node_attribute_names: list) -> list[float]:
    """Every Leiden/CPM resolution the graph carries a community assignment for."""
    found = _resolutions_from_attribute_names(node_attribute_names)
    logger.info("graph carries %d community resolutions: %s", len(found), found)
    return found


def community_resolution(resolutions: list[float], default_resolution: float) -> Optional[float]:
    """The resolution the map is coloured by on load: the requested default when
    the graph has it, else the lowest one (with a warning), else None."""
    if not resolutions:
        logger.warning("graph has no community assignments; the community grouping will be empty")
        return None
    if default_resolution in resolutions:
        return default_resolution
    logger.warning("default resolution %s not on the graph; using %s", default_resolution, resolutions[0])
    return resolutions[0]


def topic_metrics(topic_metrics_path: Optional[Path]) -> dict:
    """Optional per-topic citation-community metrics from topic_community_analysis.py."""
    if topic_metrics_path is not None and Path(topic_metrics_path).exists():
        with open(topic_metrics_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        logger.info("loaded topic-community metrics for %d topics", len(m))
        return m
    logger.info("no topic-community metrics (topic panels omit them)")
    return {}


def node_records(raw_nodes: list, resolutions: list[float], community_resolution: Optional[float]) -> list[dict]:
    """Per-paper web records: position (graph x/y), semantic topic (`cluster`
    + `color`), citation community at the default resolution (`community` +
    `community_color`, greyed below MIN_NAMED_GROUP_SIZE), the community id at
    every resolution (`communities`), and display metadata."""
    community_attr = f"{COMMUNITY_ATTRIBUTE_PREFIX}{community_resolution}" if community_resolution is not None else None
    community_sizes: Counter = Counter(
        _to_int(a.get(community_attr), OUTLIER) for _, a in raw_nodes) if community_attr else Counter()

    records = []
    for nid, a in raw_nodes:
        topic = _to_int(a.get(TOPIC_ATTR), OUTLIER)
        community = _to_int(a.get(community_attr), OUTLIER) if community_attr else OUTLIER
        named = community >= 0 and community_sizes[community] >= MIN_NAMED_GROUP_SIZE
        records.append({
            "id": nid,
            "title": (a.get("title") or "").strip(),
            "authors": (a.get("authors") or "").strip(),
            "keywords": (a.get("keywords") or "").strip(),
            "year": _to_int(a.get("year")),
            "journal": (a.get("journal") or "").strip(),
            "doi": (a.get("name") or "").strip(),
            "cluster": topic,
            "color": _cluster_color(topic),
            "community": community,
            "community_color": _cluster_color(community) if named else OUTLIER_COLOR,
            "communities": _resolution_communities(a, resolutions),
            "x": round(_to_float(a.get("x"), 0.0), 3),
            "y": round(_to_float(a.get("y"), 0.0), 3),
            "size": round(_to_float(a.get("size"), 1.0), 3),
            "indegree": _to_int(a.get("Eingangsgrad"), 0),
            "degree": _to_int(a.get("Grad"), 0),
        })
    return records


def abstracts(raw_nodes: list) -> dict:
    return {nid: (a.get("abstract") or "").strip()
            for nid, a in raw_nodes if (a.get("abstract") or "").strip()}


def community_labels_from_graph(raw_nodes: list, resolutions: list[float]) -> dict[str, dict[str, str]]:
    """resolution (str) -> community_id (str) -> label, from the
    ``top_keywords_at_res=<r>`` vertex attributes written by
    community_keywords.py. Empty where the graph has no such attribute."""
    labels: dict[str, dict[str, str]] = {}
    for r in resolutions:
        label_attr = f"{LABEL_ATTRIBUTE_PREFIX}{r}"
        community_attr = f"{COMMUNITY_ATTRIBUTE_PREFIX}{r}"
        per_community: dict[str, str] = {}
        for _, a in raw_nodes:
            label = (a.get(label_attr) or "").strip()
            if not label:
                continue
            cid = _to_int(a.get(community_attr), OUTLIER)
            if cid >= 0 and str(cid) not in per_community:
                per_community[str(cid)] = label
        if per_community:
            labels[str(r)] = per_community
    if labels:
        logger.info("community labels from the graph at %d resolutions", len(labels))
    return labels


def clusters_legend(node_records: list[dict], topic_metrics: dict) -> dict:
    """Legend for the semantic `cluster` (topic) grouping."""
    top_lists = _top_lists_by_group(node_records, "cluster")
    agg: dict[int, dict] = defaultdict(lambda: {"size": 0, "sx": 0.0, "sy": 0.0})
    for r in node_records:
        g = agg[r["cluster"]]
        g["size"] += 1
        g["sx"] += r["x"]
        g["sy"] += r["y"]

    clusters = {}
    for cid in sorted(agg):
        if cid < 0:
            continue
        lists = top_lists.get(cid, {})
        kws = lists.get("top_keywords", [])
        a = agg[cid]
        entry = {
            "id": cid,
            "name": _label_from_keywords(kws, f"Topic {cid}"),
            "color": _cluster_color(cid),
            "centroid": [round(a["sx"] / a["size"], 3), round(a["sy"] / a["size"], 3)],
            "size": a["size"],
            "top_papers": lists.get("top_papers", []),
            "top_authors": lists.get("top_authors", []),
            "top_keywords": kws,
        }
        m = topic_metrics.get(str(cid))
        if m is not None:
            entry["community_metrics"] = m
        clusters[str(cid)] = entry
    return clusters


def per_community_quality_df(per_community_metrics_path: Optional[Path]) -> Optional[pd.DataFrame]:
    """Per-(resolution, community) quality metrics from community_quality_metrics.py."""
    return _optional_parquet(per_community_metrics_path)


def per_partition_quality_df(per_partition_metrics_path: Optional[Path]) -> Optional[pd.DataFrame]:
    """Per-resolution partition-level quality metrics from community_quality_metrics.py."""
    return _optional_parquet(per_partition_metrics_path)


def per_partition_quality_after_cm_df(after_cm_partition_metrics_path: Optional[Path]) -> Optional[pd.DataFrame]:
    return _optional_parquet(after_cm_partition_metrics_path)


def connectivity_diagnostic_partition_df(connectivity_diagnostic_partition_path: Optional[Path]) -> Optional[pd.DataFrame]:
    return _optional_parquet(connectivity_diagnostic_partition_path)


def connectivity_modifier_partition_df(connectivity_modifier_partition_path: Optional[Path]) -> Optional[pd.DataFrame]:
    return _optional_parquet(connectivity_modifier_partition_path)


def community_keyword_scores_df(community_keyword_scores_path: Optional[Path]) -> Optional[pd.DataFrame]:
    """Long-form ranked keywords per (resolution, community) from community_keywords.py."""
    return _optional_parquet(community_keyword_scores_path)


def community_quality_lookup(per_community_quality_df: Optional[pd.DataFrame]) -> dict:
    """resolution (str) -> community_id (str) -> quality metrics."""
    if per_community_quality_df is None:
        return {}
    quality_fields = [
        "community_size", "conductance", "conductance_out", "conductance_in",
        "internal_edge_density", "internal_edge_surprise",
        "internal_directed_edge_count", "boundary_edge_count",
    ]
    present = [f for f in quality_fields if f in per_community_quality_df.columns]
    lookup: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in per_community_quality_df.itertuples(index=False):
        lookup[str(row.resolution)][str(int(row.community_id))] = {f: getattr(row, f) for f in present}
    return dict(lookup)


def resolution_metrics_records(
    per_partition_quality_df: Optional[pd.DataFrame], graph_attributes: dict
) -> list[dict]:
    """Whole-graph metrics vs. resolution: the per-partition parquet when given,
    else the graph-level ``<metric>_at_res=<r>`` attributes on the GraphML.
    Empty when neither exists (the Metrics tab stays hidden)."""
    if per_partition_quality_df is not None:
        return _partition_records(per_partition_quality_df)
    records = _partition_records_from_graph_attributes(graph_attributes)
    if records:
        logger.info("resolution metrics taken from %d graph-level attribute sets", len(records))
    return records


def resolution_metrics_after_cm_records(per_partition_quality_after_cm_df: Optional[pd.DataFrame]) -> list[dict]:
    return _partition_records(per_partition_quality_after_cm_df) if per_partition_quality_after_cm_df is not None else []


def connectivity_metrics_records(
    connectivity_diagnostic_partition_df: Optional[pd.DataFrame],
    connectivity_modifier_partition_df: Optional[pd.DataFrame],
) -> list[dict]:
    """The well-connectedness diagnostic merged with the Connectivity Modifier
    before/after summary, one record per resolution."""
    if connectivity_diagnostic_partition_df is None:
        return []
    modifier_by_resolution = (
        {r["resolution"]: r for r in _partition_records(connectivity_modifier_partition_df)}
        if connectivity_modifier_partition_df is not None else {})
    merged = []
    for diagnostic in _partition_records(connectivity_diagnostic_partition_df):
        merged.append({**diagnostic, **modifier_by_resolution.get(diagnostic["resolution"], {})})
    return merged


def community_distribution_records(
    per_community_quality_df: Optional[pd.DataFrame], community_resolution: Optional[float]
) -> dict:
    """Every community's health metrics at every resolution, as parallel numeric
    arrays per resolution (the singleton mass never reaches the legend, so the
    health views need this)."""
    if per_community_quality_df is None:
        return {}

    def _rounded(values, digits: int) -> list:
        return [round(float(v), digits) if math.isfinite(v) else None for v in values]

    by_resolution: dict[str, dict] = {}
    for resolution, group in per_community_quality_df.groupby("resolution"):
        group = group.sort_values("community_size", ascending=False)
        by_resolution[str(resolution)] = {
            "community_id": [int(v) for v in group["community_id"]],
            "community_size": [int(v) for v in group["community_size"]],
            "conductance": _rounded(group["conductance"], 4),
            "internal_edge_density": _rounded(group["internal_edge_density"], 5),
            "internal_edge_surprise": _rounded(group["internal_edge_surprise"], 2),
        }
    return {"default_resolution": str(community_resolution), "by_resolution": by_resolution}


def communities_legend_by_resolution(
    node_records: list[dict],
    community_quality_lookup: dict,
    community_labels_from_graph: dict,
    resolutions: list[float],
) -> dict:
    """Per-resolution citation-community legend: for every resolution the graph
    carries, top papers/authors/keywords + centroid for communities of at least
    MIN_NAMED_GROUP_SIZE, merged with the true full-network quality metrics
    where available. Names prefer the pipeline's labels on the graph."""
    result = {}
    for resolution in resolutions:
        res_key = str(resolution)
        quality_for_res = community_quality_lookup.get(res_key, {})
        labels_for_res = community_labels_from_graph.get(res_key, {})
        recs_at_res = [{**r, "community": r["communities"].get(res_key, OUTLIER)} for r in node_records]
        top_lists = _top_lists_by_group(recs_at_res, "community")

        agg: dict[int, dict] = defaultdict(lambda: {"size": 0, "sx": 0.0, "sy": 0.0})
        for r in recs_at_res:
            g = agg[r["community"]]
            g["size"] += 1
            g["sx"] += r["x"]
            g["sy"] += r["y"]

        communities = {}
        for cid in sorted(agg):
            if cid < 0 or agg[cid]["size"] < MIN_NAMED_GROUP_SIZE:
                continue
            lists = top_lists.get(cid, {})
            kws = lists.get("top_keywords", [])
            a = agg[cid]
            graph_label = labels_for_res.get(str(cid))
            entry = {
                "id": cid,
                "name": graph_label or _label_from_keywords(kws, f"Community {cid}"),
                "name_source": "pipeline" if graph_label else "site",
                "color": _cluster_color(cid),
                "centroid": [round(a["sx"] / a["size"], 3), round(a["sy"] / a["size"], 3)],
                "size": a["size"],
                "top_papers": lists.get("top_papers", []),
                "top_authors": lists.get("top_authors", []),
                "top_keywords": kws,
            }
            quality = quality_for_res.get(str(cid))
            if quality is not None:
                entry["quality"] = quality
                if int(quality["community_size"]) != a["size"]:
                    entry["true_size"] = int(quality["community_size"])
            communities[str(cid)] = entry
        result[res_key] = communities
    return result


def community_keywords_records(
    communities_legend_by_resolution: dict,
    community_keyword_scores_df: Optional[pd.DataFrame],
) -> dict:
    """resolution -> community_id -> ranked [{keyword, score}] for every named
    community. From community_keywords.py's scores when given (one method
    across parquet, graph and site); else the site's own quick TF-IDF."""
    by_resolution: dict[str, dict] = {}
    source = "site"
    scores_by_key: dict[tuple[str, str], list] = {}
    if community_keyword_scores_df is not None and len(community_keyword_scores_df):
        source = "pipeline"
        df = community_keyword_scores_df.sort_values(["resolution", "community_id", "rank"])
        for row in df.itertuples(index=False):
            key = (str(float(row.resolution)), str(int(row.community_id)))
            bucket = scores_by_key.setdefault(key, [])
            if len(bucket) < TOP_KEYWORD_BARS:
                bucket.append({"keyword": row.keyword, "score": round(float(row.corrected_tfidf_score), 4)})
    for res_key, communities in communities_legend_by_resolution.items():
        per_community = {}
        for cid, entry in communities.items():
            ranked = scores_by_key.get((res_key, cid))
            if not ranked:
                ranked = [{"keyword": k["keyword"], "score": k["tfidf"]}
                          for k in entry.get("top_keywords", [])[:TOP_KEYWORD_BARS]]
            if ranked:
                per_community[cid] = ranked
        by_resolution[res_key] = per_community
    return {"source": source, "score_label": "corrected TF-IDF" if source == "pipeline" else "TF-IDF",
            "by_resolution": by_resolution}


def figure_entries(figure_manifest: list, graphml_path: Path) -> list[dict]:
    """Validated figure-manifest entries: existing files only, each with a
    site-relative `file` name (kept unique by prefixing the parent dir when
    stems collide) and the graph/resolution tags the gallery filters on."""
    entries, seen = [], set()
    for item in figure_manifest or []:
        src = Path(item["path"])
        if not src.exists():
            logger.warning("figure missing, skipped: %s", src)
            continue
        name = src.name
        if name in seen:
            name = f"{src.parent.name}__{src.name}"
        seen.add(name)
        entries.append({
            "file": name,
            "source": str(src),
            "kind": item.get("kind", "figure"),
            "graph": item.get("graph"),
            "resolution": item.get("resolution"),
            "title": item.get("title") or src.stem,
        })
    logger.info("%d figures for the gallery", len(entries))
    return entries


def extra_views(
    extra_layouts: list,
    node_records: list[dict],
    edges: list,
    community_quality_lookup: dict,
    community_labels_from_graph: dict,
    resolutions: list[float],
    community_resolution: Optional[float],
) -> list[dict]:
    """Every extra layout of params.yaml `website.extra_layouts`, as a view."""
    return [
        _extra_view(spec, node_records, edges, community_quality_lookup, community_labels_from_graph,
                    resolutions, community_resolution)
        for spec in extra_layouts
    ]


def _data_dir(website_dir: Path) -> Path:
    d = Path(website_dir) / DATA_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def _write_optional_json(path: Path, payload, present: bool) -> Optional[str]:
    """Write the file when its panel has data; otherwise remove any stale copy
    so the frontend sees a clean 404 and hides the panel."""
    if present:
        _write_json(path, payload)
        return str(path)
    if path.exists():
        path.unlink()
    return None


@datasaver()
def save_nodes_json(node_records: list[dict], website_dir: Path) -> dict:
    years = [r["year"] for r in node_records if r["year"] is not None]
    path = _data_dir(website_dir) / "nodes.json"
    _write_json(path, {"year_min": min(years) if years else None,
                       "year_max": max(years) if years else None,
                       "nodes": node_records})
    return {"path": str(path), "n_nodes": len(node_records)}


@datasaver()
def save_clusters_json(clusters_legend: dict, website_dir: Path) -> dict:
    path = _data_dir(website_dir) / "clusters.json"
    _write_json(path, clusters_legend)
    return {"path": str(path), "n_clusters": len(clusters_legend)}


@datasaver()
def save_communities_by_resolution_json(
    communities_legend_by_resolution: dict, community_resolution: Optional[float], website_dir: Path
) -> dict:
    path = _data_dir(website_dir) / "communities_by_resolution.json"
    present = bool(communities_legend_by_resolution)
    _write_optional_json(path, {"default_resolution": str(community_resolution),
                                "by_resolution": communities_legend_by_resolution}, present)
    return {"path": str(path) if present else None,
            "n_resolutions": len(communities_legend_by_resolution),
            "n_communities_total": sum(len(v) for v in communities_legend_by_resolution.values())}


@datasaver()
def save_resolution_metrics_json(
    resolution_metrics_records: list[dict], community_resolution: Optional[float], website_dir: Path
) -> dict:
    path = _data_dir(website_dir) / "resolution_metrics.json"
    written = _write_optional_json(
        path, {"default_resolution": str(community_resolution), "resolutions": resolution_metrics_records},
        bool(resolution_metrics_records))
    return {"path": written, "n_resolutions": len(resolution_metrics_records)}


@datasaver()
def save_resolution_metrics_after_cm_json(
    resolution_metrics_after_cm_records: list[dict], community_resolution: Optional[float], website_dir: Path
) -> dict:
    path = _data_dir(website_dir) / "resolution_metrics_after_cm.json"
    written = _write_optional_json(
        path, {"default_resolution": str(community_resolution), "resolutions": resolution_metrics_after_cm_records},
        bool(resolution_metrics_after_cm_records))
    return {"path": written, "n_resolutions": len(resolution_metrics_after_cm_records)}


@datasaver()
def save_connectivity_metrics_json(
    connectivity_metrics_records: list[dict], community_resolution: Optional[float], website_dir: Path
) -> dict:
    path = _data_dir(website_dir) / "connectivity_metrics.json"
    written = _write_optional_json(
        path, {"default_resolution": str(community_resolution), "resolutions": connectivity_metrics_records},
        bool(connectivity_metrics_records))
    return {"path": written, "n_resolutions": len(connectivity_metrics_records)}


@datasaver()
def save_community_distributions_json(community_distribution_records: dict, website_dir: Path) -> dict:
    path = _data_dir(website_dir) / "community_distributions.json"
    present = bool(community_distribution_records.get("by_resolution"))
    written = _write_optional_json(path, community_distribution_records, present)
    n_total = sum(len(v["community_id"]) for v in community_distribution_records.get("by_resolution", {}).values())
    return {"path": written, "n_resolutions": len(community_distribution_records.get("by_resolution", {})),
            "n_communities_total": n_total}


@datasaver()
def save_community_keywords_json(community_keywords_records: dict, website_dir: Path) -> dict:
    path = _data_dir(website_dir) / "community_keywords.json"
    n_communities = sum(len(v) for v in community_keywords_records["by_resolution"].values())
    written = _write_optional_json(path, community_keywords_records, n_communities > 0)
    return {"path": written, "n_communities": n_communities, "source": community_keywords_records["source"]}


@datasaver()
def save_figures(figure_entries: list[dict], graphml_path: Path, website_dir: Path) -> dict:
    """Copy every figure into <website>/figures/ and write figures.json (the
    gallery index). The site graph's name lets the gallery tell figures made on
    this graph from figures made on another one."""
    figures_dir = Path(website_dir) / FIGURES_SUBDIR
    figures_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for entry in figure_entries:
        shutil.copy2(entry["source"], figures_dir / entry["file"])
        index.append({k: v for k, v in entry.items() if k != "source"})
    payload = {"site_graph": Path(graphml_path).stem, "figures_dir": FIGURES_SUBDIR, "figures": index}
    written = _write_optional_json(_data_dir(website_dir) / "figures.json", payload, bool(index))
    return {"path": written, "n_figures": len(index)}


@datasaver()
def save_abstracts_json(abstracts: dict, website_dir: Path) -> dict:
    path = _data_dir(website_dir) / "abstracts.json"
    _write_json(path, abstracts)
    return {"path": str(path), "n_abstracts": len(abstracts)}


@datasaver()
def save_edges_bins(node_records: list[dict], edges: list, website_dir: Path) -> dict:
    n = len(node_records)
    out_off, out_tgt = _build_csr(n, edges, "out")
    in_off, in_tgt = _build_csr(n, edges, "in")
    d = _data_dir(website_dir)
    _write_csr(d / "edges_out.bin", out_off, out_tgt)
    _write_csr(d / "edges_in.bin", in_off, in_tgt)
    return {"path": str(d), "n_edges": len(edges)}


@datasaver()
def save_extra_views(extra_views: list[dict], community_resolution: Optional[float], website_dir: Path) -> dict:
    """Each extra view's own bundle in `<key>_data/`: nodes, citations among them,
    its topics, the communities of every resolution, and time snapshots."""
    written = {}
    for view in extra_views:
        d = Path(website_dir) / f"{view['key']}_data"
        d.mkdir(parents=True, exist_ok=True)
        years = [r["year"] for r in view["records"] if r["year"] is not None]
        _write_json(d / "nodes.json", {"year_min": min(years) if years else None,
                                       "year_max": max(years) if years else None,
                                       "nodes": view["records"]})
        n = len(view["records"])
        _write_csr(d / "edges_out.bin", *_build_csr(n, view["edges"], "out"))
        _write_csr(d / "edges_in.bin", *_build_csr(n, view["edges"], "in"))
        _write_json(d / "clusters.json", view["clusters"])
        _write_optional_json(d / "communities_by_resolution.json",
                             {"default_resolution": str(community_resolution), "by_resolution": view["communities"]},
                             bool(view["communities"]))
        _write_optional_json(d / "snapshots.json", view["snapshots"], view["snapshots"] is not None)
        written[view["key"]] = {"dir": d.name, "nodes": n, "edges": len(view["edges"]),
                                "topics": len(view["clusters"]),
                                "snapshots": len(view["snapshots"]["cutoffs"]) if view["snapshots"] else 0}
    return written


@datasaver()
def save_views_manifest(
    save_extra_views: dict, extra_views: list[dict], base_view_label: str, website_dir: Path
) -> dict:
    """views.json, the list of views the site offers, when there is more than the
    citation layout (otherwise any stale copy is removed)."""
    views = [{"key": "network", "label": base_view_label, "dir": DATA_SUBDIR,
              "subtitle": "Citation-network layout", "snapshots": False,
              "groupings": _view_groupings("Topic")}]
    for view in extra_views:
        views.append({"key": view["key"], "label": view["label"], "dir": save_extra_views[view["key"]]["dir"],
                      "subtitle": view["subtitle"], "snapshots": view["snapshots"] is not None,
                      "groupings": _view_groupings(view["topic_label"])})
    path = Path(website_dir) / "views.json"
    _write_optional_json(path, {"default": "network", "shared_dir": DATA_SUBDIR, "views": views}, len(views) > 1)
    return {"views": [v["key"] for v in views], "extra": save_extra_views}


def assembled_website(
    save_nodes_json: dict,
    save_clusters_json: dict,
    save_communities_by_resolution_json: dict,
    save_resolution_metrics_json: dict,
    save_resolution_metrics_after_cm_json: dict,
    save_connectivity_metrics_json: dict,
    save_community_distributions_json: dict,
    save_community_keywords_json: dict,
    save_figures: dict,
    save_abstracts_json: dict,
    save_edges_bins: dict,
    save_views_manifest: dict,
    website_dir: Path,
    site_title: str,
) -> dict:
    """Copy the vendored frontend next to the freshly written data bundle,
    producing a directory ready to serve."""
    website_dir = Path(website_dir)
    website_dir.mkdir(parents=True, exist_ok=True)
    for asset in FRONTEND_FILES:
        if asset == "index.html":
            page = (ASSETS_DIR / asset).read_text(encoding="utf-8")
            (website_dir / asset).write_text(page.replace("{{SITE_TITLE}}", html.escape(site_title)), encoding="utf-8")
        else:
            shutil.copy2(ASSETS_DIR / asset, website_dir / asset)
    manifest = {
        "website_dir": str(website_dir),
        "data_dir": str(website_dir / DATA_SUBDIR),
        "nodes": save_nodes_json["n_nodes"],
        "clusters": save_clusters_json["n_clusters"],
        "resolutions": save_communities_by_resolution_json["n_resolutions"],
        "communities_total": save_communities_by_resolution_json["n_communities_total"],
        "resolution_metrics": save_resolution_metrics_json["n_resolutions"],
        "resolution_metrics_after_cm": save_resolution_metrics_after_cm_json["n_resolutions"],
        "connectivity_resolutions": save_connectivity_metrics_json["n_resolutions"],
        "communities_profiled": save_community_distributions_json["n_communities_total"],
        "communities_with_keywords": save_community_keywords_json["n_communities"],
        "keyword_source": save_community_keywords_json["source"],
        "figures": save_figures["n_figures"],
        "abstracts": save_abstracts_json["n_abstracts"],
        "edges": save_edges_bins["n_edges"],
        "views": save_views_manifest["views"],
        "extra_views": save_views_manifest["extra"],
    }
    logger.info("assembled website: %s", manifest)
    logger.info("serve with: python -m http.server 8123 --directory %s", website_dir)
    return manifest


if __name__ == "__main__":
    sys.exit(_main())
