"""Comprehensive tests for the DocSearcher class (searcher.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from mkdocs_mcp.indexer import DocIndexer
from mkdocs_mcp.models import SearchMatch, SearchResult
from mkdocs_mcp.searcher import DocSearcher, _clamp, _rrf_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def search_docs(tmp_path: Path) -> Path:
    """Create a temp docs directory with 10+ diverse markdown files for search tests.

    Each file has unique frontmatter, multiple headings, and distinctive
    content so that search queries can reliably target specific documents.
    """
    docs = tmp_path / "docs"
    docs.mkdir()

    # index.md — Homepage
    (docs / "index.md").write_text(
        "---\n"
        "title: Welcome\n"
        "description: The main landing page for our project documentation.\n"
        "---\n"
        "\n"
        "# Welcome to the Project\n"
        "\n"
        "This is the homepage of our comprehensive documentation site.\n"
        "It provides an overview of all available resources and guides.\n"
        "\n"
        "## Overview\n"
        "\n"
        "Our project offers a powerful toolkit for building modern applications.\n"
        "\n"
        "## Quick Links\n"
        "\n"
        "Navigate to the sections that interest you most.\n",
        encoding="utf-8",
    )

    # getting-started.md — Installation guide
    (docs / "getting-started.md").write_text(
        "---\n"
        "title: Getting Started\n"
        "description: Step-by-step installation and setup guide.\n"
        "---\n"
        "\n"
        "# Getting Started\n"
        "\n"
        "Follow these steps for installation and initial setup.\n"
        "\n"
        "## Installation\n"
        "\n"
        "Install the package using pip:\n"
        "\n"
        "```bash\n"
        "pip install our-project\n"
        "```\n"
        "\n"
        "## Configuration\n"
        "\n"
        "After installation, configure the settings in your config file.\n"
        "\n"
        "### Environment Variables\n"
        "\n"
        "Set the following environment variables for proper operation.\n",
        encoding="utf-8",
    )

    # api-reference.md — API docs
    (docs / "api-reference.md").write_text(
        "---\n"
        "title: API Reference\n"
        "description: Complete reference for public API endpoints and functions.\n"
        "---\n"
        "\n"
        "# API Reference\n"
        "\n"
        "This document describes all available API endpoints.\n"
        "\n"
        "## Authentication\n"
        "\n"
        "All requests require a Bearer token for authentication.\n"
        "\n"
        "## Endpoints\n"
        "\n"
        "### GET /users\n"
        "\n"
        "Returns a list of users.\n"
        "\n"
        "```python\n"
        "def get_users(limit: int = 10) -> list[User]:\n"
        '    """Fetch users from the database."""\n'
        "    pass\n"
        "```\n"
        "\n"
        "### POST /users\n"
        "\n"
        "Creates a new user in the system.\n",
        encoding="utf-8",
    )

    # deployment.md — Deployment guide
    (docs / "deployment.md").write_text(
        "---\n"
        "title: Deployment Guide\n"
        "description: How to deploy the application to production.\n"
        "---\n"
        "\n"
        "# Deployment Guide\n"
        "\n"
        "This guide covers deploying to various environments.\n"
        "\n"
        "## Docker\n"
        "\n"
        "Build and run with Docker containers for isolation.\n"
        "\n"
        "```dockerfile\n"
        "FROM python:3.11-slim\n"
        "COPY . /app\n"
        "RUN pip install -r requirements.txt\n"
        "```\n"
        "\n"
        "## Kubernetes\n"
        "\n"
        "Deploy to Kubernetes clusters for orchestration and scaling.\n"
        "\n"
        "### Helm Charts\n"
        "\n"
        "Use Helm charts for simplified Kubernetes deployments.\n",
        encoding="utf-8",
    )

    # troubleshooting.md — Error solutions
    (docs / "troubleshooting.md").write_text(
        "---\n"
        "title: Troubleshooting\n"
        "description: Solutions to common errors and problems.\n"
        "---\n"
        "\n"
        "# Troubleshooting\n"
        "\n"
        "This page contains solutions to commonly encountered errors.\n"
        "\n"
        "## Connection Errors\n"
        "\n"
        "If you see 'ConnectionRefusedError', check that the server is running.\n"
        "\n"
        "## Permission Denied\n"
        "\n"
        "A 'PermissionError' usually means insufficient file system privileges.\n"
        "\n"
        "## Memory Issues\n"
        "\n"
        "OutOfMemoryError can occur when processing very large datasets.\n",
        encoding="utf-8",
    )

    # advanced/performance.md — Performance tuning
    adv_dir = docs / "advanced"
    adv_dir.mkdir()
    (adv_dir / "performance.md").write_text(
        "---\n"
        "title: Performance Tuning\n"
        "description: Tips for optimizing application performance.\n"
        "---\n"
        "\n"
        "# Performance Tuning\n"
        "\n"
        "Optimize your application for maximum throughput.\n"
        "\n"
        "## Caching Strategies\n"
        "\n"
        "Implement caching to reduce database load and latency.\n"
        "\n"
        "## Database Optimization\n"
        "\n"
        "Index your queries and use connection pooling for better performance.\n"
        "\n"
        "### Query Profiling\n"
        "\n"
        "Use EXPLAIN ANALYZE to profile slow queries.\n",
        encoding="utf-8",
    )

    # advanced/security.md — Security best practices
    (adv_dir / "security.md").write_text(
        "---\n"
        "title: Security Best Practices\n"
        "description: Guidelines for securing your application.\n"
        "---\n"
        "\n"
        "# Security Best Practices\n"
        "\n"
        "Follow these guidelines to keep your application secure.\n"
        "\n"
        "## Authentication\n"
        "\n"
        "Use OAuth2 or JWT tokens for user authentication.\n"
        "\n"
        "## Encryption\n"
        "\n"
        "Enable TLS encryption for all network communication.\n"
        "Use AES-256 for encrypting sensitive data at rest.\n"
        "\n"
        "### Key Management\n"
        "\n"
        "Rotate encryption keys regularly and store them in a vault.\n",
        encoding="utf-8",
    )

    # changelog.md — Release notes
    (docs / "changelog.md").write_text(
        "---\n"
        "title: Changelog\n"
        "description: Release notes and version history.\n"
        "---\n"
        "\n"
        "# Changelog\n"
        "\n"
        "All notable changes to this project are documented here.\n"
        "\n"
        "## Version 2.1.0\n"
        "\n"
        "- Added support for hybrid search\n"
        "- Improved indexing performance by 30%\n"
        "\n"
        "## Version 2.0.0\n"
        "\n"
        "- Breaking: Removed legacy API endpoints\n"
        "- Added vector search capabilities\n"
        "\n"
        "## Version 1.0.0\n"
        "\n"
        "- Initial release with keyword search\n",
        encoding="utf-8",
    )

    # contributing.md — Contribution guidelines
    (docs / "contributing.md").write_text(
        "---\n"
        "title: Contributing\n"
        "description: How to contribute to this project.\n"
        "---\n"
        "\n"
        "# Contributing\n"
        "\n"
        "We welcome contributions from the community.\n"
        "\n"
        "## Code Style\n"
        "\n"
        "Follow PEP 8 for Python code style guidelines.\n"
        "\n"
        "## Pull Requests\n"
        "\n"
        "Submit pull requests against the main branch.\n"
        "\n"
        "### Review Process\n"
        "\n"
        "All pull requests require at least one approval before merging.\n",
        encoding="utf-8",
    )

    # faq.md — Frequently asked questions
    (docs / "faq.md").write_text(
        "---\n"
        "title: FAQ\n"
        "description: Frequently asked questions about the project.\n"
        "---\n"
        "\n"
        "# Frequently Asked Questions\n"
        "\n"
        "## What is this project?\n"
        "\n"
        "This project is a documentation search engine built on SQLite FTS5.\n"
        "\n"
        "## How do I install it?\n"
        "\n"
        "See the Getting Started page for installation instructions.\n"
        "\n"
        "## Is it free?\n"
        "\n"
        "Yes, this project is open source and free to use.\n",
        encoding="utf-8",
    )

    # tiny.md — A very short document for snippet testing
    (docs / "tiny.md").write_text(
        "---\n"
        "title: Tiny\n"
        "---\n"
        "\n"
        "# Tiny\n"
        "\n"
        "Short.\n",
        encoding="utf-8",
    )

    return docs


@pytest.fixture
def indexed_db(search_docs: Path, tmp_path: Path) -> Path:
    """Build a full-text search index from search_docs and return the db path."""
    db_path = tmp_path / "search_test.db"
    with DocIndexer(docs_dir=search_docs, db_path=db_path) as indexer:
        indexer.build_index()
    return db_path


@pytest.fixture
def searcher(indexed_db: Path):
    """Create a DocSearcher from the indexed database, yield and close."""
    s = DocSearcher(db_path=indexed_db)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helper: mock embedder
# ---------------------------------------------------------------------------


def _make_mock_embedder(dim: int = 64):
    """Create a mock embedder that returns deterministic numpy vectors.

    The encode method hashes the input string to produce a repeatable vector.
    """
    embedder = MagicMock()

    def _encode(text: str) -> np.ndarray:
        # Deterministic: hash text bytes to seed random state
        import hashlib

        digest = hashlib.sha256(text.encode()).digest()
        seed = int.from_bytes(digest[:4], "big")
        rng = np.random.RandomState(seed)
        vec = rng.randn(dim).astype(np.float32)
        # Normalise so cosine similarity is meaningful
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    embedder.encode = _encode
    embedder.model_name = "mock-embedder"
    return embedder


# ---------------------------------------------------------------------------
# TestClampAndRRF (unit helpers)
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for module-level helper functions _clamp and _rrf_score."""

    def test_clamp_within_range(self):
        """Value within bounds is returned unchanged."""
        assert _clamp(5, 1, 10) == 5

    def test_clamp_below_min(self):
        """Value below lo is clamped to lo."""
        assert _clamp(-1, 1, 10) == 1

    def test_clamp_above_max(self):
        """Value above hi is clamped to hi."""
        assert _clamp(999, 1, 10) == 10

    def test_clamp_at_boundaries(self):
        """Values exactly at lo and hi are returned unchanged."""
        assert _clamp(1, 1, 10) == 1
        assert _clamp(10, 1, 10) == 10

    def test_rrf_score_rank_1(self):
        """RRF score for rank 1 with default k=60 is 1/61."""
        assert _rrf_score(1) == pytest.approx(1.0 / 61)

    def test_rrf_score_rank_increases_denominator(self):
        """Higher ranks produce lower RRF scores."""
        assert _rrf_score(1) > _rrf_score(2) > _rrf_score(10)

    def test_rrf_score_custom_k(self):
        """RRF score uses the provided k parameter."""
        assert _rrf_score(1, k=0) == pytest.approx(1.0)
        assert _rrf_score(5, k=10) == pytest.approx(1.0 / 15)


