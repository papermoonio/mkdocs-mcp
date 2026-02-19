"""Comprehensive tests for the DocIndexer class (indexer.py)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mkdocs_mcp.indexer import DocIndexer
from mkdocs_mcp.models import IndexStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_docs_for_index(tmp_path: Path) -> Path:
    """Create a temp docs directory with 5 markdown files for indexer tests.

    Layout:
        index.md                     — frontmatter (title, description, categories as string)
        guide/getting-started.md     — frontmatter (title, categories as comma-string), code blocks
        guide/advanced.md            — frontmatter (title), advanced topics content
        reference/api.md             — frontmatter (title), technical reference content
        reference/glossary.md        — no frontmatter, headings + definitions only
    """
    docs = tmp_path / "docs"
    docs.mkdir()

    # index.md — categories as plain string for parsing test
    (docs / "index.md").write_text(
        "---\n"
        "title: Home\n"
        "description: Welcome to the documentation.\n"
        'categories: "General"\n'
        "---\n"
        "\n"
        "# Home\n"
        "\n"
        "This is the main landing page for the documentation site.\n"
        "It contains an overview of all available resources.\n",
        encoding="utf-8",
    )

    # guide/getting-started.md — categories as comma-separated string
    guide_dir = docs / "guide"
    guide_dir.mkdir()
    (guide_dir / "getting-started.md").write_text(
        "---\n"
        "title: Getting Started\n"
        'categories: "Tutorial, Beginner"\n'
        "---\n"
        "\n"
        "# Getting Started\n"
        "\n"
        "Follow these steps to get up and running.\n"
        "\n"
        "```bash\n"
        "# install the package\n"
        "pip install mkdocs-mcp\n"
        "```\n"
        "\n"
        "## Prerequisites\n"
        "\n"
        "You need Python 3.11 or newer.\n",
        encoding="utf-8",
    )

    # guide/advanced.md — minimal frontmatter
    (guide_dir / "advanced.md").write_text(
        "---\n"
        "title: Advanced Guide\n"
        "---\n"
        "\n"
        "# Advanced Topics\n"
        "\n"
        "This guide covers advanced configuration and customization.\n"
        "\n"
        "## Plugin System\n"
        "\n"
        "Plugins extend the core functionality.\n"
        "\n"
        "## Performance Tuning\n"
        "\n"
        "Tips for improving build performance.\n",
        encoding="utf-8",
    )

    # reference/api.md — technical reference
    ref_dir = docs / "reference"
    ref_dir.mkdir()
    (ref_dir / "api.md").write_text(
        "---\n"
        "title: API Reference\n"
        "---\n"
        "\n"
        "# API Reference\n"
        "\n"
        "Complete reference for the public API endpoints.\n"
        "\n"
        "## Authentication\n"
        "\n"
        "All requests require a valid API token.\n"
        "\n"
        "## Endpoints\n"
        "\n"
        "The following endpoints are available.\n",
        encoding="utf-8",
    )

    # reference/glossary.md — no frontmatter at all
    (ref_dir / "glossary.md").write_text(
        "# Glossary\n"
        "\n"
        "## API\n"
        "\n"
        "Application Programming Interface.\n"
        "\n"
        "## MkDocs\n"
        "\n"
        "A fast static site generator geared towards project documentation.\n"
        "\n"
        "## FTS\n"
        "\n"
        "Full-Text Search, a technique for searching document content.\n",
        encoding="utf-8",
    )

    return docs


@pytest.fixture
def indexer(tmp_docs_for_index: Path, tmp_path: Path) -> DocIndexer:
    """Create a DocIndexer pointed at the temp docs dir with a temp db path."""
    db_path = tmp_path / "test_index.db"
    idx = DocIndexer(docs_dir=tmp_docs_for_index, db_path=db_path)
    yield idx
    idx.close()


@pytest.fixture
def indexed(indexer: DocIndexer) -> DocIndexer:
    """Build a full index and return the indexer (already indexed)."""
    indexer.build_index()
    return indexer


# ---------------------------------------------------------------------------
# TestBuildIndex
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for DocIndexer.build_index()."""

    def test_build_index_empty_dir(self, tmp_path: Path) -> None:
        """An empty docs directory produces a valid IndexStatus with total=0."""
        empty_docs = tmp_path / "empty_docs"
        empty_docs.mkdir()
        db_path = tmp_path / "empty.db"
        idx = DocIndexer(docs_dir=empty_docs, db_path=db_path)
        try:
            status = idx.build_index()
        finally:
            idx.close()

        assert isinstance(status, IndexStatus)
        assert status.total_documents == 0
        assert status.indexed == 0
        assert status.failed == 0

    def test_build_index_single_file(self, tmp_path: Path) -> None:
        """A directory with one .md file produces total_documents=1, indexed=1."""
        single_docs = tmp_path / "single_docs"
        single_docs.mkdir()
        (single_docs / "page.md").write_text(
            "---\ntitle: Single Page\n---\n\n# Single Page\n\nContent here.\n",
            encoding="utf-8",
        )
        db_path = tmp_path / "single.db"
        idx = DocIndexer(docs_dir=single_docs, db_path=db_path)
        try:
            status = idx.build_index()
        finally:
            idx.close()

        assert status.total_documents == 1
        assert status.indexed == 1
        assert status.failed == 0

    def test_build_index_with_frontmatter(self, indexer: DocIndexer) -> None:
        """Frontmatter fields (title, description, categories) are stored correctly."""
        indexer.build_index()

        row = indexer._conn.execute(
            "SELECT title, description, categories FROM doc_metadata WHERE path = ?",
            ("index.md",),
        ).fetchone()

        assert row is not None
        title, description, categories_json = row
        assert title == "Home"
        assert description == "Welcome to the documentation."
        # categories stored as JSON list
        import json
        cats = json.loads(categories_json)
        assert isinstance(cats, list)
        assert len(cats) >= 1

    def test_build_index_multiple_files(self, indexer: DocIndexer) -> None:
        """All 5 markdown documents are indexed."""
        status = indexer.build_index()

        assert status.total_documents == 5
        assert status.indexed == 5
        assert status.failed == 0

    def test_build_index_skips_hidden(
        self, tmp_docs_for_index: Path, indexer: DocIndexer
    ) -> None:
        """Files inside hidden directories (starting with '.') are not indexed."""
        hidden_dir = tmp_docs_for_index / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.md").write_text(
            "---\ntitle: Secret\n---\n\n# Secret\n\nThis should not be indexed.\n",
            encoding="utf-8",
        )

        status = indexer.build_index()

        # Hidden file must not appear in metadata
        row = indexer._conn.execute(
            "SELECT path FROM doc_metadata WHERE path LIKE '%.hidden%'",
        ).fetchone()
        assert row is None, "Hidden file was indexed but should have been skipped"
        # Total should still be only 5 (the non-hidden files)
        assert status.total_documents == 5

    def test_build_index_skips_non_markdown(
        self, tmp_docs_for_index: Path, indexer: DocIndexer
    ) -> None:
        """Non-.md files are not indexed."""
        (tmp_docs_for_index / "notes.txt").write_text(
            "Plain text notes that should be ignored.\n",
            encoding="utf-8",
        )

        status = indexer.build_index()

        row = indexer._conn.execute(
            "SELECT path FROM doc_metadata WHERE path = 'notes.txt'"
        ).fetchone()
        assert row is None, "Non-.md file was indexed but should have been skipped"
        assert status.total_documents == 5

    def test_build_index_categories_string(self, indexer: DocIndexer) -> None:
        """A comma-separated categories string is parsed into a proper list."""
        import json

        indexer.build_index()

        row = indexer._conn.execute(
            "SELECT categories FROM doc_metadata WHERE path = ?",
            ("guide/getting-started.md",),
        ).fetchone()

        assert row is not None
        cats = json.loads(row[0])
        assert isinstance(cats, list)
        assert "Tutorial" in cats
        assert "Beginner" in cats


