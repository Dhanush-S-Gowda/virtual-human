"""Identity service — orchestrates face matching and identity lifecycle.

Bridges the face detector, quality filter, matcher, and persistent store
into a cohesive identity management workflow.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from cv_assistant import events
from cv_assistant.config import settings
from cv_assistant.faces.embeddings import normalise_embedding
from cv_assistant.faces.matcher import FaceMatcher, MatchResult
from cv_assistant.faces.quality import QualityResult, assess_quality
from cv_assistant.identity.store import IdentityStore
from cv_assistant.state import SceneState

logger = logging.getLogger(__name__)


class IdentityService:
    """High-level identity management for tracked people.

    Coordinates face quality assessment, matching against the persistent
    store, enrollment of new people, and name binding from speech.
    """

    def __init__(
        self,
        store: IdentityStore,
        matcher: FaceMatcher,
        scene: SceneState,
        model_version: str | None = None,
    ) -> None:
        self._store = store
        self._matcher = matcher
        self._scene = scene
        self._camera_id = settings.camera.camera_id
        self._model_version = model_version or getattr(settings.face, "embedding_model", "w600k_mbf")
        self._auto_enroll = getattr(settings.identity, "auto_enroll", False)
        self._pending_embeddings: dict[int, tuple[np.ndarray, float]] = {}

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def auto_enroll(self) -> bool:
        return self._auto_enroll

    @auto_enroll.setter
    def auto_enroll(self, value: bool) -> None:
        self._auto_enroll = bool(value)

    def enroll_person_from_track(
        self,
        track_id: int,
        embedding: np.ndarray,
        quality: float = 1.0,
        display_name: str = "Unknown",
        consent_status: str = "granted",
    ) -> str:
        """Enroll a person from an observed track and assign persistent identity."""
        person_id = self._store.create_person(
            display_name=display_name,
            consent_status=consent_status,
        )
        self._store.add_embedding(
            person_id,
            embedding,
            quality,
            model_version=self._model_version,
        )
        track = self._scene.get_track(track_id)
        if track:
            track.person_id = person_id
            track.display_name = display_name
            track.identity_confidence = 1.0
            track.consent_status = consent_status
        self._pending_embeddings.pop(track_id, None)
        return person_id

    def confirm_legacy_migration(
        self,
        legacy_person_id: str,
        embedding: np.ndarray,
        quality: float = 1.0,
    ) -> bool:
        """Explicitly confirm attaching a new model embedding to a legacy profile.

        Never called automatically from STT or name match. Requires explicit confirmation.
        """
        person = self._store.get_person(legacy_person_id)
        if not person:
            logger.warning("Legacy migration failed: person %s not found.", legacy_person_id)
            return False
        legacy_count = self._store.get_embedding_count(legacy_person_id, model_version="w600k_r50")
        if legacy_count == 0:
            logger.warning("Person %s has no legacy w600k_r50 embeddings.", legacy_person_id)
        self._store.add_embedding(
            legacy_person_id,
            embedding,
            quality,
            model_version=self._model_version,
        )
        logger.info(
            "Explicitly confirmed legacy migration for %s (%s) with %s",
            legacy_person_id, person.get("display_name"), self._model_version,
        )
        return True

    def forget_track(self, track_id: int) -> None:
        """Discard an unbound face embedding when its temporary track ends."""
        self._pending_embeddings.pop(track_id, None)

    def has_face_embedding(self, person_id: str) -> bool:
        """Return whether a persistent identity can be recognized with active model."""
        return self._store.get_embedding_count(person_id, model_version=self._model_version) > 0

    # ── Face identity resolution ────────────────────────────────────────

    def process_face_observation(
        self,
        track_id: int,
        face: dict[str, Any],
        frame_shape: tuple[int, ...] | None = None,
    ) -> tuple[MatchResult | None, list[events.CVEvent]]:
        """Process a face detection for a tracked person.

        1. Assess quality.
        2. If the track already has a confident identity, optionally
           update the stored embeddings.
        3. If not, attempt matching against the identity store.
        4. If no match and auto-enroll is enabled, create a new identity.

        Returns the match result and any events to emit.
        """
        emitted: list[events.CVEvent] = []
        track = self._scene.get_track(track_id)
        if track is None:
            return None, emitted

        # Quality gate
        quality = assess_quality(face, frame_shape)
        if not quality.passed:
            logger.debug(
                "Face quality failed for track %d: %s",
                track_id, quality.reasons,
            )
            return None, emitted

        embedding = face.get("embedding")
        if embedding is None:
            return None, emitted
        embedding = normalise_embedding(np.asarray(embedding, dtype=np.float32))
        self._pending_embeddings[track_id] = (embedding.copy(), quality.score)

        # If track already has a confident identity, consider updating embeddings
        if track.person_id is not None and track.identity_confidence > settings.face.matching.threshold:
            if self._store.get_embedding_count(track.person_id, model_version=self._model_version) == 0:
                self._persist_pending_embedding(track_id, track.person_id)
            else:
                self._pending_embeddings.pop(track_id, None)
                self._maybe_update_embeddings(track.person_id, embedding, quality.score)
            self._store.update_last_seen(track.person_id)
            return MatchResult(
                outcome="match",
                person_id=track.person_id,
                display_name=track.display_name,
                similarity=track.identity_confidence,
            ), emitted

        # Match against known identities with compatible model_version
        known = self._store.get_all_identities_with_embeddings(model_version=self._model_version)
        result = self._matcher.match(embedding, known)

        if result.outcome == "match" and result.person_id:
            self._pending_embeddings.pop(track_id, None)
            # Map the track to this identity
            track.person_id = result.person_id
            track.display_name = result.display_name
            track.identity_confidence = result.similarity
            self._store.update_last_seen(result.person_id)
            emitted.append(events.person_identified(
                track_id=track_id,
                person_id=result.person_id,
                display_name=result.display_name or "Unknown",
                confidence=result.similarity,
                camera_id=self._camera_id,
            ))
            # Optionally add this good embedding
            self._maybe_update_embeddings(result.person_id, embedding, quality.score)

        elif result.outcome == "unknown":
            # Consider auto-enrollment
            if self._auto_enroll:
                if not settings.identity.consent_required or track.consent_status == "granted":
                    person_id = self._store.create_person(
                        display_name="Unknown",
                        consent_status=track.consent_status,
                    )
                    self._store.add_embedding(
                        person_id,
                        embedding,
                        quality.score,
                        max_embeddings=settings.face.quality.max_embeddings_per_person,
                        model_version=self._model_version,
                    )
                    self._pending_embeddings.pop(track_id, None)
                    track.person_id = person_id
                    track.display_name = "Unknown"
                    track.identity_confidence = 1.0  # Self-match
                    result = MatchResult(
                        outcome="enrolled",
                        person_id=person_id,
                        display_name="Unknown",
                        similarity=1.0,
                    )
                    emitted.append(events.person_identified(
                        track_id=track_id,
                        person_id=person_id,
                        display_name="Unknown",
                        confidence=1.0,
                        camera_id=self._camera_id,
                    ))
                    logger.info(
                        "Auto-enrolled new person: track=%d person_id=%s (model=%s)",
                        track_id, person_id, self._model_version,
                    )
                else:
                    logger.debug(
                        "Skipping enrollment for track %d: consent not granted.",
                        track_id,
                    )

        elif result.outcome == "ambiguous":
            logger.info(
                "Ambiguous face match for track %d: best=%s sim=%.3f margin=%.3f",
                track_id, result.person_id, result.similarity, result.margin,
            )

        return result, emitted

    def _persist_pending_embedding(self, track_id: int, person_id: str) -> None:
        """Attach a recent valid face observation to a named identity if needed."""
        pending = self._pending_embeddings.pop(track_id, None)
        if pending is None or self._store.get_embedding_count(person_id, model_version=self._model_version) > 0:
            return

        # Check if this person_id is a legacy-only profile (w600k_r50 only)
        legacy_count = self._store.get_embedding_count(person_id, model_version="w600k_r50")
        if legacy_count > 0:
            logger.warning(
                "Blocked auto-associating new %s embedding to legacy-only person_id %s without explicit confirmation.",
                self._model_version, person_id,
            )
            return

        embedding, quality = pending
        self._store.add_embedding(
            person_id,
            embedding,
            quality,
            max_embeddings=settings.face.quality.max_embeddings_per_person,
            model_version=self._model_version,
        )

    def _maybe_update_embeddings(
        self,
        person_id: str,
        embedding: np.ndarray,
        quality: float,
    ) -> None:
        """Add a new embedding if quality is high enough and under the limit."""
        if quality < settings.face.quality.min_quality_for_update:
            return

        # Do not silently attach to legacy-only profile without explicit confirmation
        active_count = self._store.get_embedding_count(person_id, model_version=self._model_version)
        legacy_count = self._store.get_embedding_count(person_id, model_version="w600k_r50")
        if active_count == 0 and legacy_count > 0:
            logger.warning(
                "Refusing to silently add %s embedding to legacy profile %s.",
                self._model_version, person_id,
            )
            return

        max_emb = settings.face.quality.max_embeddings_per_person
        if active_count < max_emb:
            self._store.add_embedding(
                person_id,
                embedding,
                quality,
                max_embeddings=max_emb,
                model_version=self._model_version,
            )

    # ── Name binding from speech ────────────────────────────────────────

    def bind_name_to_person(
        self,
        person_id: str,
        name: str,
    ) -> events.CVEvent | None:
        """Update the display name for a persistent identity.

        Does NOT change the ``person_id`` — names are metadata.
        Returns a ``person_renamed`` event on success.
        """
        person = self._store.get_person(person_id)
        if person is None:
            logger.warning("Cannot rename: person_id %s not found.", person_id)
            return None

        track = self._scene.find_track_by_person_id(person_id)
        if track is not None:
            self._persist_pending_embedding(track.track_id, person_id)

        old_name = person["display_name"]
        if old_name == name:
            return None  # No change needed

        success = self._store.rename_person(person_id, name)
        if not success:
            return None
        # Update any active tracks with this identity
        # Update any active tracks with this identity
        if track is not None:
            track.display_name = name

        ev = events.person_renamed(
            person_id=person_id,
            old_name=old_name,
            new_name=name,
            track_id=track.track_id if track else None,
            camera_id=self._camera_id,
        )
        logger.info("Person renamed: %s -> %s (person_id=%s)", old_name, name, person_id)
        return ev

    def bind_name_to_active_person(
        self,
        name: str,
        track_id: int | None = None,
    ) -> tuple[events.CVEvent | None, str]:
        """Bind a name to the currently active person.

        If *track_id* is specified, use that track.  Otherwise, if exactly
        one person is visible and has an identity, use them.

        Returns ``(event, status)`` where status is one of:
        ``"renamed"``, ``"ambiguous"``, ``"no_identity"``, ``"not_found"``.
        """
        if track_id is not None:
            track = self._scene.get_track(track_id)
            if track is None:
                return None, "not_found"
            if track.person_id is None:
                person_id = self._store.create_person(
                    display_name=name,
                    consent_status="granted",
                )
                self._persist_pending_embedding(track_id, person_id)
                track.person_id = person_id
                track.display_name = name
                track.identity_confidence = 1.0
                track.consent_status = "granted"
                ev = events.person_renamed(
                    person_id=person_id,
                    old_name="Unknown",
                    new_name=name,
                    track_id=track.track_id,
                    camera_id=self._camera_id,
                )
                logger.info("Enrolled consenting person from speech: %s (person_id=%s)", name, person_id)
                return ev, "renamed"
            ev = self.bind_name_to_person(track.person_id, name)
            return ev, "renamed" if ev else "no_change"

        # Find the best candidate
        identified = self._scene.get_tracks_with_identity()
        if len(identified) == 1:
            person_id = identified[0].person_id
            if person_id is None:
                return None, "no_identity"
            ev = self.bind_name_to_person(person_id, name)
            return ev, "renamed" if ev else "no_change"
        if len(identified) == 0:
            stable = self._scene.get_stable_tracks()
            if len(stable) == 1:
                return self.bind_name_to_active_person(name, track_id=stable[0].track_id)
            return None, "no_identity"

        # Multiple people — ambiguous
        return None, "ambiguous"
