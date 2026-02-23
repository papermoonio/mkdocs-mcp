"""Integration tests against the real polkadot-mkdocs project.

These tests build a full FTS5 index over the ~150 Polkadot Developer Docs
markdown files and exercise all five MCP tools (search, read_document,
list_documents, get_project_info, get_document_outline) against real content.

Tests are skipped when polkadot-mkdocs is not present on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import _make_ctx
from mkdocs_mcp.config import MkDocsConfig
from mkdocs_mcp.indexer import DocIndexer
from mkdocs_mcp.searcher import DocSearcher
from mkdocs_mcp.server import (
    get_document_outline,
    get_project_info,
    list_documents,
    read_document,
    search,
)

# In FastMCP 3.0, @mcp.tool returns the original function directly.
_search = search
_read_document = read_document
_list_documents = list_documents
_get_project_info = get_project_info
_get_document_outline = get_document_outline

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

POLKADOT_ROOT = Path("/workspace/polkadot-mkdocs")
POLKADOT_AVAILABLE = POLKADOT_ROOT.is_dir() and (POLKADOT_ROOT / "mkdocs.yml").is_file()

pytestmark = pytest.mark.skipif(
    not POLKADOT_AVAILABLE, reason="polkadot-mkdocs not available"
)


# ---------------------------------------------------------------------------
# Module-scoped fixtures (index once, reuse for all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def polkadot_config():
    """Parse the real polkadot mkdocs.yml."""
    return MkDocsConfig.from_file(POLKADOT_ROOT / "mkdocs.yml")


@pytest.fixture(scope="module")
def polkadot_index(polkadot_config, tmp_path_factory):
    """Build a full FTS5 index over all polkadot docs."""
    db_path = tmp_path_factory.mktemp("polkadot") / "polkadot.db"
    indexer = DocIndexer(polkadot_config.docs_dir, db_path=db_path)
    status = indexer.build_index()
    yield indexer, status, db_path
    indexer.close()


@pytest.fixture(scope="module")
def polkadot_searcher(polkadot_index):
    """Open a read-only searcher against the polkadot index."""
    _, _, db_path = polkadot_index
    searcher = DocSearcher(db_path)
    yield searcher
    searcher.close()


@pytest.fixture(scope="module")
def polkadot_ctx(polkadot_config, polkadot_index, polkadot_searcher):
    """Mock MCP Context wired to real polkadot config/indexer/searcher."""
    indexer, _, _ = polkadot_index
    return _make_ctx(polkadot_config, indexer, polkadot_searcher)


# ---------------------------------------------------------------------------
# TestPolkadotIndex
# ---------------------------------------------------------------------------


class TestPolkadotIndex:
    """Verify the indexer correctly processes the full polkadot corpus."""

    def test_index_build_all_docs(self, polkadot_index):
        """All polkadot docs are indexed without errors."""
        _, status, _ = polkadot_index
        assert status.total_documents >= 140  # Allow variance for excluded files
        assert status.failed == 0
        assert status.is_fresh is True

    def test_incremental_update_fast(self, polkadot_index):
        """Second update on unchanged corpus skips all documents quickly."""
        indexer, _, _ = polkadot_index
        status = indexer.update_index()
        assert status.skipped >= 140
        assert status.indexed == 0


# ---------------------------------------------------------------------------
# TestPolkadotSearch
# ---------------------------------------------------------------------------


class TestPolkadotSearch:
    """Search tool tests against real polkadot documentation content."""

    def test_search_collator(self, polkadot_ctx):
        """Search 'collator' finds relevant polkadot docs."""
        result = _search("collator", ctx=polkadot_ctx)
        assert "results" in result
        assert len(result["results"]) >= 1

    def test_search_validator(self, polkadot_ctx):
        """Search 'validator' returns relevant results."""
        result = _search("validator", ctx=polkadot_ctx)
        assert len(result["results"]) >= 1

    def test_search_staking_keyword(self, polkadot_ctx):
        """Keyword search for 'staking' finds staking-related docs."""
        result = _search("staking", ctx=polkadot_ctx, search_type="keyword")
        assert len(result["results"]) >= 1
        assert result["search_type"] == "keyword"

    def test_search_parachain(self, polkadot_ctx):
        """Search 'parachain' returns results from a core Polkadot topic."""
        result = _search("parachain", ctx=polkadot_ctx, search_type="keyword")
        assert len(result["results"]) >= 1

    def test_search_result_fields(self, polkadot_ctx):
        """Each search result from real data has required fields."""
        result = _search("node", ctx=polkadot_ctx, search_type="keyword")
        assert result["total_count"] >= 1
        match = result["results"][0]
        for field in ("path", "title", "score", "snippet", "search_method"):
            assert field in match


# ---------------------------------------------------------------------------
# TestPolkadotReadDocument
# ---------------------------------------------------------------------------


class TestPolkadotReadDocument:
    """Read tool tests against real polkadot docs."""

    def test_read_index_md(self, polkadot_ctx):
        """Read the main index.md from polkadot docs."""
        result = _read_document("index.md", ctx=polkadot_ctx)
        assert "error" not in result
        assert "content" in result
        # content is the markdown body (frontmatter stripped);
        # frontmatter-only files may have empty body
        assert isinstance(result["content"], str)

    def test_read_document_has_frontmatter(self, polkadot_ctx):
        """Read a doc and verify frontmatter is extracted."""
        result = _read_document("index.md", ctx=polkadot_ctx)
        assert "frontmatter" in result

    def test_read_nested_document(self, polkadot_ctx):
        """Read a deeply nested document from node-infrastructure."""
        result = _read_document("node-infrastructure/index.md", ctx=polkadot_ctx)
        assert "error" not in result
        assert "content" in result
        assert len(result["content"]) > 0

    def test_read_nonexistent_returns_error(self, polkadot_ctx):
        """Reading a nonexistent path returns an error dict."""
        result = _read_document("does-not-exist.md", ctx=polkadot_ctx)
        assert "error" in result


# ---------------------------------------------------------------------------
# TestPolkadotListDocuments
# ---------------------------------------------------------------------------


class TestPolkadotListDocuments:
    """List tool tests against the real polkadot corpus."""

    def test_list_all_documents(self, polkadot_ctx):
        """List all polkadot docs -- count matches indexed count."""
        result = _list_documents(ctx=polkadot_ctx)
        assert result["total_count"] >= 140

    def test_list_filtered_section(self, polkadot_ctx):
        """List docs filtered by node-infrastructure returns a subset."""
        all_result = _list_documents(ctx=polkadot_ctx)
        filtered = _list_documents(ctx=polkadot_ctx, section="node-infrastructure")
        assert filtered["total_count"] > 0
        assert filtered["total_count"] < all_result["total_count"]
        for doc in filtered["documents"]:
            assert doc["path"].startswith("node-infrastructure/")

    def test_list_nonexistent_section(self, polkadot_ctx):
        """Filtering by a nonexistent section returns zero results."""
        result = _list_documents(ctx=polkadot_ctx, section="nonexistent-section")
        assert result["total_count"] == 0
        assert result["documents"] == []

    def test_list_documents_have_fields(self, polkadot_ctx):
        """Each listed document has required metadata fields."""
        result = _list_documents(ctx=polkadot_ctx)
        assert result["total_count"] >= 1
        doc = result["documents"][0]
        for field in ("path", "title", "size", "mtime"):
            assert field in doc


# ---------------------------------------------------------------------------
# TestPolkadotProjectInfo
# ---------------------------------------------------------------------------


class TestPolkadotProjectInfo:
    """Project info tool tests against the real polkadot config."""

    def test_project_info_site_name(self, polkadot_ctx):
        """Project info returns correct site name."""
        result = _get_project_info(ctx=polkadot_ctx)
        assert result["site_name"] == "Polkadot Developer Docs"

    def test_project_info_document_count(self, polkadot_ctx):
        """Project info reports correct number of indexed documents."""
        result = _get_project_info(ctx=polkadot_ctx)
        assert result["document_count"] >= 140

    def test_project_info_index_ready(self, polkadot_ctx):
        """Index status is 'ready' after a successful build."""
        result = _get_project_info(ctx=polkadot_ctx)
        assert result["index_status"] == "ready"

    def test_project_info_has_theme(self, polkadot_ctx):
        """Project info includes the material theme from polkadot config."""
        result = _get_project_info(ctx=polkadot_ctx)
        assert result["theme"] == "material"

    def test_nav_has_sections(self, polkadot_ctx):
        """Navigation tree has top-level sections from .nav.yml files."""
        result = _get_project_info(ctx=polkadot_ctx)
        assert len(result["nav"]) >= 1


# ---------------------------------------------------------------------------
# TestPolkadotOutline
# ---------------------------------------------------------------------------


class TestPolkadotOutline:
    """Document outline tool tests against real polkadot docs."""

    def test_outline_for_index(self, polkadot_ctx):
        """Document outline for index.md has headings."""
        result = _get_document_outline("index.md", ctx=polkadot_ctx)
        assert "error" not in result
        assert "headings" in result

    def test_outline_heading_structure(self, polkadot_ctx):
        """Each heading in the outline has level, text, and anchor."""
        result = _get_document_outline(
            "node-infrastructure/index.md", ctx=polkadot_ctx
        )
        assert "error" not in result
        if result["headings"]:
            heading = result["headings"][0]
            for field in ("level", "text", "anchor"):
                assert field in heading

    def test_outline_nonexistent_file(self, polkadot_ctx):
        """Outline for a missing file returns error."""
        result = _get_document_outline("does-not-exist.md", ctx=polkadot_ctx)
        assert "error" in result

    def test_outline_echoes_path(self, polkadot_ctx):
        """Outline response echoes back the requested path."""
        result = _get_document_outline("index.md", ctx=polkadot_ctx)
        assert result["path"] == "index.md"
