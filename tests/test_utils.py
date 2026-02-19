"""Tests for mkdocs_mcp.utils — path validation, frontmatter, headings, slugify, FTS."""

from __future__ import annotations

from pathlib import Path

import pytest

from mkdocs_mcp.utils import (
    content_hash,
    extract_headings,
    markdown_to_text,
    parse_frontmatter,
    sanitize_fts_query,
    slugify,
    validate_doc_path,
)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestValidateDocPath:
    """Security-critical path validation tests."""

    def test_validate_doc_path_valid(self, tmp_docs_dir: Path) -> None:
        result = validate_doc_path("index.md", tmp_docs_dir)
        assert result == (tmp_docs_dir / "index.md").resolve()

    def test_validate_doc_path_subdirectory(self, tmp_docs_dir: Path) -> None:
        result = validate_doc_path("guide/getting-started.md", tmp_docs_dir)
        assert result == (tmp_docs_dir / "guide" / "getting-started.md").resolve()

    def test_validate_doc_path_absolute(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="Absolute paths are not allowed"):
            validate_doc_path("/etc/passwd", tmp_docs_dir)

    def test_validate_doc_path_traversal(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="traversal|not allowed"):
            validate_doc_path("../../etc/passwd", tmp_docs_dir)

    def test_validate_doc_path_windows_traversal(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="Backslashes|not allowed"):
            validate_doc_path("..\\..\\etc\\passwd", tmp_docs_dir)

    def test_validate_doc_path_symlink_escape(self, tmp_docs_dir: Path, tmp_path: Path) -> None:
        """Symlink inside docs_dir whose target lives outside docs_dir is rejected."""
        # Create a target file outside docs_dir
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.md"
        outside_file.write_text("secret content")

        link = tmp_docs_dir / "evil.md"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        with pytest.raises(ValueError, match="[Ss]ymlink|escapes"):
            validate_doc_path("evil.md", tmp_docs_dir)

    def test_validate_doc_path_symlink_chain(self, tmp_docs_dir: Path, tmp_path: Path) -> None:
        """Chain of symlinks where the final target escapes docs_dir is rejected."""
        outside_dir = tmp_path / "outside2"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret2.md"
        outside_file.write_text("more secret content")

        # intermediate symlink outside docs_dir
        intermediate = tmp_path / "hop.md"
        try:
            intermediate.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        # link inside docs_dir → intermediate (outside) → outside_file
        link = tmp_docs_dir / "chain.md"
        try:
            link.symlink_to(intermediate)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        with pytest.raises(ValueError, match="[Ss]ymlink|escapes"):
            validate_doc_path("chain.md", tmp_docs_dir)

    def test_validate_doc_path_nonexistent(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="[Ff]ile not found|not found"):
            validate_doc_path("nonexistent.md", tmp_docs_dir)

    def test_validate_doc_path_non_markdown(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="\\.md|markdown"):
            validate_doc_path("notes.txt", tmp_docs_dir)

    def test_validate_doc_path_url_encoded(self, tmp_docs_dir: Path) -> None:
        # %2e%2e%2f decodes to ../../ — should be caught as traversal
        with pytest.raises(ValueError, match="traversal|not allowed|escapes"):
            validate_doc_path("%2e%2e%2fetc/passwd", tmp_docs_dir)

    def test_validate_doc_path_empty(self, tmp_docs_dir: Path) -> None:
        with pytest.raises(ValueError, match="[Ee]mpty|not empty"):
            validate_doc_path("", tmp_docs_dir)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    """Tests for YAML frontmatter extraction."""

    def test_parse_frontmatter_present(self) -> None:
        content = (
            "---\n"
            "title: Hello\n"
            "description: A test.\n"
            "categories:\n"
            "  - alpha\n"
            "  - beta\n"
            "---\n"
            "\n"
            "# Body\n"
        )
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "Hello"
        assert meta["description"] == "A test."
        assert meta["categories"] == ["alpha", "beta"]
        assert "# Body" in body

    def test_parse_frontmatter_absent(self) -> None:
        content = "# No Frontmatter\n\nJust plain markdown.\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_parse_frontmatter_complex(self, tmp_docs_dir: Path) -> None:
        content = (tmp_docs_dir / "reference" / "api.md").read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "API Reference"
        assert meta["categories"] == ["reference", "api"]
        assert meta["tags"] == ["python", "rest"]
        assert meta["hide"] == ["toc"]
        assert meta["meta"]["author"] == "Test Author"
        assert meta["meta"]["version"] == 2
        assert "# API Reference" in body

    def test_parse_frontmatter_bom(self) -> None:
        content = "\ufeff---\ntitle: BOM Test\n---\n\nBody text.\n"
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "BOM Test"
        assert "Body text." in body

    def test_parse_frontmatter_non_dict(self) -> None:
        # YAML parses to a bare string, not a dict
        content = "---\njust a string\n---\n\nBody.\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_parse_frontmatter_only_file(self, tmp_docs_dir: Path) -> None:
        content = (tmp_docs_dir / "frontmatter-only.md").read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "Frontmatter Only"
        assert body == ""

    def test_parse_frontmatter_empty_file(self) -> None:
        meta, body = parse_frontmatter("")
        assert meta == {}
        assert body == ""


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


