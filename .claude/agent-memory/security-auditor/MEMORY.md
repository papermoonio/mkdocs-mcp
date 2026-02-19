# Security Auditor Memory — mkdocs-mcp

## Codebase security posture (as of 2026-02-19)

Strong baseline: parameterized SQL throughout, `validate_doc_path` is thorough
(URL-decode before check, symlink check before resolve, `is_relative_to` for
containment), `yaml.safe_load` used everywhere, `sanitize_fts_query` wraps
tokens in double-quotes.

## Known open findings (track remediation)

| ID | Severity | Issue | File | Lines |
|----|----------|-------|------|-------|
| SEC-01 | HIGH | No file-size limit before read_text() | indexer.py | 170, 243 |
| SEC-02 | MEDIUM | TOCTOU stat() then read_text() | indexer.py | 162-170 |
| SEC-03 | MEDIUM | No embedding BLOB size limit | indexer.py | 291-301 |
| SEC-04 | LOW | executescript() implicit commit | indexer.py | 48, 88 |
| SEC-05 | LOW | fts_query_raw trust boundary docs only | utils.py | 287-296 |
| SEC-06 | INFO | DB placed in docs_dir parent (web root risk) | indexer.py | 40 |
| SEC-07 | INFO | heading_text join unbounded | indexer.py | 253 |

## Key patterns to re-check on future changes

- Any new call to read_text() or open(): must check st_size first (SEC-01 pattern)
- Any new embedder path: must validate blob byte length (SEC-03 pattern)
- Any new executescript(): implicit commit hazard (SEC-04 pattern)
- Any new SQL: must use ? placeholders, never string concat
- Any new path entry point from MCP tools: must call validate_doc_path

## Full audit report

`docs/security/audit-indexer-2026-02-19.md`
