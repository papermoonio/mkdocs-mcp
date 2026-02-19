# Security Audit Report: indexer.py and utils.py

**Date:** 2026-02-19
**Auditor:** security-auditor agent
**Scope:** `/workspace/mkdocs-mcp/src/mkdocs_mcp/indexer.py` and
           `/workspace/mkdocs-mcp/src/mkdocs_mcp/utils.py`
**Methodology:** Static code review — read-only, no code was modified.

---

## Executive Summary

The indexer is well-structured and shows clear security awareness: all SQL
parameters are passed through the DB-API placeholder mechanism, a dedicated
`validate_doc_path` function guards external path inputs, and symlink escapes
are checked in both the scan path and the validation utility.  No critical
SQL-injection or path-traversal vulnerabilities were found.

Six exploitable issues were identified: one HIGH (unbounded file reads
enabling denial of service), one MEDIUM (TOCTOU window on mtime checks),
one MEDIUM (unconstrained embedding BLOB), one LOW (`executescript` commit
side-effects), one LOW (`fts_query_raw` trust-boundary gap), and two INFO
observations.

| ID | Severity | Title |
|----|----------|-------|
| SEC-01 | HIGH | No file-size limit before `read_text()` |
| SEC-02 | MEDIUM | TOCTOU between `stat()` and `read_text()` in `update_index` |
| SEC-03 | MEDIUM | Embedding BLOB is written without size validation |
| SEC-04 | LOW | `executescript()` implicitly commits open transactions |
| SEC-05 | LOW | `fts_query_raw` trust boundary not enforced in module |
| SEC-06 | INFO | `docs_dir` parent chosen for DB placement without validation |
| SEC-07 | INFO | `heading_text` concat is unbounded |

---

## Findings

---

### SEC-01 — HIGH: No file-size limit before `read_text()`

**File:** `indexer.py`
**Lines:** 170, 243

**Description**

`_index_document` and the mtime-change branch of `update_index` both call
`file_path.read_text(encoding="utf-8")` with no prior size check.  A file
that is gigabytes in size (or a named pipe / special file whose read never
terminates on some kernels) will be read entirely into memory.  If an
attacker can write a large file into `docs_dir` — or if `docs_dir` is on a
network mount they influence — they can exhaust process memory and crash the
MCP server process.

The `_scan_documents` function does filter symlink escapes but does not
limit the size of legitimate `.md` files already inside `docs_dir`.

**Affected code**

```python
# indexer.py line 170 (update_index)
file_content = abs_path.read_text(encoding="utf-8")

# indexer.py line 243 (_index_document)
file_content = file_path.read_text(encoding="utf-8")
```

**Exploitability**

Requires write access to `docs_dir` or influence over a network-mounted
docs directory.  This is realistic for shared CI environments or
documentation-as-code repositories where contributors can push arbitrary
files.  Impact is process crash (denial of service); no confidentiality
or integrity breach.

**Recommendation**

Check `stat().st_size` before reading and reject files above a configurable
threshold (suggested default: 10 MB).  Example:

```python
MAX_DOC_BYTES = 10 * 1024 * 1024  # 10 MB

stat = file_path.stat()
if stat.st_size > MAX_DOC_BYTES:
    logger.warning("Skipping oversized file %s (%d bytes)", file_path, stat.st_size)
    return False
file_content = file_path.read_text(encoding="utf-8")
```

Apply the same guard in the `update_index` mtime-change branch (line 170).
Note: `stat()` is already called later in `_index_document` (line 244) for
`mtime`; the guard can reuse the same stat object, adding zero overhead.

---

### SEC-02 — MEDIUM: TOCTOU between `stat()` and `read_text()` in `update_index`

**File:** `indexer.py`
**Lines:** 162–170

**Description**

`update_index` calls `abs_path.stat().st_mtime` (line 162) and then, in the
mtime-changed branch, calls `abs_path.read_text()` (line 170).  There is no
atomic operation guaranteeing the file has not been replaced or truncated
between those two system calls.

A race condition (time-of-check to time-of-use) means:

1. Stat returns mtime T1 — file contains safe content A.
2. Attacker replaces file with content B.
3. `read_text()` reads content B.
4. Content B is indexed; `stored_mtime` is updated to T1.
5. On the next cycle, mtime matches T1 (attacker can set mtime back via
   `touch -t`), so the hash is not re-checked and content B stays indexed.

This is a niche attack path and requires race-condition timing, but it is
worth acknowledging in a server that may be run with elevated file-system
privileges.

**Affected code**

```python
# indexer.py lines 162-170
current_mtime = abs_path.stat().st_mtime
if current_mtime == stored_mtime:
    skipped += 1
else:
    try:
        file_content = abs_path.read_text(encoding="utf-8")
    ...
    current_hash = content_hash(file_content)
```

