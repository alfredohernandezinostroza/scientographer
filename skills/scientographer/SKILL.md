---
name: scientographer
description: How to install, run, configure, extend and troubleshoot Scientographer, the Python package that maps a scientific literature from its citation network (from two tables, or straight from a PubMed search) (Leiden/CPM communities over a resolution sweep, quality and well-connectedness metrics, keyword labels, text-embedding maps, an interactive website) as a Hamilton + DVC pipeline. Use whenever a project has a params.yaml/dvc.yaml made by `scientographer init`, the user mentions Scientographer, or asks to build a citation map, run the pipeline, pick a resolution, add resolutions, add Gemini/SPECTER2 embedding maps, read the pipeline's results, or publish the map.
---

# Scientographer

Scientographer turns two tables (papers, references) into a citation graph,
Leiden/CPM communities at many resolutions, metrics that say how real those
communities are, keyword names, and a static interactive website. Each stage is
a module of the `scientographer` package run by DVC; **every setting lives in the
project's `params.yaml`**, which documents itself. Read it before changing anything.

## Ground rules

- **Run everything inside the project's environment.** In a pixi project prefix
  commands with `pixi run` (or work in `pixi shell`); with pip, activate the venv.
  `scientographer: command not found` almost always means the wrong environment.
- **Change settings in `params.yaml`, never in package code.** Stages read their
  section through `scientographer.config.params()`; DVC re-runs exactly the
  stages whose section changed.
- **Run the pipeline with `dvc repro`** (or `scientographer pipeline`). `scientographer
  run <stage>` runs one stage without updating `dvc.lock`; after it, `dvc commit
  <stage>` records the result, or `dvc repro` will run the stage again.
- **Never print or commit secrets.** API keys (`GEMINI_API_KEY`) go in the project's
  `.env`, which the generated `.gitignore` excludes. Refer to them by name only.
- **Results depend on library versions.** `leidenalg` 0.11 and 0.12 give different
  partitions with the same seed. Keep `pixi.lock` (or a `pip freeze`) with the
  results, and do not upgrade packages in a project mid-analysis without saying so.
- **Long stages:** the Connectivity Modifier (minimum cuts) can take hours on large
  communities. Before a big run, check `communities.connectivity_modifier_max_community_size`
  and `communities.workers`; run long jobs in the background and watch their log.

## Setting up

```bash
# pixi (recommended: a lock file pins every version)
pixi init my-field && cd my-field
pixi add python=3.12 graphviz
pixi add --pypi scientographer

# or pip
python -m venv .venv && source .venv/bin/activate
pip install scientographer
```

The plain install runs every stage of a project (DVC, ForceAtlas2, word clouds, the
map). Extras, installed as `"scientographer[embeddings]"`: `embeddings` (Gemini client,
SPECTER2 via PyTorch, BERTopic, UMAP; large), `ui` (Hamilton UI tracker), `all`,
`dev` (pytest, ruff).

Then either the bundled example (3,925 open-access papers; a few minutes):

```bash
scientographer example my-example && cd my-example
git init && dvc init && dvc repro && scientographer website   # http://localhost:8123
```

or a project built from a PubMed search (no tables to prepare):

```bash
scientographer init my-field --pubmed '"motor learning"[tiab]' --email you@uni.edu
cd my-field && git init && dvc init && dvc repro && scientographer website
```

or a project from the user's own tables: `scientographer init my-field`, put the two
tables in `data/`, adjust `params.yaml`, then `git init && dvc init && dvc repro`.

### The PubMed path

`init --pubmed` puts three stages in front of the pipeline: `search_pubmed` (NCBI
E-utilities; MEDLINE records to `data/raw/pubmed/`), `ingest` (records to
`data/papers.parquet`, merged by DOI; report and DOI-less records in `data/ingest/`)
and `fetch_references` (citations among the papers from OpenAlex by DOI, to
`data/references.parquet`; report in `data/references_store/report.json`). Settings:
`pubmed_search`, `ingest`, `fetch_references` in `params.yaml`.

- Before running, check the query's size: a few thousand papers or more make a map
  (small corpora barely cite each other); more than `pubmed_search.max_records` stops
  the stage on purpose. PubMed's 10,000-per-search limit is handled by splitting by date.
