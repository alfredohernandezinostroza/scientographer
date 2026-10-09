# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paper text, embedding stores, incremental embedding and embedding maps."""

import json

import igraph as ig
import numpy as np
import pandas as pd
import pytest

from scientographer._embedding_store import EmbeddingStore
from scientographer._paper_text import paper_texts

MODEL = {"provider": "fake", "model": "fake-1", "dimensions": 4}


def _graph(papers):
    g = ig.Graph(n=len(papers), directed=True)
    for key in ("name", "title", "abstract", "year"):
        g.vs[key] = [p.get(key) for p in papers]
    return g


# ── text ───────────────────────────────────────────────────────────────────────
def test_paper_text_cleaning_and_forms():
    g = _graph([
        {"name": "10.1/A", "title": "Motor &amp; skill", "abstract": "Background: We test. © 2020 Elsevier", "year": 2001.0},
        {"name": "10.1/b", "title": "Only a title", "abstract": "No abstract available.", "year": None},
        {"name": "10.1/c", "title": "", "abstract": "  Just   an abstract ", "year": 1999},
        {"name": "10.1/d", "title": "N/A", "abstract": None, "year": 2000},
    ])
    t = paper_texts(g).set_index("doi")
    assert list(t.index) == ["10.1/a", "10.1/b", "10.1/c"]  # d has neither: skipped
    assert t.at["10.1/a", "embedding_text"] == "Title: Motor & skill\nAbstract: We test."
    assert t.at["10.1/b", "embedding_text"] == "Title: Only a title"
    assert t.at["10.1/c", "embedding_text"] == "Abstract: Just an abstract"
    assert t.at["10.1/a", "year"] == 2001 and pd.isna(t.at["10.1/b", "year"])
    assert len(t.at["10.1/a", "text_sha256"]) == 64


# ── store ──────────────────────────────────────────────────────────────────────
def _texts(*pairs):
    return pd.DataFrame([{"doi": d, "text_sha256": s} for d, s in pairs])


def test_store_round_trip_reuse_and_pruning(tmp_path):
    store = EmbeddingStore(tmp_path, MODEL)
    store.put("a", "s1", [1, 2, 3, 4])
    store.put("b", "s2", [5, 6, 7, 8])
    store.save()
    again = EmbeddingStore(tmp_path, MODEL)
    assert np.array_equal(again.matrix(_texts(("b", "s2"), ("a", "s1"))), [[5, 6, 7, 8], [1, 2, 3, 4]])
    assert list(again.missing(_texts(("a", "s1"), ("a", "changed"), ("c", "s3")))["doi"]) == ["a", "c"]
    assert again.save(keep=_texts(("a", "s1"))) == 1           # b left the corpus
    assert EmbeddingStore(tmp_path, MODEL).get("b", "s2") is None
    # Another model never reuses these vectors.
    assert EmbeddingStore(tmp_path, {**MODEL, "model": "fake-2"}).get("a", "s1") is None
    with pytest.raises(ValueError):
        again.put("x", "s", [1, 2])


# ── incremental embedding ──────────────────────────────────────────────────────
def test_embed_only_what_is_missing(tmp_path, monkeypatch):
    from scientographer import embed_papers

    calls = []

    def fake(texts, store, spec, save):
        calls.append(sorted(texts["doi"]))
        for row in texts.itertuples():
            store.put(row.doi, row.text_sha256, np.full(3072, len(row.embedding_text), np.float32))
        return len(texts)

    monkeypatch.setitem(embed_papers._EMBEDDERS, "gemini", fake)
    spec = [{"name": "gemini", "provider": "gemini"}]
    papers = [{"name": f"10.1/{i}", "title": f"Paper {i}", "abstract": "Text.", "year": 2000} for i in range(3)]
    embed_papers.embedding_stores(paper_texts(_graph(papers)), spec, tmp_path)
    embed_papers.embedding_stores(paper_texts(_graph(papers)), spec, tmp_path)   # nothing new
    papers[1]["abstract"] = "Changed text."
    papers.append({"name": "10.1/new", "title": "New", "abstract": "Text.", "year": 2001})
    summary = embed_papers.embedding_stores(paper_texts(_graph(papers)), spec, tmp_path)
    assert calls == [["10.1/0", "10.1/1", "10.1/2"], ["10.1/1", "10.1/new"]]
    assert summary == {"gemini": {"papers": 4, "embedded_now": 2}}


# ── maps ───────────────────────────────────────────────────────────────────────
def test_embedding_maps_topics_layout_and_snapshots(tmp_path, monkeypatch):
    pytest.importorskip("bertopic", reason="needs the `embeddings` extra")
    from scientographer import embedding_maps

    rng = np.random.default_rng(0)
    words = [["cerebellum purkinje climbing fibre", "cerebellar purkinje plasticity"],
             ["stroke rehabilitation therapy", "stroke patients rehabilitation"],
             ["sleep consolidation memory", "sleep spindles consolidation"]]
    rows, vectors = [], []
    centres = rng.normal(size=(3, 16)) * 5
    for c in range(3):
        for i in range(60):
            rows.append({"doi": f"10.1/{c}-{i}", "year": 1975 + (i % 40),
                         "embedding_text": f"Title: {words[c][i % 2]}\nAbstract: {words[c][(i + 1) % 2]}"})
            vectors.append(centres[c] + rng.normal(size=16))
    texts = pd.DataFrame(rows)
    texts["text_sha256"] = [f"s{i}" for i in range(len(texts))]
    store_dir = tmp_path / "emb" / "fake"
    store = EmbeddingStore(store_dir, {"provider": "fake", "model": "m", "dimensions": 16})
    for row, v in zip(texts.itertuples(), vectors):
        store.put(row.doi, row.text_sha256, v)
    store.save()
    monkeypatch.setattr(embedding_maps, "EMBEDDINGS_DIR", tmp_path / "emb")

    maps = embedding_maps.embedding_maps(texts, [{"name": "fake", "min_cluster_size": 20, "snapshots": [1990, 2010]}])
    written = embedding_maps.save_embedding_maps(maps, tmp_path / "layouts")
    layout = pd.read_csv(tmp_path / "layouts" / "fake.csv")
    assert list(layout.columns) == ["doi", "x", "y", "topic", "topic_name"] and len(layout) == 180
    assert written["fake"]["topics"] == 3
    # Each planted group is one topic, named by its words.
    for c, word in enumerate(["purkinje", "stroke", "sleep"]):
        group = layout[layout["doi"].str.startswith(f"10.1/{c}-")]
        assert group["topic"].nunique() == 1 and word in group["topic_name"].iat[0]
    snaps = json.loads((tmp_path / "layouts" / "fake_snapshots.json").read_text())
    assert snaps["cutoffs"] == [1990, 2010]
    # Years cycle 1975..2014 over each group's 60 papers: 32 are up to 1990, 56 up to 2010.
    assert len(snaps["snapshots"]["1990"]) == 3 * 32 and len(snaps["snapshots"]["2010"]) == 3 * 56