**Recommendation**

After reading, re-stat the file and verify the mtime has not changed between
the two calls.  If it has changed, discard the read and schedule a retry, or
simply re-index on the next cycle.  Alternatively, rely solely on
`content_hash` for integrity (the mtime check is already an optimisation, not
a security gate) and document that the mtime fast-path is a performance
optimisation only.

---

### SEC-03 — MEDIUM: Embedding BLOB written without size validation

**File:** `indexer.py`
**Lines:** 291–301

**Description**

The embedding BLOB is stored with no size upper bound:

```python
embedding = embedder.encode(plain_text[:8192])  # Cap input length
self._conn.execute(
    """INSERT OR REPLACE INTO doc_embeddings (path, embedding, model_name)
       VALUES (?, ?, ?)""",
    (rel_path, embedding.tobytes(), ...),
)
```

The `plain_text[:8192]` cap limits the *input* to the embedder, not the
*output*.  A malicious or misconfigured embedder could return an arbitrarily
large numpy array.  Calling `.tobytes()` on a 1 GB array and writing it to
SQLite would consume disk space equal to the BLOB size.

With many documents, a high-dimensional or buggy model could silently grow
the database by hundreds of megabytes per document.

**Recommendation**

Validate `embedding.shape` or `len(embedding.tobytes())` before storing.
Typical sentence-transformers produce 384–1536 float32 dimensions.  A
reasonable guard:

```python
MAX_EMBEDDING_BYTES = 32 * 1024  # 32 KB covers 8192-dim float32 with margin

blob = embedding.tobytes()
if len(blob) > MAX_EMBEDDING_BYTES:
    logger.warning(
        "Embedding for %s is unexpectedly large (%d bytes); skipping.",
        rel_path, len(blob),
    )
else:
    self._conn.execute(...)
```

---

### SEC-04 — LOW: `executescript()` implicitly commits open transactions

**File:** `indexer.py`
**Lines:** 48, 88

**Description**

`sqlite3.Connection.executescript()` always issues an implicit `COMMIT`
before executing its statements.  This is documented Python behaviour.  The
two call sites are:

1. `_init_db` (line 48): `CREATE TABLE IF NOT EXISTS ...` — harmless at
   startup because no transaction is open.
2. `build_index` (line 88): `DELETE FROM doc_metadata; DELETE FROM docs_fts;
   DELETE FROM doc_embeddings;` — executed after a fresh connection, so
   the implicit commit is a no-op in practice.

However, if future code opens a transaction before calling either method,
the implicit commit will silently flush a partially-built state.  This is
a latent bug rather than a currently-exploitable issue.

**Recommendation**

Replace `executescript()` with individual `execute()` calls inside an
explicit transaction, or add a comment noting the implicit-commit behaviour
so future developers are warned.

For `build_index` specifically:

```python
# Preferred: explicit transaction, no implicit commit surprise
with self._conn:
    self._conn.execute("DELETE FROM doc_metadata")
    self._conn.execute("DELETE FROM docs_fts")
    self._conn.execute("DELETE FROM doc_embeddings")
```

---

### SEC-05 — LOW: `fts_query_raw` trust boundary not enforced at module level

**File:** `utils.py`
**Lines:** 287–296

**Description**

`fts_query_raw` is documented as "only used when caller explicitly opts in
via trusted internal code" and "Must NOT be exposed directly to MCP tool
parameters."  However, this is a documentation contract only.  Nothing in
the module prevents another developer from importing and calling it with
user-controlled input.  If a future MCP tool incorrectly calls
`fts_query_raw(user_query)` instead of `sanitize_fts_query(user_query)`,
the user gains full FTS5 operator access.

FTS5 operator injection is not SQL injection (parameterised binding prevents
that), but it does allow:
- `NEAR(word1 word2, 5)` — legitimate
- Malformed queries that trigger SQLite FTS5 parse errors
- Column filters like `path:secret` that may leak path data visible in
  result rows

**Recommendation**

Add a module-level note or rename the function to make the danger more
prominent (e.g., `fts_query_raw_UNSAFE` or `_fts_query_raw_internal`).
Consider adding a keyword-only `_trusted_caller` sentinel parameter that
callers must pass explicitly, making accidental usage harder.  A lint rule
(e.g., a `# noqa` allowlist) would also work.

---

### SEC-06 — INFO: DB file placed in `docs_dir` parent without validation

**File:** `indexer.py`
**Lines:** 40

**Description**

```python
self.db_path = db_path or (self.docs_dir.parent / ".mkdocs-mcp.db")
```

If `docs_dir` is the filesystem root (`/`) — an unlikely but possible
misconfiguration — `docs_dir.parent` is also `/` and the database is written
to `/.mkdocs-mcp.db`, which may fail with a permission error or, on a
writable root filesystem, write to an unexpected location.

