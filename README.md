# Scientographer

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
  `cpm_communities_at_res=<r>` vertex attributes.
- **Evidence for choosing a resolution.** Per resolution: modularity, the CPM score,
  surprise, significance, coverage, cross-seed stability and resolution plateaus.
  Per community: size, conductance, internal edge density and internal edge surprise
  (a size-aware test of whether a community is denser than chance).
- **Well-connectedness.** The minimum-edge-cut test of Park et al. (2023), and the
  Connectivity Modifier remediation with the same metrics recomputed afterwards.
- **Names.** Each community labelled by corrected TF-IDF over its papers' keywords
  (optionally merging synonyms), written onto the graph as `top_keywords_at_res=<r>`.
- **Word clouds** and TF-IDF histograms per community.
- **An interactive map.** Every paper positioned by a ForceAtlas2 layout (or your own
  Gephi layout), coloured by community at any resolution through a dropdown, with
  search, filters, citation edges, per-community panels with keyword bars, a Metrics
  tab for all the measures above, and a Figures tab for the word clouds.

The GraphML files the pipeline writes carry the communities, labels and metrics as
attributes, so you can also open them in Gephi or load them with igraph or networkx.

## Install

```bash
pip install "scientographer[all]"            # when published; until then:
pip install "scientographer[all] @ git+https://github.com/alfredohernandezinostroza/scientographer"
```

Extras: `layout` (ForceAtlas2), `wordclouds`, `dvc` (running the pipeline, and pushing
data to S3-compatible storage such as DagsHub), `ui` (the Hamilton UI tracker). The core
install runs every other stage. For other DVC storage, add its plugin (`pip install dvc-gdrive`, …).

## Quick start

```bash
scientographer init my-field       # params.yaml, dvc.yaml, data/, .gitignore
cd my-field
```

Put two tables in `data/` (Parquet, CSV or TSV):

| File | Columns |
|---|---|
| `data/papers.parquet` | `doi` (required), plus any of `title`, `authors`, `keywords`, `abstract`, `journal`, `year` — `authors` and `keywords` as lists or `\|`-separated strings |
| `data/references.parquet` | `citing_doi`, `cited_dois` (a list, or `\|`-separated) |

Only citations between two papers of your corpus become edges. Where the tables come
from — Scopus, Web of Science, PubMed, OpenAlex, OpenCitations — is up to you.

Then:

```bash
git init && dvc init
dvc repro                      # the whole pipeline
scientographer website         # http://localhost:8123
```

Before a real run, open `params.yaml`: the size thresholds (`substantive_min_size`,
`min_community_size`, `min_named_group_size`) should scale with your corpus, and the
resolution sweep and `canonical` resolution are the choices that shape the map.

## Try the example

`examples/motor_learning_open_access/` is a complete project built only from openly
licensed data: 3,925 open-access articles on motor adaptation and motor skill and
sequence learning (2001–2026), all published under CC BY, with their authors' own
keywords and abstracts from Europe PMC, and the 6,860 citations between them from
OpenAlex (CC0). The pipeline keeps the connected core (about 2,500 papers) and finds
dozens of communities; the whole run takes a few minutes. Sources and attribution are
in `data/ATTRIBUTION.md`.

```bash
cd examples/motor_learning_open_access
dvc repro
scientographer website
```

## Commands

| Command | What it does |
|---|---|
| `scientographer init [dir]` | create a project |
| `scientographer stages` | list the stages (`*` = in this project's dvc.yaml) |
| `scientographer run <stage>` | run one stage now, without DVC bookkeeping |
| `scientographer pipeline [stage]` | `dvc repro`, for everything or up to one stage |
| `scientographer website` | serve the built site |
| `scientographer ui` | start the Hamilton UI tracker (set `tracker.enabled: true`) |
| `scientographer params` | show which `params.yaml` is in effect |

`params.yaml` is found in this order: the file named by `SCIENTOGRAPHER_PARAMS`, then
`./params.yaml`, then the packaged defaults.

## The pipeline

```
data/papers + data/references
  -> build_citation_network -> detect_communities -> layout_graph
  -> community_quality_metrics
  -> community_connectivity_metrics -> community_connectivity_modifier
  -> community_quality_metrics_after_connectivity_modifier
  -> community_keywords -> wordclouds -> build_website
```

Each arrow is a DVC stage (see the project's `dvc.yaml`); each stage is one module of
this package, and `scientographer run <stage>` draws its Hamilton DAG to
`reports/figures/`.

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

## Tracking runs with the Hamilton UI

Every stage is a Hamilton DAG, and Hamilton's UI can record each run: which
functions ran, with which inputs, how long they took, and what they produced.

```bash
pip install "scientographer[ui]"
scientographer ui            # starts the UI, sets up its project, keeps serving
```

Then set `tracker.enabled: true` in `params.yaml`, and every `dvc repro` or
`scientographer run` reports to it; each run prints a link to its page. The UI keeps
its data in `.hamilton/` inside the project.

## Development

```bash
pip install -e ".[all,dev]"
pytest
```

## License

- **The pipeline** (the `scientographer` package) is licensed under the
  [GNU Affero General Public License v3.0 or later](LICENSE). You can use, study,
  modify and share it freely. If you distribute a modified version, or let people use
  a modified version over a network (for example as a hosted service), you must offer
  them its source code under the same license.
- **The map frontend** (`scientographer/website_assets/`) and **the project templates**
  (`params.yaml`, `templates/dvc.yaml`) are [MIT-licensed](LICENSE-MIT). These are the
  files Scientographer copies into your maps and projects, so maps and projects you
  publish carry no license obligations.
- **Your data and results** are yours: the graphs, metrics, JSON and figures the pipeline
  computes are its output, not part of the software.

Each file states its license in an `SPDX-License-Identifier` header. If you use
Scientographer in research, please cite it (see [CITATION.cff](CITATION.cff)).

## Origin

Scientographer grew out of a citation-network study of the motor-learning literature
(roughly 23,000 papers from Scopus, Web of Science, MEDLINE and EBSCO). The study
remains its own project and uses this package as a dependency.
