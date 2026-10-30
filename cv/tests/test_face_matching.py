"""Unit tests for face matching and quality assessment."""

import numpy as np
import pytest

from cv_assistant.faces.matcher import FaceMatcher
from cv_assistant.faces.quality import assess_quality
from cv_assistant.faces.embeddings import normalise_embedding, cosine_similarity


def test_cosine_similarity():
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v3 = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    assert np.isclose(cosine_similarity(v1, v2), 1.0)
    assert np.isclose(cosine_similarity(v1, v3), 0.0)


def test_face_matcher_exact_match():
    matcher = FaceMatcher(threshold=0.5, ambiguity_margin=0.05)

    base_emb = normalise_embedding(np.random.randn(512).astype(np.float32))
    known = [
        {
            "person_id": "pid-1",
            "display_name": "Alice",
            "embeddings": [base_emb],
        }
    ]

    # Query with identical vector
    result = matcher.match(base_emb, known)
    assert result.outcome == "match"
    assert result.person_id == "pid-1"
    assert result.display_name == "Alice"
    assert result.similarity >= 0.99


def test_face_matcher_unknown_on_low_similarity():
    matcher = FaceMatcher(threshold=0.6, ambiguity_margin=0.05)

    emb1 = np.zeros(512, dtype=np.float32)
    emb1[0] = 1.0
    emb2 = np.zeros(512, dtype=np.float32)
    emb2[1] = 1.0  # Orthogonal -> sim = 0

    known = [
        {
            "person_id": "pid-1",
            "display_name": "Alice",
            "embeddings": [emb1],
        }
    ]

    result = matcher.match(emb2, known)
    assert result.outcome == "unknown"


def test_face_matcher_ambiguous_close_candidates():
    matcher = FaceMatcher(threshold=0.5, ambiguity_margin=0.1)

    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0

    # Two known identities with very similar embeddings to base
    emb_cand1 = np.zeros(512, dtype=np.float32)
    emb_cand1[0] = 0.95
    emb_cand1[1] = 0.05
    emb_cand1 = normalise_embedding(emb_cand1)

    emb_cand2 = np.zeros(512, dtype=np.float32)
    emb_cand2[0] = 0.94
    emb_cand2[2] = 0.06
    emb_cand2 = normalise_embedding(emb_cand2)

    known = [
        {"person_id": "pid-1", "display_name": "Person1", "embeddings": [emb_cand1]},
        {"person_id": "pid-2", "display_name": "Person2", "embeddings": [emb_cand2]},
    ]

    result = matcher.match(base, known)
    assert result.outcome == "ambiguous"
    assert result.margin < 0.1


def test_face_matcher_empty_known():
    matcher = FaceMatcher()
    result = matcher.match(np.random.randn(512), [])
    assert result.outcome == "unknown"


def test_assess_quality_passed():
    face = {
        "score": 0.85,
        "bbox": (10, 10, 110, 120),  # width = 100 > min_face_width (80)
        "embedding": np.random.randn(512).astype(np.float32),
    }
    q = assess_quality(face)
    assert q.passed is True
    assert q.score > 0.7
    assert len(q.reasons) == 0


def test_assess_quality_failed_detection_score():
    face = {
        "score": 0.3,  # Below default min_detection_score 0.6
        "bbox": (10, 10, 110, 120),
        "embedding": np.random.randn(512).astype(np.float32),
    }
    q = assess_quality(face)
    assert q.passed is False
    assert any("detection_score" in r for r in q.reasons)


def test_assess_quality_failed_face_crop_size():
    face = {
        "score": 0.9,
        "bbox": (10, 10, 40, 50),  # width = 30 < 80
        "embedding": np.random.randn(512).astype(np.float32),
    }
    q = assess_quality(face)
    assert q.passed is False
    assert any("face_width" in r for r in q.reasons)


def test_service_legacy_profile_isolation_and_confirm_migration():
    import tempfile
    from pathlib import Path
    from cv_assistant.identity.store import IdentityStore
    from cv_assistant.identity.service import IdentityService
    from cv_assistant.state import SceneState

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()
        matcher = FaceMatcher()
        scene = SceneState()
        service = IdentityService(store=store, matcher=matcher, scene=scene, model_version="w600k_mbf")

        # 1. Create a legacy profile with w600k_r50 embedding only
        legacy_pid = store.create_person(display_name="LegacyGlad")
        emb_r50 = np.random.randn(512).astype(np.float32)
        store.add_embedding(legacy_pid, emb_r50, quality=0.9, model_version="w600k_r50")

        # 2. Querying active identities for w600k_mbf must NOT return LegacyGlad
        active_identities = store.get_all_identities_with_embeddings(model_version="w600k_mbf")
        assert len(active_identities) == 0

        # 3. An incoming observation with a w600k_mbf embedding must NOT match LegacyGlad
        emb_mbf = np.random.randn(512).astype(np.float32)
        match_res = matcher.match(emb_mbf, active_identities)
        assert match_res.outcome == "unknown"

        # 4. Explicit legacy migration confirmation attaches w600k_mbf embedding
        migrated = service.confirm_legacy_migration(legacy_pid, emb_mbf, quality=0.95)
        assert migrated is True
        assert store.get_embedding_count(legacy_pid, model_version="w600k_mbf") == 1
        assert store.get_embedding_count(legacy_pid, model_version="w600k_r50") == 1

        # Now active matching includes LegacyGlad
        updated_active = store.get_all_identities_with_embeddings(model_version="w600k_mbf")
        assert len(updated_active) == 1
        assert updated_active[0]["person_id"] == legacy_pid

        store.close()
