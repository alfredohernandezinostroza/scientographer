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
- [ ] Bridge papers: a "rule" menu next to "Highlight bridge papers", offering several
      definitions instead of the one fixed rule. Today (`rankings.js`): at least 10
      citation links (`BRIDGE_MIN_LINKS`) and participation coefficient P > 0.62
      (`BRIDGE_MIN_PARTICIPATION`), P = 1 - sum over communities c of (k_ic / k_i)^2,
      at the current resolution. The options, with how many of the 22,982 papers of the
      motor-learning map each marks at γ = 0.0015:
      - the current rule (>= 10 links, P > 0.62): 3,033 (13.2%), too many to highlight
      - P > 0.75 (>= 10 links): 1,089 (4.7%)
      - P > 0.80 (>= 10 links), Guimerà & Amaral's "kinless" nodes: 521 (2.3%)
      - **hub rule** (recommended default): within-community z-score >= 2.5 and P > 0.30,
        Guimerà & Amaral's connector and kinless hubs: 419 (1.8%). The z-score of paper i
        in community c: (its links inside c - the mean of that over c's papers) / the
        standard deviation over c's papers (0 when c has one paper or no spread)
      - hubs with P > 0.62: 163 (0.7%)
      - the top 1% by P among papers with >= 10 links: 102 (0.4%)

      Compute z alongside P (one pass over the citations, per resolution) and show both
      in the paper panel. Counts change with the resolution (the current rule: 861 at
      0.0005, 6,108 at 0.01). Reference: Guimerà & Amaral (2005), Functional
      cartography of complex metabolic networks, Nature 433, 895-900.

## Pipeline and analysis

- [ ] Layouts: an option for ForceAtlas2's strong gravity, to pull detached groups of
      papers in towards the map
- [ ] Embedding topics: choose `min_cluster_size` by a stability sweep instead of a fixed
      value
- [ ] More ingest formats: RIS, BibTeX, Web of Science plain text, Dimensions, Lens
- [ ] `scientographer init --exports`: put the `ingest` stage in the pipeline for a
      corpus built from database exports (today only `--pubmed` does), so `dvc repro`
      reads the exports too and reruns when one changes; now `scientographer run
      ingest` has to be run by hand first
- [ ] Gemini-only topics, without BERTopic and PyTorch

## Provenance: how a map was made

Two kinds: how the map was processed and where each paper came from (further down).
Every stage now writes a record of its step (stage, exact version, settings, input
md5s, outputs, date) that travels with its outputs, and the site's "About this map"
panel lists them. Still to do on that side:

- [ ] `scientographer status`: the stages that ran on an older version than the one
      installed (upgrading reruns nothing by itself, since the code is not a DVC
      dependency; `dvc repro -f <stage>` does)
- [ ] "About this map": the pipeline's stage graph, drawn from the records' inputs and
      outputs (or `dvc dag`), and each stage's Hamilton DAG drawing (drawn to
      `reports/figures/`, not yet shipped with the site)
- [ ] The exact environment for a rebuild: the project's `pixi.lock`, or a
      `pip freeze` written next to the site when there is none

Old maps: each site carries its own copy of the page code, so it keeps working after
the website changes; keeping old maps is the project's job (its git and DVC history,
or the published site's repository). Maybe later, an opt-in archive: `site/<build>/`
per build and a "previous versions" list on the page.

Where each paper came from:

- [ ] A registry of searches (database, query, date, filters, export file), each with
      an id; every paper keeps all the routes that brought it in (which searches; or,
      by snowballing, which paper's references or citations, how many steps from
      which search); merging duplicates merges their routes. Filter and colour the
      map by origin. The ingest step's record lists the searches it read
- [ ] Citation snowballing (backward: references; forward: citing papers) as a way to
      build or extend a corpus
- [ ] A PRISMA-style flow on the site: records per source, duplicates removed, papers
      in the graph

## Project

- [ ] Release 0.2.0 (PyPI and Zenodo)
- [ ] CI: a docs-only pull request (Markdown, `docs/`) finishes the test check in
      seconds instead of running the suite; it must still report a result, since the
      check is required (`paths-ignore` would leave it pending forever)

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
- [x] Bridge papers work: participation coefficient at the current resolution
      (Guimerà & Amaral), in the highlight and the paper panel
- [x] Top papers by four rankings (citations, citations within the community,
      citations per year, PageRank), in the community panel and a "Highlight top
      papers" control (#13)
- [x] Ingest reads Scopus CSV, Web of Science tab-delimited and EBSCOhost CSV exports,
      merged by DOI with PubMed's MEDLINE (#12)
- [x] CI cancels outdated runs of a pull request (#11)
- [x] GitHub social preview image, `docs/logo/scientographer-social-preview.png`
      (#10; it goes live once uploaded in Settings → Social preview)
- [x] `scientographer --version` prints the exact version: the release, or the release
      plus the commit (and `.dirty`) from a git checkout
- [x] Every stage records its step, carried with its outputs (GraphML attribute,
      parquet metadata, `provenance.json` in output folders), from search/ingest to
      the site
- [x] The site shows its version and build date by the compass badge, which opens
      "About this map: how it was made": every step, what it read and from which
      step, its settings, and warnings for mixed versions or uncommitted changes. The
      controls' old "About this map" section is now "Help"
