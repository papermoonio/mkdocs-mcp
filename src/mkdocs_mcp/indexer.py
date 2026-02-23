"""Document indexer: orchestrates filesystem scanning and index builds."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import markdown

from mkdocs_mcp.models import IndexStatus
from mkdocs_mcp.repository import DocRepository
from mkdocs_mcp.utils import (
    content_hash,
    extract_headings,
    is_path_contained,
    markdown_to_text,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

# Max file size to index (10 MB) — prevents OOM from huge files
_MAX_FILE_SIZE = 10 * 1024 * 1024


def scan_documents(docs_dir: Path) -> list[Path]:
    """Find all .md files in *docs_dir* recursively.

    Skips hidden files/dirs (starting with '.') and paths that
    escape *docs_dir* via symlinks or traversal.
    """
    results: list[Path] = []
    for path in docs_dir.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(docs_dir).parts):
            continue
        if not is_path_contained(path, docs_dir):
            continue
        results.append(path)
    return sorted(results)


class DocIndexer:
    """Orchestrates documentation indexing.

    Coordinates filesystem scanning (via ``scan_documents``) and database
    persistence (via ``DocRepository``) to build and maintain the FTS5
    search index.
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
        self.repo = DocRepository(self.db_path)
        self._md = markdown.Markdown(extensions=['fenced_code', 'tables'])

    def build_index(self, embedder: Any | None = None) -> IndexStatus:
        """Full index rebuild. Scans all .md files, parses, and indexes.

        Clears existing index data before rebuilding.
        """
        start = time.monotonic()

        try:
            self.repo.clear_documents()

            files = scan_documents(self.docs_dir)
            indexed = 0
            failed = 0

            for file_path in files:
                if self._index_document(file_path, embedder):
                    indexed += 1
                else:
                    failed += 1

            duration = (time.monotonic() - start) * 1000
            self.repo.update_index_info(duration_ms=round(duration, 2))
            self.repo.commit()

            return IndexStatus(
                total_documents=len(files),
                indexed=indexed,
                skipped=0,
                removed=0,
                failed=failed,
                duration_ms=round(duration, 2),
                is_fresh=(failed == 0),
            )
        except Exception:
            self.repo.rollback()
            raise

    def update_index(self, embedder: Any | None = None) -> IndexStatus:
        """Incremental index update based on file mtimes + content_hash.

        Strategy:
        1. mtime unchanged -> skip (fast path, no file read)
        2. mtime changed -> read file, compute hash
           - hash matches stored -> update mtime only, skip re-index
           - hash differs -> full re-index of that file
        3. File deleted -> remove from ALL tables
        4. New file -> index and store
        """
        start = time.monotonic()

        try:
            # Get current files on disk
            current_files = scan_documents(self.docs_dir)
            disk_paths: dict[str, Path] = {}
            for fp in current_files:
                rel = str(fp.relative_to(self.docs_dir))
                disk_paths[rel] = fp

            # Get stored metadata
            stored = self.repo.get_stored_metadata()

            indexed = 0
            skipped = 0
            removed = 0
            failed = 0

            # Handle deleted files
            for stored_path in stored:
                if stored_path not in disk_paths:
                    self.repo.remove_document(stored_path)
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
                            self.repo.update_mtime(rel_path, current_mtime)
                            skipped += 1
                        else:
                            # Content changed — re-index (pass already-loaded data)
                            self.repo.remove_document(rel_path)
                            if self._index_document(
                                abs_path, embedder,
                                preloaded_content=file_content,
                                preloaded_mtime=current_mtime,
                                preloaded_size=file_stat.st_size,
                                preloaded_hash=current_hash,
                            ):
                                indexed += 1
                            else:
                                failed += 1

            duration = (time.monotonic() - start) * 1000
            self.repo.update_index_info(duration_ms=round(duration, 2))
            self.repo.commit()

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
        except Exception:
            self.repo.rollback()
            raise

    def _index_document(
        self,
        file_path: Path,
        embedder: Any | None = None,
        *,
        preloaded_content: str | None = None,
        preloaded_mtime: float | None = None,
        preloaded_size: int | None = None,
        preloaded_hash: str | None = None,
    ) -> bool:
        """Parse and index a single document. Returns True on success.

        When called without pre-loaded data, uses a stat-read-stat pattern
        to avoid TOCTOU races.  When *preloaded_content* (and siblings) are
        provided, skips redundant I/O and hashing — used by ``update_index``
        which has already read the file for change detection.
        """
        rel_path = str(file_path.relative_to(self.docs_dir))

        # --- Obtain content + metadata (read from disk or use pre-loaded) ---
        if preloaded_content is not None:
            file_content = preloaded_content
            mtime = preloaded_mtime or 0.0
            size = preloaded_size or len(file_content.encode("utf-8"))
            file_hash = preloaded_hash or content_hash(file_content)
        else:
            try:
                stat = file_path.stat()
                if stat.st_size > _MAX_FILE_SIZE:
                    logger.warning("Skipping %s: file too large (%d bytes)", file_path, stat.st_size)
                    return False
                file_content = file_path.read_text(encoding="utf-8")
                stat_after = file_path.stat()
                if stat_after.st_mtime != stat.st_mtime:
                    # File changed during read — retry once with fresh state
                    stat = stat_after
                    file_content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError) as e:
                logger.warning("Failed to read %s: %s", file_path, e)
                return False
            mtime = stat.st_mtime
            size = stat.st_size
            file_hash = content_hash(file_content)

        # --- Parse and store ---
        try:
            frontmatter, body = parse_frontmatter(file_content)
            headings = extract_headings(body)
            self._md.reset()
            plain_text = markdown_to_text(file_content, self._md)
            heading_text = " ".join(h["text"] for h in headings)

            title = frontmatter.get("title", "")
            if not title and headings:
                title = headings[0]["text"]
            description = frontmatter.get("description")
            categories = frontmatter.get("categories", [])
            if isinstance(categories, str):
                categories = [c.strip() for c in categories.split(",")]

            self.repo.upsert_document(
                rel_path=rel_path,
                mtime=mtime,
                title=title,
                description=description,
                categories=categories,
                frontmatter=frontmatter,
                content_hash=file_hash,
                size=size,
                heading_text=heading_text,
                plain_text=plain_text,
            )

            # Generate and store embedding if embedder available
            if embedder is not None:
                try:
                    import numpy as _np
                    embedding = _np.asarray(
                        embedder.encode(plain_text[:8192]), dtype=_np.float32
                    ).ravel()
                    self.repo.upsert_embedding(
                        rel_path=rel_path,
                        embedding_blob=embedding.tobytes(),
                        model_name=getattr(embedder, "model_name", "unknown"),
                    )
                except Exception as e:
                    logger.warning("Failed to generate embedding for %s: %s", rel_path, e)

        except Exception as e:
            logger.warning("Failed to index %s: %s", file_path, e)
            return False

        return True

    def get_index_status(self) -> IndexStatus:
        """Get current index status by comparing stored metadata against disk.

        Performs stat()-only checks (no file reads) to count:
        - stale: files whose mtime changed or are new on disk
        - removed: files in the index that no longer exist on disk
        """
        total = self.repo.get_document_count()
        duration = self.repo.get_last_build_duration()

        # Compare disk state against stored metadata (stat-only, no file reads)
        stored = self.repo.get_stored_metadata()
        current_files = scan_documents(self.docs_dir)
        disk_paths: set[str] = set()
        stale = 0
        for fp in current_files:
            rel = str(fp.relative_to(self.docs_dir))
            disk_paths.add(rel)
            if rel not in stored:
                stale += 1  # new file, not yet indexed
            else:
                stored_mtime, _ = stored[rel]
                try:
                    if fp.stat().st_mtime != stored_mtime:
                        stale += 1  # mtime changed
                except OSError:
                    stale += 1

        removed = sum(1 for p in stored if p not in disk_paths)

        return IndexStatus(
            total_documents=total,
            indexed=total,
            skipped=0,
            removed=removed,
            failed=stale,
            duration_ms=duration,
            is_fresh=(stale == 0 and removed == 0),
        )

    def get_document_metadata(self, rel_path: str) -> dict[str, Any] | None:
        """Get metadata for a single document by relative path."""
        return self.repo.get_document_metadata(rel_path)

    def close(self) -> None:
        """Close the database connection."""
        self.repo.close()

    def __enter__(self) -> DocIndexer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
