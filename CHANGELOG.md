# Changelog

All notable changes to Scientographer. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
settings or outputs).

## [Unreleased]

- **Citations fetched for any corpus:** `scientographer init` now puts the
  `fetch_references` stage in front of the pipeline, so a papers table with DOIs is
  enough; the citations between the papers come from OpenAlex. Projects that bring
  their own references table use `init --own-references`.
- `init` on an existing project keeps its `dvc.yaml` and `params.yaml` as they are
  (`--force` starts it again).
- **LinLog and 3D layouts:** a new `citation_layouts` stage runs ForceAtlas2 again,
  in LinLog mode and/or in 3D, and the map shows each result as another view; 3D
  views open in a 3D viewer (three.js, loaded only for them) with the same legend,
  search, filters and paper panels. New `fast-layout` extra (numba) for exact,
  multi-core layouts. The example shows the standard layout in 3D and LinLog in 2D and 3D.
- The default resolution sweep gains 0.0015 (37 values).
- **Fixed:** on a map whose graph was not laid out in Gephi, every paper showed 0
  citations, the same tiny dot, and community top-paper lists in arbitrary order:
  the site read only Gephi's own in-degree, degree and size columns. It now counts
  them from the graph's citations when those columns are missing.
- A **Dot size** slider in every view.

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
