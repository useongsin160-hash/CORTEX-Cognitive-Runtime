"""Shared embedding function for all vector operations.

Phase 3 STEP 1.5 option F: switched from chromadb's default
all-MiniLM-L6-v2 (English-only — collapses Korean text to mean cosine
~0.95) to intfloat/multilingual-e5-base for cross-lingual coverage.
Model weights are injected from a GitHub Release because the sandbox
network policy blocks huggingface.co.

Critical invariant: SemanticCache and CentroidStore MUST use the same
embedder instance. Both modules import `get_embedding_function()` and
never instantiate their own.

E5 usage: prefix every input with "query: " (symmetric variant), which
is the documented fallback for similarity-only workloads where stored
texts and lookup texts share a vocabulary. Embeddings are
L2-normalized so ChromaDB's cosine space and CentroidStore's dot-
product similarity agree on a common metric.
"""
from __future__ import annotations

import os
from typing import Sequence

# Force HF Hub into offline mode — the cache is pre-injected and we never
# want a stray online call to make a test hang waiting for huggingface.co.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from chromadb import Documents, EmbeddingFunction, Embeddings  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

EMBEDDER_MODEL_NAME = "intfloat/multilingual-e5-base"
_E5_PREFIX = "query: "

_model_singleton: SentenceTransformer | None = None
_embedder_singleton: "MultilingualE5Embedder | None" = None


def _get_model() -> SentenceTransformer:
    global _model_singleton
    if _model_singleton is None:
        # disable_mmap=True: read the safetensors weights fully into RAM instead
        # of memory-mapping them. The default mmap path makes tensor
        # materialization (transformers' _materialize_copy does `tensor[...]` on a
        # safetensors slice) page-fault under host memory pressure, raising a
        # native access violation (0xC0000005) on Windows — intermittently, and in
        # ANY thread (main or asyncio worker). Reading into RAM removes the mmap
        # page-fault entirely; the worst case degrades from a native crash to a
        # clean, catchable OSError. Applied unconditionally (no env/test branch):
        # the model weights, embeddings, routing and outputs are identical — only
        # the load strategy changes.
        _model_singleton = SentenceTransformer(
            EMBEDDER_MODEL_NAME, model_kwargs={"disable_mmap": True}
        )
    return _model_singleton


class MultilingualE5Embedder(EmbeddingFunction[Documents]):
    """ChromaDB-compatible wrapper around intfloat/multilingual-e5-base."""

    def __call__(self, input: Documents) -> Embeddings:
        texts: Sequence[str] = list(input)
        prefixed = [_E5_PREFIX + str(t) for t in texts]
        vectors = _get_model().encode(
            prefixed,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    @staticmethod
    def name() -> str:
        return "intfloat-multilingual-e5-base-symmetric"


def get_embedding_function() -> MultilingualE5Embedder:
    """Return the shared embedder instance. Idempotent."""
    global _embedder_singleton
    if _embedder_singleton is None:
        _embedder_singleton = MultilingualE5Embedder()
    return _embedder_singleton
