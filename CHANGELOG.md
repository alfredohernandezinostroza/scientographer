# Changelog

All notable changes to Scientographer. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
settings or outputs).

## [0.1.0] - unreleased

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

[0.1.0]: https://github.com/alfredohernandezinostroza/scientographer/releases/tag/v0.1.0