class TestExtractHeadings:
    """Tests for ATX heading extraction, including code block exclusion."""

    def test_extract_headings_basic(self, sample_markdown: str) -> None:
        # Strip frontmatter before calling extract_headings (it operates on body)
        _, body = parse_frontmatter(sample_markdown)
        headings = extract_headings(body)
        levels = [h["level"] for h in headings]
        texts = [h["text"] for h in headings]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels
        assert 4 in levels
        assert "Heading One" in texts
        assert "Heading Two" in texts
        assert "Heading Three" in texts
        assert "Heading Four" in texts

    def test_extract_headings_code_blocks(self, sample_markdown: str) -> None:
        _, body = parse_frontmatter(sample_markdown)
        headings = extract_headings(body)
        texts = [h["text"] for h in headings]
        # The line "# Not a heading" is inside a fenced code block
        assert "Not a heading" not in texts

    def test_extract_headings_empty(self) -> None:
        headings = extract_headings("")
        assert headings == []

    def test_extract_headings_no_headings(self) -> None:
        content = "Just some plain text.\n\nNo headings here.\n"
        headings = extract_headings(content)
        assert headings == []

    def test_extract_headings_anchor_set(self, sample_markdown: str) -> None:
        _, body = parse_frontmatter(sample_markdown)
        headings = extract_headings(body)
        for h in headings:
            assert "anchor" in h
            assert isinstance(h["anchor"], str)
            assert len(h["anchor"]) > 0

    def test_extract_headings_ignores_code_block_tilde(self) -> None:
        content = (
            "# Real Heading\n"
            "\n"
            "~~~\n"
            "# Inside tilde fence\n"
            "~~~\n"
            "\n"
            "## Another Real\n"
        )
        headings = extract_headings(content)
        texts = [h["text"] for h in headings]
        assert "Real Heading" in texts
        assert "Another Real" in texts
        assert "Inside tilde fence" not in texts


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """Tests for URL slug generation."""

    def test_slugify_basic(self) -> None:
        assert slugify("Getting Started") == "getting-started"

    def test_slugify_special_chars(self) -> None:
        assert slugify("API Reference (v2)") == "api-reference-v2"

    def test_slugify_multiple_spaces(self) -> None:
        assert slugify("Hello   World") == "hello-world"

    def test_slugify_leading_trailing(self) -> None:
        assert slugify(" --hello-- ") == "hello"

    def test_slugify_lowercase(self) -> None:
        assert slugify("ALL CAPS") == "all-caps"

    def test_slugify_underscores(self) -> None:
        assert slugify("snake_case_name") == "snake-case-name"

    def test_slugify_empty(self) -> None:
        assert slugify("") == ""

    def test_slugify_only_special(self) -> None:
        assert slugify("!!!") == ""


# ---------------------------------------------------------------------------
# Markdown to plain text
# ---------------------------------------------------------------------------