# ---------------------------------------------------------------------------
# TestIncrementalIndex
# ---------------------------------------------------------------------------


class TestIncrementalIndex:
    """Tests for DocIndexer.update_index() incremental behaviour."""

    def test_update_index_no_changes(self, indexed: DocIndexer) -> None:
        """Calling update_index immediately after build_index skips all docs."""
        status = indexed.update_index()

        assert status.skipped == 5
        assert status.indexed == 0
        assert status.removed == 0
        assert status.failed == 0

    def test_update_index_modified_file(
        self, indexed: DocIndexer, tmp_docs_for_index: Path
    ) -> None:
        """Modifying a file's content causes it to be re-indexed."""
        target = tmp_docs_for_index / "index.md"

        # Sleep briefly to guarantee mtime advances
        time.sleep(0.05)
        target.write_text(
            "---\ntitle: Home (Updated)\n---\n\n# Home Updated\n\nModified content.\n",
            encoding="utf-8",
        )

        status = indexed.update_index()

        assert status.indexed == 1
        assert status.skipped == 4

        # Verify new title is persisted
        row = indexed._conn.execute(
            "SELECT title FROM doc_metadata WHERE path = 'index.md'"
        ).fetchone()
        assert row[0] == "Home (Updated)"

    def test_update_index_new_file(
        self, indexed: DocIndexer, tmp_docs_for_index: Path
    ) -> None:
        """A new file created after the initial build is picked up on update."""
        new_file = tmp_docs_for_index / "new-page.md"
        new_file.write_text(
            "---\ntitle: New Page\n---\n\n# New Page\n\nBrand new content.\n",
            encoding="utf-8",
        )

        status = indexed.update_index()

        assert status.indexed == 1
        assert status.total_documents == 6

        row = indexed._conn.execute(
            "SELECT title FROM doc_metadata WHERE path = 'new-page.md'"
        ).fetchone()
        assert row is not None
        assert row[0] == "New Page"

    def test_update_index_deleted_file(
        self, indexed: DocIndexer, tmp_docs_for_index: Path
    ) -> None:
        """Deleting a file causes it to be removed from the index on next update."""
        target = tmp_docs_for_index / "reference" / "glossary.md"
        target.unlink()

        status = indexed.update_index()

        assert status.removed == 1
        assert status.total_documents == 4

    def test_update_index_deleted_file_search(
        self, indexed: DocIndexer, tmp_docs_for_index: Path
    ) -> None:
        """A deleted file is removed from FTS5 — it no longer appears in MATCH queries."""
        # Confirm the glossary is currently searchable
        rows_before = indexed._conn.execute(
            "SELECT path FROM docs_fts WHERE docs_fts MATCH 'Glossary'"
        ).fetchall()
        paths_before = [r[0] for r in rows_before]
        assert any("glossary" in p for p in paths_before), (
            "Glossary should be in FTS before deletion"
        )

        # Delete the file and update
        (tmp_docs_for_index / "reference" / "glossary.md").unlink()
        indexed.update_index()

        # FTS5 should no longer return the deleted doc
        rows_after = indexed._conn.execute(
            "SELECT path FROM docs_fts WHERE docs_fts MATCH 'Glossary'"
        ).fetchall()
        paths_after = [r[0] for r in rows_after]
        assert not any("glossary" in p for p in paths_after), (
            "Glossary should NOT be in FTS after deletion"
        )

    def test_update_index_mtime_changed_content_same(
        self, indexed: DocIndexer, tmp_docs_for_index: Path
    ) -> None:
        """Writing the same content (touching mtime) is skipped due to hash match."""
        target = tmp_docs_for_index / "index.md"
        original_content = target.read_text(encoding="utf-8")

        # Write identical content — changes mtime but not hash
        time.sleep(0.05)
        target.write_text(original_content, encoding="utf-8")

        # Ensure the mtime actually changed by advancing it explicitly
        new_mtime = target.stat().st_mtime + 1.0
        os.utime(str(target), (new_mtime, new_mtime))

        status = indexed.update_index()

        # Hash matched → counted as skipped, not indexed
        assert status.skipped == 5
        assert status.indexed == 0

    def test_update_idempotent(self, indexed: DocIndexer) -> None:
        """Running update_index twice with no changes returns consistent results."""
        first = indexed.update_index()
        second = indexed.update_index()

        assert first.skipped == second.skipped
        assert first.indexed == second.indexed
        assert first.removed == second.removed
        assert second.indexed == 0
        assert second.removed == 0