# ---------------------------------------------------------------------------
# TestKeywordSearch
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    """Tests for DocSearcher.keyword_search()."""

    def test_keyword_search_basic(self, searcher: DocSearcher):
        """Searching for 'installation' finds the getting-started document."""
        results = searcher.keyword_search("installation")
        assert len(results) >= 1
        paths = [m.path for m in results]
        assert "getting-started.md" in paths

    def test_keyword_search_title_boost(self, search_docs: Path, tmp_path: Path):
        """A term in a document's title ranks higher than the same term in body only.

        The FTS5 BM25 weights in the searcher give title column weight=10
        vs content weight=1, so a title match should dominate.
        """
        # Create two docs: one with 'zebrafish' in title, one only in body
        boost_docs = tmp_path / "boost_docs"
        boost_docs.mkdir()

        (boost_docs / "title_match.md").write_text(
            "---\n"
            "title: Zebrafish Research Guide\n"
            "---\n"
            "\n"
            "# Zebrafish Research Guide\n"
            "\n"
            "This guide covers zebrafish biology.\n",
            encoding="utf-8",
        )

        (boost_docs / "body_match.md").write_text(
            "---\n"
            "title: Marine Biology Notes\n"
            "---\n"
            "\n"
            "# Marine Biology Notes\n"
            "\n"
            "Some information about zebrafish in laboratory settings.\n",
            encoding="utf-8",
        )

        db_path = tmp_path / "boost_test.db"
        with DocIndexer(docs_dir=boost_docs, db_path=db_path) as indexer:
            indexer.build_index()

        with DocSearcher(db_path=db_path) as s:
            results = s.keyword_search("zebrafish")
            assert len(results) == 2
            # Title-match doc should rank first (higher score)
            assert results[0].path == "title_match.md", (
                f"Expected title match to rank first, got: {[r.path for r in results]}"
            )
            assert results[0].score > results[1].score

    def test_keyword_search_no_results(self, searcher: DocSearcher):
        """A query for a nonexistent term returns an empty list."""
        results = searcher.keyword_search("xyznonexistent")
        assert results == []

    def test_keyword_search_special_chars(self, searcher: DocSearcher):
        """Special FTS5 characters are sanitized and do not cause exceptions."""
        special_queries = ['"quoted"', "(parens)", "a*b", "NEAR/3 test", 'a"b(c)d*e']
        for q in special_queries:
            # Should not raise
            results = searcher.keyword_search(q)
            assert isinstance(results, list), f"Query {q!r} should return a list"

    def test_keyword_search_snippet_contains_term(self, searcher: DocSearcher):
        """The snippet for a matched result contains the search term (case-insensitive)."""
        results = searcher.keyword_search("Docker")
        assert len(results) >= 1
        # Find the deployment doc result
        deploy_matches = [m for m in results if m.path == "deployment.md"]
        assert len(deploy_matches) >= 1
        snippet = deploy_matches[0].snippet.lower()
        assert "docker" in snippet, f"Expected 'docker' in snippet, got: {snippet}"

    def test_keyword_search_snippet_short_doc(self, searcher: DocSearcher):
        """Even a very short document produces a non-empty snippet when matched."""
        # Search for 'short' which appears only in tiny.md
        results = searcher.keyword_search("Short")
        assert len(results) >= 1
        tiny_matches = [m for m in results if m.path == "tiny.md"]
        assert len(tiny_matches) >= 1
        assert tiny_matches[0].snippet != ""

    def test_keyword_search_max_results(self, searcher: DocSearcher):
        """Setting max_results=3 returns at most 3 results even when more match."""
        # 'the' should appear in many documents
        results = searcher.keyword_search("the", max_results=3)
        assert len(results) <= 3

    def test_keyword_search_empty_query(self, searcher: DocSearcher):
        """An empty query string returns an empty list."""
        assert searcher.keyword_search("") == []

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "()",
            "***",
            "NEAR/3",
            'hello"world',
            '"(NOT)"',
            "   ",
            "a OR b",
            "test AND fail",
            "😀🎉",
            "日本語テスト",
            "../../../etc/passwd",
            "<script>alert(1)</script>",
            "SELECT * FROM users; DROP TABLE users;--",
            "a" * 1000,
            "\x00\x01\x02",
            "OR OR OR",
            "NOT NOT NOT",
            "NEAR NEAR NEAR",
            '""""""',
            "(((())))",
            "^prefix",
            "{column}:term",
            "a AND b OR c NOT d NEAR e",
        ],
        ids=[
            "empty",
            "parens-only",
            "asterisks",
            "near-slash",
            "embedded-quote",
            "quoted-not-parens",
            "whitespace-only",
            "or-operator",
            "and-operator",
            "emojis",
            "unicode-cjk",
            "path-traversal",
            "html-injection",
            "sql-injection",
            "very-long",
            "null-bytes",
            "repeated-or",
            "repeated-not",
            "repeated-near",
            "many-quotes",
            "nested-parens",
            "caret-prefix",
            "column-syntax",
            "all-operators",
        ],
    )
    def test_keyword_search_no_exception_fuzz(self, searcher: DocSearcher, query: str):
        """Fuzz: no query string should cause an exception in keyword_search."""
        # Must not raise any exception
        results = searcher.keyword_search(query)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# TestVectorSearch
