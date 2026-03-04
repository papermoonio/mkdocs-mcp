"""MCP server exposing MkDocs documentation via FastMCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP

from mkdocs_mcp.config import MkDocsConfig
from mkdocs_mcp.indexer import DocIndexer
from mkdocs_mcp.models import (
    DocumentContent,
    DocumentInfo,
    DocumentOutline,
    HeadingInfo,
    ProjectInfo,
)
from mkdocs_mcp.searcher import DocSearcher
from mkdocs_mcp.utils import extract_headings, parse_frontmatter, validate_doc_path

logger = logging.getLogger(__name__)

_MAX_READ_SIZE = 10 * 1024 * 1024  # 10 MB
_config_path_override: str | None = None


def _read_doc_file(path: str, docs_dir: Path) -> str | dict:
    """Validate, size-check, and read a documentation file.

    Returns the raw content string on success, or an error dict on failure.
    """
    try:
        full_path = validate_doc_path(path, docs_dir)
    except ValueError as exc:
        return {"error": f"Invalid path: {exc}"}

    try:
        if full_path.stat().st_size > _MAX_READ_SIZE:
            return {"error": "File too large"}
    except OSError:
        return {"error": "File not found"}

    try:
        return full_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {"error": "Failed to read file"}


# ---------------------------------------------------------------------------
# Lifespan: initialise config, indexer, and searcher once at startup
# ---------------------------------------------------------------------------


def _try_load_embedder() -> Any:
    """Attempt to load sentence-transformers for vector search.

    Returns None if the optional dependency is not installed.
    """
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        logger.info("sentence-transformers not installed; vector search disabled")
        return None


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Initialise shared state for the MCP server.

    Reads the MkDocs config, builds/refreshes the search index, and
    yields config, indexer, and searcher instances to the tool handlers.
    """
    config_path: str | None = _config_path_override

    if config_path:
        config = MkDocsConfig.from_file(Path(config_path))
    else:
        config = MkDocsConfig.detect()

    embedder = await asyncio.to_thread(_try_load_embedder)

    indexer = DocIndexer(config.docs_dir)
    await asyncio.to_thread(indexer.update_index, embedder)

    searcher = DocSearcher(indexer.db_path, embedder)

    try:
        yield {
            "config": config,
            "indexer": indexer,
            "searcher": searcher,
        }
    finally:
        searcher.close()
        indexer.close()


mcp = FastMCP("papermoon-mkdocs-mcp", lifespan=app_lifespan)


# ---------------------------------------------------------------------------
# Tool 1: search
# ---------------------------------------------------------------------------


@mcp.tool
def search(query: str, ctx: Context, search_type: str = "hybrid", max_results: int = 10) -> dict:
    """Search documentation using keyword, semantic, or hybrid search.

    Args:
        query: The search query string.
        search_type: One of 'keyword', 'vector', or 'hybrid' (default).
        max_results: Maximum number of results to return (1-100, default 10).
    """
    searcher: DocSearcher = ctx.lifespan_context["searcher"]
    try:
        result = searcher.search(query, search_type, max_results)
    except ValueError as exc:
        return {"error": str(exc)}
    return result.model_dump()


# ---------------------------------------------------------------------------
# Tool 2: read_document
# ---------------------------------------------------------------------------


@mcp.tool
def read_document(path: str, ctx: Context) -> dict:
    """Read a documentation file by its relative path.

    Args:
        path: Relative path from the docs directory (e.g. 'guide/setup.md').
    """
    config: MkDocsConfig = ctx.lifespan_context["config"]

    result = _read_doc_file(path, config.docs_dir)
    if isinstance(result, dict):
        return result
    raw_content = result

    frontmatter, body = parse_frontmatter(raw_content)
    headings_raw = extract_headings(body)
    headings = [
        HeadingInfo(level=h["level"], text=h["text"], anchor=h["anchor"]) for h in headings_raw
    ]

    title = frontmatter.get("title", "")
    if not title and headings:
        title = headings[0].text

    description = frontmatter.get("description")
    categories = frontmatter.get("categories", [])
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",")]

    doc = DocumentContent(
        path=path,
        title=title,
        description=description,
        categories=categories,
        content=body,
        headings=headings,
        frontmatter=frontmatter,
        size=len(raw_content.encode("utf-8")),
    )
    return doc.model_dump()


