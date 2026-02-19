"""MCP tool integration tests for the mkdocs-mcp server.

Tests each of the five MCP tools (search, read_document, list_documents,
get_project_info, get_document_outline) by calling the underlying function
directly with a mock Context that provides real config/indexer/searcher state.
"""

from __future__ import annotations

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mkdocs_project(tmp_path):
    """Create a minimal mkdocs project with docs directory and config file.

    Produces 5 markdown files covering different content areas so that
    search, list, read, and outline tools all have meaningful data to
    operate on.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    # mkdocs.yml
    config_file = tmp_path / "mkdocs.yml"
    config_file.write_text("site_name: Test Project\n", encoding="utf-8")

    # docs/index.md
    (docs_dir / "index.md").write_text(
        "---\n"
        "title: Welcome\n"
        "description: Homepage of the test project.\n"
        "---\n"
        "\n"
        "# Welcome\n"
        "\n"
        "Welcome to the test project documentation.\n"
        "\n"
        "## Overview\n"
        "\n"
        "This project demonstrates MCP tool integration.\n"
        "\n"
        "## Quick Start\n"
        "\n"
        "Read the getting-started guide to begin.\n",
        encoding="utf-8",
    )

    # docs/getting-started.md
    (docs_dir / "getting-started.md").write_text(
        "---\n"
        "title: Getting Started\n"
        "description: Installation and setup instructions.\n"
        "---\n"
        "\n"
        "# Getting Started\n"
        "\n"
        "Follow these steps for installation.\n"
        "\n"
        "## Installation\n"
        "\n"
        "Install via pip:\n"
        "\n"
        "```bash\n"
        "pip install test-project\n"
        "```\n"
        "\n"
        "## Configuration\n"
        "\n"
        "Edit your config file after installing.\n",
        encoding="utf-8",
    )

    # docs/api.md
    (docs_dir / "api.md").write_text(
        "---\n"
        "title: API Reference\n"
        "description: Public API documentation.\n"
        "categories:\n"
        "  - reference\n"
        "---\n"
        "\n"
        "# API Reference\n"
        "\n"
        "Complete API documentation for the project.\n"
        "\n"
        "## Endpoints\n"
        "\n"
        "### GET /users\n"
        "\n"
        "Returns all users.\n"
        "\n"
        "### POST /users\n"
        "\n"
        "Creates a new user.\n",
        encoding="utf-8",
    )

    # docs/guide/setup.md (subdirectory)
    guide_dir = docs_dir / "guide"
    guide_dir.mkdir()
    (guide_dir / "setup.md").write_text(
        "---\n"
        "title: Setup Guide\n"
        "description: Detailed setup instructions.\n"
        "---\n"
        "\n"
        "# Setup Guide\n"
        "\n"
        "This guide walks through the full setup process.\n"
        "\n"
        "## Prerequisites\n"
        "\n"
        "You need Python 3.10 or later.\n"
        "\n"
        "## Step-by-Step\n"
        "\n"
        "Follow the steps below carefully.\n",
        encoding="utf-8",
    )

    # docs/guide/advanced.md (subdirectory)
    (guide_dir / "advanced.md").write_text(
        "---\n"
        "title: Advanced Usage\n"
        "description: Advanced features and configuration.\n"
        "---\n"
        "\n"
        "# Advanced Usage\n"
        "\n"
        "This section covers advanced topics.\n"
        "\n"
        "## Custom Plugins\n"
        "\n"
        "Write your own plugins for extended functionality.\n"
        "\n"
        "## Performance Tuning\n"
        "\n"
        "Optimize for large documentation sites.\n",
        encoding="utf-8",
    )

    return tmp_path, docs_dir, config_file


@pytest.fixture
def server_ctx(mkdocs_project):
    """Build config, indexer, searcher from the mkdocs project and return a mock context."""
    project_root, docs_dir, config_file = mkdocs_project
    config = MkDocsConfig.from_file(config_file)
    indexer = DocIndexer(docs_dir)
    indexer.build_index()
    searcher = DocSearcher(indexer.db_path)
    ctx = _make_ctx(config, indexer, searcher)
    yield ctx, config, indexer, searcher
    searcher.close()
    indexer.close()


# ---------------------------------------------------------------------------
# TestSearchTool
# ---------------------------------------------------------------------------


class TestSearchTool:
    """Tests for the search() MCP tool."""

    def test_search_returns_results(self, server_ctx):
        """search() returns results for a known term present in docs."""
        ctx, *_ = server_ctx
        result = _search("installation", ctx=ctx)
        assert "results" in result
        assert len(result["results"]) >= 1

    def test_search_keyword_type(self, server_ctx):
        """search() with search_type='keyword' uses keyword search and reports it."""
        ctx, *_ = server_ctx
        result = _search("welcome", ctx=ctx, search_type="keyword")
        assert result["search_type"] == "keyword"
        assert result["total_count"] >= 1

    def test_search_hybrid_type(self, server_ctx):
        """search() defaults to hybrid and returns results."""
        ctx, *_ = server_ctx
        result = _search("setup", ctx=ctx)
        assert result["search_type"] == "hybrid"

    def test_search_invalid_type(self, server_ctx):
        """search() with an invalid search_type returns an error dict."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, search_type="invalid")
        assert "error" in result

    def test_search_empty_query(self, server_ctx):
        """search() with an empty query returns zero results."""
        ctx, *_ = server_ctx
        result = _search("", ctx=ctx)
        assert result["total_count"] == 0

    def test_search_whitespace_query(self, server_ctx):
        """search() with whitespace-only query returns zero results."""
        ctx, *_ = server_ctx
        result = _search("   ", ctx=ctx)
        assert result["total_count"] == 0

    def test_search_max_results(self, server_ctx):
        """search() respects the max_results parameter."""
        ctx, *_ = server_ctx
        result = _search("the", ctx=ctx, max_results=2)
        assert len(result["results"]) <= 2

    def test_search_result_has_required_fields(self, server_ctx):
        """Each search result contains path, title, score, snippet, search_method."""
        ctx, *_ = server_ctx
        result = _search("installation", ctx=ctx, search_type="keyword")
        assert result["total_count"] >= 1
        match = result["results"][0]
        assert "path" in match
        assert "title" in match
        assert "score" in match
        assert "snippet" in match
        assert "search_method" in match

    def test_search_returns_dict(self, server_ctx):
        """search() always returns a plain dict (serialized from Pydantic model)."""
        ctx, *_ = server_ctx
        result = _search("welcome", ctx=ctx)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestReadDocumentTool
