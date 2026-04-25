"""Sentence-transformers wrapper.

Loads ``paraphrase-multilingual-MiniLM-L12-v2`` lazily on first use and caches
weights to the ``EMBEDDING_CACHE_DIR`` volume. The model is ~480 MB; a single
encode is ~10ms on CPU. We keep diacritics in the input — the multilingual
MiniLM is trained with full Unicode tone-aware tokenization for VN.
"""

from __future__ import annotations

import logging
from typing import Any

from poi_lake.config import get_settings

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 384  # MiniLM-L12 output dim — keep in lock-step with VECTOR(384)


class EmbeddingService:
    """Singleton-friendly wrapper. Construct once per process; ``encode`` is
    re-entrant. Tests can swap in a stub via ``set_embedding_service``."""

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._cache_dir = cache_dir or settings.embedding_cache_dir
        self._model: Any | None = None  # lazy

    @property
    def dim(self) -> int:
        return _EMBEDDING_DIM

    def _load(self) -> Any:
        if self._model is None:
            # Import here so test environments that swap the service in
            # don't need sentence-transformers installed.
            from sentence_transformers import SentenceTransformer

            logger.info(
                "loading embedding model %s (cache=%s)", self._model_name, self._cache_dir
            )
            self._model = SentenceTransformer(
                self._model_name,
                cache_folder=self._cache_dir,
                device="cpu",
            )
        return self._model

    def encode(self, text: str) -> list[float]:
        """Encode a single string into a 384-dim list of floats."""
        if not text or not text.strip():
            # Return zero vector for empty input. pgvector accepts it; the
            # dedupe layer will down-rank zero-vec records.
            return [0.0] * _EMBEDDING_DIM

        model = self._load()
        vec = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
        return vec.tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode many strings in one model call (much faster than N encodes)."""
        if not texts:
            return []
        model = self._load()
        vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, batch_size=32)
        return vecs.tolist()


# Module-level singleton — workers reuse the same loaded model.
_default: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _default
    if _default is None:
        _default = EmbeddingService()
    return _default


def set_embedding_service(svc: EmbeddingService | None) -> None:
    """Test hook: swap in a stub or reset to default (when called with None)."""
    global _default
    _default = svc