# ---------------------------------------------------------------------------
# Tool 3: list_documents
# ---------------------------------------------------------------------------


@mcp.tool
def list_documents(ctx: Context, section: str | None = None) -> dict:
    """List all documentation files, optionally filtered by section.

    Args:
        section: Optional section prefix to filter by (e.g. 'guide').
    """
    searcher: DocSearcher = ctx.lifespan_context["searcher"]
    rows = searcher.list_documents(section)

    documents: list[dict[str, Any]] = []
    for row in rows:
        path_str, title, description, categories_json, mtime, size = row
        categories = json.loads(categories_json) if categories_json else []

        doc_info = DocumentInfo(
            path=path_str,
            title=title or path_str,
            description=description,
            categories=categories,
            size=size or 0,
            mtime=mtime,
        )
        documents.append(doc_info.model_dump())

    return {"documents": documents, "total_count": len(documents)}


# ---------------------------------------------------------------------------
# Tool 4: get_project_info
# ---------------------------------------------------------------------------


@mcp.tool
def get_project_info(ctx: Context) -> dict:
    """Get MkDocs project information: name, theme, navigation, and config."""
    config: MkDocsConfig = ctx.lifespan_context["config"]
    indexer: DocIndexer = ctx.lifespan_context["indexer"]

    status = indexer.get_index_status()

    info = ProjectInfo(
        site_name=config.site_name,
        site_url=config.site_url,
        docs_dir=str(config.docs_dir.relative_to(config.project_root)),
        theme=config.theme_name,
        nav=config.nav,
        document_count=status.total_documents,
        index_status="ready" if status.is_fresh else "stale",
    )
    return info.model_dump()


# ---------------------------------------------------------------------------
# Tool 5: get_document_outline
# ---------------------------------------------------------------------------


@mcp.tool
def get_document_outline(path: str, ctx: Context) -> dict:
    """Get the heading structure / table of contents for a document.

    Args:
        path: Relative path from the docs directory (e.g. 'guide/setup.md').
    """
    config: MkDocsConfig = ctx.lifespan_context["config"]

    result = _read_doc_file(path, config.docs_dir)
    if isinstance(result, dict):
        return result
    raw_content = result

    frontmatter, body = parse_frontmatter(raw_content)
    headings_raw = extract_headings(body)
    headings = [
        HeadingInfo(level=h["level"], text=h["text"], anchor=h["anchor"]) for h in headings_raw
    ]

    title = ""
    if headings:
        title = headings[0].text
    if frontmatter.get("title"):
        title = frontmatter["title"]

    outline = DocumentOutline(path=path, title=title, headings=headings)
    return outline.model_dump()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MkDocs MCP server from the command line."""
    import argparse

    parser = argparse.ArgumentParser(description="MkDocs MCP Server")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to mkdocs.yml (auto-detected if omitted)",
    )
    parser.add_argument(
        "--transport",
        type=str,
        default="stdio",
        help="Transport protocol: stdio, sse, or streamable-http (default: stdio)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to when using network transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to when using network transports (default: 8000)",
    )
    args = parser.parse_args()

    if args.config:
        global _config_path_override
        _config_path_override = args.config

    run_kwargs: dict[str, Any] = {"transport": args.transport}
    if args.transport != "stdio":
        run_kwargs["host"] = args.host
        run_kwargs["port"] = args.port
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            logging.warning(
                "Binding to non-loopback address %s without TLS. "
                "Use a reverse proxy (e.g. nginx) to terminate TLS "
                "when exposing the server to a network.",
                args.host,
            )

    mcp.run(**run_kwargs)
