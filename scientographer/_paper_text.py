# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The text each paper is embedded from: its cleaned title and abstract.

HTML entities are decoded, copyright / publisher boilerplate is cut, structured-
abstract headings ("Background:", "METHODS -") are removed, whitespace is
normalised, and placeholder fields ("No abstract available") count as missing.
The embedding text is ``Title: {title}\nAbstract: {abstract}`` (or whichever of
the two exists); a paper with neither is skipped. Nothing citation-derived
(authors, venue, year, references) enters it, so the text view stays
independent of the citation view it is compared with.

Ported verbatim from the motor-learning study's embedding-space work
(preprocess_graphml_text_for_embeddings.py), so vectors embedded from it stay
valid.
"""

import hashlib
import html
import math
import re
from typing import Final, Optional

import igraph as ig
import pandas as pd

# Status labels for per-field availability.
ST_AVAILABLE: Final[str] = "available"
ST_MISSING: Final[str] = "missing"
ST_PLACEHOLDER: Final[str] = "placeholder"

# A field is treated as unavailable ("placeholder") if, after trimming and
# stripping surrounding brackets/punctuation, it equals one of these (case-
# insensitive). Matched against the WHOLE field only -- never scanned inside real
# text -- so sentences containing e.g. "was not available to the ..." are safe.
_ABSTRACT_PLACEHOLDERS: Final[set[str]] = {
    "no abstract available", "abstract not available", "abstract unavailable",
    "not available", "n/a", "na", "none", "null",
}
_TITLE_PLACEHOLDERS: Final[set[str]] = {
    "no title available", "title not available", "title unavailable", "untitled",
    "not available", "n/a", "na", "none", "null",
}

# Copyright sign in literal or HTML-escaped form -- marks the start of publisher
# boilerplate so everything from there on is dropped (matched BEFORE decoding).
_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|&#0*169;|&#x0*A9;)", re.IGNORECASE)

# Structured-abstract section headings, normalized away only when they appear as
# a standalone label (heading word immediately followed by a separator).
_HEADING_WORDS: Final[list[str]] = [
    "Background", "Backgrounds", "Objective", "Objectives", "Aim", "Aims",
    "Method", "Methods", "Methodology", "Materials and Methods",
    "Material and Methods", "Result", "Results", "Discussion", "Discussions",
    "Conclusion", "Conclusions", "Significance", "Introduction", "Purpose",
    "Design", "Setting", "Settings", "Participants", "Intervention",
    "Interventions", "Findings", "Finding", "Implication", "Implications",
    "Outcome", "Outcomes", "Measurements", "Main Outcome Measures", "Context",
    "Rationale",
]
# Longest-first so multi-word headings match before their single-word prefixes.
_HEADING_ALT = "|".join(re.escape(w) for w in sorted(_HEADING_WORDS, key=len, reverse=True))
_HEADING_RE = re.compile(
    r"(?:(?<=^)|(?<=[.;!?])\s+|(?<=\n)|(?<=\s))"   # boundary before heading
    r"[\[(]?\s*"                                    # optional opening bracket
    r"(?:" + _HEADING_ALT + r")"                    # the heading word(s)
    r"\s*[\])]?\s*"                                 # optional closing bracket
    r"(?::|–|—|-)\s+",                              # required separator
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


#####################
##  Aux Functions  ##
#####################
def _decode_entities(text: str) -> str:
    """Decode HTML entities (``&amp;`` -> ``&``); run twice for double-encoding."""
    if not text:
        return text
    out = html.unescape(text)
    if "&" in out:
        out = html.unescape(out)
    return out


def _strip_copyright(text: str) -> tuple[str, bool]:
    """Remove the copyright symbol and everything after it. Returns (text, truncated)."""
    if not text:
        return text, False
    m = _COPYRIGHT_RE.search(text)
    if m is None:
        return text, False
    return text[: m.start()], True


def _normalize_headings(text: str) -> tuple[str, bool]:
    """Strip standalone structured-abstract heading labels, keeping the prose after."""
    if not text:
        return text, False
    new_text, n = _HEADING_RE.subn(" ", text)
    return new_text, n > 0


def _normalize_whitespace(text: str) -> str:
    """Collapse whitespace runs to a single space and strip ends."""
    if not text:
        return text
    return _WHITESPACE_RE.sub(" ", text).strip()


def _normalized_for_placeholder_check(text: str) -> str:
    """Lowercased, bracket/quote/period-stripped form used only for placeholder matching."""
    s = text.strip()
    s = re.sub(r"^[\[\(\{]+|[\]\)\}]+$", "", s).strip()
    s = s.strip("\"'").strip()
    s = s.rstrip(".").strip()
    return s.lower()


def _clean_field(raw: Optional[str], placeholders: set[str]) -> tuple[str, str, dict]:
    """Clean one title/abstract field. Returns (clean_text, status, flags)."""
    flags = {"copyright_truncated": False, "heading_normalized": False}
    if raw is None or not raw.strip():
        return "", ST_MISSING, flags

    # Remove copyright boilerplate FIRST (catches escaped forms before decoding).
    text, truncated = _strip_copyright(raw)
    flags["copyright_truncated"] = truncated
    text = _decode_entities(text)
    # A copyright sign may only surface after decoding (rare) -- handle again.
    text2, truncated2 = _strip_copyright(text)
    if truncated2:
        text, flags["copyright_truncated"] = text2, True

    text, heading_changed = _normalize_headings(text)
    flags["heading_normalized"] = heading_changed
    text = _normalize_whitespace(text)

    if not text:
        return "", ST_MISSING, flags
    if _normalized_for_placeholder_check(text) in placeholders:
        return "", ST_PLACEHOLDER, flags
    return text, ST_AVAILABLE, flags


def paper_texts(graph: ig.Graph) -> pd.DataFrame:
    """One row per paper of the graph with any usable text: doi (the vertex
    name, lower-cased), year, title_clean, abstract_clean, embedding_text and
    text_sha256 (what an embedding of it is keyed on)."""
    rows = []
    names = graph.vs["name"]
    years = graph.vs["year"] if "year" in graph.vs.attributes() else [None] * graph.vcount()
    titles = graph.vs["title"] if "title" in graph.vs.attributes() else [None] * graph.vcount()
    abstracts = graph.vs["abstract"] if "abstract" in graph.vs.attributes() else [None] * graph.vcount()
    for name, year, title_raw, abstract_raw in zip(names, years, titles, abstracts):
        title, title_status, _ = _clean_field(title_raw, _TITLE_PLACEHOLDERS)
        abstract, abstract_status, _ = _clean_field(abstract_raw, _ABSTRACT_PLACEHOLDERS)
        if title_status == ST_AVAILABLE and abstract_status == ST_AVAILABLE:
            text = f"Title: {title}\nAbstract: {abstract}"
        elif title_status == ST_AVAILABLE:
            text = f"Title: {title}"
        elif abstract_status == ST_AVAILABLE:
            text = f"Abstract: {abstract}"
        else:
            continue
        rows.append({
            "doi": str(name).strip().lower(),
            "year": None if year is None or math.isnan(float(year)) else int(year),
            "title_clean": title or None,
            "abstract_clean": abstract or None,
            "embedding_text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    return pd.DataFrame(rows, columns=["doi", "year", "title_clean", "abstract_clean", "embedding_text", "text_sha256"])