# ---------------------------------------------------------------------------


class TestReadDocumentTool:
    """Tests for the read_document() MCP tool."""

    def test_read_document_success(self, server_ctx):
        """read_document() returns content dict for a valid path."""
        ctx, *_ = server_ctx
        result = _read_document("index.md", ctx=ctx)
        assert "content" in result
        assert result["title"] == "Welcome"
        assert "frontmatter" in result

    def test_read_document_not_found(self, server_ctx):
        """read_document() returns error dict for a nonexistent file."""
        ctx, *_ = server_ctx
        result = _read_document("nonexistent.md", ctx=ctx)
        assert "error" in result

    def test_read_document_subdirectory(self, server_ctx):
        """read_document() successfully reads files in subdirectories."""
        ctx, *_ = server_ctx
        result = _read_document("guide/setup.md", ctx=ctx)
        assert "content" in result
        assert result["title"] == "Setup Guide"

    def test_read_document_has_headings(self, server_ctx):
        """read_document() includes a list of parsed headings."""
        ctx, *_ = server_ctx
        result = _read_document("index.md", ctx=ctx)
        assert "headings" in result
        assert len(result["headings"]) >= 1
        # Check heading structure
        heading = result["headings"][0]
        assert "level" in heading
        assert "text" in heading
        assert "anchor" in heading

    def test_read_document_has_size(self, server_ctx):
        """read_document() includes byte size of the file."""
        ctx, *_ = server_ctx
        result = _read_document("index.md", ctx=ctx)
        assert "size" in result
        assert result["size"] > 0

    def test_read_document_has_description(self, server_ctx):
        """read_document() extracts description from frontmatter."""
        ctx, *_ = server_ctx
        result = _read_document("api.md", ctx=ctx)
        assert result["description"] == "Public API documentation."

    def test_read_document_has_categories(self, server_ctx):
        """read_document() extracts categories from frontmatter."""
        ctx, *_ = server_ctx
        result = _read_document("api.md", ctx=ctx)
        assert "categories" in result
        assert "reference" in result["categories"]

    def test_read_document_content_is_raw_markdown(self, server_ctx):
        """read_document() returns the raw markdown source, not rendered HTML."""
        ctx, *_ = server_ctx
        result = _read_document("index.md", ctx=ctx)
        # Raw content should contain markdown syntax
        assert "# Welcome" in result["content"]

    def test_read_document_path_traversal(self, server_ctx):
        """read_document() rejects path traversal attempts."""
        ctx, *_ = server_ctx
        result = _read_document("../../etc/passwd", ctx=ctx)
        assert "error" in result


# ---------------------------------------------------------------------------
# TestListDocumentsTool
# ---------------------------------------------------------------------------


