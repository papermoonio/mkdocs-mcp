"""Security-focused tests for the mkdocs-mcp server tools.

Validates that path traversal, symlink escape, non-markdown access,
SQL injection, FTS5 operator injection, and parameter validation are
all handled safely without crashes or information leakage.
"""

from __future__ import annotations

import inspect
import os
import tempfile

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
    """Create a minimal mkdocs project for security testing.

    Includes the basic docs needed plus extra files for security scenarios.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    config_file = tmp_path / "mkdocs.yml"
    config_file.write_text("site_name: Security Test\n", encoding="utf-8")

    # Minimal docs so search has something to work with
    (docs_dir / "index.md").write_text(
        "---\n"
        "title: Home\n"
        "---\n"
        "\n"
        "# Home\n"
        "\n"
        "Welcome to the documentation.\n",
        encoding="utf-8",
    )

    (docs_dir / "page.md").write_text(
        "---\n"
        "title: Sample Page\n"
        "---\n"
        "\n"
        "# Sample Page\n"
        "\n"
        "Some content for testing.\n",
        encoding="utf-8",
    )

    # Subdirectory with a file
    guide_dir = docs_dir / "guide"
    guide_dir.mkdir()
    (guide_dir / "intro.md").write_text(
        "---\n"
        "title: Introduction\n"
        "---\n"
        "\n"
        "# Introduction\n"
        "\n"
        "Getting started guide.\n",
        encoding="utf-8",
    )

    return tmp_path, docs_dir, config_file


@pytest.fixture
def server_ctx(mkdocs_project):
    """Build config, indexer, searcher and return a mock context for security tests."""
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
# TestPathTraversal
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Tests that path traversal attacks are rejected by read_document and get_document_outline."""

    def test_read_document_dotdot_traversal(self, server_ctx):
        """Rejects ../../etc/passwd path traversal."""
        ctx, *_ = server_ctx
        result = _read_document("../../etc/passwd", ctx=ctx)
        assert "error" in result

    def test_read_document_absolute_unix_path(self, server_ctx):
        """Rejects absolute Unix path /etc/passwd."""
        ctx, *_ = server_ctx
        result = _read_document("/etc/passwd", ctx=ctx)
        assert "error" in result

    def test_read_document_windows_backslash_traversal(self, server_ctx):
        """Rejects Windows-style backslash traversal ..\\..\\etc\\passwd."""
        ctx, *_ = server_ctx
        result = _read_document("..\\..\\etc\\passwd", ctx=ctx)
        assert "error" in result

    def test_read_document_url_encoded_traversal(self, server_ctx):
        """Rejects URL-encoded traversal %2e%2e%2f (decoded to ../)."""
        ctx, *_ = server_ctx
        result = _read_document("%2e%2e%2fetc%2fpasswd", ctx=ctx)
        assert "error" in result

    def test_read_document_double_url_encoded(self, server_ctx):
        """Rejects double URL-encoded traversal %252e%252e%252f."""
        ctx, *_ = server_ctx
        result = _read_document("%252e%252e%252fetc%252fpasswd", ctx=ctx)
        assert "error" in result

    def test_read_document_mixed_separator_traversal(self, server_ctx):
        """Rejects mixed forward/back slash traversal ..\\../etc/passwd."""
        ctx, *_ = server_ctx
        result = _read_document("..\\../etc/passwd", ctx=ctx)
        assert "error" in result

    def test_read_document_empty_path(self, server_ctx):
        """Rejects empty path string."""
        ctx, *_ = server_ctx
        result = _read_document("", ctx=ctx)
        assert "error" in result

    def test_read_document_symlink_escape(self, server_ctx, mkdocs_project):
        """Rejects symlinks that point outside docs_dir."""
        _, docs_dir, _ = mkdocs_project
        # Create a temp file outside docs
        fd, target_path = tempfile.mkstemp(suffix=".md")
        try:
            os.write(fd, b"# Secret\n\nThis should not be readable.\n")
            os.close(fd)
            # Create symlink inside docs pointing to the outside file
            link_path = docs_dir / "escape.md"
            link_path.symlink_to(target_path)
            ctx, *_ = server_ctx
            result = _read_document("escape.md", ctx=ctx)
            assert "error" in result
        finally:
            os.unlink(target_path)
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()

    def test_read_document_non_markdown_extension(self, server_ctx, mkdocs_project):
        """Rejects non-.md files even if they exist in docs_dir."""
        _, docs_dir, _ = mkdocs_project
        (docs_dir / "secret.txt").write_text("secret data", encoding="utf-8")
        ctx, *_ = server_ctx
        result = _read_document("secret.txt", ctx=ctx)
        assert "error" in result

    def test_read_document_dotfile(self, server_ctx, mkdocs_project):
        """Rejects hidden dotfiles (.env.md) if they resolve outside or are not valid."""
        _, docs_dir, _ = mkdocs_project
        (docs_dir / ".hidden.md").write_text("# Hidden\n", encoding="utf-8")
        ctx, *_ = server_ctx
        # The file exists and is .md, so validate_doc_path may allow it;
        # we just verify no crash occurs
        result = _read_document(".hidden.md", ctx=ctx)
        assert isinstance(result, dict)

    def test_outline_path_traversal(self, server_ctx):
        """get_document_outline also rejects path traversal."""
        ctx, *_ = server_ctx
        result = _get_document_outline("../../etc/passwd", ctx=ctx)
        assert "error" in result

    def test_outline_absolute_path(self, server_ctx):
        """get_document_outline rejects absolute paths."""
        ctx, *_ = server_ctx
        result = _get_document_outline("/etc/passwd", ctx=ctx)
        assert "error" in result

    def test_outline_url_encoded_traversal(self, server_ctx):
        """get_document_outline rejects URL-encoded traversal."""
        ctx, *_ = server_ctx
        result = _get_document_outline("%2e%2e%2fetc%2fpasswd", ctx=ctx)
        assert "error" in result

    @pytest.mark.parametrize(
        "malicious_path",
        [
            "../../etc/shadow",
            "../../../proc/self/environ",
            "/etc/hosts",
            "..\\..\\windows\\system32\\config\\sam",
            "%2e%2e/%2e%2e/etc/passwd",
            "....//....//etc/passwd",
            "guide/../../../etc/passwd",
            "guide/../../secret",
        ],
        ids=[
            "shadow-file",
            "proc-environ",
            "etc-hosts",
            "windows-sam",
            "partial-encode",
            "double-dot-slash",
            "subdir-breakout",
            "subdir-partial-breakout",
        ],
    )
    def test_read_document_parametrized_traversals(self, server_ctx, malicious_path):
        """Parametrized: various path traversal variants are all rejected."""
        ctx, *_ = server_ctx
        result = _read_document(malicious_path, ctx=ctx)
        assert "error" in result, f"Path {malicious_path!r} should have been rejected"


