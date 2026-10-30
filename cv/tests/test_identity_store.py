"""Unit tests for the SQLite-backed IdentityStore."""

import os
import tempfile
from pathlib import Path
import numpy as np
import pytest

from cv_assistant.identity.store import IdentityStore


@pytest.fixture
def temp_store():
    """Create a temporary IdentityStore for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()
        yield store
        store.close()


def test_create_and_get_person(temp_store):
    person_id = temp_store.create_person(display_name="Alice", consent_status="granted")
    assert person_id is not None
    assert len(person_id) > 0

    person = temp_store.get_person(person_id)
    assert person is not None
    assert person["person_id"] == person_id
    assert person["display_name"] == "Alice"
    assert person["consent_status"] == "granted"


def test_list_people(temp_store):
    assert len(temp_store.list_people()) == 0
    id1 = temp_store.create_person(display_name="User1")
    id2 = temp_store.create_person(display_name="User2")

    people = temp_store.list_people()
    assert len(people) == 2
    ids = {p["person_id"] for p in people}
    assert id1 in ids
    assert id2 in ids


def test_rename_person(temp_store):
    pid = temp_store.create_person(display_name="Unknown")
    success = temp_store.rename_person(pid, "Bablu")
    assert success is True

    updated = temp_store.get_person(pid)
    assert updated["display_name"] == "Bablu"

    # Non-existent person
    assert temp_store.rename_person("non-existent-id", "Nobody") is False


def test_delete_person_and_cascade_embeddings(temp_store):
    pid = temp_store.create_person(display_name="Charlie")
    emb = np.random.randn(512).astype(np.float32)
    temp_store.add_embedding(pid, emb, quality=0.85)
    assert temp_store.get_embedding_count(pid) == 1

    deleted = temp_store.delete_person(pid)
    assert deleted is True
    assert temp_store.get_person(pid) is None
    assert temp_store.get_embedding_count(pid) == 0


def test_add_and_retrieve_embeddings(temp_store):
    pid = temp_store.create_person(display_name="David")
    emb1 = np.random.randn(512).astype(np.float32)
    emb2 = np.random.randn(512).astype(np.float32)

    temp_store.add_embedding(pid, emb1, quality=0.8)
    temp_store.add_embedding(pid, emb2, quality=0.9)

    embeddings = temp_store.get_embeddings(pid)
    assert len(embeddings) == 2
    assert embeddings[0].shape == (512,)
    # Verify embeddings are normalized
    assert np.isclose(np.linalg.norm(embeddings[0]), 1.0, atol=1e-4)


def test_embedding_max_limit_replaces_lowest_quality(temp_store):
    pid = temp_store.create_person(display_name="Eve")
    # Add with qualities: 0.5, 0.9, 0.7 (max 2)
    emb1 = np.random.randn(512)
    emb2 = np.random.randn(512)
    emb3 = np.random.randn(512)

    temp_store.add_embedding(pid, emb1, quality=0.5, max_embeddings=2)
    temp_store.add_embedding(pid, emb2, quality=0.9, max_embeddings=2)
    assert temp_store.get_embedding_count(pid) == 2

    # Adding third should evict the 0.5 quality one
    temp_store.add_embedding(pid, emb3, quality=0.8, max_embeddings=2)
    assert temp_store.get_embedding_count(pid) == 2


def test_get_all_identities_with_embeddings(temp_store):
    pid1 = temp_store.create_person(display_name="WithEmbeddings")
    temp_store.add_embedding(pid1, np.random.randn(512), quality=0.8)

    pid2 = temp_store.create_person(display_name="WithoutEmbeddings")

    all_identities = temp_store.get_all_identities_with_embeddings()
    assert len(all_identities) == 1
    assert all_identities[0]["person_id"] == pid1
    assert len(all_identities[0]["embeddings"]) == 1


def test_model_version_isolation(temp_store):
    pid = temp_store.create_person(display_name="IsolatedPerson")
    emb_r50 = np.random.randn(512).astype(np.float32)
    emb_mbf = np.random.randn(512).astype(np.float32)

    # Store legacy embedding
    temp_store.add_embedding(pid, emb_r50, quality=0.8, model_version="w600k_r50")
    # Store new buffalo_s embedding
    temp_store.add_embedding(pid, emb_mbf, quality=0.9, model_version="w600k_mbf")

    assert temp_store.get_embedding_count(pid, model_version="w600k_r50") == 1
    assert temp_store.get_embedding_count(pid, model_version="w600k_mbf") == 1
    assert temp_store.get_embedding_count(pid) == 2

    # Querying by active model returns only the compatible embedding
    mbf_embeddings = temp_store.get_embeddings(pid, model_version="w600k_mbf")
    assert len(mbf_embeddings) == 1

    r50_embeddings = temp_store.get_embeddings(pid, model_version="w600k_r50")
    assert len(r50_embeddings) == 1

    # get_all_identities_with_embeddings with default model_version="w600k_mbf"
    mbf_identities = temp_store.get_all_identities_with_embeddings(model_version="w600k_mbf")
    assert len(mbf_identities) == 1
    assert len(mbf_identities[0]["embeddings"]) == 1


def test_schema_migration_on_legacy_db():
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy_identities.db"
        # Create legacy database manually without model_version column
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE people (
                person_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT 'Unknown',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                consent_status TEXT NOT NULL DEFAULT 'unknown',
                notes TEXT
            );
            CREATE TABLE face_embeddings (
                embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                quality REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE CASCADE
            );
        """)
        emb_blob = np.random.randn(512).astype(np.float32).tobytes()
        conn.execute("INSERT INTO people VALUES ('legacy-pid-1', 'GladLegacy', '2026-01-01', '2026-01-01', NULL, 'granted', NULL)")
        conn.execute("INSERT INTO face_embeddings VALUES (1, 'legacy-pid-1', ?, 512, 0.95, '2026-01-01')", (emb_blob,))
        conn.commit()
        conn.close()

        # Open with IdentityStore — should migrate table by adding model_version with default 'w600k_r50'
        store = IdentityStore(db_path=db_path)
        store.open()

        people = store.list_people()
        assert len(people) == 1
        assert people[0]["display_name"] == "GladLegacy"

        # Legacy embedding should have model_version 'w600k_r50'
        assert store.get_embedding_count("legacy-pid-1", model_version="w600k_r50") == 1
        # No 'w600k_mbf' embeddings yet
        assert store.get_embedding_count("legacy-pid-1", model_version="w600k_mbf") == 0

        # Adding new embedding to migrated database
        store.add_embedding("legacy-pid-1", np.random.randn(512), quality=0.85, model_version="w600k_mbf")
        assert store.get_embedding_count("legacy-pid-1", model_version="w600k_mbf") == 1
        assert store.get_embedding_count("legacy-pid-1", model_version="w600k_r50") == 1

        store.close()


def test_backup_and_restore_rollback():
    import shutil
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "identities.db"
        store = IdentityStore(db_path=db_path)
        store.open()

        pid = store.create_person(display_name="OriginalName")
        store.add_embedding(pid, np.random.randn(512), quality=0.9, model_version="w600k_r50")
        store.close()

        # Create backup copy
        bak_path = Path(tmpdir) / "identities.db.bak"
        shutil.copy2(db_path, bak_path)

        # Reopen and mutate database
        store.open()
        store.rename_person(pid, "MutatedName")
        store.create_person(display_name="NewPerson")
        assert store.get_person(pid)["display_name"] == "MutatedName"
        assert len(store.list_people()) == 2

        # Rollback via restore_backup
        store.restore_backup(bak_path)

        assert len(store.list_people()) == 1
        assert store.get_person(pid)["display_name"] == "OriginalName"
        assert store.get_embedding_count(pid, model_version="w600k_r50") == 1

        store.close()
