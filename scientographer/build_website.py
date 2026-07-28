"""Build the interactive citation-network website from a single GraphML.

Produces a self-contained sigma.js site (the same frontend as the companion
Mariana-Embedding-Space-Analysis project) that renders this repo's citation
network as a pannable/zoomable map: each dot is a paper, positioned by the
graph's own layout (``x``/``y`` node attributes), colourable by semantic
**topic** (the ``topic`` attribute) or by Leiden citation **community** (the
``cpm_communities_at_res={COMMUNITY_RESOLUTION}`` attribute), with citation
edges, search, filters, and per-topic/community detail panels.

Everything is derived from ONE GraphML plus (optionally) the per-topic metrics
from ``topic_community_analysis.py`` and the resolution-quality metrics from
``community_quality_metrics.py``; no text-embedding UMAP parquet is needed,
because positions come from the graph layout and cluster keyword labels are
computed by TF-IDF over the nodes' own keyword fields. This consolidates the
Mariana project's ``build_web_data*.py`` scripts into one Hamilton DAG.

Output (default ``reports/website/``): a directory ready to serve over HTTP ::

    reports/website/
      index.html  main.js  styles.css  tour.js   (vendored frontend, copied)
      network_data/
        nodes.json                     per-paper records (position, colour, metadata,
                                        + this paper's community id at every resolution)
        clusters.json                  topic legend (keywords, centroid, community metrics)
        communities_by_resolution.json citation-community legend per Leiden/CPM resolution,
                                        merged with community_quality_metrics.py's true
                                        (full-network) per-community quality metrics
        resolution_metrics.json        whole-graph quality metrics vs. resolution (for the
                                        Metrics tab's small-multiple charts)
        abstracts.json      {node_id: abstract}, lazily loaded
        edges_out.bin       directed citation edges, CSR uint32 (out-neighbours)
        edges_in.bin        directed citation edges, CSR uint32 (in-neighbours)

Serve with::

    python -m http.server 8123 --directory reports/website
    # then open http://localhost:8123

The web-export payload/CSR logic is ported from the Mariana project's
``build_web_data.py`` / ``build_web_data_gemini.py``; graph parsing uses
ElementTree (as the original did) because the payload builders operate on raw
per-node attribute dicts and dense edge indices.
"""

import sys
import json
import math
import struct
import shutil
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final, Optional
from collections import Counter, defaultdict

import pandas as pd

from hamilton.function_modifiers import datasaver, unpack_fields
from hamilton import driver
import hamilton.log_setup

from motor_learning_network.constants import GRAPH_LEVEL_DATA_PATH, FIGURES_PATH
from motor_learning_network.community_quality_metrics import (
    RESOLUTIONS,
    PER_COMMUNITY_PARQUET,
    PER_PARTITION_PARQUET,
)

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True
USE_TRACKER = False

GRAPHML_NS: Final[str] = "http://graphml.graphdrawing.org/xmlns"
NS = {"g": GRAPHML_NS}

# Semantic topic and citation-community node attributes on the graph.
TOPIC_ATTR: Final[str] = "topic"
COMMUNITY_RESOLUTION: Final[float] = 0.005      # repo-canonical Leiden/CPM resolution
COMMUNITY_ATTR: Final[str] = f"cpm_communities_at_res={COMMUNITY_RESOLUTION}"
OUTLIER: Final[int] = -1

# Communities below this size are left uncoloured/unnamed ("No community"),
# mirroring the Mariana site where only the largest communities are curated.
MIN_NAMED_GROUP_SIZE: Final[int] = 30

# How many entries to keep per group for the detail panels.
TOP_PAPERS: Final[int] = 5
TOP_AUTHORS: Final[int] = 5
TOP_KEYWORDS: Final[int] = 10

