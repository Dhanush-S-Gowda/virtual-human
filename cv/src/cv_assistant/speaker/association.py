"""Speaker-to-face association and name-intent parsing.

Associates a spoken self-introduction with the correct visible person.
Uses multiple heuristics in priority order, with explicit ambiguity
handling.  Mouth aspect ratio is used as a **heuristic**, not proof.

The current STT pipeline in virtual-human provides no speaker
diarization or per-word timestamps, so association relies on:
1. Single-person shortcut (only one visible person with identity).
2. Mouth motion heuristic (if face landmarks are available).
3. Ambiguity fallback (ask for clarification).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from cv_assistant import events
from cv_assistant.config import settings
from cv_assistant.identity.service import IdentityService
from cv_assistant.state import SceneState

logger = logging.getLogger(__name__)


@dataclass
class NameIntentResult:
    """Result of parsing a self-introduction from transcript text."""

    detected: bool
    name: str | None = None
    pattern_matched: str | None = None


def parse_name_intent(text: str) -> NameIntentResult:
    """Extract a self-introduction intent from transcript text.

    Checks against configured regex patterns (e.g. "my name is Bablu",
    "I am Bablu", "call me Bablu").
    """
    text_lower = text.lower().strip()
    patterns = settings.speaker.name_patterns

    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            name = match.group(1).strip().capitalize()
            if name and len(name) >= 2:
                return NameIntentResult(
                    detected=True,
                    name=name,
                    pattern_matched=pattern,
                )

    return NameIntentResult(detected=False)


class SpeakerAssociation:
    """Associate a spoken utterance with the correct visible person.

    Priority order:
    1. If only one consenting, identified person is visible → associate.
    2. If mouth motion data is available → use as heuristic.
    3. Otherwise → mark as ambiguous.
    """

    def __init__(
        self,
        scene: SceneState,
        identity_service: IdentityService,
    ) -> None:
        self._scene = scene
        self._identity_service = identity_service
        self._camera_id = settings.camera.camera_id

    def handle_transcript(self, text: str) -> list[events.CVEvent]:
        """Process a transcript from the STT pipeline.

        If a self-introduction is detected, attempt to associate the
        speaker with a visible person and rename them.

        Returns a list of events to emit.
        """
        result = parse_name_intent(text)
        if not result.detected or not result.name:
            return []

        logger.info(
            "Self-introduction detected: name='%s' pattern='%s'",
            result.name, result.pattern_matched,
        )

        emitted: list[events.CVEvent] = []
        association = self._resolve_speaker()

        if association["outcome"] in ("single_person", "mouth_motion"):
            track_id = association["track_id"]
            confidence = association.get("confidence", 0.0)
            if confidence >= settings.speaker.ambiguity_threshold:
                ev, status = self._identity_service.bind_name_to_active_person(
                    name=result.name,
                    track_id=track_id,
                )
                if ev:
                    emitted.append(ev)
                elif status == "no_identity":
                    logger.info(
                        "Cannot rename: track %d has no identity yet.", track_id,
                    )
            else:
                emitted.append(events.speaker_association_ambiguous(
                    transcript=text,
                    candidates=association.get("candidates", []),
                    camera_id=self._camera_id,
                ))

        elif association["outcome"] == "ambiguous":
            emitted.append(events.speaker_association_ambiguous(
                transcript=text,
                candidates=association.get("candidates", []),
                camera_id=self._camera_id,
            ))
            logger.info(
                "Speaker association ambiguous: %d candidates",
                len(association.get("candidates", [])),
            )

        elif association["outcome"] == "no_people":
            logger.info("No identified people visible to associate name with.")

        return emitted

    def _resolve_speaker(self, require_evidence: bool | None = None) -> dict[str, Any]:
        """Determine which visible person is the speaker.

        Returns a dict with:
        - ``outcome``: "single_person", "mouth_motion", "ambiguous", "no_people"
        - ``track_id``: if resolved
        - ``confidence``: if applicable
        - ``candidates``: list of candidate tracks if ambiguous
        """
        req_evidence = (
            require_evidence
            if require_evidence is not None
            else getattr(settings.speaker, "require_speaker_evidence", False)
        )

        identified = self._scene.get_tracks_with_identity()
        visible_tracks = identified if identified else self._scene.get_stable_tracks()

        if len(visible_tracks) == 0:
            return {"outcome": "no_people"}

        best_track_id, best_motion, candidates = self._mouth_motion_heuristic(visible_tracks)

        if len(visible_tracks) == 1:
            track = visible_tracks[0]
            # If mouth history exists, ensure person is actually speaking (not passive listener with off-camera speaker)
            if len(track.mouth_history) >= 3:
                if best_motion < settings.speaker.ambiguity_threshold:
                    logger.info("Single visible person has no active mouth motion; treating as ambiguous.")
                    return {
                        "outcome": "ambiguous",
                        "candidates": candidates,
                    }
                return {
                    "outcome": "mouth_motion",
                    "track_id": track.track_id,
                    "confidence": max(best_motion, settings.speaker.single_person_confidence),
                    "candidates": candidates,
                }

            if req_evidence:
                return {
                    "outcome": "ambiguous",
                    "candidates": [
                        {"track_id": track.track_id, "person_id": track.person_id, "reason": "unverified_speaker"}
                    ],
                }

            return {
                "outcome": "single_person",
                "track_id": track.track_id,
                "confidence": settings.speaker.single_person_confidence,
            }

        # Multiple people visible — evaluate mouth motion across candidates
        if best_track_id is not None and best_motion >= settings.speaker.ambiguity_threshold:
            return {
                "outcome": "mouth_motion",
                "track_id": best_track_id,
                "confidence": best_motion,
                "candidates": candidates,
            }

        return {
            "outcome": "ambiguous",
            "candidates": candidates,
        }

    def _mouth_motion_heuristic(
        self,
        tracks: list[Any],
    ) -> tuple[int | None, float, list[dict[str, Any]]]:
        """Rank tracks by recent mouth motion.

        This is a **heuristic**, not a reliable active-speaker detector.
        It can fail due to head pose, low resolution, or unrelated movement.

        Returns ``(best_track_id, best_score, candidates_list)``.
        """
        candidates: list[dict[str, Any]] = []
        best_id: int | None = None
        best_score = 0.0

        for track in tracks:
            history = track.mouth_history
            score = 0.0
            if len(history) >= 3:
                # Compute variance of mouth aspect ratio over recent frames
                ratios = [r for _, r in history[-10:]]
                if ratios:
                    import numpy as np
                    variance = float(np.var(ratios))
                    score = min(variance * 100, 1.0)  # Scale heuristic

            candidates.append({
                "track_id": track.track_id,
                "person_id": track.person_id,
                "display_name": track.display_name,
                "mouth_motion_score": round(score, 3),
            })

            if score > best_score:
                best_score = score
                best_id = track.track_id

        # Check margin between best and second-best
        scores = sorted([c["mouth_motion_score"] for c in candidates], reverse=True)
        if len(scores) >= 2 and scores[0] - scores[1] < settings.speaker.ambiguity_threshold:
            return None, 0.0, candidates

        return best_id, best_score, candidates

    def get_active_speaker(self) -> dict[str, Any] | None:
        """Resolve current active speaker across all visible tracks."""
        stable = self._scene.get_stable_tracks()
        if not stable:
            return None
        if len(stable) == 1:
            t = stable[0]
            return {
                "track_id": t.track_id,
                "person_id": t.person_id,
                "display_name": t.display_name or "Unknown",
                "confidence": settings.speaker.single_person_confidence,
            }

        # Multiple tracks visible -> evaluate mouth motion across all stable tracks
        best_id, best_score, candidates = self._mouth_motion_heuristic(stable)
        if best_id is not None:
            t = self._scene.get_track(best_id)
            if t:
                return {
                    "track_id": t.track_id,
                    "person_id": t.person_id,
                    "display_name": t.display_name or "Unknown",
                    "confidence": round(best_score, 2),
                }

        # Fallback to closest/largest or first identified
        identified = [t for t in stable if t.display_name and t.display_name != "Unknown"]
        if identified:
            t = identified[0]
            return {
                "track_id": t.track_id,
                "person_id": t.person_id,
                "display_name": t.display_name,
                "confidence": 0.6,
            }

        t = stable[0]
        return {
            "track_id": t.track_id,
            "person_id": t.person_id,
            "display_name": t.display_name or "Unknown",
            "confidence": 0.5,
        }
