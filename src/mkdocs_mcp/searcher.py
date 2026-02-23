"""Document searcher: read-only FTS5 keyword, vector, and hybrid search."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from mkdocs_mcp.models import SearchMatch, SearchResult
from mkdocs_mcp.utils import sanitize_fts_query

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

_VALID_SEARCH_TYPES = frozenset({"keyword", "vector", "hybrid"})


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion score for a given 1-based rank."""
    return 1.0 / (k + rank)


class DocSearcher:
    """Read-only searcher against the SQLite FTS5 + vector index.

    Supports keyword (BM25), vector (cosine similarity), and hybrid (RRF) search.
    """

    def __init__(self, db_path: Path, embedder: Any | None = None) -> None:
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.execute("PRAGMA query_only = ON")
        self._embedder = embedder
        self._embedding_cache: dict[str, Any] | None = None
        self._cache_paths: list[str] | None = None
        self._cache_matrix: Any | None = None  # numpy matrix of all embeddings
        self._cache_norms: Any | None = None  # precomputed L2 norms per document
        self._cache_hashes: dict[str, str] = {}  # path -> content_hash for change detection
        self._db_path = db_path
        self._db_mtime: float = 0.0
        self._cache_lock = threading.Lock()

    def search(
        self, query: str, search_type: str = "hybrid", max_results: int = 10
    ) -> SearchResult:
        """Dispatch to the appropriate search method.

        Raises:
            ValueError: If *search_type* is not ``"keyword"``, ``"vector"``, or ``"hybrid"``.
        """
        if search_type not in _VALID_SEARCH_TYPES:
            raise ValueError(
                f"Invalid search_type {search_type!r}; must be one of {sorted(_VALID_SEARCH_TYPES)}"
            )
        max_results = _clamp(max_results, 1, 100)

        if not query or not query.strip():
            return SearchResult(
                query=query or "", search_type=search_type, results=[], total_count=0
            )

        if search_type == "keyword":
            matches = self.keyword_search(query, max_results)
            total_count = self._keyword_match_count(query)
        elif search_type == "vector":
            matches = self.vector_search(query, max_results)
            total_count = len(matches)  # vector search has no efficient count
        else:
            matches = self.hybrid_search(query, max_results)
            total_count = self._keyword_match_count(query)

        return SearchResult(
            query=query, search_type=search_type, results=matches,
            total_count=max(total_count, len(matches)),
        )

    def keyword_search(self, query: str, max_results: int = 10) -> list[SearchMatch]:
        """BM25-ranked full-text search via FTS5."""
        if not query or not query.strip():
            return []

        max_results = _clamp(max_results, 1, 100)
        safe_query = sanitize_fts_query(query)
        if not safe_query:
            return []

        if self._conn is None:
            raise RuntimeError("Searcher is closed")
        cursor = self._conn.execute(
            """
            SELECT
                f.path,
                COALESCE(m.title, f.title, ''),
                bm25(docs_fts, 0, 10, 5, 1) AS rank,
                snippet(docs_fts, 3, '<b>', '</b>', '...', 32),
                COALESCE(m.description, '')
            FROM docs_fts AS f
            LEFT JOIN doc_metadata AS m ON m.path = f.path
            WHERE docs_fts MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (safe_query, max_results),
        )

        results: list[SearchMatch] = []
        for path, title, rank, snippet_text, _desc in cursor.fetchall():
            # bm25() returns negative values; negate so higher = better
            results.append(
                SearchMatch(
                    path=path,
                    title=title or path,
                    score=-rank,
                    snippet=snippet_text or "",
                    search_method="keyword",
                )
            )

        # Normalize scores to 0.0-1.0 (top result = 1.0)
        if results:
            max_score = results[0].score
            if max_score > 0:
                for match in results:
                    match.score /= max_score

        return results

    def _keyword_match_count(self, query: str) -> int:
        """Return the total number of FTS5 matches for *query* (no LIMIT)."""
        safe_query = sanitize_fts_query(query)
        if not safe_query or self._conn is None:
            return 0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM docs_fts WHERE docs_fts MATCH ?",
            (safe_query,),
        ).fetchone()
        return row[0] if row else 0

    def vector_search(self, query: str, max_results: int = 10) -> list[SearchMatch]:
        """Cosine-similarity search against stored document embeddings."""
        if self._embedder is None or not _HAS_NUMPY:
            return []
        if not query or not query.strip():
            return []

        max_results = _clamp(max_results, 1, 100)
        self._load_embedding_cache()

        if not self._embedding_cache:
            return []

        assert self._cache_matrix is not None
        assert self._cache_paths is not None

        # Encode query (truncate to prevent OOM on huge inputs)
        query_vec = np.asarray(self._embedder.encode(query[:8192]), dtype=np.float32).ravel()

        # Cosine similarity: dot(q, D) / (|q| * |D|)
        query_norm = float(np.linalg.norm(query_vec))
        if query_norm == 0:
            return []

        assert self._cache_norms is not None
        similarities = self._cache_matrix @ query_vec / (self._cache_norms * query_norm)

        # Top-k indices (descending similarity)
        k = min(max_results, len(self._cache_paths))
        top_indices = np.argsort(similarities)[::-1][:k]

        # Filter to positive-score matches and collect their paths
        if self._conn is None:
            raise RuntimeError("Searcher is closed")

        scored: list[tuple[str, float]] = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score <= 0:
                continue
            scored.append((self._cache_paths[idx], score))

        if not scored:
            return []

        matched_paths = [p for p, _ in scored]
        placeholders = ",".join("?" for _ in matched_paths)

        # Batch metadata lookup (single query instead of N)
        meta_rows = self._conn.execute(
            f"SELECT path, title, description FROM doc_metadata "  # noqa: S608
            f"WHERE path IN ({placeholders})",
            matched_paths,
        ).fetchall()
        meta_map: dict[str, tuple[str, str]] = {
            row[0]: (row[1] or "", row[2] or "") for row in meta_rows
        }

        # Batch snippet extraction (single FTS5 query instead of N)
        safe_query = sanitize_fts_query(query)
        snippet_map: dict[str, str] = {}
        if safe_query:
            try:
                snip_rows = self._conn.execute(
                    "SELECT path, snippet(docs_fts, 3, '<b>', '</b>', '...', 32) "
                    f"FROM docs_fts WHERE docs_fts MATCH ? AND path IN ({placeholders})",  # noqa: S608
                    [safe_query, *matched_paths],
                ).fetchall()
                for spath, snip in snip_rows:
                    if snip:
                        snippet_map[spath] = snip
            except sqlite3.OperationalError:
                pass  # FTS query may fail on unusual terms

        results: list[SearchMatch] = []
        for doc_path, score in scored:
            title, desc = meta_map.get(doc_path, (doc_path, ""))
            snippet_text = snippet_map.get(doc_path, "")
            results.append(
                SearchMatch(
                    path=doc_path,
                    title=title or doc_path,
                    score=score,
                    snippet=snippet_text or desc,
                    search_method="vector",
                )
            )
        return results

    def hybrid_search(self, query: str, max_results: int = 10) -> list[SearchMatch]:
        """Combined keyword + vector search using Reciprocal Rank Fusion."""
        max_results = _clamp(max_results, 1, 100)
        fetch_count = max_results * 2

        kw_results = self.keyword_search(query, fetch_count)
        vec_results = self.vector_search(query, fetch_count)

        if not kw_results and not vec_results:
            return []

        # Always apply RRF scoring, even when only one engine has results.
        # This keeps hybrid scores on a consistent scale.
        rrf_scores: dict[str, float] = {}
        best_match: dict[str, SearchMatch] = {}

        for rank, match in enumerate(kw_results, start=1):
            rrf_scores[match.path] = rrf_scores.get(match.path, 0.0) + _rrf_score(rank)
            best_match[match.path] = match

        for rank, match in enumerate(vec_results, start=1):
            rrf_scores[match.path] = rrf_scores.get(match.path, 0.0) + _rrf_score(rank)
            if match.path not in best_match:
                best_match[match.path] = match

        # Sort by combined RRF score descending
        sorted_paths = sorted(rrf_scores, key=lambda p: rrf_scores[p], reverse=True)

        # Normalize RRF scores to 0.0-1.0 (top result = 1.0)
        max_rrf = rrf_scores[sorted_paths[0]]

        results: list[SearchMatch] = []
        for path in sorted_paths[:max_results]:
            original = best_match[path]
            results.append(
                SearchMatch(
                    path=original.path,
                    title=original.title,
                    score=rrf_scores[path] / max_rrf,
                    snippet=original.snippet,
                    search_method="hybrid",
                )
            )
        return results

    def _load_embedding_cache(self) -> None:
        """Load or incrementally refresh the embedding cache when DB changes.

        Uses content_hash from doc_metadata as a lightweight change fingerprint
        to avoid reloading unchanged embedding BLOBs.

        Thread-safe: a lock ensures concurrent requests don't trigger
        redundant reloads or read partially-updated state.
        """
        if not _HAS_NUMPY:
            return

        try:
            current_mtime = os.path.getmtime(self._db_path)
        except OSError:
            return

        # Fast path (no lock): cache is fresh
        if self._embedding_cache is not None and current_mtime == self._db_mtime:
            return

        with self._cache_lock:
            # Re-check under lock (another thread may have refreshed)
            if self._embedding_cache is not None and current_mtime == self._db_mtime:
                return

            if self._conn is None:
                raise RuntimeError("Searcher is closed")

            # Lightweight query: paths + content hashes (no BLOBs)
            index_rows = self._conn.execute(
                "SELECT de.path, dm.content_hash "
                "FROM doc_embeddings de "
                "JOIN doc_metadata dm ON de.path = dm.path"
            ).fetchall()
            current_hashes: dict[str, str] = {
                path: chash for path, chash in index_rows
            }

            # If cache exists and fingerprints match, just update mtime
            if (
                self._embedding_cache is not None
                and current_hashes == self._cache_hashes
            ):
                self._db_mtime = current_mtime
                return

            # Determine what changed
            cached_keys = set(self._cache_hashes)
            current_keys = set(current_hashes)

            added = current_keys - cached_keys
            removed = cached_keys - current_keys
            changed = {
                p
                for p in cached_keys & current_keys
                if current_hashes[p] != self._cache_hashes[p]
            }
            needs_load = added | changed

            # First load or major change (>50% affected) -> full reload
            if (
                self._embedding_cache is None
                or not current_hashes
                or (len(needs_load) + len(removed) > len(current_hashes) // 2)
            ):
                self._full_reload_cache(current_hashes, current_mtime)
                return

            # Nothing actually changed in embeddings
            if not needs_load and not removed:
                self._cache_hashes = current_hashes
                self._db_mtime = current_mtime
                return

            # --- Incremental update ---
            # Remove deleted/changed entries from cache dict
            for p in removed | changed:
                self._embedding_cache.pop(p, None)

            # Load only new/changed embeddings
            if needs_load:
                placeholders = ",".join("?" for _ in needs_load)
                rows = self._conn.execute(
                    f"SELECT path, embedding FROM doc_embeddings "  # noqa: S608
                    f"WHERE path IN ({placeholders})",
                    list(needs_load),
                ).fetchall()
                for path, blob in rows:
                    self._embedding_cache[path] = np.frombuffer(
                        blob, dtype=np.float32
                    ).copy()

            # Rebuild derived structures from updated cache
            self._rebuild_matrix()
            self._cache_hashes = current_hashes
            self._db_mtime = current_mtime

    def _full_reload_cache(
        self, current_hashes: dict[str, str], current_mtime: float
    ) -> None:
        """Full reload of all embeddings from DB (used on first load or major changes)."""
        assert self._conn is not None

        rows = self._conn.execute(
            "SELECT path, embedding FROM doc_embeddings"
        ).fetchall()

        if not rows:
            self._embedding_cache = {}
            self._cache_paths = []
            self._cache_matrix = None
            self._cache_norms = None
            self._cache_hashes = current_hashes
            self._db_mtime = current_mtime
            return

        cache: dict[str, Any] = {}
        for path, blob in rows:
            cache[path] = np.frombuffer(blob, dtype=np.float32).copy()

        self._embedding_cache = cache
        self._rebuild_matrix()
        self._cache_hashes = current_hashes
        self._db_mtime = current_mtime

    def _rebuild_matrix(self) -> None:
        """Rebuild the numpy matrix and norms from the embedding cache dict."""
        if not self._embedding_cache:
            self._cache_paths = []
            self._cache_matrix = None
            self._cache_norms = None
            return

        self._cache_paths = sorted(self._embedding_cache.keys())
        vectors = [self._embedding_cache[p] for p in self._cache_paths]
        self._cache_matrix = np.vstack(vectors)
        norms = np.linalg.norm(self._cache_matrix, axis=1)
        self._cache_norms = np.where(norms == 0, 1.0, norms)

    def list_documents(self, section: str | None = None) -> list[tuple]:
        """List all indexed documents, optionally filtered by section prefix."""
        if self._conn is None:
            return []
        if section:
            prefix = section.rstrip("/") + "/"
            cursor = self._conn.execute(
                "SELECT path, title, description, categories, mtime, size FROM doc_metadata "
                "WHERE path LIKE ? ORDER BY path",
                (prefix + "%",),
            )
        else:
            cursor = self._conn.execute(
                "SELECT path, title, description, categories, mtime, size FROM doc_metadata "
                "ORDER BY path"
            )
        return cursor.fetchall()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> DocSearcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