# ---------------------------------------------------------------------------
# TestIndexPersistence
# ---------------------------------------------------------------------------


class TestIndexPersistence:
    """Tests verifying that index data survives closing and reopening the DB."""

    def test_index_survives_close_reopen(
        self, tmp_docs_for_index: Path, tmp_path: Path
    ) -> None:
        """Data written in one DocIndexer instance is readable after reopen."""
        db_path = tmp_path / "persist.db"

        # Build index and close
        idx1 = DocIndexer(docs_dir=tmp_docs_for_index, db_path=db_path)
        idx1.build_index()
        idx1.close()

        # Reopen with a brand-new DocIndexer
        idx2 = DocIndexer(docs_dir=tmp_docs_for_index, db_path=db_path)
        try:
            count = idx2._conn.execute(
                "SELECT COUNT(*) FROM doc_metadata"
            ).fetchone()[0]
            assert count == 5

            row = idx2._conn.execute(
                "SELECT title FROM doc_metadata WHERE path = 'index.md'"
            ).fetchone()
            assert row is not None
            assert row[0] == "Home"
        finally:
            idx2.close()

    def test_fts5_searchable_after_reopen(
        self, tmp_docs_for_index: Path, tmp_path: Path
    ) -> None:
        """FTS5 data is available for MATCH queries after closing and reopening."""
        db_path = tmp_path / "persist_fts.db"

        idx1 = DocIndexer(docs_dir=tmp_docs_for_index, db_path=db_path)
        idx1.build_index()
        idx1.close()

        idx2 = DocIndexer(docs_dir=tmp_docs_for_index, db_path=db_path)
        try:
            rows = idx2._conn.execute(
                "SELECT path FROM docs_fts WHERE docs_fts MATCH 'documentation'"
            ).fetchall()
            assert len(rows) > 0, "Expected FTS results after reopen"
        finally:
            idx2.close()


