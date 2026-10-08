# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One embedding model's vectors, one per paper, kept between runs.

A store is a directory holding ``vectors.npy`` (float32, one row per paper),
``papers.parquet`` (doi and text_sha256 for each row, in the same order) and
``model.json`` (provider, model, dimensions). A vector is reused only for the
same paper (DOI) with the same text (its SHA-256) and the same model, so
embedding is resumable and incremental: a run embeds only papers that are new
or whose text changed. Writes are atomic (temporary files, then rename).
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

_VECTORS = "vectors.npy"
_PAPERS = "papers.parquet"
_MODEL = "model.json"


class EmbeddingStore:
    def __init__(self, directory: Path, model: dict):
        self.directory = Path(directory)
        self.model = {k: model[k] for k in ("provider", "model", "dimensions") if k in model}
        self._vectors: dict[tuple[str, str], np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        try:
            stored_model = json.loads((self.directory / _MODEL).read_text(encoding="utf-8"))
            papers = pd.read_parquet(self.directory / _PAPERS)
            vectors = np.load(self.directory / _VECTORS, allow_pickle=False)
        except (OSError, ValueError):
            return
        if stored_model != self.model or len(papers) != len(vectors):
            return  # another model, or a damaged store: start afresh
        for doi, sha, vector in zip(papers["doi"], papers["text_sha256"], vectors):
            self._vectors[(doi, sha)] = vector

    def get(self, doi: str, text_sha256: str):
        return self._vectors.get((doi, text_sha256))

    def missing(self, texts: pd.DataFrame) -> pd.DataFrame:
        """The rows of `texts` (doi, text_sha256, ...) without a stored vector."""
        have = [(d, s) in self._vectors for d, s in zip(texts["doi"], texts["text_sha256"])]
        return texts[~np.asarray(have, dtype=bool)]

    def put(self, doi: str, text_sha256: str, vector) -> None:
        vector = np.asarray(vector, dtype=np.float32)
        if "dimensions" in self.model and vector.shape != (int(self.model["dimensions"]),):
            raise ValueError(f"vector for {doi} has shape {vector.shape}, expected ({self.model['dimensions']},)")
        self._vectors[(doi, text_sha256)] = vector

    def matrix(self, texts: pd.DataFrame) -> np.ndarray:
        """The vectors of `texts`' papers, in its row order (all must be stored)."""
        return np.stack([self._vectors[(d, s)] for d, s in zip(texts["doi"], texts["text_sha256"])])

    def save(self, keep: pd.DataFrame | None = None) -> int:
        """Write the store (only the papers in `keep`, if given). Returns the row count."""
        items = list(self._vectors.items())
        if keep is not None:
            wanted = set(zip(keep["doi"], keep["text_sha256"]))
            items = [(key, vector) for key, vector in items if key in wanted]
        items.sort(key=lambda kv: kv[0])
        self.directory.mkdir(parents=True, exist_ok=True)
        papers = pd.DataFrame([key for key, _ in items], columns=["doi", "text_sha256"])
        vectors = np.stack([v for _, v in items]) if items else np.zeros((0, int(self.model.get("dimensions", 0))), np.float32)
        tmp = {name: self.directory / f".{name}.tmp" for name in (_VECTORS, _PAPERS, _MODEL)}
        with open(tmp[_VECTORS], "wb") as f:
            np.save(f, vectors, allow_pickle=False)
        papers.to_parquet(tmp[_PAPERS], index=False)
        tmp[_MODEL].write_text(json.dumps(self.model, sort_keys=True), encoding="utf-8")
        for name, path in tmp.items():
            os.replace(path, self.directory / name)
        return len(papers)