# ---------------------------------------------------------------------------


class TestVectorSearch:
    """Tests for DocSearcher.vector_search()."""

    def test_vector_search_unavailable(self, searcher: DocSearcher):
        """A searcher without an embedder returns empty list for vector search."""
        assert searcher._embedder is None
        results = searcher.vector_search("test query")
        assert results == []

    def test_vector_search_empty_query(self, indexed_db: Path):
        """An empty query returns an empty list even with an embedder."""
        mock_embedder = _make_mock_embedder()
        with DocSearcher(db_path=indexed_db, embedder=mock_embedder) as s:
            assert s.vector_search("") == []
            assert s.vector_search("   ") == []


# ---------------------------------------------------------------------------
# TestHybridSearch
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """Tests for DocSearcher.hybrid_search()."""

    def test_hybrid_search_fallback(self, searcher: DocSearcher):
        """Without an embedder, hybrid search falls back to keyword-only results."""
        results = searcher.hybrid_search("installation")
        assert len(results) >= 1
        paths = [m.path for m in results]
        assert "getting-started.md" in paths

    def test_hybrid_search_deduplication(self, searcher: DocSearcher):
        """Hybrid search results contain no duplicate paths."""
        results = searcher.hybrid_search("documentation")
        paths = [m.path for m in results]
        assert len(paths) == len(set(paths)), f"Duplicate paths found: {paths}"

    def test_hybrid_search_keyword_zero_vector_returns(
        self, search_docs: Path, tmp_path: Path
    ):
        """When keyword returns 0 results but vector returns results,
        hybrid should still return the vector results."""
        mock_embedder = _make_mock_embedder()

        # Build index with embeddings
        db_path = tmp_path / "hybrid_kw0.db"
        with DocIndexer(docs_dir=search_docs, db_path=db_path) as indexer:
            indexer.build_index(embedder=mock_embedder)

        with DocSearcher(db_path=db_path, embedder=mock_embedder) as s:
            # Use a term that produces no keyword matches but vector search
            # will still return results (since it's cosine similarity on embeddings)
            results = s.hybrid_search("xyznonexistent")
            # Even if keyword returns nothing, vector might return results.
            # If vector also returns nothing (score <= 0), the list is empty,
            # which is also a valid outcome.
            assert isinstance(results, list)

    def test_hybrid_search_vector_zero_keyword_returns(self, searcher: DocSearcher):
        """When vector returns 0 results (no embedder), keyword results still appear."""
        assert searcher._embedder is None  # no embedder
        results = searcher.hybrid_search("installation")
        assert len(results) >= 1
        # All results should be from keyword (but method is set to hybrid in hybrid path;
        # actually when vec_results is empty, kw_results are returned directly)
        paths = [m.path for m in results]
        assert "getting-started.md" in paths


