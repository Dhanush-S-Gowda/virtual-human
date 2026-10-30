"""Persistent identity store backed by SQLite.

Stores person records and face embeddings with proper transactional
safety.  Embeddings are stored as binary blobs with explicit dimension
metadata.  The database path is configurable; database files are
excluded from version control.

**Privacy note**: Face embeddings are biometric data.  This store
supports consent tracking and explicit deletion.  The system should
not be used as an authentication or security mechanism.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from cv_assistant.faces.embeddings import normalise_embedding

logger = logging.getLogger(__name__)


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT 'Unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    consent_status TEXT NOT NULL DEFAULT 'unknown',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_dim INTEGER NOT NULL,
    quality REAL NOT NULL,
    created_at TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'w600k_r50',
    FOREIGN KEY(person_id) REFERENCES people(person_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_embeddings_person
    ON face_embeddings(person_id);
"""


class IdentityStore:
    """SQLite-backed persistent identity and face embedding store.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database file.  Parent directories are created
        automatically.
    """

    def __init__(self, db_path: str | Path = "data/identities.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        """Open the database connection and ensure tables exist."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_CREATE_TABLES_SQL)

        # Ensure migration: check if model_version column exists in face_embeddings
        columns = [
            row[1]
            for row in self._conn.execute("PRAGMA table_info(face_embeddings)").fetchall()
        ]
        if "model_version" not in columns:
            logger.info("Migrating face_embeddings table: adding model_version column...")
            self._conn.execute(
                "ALTER TABLE face_embeddings ADD COLUMN model_version TEXT NOT NULL DEFAULT 'w600k_r50'"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON face_embeddings(person_id, model_version)"
        )

        self._conn.commit()
        logger.info("Identity store opened: %s", self._db_path)

    def restore_backup(self, backup_path: str | Path | None = None) -> None:
        """Restore database from a backup file."""
        import shutil
        if backup_path is None:
            backup_path = self._db_path.with_suffix(".db.bak")
        backup_file = Path(backup_path)
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup file not found at {backup_file}")
        self.close()
        shutil.copy2(backup_file, self._db_path)
        self.open()
        logger.info("Restored database from backup: %s", backup_file)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Identity store closed.")

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("IdentityStore is not open.")
        return self._conn

    # ── Person CRUD ─────────────────────────────────────────────────────

    def create_person(
        self,
        display_name: str = "Unknown",
        consent_status: str = "unknown",
        notes: str | None = None,
    ) -> str:
        """Create a new person record.  Returns the generated ``person_id``."""
        person_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO people
               (person_id, display_name, created_at, updated_at, consent_status, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (person_id, display_name, now, now, consent_status, notes),
        )
        self._db.commit()
        logger.info("Created person: id=%s name=%s", person_id, display_name)
        return person_id

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        """Fetch a person record by ID."""
        row = self._db.execute(
            "SELECT * FROM people WHERE person_id = ?", (person_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_person(row)

    def list_people(self) -> list[dict[str, Any]]:
        """List all person records."""
        rows = self._db.execute(
            "SELECT * FROM people ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_person(r) for r in rows]

    def rename_person(self, person_id: str, new_name: str) -> bool:
        """Rename a person.  Returns True if the record was found."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._db.execute(
            "UPDATE people SET display_name = ?, updated_at = ? WHERE person_id = ?",
            (new_name, now, person_id),
        )
        self._db.commit()
        if cursor.rowcount > 0:
            logger.info("Renamed person %s -> %s", person_id, new_name)
            return True
        return False

    def update_last_seen(self, person_id: str) -> None:
        """Update the last_seen_at timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "UPDATE people SET last_seen_at = ?, updated_at = ? WHERE person_id = ?",
            (now, now, person_id),
        )
        self._db.commit()

    def delete_person(self, person_id: str) -> bool:
        """Delete a person and all associated embeddings.

        Returns True if the person existed.
        """
        # Foreign key CASCADE handles embedding deletion
        cursor = self._db.execute(
            "DELETE FROM people WHERE person_id = ?", (person_id,)
        )
        self._db.commit()
        if cursor.rowcount > 0:
            logger.info("Deleted person and embeddings: %s", person_id)
            return True
        return False

    # ── Embedding management ────────────────────────────────────────────

    def add_embedding(
        self,
        person_id: str,
        embedding: np.ndarray,
        quality: float,
        max_embeddings: int = 10,
        model_version: str = "w600k_mbf",
    ) -> int:
        """Store a face embedding for a person.

        If the person already has ``max_embeddings`` for the specified model_version,
        the lowest-quality one for that model_version is removed to make room.

        Returns the new embedding_id.
        """
        embedding = normalise_embedding(embedding)
        blob = embedding.astype(np.float32).tobytes()
        dim = embedding.shape[0]
        now = datetime.now(timezone.utc).isoformat()

        # Check current count for this person and model_version
        count = self._db.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE person_id = ? AND model_version = ?",
            (person_id, model_version),
        ).fetchone()[0]

        if count >= max_embeddings:
            # Remove lowest quality embedding for this model_version
            self._db.execute(
                """DELETE FROM face_embeddings
                   WHERE embedding_id = (
                       SELECT embedding_id FROM face_embeddings
                       WHERE person_id = ? AND model_version = ?
                       ORDER BY quality ASC LIMIT 1
                   )""",
                (person_id, model_version),
            )

        cursor = self._db.execute(
            """INSERT INTO face_embeddings
               (person_id, embedding, embedding_dim, quality, created_at, model_version)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (person_id, blob, dim, quality, now, model_version),
        )
        self._db.commit()
        return cursor.lastrowid

    def get_embeddings(
        self,
        person_id: str,
        model_version: str | None = "w600k_mbf",
    ) -> list[np.ndarray]:
        """Load embeddings for a person as numpy arrays, filtered by model_version if provided."""
        if model_version:
            rows = self._db.execute(
                "SELECT embedding, embedding_dim FROM face_embeddings WHERE person_id = ? AND model_version = ?",
                (person_id, model_version),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT embedding, embedding_dim FROM face_embeddings WHERE person_id = ?",
                (person_id,),
            ).fetchall()

        embeddings = []
        for blob, dim in rows:
            arr = np.frombuffer(blob, dtype=np.float32)
            if len(arr) != dim:
                logger.warning(
                    "Embedding dimension mismatch for person %s: expected %d, got %d",
                    person_id, dim, len(arr),
                )
                continue
            embeddings.append(arr)
        return embeddings

    def get_all_identities_with_embeddings(
        self,
        model_version: str | None = "w600k_mbf",
    ) -> list[dict[str, Any]]:
        """Load all people with their embeddings for matching against active model_version."""
        people = self.list_people()
        result = []
        for person in people:
            embeddings = self.get_embeddings(person["person_id"], model_version=model_version)
            if embeddings:
                result.append({
                    "person_id": person["person_id"],
                    "display_name": person["display_name"],
                    "consent_status": person["consent_status"],
                    "embeddings": embeddings,
                })
        return result

    def get_embedding_count(
        self,
        person_id: str,
        model_version: str | None = None,
    ) -> int:
        """Return the number of stored embeddings for a person, optionally filtered by model_version."""
        if model_version:
            row = self._db.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE person_id = ? AND model_version = ?",
                (person_id, model_version),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return row[0] if row else 0

    # ── Helpers ─────────────────────────────────────────────────────────

    def _row_to_person(self, row: tuple) -> dict[str, Any]:
        """Convert a database row to a person dict."""
        return {
            "person_id": row[0],
            "display_name": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "last_seen_at": row[4],
            "consent_status": row[5],
            "notes": row[6],
        }