# Distinct, high-contrast palette (cycled), kept clear of near-black tones since
# the map background is dark. Outliers/small groups get grey.
PALETTE: Final[list[str]] = [
    "#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9A6324", "#808000", "#6f6fff", "#a9a9a9",
    "#ff7f50", "#aaffc3", "#ffd8b1", "#ffe119", "#e6beff",
]
OUTLIER_COLOR: Final[str] = "#cccccc"

ASSETS_DIR: Final[Path] = Path(__file__).resolve().parent / "website_assets"
WEBSITE_DIR: Final[Path] = Path("reports", "website")
DATA_SUBDIR: Final[str] = "network_data"


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
    """Return (nodes, edges, id_to_idx): nodes = [(graphml_id, attrs_dict)],
    edges = [(src_idx, tgt_idx)] over the dense node index."""
    tree = ET.parse(graphml_file)
    root = tree.getroot()
    keys = {k.attrib["id"]: k.attrib["attr.name"] for k in root.findall("g:key", NS)}

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
    return nodes, edges, id_to_idx


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
    Used for both the topic and community legends."""
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


def _resolution_communities(a: dict) -> dict[str, int]:
    """This paper's community id at every swept Leiden/CPM resolution, read
    from the graph's `cpm_communities_at_res=<r>` attributes."""
    return {str(r): _to_int(a.get(f"cpm_communities_at_res={r}"), OUTLIER) for r in RESOLUTIONS}


##################
##     Main     ##
##################
def _main() -> int:
    inputs = dict(
        graphml_path=GRAPH_LEVEL_DATA_PATH / "citation_network_with_topics_new.graphml",
        topic_metrics_path=GRAPH_LEVEL_DATA_PATH / "topic_community" / "topic_community_metrics.json",
        per_community_metrics_path=PER_COMMUNITY_PARQUET,
        per_partition_metrics_path=PER_PARTITION_PARQUET,
    )
    outputs = ["assembled_website"]
    import __main__
    builder = driver.Builder().with_modules(__main__)
    if USE_TRACKER:
        from hamilton_sdk import adapters
        from motor_learning_network.constants import (
            DEFAULT_UI_PROJECT_ID, DEFAULT_UI_USERNAME, TEAM_NAME)
        builder = builder.with_adapters(adapters.HamiltonTracker(
            project_id=DEFAULT_UI_PROJECT_ID, username=DEFAULT_UI_USERNAME,
            dag_name=CURRENT_FILE_NAME,
            tags={"environment": "DEV", "team": TEAM_NAME, "version": "0.1"}))
    dr = builder.build()
    dr.validate_execution(outputs, inputs=inputs)
    dr.display_all_functions(
        FIGURES_PATH / f"{CURRENT_FILE_NAME}_all_functions.png", keep_dot=True, deduplicate_inputs=True)
    dr.visualize_execution(
        outputs, inputs=inputs,
        output_file_path=FIGURES_PATH / f"{CURRENT_FILE_NAME}.png", keep_dot=False, deduplicate_inputs=True)
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
@unpack_fields("raw_nodes", "edges")
def parsed_graphml(graphml_path: Path) -> tuple[list, list]:
    raw_nodes, edges, _ = _parse_graphml(graphml_path)
    logger.info("parsed graphml: %d nodes, %d edges", len(raw_nodes), len(edges))
    return raw_nodes, edges


