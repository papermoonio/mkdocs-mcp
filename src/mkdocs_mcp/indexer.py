"""Document indexer: SQLite FTS5 full-text index with incremental updates."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from mkdocs_mcp.models import IndexStatus
from mkdocs_mcp.utils import (
    content_hash,
    extract_headings,
    markdown_to_text,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

# Max file size to index (10 MB) — prevents OOM from huge files
_MAX_FILE_SIZE = 10 * 1024 * 1024


class DocIndexer:
    """Manages the SQLite FTS5 search index for MkDocs documentation.

    Owns the write connection to the database. Creates the schema on init,
    supports full and incremental index builds. Uses WAL mode for safe
    concurrent reads during writes.
    """

    def __init__(self, docs_dir: Path, db_path: Path | None = None):
        """Initialize the indexer.

        Args:
            docs_dir: Absolute path to the documentation directory.
            db_path: Path for the SQLite database file.
                     Defaults to docs_dir/../.mkdocs-mcp.db
        """
        self.docs_dir = docs_dir.resolve()
        self.db_path = db_path or (self.docs_dir.parent / ".mkdocs-mcp.db")
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS doc_metadata (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                description TEXT,
                categories TEXT,
                frontmatter TEXT,
                content_hash TEXT NOT NULL
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

    def build_index(self, embedder: Any | None = None) -> IndexStatus:
        """Full index rebuild. Scans all .md files, parses, and indexes.

        Clears existing index data before rebuilding.
        """
        start = time.monotonic()

        try:
            # Clear all existing data (avoid executescript — it forces implicit commits)
            self._conn.execute("DELETE FROM doc_metadata")
            self._conn.execute("DELETE FROM docs_fts")
            self._conn.execute("DELETE FROM doc_embeddings")

            files = self._scan_documents()
            indexed = 0
            failed = 0

            for file_path in files:
                if self._index_document(file_path, embedder):
                    indexed += 1
                else:
                    failed += 1

            duration = (time.monotonic() - start) * 1000
            self._update_index_info(duration_ms=round(duration, 2))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return IndexStatus(
            total_documents=len(files),
            indexed=indexed,
            skipped=0,
            removed=0,
            failed=failed,
            duration_ms=round(duration, 2),
            is_fresh=(failed == 0),
        )

    def update_index(self, embedder: Any | None = None) -> IndexStatus:
        """Incremental index update based on file mtimes + content_hash.

        Strategy:
        1. mtime unchanged → skip (fast path, no file read)
        2. mtime changed → read file, compute hash
           - hash matches stored → update mtime only, skip re-index
           - hash differs → full re-index of that file
        3. File deleted → remove from ALL tables
        4. New file → index and store
        """
        start = time.monotonic()

        try:
            # Get current files on disk
            current_files = self._scan_documents()
            disk_paths = {}
            for fp in current_files:
                rel = str(fp.relative_to(self.docs_dir))
                disk_paths[rel] = fp

            # Get stored metadata
            stored = self._get_stored_metadata()

            indexed = 0
            skipped = 0
            removed = 0
            failed = 0

            # Handle deleted files
            for stored_path in stored:
                if stored_path not in disk_paths:
                    self._remove_document(stored_path)
                    removed += 1

            # Handle new and modified files
            for rel_path, abs_path in disk_paths.items():
                if rel_path not in stored:
                    # New file
                    if self._index_document(abs_path, embedder):
                        indexed += 1
                    else:
                        failed += 1
                else:
                    stored_mtime, stored_hash = stored[rel_path]
                    try:
                        file_stat = abs_path.stat()
                    except OSError:
                        failed += 1
                        continue
                    current_mtime = file_stat.st_mtime

                    # Float comparison is safe: Python float == SQLite REAL round-trips exactly.
                    # Content-hash fallback handles edge cases from coarse-grained filesystems.
                    if current_mtime == stored_mtime:
                        # Unchanged — skip
                        skipped += 1
                    else:
                        # mtime changed — check hash (skip oversized files)
                        if file_stat.st_size > _MAX_FILE_SIZE:
                            logger.warning("Skipping %s: file too large", abs_path)
                            failed += 1
                            continue
                        try:
                            file_content = abs_path.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            failed += 1
                            continue

                        current_hash = content_hash(file_content)
                        if current_hash == stored_hash:
                            # Content identical — update mtime only
                            self._conn.execute(
                                "UPDATE doc_metadata SET mtime = ? WHERE path = ?",
                                (current_mtime, rel_path),
                            )
                            skipped += 1
                        else:
                            # Content changed — re-index
                            self._remove_document(rel_path)
                            if self._index_document(abs_path, embedder):
                                indexed += 1
                            else:
                                failed += 1

            duration = (time.monotonic() - start) * 1000
            self._update_index_info(duration_ms=round(duration, 2))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        total = len(disk_paths)
        return IndexStatus(
            total_documents=total,
            indexed=indexed,
            skipped=skipped,
            removed=removed,
            failed=failed,
            duration_ms=round(duration, 2),
            is_fresh=(failed == 0),
        )

    def _scan_documents(self) -> list[Path]:
        """Find all .md files in docs_dir recursively.

        Skips:
        - Files/dirs starting with '.'
        - Symlinks whose targets escape docs_dir
        - Non-.md files
        """
        docs_resolved = self.docs_dir.resolve()
        results: list[Path] = []

        for path in self.docs_dir.rglob("*.md"):
            # Skip hidden files/directories
            if any(part.startswith(".") for part in path.relative_to(self.docs_dir).parts):
                continue

            # Skip symlinks that escape docs_dir
            if path.is_symlink():
                target = path.resolve()
                if not target.is_relative_to(docs_resolved):
                    continue

            # Verify the resolved path is within docs_dir
            resolved = path.resolve()
            if not resolved.is_relative_to(docs_resolved):
                continue

            results.append(path)

        return sorted(results)

    def _index_document(
        self, file_path: Path, embedder: Any | None = None
    ) -> bool:
        """Parse and index a single document. Returns True on success."""
        try:
            rel_path = str(file_path.relative_to(self.docs_dir))
            stat = file_path.stat()
            if stat.st_size > _MAX_FILE_SIZE:
                logger.warning("Skipping %s: file too large (%d bytes)", file_path, stat.st_size)
                return False
            file_content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return False

        try:
            frontmatter, body = parse_frontmatter(file_content)
            headings = extract_headings(body)
            plain_text = markdown_to_text(file_content)
            heading_text = " ".join(h["text"] for h in headings)

            title = frontmatter.get("title", "")
            if not title and headings:
                title = headings[0]["text"]
            description = frontmatter.get("description")
            categories = frontmatter.get("categories", [])
            if isinstance(categories, str):
                categories = [c.strip() for c in categories.split(",")]

            file_hash = content_hash(file_content)

            # Insert into metadata table
            self._conn.execute(
                """INSERT OR REPLACE INTO doc_metadata
                   (path, mtime, title, description, categories, frontmatter, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rel_path,
                    stat.st_mtime,
                    title,
                    description,
                    json.dumps(categories),
                    json.dumps(frontmatter),
                    file_hash,
                ),
            )

            # Insert into FTS5 table
            # First delete any existing entry (FTS5 doesn't support REPLACE)
            self._conn.execute("DELETE FROM docs_fts WHERE path = ?", (rel_path,))
            self._conn.execute(
                "INSERT INTO docs_fts (path, title, headings, content) VALUES (?, ?, ?, ?)",
                (rel_path, title, heading_text, plain_text),
            )

            # Generate and store embedding if embedder available
            if embedder is not None:
                try:
                    embedding = embedder.encode(plain_text[:8192])  # Cap input length
                    self._conn.execute(
                        """INSERT OR REPLACE INTO doc_embeddings (path, embedding, model_name)
                           VALUES (?, ?, ?)""",
                        (
                            rel_path,
                            embedding.tobytes(),
                            getattr(embedder, "model_name", "unknown"),
                        ),
                    )
                except Exception as e:
                    logger.warning("Failed to generate embedding for %s: %s", rel_path, e)

        except Exception as e:
            logger.warning("Failed to index %s: %s", file_path, e)
            return False

        return True

    def _get_stored_metadata(self) -> dict[str, tuple[float, str]]:
        """Get all stored path -> (mtime, content_hash) from doc_metadata."""
        cursor = self._conn.execute(
            "SELECT path, mtime, content_hash FROM doc_metadata"
        )
        return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

    def _remove_document(self, rel_path: str) -> None:
        """Remove a document from ALL tables: doc_metadata, docs_fts, doc_embeddings."""
        self._conn.execute("DELETE FROM doc_metadata WHERE path = ?", (rel_path,))
        self._conn.execute("DELETE FROM docs_fts WHERE path = ?", (rel_path,))
        self._conn.execute("DELETE FROM doc_embeddings WHERE path = ?", (rel_path,))

    def get_index_status(self) -> IndexStatus:
        """Get current index status: doc count, last build time, stale count."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM doc_metadata")
        total = cursor.fetchone()[0]

        cursor = self._conn.execute(
            "SELECT value FROM index_info WHERE key = 'last_build_duration_ms'"
        )
        row = cursor.fetchone()
        duration = float(row[0]) if row else 0.0

        return IndexStatus(
            total_documents=total,
            indexed=total,
            skipped=0,
            removed=0,
            failed=0,
            duration_ms=duration,
            is_fresh=True,
        )

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

    def _update_index_info(self, duration_ms: float = 0.0) -> None:
        """Update index_info with current build metadata.

        Args:
            duration_ms: Build duration in milliseconds.

        Does NOT commit — caller is responsible for committing.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO index_info (key, value) VALUES (?, ?)",
            ("last_build_time", str(time.time())),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO index_info (key, value) VALUES (?, ?)",
            ("last_build_duration_ms", str(duration_ms)),
        )

    def __enter__(self) -> DocIndexer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]
