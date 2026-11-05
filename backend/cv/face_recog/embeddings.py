"""Face embedding extraction and normalisation.

Thin wrapper around InsightFace's embedding output, ensuring vectors
are L2-normalised before storage or comparison.
"""

from __future__ import annotations

import numpy as np


def normalise_embedding(embedding: np.ndarray) -> np.ndarray:
    """L2-normalise a face embedding vector."""
    norm = np.linalg.norm(embedding)
    if norm < 1e-10:
        return embedding
    return embedding / norm


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two L2-normalised vectors."""
    return float(np.dot(a, b))


def compute_centroid(embeddings: list[np.ndarray]) -> np.ndarray:
    """Compute the L2-normalised centroid of a list of embeddings."""
    if not embeddings:
        raise ValueError("Cannot compute centroid of empty list.")
    stacked = np.stack(embeddings)
    centroid = stacked.mean(axis=0)
    return normalise_embedding(centroid)
