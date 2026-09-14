"""Tests for mcp_exclude — pattern semantics and end-to-end enforcement.

The feature is only useful if every surface agrees, so these tests check
the pattern matcher in isolation and then assert that an excluded document
is absent from the nav tree, the search index, document listings, and
direct reads alike.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import _make_ctx
from mkdocs_mcp.config import MkDocsConfig
from mkdocs_mcp.exclusions import ExclusionRules
from mkdocs_mcp.indexer import DocIndexer, scan_documents
from mkdocs_mcp.searcher import DocSearcher
from mkdocs_mcp.server import (
    get_document_outline,
    list_documents,
    read_document,
    search,
)


# ---------------------------------------------------------------------------
# Pattern semantics
# ---------------------------------------------------------------------------


class TestPatternSemantics:
    """Unit tests for ExclusionRules matching."""

    @pytest.mark.parametrize(
        ("patterns", "path", "expected"),
        [
            # Bare directory name matches at any depth
            (["drafts/"], "drafts/a.md", True),
            (["drafts/"], "guide/drafts/a.md", True),
            (["drafts/"], "drafts/deep/b.md", True),
            # A trailing slash means directories only — not a like-named file
            (["drafts/"], "drafts.md", False),
            (["drafts/"], "guide/drafts.md", False),
            # Anchored patterns only match from docs_dir
            (["/internal/"], "internal/a.md", True),
            (["/internal/"], "guide/internal/a.md", False),
            (["internal/**"], "internal/a.md", True),
            (["internal/**"], "internal/deep/a.md", True),
            (["internal/**"], "guide/internal/a.md", False),
            # '*' stays within one segment, '**' crosses
            (["*.md"], "a.md", True),
            (["*.md"], "guide/a.md", True),
            (["guide/*.md"], "guide/a.md", True),
            (["guide/*.md"], "guide/deep/a.md", False),
            (["guide/**/*.md"], "guide/deep/a.md", True),
            (["guide/**/*.md"], "guide/a.md", True),
            # Suffix globs
            (["*-scratch.md"], "notes-scratch.md", True),
            (["*-scratch.md"], "guide/notes-scratch.md", True),
            (["*-scratch.md"], "guide/notes.md", False),
            # Single-character and class matching
            (["draft?.md"], "draft1.md", True),
            (["draft?.md"], "draft12.md", False),
            (["draft[0-9].md"], "draft7.md", True),
            (["draft[!0-9].md"], "draft7.md", False),
            (["draft[!0-9].md"], "draftx.md", True),
            # Naming a directory excludes everything beneath it, but the
            # name must match a whole segment — not act as a prefix.
            (["internal"], "internal/a.md", True),
            (["internal"], "internal.md", False),
            (["internal"], "internal-notes.md", False),
            # No patterns at all
            ([], "anything.md", False),
        ],
    )
    def test_matching(self, patterns: list[str], path: str, expected: bool) -> None:
        assert ExclusionRules(patterns).is_excluded(path) is expected

    def test_negation_reincludes(self) -> None:
        """A later '!' rule wins over an earlier exclusion."""
        rules = ExclusionRules(["internal/**", "!internal/public.md"])

        assert rules.is_excluded("internal/secret.md") is True
        assert rules.is_excluded("internal/public.md") is False

    def test_last_match_wins_in_order(self) -> None:
        """Rule order decides: re-excluding after a negation sticks."""
        rules = ExclusionRules(["a/**", "!a/keep.md", "a/keep.md"])

        assert rules.is_excluded("a/keep.md") is True

    def test_negation_disables_directory_pruning(self) -> None:
        """A dir cannot be pruned wholesale if a negation might re-include."""
        prunable = ExclusionRules(["internal/"])
        not_prunable = ExclusionRules(["internal/", "!internal/public.md"])

        assert prunable.is_dir_excluded("internal") is True
        assert not_prunable.is_dir_excluded("internal") is False

    def test_blank_and_comment_lines_ignored(self) -> None:
        rules = ExclusionRules(["", "   ", "# a comment", "drafts/"])

        assert rules.is_excluded("drafts/a.md") is True
        assert rules.is_excluded("guide/a.md") is False

    def test_path_forms_normalise(self) -> None:
        """Path objects, './' prefixes and duplicate slashes match alike."""
        rules = ExclusionRules(["guide/a.md"])

        assert rules.is_excluded(Path("guide/a.md")) is True
        assert rules.is_excluded("./guide/a.md") is True
        assert rules.is_excluded("guide//a.md") is True

    def test_empty_rules_are_falsy(self) -> None:
        assert not ExclusionRules()
        assert not ExclusionRules([])
        assert ExclusionRules(["x"])

    def test_unterminated_character_class_is_literal(self) -> None:
        """A stray '[' must not raise — it is matched literally."""
        rules = ExclusionRules(["draft[0-9.md"])

        assert rules.is_excluded("draft[0-9.md") is True


class TestFromConfig:
    """ExclusionRules.from_config tolerates the shapes YAML can produce."""

    def test_list_form(self) -> None:
        rules = ExclusionRules.from_config(["drafts/", "*.tmp.md"])

        assert rules.is_excluded("drafts/a.md") is True
        assert rules.is_excluded("x.tmp.md") is True

    def test_block_scalar_form(self) -> None:
        """The `mcp_exclude: |` newline-separated form works too."""
        rules = ExclusionRules.from_config("drafts/\ninternal/**\n")

        assert rules.is_excluded("drafts/a.md") is True
        assert rules.is_excluded("internal/a.md") is True

    def test_absent_key(self) -> None:
        assert not ExclusionRules.from_config(None)

    @pytest.mark.parametrize("bad", [42, {"drafts": True}, object()])
    def test_malformed_values_exclude_nothing(self, bad: object) -> None:
        """A malformed list must not crash config parsing."""
        rules = ExclusionRules.from_config(bad)

        assert rules.is_excluded("anything.md") is False


# ---------------------------------------------------------------------------
# End-to-end enforcement
# ---------------------------------------------------------------------------


_EXCLUDE_BLOCK = """\
extra:
  mcp_exclude:
    - drafts/
    - internal/**
    - "*-scratch.md"
    - "!internal/public.md"
"""

_LEGACY_EXCLUDE_BLOCK = """\
mcp_exclude:
  - drafts/
  - internal/**
  - "*-scratch.md"
  - "!internal/public.md"
"""


def _write_docs(docs_dir: Path) -> None:
    """Lay down a tree covering kept, excluded, and re-included documents."""
    files = {
        "index.md": "homepage zebra",
        "guide/intro.md": "guide zebra",
        "drafts/wip.md": "SECRETWORD draft zebra",
        "internal/runbook.md": "SECRETWORD internal zebra",
        "internal/public.md": "published anyway zebra",
        "guide/notes-scratch.md": "SECRETWORD scratch zebra",
    }
    for rel, body in files.items():
        path = docs_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n\n{body}\n", encoding="utf-8")


# Everything that must stay hidden, and everything that must remain visible.
EXCLUDED = ["drafts/wip.md", "internal/runbook.md", "guide/notes-scratch.md"]
VISIBLE = ["index.md", "guide/intro.md", "internal/public.md"]


@pytest.fixture
def excl_project(tmp_path: Path) -> Path:
    """An mkdocs project whose config carries an mcp_exclude block."""
    docs = tmp_path / "docs"
    docs.mkdir()
    _write_docs(docs)
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: Excl Demo\n" + _EXCLUDE_BLOCK, encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def excl_ctx(excl_project: Path):
    """Config, indexer and searcher wired together as the server does."""
    config = MkDocsConfig.from_file(excl_project / "mkdocs.yml")
    indexer = DocIndexer(config.docs_dir, exclusions=config.exclusions)
    indexer.build_index()
    searcher = DocSearcher(indexer.db_path)
    ctx = _make_ctx(config, indexer, searcher)
    yield ctx, config, indexer, searcher
    searcher.close()
    indexer.close()


def _flatten(items) -> list[str]:
    """Collect every path referenced anywhere in a nav tree."""
    paths: list[str] = []
    for item in items:
        if item.path:
            paths.append(item.path.replace("\\", "/"))
        paths.extend(_flatten(item.children))
    return paths


class TestConfigLevelExclusion:
    """mcp_exclude reaches MkDocsConfig and prunes the nav tree."""

    def test_exclusions_parsed_from_config(self, excl_project: Path) -> None:
        config = MkDocsConfig.from_file(excl_project / "mkdocs.yml")

        assert config.exclusions.is_excluded("drafts/wip.md") is True
        assert config.exclusions.is_excluded("index.md") is False

    def test_legacy_top_level_key_still_honoured(self, tmp_path: Path) -> None:
        """A bare top-level mcp_exclude still works as a deprecated fallback."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Legacy\n" + _LEGACY_EXCLUDE_BLOCK, encoding="utf-8"
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.exclusions.is_excluded("drafts/wip.md") is True
        assert config.exclusions.is_excluded("index.md") is False

    def test_extra_key_takes_precedence_over_legacy(self, tmp_path: Path) -> None:
        """If both are present, the new extra.mcp_exclude location wins."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Both\n"
            "mcp_exclude:\n"
            "  - drafts/\n"
            "extra:\n"
            "  mcp_exclude:\n"
            "    - internal/**\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.exclusions.is_excluded("internal/runbook.md") is True
        assert config.exclusions.is_excluded("drafts/wip.md") is False

    def test_nav_from_directory_omits_excluded(self, excl_project: Path) -> None:
        config = MkDocsConfig.from_file(excl_project / "mkdocs.yml")
        paths = _flatten(config.nav)

        assert sorted(paths) == sorted(VISIBLE)

    def test_fully_excluded_directory_produces_no_section(
        self, excl_project: Path
    ) -> None:
        """'drafts' has no surviving children, so no empty heading appears."""
        config = MkDocsConfig.from_file(excl_project / "mkdocs.yml")
        titles = [item.title for item in config.nav]

        assert "Drafts" not in titles

    def test_explicit_nav_is_filtered(self, tmp_path: Path) -> None:
        """An explicit `nav:` block drops excluded leaves and empty sections."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Explicit Nav\n"
            + _EXCLUDE_BLOCK
            + "nav:\n"
            "  - Home: index.md\n"
            "  - Guide:\n"
            "      - Intro: guide/intro.md\n"
            "      - Scratch: guide/notes-scratch.md\n"
            "  - Drafts:\n"
            "      - WIP: drafts/wip.md\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert _flatten(config.nav) == ["index.md", "guide/intro.md"]
        assert [i.title for i in config.nav] == ["Home", "Guide"]

    def test_nav_yml_is_filtered(self, tmp_path: Path) -> None:
        """The awesome-nav (.nav.yml) branch honours exclusions too."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - Home: index.md\n"
            "  - Guide: guide\n"
            "  - Drafts: drafts\n",
            encoding="utf-8",
        )
        (docs / "guide" / ".nav.yml").write_text(
            "nav:\n"
            "  - Intro: intro.md\n"
            "  - Scratch: notes-scratch.md\n",
            encoding="utf-8",
        )
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Nav Yml\nplugins:\n  - awesome-nav\n" + _EXCLUDE_BLOCK,
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert _flatten(config.nav) == ["index.md", "guide/intro.md"]
        assert "Drafts" not in [i.title for i in config.nav]

    def test_no_exclude_key_keeps_everything(self, tmp_path: Path) -> None:
        """Without mcp_exclude nothing is hidden — the default is unchanged."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text("site_name: Open\n", encoding="utf-8")

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert sorted(_flatten(config.nav)) == sorted(EXCLUDED + VISIBLE)


class TestIndexExclusion:
    """Excluded documents never enter the search index."""

    def test_scan_documents_filters(self, excl_project: Path) -> None:
        docs = excl_project / "docs"
        rules = ExclusionRules(["drafts/", "internal/**", "*-scratch.md"])

        found = {str(p.relative_to(docs)) for p in scan_documents(docs, rules)}

        assert found == {"index.md", "guide/intro.md"}

    def test_index_only_contains_visible(self, excl_ctx) -> None:
        ctx, *_ = excl_ctx
        listed = {d["path"] for d in list_documents(ctx)["documents"]}

        assert listed == set(VISIBLE)

    def test_search_cannot_reach_excluded_content(self, excl_ctx) -> None:
        """A term appearing only in excluded files returns nothing."""
        ctx, *_ = excl_ctx

        assert search("SECRETWORD", ctx)["results"] == []

    def test_search_still_finds_visible_content(self, excl_ctx) -> None:
        ctx, *_ = excl_ctx
        hits = {r["path"] for r in search("zebra", ctx)["results"]}

        assert hits == set(VISIBLE)

    def test_newly_excluded_files_are_removed_on_update(self, tmp_path: Path) -> None:
        """Adding a pattern evicts already-indexed documents on the next update."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text("site_name: Open\n", encoding="utf-8")

        open_config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")
        indexer = DocIndexer(open_config.docs_dir)
        indexer.build_index()
        before = set(indexer.repo.get_stored_metadata())
        indexer.close()

        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Open\n" + _EXCLUDE_BLOCK, encoding="utf-8"
        )
        closed_config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")
        indexer = DocIndexer(closed_config.docs_dir, exclusions=closed_config.exclusions)
        status = indexer.update_index()
        after = set(indexer.repo.get_stored_metadata())
        indexer.close()

        assert before == set(EXCLUDED + VISIBLE)
        assert after == set(VISIBLE)
        assert status.removed == len(EXCLUDED)

    def test_unexcluding_restores_documents(self, tmp_path: Path) -> None:
        """Removing the patterns brings the documents back on the next update."""
        docs = tmp_path / "docs"
        docs.mkdir()
        _write_docs(docs)
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Open\n" + _EXCLUDE_BLOCK, encoding="utf-8"
        )
        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")
        indexer = DocIndexer(config.docs_dir, exclusions=config.exclusions)
        indexer.build_index()
        indexer.close()

        indexer = DocIndexer(docs)
        indexer.update_index()
        after = set(indexer.repo.get_stored_metadata())
        indexer.close()

        assert after == set(EXCLUDED + VISIBLE)


class TestReadExclusion:
    """Excluded documents are refused by the tools that read from disk."""

    @pytest.mark.parametrize("path", EXCLUDED)
    def test_read_document_denies_excluded(self, excl_ctx, path: str) -> None:
        ctx, *_ = excl_ctx
        result = read_document(path, ctx)

        assert "error" in result
        assert "content" not in result

    @pytest.mark.parametrize("path", EXCLUDED)
    def test_outline_denies_excluded(self, excl_ctx, path: str) -> None:
        ctx, *_ = excl_ctx
        result = get_document_outline(path, ctx)

        assert "error" in result
        assert "headings" not in result

    def test_denial_is_indistinguishable_from_a_missing_file(self, excl_ctx) -> None:
        """The error must not confirm that an excluded document exists."""
        ctx, *_ = excl_ctx

        excluded = read_document("drafts/wip.md", ctx)
        missing = read_document("no/such/file.md", ctx)

        assert excluded == missing

    @pytest.mark.parametrize("path", VISIBLE)
    def test_visible_documents_still_readable(self, excl_ctx, path: str) -> None:
        ctx, *_ = excl_ctx
        result = read_document(path, ctx)

        assert "error" not in result
        assert result["content"]

    def test_negated_document_is_readable(self, excl_ctx) -> None:
        """internal/public.md is re-included by '!' despite internal/**."""
        ctx, *_ = excl_ctx
        result = read_document("internal/public.md", ctx)

        assert "error" not in result
        assert "published anyway" in result["content"]

    def test_url_encoded_path_cannot_bypass_exclusion(self, excl_ctx) -> None:
        """Percent-encoding must not slip past the exclusion check."""
        ctx, *_ = excl_ctx
        result = read_document("drafts%2Fwip.md", ctx)

        assert "error" in result

    def test_dot_segment_path_cannot_bypass_exclusion(self, excl_ctx) -> None:
        """A './' prefix normalises to the same excluded path."""
        ctx, *_ = excl_ctx
        result = read_document("./drafts/wip.md", ctx)

        assert "error" in result

    def test_traversal_into_excluded_dir_is_denied(self, excl_ctx) -> None:
        """Walking out and back in must not reach an excluded document."""
        ctx, *_ = excl_ctx
        result = read_document("guide/../drafts/wip.md", ctx)

        assert "error" in result