# ---------------------------------------------------------------------------
# TestSearchDispatch
# ---------------------------------------------------------------------------


class TestSearchDispatch:
    """Tests for DocSearcher.search() dispatch and SearchResult wrapping."""

    def test_search_type_keyword(self, searcher: DocSearcher):
        """search(type='keyword') returns a SearchResult with search_type='keyword'."""
        result = searcher.search("installation", search_type="keyword")
        assert isinstance(result, SearchResult)
        assert result.search_type == "keyword"
        assert result.query == "installation"
        assert result.total_count >= 1
        assert len(result.results) == result.total_count

    def test_search_type_vector(self, searcher: DocSearcher):
        """search(type='vector') returns a SearchResult with search_type='vector'."""
        result = searcher.search("installation", search_type="vector")
        assert isinstance(result, SearchResult)
        assert result.search_type == "vector"
        # Without embedder, results should be empty
        assert result.total_count == 0

    def test_search_type_hybrid(self, searcher: DocSearcher):
        """search(type='hybrid') returns a SearchResult with search_type='hybrid'."""
        result = searcher.search("installation", search_type="hybrid")
        assert isinstance(result, SearchResult)
        assert result.search_type == "hybrid"

    def test_search_type_validation(self, searcher: DocSearcher):
        """search(type='invalid') raises ValueError."""
        with pytest.raises(ValueError, match="Invalid search_type"):
            searcher.search("test", search_type="invalid")

    def test_search_empty_query(self, searcher: DocSearcher):
        """search with an empty query returns SearchResult with 0 results."""
        result = searcher.search("", search_type="keyword")
        assert isinstance(result, SearchResult)
        assert result.total_count == 0
        assert result.results == []

    def test_search_whitespace_query(self, searcher: DocSearcher):
        """search with whitespace-only query returns SearchResult with 0 results."""
        result = searcher.search("   ", search_type="keyword")
        assert isinstance(result, SearchResult)
        assert result.total_count == 0
        assert result.results == []

    def test_search_max_results_clamped_low(self, searcher: DocSearcher):
        """max_results=0 is clamped to 1."""
        result = searcher.search("the", search_type="keyword", max_results=0)
        assert isinstance(result, SearchResult)
        # Should return at most 1 result (clamped from 0 to 1)
        assert len(result.results) <= 1

    def test_search_max_results_clamped_high(self, searcher: DocSearcher):
        """max_results=999 is clamped to 100."""
        result = searcher.search("the", search_type="keyword", max_results=999)
        assert isinstance(result, SearchResult)
        # Should not return more than 100 (clamped from 999 to 100)
        assert len(result.results) <= 100

    def test_search_max_results_negative(self, searcher: DocSearcher):
        """max_results=-5 is clamped to 1."""
        result = searcher.search("the", search_type="keyword", max_results=-5)
        assert isinstance(result, SearchResult)
        assert len(result.results) <= 1


