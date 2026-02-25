"""Shared fixtures for papermoon-mkdocs-mcp tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_ctx(config, indexer, searcher):
    """Create a mock Context whose lifespan_context returns the real objects."""
    ctx = MagicMock()
    ctx.lifespan_context = {"config": config, "indexer": indexer, "searcher": searcher}
    return ctx


@pytest.fixture
def tmp_docs_dir(tmp_path: Path) -> Path:
    """Create a temporary docs directory with sample markdown files.

    Layout:
        index.md               — frontmatter + headings
        guide/getting-started.md — frontmatter + code block with # inside
        reference/api.md       — complex frontmatter (lists, nested, hide field)
        empty.md               — truly empty file
        frontmatter-only.md    — frontmatter with no body
        notes.txt              — non-markdown file
    """
    docs = tmp_path / "docs"
    docs.mkdir()

    # index.md
    (docs / "index.md").write_text(
        "---\n"
        "title: Home\n"
        "description: The main entry point for the documentation.\n"
        "categories:\n"
        "  - overview\n"
        "  - getting-started\n"
        "---\n"
        "\n"
        "# Welcome\n"
        "\n"
        "This is the home page.\n"
        "\n"
        "## Introduction\n"
        "\n"
        "Some introductory text.\n"
        "\n"
        "### Details\n"
        "\n"
        "Even more detail here.\n",
        encoding="utf-8",
    )

    # guide/getting-started.md
    guide_dir = docs / "guide"
    guide_dir.mkdir()
    (guide_dir / "getting-started.md").write_text(
        "---\n"
        "title: Getting Started\n"
        "description: How to get started quickly.\n"
        "---\n"
        "\n"
        "# Getting Started\n"
        "\n"
        "Follow these steps:\n"
        "\n"
        "```bash\n"
        "# Not a heading — this is inside a code block\n"
        "pip install papermoon-mkdocs-mcp\n"
        "```\n"
        "\n"
        "## Installation\n"
        "\n"
        "Install via pip.\n",
        encoding="utf-8",
    )

    # reference/api.md
    ref_dir = docs / "reference"
    ref_dir.mkdir()
    (ref_dir / "api.md").write_text(
        "---\n"
        "title: API Reference\n"
        "description: Complete API documentation.\n"
        "categories:\n"
        "  - reference\n"
        "  - api\n"
        "tags:\n"
        "  - python\n"
        "  - rest\n"
        "hide:\n"
        "  - toc\n"
        "meta:\n"
        "  author: Test Author\n"
        "  version: 2\n"
        "---\n"
        "\n"
        "# API Reference\n"
        "\n"
        "This page documents the public API.\n",
        encoding="utf-8",
    )

    # empty.md
    (docs / "empty.md").write_text("", encoding="utf-8")

    # frontmatter-only.md
    (docs / "frontmatter-only.md").write_text(
        "---\n"
        "title: Frontmatter Only\n"
        "description: No body content.\n"
        "---",
        encoding="utf-8",
    )

    # notes.txt — non-markdown file
    (docs / "notes.txt").write_text("These are some plain text notes.\n", encoding="utf-8")

    return docs


@pytest.fixture
def sample_markdown() -> str:
    """Return a markdown string with frontmatter, varied headings, and a fenced code block."""
    return (
        "---\n"
        "title: Sample Document\n"
        "description: A sample for testing.\n"
        "---\n"
        "\n"
        "# Heading One\n"
        "\n"
        "Some paragraph text with `inline code` here.\n"
        "\n"
        "## Heading Two\n"
        "\n"
        "More text.\n"
        "\n"
        "### Heading Three\n"
        "\n"
        "Even more text.\n"
        "\n"
        "#### Heading Four\n"
        "\n"
        "Deep heading.\n"
        "\n"
        "```python\n"
        "# Not a heading\n"
        "def hello():\n"
        "    return 'world'\n"
        "```\n"
        "\n"
        "!!! note\n"
        "    This is an admonition block.\n"
        "\n"
        "Final paragraph.\n"
    )
