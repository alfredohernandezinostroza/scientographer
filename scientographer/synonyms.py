"""Keyword synonym groups, shared by the keyword stages.

A synonym file is a JSON object ``{"Canonical Term": ["variant", "Variant 2", ...]}``:
every variant is rewritten to its canonical term before keywords are counted, so
"motor adaptation" and "sensorimotor adaptation" can count as one term if you say
so. The file is optional (``synonyms_file: null``): without it every keyword is its
own term. ``extra_synonyms`` in params.yaml adds variants on top of the file, with
the same shape, so a one-off alias doesn't require editing a generated file.
"""

import json
from pathlib import Path
from typing import Optional


def load_synonym_groups(path: Optional[Path], extra_synonyms: Optional[dict] = None) -> dict[str, list[str]]:
    """The synonym groups from ``path`` (missing or None -> none), merged with
    ``extra_synonyms`` (variants appended to the canonical term's list)."""
    groups: dict[str, list[str]] = {}
    if path is not None and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must be a JSON object of canonical term -> list of variants")
        groups = {str(k): list(v) for k, v in loaded.items()}
    for canonical, variants in (extra_synonyms or {}).items():
        groups.setdefault(str(canonical), []).extend(str(v) for v in variants)
    return groups