class TestMarkdownToText:
    """Tests for markdown → plain text conversion (FTS indexing)."""

    def test_markdown_to_text_basic(self) -> None:
        content = "# Heading\n\nSome **bold** and *italic* text.\n"
        result = markdown_to_text(content)
        assert "Heading" in result
        assert "bold" in result
        assert "italic" in result
        # Markdown formatting chars should be gone
        assert "**" not in result
        assert "*" not in result

    def test_markdown_to_text_preserves_spacing(self) -> None:
        content = "# First\n\nParagraph one.\n\n## Second\n\nParagraph two.\n"
        result = markdown_to_text(content)
        # Block elements should not be smashed together on one line
        assert "First" in result
        assert "Second" in result
        # Should have some whitespace separation, not "FirstParagraph"
        assert "FirstParagraph" not in result

    def test_markdown_to_text_includes_code(self) -> None:
        content = "```python\ndef hello():\n    return 'world'\n```\n"
        result = markdown_to_text(content)
        assert "hello" in result

    def test_markdown_to_text_strips_frontmatter(self, sample_markdown: str) -> None:
        result = markdown_to_text(sample_markdown)
        # Frontmatter keys/delimiters should not appear in plain text output
        assert "---" not in result
        assert "description:" not in result
        # But body content should be present
        assert "Heading One" in result

    def test_markdown_to_text_empty(self) -> None:
        result = markdown_to_text("")
        assert result == ""

    def test_markdown_to_text_only_frontmatter(self) -> None:
        content = "---\ntitle: Only Meta\n---"
        result = markdown_to_text(content)
        # Body is empty, result should be empty or whitespace only
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# FTS query sanitization
# ---------------------------------------------------------------------------


class TestSanitizeFtsQuery:
    """Tests for FTS5 query safety and sanitization."""

    def test_sanitize_fts_query_basic(self) -> None:
        result = sanitize_fts_query("hello world")
        assert result == '"hello" OR "world"'

    def test_sanitize_fts_query_special_chars(self) -> None:
        result = sanitize_fts_query('"(NEAR)*hello')
        # Operator chars stripped; NEAR keyword removed; hello remains
        assert "hello" in result
        assert '"hello"' in result
        assert "NEAR" not in result
        assert "(" not in result
        assert ")" not in result
        assert "*" not in result

    def test_sanitize_fts_query_empty(self) -> None:
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("***") == ""

    def test_sanitize_fts_query_keywords_removed(self) -> None:
        result = sanitize_fts_query("docs AND search OR filter NOT hidden")
        # FTS keywords AND, NOT should be stripped as standalone tokens.
        # OR is used by sanitize_fts_query itself as a joiner, so we only
        # check that the user-supplied keyword tokens are gone from the quoted terms.
        quoted_terms = [t.strip('" ') for t in result.split(" OR ")]
        assert "AND" not in quoted_terms
        assert "NOT" not in quoted_terms
        assert "OR" not in quoted_terms
        # The four real words should appear as quoted terms in the output
        assert '"docs"' in result
        assert '"search"' in result
        assert '"filter"' in result
        assert '"hidden"' in result

    def test_sanitize_fts_query_single_term(self) -> None:
        result = sanitize_fts_query("pytest")
        assert result == '"pytest"'

    @pytest.mark.parametrize(
        "weird_input",
        [
            '"',
            "()",
            "NEAR/3",
            'hello"world',
            '"(NOT)"',
            "***",
            "   ",
            "a OR b",
            "test AND fail",
            "🎉 emoji",
            "path/to/file",
            "<script>alert</script>",
            "DROP TABLE docs",
            '"; DROP TABLE',
            "a" * 1000,
            "\x00null",
            "café résumé",
            "日本語テスト",
            "hello\nworld",
            "NEAR(a,b,3)",
        ],
    )
    def test_sanitize_fts_query_no_exceptions(self, weird_input: str) -> None:
        """No input should cause sanitize_fts_query to raise an exception."""
        # Should never raise — just return a safe string (possibly empty)
        result = sanitize_fts_query(weird_input)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


class TestContentHash:
    """Tests for SHA-256 content hashing."""

    def test_content_hash_same_content(self) -> None:
        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_content_hash_different_content(self) -> None:
        h1 = content_hash("hello world")
        h2 = content_hash("goodbye world")
        assert h1 != h2

    def test_content_hash_returns_hex_string(self) -> None:
        result = content_hash("test")
        assert isinstance(result, str)
        # SHA-256 hex digest is 64 characters
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_content_hash_empty_string(self) -> None:
        result = content_hash("")
        # SHA-256 of empty string is well-known
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_content_hash_unicode(self) -> None:
        h1 = content_hash("café")
        h2 = content_hash("cafe")
        assert h1 != h2
