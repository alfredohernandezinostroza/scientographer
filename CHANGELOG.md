# Changelog

All notable changes to Scientographer. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
settings or outputs).

## [Unreleased]

- **Map edges** are coloured by the citing paper, like the PNG export, instead of
  one grey, with **Edge opacity** (up to 1) and **Edge thickness** (from 0.05 pixels)
  sliders. Fixed: overlapping edges no longer add up to white on the 2D maps (sigma
  blends premultiplied colours, which the edge colours were not), and edges can be
  thinner than sigma's 1.7-pixel minimum.
- **How a map was made:** `scientographer --version` prints the exact version: the
  release, or from a git checkout of Scientographer the release plus the commit
  (`0.2.0.dev0+g1a2b3c4`, with `.dirty` for uncommitted changes). Every stage writes
  a record of its step: the stage, the version, its settings, its input files with
  their md5 (as in `dvc.lock`), its outputs and the date. The record travels with the
  data: in a GraphML's `provenance` graph attribute, in a parquet table's metadata
  (`scientographer_provenance`), and as `provenance.json` in each output folder. A
  stage keeps the records it finds in its inputs and adds its own, so the steps run
  from the PubMed search or table import all the way to the site. The site gathers
  them into `network_data/provenance.json`, shows the version and build date next
  to its "Made with" badge (and the version under the loading screen's logo), and
  lists every step in **About this map**, a panel that opens out of the compass
  badge (or the stamp) beside the controls: which earlier step wrote each file it
  read, its settings, and a warning when the steps ran on more than one version or
  on a checkout with uncommitted changes. Maps and outputs made before keep working;
  the tab says they have no record.
- **The environment behind every step:** each step record also keeps its Python,
  platform and the versions of the packages that change results (igraph, leidenalg,
  numpy, pandas, scikit-learn, ...), and how the installed packages compare with the
  project's lock file: `pixi.lock`, else `uv.lock`, else a `requirements.txt` pinned
  throughout with `==`. When a lock exists and the environment differs from it, a
  stage refuses to run (`scientographer run`, `pipeline` and `python -m
  scientographer.<stage>` alike), listing the differences and how to fix them;
  `SCIENTOGRAPHER_ALLOW_ENVIRONMENT_MISMATCH=1` or `--allow-environment-mismatch` runs
  it anyway and the record keeps the override and the differences. Without a lock,
  stages run as before. The site's `provenance.json` adds the full package list of
  the environment that built it; **About this map** shows it in an Environment
  section (with a `requirements.txt` download), each step's key packages, and
  warnings when steps ran on different package versions or outside the lock. The
  README explains how to rerun one stage (`dvc repro -f -s <stage>`, then `dvc repro`)
  without redoing the search and the downloads.
- **More ingest formats:** `ingest` now reads Scopus CSV (`scopus`), Web of Science
  tab-delimited (`wos`, a folder of `savedrecs.txt` files) and EBSCOhost CSV (`ebsco`)
  exports as well as PubMed's MEDLINE, and merges them by DOI: a paper in several
  exports is one row whose `sources` lists them all. Keywords are the authors' own
  (EBSCO's subject terms); Scopus index keywords, Web of Science Keywords Plus and
  each database's record id are kept in their own columns. DOIs given as
  `https://doi.org/...` links are matched too, and of two DOIs joined by "; " the
  first is used. A journal name ending in its ISSN in brackets (as EBSCO writes it)
  loses it to an `issn` column. Web of Science lines that cannot be split are
  reported as `unreadable_records`.
- **Citations fetched for any corpus:** `scientographer init` now puts the
  `fetch_references` stage in front of the pipeline, so a papers table with DOIs is
  enough; the citations between the papers come from OpenAlex. Projects that bring
  their own references table use `init --own-references`.
- `init` on an existing project keeps its `dvc.yaml` and `params.yaml` as they are
  (`--force` starts it again).
- **LinLog and 3D layouts:** a new `citation_layouts` stage runs ForceAtlas2 again,
  in LinLog mode and/or in 3D, and the map shows each result as another view; 3D
  views open in a 3D viewer (three.js, loaded only for them) with the same legend,
  search, filters and paper panels. Every layout is lined up with the main 2D map.
  New projects compute the standard layout in 3D and the site opens on it
  (`website.default_view`); numba joins the base install for exact, multi-core
  layouts. The example also shows LinLog in 2D and 3D.
- The default resolution sweep gains 0.0015 (37 values).
- **Fixed:** on a map whose graph was not laid out in Gephi, every paper showed 0
  citations, the same tiny dot, and community top-paper lists in arbitrary order:
  the site read only Gephi's own in-degree, degree and size columns. It now counts
  them from the graph's citations when those columns are missing.
- A **Dot size** slider in every view.
- **Search and filters:** `|` means OR and quoted text is matched literally (commas,
  brackets and all), so `"First title" | "Second title"` finds exactly those papers;
  a comma still means AND.
- **Logo:** the Scientographer logo (`docs/logo/`, CC BY-ND 4.0) heads the README, and
  every map shows "Made with" and the logo while it loads (for at least 1 s), then
  a round compass badge linking to the project; the compass is also the map's
  browser-tab icon. The Guided tour button moves to the top right.
- **Bridge papers work** (the toggle and badge never did anything): a bridge paper
  has at least 10 citation links and a participation coefficient above 0.62 at the
  current resolution, shown in the paper panel; **top papers by four rankings**
  (citations, citations within the community, citations per year, PageRank), in
  the community panel and as a new "Highlight top papers" control.
- **Bridge papers** are now the 50 papers with the highest participation coefficient
  × ln(links) (at least 10 links) at the current resolution, instead of every paper
  with a coefficient above 0.62: at a fine resolution that rule marked thousands
  (3,033 of 22,982 papers on the motor-learning map at γ = 0.0015).

## [0.1.0] - 2026-10-09

The first release.

- **Pipeline:** citation graph from two tables; Leiden/CPM communities over a
  resolution sweep, stored per resolution so adding one computes only that one;
  quality metrics (modularity, CPM score, surprise, significance, coverage,
  cross-seed stability, plateaus; per-community conductance, density and surprise);
  well-connectedness and the Connectivity Modifier; keyword labels; word clouds;
  ForceAtlas2 layout with a reproducible default orientation.
- **Corpus from PubMed:** `scientographer init --pubmed QUERY` searches PubMed,
  reads the MEDLINE records and fetches the citations among them from OpenAlex.
- **Text-embedding maps** (optional): Gemini and SPECTER2 embeddings, BERTopic
  topics, UMAP layouts and time snapshots.
- **Interactive map:** views per layout, colouring by community at any resolution or
  by topic, well-connected filter, whole-word/regex search and filters (MeSH terms
  for PubMed corpora), Word map heatmaps, high-resolution PNG export.
- **Tooling:** `scientographer init`, `example`, `run`, `pipeline`, `website`,
  `store-existing-results`, `ui`; DVC stages and experiments; Hamilton UI tracking;
  a bundled open-access example; an agent skill.
- **Install:** `pip install scientographer` runs every stage of a project; the
  `embeddings` and `ui` extras add the text-embedding maps and the Hamilton UI.

[Unreleased]: https://github.com/alfredohernandezinostroza/scientographer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alfredohernandezinostroza/scientographer/releases/tag/v0.1.0