- `init --pubmed` sets the size thresholds to 15; raise them to ~30 above 10,000 papers.
- Optional keys in `.env`: `NCBI_API_KEY` (3 -> 10 requests/s), `OPENALEX_API_KEY`
  (10x the keyless daily budget of roughly 100,000 papers). A quota stop keeps what was
  fetched; re-running resumes.
- The search does not re-run by itself: `dvc repro -f search_pubmed` fetches papers
  published since. Report the funnel from the two report files (records, without DOI,
  found in OpenAlex, citations, papers in the graph).
- `ingest` reads only the `medline` format so far; other exports go through the
  two-table route.

### Input tables (Parquet, CSV or TSV)

| File | Columns |
|---|---|
| `data/papers.parquet` | `doi` (required); optional `title`, `authors`, `keywords`, `abstract`, `journal`, `year`. `authors`/`keywords` as lists or `\|`-separated strings |
| `data/references.parquet` | `citing_doi`, `cited_dois` (list or `\|`-separated) |

DOIs are matched case-insensitively. Only citations between two corpus papers become
edges; isolated papers are dropped and, by default, only the largest weakly connected
component is kept (`citation_network.keep_giant_component`). Expect the graph to be
noticeably smaller than the papers table; report both numbers.

## Settings that matter (all in `params.yaml`)

| Setting | What it decides |
|---|---|
| `resolutions.low/mid/high` | the Leiden/CPM sweep (CPM on the directed graph; higher = more, smaller communities) |
| `resolutions.canonical` | the resolution summarised in `data/analysis/metrics/*.json` (for `dvc exp`) |
| `website.default_resolution` | the resolution the map opens at (must be in the sweep) |
| `communities.substantive_min_size`, `community_keywords.min_community_size`, `website.min_named_group_size` | size cutoffs; scale with the corpus (30 for 10k+ papers, ~5 for a few hundred) |
| `communities.connectivity_modifier_max_community_size` | skip the Connectivity Modifier where the largest community is bigger (null = never) |
| `communities.workers` | parallel processes ("auto" = one per core) |
| `layout.mode` | `forceatlas2` (computed) or `import` (x/y from a Gephi graphml or a CSV via `import_path`) |
| `layout.orientation` | `principal` (reproducible: long axis horizontal) or `none` |
| `embeddings.models`, `embedding_maps.maps`, `website.extra_layouts` | the optional text-embedding maps (below) |

Note on resolution values: Gephi's undirected CPM penalises γ·n(n−1)/2 and this
package's directed CPM γ·n(n−1), so a Gephi resolution γ corresponds to γ/2 here.

## Common tasks

**Add resolutions.** Append the values to `resolutions.*` and `dvc repro`. Each stage
keeps per-resolution results under `.../by_resolution/` and computes only the new
ones; existing results are reused only when the graph, that resolution's partition,
the settings and library versions are unchanged. A project made before the stores
existed seeds them once: `scientographer store-existing-results`.

**Pick a resolution.** There is no single right answer; compare per resolution in
`data/analysis/community_quality_metrics/community_quality_metrics_per_partition.parquet`:
`cross_seed_normalized_mutual_information` (stability), plateaus of adjacent partitions
agreeing above `communities.plateau_nmi_threshold`, `share_of_nodes_in_substantive_communities`,
`share_of_communities_that_are_statistically_dense`, and the well-connected fraction
(connectivity metrics). State which population an aggregate is over: singletons dominate
raw community counts.

**Text-embedding maps.** Install the `embeddings` extra. Under `embeddings.models` list
`{name, provider: specter2}` (local) and/or `{name, provider: gemini, model:
gemini-embedding-2, dimensions: 3072, requests_per_minute: 90}` (needs `GEMINI_API_KEY`
in `.env`). Under `embedding_maps.maps` list `{name, min_cluster_size, snapshots}`;
`min_cluster_size` is HDBSCAN's smallest topic and sets the topic granularity, so a
bigger corpus needs a bigger value. Under `website.extra_layouts` list `{key, label,
positions: data/layouts/<name>.csv, snapshots: data/layouts/<name>_snapshots.json}`.
Embeddings are stored per paper and text, so later runs embed only what is new. The
citation view is coloured by the first map's topics (`website.network_topics`).

