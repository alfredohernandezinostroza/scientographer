# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text embeddings of every paper, one store per model (params.yaml ``embeddings``).

Each paper's text is its cleaned title and abstract (``_paper_text.py``). Every
model listed under ``embeddings.models`` gets a store under
``<output_dir>/<name>/`` (``_embedding_store.py``): a vector is reused for the
same paper with the same text and model, so runs are resumable and incremental
(only new papers, or papers whose text changed, are embedded). Providers:

- ``gemini``: Google's Gemini embedding API (``model``, e.g. ``gemini-embedding-2``,
  ``dimensions`` up to 3072), one request per paper, rate-limited
  (``requests_per_minute``) over ``workers`` threads, retried with backoff. Needs
  ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) in the environment or the project's
  .env. The store is saved every ``checkpoint_every`` papers, so an interrupted
  run loses little.
- ``specter2``: SPECTER2 run locally (``allenai/specter2_base`` with the
  ``allenai/specter2`` proximity adapter, the model card's recipe for paper
  similarity): input ``title [SEP] abstract``, first-token embedding, 768
  dimensions. Downloads the model from Hugging Face the first time.

Outputs (params.yaml ``embeddings.output_dir``):
  paper_texts.parquet   doi, year, cleaned title and abstract, embedding_text, text_sha256
  <name>/               one store per model (vectors.npy, papers.parquet, model.json)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from pathlib import Path
import random
import sys
import threading
import time
from typing import Final

from hamilton import driver
from hamilton.function_modifiers import dataloader, datasaver
from hamilton.io import utils
import hamilton.log_setup
import igraph as ig
import numpy as np
import pandas as pd

from scientographer._embedding_store import EmbeddingStore
from scientographer._paper_text import paper_texts as _paper_texts
from scientographer.config import FIGURES_PATH, PARAMS, draw_dag, ensure_dirs, tracker_adapters

###################
##   Constants   ##
###################
CURRENT_FILE_NAME = Path(__file__).stem
hamilton.log_setup.setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

EXECUTE = True

_cfg = PARAMS.get("embeddings", {})  # optional section: projects without embeddings skip it
INPUT_GRAPHML: Final[Path] = Path(_cfg.get("input_graphml", "data/citation_network.graphml"))
OUTPUT_DIR: Final[Path] = Path(_cfg.get("output_dir", "data/embeddings"))
MODELS: Final[list[dict]] = list(_cfg.get("models") or [])
PROVIDERS: Final[tuple[str, ...]] = ("gemini", "specter2")

GEMINI_MAX_RETRIES: Final[int] = 8
GEMINI_MAX_INPUT_CHARS: Final[int] = 30000  # below the model's input-token limit
SPECTER2_BASE: Final[str] = "allenai/specter2_base"
SPECTER2_ADAPTER: Final[str] = "allenai/specter2"


