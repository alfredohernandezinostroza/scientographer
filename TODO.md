# To do

Ideas and plans for Scientographer, in no fixed order: move the important ones up.
Finished items stay at the bottom until the next release, then go to the CHANGELOG.

## Website

- [ ] README: a live example (the motor-learning CitationMap site)
- [ ] 3D views: dark grey for "No community"
- [ ] 3D views: a "Reset view" button
- [ ] Dot size: a "Same size" option
- [ ] 3D views: time snapshots and PNG export (2D only for now)
- [ ] Communities that share a keyword name (two "Cerebellum" at γ = 0.0015): tell them
      apart in the legend

## Pipeline and analysis

- [ ] Detached communities far from the map: filter them out, or pull them in with
      ForceAtlas2's strong gravity (0.003)
- [ ] Topic granularity: replace the fixed `min_cluster_size` with a stability sweep
- [ ] SPECTER2: check its `min_cluster_size`
- [ ] More ingest formats
- [ ] Gemini-only topics, without BERTopic and PyTorch

## Project

- [ ] Release 0.2.0 (PyPI and Zenodo)
- [ ] Rulesets: protect `main` (pull requests, status checks, no force pushes or
      deletions)
- [ ] CI: cancel outdated runs (`concurrency` in `tests.yml`)
- [ ] GitHub social preview image (the logo)
- [ ] ORCID: connect DataCite, so the Zenodo records appear on the profile

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
