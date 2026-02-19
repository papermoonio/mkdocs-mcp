# Pytest Expert Memory

## Project: mkdocs-mcp

### Key Findings
- **Null bytes bug**: `sanitize_fts_query()` in `src/mkdocs_mcp/utils.py` does not strip null bytes (`\x00`), which causes `sqlite3.OperationalError: unterminated string` when passed through to FTS5 MATCH. Documented as xfail in `tests/test_searcher.py`.

### Project Structure
- Source: `src/mkdocs_mcp/`
- Tests: `tests/`
- Test pattern: `sys.path.insert(0, str(Path(__file__).parent.parent / "src"))` at module top
- Models use Pydantic (`pydantic.BaseModel`)
- No virtual env; packages installed system-wide with `--break-system-packages`
- numpy must be installed separately (not in default deps)

### Test Conventions
- Use `pytest.fixture` with `yield` for cleanup
- Use `DocIndexer` context manager to build test indexes
- Group tests in classes by feature area (e.g., `TestKeywordSearch`, `TestVectorSearch`)
- Each test has a docstring explaining what it verifies
- Use `pytest.mark.parametrize` for data-driven tests

### FastMCP Tool Testing Pattern
- `@mcp.tool` decorator wraps functions into `FunctionTool` objects (not callable)
- Access the underlying function via `.fn` attribute: `search.fn`, `read_document.fn`
- Mock `Context` with `MagicMock()` and `ctx.get_state.side_effect = lambda key: state.get(key)`
- State dict keys: `"config"`, `"indexer"`, `"searcher"`
- Tools return plain dicts (`.model_dump()` from Pydantic models)

### Test Files
- `tests/test_server.py` - MCP tool integration tests (38 tests, 513 lines)
- `tests/test_security.py` - Security-focused tests (51 tests, 473 lines)
- `tests/test_searcher.py` - DocSearcher tests
- `tests/test_indexer.py` - DocIndexer tests
- `tests/test_utils.py` - Utility function tests
- `tests/test_config.py` - MkDocsConfig tests
- `tests/test_integration.py` - Real-world integration tests against polkadot-mkdocs (24 tests, 299 lines)

### Polkadot-mkdocs Integration Notes
- Site name: "Polkadot Developer Docs" (NOT "Polkadot Wiki")
- docs_dir: `polkadot-docs/`, ~150 markdown files
- Uses awesome-nav plugin with `.nav.yml` files
- Uses `!!python/name:` and `!ENV` YAML tags (handled by `_SafeMkDocsLoader`)
- Good sections for filter tests: `node-infrastructure` (~17 docs), `parachains`, `smart-contracts`
- Use `scope="module"` fixtures to avoid re-indexing 150 docs per test