#####################
##  Aux Functions  ##
#####################
class _RateLimiter:
    """At most `per_minute` acquisitions per rolling minute, across threads."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / max(1, per_minute)
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if wait > 0:
            time.sleep(wait)


def _gemini_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    raise RuntimeError("the gemini provider needs GEMINI_API_KEY (or GOOGLE_API_KEY) in the "
                       "environment or in the project's .env")


def _embed_gemini(texts: pd.DataFrame, store: EmbeddingStore, spec: dict, save) -> int:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_gemini_key())
    model = spec.get("model", "gemini-embedding-2")
    config = types.EmbedContentConfig(output_dimensionality=int(spec.get("dimensions", 3072)))
    limiter = _RateLimiter(int(spec.get("requests_per_minute", 90)))
    checkpoint = int(spec.get("checkpoint_every", 200))

    def embed(text: str) -> np.ndarray:
        last = None
        for attempt in range(GEMINI_MAX_RETRIES):
            limiter.acquire()
            try:
                response = client.models.embed_content(
                    model=model, contents=text[:GEMINI_MAX_INPUT_CHARS], config=config)
                return np.asarray(response.embeddings[0].values, dtype=np.float32)
            except Exception as err:  # noqa: BLE001 -- quota/network errors: back off, retry
                last = err
                time.sleep(min(120.0, 2.0 ** attempt + random.random()))
        raise RuntimeError(f"Gemini embedding failed after {GEMINI_MAX_RETRIES} attempts: {last}")

    done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=int(spec.get("workers", 8))) as pool:
        futures = {pool.submit(embed, row.embedding_text): row for row in texts.itertuples()}
        for future in as_completed(futures):
            row = futures[future]
            vector = future.result()
            with lock:
                store.put(row.doi, row.text_sha256, vector)
                done += 1
                if done % checkpoint == 0:
                    save()
                    logger.info("%s: %d of %d embedded", spec["name"], done, len(texts))
    return done


def _embed_specter2(texts: pd.DataFrame, store: EmbeddingStore, spec: dict, save) -> int:
    from adapters import AutoAdapterModel
    import torch
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(SPECTER2_BASE)
    model = AutoAdapterModel.from_pretrained(SPECTER2_BASE)
    model.load_adapter(SPECTER2_ADAPTER, source="hf", load_as="proximity", set_active=True)
    model.eval()
    batch_size = int(spec.get("batch_size", 32))
    checkpoint = int(spec.get("checkpoint_every", 2000))
    inputs = [(r.title_clean or "") + tokenizer.sep_token + (r.abstract_clean or "") for r in texts.itertuples()]
    done = 0
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            batch = inputs[start:start + batch_size]
            encoded = tokenizer(batch, padding=True, truncation=True, return_tensors="pt",
                                return_token_type_ids=False, max_length=512)
            vectors = model(**encoded).last_hidden_state[:, 0, :].numpy()
            for row, vector in zip(texts.iloc[start:start + batch_size].itertuples(), vectors):
                store.put(row.doi, row.text_sha256, vector)
            done += len(batch)
            if done % checkpoint < batch_size:
                save()
                logger.info("%s: %d of %d embedded", spec["name"], done, len(texts))
    return done


_EMBEDDERS = {"gemini": _embed_gemini, "specter2": _embed_specter2}


def _model_meta(spec: dict) -> dict:
    if spec.get("provider") not in PROVIDERS:
        raise ValueError(f"embeddings model {spec.get('name')!r}: provider must be one of {PROVIDERS}")
    if spec["provider"] == "gemini":
        return {"provider": "gemini", "model": spec.get("model", "gemini-embedding-2"),
                "dimensions": int(spec.get("dimensions", 3072))}
    return {"provider": "specter2", "model": f"{SPECTER2_BASE}+{SPECTER2_ADAPTER}", "dimensions": 768}


##################
##     Main     ##
##################
def _main() -> int:
    ensure_dirs(FIGURES_PATH, OUTPUT_DIR)
    inputs = dict(citation_network_path=INPUT_GRAPHML, models=MODELS, output_dir=OUTPUT_DIR)
    outputs = ["save_paper_texts", "embedding_stores"]
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
@dataloader()
def citation_network(citation_network_path: Path) -> tuple[ig.Graph, dict]:
    graph = ig.Graph.Read_GraphML(str(citation_network_path))
    return graph, utils.get_file_metadata(citation_network_path)


def paper_texts(citation_network: ig.Graph) -> pd.DataFrame:
    texts = _paper_texts(citation_network)
    logger.info("%d of %d papers have a title or abstract to embed", len(texts), citation_network.vcount())
    return texts


def embedding_stores(paper_texts: pd.DataFrame, models: list, output_dir: Path) -> dict:
    """Bring every model's store up to date with the papers' current texts."""
    summary = {}
    for spec in models:
        store = EmbeddingStore(Path(output_dir) / spec["name"], _model_meta(spec))
        missing = store.missing(paper_texts)
        logger.info("%s: %d of %d papers already embedded, %d to embed",
                    spec["name"], len(paper_texts) - len(missing), len(paper_texts), len(missing))

        def save(store=store):
            store.save()

        if len(missing):
            _EMBEDDERS[spec["provider"]](missing, store, spec, save)
        rows = store.save(keep=paper_texts)  # drop papers no longer in the corpus
        summary[spec["name"]] = {"papers": rows, "embedded_now": len(missing)}
    return summary


@datasaver()
def save_paper_texts(paper_texts: pd.DataFrame, output_dir: Path) -> dict:
    path = Path(output_dir) / "paper_texts.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    paper_texts.to_parquet(path, index=False)
    return utils.get_file_metadata(path)


if __name__ == "__main__":
    sys.exit(_main())
