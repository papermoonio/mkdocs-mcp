"""Path-exclusion rules for hiding documents from the MCP surface.

Some markdown files in a docs tree are not worth exposing over MCP —
drafts, internal runbooks, generated scratch files. ``mkdocs.yml`` may
carry an ``mcp_exclude`` list of gitignore-style patterns, nested under
``extra`` so MkDocs' own config schema (and ``--strict`` mode) doesn't
flag it as an unrecognised key:

.. code-block:: yaml

    extra:
      mcp_exclude:
        - drafts/
        - internal/**
        - "*-scratch.md"
        - "!internal/public.md"

The rules are applied at every point where a document could otherwise
become visible — the search index, the navigation tree, and direct reads —
so an excluded file is absent from the MCP surface rather than merely
hidden from listings.

Pattern syntax
--------------
Patterns are matched against a document's POSIX-style path relative to
``docs_dir`` (e.g. ``guide/intro.md``).

- ``*`` matches any run of characters within a single path segment.
- ``**`` matches across segments; ``**/`` also matches zero directories.
- ``?`` matches a single character other than ``/``.
- ``[abc]`` / ``[!abc]`` match a character class.
- A pattern containing a ``/`` (other than a trailing one) is anchored at
  ``docs_dir``. A pattern without one matches at any depth, so ``drafts/``
  hides every directory named ``drafts`` wherever it appears.
- A trailing ``/`` restricts the pattern to directories: the directory's
  contents are excluded, but a *file* of that name is not.
- Matching a directory excludes everything beneath it.
- A leading ``!`` negates. Rules are evaluated in order and the last one
  to match decides, so a negation can re-include part of a broader
  exclusion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


def _translate(pattern: str) -> str:
    """Translate one glob pattern body into a regular-expression fragment.

    Unlike :func:`fnmatch.translate`, ``*`` does not cross path separators
    and ``**`` does.
    """
    out: list[str] = []
    i, n = 0, len(pattern)

    while i < n:
        char = pattern[i]

        if char == "*":
            star_end = i
            while star_end < n and pattern[star_end] == "*":
                star_end += 1
            is_double = (star_end - i) >= 2

            if is_double and star_end < n and pattern[star_end] == "/":
                # '**/' matches zero or more leading directories
                out.append("(?:.*/)?")
                i = star_end + 1
                continue
            out.append(".*" if is_double else "[^/]*")
            i = star_end
            continue

        if char == "?":
            out.append("[^/]")
            i += 1
            continue

        if char == "[":
            close = i + 1
            if close < n and pattern[close] in "!^":
                close += 1
            if close < n and pattern[close] == "]":
                close += 1
            while close < n and pattern[close] != "]":
                close += 1
            if close >= n:
                # Unterminated class — treat the '[' as a literal
                out.append(re.escape(char))
                i += 1
                continue
            body = pattern[i + 1 : close].replace("\\", "\\\\")
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = close + 1
            continue

        out.append(re.escape(char))
        i += 1

    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    """One compiled pattern: how it matches files, directories, and its sense."""

    pattern: str
    negated: bool
    file_re: re.Pattern[str]
    dir_re: re.Pattern[str]

    @classmethod
    def compile(cls, raw: str) -> _Rule | None:
        """Compile a single pattern. Returns None for blank/comment lines."""
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            return None

        negated = pattern.startswith("!")
        if negated:
            pattern = pattern[1:].strip()
            if not pattern:
                return None

        dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if not pattern:
            return None

        # A leading '/' anchors without itself being a separator
        rooted = pattern.startswith("/")
        if rooted:
            pattern = pattern.lstrip("/")
            if not pattern:
                return None

        # Anchored patterns match from docs_dir; bare names match at any depth.
        anchored = rooted or "/" in pattern
        prefix = "^" if anchored else "(?:^|.*/)"
        base = prefix + _translate(pattern)

        # A directory-only rule cannot match a file of the same name, but
        # still excludes everything underneath it.
        file_pattern = f"{base}/.*$" if dir_only else f"{base}(?:/.*)?$"
        dir_pattern = f"{base}(?:/.*)?$"

        return cls(
            pattern=raw.strip(),
            negated=negated,
            file_re=re.compile(file_pattern),
            dir_re=re.compile(dir_pattern),
        )


class ExclusionRules:
    """An ordered set of exclusion patterns, evaluated last-match-wins.

    An instance built from no patterns is falsy and excludes nothing, so
    callers can hold one unconditionally instead of threading ``None``.
    """

    __slots__ = ("_rules", "patterns")

    def __init__(self, patterns: Sequence[str] | None = None) -> None:
        self.patterns: tuple[str, ...] = tuple(patterns or ())
        rules = (_Rule.compile(p) for p in self.patterns)
        self._rules: tuple[_Rule, ...] = tuple(r for r in rules if r is not None)

    @classmethod
    def from_config(cls, raw: Any) -> ExclusionRules:
        """Build rules from a raw ``mcp_exclude`` value.

        Accepts a list of strings or a single newline-separated string
        (the block-scalar form). Any other shape yields empty rules rather
        than raising — a malformed exclude list must not take down config
        parsing, and silently exposing documents is the visible failure.
        """
        if raw is None:
            return cls()
        if isinstance(raw, str):
            return cls(raw.splitlines())
        if isinstance(raw, Iterable):
            return cls([str(item) for item in raw if isinstance(item, (str, int, float))])
        return cls()

    @staticmethod
    def _normalise(rel_path: str | Path) -> str:
        """Reduce a relative path to the POSIX form the patterns match against."""
        if isinstance(rel_path, Path):
            text = rel_path.as_posix()
        else:
            text = str(rel_path).replace("\\", "/")
        # './guide/x.md' and 'guide//x.md' must match the same rules as 'guide/x.md'
        return PurePosixPath(text).as_posix().lstrip("/")

    def _matches(self, rel_path: str | Path, *, is_dir: bool) -> bool:
        text = self._normalise(rel_path)
        if not text or text == ".":
            return False

        excluded = False
        for rule in self._rules:
            regex = rule.dir_re if is_dir else rule.file_re
            if regex.match(text):
                excluded = not rule.negated
        return excluded

    def is_excluded(self, rel_path: str | Path) -> bool:
        """True if the document at *rel_path* must not be exposed."""
        return self._matches(rel_path, is_dir=False)

    def is_dir_excluded(self, rel_dir: str | Path) -> bool:
        """True if *rel_dir* and everything beneath it can be skipped.

        Only safe to use for pruning when no negation could re-include a
        descendant, so this returns False whenever any negated rule exists.
        """
        if any(rule.negated for rule in self._rules):
            return False
        return self._matches(rel_dir, is_dir=True)

    def __bool__(self) -> bool:
        return bool(self._rules)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ExclusionRules({list(self.patterns)!r})"
