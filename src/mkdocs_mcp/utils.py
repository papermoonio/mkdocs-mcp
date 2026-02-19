"""Core utility functions: path validation, frontmatter parsing, text extraction."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import markdown
import yaml


# ---------------------------------------------------------------------------
# Path validation (security-critical)
# ---------------------------------------------------------------------------

def validate_doc_path(path: str, docs_dir: Path) -> Path:
    """Validate a relative document path against the docs directory.

    Raises ValueError if:
    - Path is empty
    - Path is absolute (Unix or Windows style)
    - Path contains '..' components
    - Path contains backslashes (Windows traversal)
    - Resolved path escapes docs_dir
    - Path points to a symlink whose target escapes docs_dir
    - File does not exist
    - File is not a .md file

    URL-encoded input is decoded first (prevents %2e%2e%2f bypasses).
    """
    if not path:
        raise ValueError("Path must not be empty")

    # Decode URL-encoding first, then validate
    decoded = unquote(path)

    # Reject absolute paths (Unix and Windows)
    if decoded.startswith('/') or decoded.startswith('\\'):
        raise ValueError("Absolute paths are not allowed")
    if len(decoded) >= 2 and decoded[1] == ':':
        raise ValueError("Absolute paths are not allowed")

    # Reject backslashes (Windows-style traversal)
    if '\\' in decoded:
        raise ValueError("Backslashes are not allowed in paths")

    # Reject '..' components before resolution (defense-in-depth)
    parts = decoded.split('/')
    if '..' in parts:
        raise ValueError("Path traversal ('..') is not allowed")

    # Resolve docs_dir FIRST to a canonical path
    docs_resolved = docs_dir.resolve()

    # Build the unresolved path (for symlink detection)
    unresolved = docs_resolved / decoded

    # Check symlink BEFORE resolving — resolve() follows symlinks,
    # making is_symlink() always False on the resolved result
    if unresolved.is_symlink():
        target = unresolved.resolve()
        if not target.is_relative_to(docs_resolved):
            raise ValueError("Symlink target escapes the documentation directory")

    # Now resolve for containment check
    full_path = unresolved.resolve()
    if not full_path.is_relative_to(docs_resolved):
        raise ValueError("Path escapes the documentation directory")

    # Must exist
    if not full_path.exists():
        raise ValueError("File not found")

    # Must be .md
    if full_path.suffix.lower() != '.md':
        raise ValueError("Only .md files can be accessed")

    return full_path


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Rules:
    - Only treats '---' on the very first line (after optional BOM) as frontmatter start
    - Returns ({}, content) if no frontmatter found
    - Returns ({}, body) if YAML parses to non-dict (bare string, list, etc.)
    - Handles frontmatter-only files (no body) gracefully
    """
    # Strip BOM if present
    text = content.lstrip('\ufeff')

    # Must start with '---' on the first line
    if not text.startswith('---'):
        return {}, content

    # Find the closing '---'
    # Look for '---' on its own line after the opening
    end_match = re.search(r'\n---\s*\n', text[3:])
    if end_match is None:
        # Check for '---' at end of file (frontmatter-only)
        end_match = re.search(r'\n---\s*$', text[3:])
        if end_match is None:
            return {}, content
        yaml_text = text[3:3 + end_match.start()]
        body = ''
    else:
        yaml_text = text[3:3 + end_match.start()]
        body = text[3 + end_match.end():]

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}, content

    # Only accept dict frontmatter
    if not isinstance(parsed, dict):
        return {}, body if body else content

    return parsed, body


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------

def extract_headings(content: str) -> list[dict[str, Any]]:
    """Extract headings from markdown content.

    Returns list of {level: int, text: str, anchor: str}.
    Ignores headings inside fenced code blocks (``` or ~~~).
    """
    headings = []
    in_code_block = False

    for line in content.split('\n'):
        stripped = line.strip()

        # Track fenced code blocks
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Match ATX headings: # through ######
        match = re.match(r'^(#{1,6})\s+(.+?)(?:\s+#+\s*)?$', line)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            headings.append({
                'level': level,
                'text': text,
                'anchor': slugify(text),
            })

    return headings


# ---------------------------------------------------------------------------
# Slugify (Material-like anchors)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert heading text to Material-like URL-safe anchor slug.

    Produces Material-like anchors: lowercase, hyphens for spaces,
    strip most punctuation. Does NOT guarantee exact parity with every
    Material version — tested against an expected-output table.
    """
    # Lowercase
    slug = text.lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r'[\s_]+', '-', slug)
    # Remove non-alphanumeric chars except hyphens (Unicode-aware: \w matches
    # letters/digits/underscore in any script, so CJK/accented chars are kept)
    slug = re.sub(r'[^\w-]', '', slug)
    # Collapse consecutive hyphens
    slug = re.sub(r'-+', '-', slug)
    # Strip leading/trailing hyphens
    slug = slug.strip('-')
    return slug


# ---------------------------------------------------------------------------
# Markdown to plain text
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Strip HTML tags, preserving text with spacing between blocks."""

    BLOCK_TAGS = frozenset([
        'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'tr', 'br', 'hr', 'pre', 'blockquote', 'table',
    ])

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append('\n')

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = ''.join(self._parts)
        # Collapse multiple newlines but preserve paragraph breaks
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def markdown_to_text(content: str) -> str:
    """Convert markdown to plain text for FTS indexing.

    - Strips frontmatter first
    - Uses python-markdown to render HTML, then strips tags
    - Preserves spacing between block-level elements (no smashed text)
    - Includes code block content (searchable)
    """
    # Remove frontmatter
    _, body = parse_frontmatter(content)

    # Convert markdown to HTML
    md = markdown.Markdown(extensions=['fenced_code', 'tables'])
    html = md.convert(body)

    # Strip HTML tags, preserving block spacing
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# FTS5 query safety
# ---------------------------------------------------------------------------

_FTS_KEYWORDS = frozenset({'AND', 'OR', 'NOT', 'NEAR'})
_MAX_QUERY_LEN = 500
_MAX_QUERY_TOKENS = 20

def sanitize_fts_query(query: str) -> str:
    """Build a safe FTS5 query from raw user input.

    Default safe mode:
    - Truncates input to 500 chars (prevents DoS via huge queries)
    - Strips FTS5 syntax chars: " ( ) * ^
    - Tokenizes on whitespace
    - Removes FTS5 keywords (AND, OR, NOT, NEAR)
    - Caps at 20 tokens (bounds output size)
    - Joins as "term1" OR "term2" OR ...
    - Returns empty string for empty / all-punctuation input
    """
    # Length guard
    truncated = query[:_MAX_QUERY_LEN]
    # Strip null bytes (FTS5 rejects them)
    truncated = truncated.replace('\x00', '')
    # Strip FTS5 operators and syntax chars
    cleaned = re.sub(r'["\(\)\*\^]', ' ', truncated)
    # Tokenize
    tokens = cleaned.split()
    # Remove FTS5 keywords and empty tokens
    tokens = [t for t in tokens if t.upper() not in _FTS_KEYWORDS and t.strip()]
    # Cap token count
    tokens = tokens[:_MAX_QUERY_TOKENS]
    if not tokens:
        return ''
    # Build safe OR query with quoted terms
    return ' OR '.join(f'"{t}"' for t in tokens)



# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------

def content_hash(content: str) -> str:
    """Compute SHA-256 hash of content for incremental indexing."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