# ---------------------------------------------------------------------------
# TestFTS5Queries
# ---------------------------------------------------------------------------


class TestFTS5Queries:
    """Tests for FTS5 full-text search behaviour on the indexed data."""

    def test_fts5_match_basic(self, indexed: DocIndexer) -> None:
        """A basic FTS5 MATCH query returns at least one result."""
        rows = indexed._conn.execute(
            "SELECT path FROM docs_fts WHERE docs_fts MATCH 'documentation'"
        ).fetchall()
        assert len(rows) >= 1

    def test_fts5_bm25_ranking(self, tmp_path: Path) -> None:
        """A doc with the search term in its title ranks higher (lower bm25) than
        a doc that only contains the term in body content."""
        docs = tmp_path / "rank_docs"
        docs.mkdir()

        # doc_a: search term "zebrafish" in title AND content
        (docs / "doc_a.md").write_text(
            "---\ntitle: zebrafish Overview\n---\n\n"
            "# zebrafish Overview\n\nThis page is about zebrafish biology.\n",
            encoding="utf-8",
        )

        # doc_b: search term "zebrafish" only in body content, unrelated title
        (docs / "doc_b.md").write_text(
            "---\ntitle: Marine Biology Notes\n---\n\n"
            "# Marine Biology Notes\n\nSome information about zebrafish in the wild.\n",
            encoding="utf-8",
        )

        db_path = tmp_path / "rank.db"
        idx = DocIndexer(docs_dir=docs, db_path=db_path)
        try:
            idx.build_index()

            rows = idx._conn.execute(
                "SELECT path, bm25(docs_fts) AS rank FROM docs_fts "
                "WHERE docs_fts MATCH 'zebrafish' "
                "ORDER BY rank"  # lower (more negative) = better match
            ).fetchall()

            assert len(rows) == 2
            paths = [r[0] for r in rows]
            # doc_a (title match) should appear before doc_b (content-only)
            assert paths[0] == "doc_a.md", (
                f"Expected doc_a (title match) to rank first, got order: {paths}"
            )
            # Verify doc_a has a more negative bm25 score
            rank_a = rows[0][1]
            rank_b = rows[1][1]
            assert rank_a < rank_b, (
                f"doc_a bm25={rank_a} should be lower (better) than doc_b bm25={rank_b}"
            )
        finally:
            idx.close()

    def test_fts5_snippet(self, indexed: DocIndexer) -> None:
        """The FTS5 snippet() function returns a non-empty string with the query term."""
        rows = indexed._conn.execute(
            "SELECT snippet(docs_fts, 3, '<b>', '</b>', '...', 10) AS snip "
            "FROM docs_fts WHERE docs_fts MATCH 'documentation'"
        ).fetchall()

        assert len(rows) >= 1
        snippet = rows[0][0]
        assert snippet  # non-empty
        assert isinstance(snippet, str)

    def test_fts5_searchable(self, indexed: DocIndexer) -> None:
        """After build_index, the FTS5 table contains entries for all 5 documents."""
        count = indexed._conn.execute(
            "SELECT COUNT(*) FROM docs_fts"
        ).fetchone()[0]
        assert count == 5


