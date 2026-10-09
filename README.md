# Scientographer

[![PyPI](https://img.shields.io/pypi/v/scientographer)](https://pypi.org/project/scientographer/)
[![Python](https://img.shields.io/pypi/pyversions/scientographer)](https://pypi.org/project/scientographer/)
[![Tests](https://github.com/alfredohernandezinostroza/scientographer/actions/workflows/tests.yml/badge.svg)](https://github.com/alfredohernandezinostroza/scientographer/actions/workflows/tests.yml)
[![License: AGPL v3+](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](https://github.com/alfredohernandezinostroza/scientographer/blob/main/LICENSE)

Map a scientific literature from its citation network.

Give Scientographer two tables — the papers of a field and the references between
them — and it builds the citation graph, detects Leiden/CPM communities at every
resolution of a sweep, measures how real those communities are, names each one by
its most distinguishing keywords, and renders everything as an interactive map you
can open in a browser.

Every stage is an [Apache Hamilton](https://hamilton.apache.org/) DAG, the stages are
wired together by [DVC](https://dvc.org/), and one `params.yaml` holds every setting.
A run is therefore reproducible, cached, and recorded: change a setting and only the
stages that read it run again.

## What you get

- **Communities at every resolution.** Leiden with the constant Potts model across a
  resolution sweep (36 values by default), written onto one graph as
  `cpm_communities_at_res=<r>` vertex attributes. Results are stored per resolution,
  so adding a resolution later computes only that one.
- **Evidence for choosing a resolution.** Per resolution: modularity, the CPM score,
  surprise, significance, coverage, cross-seed stability and resolution plateaus.
  Per community: size, conductance, internal edge density and internal edge surprise
  (a size-aware test of whether a community is denser than chance).
- **Well-connectedness.** The minimum-edge-cut test of Park et al. (2023), and the
  Connectivity Modifier remediation with the same metrics recomputed afterwards.
- **Names.** Each community labelled by corrected TF-IDF over its papers' keywords
  (optionally merging synonyms), written onto the graph as `top_keywords_at_res=<r>`.
- **Word clouds** and TF-IDF histograms per community.
- **Text-embedding maps** (optional). Each paper's title and abstract embedded with
  Gemini's API and/or SPECTER2 run locally; BERTopic topics, a 2-D UMAP layout and
  time snapshots per embedding, shown as extra views of the map.
- **An interactive map** (see [The map](#the-map)).

The GraphML files the pipeline writes carry the communities, labels and metrics as
attributes, so you can also open them in Gephi or load them with igraph or networkx.

## Install

Scientographer needs Python 3.10 or later. The plain install runs every stage of a
project (DVC, ForceAtlas2, word clouds, the map); two extras add the heavy parts:

| Extra | For |
|---|---|
| `embeddings` | the text-embedding maps (installs PyTorch, a few GB) |
| `ui` | the Hamilton UI run tracker |
| `all` | both |

### With pixi (recommended)

[pixi](https://pixi.sh) writes a `pixi.lock` that records the exact version of every
package, so your project gives the same communities on any machine and in a year's
time. This matters: Leiden's partitions change between library versions (for example
`leidenalg` 0.11 and 0.12) even with the same seed. pixi also installs the non-Python
tools some stages use, such as Graphviz for the DAG drawings.

```bash
pixi init my-field && cd my-field
pixi add python=3.12 graphviz
pixi add --pypi scientographer
pixi shell                    # or prefix every command below with `pixi run`
```

Commit `pixi.toml` and `pixi.lock` with your project.

### With pip

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install scientographer
```

There is no lock file, so commit a `pip freeze > requirements.txt` to keep the versions
behind your results on record. The DAG drawings also need Graphviz's `dot` program from
your system's package manager (`apt install graphviz`, `brew install graphviz`); without
it the stages still run and only skip the drawings.

To check the install: `scientographer --help`. The development version installs
from GitHub: replace `scientographer` by
`"scientographer @ git+https://github.com/alfredohernandezinostroza/scientographer.git"`.
Extras go in square brackets, quoted: `pip install "scientographer[embeddings]"`.

## Try the example

The package ships a complete example built only from openly licensed data: 3,925
open-access articles on motor adaptation and motor skill and sequence learning
(2001–2026), all published under CC BY, with their authors' own keywords and abstracts
from Europe PMC, and the 6,860 citations between them from OpenAlex (CC0). The pipeline
keeps the connected core (about 2,500 papers) and finds dozens of communities; the
whole run takes a few minutes.

```bash
scientographer example my-example && cd my-example
git init && dvc init
dvc repro                      # the whole pipeline
scientographer website         # http://localhost:8123
```

Sources and attribution are in the example's `data/ATTRIBUTION.md`.

## Start from a PubMed search

No tables to prepare: give a PubMed query, and the pipeline downloads the matching
records from PubMed and the citations between them from OpenAlex, then maps them.

```bash
scientographer init my-field --pubmed '("motor learning"[tiab] OR "motor adaptation"[tiab])' \
    --email you@university.edu
cd my-field && git init && dvc init
dvc repro
scientographer website
```

Three stages run before the pipeline:

| Stage | What it does |
|---|---|
| `search_pubmed` | searches PubMed (NCBI E-utilities) and downloads the matching records in MEDLINE format to `data/raw/pubmed/` |
| `ingest` | reads them into `data/papers.parquet`: title, abstract, authors, journal, year, MeSH terms, and keywords (the authors' own, else the MeSH major topics); records without a DOI are left out and listed in `data/ingest/` |
| `fetch_references` | looks the papers up in [OpenAlex](https://openalex.org) by DOI and writes the citations among them to `data/references.parquet` |

Choose a query of a few thousand papers or more: a field's papers cite each other
enough to form a map only at that scale (two years of one phrase gave a 30-paper
graph; three phrases over all years, 14,156 records, gave 10,892 connected papers).
The search and the citations took about 4 minutes for those 14,156 records.

- PubMed returns at most 10,000 records per search; larger results are collected by
  splitting the search by publication date. A query matching more than `--max-records`
  (20,000 by default, `pubmed_search.max_records`) stops with an error rather than
  mapping an arbitrary subset.
- `init --pubmed` sets the size thresholds for a few thousand papers (15); for
  10,000 papers or more, raise them to about 30 in `params.yaml`.
- Neither service needs an account. Optional API keys raise their limits: NCBI's
  (`NCBI_API_KEY`) allows 10 requests a second instead of 3, and OpenAlex's
  (`OPENALEX_API_KEY`) a ten times larger daily budget than the roughly 100,000 papers a
  day it allows without one. Put them in the project's `.env`.
- Downloaded records and looked-up papers are kept: widening the query later fetches
  only what is new, and a run stopped by a quota resumes where it stopped.
- To include papers published since the last search, re-run the search on purpose:
  `dvc repro -f search_pubmed`, then `dvc repro`.

## Your own corpus

```bash
scientographer init my-field && cd my-field    # params.yaml, dvc.yaml, data/, .gitignore
```

Put two tables in `data/` (Parquet, CSV or TSV):

| File | Columns |
|---|---|
| `data/papers.parquet` | `doi` (required), plus any of `title`, `authors`, `keywords`, `abstract`, `journal`, `year`; `authors` and `keywords` as lists or `\|`-separated strings |
| `data/references.parquet` | `citing_doi`, `cited_dois` (a list, or `\|`-separated) |

Only citations between two papers of your corpus become edges, and only the largest
connected component is kept. Where the tables come from (Scopus, Web of Science,
PubMed, OpenAlex, OpenCitations) is up to you.

Before the first run, open `params.yaml`. Every setting is documented there; the ones
that matter most:

- **Size thresholds** scale with your corpus: `communities.substantive_min_size`,
  `community_keywords.min_community_size` and `website.min_named_group_size` (30 suits
  10,000+ papers; a few hundred papers want about 5).
- **The resolution sweep** (`resolutions.low/mid/high`) and the resolutions the map
  opens at (`website.default_resolution`) and summarises (`resolutions.canonical`).
- **Run time.** The Connectivity Modifier grows steeply with community size;
  `communities.connectivity_modifier_max_community_size` skips resolutions whose
  largest community is too big. `communities.workers` sets the parallelism.

Then:

```bash
git init && dvc init
dvc repro
scientographer website
```

### Adding resolutions later

Add values to the sweep in `params.yaml` and run `dvc repro`: every stage reuses the
results it has stored for the other resolutions and computes only the new ones. A
project whose outputs were made before the stores existed fills them once with
`scientographer store-existing-results`.

### Text-embedding maps

Install the `embeddings` extra, then list the models and maps in `params.yaml`:

```yaml
embeddings:
  models:
    - name: specter2            # local, no key needed
      provider: specter2
    - name: gemini              # Google's API
      provider: gemini
      model: gemini-embedding-2
      dimensions: 3072
      requests_per_minute: 90
embedding_maps:
  maps:
    - name: specter2
      min_cluster_size: 50      # HDBSCAN: the smallest topic; scale it with the corpus
    - name: gemini
      min_cluster_size: 50
      snapshots: [1990, 2000, 2010]
website:
  extra_layouts:
    - {key: specter2, label: SPECTER2 embedding, positions: data/layouts/specter2.csv}
    - {key: gemini, label: Gemini embedding, positions: data/layouts/gemini.csv,
       snapshots: data/layouts/gemini_snapshots.json}
```

Gemini needs `GEMINI_API_KEY` in your environment or in the project's `.env` (which
the generated `.gitignore` keeps out of git). Embeddings are stored per paper, so a
later run embeds only new papers. The map then offers each embedding as a view, and
colours the citation layout by the first embedding's topics too
(`website.network_topics`).

## The map

`scientographer website` serves the site the last stage builds (a static folder,
`reports/website/`, that you can also publish on any web host). It offers:

- **Views:** the citation layout (ForceAtlas2, or your own Gephi layout through
  `layout.mode: import`), plus one per text embedding, each with a time menu when it has
  snapshots.
- **Colour by** community at any resolution of the sweep, embedding topic, year,
  citations, or integration; **well-connected papers only** hides the papers the
  Connectivity Modifier left out at that resolution.
- **Search and filters** on title, author, abstract, journal, keywords and (for a
  PubMed corpus) MeSH terms, matching
  whole words by default (so *dance* does not match *guidance*), or prefixes,
  substrings or regular expressions.
- **Panels** per paper and per community (top papers, authors, keyword bars), a
  **Metrics** tab with every measure above against resolution, a **Figures** tab with
  the word clouds, and a **Word map** tab: where words occur across the map, as
  density heatmaps.
- **Export** of the visible map as a high-resolution PNG: transparent or on a
  background, styled for white paper or dark slides, with straight or curved citation
  edges, and labels on the communities or around the map's outline.
- **Rotate and flip** buttons. The default orientation is reproducible: the main
  body's long axis horizontal, its long tail down, its most-cited side left.

## Commands

| Command | What it does |
|---|---|
| `scientographer init [dir]` | create a project (`--pubmed QUERY`: from a PubMed search) |
| `scientographer example [dir]` | create the example project, data included |
| `scientographer stages` | list the stages (`*` = in this project's dvc.yaml) |
| `scientographer run <stage>` | run one stage now, without DVC bookkeeping |
| `scientographer pipeline [stage]` | `dvc repro`, for everything or up to one stage |
| `scientographer website [--port N]` | serve the built site |
| `scientographer store-existing-results` | fill the per-resolution stores from current outputs |
| `scientographer ui` | start the Hamilton UI tracker (set `tracker.enabled: true`) |
| `scientographer params` | show which `params.yaml` is in effect |

`params.yaml` is found in this order: the file named by `SCIENTOGRAPHER_PARAMS`, then
`./params.yaml`, then the packaged defaults.

## The pipeline

```
(search_pubmed -> ingest -> fetch_references ->)      with `init --pubmed`
data/papers + data/references
  -> build_citation_network -> detect_communities -> layout_graph
  -> community_quality_metrics
  -> community_connectivity_metrics -> community_connectivity_modifier
  -> community_quality_metrics_after_connectivity_modifier
  -> community_keywords -> wordclouds -> build_website
build_citation_network -> embed_papers -> embedding_maps -> build_website   (optional)
```

Each arrow is a DVC stage (see the project's `dvc.yaml`); each stage is one module of
this package, and `scientographer run <stage>` draws its Hamilton DAG to
`reports/figures/`. Stages whose section of `params.yaml` is empty (the embedding
stages by default) do nothing.

## Experiments

Every stage declares which `params.yaml` settings it reads, so DVC experiments work
out of the box. Commit a baseline run first, so experiments have something to be
compared against:

```bash
dvc repro
git add -A && git commit -m "baseline run"      # dvc.lock + the small metrics files
dvc exp run -S resolutions.canonical=0.003       # re-runs only the affected stages
dvc exp show                                     # baseline vs. experiments, side by side
dvc exp apply <name>                             # keep the one you like
```

`dvc exp show` compares the summary metrics each run writes to
`data/analysis/metrics/` (communities found, coverage, modularity, stability,
well-connectedness at the canonical resolution); `dvc plots diff` compares the
per-resolution curves.

The resolution sweep itself is not an experiment: stages compare resolutions against
each other within one run (stability, plateaus), so the sweep stays inside the
stages and experiments vary the settings around it.

To share data and results, add a DVC remote (`dvc remote add`); the install
includes S3-compatible storage such as DagsHub, AWS S3 or MinIO, and other storage
needs its plugin (`dvc-gdrive`, ...).

## Tracking runs with the Hamilton UI

Every stage is a Hamilton DAG, and Hamilton's UI can record each run: which
functions ran, with which inputs, how long they took, and what they produced.
Install the `ui` extra, then:

```bash
scientographer ui            # starts the UI, sets up its project, keeps serving
```

Set `tracker.enabled: true` in `params.yaml`, and every `dvc repro` or
`scientographer run` reports to it; each run prints a link to its page. The UI keeps
its data in `.hamilton/` inside the project.

## Using it with an AI coding agent

[`skills/scientographer/SKILL.md`](https://github.com/alfredohernandezinostroza/scientographer/blob/main/skills/scientographer/SKILL.md) teaches an agent
(Claude Code, Codex, Cursor, …) how to set up, run, configure and troubleshoot a
Scientographer project. For Claude Code, copy the folder into your project's
`.claude/skills/` (or `~/.claude/skills/` for all projects):

```bash
mkdir -p .claude/skills && cd .claude/skills
curl -L https://github.com/alfredohernandezinostroza/scientographer/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=2 scientographer-main/skills/scientographer
```

Other agents can be pointed at the same file (for example from `AGENTS.md`).

## Development

```bash
git clone https://github.com/alfredohernandezinostroza/scientographer.git && cd scientographer
pip install -e ".[all,dev]"
pytest
```

See [CONTRIBUTING.md](https://github.com/alfredohernandezinostroza/scientographer/blob/main/CONTRIBUTING.md) for how a stage is built.

## License

- **The pipeline** (the `scientographer` package) is licensed under the
  [GNU Affero General Public License v3.0 or later](https://github.com/alfredohernandezinostroza/scientographer/blob/main/LICENSE). You can use, study,
  modify and share it freely. If you distribute a modified version, or let people use
  a modified version over a network (for example as a hosted service), you must offer
  them its source code under the same license.
- **The map frontend** (`scientographer/website_assets/`) and **the project templates**
  (`params.yaml`, `templates/dvc.yaml`) are [MIT-licensed](https://github.com/alfredohernandezinostroza/scientographer/blob/main/LICENSE-MIT). These are the
  files Scientographer copies into your maps and projects, so maps and projects you
  publish carry no license obligations.
- **Your data and results** are yours: the graphs, metrics, JSON and figures the pipeline
  computes are its output, not part of the software.

Each file states its license in an `SPDX-License-Identifier` header. If you use
Scientographer in research, please cite it (see [CITATION.cff](https://github.com/alfredohernandezinostroza/scientographer/blob/main/CITATION.cff)).

## Origin

Scientographer grew out of a citation-network study of the motor-learning literature
(roughly 23,000 papers from Scopus, Web of Science, MEDLINE and EBSCO). The study
remains its own project and uses this package as a dependency.