**Import a hand-tuned Gephi layout.** `layout.mode: import`, `layout.import_path:`
the Gephi graphml (or a CSV with `name`/`id`, `x`, `y`), and add the file to the
`layout_graph` stage's `deps` in `dvc.yaml`.

**Experiments.** Commit a baseline (`dvc repro && git add -A && git commit`), then
`dvc exp run -S section.key=value`, `dvc exp show`, `dvc exp apply <name>`.

**Publish the map.** `reports/website/` is a static site: copy it to any static host
(GitHub Pages, Netlify, a DVC/DagsHub remote for the data). Serve locally with
`scientographer website [--port N]`.

## Where the results are

| Path | Contents |
|---|---|
| `data/raw/pubmed/`, `data/ingest/report.json`, `data/references_store/report.json` | PubMed path only: the records, and what was read, dropped and found |
| `data/citation_network.graphml` | the citation graph (vertex `name` = DOI, paper metadata) |
| `data/citation_network_with_layout.graphml` | + `cpm_communities_at_res=<r>` per resolution, `x`/`y` |
| `data/detect_communities/partition_summary.parquet` | communities, singletons, largest community per resolution |
| `data/analysis/community_quality_metrics/*_per_partition.parquet` | whole-partition metrics per resolution |
| `data/analysis/community_quality_metrics/*_per_community.parquet` | size, conductance, internal edge density and surprise per community |
| `data/analysis/community_connectivity_metrics/` | minimum-cut well-connectedness per community and per resolution |
| `data/analysis/community_connectivity_modifier/connectivity_modifier_membership.parquet` | per paper and resolution, its community after the Connectivity Modifier (-1 = removed) |
| `data/analysis/community_quality_metrics_after_connectivity_modifier/` | the same metrics on the modified partitions |
| `data/analysis/community_keywords/` | `top_keywords` per community and resolution, keyword scores, and the final graphml carrying every attribute |
| `data/analysis/metrics/*.json` | summaries at `resolutions.canonical` (what `dvc exp show` compares) |
| `data/embeddings/<name>/`, `data/layouts/<name>.csv` | embedding stores; per-paper 2-D position, `topic`, `topic_name` |
| `data/wordclouds/` | word clouds and TF-IDF histograms |
| `reports/website/` | the site; `reports/figures/` the stages' Hamilton DAG drawings |

Load results with pandas (`pd.read_parquet`) and igraph (`ig.Graph.Read_GraphML`);
community ids are integers, `-1` means none.

## Troubleshooting

- **`dvc repro` says nothing changed but you expected a re-run:** the change was not in
  a declared dependency (for example package code after an upgrade). `dvc repro -f -s
  <stage>` forces one stage.
- **A stage re-runs everything after adding a resolution:** the stores were empty
  (run `scientographer store-existing-results` once) or a result-relevant setting or
  library version changed, which correctly invalidates them.
- **Map colours everything grey:** that view has no data for the chosen grouping (for
  example topics without an embedding map); check `reports/website/views.json`.
- **Embedding run stops on quota errors:** lower `requests_per_minute`; rerunning
  resumes, since finished papers are stored.
- **Out of memory or very slow metrics:** lower `communities.workers` (each worker holds
  a copy of the graph) or set `connectivity_modifier_max_community_size`.
- **Hugging Face or matplotlib cache not writable:** set `HF_HOME` and `MPLCONFIGDIR` to
  a writable directory.

## Changing the package itself

Clone it, `pip install -e ".[all,dev]"`, `pytest`. Each stage is a Hamilton DAG module
(functions are nodes, parameter names are edges, `@dataloader`/`@datasaver` do I/O,
`_main()` builds the driver); keep logic in private `_helpers` tested on tiny graphs.
A new setting needs a documented default in `scientographer/params.yaml`; a new stage
also needs an entry in `scientographer/templates/dvc.yaml`. Every file carries an SPDX
license header. Details: `CONTRIBUTING.md`.