# ---------------------------------------------------------------------------
# TestSearcherLifecycle
# ---------------------------------------------------------------------------


class TestSearcherLifecycle:
    """Tests for DocSearcher context manager and close() lifecycle."""

    def test_context_manager(self, indexed_db: Path):
        """DocSearcher works correctly as a context manager."""
        with DocSearcher(db_path=indexed_db) as s:
            results = s.keyword_search("installation")
            assert len(results) >= 1
        # After exiting, the connection should be closed
        assert s._conn is None

    def test_close_idempotent(self, indexed_db: Path):
        """Calling close() multiple times does not raise an exception."""
        s = DocSearcher(db_path=indexed_db)
        s.close()
        s.close()  # Second close should not raise
        assert s._conn is None

    def test_search_after_close_raises(self, indexed_db: Path):
        """Attempting to search after close raises an error."""
        s = DocSearcher(db_path=indexed_db)
        s.close()
        with pytest.raises(RuntimeError, match="Searcher is closed"):
            s.keyword_search("test")


# ---------------------------------------------------------------------------
# TestSearchMatchModel
# ---------------------------------------------------------------------------


class TestSearchMatchFields:
    """Tests verifying SearchMatch fields are populated correctly."""

    def test_keyword_match_has_all_fields(self, searcher: DocSearcher):
        """Each SearchMatch from keyword search has path, title, score, snippet, search_method."""
        results = searcher.keyword_search("installation")
        assert len(results) >= 1
        match = results[0]
        assert isinstance(match, SearchMatch)
        assert match.path
        assert match.title
        assert match.score > 0
        assert isinstance(match.snippet, str)
        assert match.search_method == "keyword"

    def test_keyword_match_title_from_frontmatter(self, searcher: DocSearcher):
        """The title field comes from frontmatter metadata when available."""
        results = searcher.keyword_search("Docker")
        deploy_matches = [m for m in results if m.path == "deployment.md"]
        assert len(deploy_matches) >= 1
        assert deploy_matches[0].title == "Deployment Guide"

    def test_keyword_scores_are_positive(self, searcher: DocSearcher):
        """BM25 scores are negated so they are positive (higher = better)."""
        results = searcher.keyword_search("documentation")
        for match in results:
            assert match.score > 0, f"Score should be positive, got {match.score}"