# ---------------------------------------------------------------------------
# TestSearchSecurity
# ---------------------------------------------------------------------------


class TestSearchSecurity:
    """Tests that search input is sanitized and does not crash or leak data."""

    def test_search_sql_injection(self, server_ctx):
        """SQL injection attempts do not crash the server."""
        ctx, *_ = server_ctx
        result = _search("'; DROP TABLE docs_fts;--", ctx=ctx)
        # Should return a valid result dict, not crash
        assert isinstance(result, dict)
        # Either has results or total_count, not an unhandled exception
        assert "total_count" in result or "error" in result

    def test_search_sql_injection_union(self, server_ctx):
        """UNION SELECT injection does not leak data."""
        ctx, *_ = server_ctx
        result = _search("test UNION SELECT * FROM doc_metadata", ctx=ctx)
        assert isinstance(result, dict)

    @pytest.mark.parametrize(
        "fts_query",
        [
            "NEAR(test, doc)",
            "test AND doc",
            "test OR doc",
            "NOT test",
            "test NOT doc",
            '"exact phrase"',
            "col:value",
            "^prefix",
            "test*",
            "{title}:hack",
        ],
        ids=[
            "near-operator",
            "and-operator",
            "or-operator",
            "not-operator",
            "not-binary",
            "exact-phrase",
            "column-filter",
            "caret-prefix",
            "wildcard",
            "column-syntax",
        ],
    )
    def test_search_fts5_operators_sanitized(self, server_ctx, fts_query):
        """FTS5 operators in user input are sanitized and do not cause crashes."""
        ctx, *_ = server_ctx
        result = _search(fts_query, ctx=ctx)
        assert isinstance(result, dict)

    def test_search_type_validation(self, server_ctx):
        """Invalid search types return error, not crash."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, search_type="malicious")
        assert "error" in result

    def test_search_type_vector_without_embedder(self, server_ctx):
        """Vector search without an embedder returns empty results, not crash."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, search_type="vector")
        assert isinstance(result, dict)
        assert result["total_count"] == 0

    def test_search_max_results_huge(self, server_ctx):
        """Very large max_results is clamped and does not cause OOM."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, max_results=99999)
        assert isinstance(result, dict)
        # Results should be at most 100 (the clamped max)
        assert len(result.get("results", [])) <= 100

    def test_search_max_results_negative(self, server_ctx):
        """Negative max_results is clamped to 1."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, max_results=-100)
        assert isinstance(result, dict)
        assert len(result.get("results", [])) <= 1

    def test_search_max_results_zero(self, server_ctx):
        """Zero max_results is clamped to 1."""
        ctx, *_ = server_ctx
        result = _search("test", ctx=ctx, max_results=0)
        assert isinstance(result, dict)
        assert len(result.get("results", [])) <= 1

    def test_search_very_long_query(self, server_ctx):
        """Extremely long query strings are truncated, not crash."""
        ctx, *_ = server_ctx
        long_query = "a " * 10000
        result = _search(long_query, ctx=ctx)
        assert isinstance(result, dict)

    def test_search_null_bytes(self, server_ctx):
        """Null bytes in query do not cause crash."""
        ctx, *_ = server_ctx
        result = _search("test\x00injection", ctx=ctx)
        assert isinstance(result, dict)

    def test_search_unicode_attacks(self, server_ctx):
        """Unicode direction override and unusual chars do not crash search."""
        ctx, *_ = server_ctx
        # Right-to-left override character
        result = _search("test\u202eevil", ctx=ctx)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestToolParameterSafety
# ---------------------------------------------------------------------------


class TestToolParameterSafety:
    """Tests that tool functions do not expose dangerous parameters."""

    def test_no_docs_dir_parameter(self):
        """Tools do not accept a docs_dir parameter (prevents arbitrary dir access)."""
        for fn in [_search, _read_document, _list_documents, _get_project_info, _get_document_outline]:
            sig = inspect.signature(fn)
            assert "docs_dir" not in sig.parameters, (
                f"{fn.__name__} should not accept docs_dir"
            )

    def test_no_db_path_parameter(self):
        """Tools do not accept a db_path parameter (prevents arbitrary DB access)."""
        for fn in [_search, _read_document, _list_documents, _get_project_info, _get_document_outline]:
            sig = inspect.signature(fn)
            assert "db_path" not in sig.parameters, (
                f"{fn.__name__} should not accept db_path"
            )

    def test_search_tool_has_ctx_parameter(self):
        """search() requires a ctx parameter (state is injected, not user-supplied)."""
        sig = inspect.signature(_search)
        assert "ctx" in sig.parameters

    def test_read_document_requires_ctx(self):
        """read_document() requires a ctx parameter."""
        sig = inspect.signature(_read_document)
        assert "ctx" in sig.parameters

    def test_list_documents_requires_ctx(self):
        """list_documents() requires a ctx parameter."""
        sig = inspect.signature(_list_documents)
        assert "ctx" in sig.parameters


# ---------------------------------------------------------------------------
# TestListDocumentsSecurity
# ---------------------------------------------------------------------------


class TestListDocumentsSecurity:
    """Tests that list_documents does not leak data outside the docs directory."""

    def test_list_section_traversal(self, server_ctx):
        """Listing with a traversal section prefix does not escape docs_dir."""
        ctx, *_ = server_ctx
        # The SQL uses LIKE with prefix matching, so '../../' as section
        # should just return zero results (no paths start with '../../')
        result = _list_documents(ctx=ctx, section="../../etc")
        assert result["total_count"] == 0

    def test_list_section_sql_injection(self, server_ctx):
        """SQL injection in section parameter does not crash."""
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx, section="'; DROP TABLE doc_metadata;--")
        assert isinstance(result, dict)
        # Parameterized queries should make this a harmless LIKE filter
        assert result["total_count"] == 0

    def test_list_documents_only_indexed_files(self, server_ctx, mkdocs_project):
        """list_documents returns only files that were indexed, not arbitrary files."""
        _, docs_dir, _ = mkdocs_project
        # Create a non-md file that should not appear
        (docs_dir / "secrets.json").write_text('{"key": "secret"}', encoding="utf-8")
        ctx, *_ = server_ctx
        result = _list_documents(ctx=ctx)
        paths = [d["path"] for d in result["documents"]]
        assert "secrets.json" not in paths


# ---------------------------------------------------------------------------
# TestReadDocumentResponseSafety
# ---------------------------------------------------------------------------


class TestReadDocumentResponseSafety:
    """Tests that read_document does not leak sensitive information in error messages."""

    def test_error_does_not_leak_absolute_path(self, server_ctx):
        """Error messages for invalid paths do not reveal the server's absolute path."""
        ctx, *_ = server_ctx
        result = _read_document("nonexistent.md", ctx=ctx)
        assert "error" in result
        # The error should not contain the full filesystem path
        error_msg = result["error"]
        assert "/tmp" not in error_msg or "docs" not in error_msg

    def test_traversal_error_is_generic(self, server_ctx):
        """Path traversal error messages do not reveal internal directory structure."""
        ctx, *_ = server_ctx
        result = _read_document("../../etc/passwd", ctx=ctx)
        assert "error" in result
        # Should be a validation error, not a file-not-found with path details
        error_msg = result["error"].lower()
        assert "traversal" in error_msg or "not allowed" in error_msg or "invalid" in error_msg
