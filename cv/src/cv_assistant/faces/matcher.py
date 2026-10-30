"""Face matching against the persistent identity store.

Compares a query embedding against all stored identities using cosine
similarity.  Returns the best match, unknown, or ambiguous outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from cv_assistant.config import settings
from cv_assistant.faces.embeddings import cosine_similarity, normalise_embedding

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Outcome of matching a face embedding against stored identities."""

    outcome: str  # "match", "unknown", "ambiguous"
    person_id: str | None = None
    display_name: str | None = None
    similarity: float = 0.0
    margin: float = 0.0  # Gap between best and second-best


class FaceMatcher:
    """Match face embeddings against a collection of known identities.

    Parameters
    ----------
    threshold : float
        Minimum cosine similarity for a positive match.
    ambiguity_margin : float
        Minimum gap between best and second-best similarity.
        If the gap is smaller, the result is ambiguous.
    """

    def __init__(
        self,
        threshold: float | None = None,
        ambiguity_margin: float | None = None,
    ) -> None:
        self._threshold = threshold or settings.face.matching.threshold
        self._ambiguity_margin = ambiguity_margin or settings.face.matching.ambiguity_margin

    def match(
        self,
        query_embedding: np.ndarray,
        known_identities: list[dict[str, Any]],
    ) -> MatchResult:
        """Match *query_embedding* against *known_identities*.

        Each identity dict must have:
        - ``person_id``: str
        - ``display_name``: str
        - ``embeddings``: list of numpy arrays (each L2-normalised)

        Returns a ``MatchResult`` with outcome, identity, and confidence.
        """
        query = normalise_embedding(np.asarray(query_embedding, dtype=np.float32))

        if not known_identities:
            return MatchResult(outcome="unknown")

        best_sim = -1.0
        second_sim = -1.0
        best_identity: dict[str, Any] | None = None

        for identity in known_identities:
            embeddings = identity.get("embeddings", [])
            if not embeddings:
                continue

            # Compare against all stored embeddings, take the max
            max_sim = max(
                cosine_similarity(query, normalise_embedding(np.asarray(e, dtype=np.float32)))
                for e in embeddings
            )

            if max_sim > best_sim:
                second_sim = best_sim
                best_sim = max_sim
                best_identity = identity
            elif max_sim > second_sim:
                second_sim = max_sim

        if best_sim < self._threshold:
            return MatchResult(outcome="unknown", similarity=best_sim)

        margin = best_sim - second_sim
        if margin < self._ambiguity_margin and second_sim >= self._threshold:
            return MatchResult(
                outcome="ambiguous",
                person_id=best_identity["person_id"] if best_identity else None,
                display_name=best_identity.get("display_name") if best_identity else None,
                similarity=best_sim,
                margin=margin,
            )

        return MatchResult(
            outcome="match",
            person_id=best_identity["person_id"],
            display_name=best_identity.get("display_name", "Unknown"),
            similarity=best_sim,
            margin=margin,
        )
