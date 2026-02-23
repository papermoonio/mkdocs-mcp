"""SQLite repository: schema management and all database operations."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DocRepository:
    """Owns the write connection to the SQLite database.

    Manages schema creation and provides methods for all CRUD operations
    against the doc_metadata, docs_fts, doc_embeddings, and index_info tables.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS doc_metadata (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                description TEXT,
                categories TEXT,
                frontmatter TEXT,
                content_hash TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                path UNINDEXED,
                title,
                headings,
                content,
                tokenize='porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS doc_embeddings (
                path TEXT PRIMARY KEY,
                embedding BLOB,
                model_name TEXT
            );

            CREATE TABLE IF NOT EXISTS index_info (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self._conn.commit()

    def clear_documents(self) -> None:
        """Delete all document data from doc_metadata, docs_fts, and doc_embeddings.

        Does NOT clear ``index_info`` (build metadata).
        """
        self._conn.execute("DELETE FROM doc_metadata")
        self._conn.execute("DELETE FROM docs_fts")
        self._conn.execute("DELETE FROM doc_embeddings")

    def upsert_document(
        self,
        rel_path: str,
        mtime: float,
        title: str,
        description: str | None,
        categories: list[str],
        frontmatter: dict[str, Any],
        content_hash: str,
        size: int,
        heading_text: str,
        plain_text: str,
    ) -> None:
        """Insert or replace a document in doc_metadata and docs_fts."""
        self._conn.execute(
            """INSERT OR REPLACE INTO doc_metadata
               (path, mtime, title, description, categories, frontmatter, content_hash, size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rel_path, mtime, title, description,
                json.dumps(categories), json.dumps(frontmatter),
                content_hash, size,
            ),
        )
        # FTS5 doesn't support REPLACE -- delete first
        self._conn.execute("DELETE FROM docs_fts WHERE path = ?", (rel_path,))
        self._conn.execute(
            "INSERT INTO docs_fts (path, title, headings, content) VALUES (?, ?, ?, ?)",
            (rel_path, title, heading_text, plain_text),
        )

    def upsert_embedding(
        self, rel_path: str, embedding_blob: bytes, model_name: str
    ) -> None:
        """Insert or replace a document embedding."""
        self._conn.execute(
            """INSERT OR REPLACE INTO doc_embeddings (path, embedding, model_name)
               VALUES (?, ?, ?)""",
            (rel_path, embedding_blob, model_name),
        )

    def remove_document(self, rel_path: str) -> None:
        """Remove a document from ALL tables."""
        self._conn.execute("DELETE FROM doc_metadata WHERE path = ?", (rel_path,))
        self._conn.execute("DELETE FROM docs_fts WHERE path = ?", (rel_path,))
        self._conn.execute("DELETE FROM doc_embeddings WHERE path = ?", (rel_path,))

    def get_stored_metadata(self) -> dict[str, tuple[float, str]]:
        """Return all stored {path: (mtime, content_hash)}."""
        cursor = self._conn.execute(
            "SELECT path, mtime, content_hash FROM doc_metadata"
        )
        return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

    def update_mtime(self, rel_path: str, mtime: float) -> None:
        """Update only the mtime for a document (content unchanged)."""
        self._conn.execute(
            "UPDATE doc_metadata SET mtime = ? WHERE path = ?",
            (mtime, rel_path),
        )

    def get_document_count(self) -> int:
        """Return total number of indexed documents."""
        return self._conn.execute("SELECT COUNT(*) FROM doc_metadata").fetchone()[0]

    def get_last_build_duration(self) -> float:
        """Return the last build duration in ms, or 0.0 if unknown."""
        row = self._conn.execute(
            "SELECT value FROM index_info WHERE key = 'last_build_duration_ms'"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def get_document_metadata(self, rel_path: str) -> dict[str, Any] | None:
        """Get metadata for a single document by relative path."""
        cursor = self._conn.execute(
            """SELECT path, mtime, title, description, categories, frontmatter
               FROM doc_metadata WHERE path = ?""",
            (rel_path,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "path": row[0],
            "mtime": row[1],
            "title": row[2],
            "description": row[3],
            "categories": json.loads(row[4]) if row[4] else [],
            "frontmatter": json.loads(row[5]) if row[5] else {},
        }

    def update_index_info(self, duration_ms: float = 0.0) -> None:
        """Update index_info with build metadata. Does NOT commit."""
        self._conn.execute(
            "INSERT OR REPLACE INTO index_info (key, value) VALUES (?, ?)",
            ("last_build_time", str(time.time())),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO index_info (key, value) VALUES (?, ?)",
            ("last_build_duration_ms", str(duration_ms)),
        )

    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self._conn.rollback()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> DocRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
