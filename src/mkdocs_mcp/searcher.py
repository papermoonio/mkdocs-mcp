"""Document searcher: read-only FTS5 keyword, vector, and hybrid search."""

from __future__ import annotations

import logging
import os
import sqlite3
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
        self._db_path = db_path
        self._db_mtime: float = 0.0

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
        elif search_type == "vector":
            matches = self.vector_search(query, max_results)
        else:
            matches = self.hybrid_search(query, max_results)

        return SearchResult(
            query=query, search_type=search_type, results=matches, total_count=len(matches)
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
        return results

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

        doc_norms = np.linalg.norm(self._cache_matrix, axis=1)
        safe_norms = np.where(doc_norms == 0, 1.0, doc_norms)
        similarities = self._cache_matrix @ query_vec / (safe_norms * query_norm)

        # Top-k indices (descending similarity)
        k = min(max_results, len(self._cache_paths))
        top_indices = np.argsort(similarities)[::-1][:k]

        # Look up metadata for matched paths
        if self._conn is None:
            raise RuntimeError("Searcher is closed")
        results: list[SearchMatch] = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score <= 0:
                continue
            doc_path = self._cache_paths[idx]
            cursor = self._conn.execute(
                "SELECT title, description FROM doc_metadata WHERE path = ?",
                (doc_path,),
            )
            row = cursor.fetchone()
            title = row[0] if row else doc_path
            desc = row[1] if row else ""
            results.append(
                SearchMatch(
                    path=doc_path,
                    title=title or doc_path,
                    score=score,
                    snippet=desc or "",
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
        if not vec_results:
            return kw_results[:max_results]
        if not kw_results:
            return vec_results[:max_results]

        # Assign RRF scores by position (1-based rank)
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

        results: list[SearchMatch] = []
        for path in sorted_paths[:max_results]:
            original = best_match[path]
            results.append(
                SearchMatch(
                    path=original.path,
                    title=original.title,
                    score=rrf_scores[path],
                    snippet=original.snippet,
                    search_method="hybrid",
                )
            )
        return results

    def _load_embedding_cache(self) -> None:
        """Load or refresh the embedding cache when DB mtime changes."""
        if not _HAS_NUMPY:
            return

        try:
            current_mtime = os.path.getmtime(self._db_path)
        except OSError:
            return

        if self._embedding_cache is not None and current_mtime == self._db_mtime:
            return

        if self._conn is None:
            raise RuntimeError("Searcher is closed")
        rows = self._conn.execute("SELECT path, embedding FROM doc_embeddings").fetchall()

        if not rows:
            self._embedding_cache = {}
            self._cache_paths = []
            self._cache_matrix = None
            self._db_mtime = current_mtime
            return

        cache: dict[str, Any] = {}
        paths: list[str] = []
        vectors: list[Any] = []

        for path, blob in rows:
            vec = np.frombuffer(blob, dtype=np.float32).copy()
            cache[path] = vec
            paths.append(path)
            vectors.append(vec)

        self._embedding_cache = cache
        self._cache_paths = paths
        self._cache_matrix = np.vstack(vectors)
        self._db_mtime = current_mtime

    def list_documents(self, section: str | None = None) -> list[tuple]:
        """List all indexed documents, optionally filtered by section prefix."""
        if self._conn is None:
            return []
        if section:
            prefix = section.rstrip("/") + "/"
            cursor = self._conn.execute(
                "SELECT path, title, description, categories, mtime FROM doc_metadata "
                "WHERE path LIKE ? ORDER BY path",
                (prefix + "%",),
            )
        else:
            cursor = self._conn.execute(
                "SELECT path, title, description, categories, mtime FROM doc_metadata ORDER BY path"
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
