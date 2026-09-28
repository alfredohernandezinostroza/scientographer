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

Extras: `layout` (ForceAtlas2), `wordclouds`, `dvc` (running the pipeline), `ui` (the
Hamilton UI tracker). The core install runs every other stage.

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

`examples/until_1990/` is a complete project: 292 papers and 585 citations from the
motor-learning literature up to 1990.

```bash
cd examples/until_1990
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

Every stage declares which `params.yaml` sections it reads, so DVC experiments work
out of the box:

```bash
dvc exp run -S resolutions.canonical=0.003
dvc exp show
```

The resolution sweep itself is not an experiment: stages compare resolutions against
each other within one run (stability, plateaus), so the sweep stays inside the
stages and experiments vary the settings around it.

## Development

```bash
pip install -e ".[all,dev]"
pytest
```

## Origin

Scientographer grew out of a citation-network study of the motor-learning literature
(roughly 23,000 papers from Scopus, Web of Science, MEDLINE and EBSCO). The study
remains its own project and uses this package as a dependency.
