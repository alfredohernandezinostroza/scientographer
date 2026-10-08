# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text-embedding maps: topics and 2-D layouts from the paper embeddings
(params.yaml ``embedding_maps``), ready for the site's extra views.

For every map listed under ``embedding_maps.maps`` (each naming a store made by
embed_papers.py):

- **Topics** with BERTopic on the stored vectors: UMAP to ``topic_dimensions``
  (5) dimensions (cosine, ``n_neighbors`` 15, ``min_dist`` 0, seed 42), then
  HDBSCAN (``min_cluster_size``, excess-of-mass selection; papers it cannot place
  are outliers, topic -1), then c-TF-IDF keywords over 1-2-word terms (English
  stop words plus "title"/"abstract", up to 10,000 terms). A topic is named by
  its three best keywords.
- **Positions**: a separate 2-D UMAP of the same vectors (cosine,
  ``n_neighbors`` 15, ``min_dist`` 0.1, seed 42), for display only; it never
  decides topic membership, so a topic can look split on the map.
- **Time snapshots** (``snapshots``: cutoff years): the same 2-D UMAP run on the
  papers published up to each year, so the map can show the field as it grew.

These are the settings of the motor-learning study's embedding-space analysis
(topic_modeling_new.py, embedding_space_analysis.py, build_time_snapshots.py).

Outputs (params.yaml ``embedding_maps.output_dir``), one set per map, in the
format ``website.extra_layouts`` reads:
  <name>.csv              doi, x, y, topic, topic_name
  <name>_topics.csv       topic, size, name, keywords
  <name>_snapshots.json   {"cutoffs": [...], "snapshots": {"<year>": {"<doi>": [x, y]}}}
