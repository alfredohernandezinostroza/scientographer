# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PubMed's MEDLINE text format: what `search_pubmed` downloads and what PubMed's
"Save > Format: PubMed" (.nbib / .txt) gives.

A record is a run of ``TAG - value`` lines (the tag padded to four characters);
a value continues on following lines indented by six spaces; records are
separated by blank lines and each starts with ``PMID-``. Repeated tags (AU, MH,
OT, ...) give lists. Specification:
https://www.nlm.nih.gov/bsd/mms/medlineelements.html
"""

from collections.abc import Iterable, Iterator
import re

_TAG_LINE = re.compile(r"^([A-Z][A-Z0-9 ]{1,3})- ?(.*)$")
_CONTINUATION = " " * 6


def records(lines: Iterable[str]) -> Iterator[dict[str, list[str]]]:
    """Each record as {tag: [values...]}, tags stripped of padding."""
    record: dict[str, list[str]] = {}
    tag = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith(_CONTINUATION) and tag is not None:
            record[tag][-1] += " " + line.strip()
            continue
        match = _TAG_LINE.match(line)
        if not match:
            continue  # stray text (e.g. a header); MEDLINE has nothing else
        tag, value = match.group(1).strip(), match.group(2).strip()
        if tag == "PMID" and record:
            yield record
            record = {}
        record.setdefault(tag, []).append(value)
    if record:
        yield record


def split_by_pmid(text: str) -> dict[str, str]:
    """{pmid: the record's raw text}, to keep or drop whole records unchanged."""
    out: dict[str, str] = {}
    current: list[str] = []
    pmid = None
    for line in text.splitlines():
        if line.startswith("PMID-"):
            if pmid is not None:
                out[pmid] = "\n".join(current).strip() + "\n"
            pmid, current = line[5:].strip(), [line]
        elif pmid is not None:
            current.append(line)
    if pmid is not None:
        out[pmid] = "\n".join(current).strip() + "\n"
    return out


def doi(record: dict[str, list[str]]) -> str:
    """The article's DOI (from AID or LID ``... [doi]``), lower-cased, or ''."""
    for tag in ("AID", "LID"):
        for value in record.get(tag, []):
            if value.endswith("[doi]"):
                return value[: -len("[doi]")].strip().lower()
    return ""


def year(record: dict[str, list[str]]):
    """Publication year from DP ("2020 Feb 1", "2019 Winter", "2018-2019"), or None."""
    for value in record.get("DP", []):
        match = re.match(r"\s*(\d{4})", value)
        if match:
            return int(match.group(1))
    return None


def mesh_major_topics(record: dict[str, list[str]]) -> list[str]:
    """MeSH headings marked as a major topic of the article (``*``), without
    their subheadings: "Motor Skills/*physiology" -> "Motor Skills"."""
    out = []
    for value in record.get("MH", []):
        if "*" not in value:
            continue
        heading = value.split("/")[0].lstrip("*").strip()
        if heading and heading not in out:
            out.append(heading)
    return out


def to_paper(record: dict[str, list[str]], keywords_from: str = "author_then_mesh") -> dict:
    """One row of the papers table. `keywords_from`: "author" (the authors' own
    keywords, OT), "mesh" (MeSH major topics) or "author_then_mesh" (the authors'
    keywords, or the MeSH major topics for a paper without any)."""
    author_keywords = list(dict.fromkeys(v for v in record.get("OT", []) if v))
    mesh = [v.split("/")[0].lstrip("*").strip() for v in record.get("MH", [])]
    if keywords_from == "author":
        keywords = author_keywords
    elif keywords_from == "mesh":
        keywords = mesh_major_topics(record)
    elif keywords_from == "author_then_mesh":
        keywords = author_keywords or mesh_major_topics(record)
    else:
        raise ValueError(f"keywords_from must be author, mesh or author_then_mesh, not {keywords_from!r}")
    first = lambda tag: (record.get(tag) or [""])[0]
    return {
        "doi": doi(record),
        "pmid": first("PMID"),
        "title": first("TI") or first("BTI"),
        "abstract": " ".join(record.get("AB", [])),
        "authors": record.get("FAU") or record.get("AU") or [],
        "keywords": keywords,
        "mesh": list(dict.fromkeys(m for m in mesh if m)),
        "journal": first("JT") or first("TA"),
        "year": year(record),
        "publication_types": record.get("PT", []),
    }