# ---------------------------------------------------------------------------
# TestRemoveDocument
# ---------------------------------------------------------------------------


class TestRemoveDocument:
    """Tests for DocIndexer._remove_document()."""

    def test_remove_from_all_tables(self, indexer: DocIndexer) -> None:
        """A document removed via _remove_document is gone from BOTH doc_metadata and docs_fts."""
        # Index a single document first
        single_doc = indexer.docs_dir / "index.md"
        indexer._index_document(single_doc)
        indexer._conn.commit()

        rel_path = "index.md"

        # Verify it is in both tables before removal
        meta_before = indexer._conn.execute(
            "SELECT COUNT(*) FROM doc_metadata WHERE path = ?", (rel_path,)
        ).fetchone()[0]
        fts_before = indexer._conn.execute(
            "SELECT COUNT(*) FROM docs_fts WHERE path = ?", (rel_path,)
        ).fetchone()[0]
        assert meta_before == 1, "Should be in doc_metadata before removal"
        assert fts_before == 1, "Should be in docs_fts before removal"

        # Remove
        indexer._remove_document(rel_path)
        indexer._conn.commit()

        # Verify gone from both tables
        meta_after = indexer._conn.execute(
            "SELECT COUNT(*) FROM doc_metadata WHERE path = ?", (rel_path,)
        ).fetchone()[0]
        fts_after = indexer._conn.execute(
            "SELECT COUNT(*) FROM docs_fts WHERE path = ?", (rel_path,)
        ).fetchone()[0]
        assert meta_after == 0, "Should be gone from doc_metadata after removal"
        assert fts_after == 0, "Should be gone from docs_fts after removal"


# ---------------------------------------------------------------------------
# TestScanDocuments
# ---------------------------------------------------------------------------


class TestScanDocuments:
    """Tests for DocIndexer._scan_documents()."""

    def test_scan_skips_symlinks_outside(
        self, tmp_docs_for_index: Path, indexer: DocIndexer, tmp_path: Path
    ) -> None:
        """Symlinks pointing outside the docs directory are skipped by _scan_documents."""
        # Create a real .md file outside docs_dir
        outside_file = tmp_path / "outside.md"
        outside_file.write_text(
            "---\ntitle: Outside\n---\n\n# Outside\n\nThis file is outside docs.\n",
            encoding="utf-8",
        )

        # Create a symlink inside docs_dir pointing to the outside file
        symlink_path = tmp_docs_for_index / "escape_link.md"
        symlink_path.symlink_to(outside_file)

        scanned = indexer._scan_documents()
        scanned_paths = [str(p) for p in scanned]

        assert not any("escape_link" in p for p in scanned_paths), (
            "Symlink pointing outside docs_dir should be excluded from scan"
        )
        # The 5 legitimate docs should still be found
        assert len(scanned) == 5