"""

import json
import logging
from pathlib import Path
import sys
from typing import Final

from hamilton import driver
import hamilton.log_setup
import numpy as np
import pandas as pd

from scientographer._embedding_store import EmbeddingStore
from scientographer.config import FIGURES_PATH, PARAMS, draw_dag, ensure_dirs, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = PARAMS.get("embedding_maps", {})
EMBEDDINGS_DIR: Final[Path] = Path(PARAMS.get("embeddings", {}).get("output_dir", "data/embeddings"))
OUTPUT_DIR: Final[Path] = Path(_cfg.get("output_dir", "data/layouts"))
MAPS: Final[list[dict]] = list(_cfg.get("maps") or [])

# BERTopic / UMAP settings (overridable per map).
DEFAULTS: Final[dict] = {
    "min_cluster_size": 50,
    "n_neighbors": 15,
    "topic_dimensions": 5,
    "layout_min_dist": 0.1,
    "seed": 42,
    "snapshots": [],
}
TOPIC_NAME_WORDS: Final[int] = 3


#####################
##  Aux Functions  ##
#####################
def _stop_words() -> list[str]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    # The embedding text starts with literal "Title:" / "Abstract:" labels.
    return sorted(ENGLISH_STOP_WORDS | {"title", "abstract"})


def _topics(documents: list[str], vectors: np.ndarray, settings: dict) -> tuple[np.ndarray, pd.DataFrame]:
    """BERTopic topic per paper (-1 = outlier) and one row per topic."""
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    model = BERTopic(
        umap_model=UMAP(n_neighbors=settings["n_neighbors"], n_components=settings["topic_dimensions"],
                        min_dist=0.0, metric="cosine", random_state=settings["seed"]),
        hdbscan_model=HDBSCAN(min_cluster_size=settings["min_cluster_size"], metric="euclidean",
                              cluster_selection_method="eom", cluster_selection_epsilon=0.0,
                              prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words=_stop_words(), max_features=10000, ngram_range=(1, 2)),
        top_n_words=10, calculate_probabilities=False, verbose=False,
    )
    topics, _ = model.fit_transform(documents, embeddings=vectors)
    rows = []
    for topic, size in pd.Series(topics).value_counts().sort_index().items():
        if topic < 0:
            continue
        words = [w for w, _ in (model.get_topic(topic) or [])]
        rows.append({"topic": int(topic), "size": int(size),
                     "name": ", ".join(words[:TOPIC_NAME_WORDS]), "keywords": " | ".join(words)})
    return np.asarray(topics, dtype=int), pd.DataFrame(rows, columns=["topic", "size", "name", "keywords"])


def _layout(vectors: np.ndarray, settings: dict) -> np.ndarray:
    from umap import UMAP

    n_neighbors = min(settings["n_neighbors"], max(2, len(vectors) - 1))
    return UMAP(n_neighbors=n_neighbors, n_components=2, min_dist=settings["layout_min_dist"],
                metric="cosine", random_state=settings["seed"]).fit_transform(vectors)


def _store_for(name: str) -> EmbeddingStore:
    directory = EMBEDDINGS_DIR / name
    model = json.loads((directory / "model.json").read_text(encoding="utf-8"))
    return EmbeddingStore(directory, model)


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_DIR)
    inputs = dict(paper_texts_path=EMBEDDINGS_DIR / "paper_texts.parquet", maps=MAPS, output_dir=OUTPUT_DIR)
    outputs = ["save_embedding_maps"]
    import __main__
    dr = (
        driver.Builder()
        .with_modules(__main__)
        .with_adapters(*tracker_adapters(CURRENT_FILE_NAME))  # params.yaml `tracker.enabled`
        .build()
    )
    dr.validate_execution(outputs, inputs=inputs)
    draw_dag(dr, CURRENT_FILE_NAME, outputs, inputs)
    if EXECUTE:
        dr.execute(outputs, inputs=inputs)
    return 0


#########################
##    DAG Definition   ##
#########################
def paper_texts(paper_texts_path: Path) -> pd.DataFrame:
    return pd.read_parquet(paper_texts_path)


def embedding_maps(paper_texts: pd.DataFrame, maps: list) -> list[dict]:
    """Topics, a 2-D layout and time snapshots for every map."""
    results = []
    for spec in maps:
        settings = {**DEFAULTS, **{k: v for k, v in spec.items() if k in DEFAULTS}}
        store = _store_for(spec.get("embeddings", spec["name"]))
        missing = store.missing(paper_texts)
        if len(missing):
            raise ValueError(f"map {spec['name']!r}: {len(missing)} papers have no embedding; run embed_papers first")
        vectors = store.matrix(paper_texts)
        logger.info("map %s: %d papers, %d dimensions", spec["name"], *vectors.shape)

        topics, topic_table = _topics(paper_texts["embedding_text"].tolist(), vectors, settings)
        logger.info("map %s: %d topics (HDBSCAN min_cluster_size %d), %d outliers",
                    spec["name"], len(topic_table), settings["min_cluster_size"], int((topics < 0).sum()))
        xy = _layout(vectors, settings)
        names = dict(zip(topic_table["topic"], topic_table["name"]))
        layout = pd.DataFrame({
            "doi": paper_texts["doi"], "x": xy[:, 0].round(4), "y": xy[:, 1].round(4),
            "topic": topics, "topic_name": [names.get(t, "") for t in topics],
        })

        snapshots = None
        if settings["snapshots"]:
            years = paper_texts["year"]
            snapshots = {"cutoffs": [int(c) for c in settings["snapshots"]], "snapshots": {}}
            for cutoff in snapshots["cutoffs"]:
                keep = np.flatnonzero((years.notna() & (years <= cutoff)).to_numpy())
                if len(keep) < 3:
                    continue
                sub = _layout(vectors[keep], settings)
                snapshots["snapshots"][str(cutoff)] = {
                    paper_texts["doi"].iat[i]: [round(float(x), 4), round(float(y), 4)]
                    for i, (x, y) in zip(keep, sub)}
                logger.info("map %s: snapshot up to %d, %d papers", spec["name"], cutoff, len(keep))
        results.append({"name": spec["name"], "layout": layout, "topics": topic_table,
                        "snapshots": snapshots, "settings": settings})
    return results


def save_embedding_maps(embedding_maps: list[dict], output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for result in embedding_maps:
        name = result["name"]
        result["layout"].to_csv(output_dir / f"{name}.csv", index=False)
        result["topics"].to_csv(output_dir / f"{name}_topics.csv", index=False)
        if result["snapshots"] is not None:
            (output_dir / f"{name}_snapshots.json").write_text(json.dumps(result["snapshots"]), encoding="utf-8")
        written[name] = {"papers": len(result["layout"]), "topics": len(result["topics"])}
    logger.info("embedding maps written to %s: %s", output_dir, written)
    return written


if __name__ == "__main__":
    sys.exit(_main())