def topic_metrics(topic_metrics_path: Path) -> dict:
    """Optional per-topic citation-community metrics from topic_community_analysis.py."""
    if Path(topic_metrics_path).exists():
        with open(topic_metrics_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        logger.info("loaded topic-community metrics for %d topics", len(m))
        return m
    logger.info("no topic-community metrics at %s (topic panels omit them)", topic_metrics_path)
    return {}


def node_records(raw_nodes: list) -> list[dict]:
    """Per-paper web records: position (graph x/y), semantic topic (`cluster`
    field + `color`), citation community (`community` + `community_color`), and
    display metadata. Communities below MIN_NAMED_GROUP_SIZE are greyed."""
    community_sizes: Counter = Counter(
        _to_int(a.get(COMMUNITY_ATTR), OUTLIER) for _, a in raw_nodes)

    records = []
    for nid, a in raw_nodes:
        topic = _to_int(a.get(TOPIC_ATTR), OUTLIER)
        community = _to_int(a.get(COMMUNITY_ATTR), OUTLIER)
        named = community >= 0 and community_sizes[community] >= MIN_NAMED_GROUP_SIZE
        records.append({
            "id": nid,
            "title": (a.get("title") or "").strip(),
            "authors": (a.get("authors") or "").strip(),
            "keywords": (a.get("keywords") or "").strip(),
            "year": _to_int(a.get("year")),
            "journal": (a.get("journal") or "").strip(),
            "doi": (a.get("name") or "").strip(),
            "cluster": topic,                                # semantic grouping
            "color": _cluster_color(topic),
            "community": community,                          # citation grouping (at COMMUNITY_RESOLUTION)
            "community_color": _cluster_color(community) if named else OUTLIER_COLOR,
            "communities": _resolution_communities(a),       # same grouping at every swept resolution
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


def clusters_legend(node_records: list[dict], topic_metrics: dict) -> dict:
    """Legend for the semantic `cluster` (topic) grouping: keyword label,
    UMAP-free centroid (mean x/y), size, keywords, and the optional per-topic
    citation-community metrics merged in for the detail panel."""
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
            continue  # topic -1 = "no topic"
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


def per_community_quality_df(per_community_metrics_path: Path) -> pd.DataFrame:
    """Per-(resolution, community) quality metrics from community_quality_metrics.py
    -- the true values computed on the full, unfiltered citation network (this
    site only plots the subset of papers that also have a semantic topic)."""
    return pd.read_parquet(per_community_metrics_path)


def per_partition_quality_df(per_partition_metrics_path: Path) -> pd.DataFrame:
    """Per-resolution partition-level quality metrics (modularity, constant
    Potts model score, surprise, significance, cross-seed stability, adjacent-
    resolution plateau detection, ...) from community_quality_metrics.py."""
    return pd.read_parquet(per_partition_metrics_path)


def community_quality_lookup(per_community_quality_df: pd.DataFrame) -> dict:
    """resolution (str) -> community_id (str) -> quality metrics, for merging
    the true full-network numbers into each resolution's community legend."""
    quality_fields = [
        "community_size", "conductance", "conductance_out", "conductance_in",
        "internal_edge_density", "internal_directed_edge_count", "boundary_edge_count",
    ]
    lookup: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in per_community_quality_df.itertuples(index=False):
        lookup[str(row.resolution)][str(int(row.community_id))] = {f: getattr(row, f) for f in quality_fields}
    return dict(lookup)


def resolution_metrics_records(
    per_partition_quality_df: pd.DataFrame, per_community_quality_df: pd.DataFrame
) -> list[dict]:
    """The whole-graph metrics vs. resolution, as a flat list of records (one
    per resolution) for the Metrics tab's small-multiple charts. NaN (the
    plateau-neighbor NMI at the first/last resolution) becomes null so this
    survives json.dump/JSON.parse; a resolution's median per-community
    conductance is added since that's what centers the Integration color scale."""
    median_conductance = per_community_quality_df.groupby("resolution")["conductance"].median()
    records = []
    for row in per_partition_quality_df.sort_values("resolution").itertuples(index=False):
        record = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row._asdict().items()}
        record["median_conductance"] = float(median_conductance.get(row.resolution, 0.0))
        records.append(record)
    return records


def communities_legend_by_resolution(node_records: list[dict], community_quality_lookup: dict) -> dict:
    """Per-resolution version of the citation-community legend: for each of the
    swept Leiden/CPM resolutions, the same top-papers/authors/keywords/centroid
    legend as before (only communities >= MIN_NAMED_GROUP_SIZE, mirroring 'only
    the largest are named'), merged with the true full-network quality metrics
    from community_quality_metrics.py. `size` is the count within this site's
    plotted subset (for centroids/top-lists); `true_size` (from the quality
    lookup) is added when it differs, since the full network has papers this
    site doesn't plot (no semantic topic assigned)."""
    result = {}
    for resolution in RESOLUTIONS:
        res_key = str(resolution)
        quality_for_res = community_quality_lookup.get(res_key, {})
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
            entry = {
                "id": cid,
                "name": _label_from_keywords(kws, f"Community {cid}"),
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


def _data_dir() -> Path:
    d = WEBSITE_DIR / DATA_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


@datasaver()
def save_nodes_json(node_records: list[dict]) -> dict:
    years = [r["year"] for r in node_records if r["year"] is not None]
    path = _data_dir() / "nodes.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"year_min": min(years) if years else None,
                   "year_max": max(years) if years else None,
                   "nodes": node_records}, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": str(path), "n_nodes": len(node_records)}


@datasaver()
def save_clusters_json(clusters_legend: dict) -> dict:
    path = _data_dir() / "clusters.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clusters_legend, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": str(path), "n_clusters": len(clusters_legend)}


@datasaver()
def save_communities_by_resolution_json(communities_legend_by_resolution: dict) -> dict:
    path = _data_dir() / "communities_by_resolution.json"
    payload = {"default_resolution": str(COMMUNITY_RESOLUTION), "by_resolution": communities_legend_by_resolution}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    n_communities_total = sum(len(v) for v in communities_legend_by_resolution.values())
    return {
        "path": str(path),
        "n_resolutions": len(communities_legend_by_resolution),
        "n_communities_total": n_communities_total,
    }


@datasaver()
def save_resolution_metrics_json(resolution_metrics_records: list[dict]) -> dict:
    path = _data_dir() / "resolution_metrics.json"
    payload = {"default_resolution": str(COMMUNITY_RESOLUTION), "resolutions": resolution_metrics_records}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": str(path), "n_resolutions": len(resolution_metrics_records)}


@datasaver()
def save_abstracts_json(abstracts: dict) -> dict:
    path = _data_dir() / "abstracts.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(abstracts, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": str(path), "n_abstracts": len(abstracts)}


