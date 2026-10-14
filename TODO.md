# To do

Ideas and plans for Scientographer, in no fixed order: move the important ones up.
Finished items stay at the bottom until the next release, then go to the CHANGELOG.

## Website

- [ ] README: a live example (the motor-learning CitationMap site)
- [ ] 3D views: dark grey for "No community"
- [ ] 3D views: a "Reset view" button
- [ ] Dot size: a "Same size" option
- [ ] 3D views: time snapshots and PNG export (2D only for now)
- [ ] Communities whose keyword names come out the same: tell them apart in the legend
- [ ] Word heatmaps (the Word map tab's densities) as a filter in the map views: 2D, 3D,
      LinLog, all of them. Needs a way to handle the density (KDE) in 3D and on each layout
- [ ] Journal reputation: hold journal ranking metadata (SCImago, Web of Science, other
      reputable sources), filter by it on the map, and colour papers by it

## Pipeline and analysis

- [ ] Layouts: an option for ForceAtlas2's strong gravity, to pull detached groups of
      papers in towards the map
- [ ] Embedding topics: choose `min_cluster_size` by a stability sweep instead of a fixed
      value
- [ ] More ingest formats: RIS, BibTeX, Web of Science plain text, Dimensions, Lens
- [ ] Gemini-only topics, without BERTopic and PyTorch

## Provenance: how a map was made

- [ ] An "About this map" panel on the site: Scientographer version, build date, every
      stage with its settings, the input files with their checksums (from `params.yaml`
      and `dvc.lock`), and the pipeline's stage graph (`dvc dag`)
- [ ] Each GraphML file records its own history: every stage appends what it did
      (stage, version, settings, input checksums) to a graph-level attribute
- [ ] The stages' Hamilton DAG drawings in the site's Figures tab (already drawn to
      `reports/figures/`)
- [ ] Where each paper came from: a registry of searches (database, query, date,
      filters, export file), each with an id; every paper keeps all the routes that
      brought it in (which searches; or, by snowballing, which paper's references or
      citations, how many steps from which search); merging duplicates merges their
      routes. Filter and colour the map by origin
- [ ] Citation snowballing (backward: references; forward: citing papers) as a way to
      build or extend a corpus
- [ ] A PRISMA-style flow on the site: records per source, duplicates removed, papers
      in the graph

## Project

- [ ] Release 0.2.0 (PyPI and Zenodo)
- [ ] CI: cancel outdated runs (`concurrency` in `tests.yml`)
- [ ] GitHub social preview image (the logo)

## Done since 0.1.0

- [x] The Zenodo DOI in the README ("How to cite") and CITATION.cff (#4)
- [x] Citations fetched from OpenAlex for any corpus, not only PubMed;
      `init --own-references` for corpora that bring their own (#5)
- [x] `init` keeps an existing project's `dvc.yaml` and `params.yaml` (#5)
- [x] Search and filters: `|` means OR, quoted text is matched literally (#6)
- [x] 3D and LinLog views: the `citation_layouts` stage, a three.js viewer for 3D
      views, layouts lined up with the 2D map (#7)
- [x] The map opens on the standard 3D view; auto-rotate, turn and tilt buttons (#7)
- [x] Dot size slider in every view (#7)
- [x] Fixed: 0 citations and equal dots on maps not laid out in Gephi (#7)
- [x] 0.0015 in the default resolution sweep (#7)
- [x] The logo: README, "Made with" while the map loads, the compass badge and
      favicon; Guided tour moved to the top right (#8)
- [x] Rulesets: `main` protected (pull requests, status checks, no force pushes or
      deletions); a repository setting, no PR
- [x] Ingest reads Scopus CSV, Web of Science tab-delimited and EBSCOhost CSV exports,
      merged by DOI with PubMed's MEDLINE