If `docs_dir` is a path like `/var/www/html/docs`, the database lands at
`/var/www/html/.mkdocs-mcp.db` which is within the web root and potentially
web-accessible.

**Recommendation**

Document the database placement clearly.  Consider defaulting to a
user-specific cache directory (`platformdirs.user_cache_dir()` or
`$XDG_CACHE_HOME`) or within `docs_dir` itself (which is already under
access control).  At minimum, validate that the parent directory exists and
is writable before proceeding.

---

### SEC-07 — INFO: `heading_text` join is unbounded

**File:** `indexer.py`
**Lines:** 253

**Description**

```python
heading_text = " ".join(h["text"] for h in headings)
```

A document with thousands of headings (e.g., an auto-generated API reference
with one heading per symbol) creates a very large `heading_text` string that
is stored in the FTS5 index.  This is not a direct security issue, but it
amplifies the impact of SEC-01: even a moderately-sized file could have
disproportionate FTS index growth.

**Recommendation**

Cap the total length of `heading_text` before inserting:

```python
heading_text = " ".join(h["text"] for h in headings)[:4096]
```

Or cap the number of headings processed:

```python
heading_text = " ".join(h["text"] for h in headings[:200])
```

---

## Not-Vulnerable: Areas Reviewed and Cleared

### SQL Injection

All SQL in `indexer.py` uses DB-API `?` placeholders.  No string
concatenation or f-string interpolation was found in any `execute()`,
`executemany()`, or related call.  The `_init_db` and `build_index`
`executescript()` calls contain only hard-coded DDL/DML with no user data.
**No SQL injection vectors found.**

### Path Traversal — External Requests (`validate_doc_path`)

`utils.validate_doc_path` is thorough:
- URL-decodes input before validation (prevents `%2e%2e%2f` bypass).
- Rejects absolute paths (Unix `/`, Windows `\` and `C:`).
- Rejects `..` at the string-split level before resolution.
- Resolves the path and checks containment with `is_relative_to()`.
- Checks symlinks *before* `resolve()` (correct order — `resolve()`
  follows symlinks, making `is_symlink()` always False on the result).

**No path traversal in the external validation path.**

### Path Traversal — Internal Scanner (`_scan_documents`)

`_scan_documents` uses `rglob("*.md")` anchored to an already-resolved
`docs_dir`.  It:
- Skips hidden files/directories (`.`-prefixed parts).
- For symlinks, resolves the target and checks `is_relative_to()`.
- For non-symlinks, still resolves and checks containment.

This dual-check (symlink-specific + general resolve check) correctly handles
both symlinks and non-symlink paths.  **No path traversal in the scanner.**

### Frontmatter / YAML Injection

`parse_frontmatter` uses `yaml.safe_load()`, which does not execute Python
objects or arbitrary constructors.  Return value is type-checked to be a
`dict` before use.  **No YAML injection.**

### FTS5 Injection from Search Queries

The `sanitize_fts_query` function strips FTS5 syntax characters, removes
keywords, caps token count, and wraps each token in double-quotes.  This
prevents FTS5 operator injection for the normal code path.  **No FTS5
injection via `sanitize_fts_query`.**  (See SEC-05 for the raw variant.)

### Embedding BLOB Integrity

Embeddings are stored as a raw byte BLOB via a parameterised `INSERT OR
REPLACE`.  There is no way for a document's content to inject SQL through
the embedding pathway.  The only concern is size (SEC-03).

---

## Risk Summary

| ID | Severity | CVSS-like Impact | Effort to Exploit |
|----|----------|-----------------|-------------------|
| SEC-01 | HIGH | Process crash (DoS) | Low — write a large `.md` file |
| SEC-02 | MEDIUM | Stale/malicious content indexed | High — requires race timing |
| SEC-03 | MEDIUM | Disk exhaustion | Low — requires rogue embedder |
| SEC-04 | LOW | Partial state committed silently | Future code change required |
| SEC-05 | LOW | FTS operator injection (future) | Low risk today |
| SEC-06 | INFO | DB in web root | Depends on deployment |
| SEC-07 | INFO | Index bloat | Requires unusual docs |

---

## Remediation Roadmap

**Immediate (before next release):**
- SEC-01: Add `stat().st_size` guard before every `read_text()` call.
- SEC-03: Validate embedding byte length before `INSERT`.

**Short-term (next sprint):**
- SEC-04: Replace `executescript()` in `build_index` with individual
  `execute()` calls inside an explicit `with self._conn:` block.
- SEC-07: Cap `heading_text` length.

**Backlog:**
- SEC-02: Document TOCTOU limitation; consider re-stat after read.
- SEC-05: Rename or annotate `fts_query_raw` to make trust boundary explicit.
- SEC-06: Document or reconsider default DB placement; add web-root warning.
