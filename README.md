# papermoon-mkdocs-mcp

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
- **Excludable documents** -- keep drafts and internal pages off the MCP surface
- **Security-first** -- path traversal prevention, read-only search connections
- **Minimal dependencies** -- 3 required, 2 optional

## Installation

```bash
pip install papermoon-mkdocs-mcp
```

To enable vector search:

```bash
pip install papermoon-mkdocs-mcp[vector]
```

## Quick Start

Run from the root of any MkDocs project (where `mkdocs.yml` lives):

```bash
cd /path/to/your/mkdocs-project
papermoon-mkdocs-mcp
```

Or point to a specific config file:

```bash
papermoon-mkdocs-mcp --config /path/to/mkdocs.yml
```

The server auto-detects `mkdocs.yml` in the current directory when `--config`
is omitted.

### Transport Options

By default the server uses **stdio** transport. You can switch to a network
transport for remote or multi-client setups:

```bash
# Streamable HTTP (recommended for network access)
papermoon-mkdocs-mcp --transport streamable-http --host 0.0.0.0 --port 9000

# SSE (legacy client compatibility)
papermoon-mkdocs-mcp --transport sse --port 8080
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
      "command": "papermoon-mkdocs-mcp",
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
      "command": "papermoon-mkdocs-mcp",
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

## Excluding Documents

Some markdown files are not worth exposing over MCP -- drafts, internal
runbooks, generated scratch files. Add an `mcp_exclude` list under `extra` in
`mkdocs.yml`:

```yaml
site_name: My Docs

extra:
  mcp_exclude:
    - drafts/             # any directory named 'drafts', at any depth
    - internal/**         # anchored: only 'internal/' at the docs root
    - "*-scratch.md"      # by filename suffix, at any depth
    - "!internal/public.md"  # re-include one file from a broader rule
```

It lives under `extra` because this project isn't a registered MkDocs
plugin, so MkDocs' own config schema has no way to know about
`mcp_exclude` -- a bare top-level key trips `Unrecognised configuration
name` warnings, which `mkdocs --strict` turns into a hard failure. `extra`
is the one top-level key MkDocs leaves open for arbitrary data, so nesting
there passes validation. (A bare top-level `mcp_exclude` still works for
now as a deprecated fallback, with a warning logged.)

Exclusions apply everywhere at once. An excluded document is absent from the
navigation tree, never enters the search index, does not appear in
`list_documents`, and is refused by `read_document` and `get_document_outline`
-- the refusal is identical to the response for a file that does not exist, so
it does not reveal that the document is there.

`mcp_exclude` affects only this MCP server. It does not change what `mkdocs
build` publishes.

### Pattern syntax

Patterns are gitignore-style and match against a document's path relative to
`docs_dir`.

| Pattern            | Matches                                                         |
|--------------------|-----------------------------------------------------------------|
| `drafts/`          | Any directory named `drafts` and everything under it            |
| `/drafts/`         | Only `drafts/` at the docs root                                 |
| `internal/**`      | Everything under a root-level `internal/`                       |
| `*.tmp.md`         | Files ending `.tmp.md`, at any depth                            |
| `guide/*.md`       | `.md` files directly in `guide/` (not in subdirectories)        |
| `guide/**/*.md`    | `.md` files anywhere under `guide/`                             |
| `draft?.md`        | `draft1.md`, `draftx.md` -- `?` is a single character           |
| `draft[0-9].md`    | A character class                                               |
| `!keep/this.md`    | Re-includes a path an earlier pattern excluded                  |

- A pattern containing `/` is anchored at `docs_dir`; one without it matches at
  any depth.
- A trailing `/` restricts a pattern to directories, so `drafts/` does not hide
  a file named `drafts.md`.
- Rules are evaluated in order and the **last** one to match decides, so put
  `!` re-inclusions after the rule they carve out of.
- Blank lines and `#` comments are ignored.

Newly excluded files are dropped from the index on the next run, and removing a
pattern brings them back -- no need to delete `.mkdocs-mcp.db`.

## Architecture

```
src/mkdocs_mcp/
  config.py      -- MkDocs config detection and nav parsing
  exclusions.py  -- mcp_exclude pattern matching
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