class TestListDocumentsTool:
    """Tests for the list_documents() MCP tool."""

    def test_list_all_documents(self, server_ctx):
        """list_documents() without a section filter returns all indexed docs."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx)
        assert "documents" in result
        assert result["total_count"] >= 5

    def test_list_filtered_by_section(self, server_ctx):
        """list_documents(section='guide') returns only docs in the guide/ directory."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx, section="guide")
        assert result["total_count"] >= 2
        for doc in result["documents"]:
            assert doc["path"].startswith("guide/"), (
                f"Expected path to start with 'guide/', got: {doc['path']}"
            )

    def test_list_empty_section(self, server_ctx):
        """list_documents() for a nonexistent section returns zero results."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx, section="nonexistent")
        assert result["total_count"] == 0
        assert result["documents"] == []

    def test_list_documents_has_required_fields(self, server_ctx):
        """Each document in the listing contains path, title, size, and mtime."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx)
        assert result["total_count"] >= 1
        doc = result["documents"][0]
        assert "path" in doc
        assert "title" in doc
        assert "size" in doc
        assert "mtime" in doc

    def test_list_documents_section_trailing_slash(self, server_ctx):
        """list_documents() normalizes section with or without trailing slash."""
        ctx, *_ = server_ctx
        result_no_slash = _list_documents(ctx=ctx, section="guide")
        result_with_slash = _list_documents(ctx=ctx, section="guide/")
        assert result_no_slash["total_count"] == result_with_slash["total_count"]

    def test_list_documents_returns_dict(self, server_ctx):
        """list_documents() returns a plain dict."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestGetProjectInfoTool
# ---------------------------------------------------------------------------


class TestGetProjectInfoTool:
    """Tests for the get_project_info() MCP tool."""

    def test_project_info_site_name(self, server_ctx):
        """get_project_info() returns the correct site_name from mkdocs.yml."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert result["site_name"] == "Test Project"

    def test_project_info_has_docs_dir(self, server_ctx):
        """get_project_info() includes the docs_dir path."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert "docs_dir" in result
        assert len(result["docs_dir"]) > 0

    def test_project_info_document_count(self, server_ctx):
        """get_project_info() reports the correct number of indexed documents."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert result["document_count"] >= 5

    def test_project_info_index_status(self, server_ctx):
        """get_project_info() reports index as ready when freshly built."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert result["index_status"] == "ready"

    def test_project_info_returns_dict(self, server_ctx):
        """get_project_info() returns a plain dict."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert isinstance(result, dict)

    def test_project_info_has_theme(self, server_ctx):
        """get_project_info() includes theme field (may be None for minimal config)."""
        ctx, *_ = server_ctx
        result = _get_project_info(ctx=ctx)
        assert "theme" in result


# ---------------------------------------------------------------------------
# TestGetDocumentOutlineTool
# ---------------------------------------------------------------------------


class TestGetDocumentOutlineTool:
    """Tests for the get_document_outline() MCP tool."""

    def test_outline_returns_headings(self, server_ctx):
        """get_document_outline() returns a headings list with at least one entry."""
        ctx, *_ = server_ctx
        result = _get_document_outline("index.md", ctx=ctx)
        assert "headings" in result
        assert len(result["headings"]) >= 1

    def test_outline_heading_structure(self, server_ctx):
        """Each heading in the outline has level, text, and anchor fields."""
        ctx, *_ = server_ctx
        result = _get_document_outline("index.md", ctx=ctx)
        for heading in result["headings"]:
            assert "level" in heading
            assert "text" in heading
            assert "anchor" in heading

    def test_outline_title_from_frontmatter(self, server_ctx):
        """get_document_outline() uses frontmatter title when available."""
        ctx, *_ = server_ctx
        result = _get_document_outline("index.md", ctx=ctx)
        assert result["title"] == "Welcome"

    def test_outline_invalid_path(self, server_ctx):
        """get_document_outline() with a traversal path returns error."""
        ctx, *_ = server_ctx
        result = _get_document_outline("../../etc/passwd", ctx=ctx)
        assert "error" in result

    def test_outline_nonexistent_file(self, server_ctx):
        """get_document_outline() for a missing file returns error."""
        ctx, *_ = server_ctx
        result = _get_document_outline("does-not-exist.md", ctx=ctx)
        assert "error" in result

    def test_outline_subdirectory_file(self, server_ctx):
        """get_document_outline() works for files in subdirectories."""
        ctx, *_ = server_ctx
        result = _get_document_outline("guide/setup.md", ctx=ctx)
        assert "headings" in result
        assert result["title"] == "Setup Guide"

    def test_outline_includes_path(self, server_ctx):
        """get_document_outline() echoes back the requested path."""
        ctx, *_ = server_ctx
        result = _get_document_outline("api.md", ctx=ctx)
        assert result["path"] == "api.md"

    def test_outline_returns_dict(self, server_ctx):
        """get_document_outline() returns a plain dict."""
        ctx, *_ = server_ctx
        result = _get_document_outline("index.md", ctx=ctx)
        assert isinstance(result, dict)
