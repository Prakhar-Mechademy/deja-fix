"""Sentence-transformer embedding service."""

import json

import numpy as np

from app.core.config import settings

_model = None


def get_embedding_model():
    global _model

    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.EMBEDDING_MODEL)

    return _model


def embed_text(text: str) -> np.ndarray:
    text = text.strip()

    if not text:
        raise ValueError("Cannot embed empty text")

    vector = get_embedding_model().encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    return np.asarray(vector, dtype=np.float32)


def serialize_embedding(vector: np.ndarray) -> str:
    return json.dumps(np.asarray(vector, dtype=np.float32).tolist())


def deserialize_embedding(value: str) -> np.ndarray:
    if not value:
        raise ValueError("Embedding is empty")

    vector = np.asarray(json.loads(value), dtype=np.float32)

    if vector.ndim != 1:
        raise ValueError("Embedding must be a one-dimensional vector")

    return vector