@datasaver()
def save_edges_bins(node_records: list[dict], edges: list) -> dict:
    n = len(node_records)
    out_off, out_tgt = _build_csr(n, edges, "out")
    in_off, in_tgt = _build_csr(n, edges, "in")
    d = _data_dir()
    _write_csr(d / "edges_out.bin", out_off, out_tgt)
    _write_csr(d / "edges_in.bin", in_off, in_tgt)
    return {"path": str(d), "n_edges": len(edges)}


def assembled_website(
    save_nodes_json: dict,
    save_clusters_json: dict,
    save_communities_by_resolution_json: dict,
    save_resolution_metrics_json: dict,
    save_abstracts_json: dict,
    save_edges_bins: dict,
) -> dict:
    """Copy the vendored frontend (index.html/main.js/styles.css/tour.js) next to
    the freshly written data bundle, producing a directory ready to serve."""
    WEBSITE_DIR.mkdir(parents=True, exist_ok=True)
    for asset in ("index.html", "main.js", "styles.css", "tour.js"):
        shutil.copy2(ASSETS_DIR / asset, WEBSITE_DIR / asset)
    manifest = {
        "website_dir": str(WEBSITE_DIR),
        "data_dir": str(WEBSITE_DIR / DATA_SUBDIR),
        "nodes": save_nodes_json["n_nodes"],
        "clusters": save_clusters_json["n_clusters"],
        "communities_total": save_communities_by_resolution_json["n_communities_total"],
        "resolutions": save_resolution_metrics_json["n_resolutions"],
        "abstracts": save_abstracts_json["n_abstracts"],
        "edges": save_edges_bins["n_edges"],
    }
    logger.info("assembled website: %s", manifest)
    logger.info("serve with: python -m http.server 8123 --directory %s", WEBSITE_DIR)
    return manifest


if __name__ == "__main__":
    sys.exit(_main())
