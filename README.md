# mkdocs-mcp

A lightweight MCP server for MkDocs documentation sites. Reads markdown files
directly from disk, provides full-text and optional semantic search, and exposes
project structure through the Model Context Protocol.

## Features

- **5 MCP tools** -- search, read_document, list_documents, get_project_info, get_document_outline
- **SQLite FTS5 keyword search** with BM25 ranking (zero external dependencies)
- **Optional semantic vector search** via sentence-transformers
- **Hybrid search** combining keyword + vector results with Reciprocal Rank Fusion
- **Incremental indexing** -- fast updates when files change
- **Persistent SQLite index** that survives server restarts
- **Navigation-aware** -- parses `mkdocs.yml` and `.nav.yml`
- **Security-first** -- path traversal prevention, read-only search connections
- **Minimal dependencies** -- 3 required, 2 optional

## Installation

```bash
pip install mkdocs-mcp
```

To enable vector search:

```bash
pip install mkdocs-mcp[vector]
```

## Quick Start

Run from the root of any MkDocs project (where `mkdocs.yml` lives):

```bash
cd /path/to/your/mkdocs-project
mkdocs-mcp
```

Or point to a specific config file:

```bash
mkdocs-mcp --config /path/to/mkdocs.yml
```

The server auto-detects `mkdocs.yml` in the current directory when `--config`
is omitted.

### Transport Options

By default the server uses **stdio** transport. You can switch to a network
transport for remote or multi-client setups:

```bash
# Streamable HTTP (recommended for network access)
mkdocs-mcp --transport streamable-http --host 0.0.0.0 --port 9000

# SSE (legacy client compatibility)
mkdocs-mcp --transport sse --port 8080
```

| Flag            | Default       | Description                                      |
|-----------------|---------------|--------------------------------------------------|
| `--transport`   | `stdio`       | `stdio`, `sse`, or `streamable-http`             |
| `--host`        | `127.0.0.1`   | Bind address (network transports only)           |
| `--port`        | `8000`        | Bind port (network transports only)              |

> **Security note:** When binding to a non-loopback address, place the server
> behind a reverse proxy (e.g. nginx, Caddy) that terminates TLS.

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop configuration file:

```json
{
  "mcpServers": {
    "mkdocs": {
      "command": "mkdocs-mcp",
      "args": ["--config", "/path/to/mkdocs.yml"]
    }
  }
}
```

**Note:** If Claude Desktop can't find the command (`Failed to spawn process: No such file or directory`), use the full path to the executable instead of just `mkdocs-mcp`:

```json
{
  "mcpServers": {
    "mkdocs": {
      "command": "/path/to/.venv/bin/mkdocs-mcp",
      "args": ["--config", "/path/to/mkdocs.yml"]
    }
  }
}
```

This is common when the package is installed in a virtual environment whose `bin/` directory isn't in Claude Desktop's PATH.

### Claude Code / VS Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "mkdocs": {
      "command": "mkdocs-mcp",
      "args": ["--config", "/path/to/mkdocs.yml"]
    }
  }
}
```

## Available Tools

### search

Search documentation using keyword, semantic, or hybrid search.

| Parameter     | Type   | Default    | Description                                  |
|---------------|--------|------------|----------------------------------------------|
| `query`       | str    | (required) | The search query string                      |
| `search_type` | str    | `"hybrid"` | `"keyword"`, `"vector"`, or `"hybrid"`       |
| `max_results` | int    | `10`       | Maximum results to return (1--100)           |

Returns ranked results with path, title, relevance score (normalized 0.0--1.0),
and text snippet.

### read_document

Read a documentation file by its relative path.

| Parameter | Type | Default    | Description                                        |
|-----------|------|------------|----------------------------------------------------|
| `path`    | str  | (required) | Relative path from docs dir (e.g. `guide/setup.md`) |

Returns the markdown body (frontmatter stripped), parsed frontmatter as a
separate field, heading structure, and file metadata.

### list_documents

List all documentation files, optionally filtered by section.

| Parameter | Type        | Default | Description                              |
|-----------|-------------|---------|------------------------------------------|
| `section` | str or null | `null`  | Directory prefix to filter by (e.g. `guide`) |

Returns document metadata (path, title, description, categories, size, mtime).

### get_project_info

Get MkDocs project metadata. Takes no parameters.

Returns site name, site URL, docs directory, theme, navigation tree, document
count, and index status.

### get_document_outline

Get the heading structure (table of contents) for a document.

| Parameter | Type | Default    | Description                                        |
|-----------|------|------------|----------------------------------------------------|
| `path`    | str  | (required) | Relative path from docs dir (e.g. `guide/setup.md`) |

Returns the document title and a list of headings with level, text, and anchor.

## Architecture

```
src/mkdocs_mcp/
  config.py      -- MkDocs config detection and nav parsing
  repository.py  -- SQLite schema and CRUD operations
  indexer.py     -- Index orchestration with incremental updates
  searcher.py    -- Keyword, vector, and hybrid search
  server.py      -- FastMCP server with 5 tool definitions
  utils.py       -- Path validation, frontmatter parsing, text extraction
  models.py      -- Pydantic response models
```

At startup the server reads `mkdocs.yml`, scans the docs directory, and
builds (or incrementally updates) a SQLite FTS5 index. Search queries hit the
index directly; vector search embeds the query with `all-MiniLM-L6-v2` and
compares against stored document embeddings. Hybrid mode fuses both result
lists using Reciprocal Rank Fusion.

## Development

```bash
git clone https://github.com/aspect-build/mkdocs-mcp.git
cd mkdocs-mcp
pip install -e ".[dev]"
pytest
```

Linting and type checking:

```bash
ruff check .
mypy src/
```

## Requirements

- Python >= 3.10
- **Required:** fastmcp (>=3.0, <4), pydantic (>=2.0, <3), pyyaml (>=6.0), markdown (>=3.4)
- **Optional (vector search):** sentence-transformers (>=3.0), numpy (>=1.24)

## License

See [LICENSE](LICENSE) for details.
