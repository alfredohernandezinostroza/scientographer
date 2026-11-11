# Changelog

All notable changes to Scientographer. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change
settings or outputs).

## [Unreleased]

- **Search and filters use the query syntax of PubMed and Scopus:** `AND`, `OR`
  (or `|`) and `NOT` in capitals, brackets to group, quotes for an exact phrase:
  `(speech OR language) AND children NOT apraxia`. A space between words now means
  AND (it was a phrase: quote `"motor learning"` for that), and a comma is no longer
  an operator: outside quotes it separates words like a space, inside quotes it is
  matched literally (`"Wolpert, Daniel"`). `&` is plain text. A malformed query says
  what is wrong ("Invalid query: a bracket is not closed"). The map's search box
  tests the title and authors together, and the Word map tab uses the same syntax,
  its "Abstract + keywords" column testing the query on both texts together.

- **The query as it is searched** shows under each filter as you type, in the chosen
  matching mode: `(speech | speeches) AND (child | children) AND NOT (apraxia |
  apraxias)`, or what is wrong with it. Whole-word matching takes singular and
  plural alike, whichever is typed (*sports* finds *sport*, *studies* finds *study*),
  no longer searches made-up forms (*speechs*, *sportses*), leaves words like
  *series* and *kinematics* as they are, and knows a few irregular plurals common in
  the literature (child/children, mouse/mice, stimulus/stimuli, criterion/criteria).
- **Heatmap enrichment:** the density heatmap's new "Colour by" menu shows where the
  selected papers are over-represented rather than where they are: at each place,
  their share of the density over the share of every paper the view shows with the
  filters off (`kde.js` `enrichmentGrid`; 1× = the same share as anywhere). Coloured
  on a log scale from 1× (and below: nothing) to the largest ratio, which the colour
  bar and the status line give as a multiple; where the map has under 3% of its
  peak density the colour fades out, a ratio of near-zeros being noise. Both
  densities share one grid, 6 bandwidths beyond the selection, so the papers around
  its edge count. On the motor-learning map, `speech` in the keywords peaks at 13×
  on the citation map and 48× on the Gemini map, matching the exact sums.
- **Density heatmap** on the 2D views: a "Density heatmap" switch under the filters
  draws a heatmap (Gaussian kernel density estimate, `kde.js`) of exactly the papers
  counted in "Nodes selected" (text filters, years, well-connected papers, hidden
  communities, time snapshot), over or under the dots, with its bandwidth, opacity, a
  colour bar and rings around the papers. It is computed once per selection on a grid
  of cells a sixth of the bandwidth wide, in the layout's own coordinates, and drawn
  through the map's camera and its rotation and flips, so it stays sharp and locked to
  the dots at any zoom and its colours do not shift. The bandwidth is a share of the
  box holding the central 99% of the view's papers, so no filter changes it. Each paper
  counts once (presence), or by its mentions of the searched words (as is, their log,
  or per 1,000 words). "Show all papers under the heat" draws the papers the filters
  leave out faded instead of hiding them. The heat hides while the filters are off.
- **Filters survive a view switch:** switching views (for example from the citation
  network to an embedding map) applies the filters again instead of silently dropping
  them while their boxes still showed them.
- **Map edges** are coloured by the citing paper, like the PNG export, instead of
  one grey, with **Edge opacity** (up to 1) and **Edge thickness** (from 0.05 pixels)
  sliders. Fixed: overlapping edges no longer add up to white on the 2D maps (sigma
  blends premultiplied colours, which the edge colours were not), and edges can be
  thinner than sigma's 1.7-pixel minimum.
- **Filters on and off:** a switch under "Nodes selected" turns the text filters and
  the year range off, bringing every paper back while the boxes keep what was typed,
  and on again, to compare the filtered map with the whole one. `F` does the same,
  and holding Space shows the unfiltered map until it is let go. Well-connected
  papers, hidden communities and the time snapshot stay as they are.
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
- **Colours of your own:** each legend (communities, topics) has a **Palette** menu,
  the original or a published palette (Kelly, Tableau 20, CARTO Bold, Vivid, Prism,
  Safe, Pastel, Antique, ColorBrewer Set3 and Paired, Okabe–Ito), so communities and
  topics need not share colours (they did: the same list, by group number, which
  suggested a correspondence between them). Clicking a group's colour opens a colour
  picker drawn by the page (`colorpicker.js`; the browser's own handed over to the
  operating system's dialog in some browsers): a colour square, hue, hex, the
  palette, 16 slots for your own colours ("Save…" asks which slot to fill or
  replace), "Pick from screen" where the browser has an eyedropper, and outside it
  the pointer is an eyedropper itself (hover a dot to preview, click a dot, a legend
  colour or a label to take it). Dots, citation edges, labels, panels and exports
  follow. Palettes and colours are kept in the browser (per site), each colour with
  its group's name, so a rebuilt map whose groups came out differently never paints
  it on the wrong one; **Reset colours** goes back to the site's.
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
- The journal filter is the last of the map's filters, after keywords (and MeSH terms).
- **Each view opens on its own colouring:** an embedding map (Gemini, SPECTER2) on
  its topics, a citation layout (the main map, LinLog, 3D) on its communities, so
  a map shows its own structure first. What the viewer picks in "Color by" wins and
  carries over to every view that offers it. `views.json` says each view's colouring
  (`color_by`, from `website.extra_layouts`: a layout's `color_by`, or its topics
  when it brings some); sites built before fall back to the same rule.
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
- The PNG export starts on curved edges and labels around the map.
- **Citations fetched for any corpus:** `scientographer init` now puts the
  `fetch_references` stage in front of the pipeline, so a papers table with DOIs is
  enough; the citations between the papers come from OpenAlex. Projects that bring
  their own references table use `init --own-references`.
- `init` on an existing project keeps its `dvc.yaml` and `params.yaml` as they are
  (`--force` starts it again).
- **Typed slider values:** click a slider's value (dot size, edge opacity and
  thickness, the years, the Word map's bandwidth) to type an exact one; Enter applies
  it, Escape cancels. The year readout takes both ends (`1990-2010`).
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
- **All or any filter:** a switch under the map's filters chooses whether a paper
  must match all the filled text filters (as before) or any of them, so a word can be
  looked for in the abstract or the keywords. Years, well-connected papers and hidden
  communities always apply.
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
