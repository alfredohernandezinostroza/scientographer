# Sources and licenses of the example data

## `papers.parquet` — 3,925 open-access articles

Each row is one article published under a **Creative Commons Attribution (CC BY)**
license, as reported by Europe PMC. The table was built on 2026-09-28 from the
[Europe PMC REST API](https://europepmc.org/RestfulWebService) with the query

    ("motor adaptation" OR "visuomotor adaptation" OR "motor skill learning"
     OR "motor sequence learning") AND OPEN_ACCESS:y AND LICENSE:"cc by"
     AND HAS_ABSTRACT:y AND KW:*

**Attribution.** The copyright of each title, abstract and keyword list belongs to the
article's authors (or their assignees), who released them under CC BY. Each row
credits its work: `title`, `authors`, `journal`, `year`, `doi` (the link to the
original) and `pmcid`, and states its `license`. CC BY exists in several versions
(2.0, 3.0, 4.0); see each article, via its DOI, for the exact one.

**Changes.** HTML markup was removed from titles and abstracts and whitespace was
normalised; the text is otherwise unchanged. `keywords` are the authors' own keywords
exactly as Europe PMC lists them (no indexer-assigned MeSH terms). 1,121 articles have
no author keywords and appear with an empty list.

## `references.parquet` — citations between those articles

For each article, the articles of this set that it cites, taken from
[OpenAlex](https://openalex.org) (`referenced_works`), whose data is released under
**CC0** (public domain). Only citations where both ends are in the set are kept:
6,860 citations from 2,322 citing articles.
