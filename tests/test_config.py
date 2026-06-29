"""Tests for mkdocs_mcp.config — config detection, parsing, nav, and plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

from mkdocs_mcp.config import (
    MkDocsConfig,
    _extract_plugin_names,
    _title_from_path,
    find_config_file,
    parse_mkdocs_nav,
    parse_nav_yml,
)
from mkdocs_mcp.models import NavItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_mkdocs_project(tmp_path: Path) -> Path:
    """Create a temporary MkDocs project structure.

    Layout:
        mkdocs.yml            — site_name: "Test Site", docs_dir: docs
        docs/
            index.md          — frontmatter + body
            getting-started.md
            reference/
                api.md
    """
    project = tmp_path / "project"
    project.mkdir()

    # mkdocs.yml
    (project / "mkdocs.yml").write_text(
        "site_name: Test Site\n"
        "docs_dir: docs\n"
        "theme:\n"
        "  name: material\n",
        encoding="utf-8",
    )

    docs = project / "docs"
    docs.mkdir()

    (docs / "index.md").write_text(
        "---\n"
        "title: Home\n"
        "description: Welcome to the Test Site.\n"
        "---\n"
        "\n"
        "# Welcome\n"
        "\n"
        "This is the home page.\n",
        encoding="utf-8",
    )

    (docs / "getting-started.md").write_text(
        "# Getting Started\n\nFollow these steps.\n",
        encoding="utf-8",
    )

    ref_dir = docs / "reference"
    ref_dir.mkdir()
    (ref_dir / "api.md").write_text(
        "# API Reference\n\nThis page documents the public API.\n",
        encoding="utf-8",
    )

    return project


@pytest.fixture
def polkadot_config_path() -> Path:
    """Return the path to the real polkadot-mkdocs config, skip if not present."""
    path = Path("/workspace/polkadot-mkdocs/mkdocs.yml")
    if not path.exists():
        pytest.skip("polkadot-mkdocs not available at /workspace/polkadot-mkdocs/mkdocs.yml")
    return path


# ---------------------------------------------------------------------------
# find_config_file
# ---------------------------------------------------------------------------


class TestFindConfigFile:
    """Tests for find_config_file — directory walking."""

    def test_find_config_direct(self, tmp_path: Path) -> None:
        """Finds mkdocs.yml when it is in the start_path itself."""
        config = tmp_path / "mkdocs.yml"
        config.write_text("site_name: Direct\n", encoding="utf-8")

        result = find_config_file(tmp_path)

        assert result is not None
        assert result == config.resolve()

    def test_find_config_walks_up(self, tmp_path: Path) -> None:
        """Finds mkdocs.yml in a parent directory when searching from a child."""
        parent = tmp_path / "parent"
        parent.mkdir()
        config = parent / "mkdocs.yml"
        config.write_text("site_name: Parent\n", encoding="utf-8")

        child = parent / "child" / "grandchild"
        child.mkdir(parents=True)

        result = find_config_file(child)

        assert result is not None
        assert result == config.resolve()

    def test_find_config_yaml_extension(self, tmp_path: Path) -> None:
        """Finds mkdocs.yaml (alternative extension) when mkdocs.yml is absent."""
        config = tmp_path / "mkdocs.yaml"
        config.write_text("site_name: Yaml Extension\n", encoding="utf-8")

        result = find_config_file(tmp_path)

        assert result is not None
        assert result == config.resolve()

    def test_find_config_prefers_yml_over_yaml(self, tmp_path: Path) -> None:
        """Prefers mkdocs.yml over mkdocs.yaml when both exist."""
        yml = tmp_path / "mkdocs.yml"
        yml.write_text("site_name: YML\n", encoding="utf-8")
        yaml = tmp_path / "mkdocs.yaml"
        yaml.write_text("site_name: YAML\n", encoding="utf-8")

        result = find_config_file(tmp_path)

        assert result is not None
        assert result.name == "mkdocs.yml"

    def test_find_config_not_found(self, tmp_path: Path) -> None:
        """Returns None when no config is found within the walk limit."""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)

        # No mkdocs.yml anywhere — will hit filesystem root or walk limit
        result = find_config_file(deep)

        assert result is None


# ---------------------------------------------------------------------------
# MkDocsConfig.from_file
# ---------------------------------------------------------------------------


class TestMkDocsConfigFromFile:
    """Tests for MkDocsConfig.from_file — parsing a specific config file."""

    def test_parse_config_basic(self, tmp_mkdocs_project: Path) -> None:
        """Parses a minimal mkdocs.yml with site_name and docs_dir."""
        config_path = tmp_mkdocs_project / "mkdocs.yml"

        config = MkDocsConfig.from_file(config_path)

        assert config.site_name == "Test Site"
        assert config.docs_dir == (tmp_mkdocs_project / "docs").resolve()
        assert config.config_path == config_path.resolve()
        assert config.project_root == tmp_mkdocs_project.resolve()

    def test_parse_config_polkadot(self, polkadot_config_path: Path) -> None:
        """The real polkadot config parses successfully.

        The tolerant YAML loader handles !!python/name: and !ENV tags
        without executing them, so real-world configs work out of the box.
        """
        config = MkDocsConfig.from_file(polkadot_config_path)
        assert config.site_name == "Polkadot Developer Docs"
        assert config.theme_name == "material"

    def test_parse_config_nondefault_docs_dir(self, tmp_path: Path) -> None:
        """Handles a custom docs_dir value."""
        custom_docs = tmp_path / "custom-docs"
        custom_docs.mkdir()
        (custom_docs / "index.md").write_text("# Home\n", encoding="utf-8")
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Custom Docs\n"
            "docs_dir: custom-docs\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.docs_dir == custom_docs.resolve()

    def test_parse_config_default_docs_dir(self, tmp_path: Path) -> None:
        """When docs_dir is not specified, defaults to 'docs'."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home\n", encoding="utf-8")
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Default Docs\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.docs_dir == docs.resolve()

    def test_parse_config_missing_site_name(self, tmp_path: Path) -> None:
        """Raises ValueError when site_name is absent."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "mkdocs.yml").write_text(
            "docs_dir: docs\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="site_name"):
            MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

    def test_parse_config_invalid_yaml(self, tmp_path: Path) -> None:
        """Raises ValueError for files with invalid YAML."""
        config_file = tmp_path / "mkdocs.yml"
        config_file.write_text(
            "site_name: Bad\n"
            "key: [\n"   # unclosed bracket — invalid YAML
            "  broken\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="[Ii]nvalid YAML|YAML"):
            MkDocsConfig.from_file(config_file)

    def test_parse_config_nonexistent(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError for a non-existent config path."""
        with pytest.raises(FileNotFoundError):
            MkDocsConfig.from_file(tmp_path / "does-not-exist.yml")

    def test_parse_config_theme_string(self, tmp_path: Path) -> None:
        """Parses theme when specified as a plain string."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: String Theme\n"
            "theme: readthedocs\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.theme_name == "readthedocs"

    def test_parse_config_theme_dict(self, tmp_mkdocs_project: Path) -> None:
        """Parses theme when specified as a dict with a 'name' key."""
        config = MkDocsConfig.from_file(tmp_mkdocs_project / "mkdocs.yml")

        assert config.theme_name == "material"

    def test_parse_config_site_url(self, tmp_path: Path) -> None:
        """Parses site_url when present."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Has URL\n"
            "site_url: https://example.com/\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.site_url == "https://example.com/"

    def test_parse_config_no_site_url(self, tmp_mkdocs_project: Path) -> None:
        """site_url is None when not specified."""
        config = MkDocsConfig.from_file(tmp_mkdocs_project / "mkdocs.yml")

        assert config.site_url is None

    def test_parse_config_extra(self, tmp_path: Path) -> None:
        """Parses the extra dict when present."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Extra Test\n"
            "extra:\n"
            "  analytics:\n"
            "    provider: google\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        assert config.extra == {"analytics": {"provider": "google"}}

    def test_parse_config_not_a_dict(self, tmp_path: Path) -> None:
        """Raises ValueError when the YAML root is not a mapping."""
        config_file = tmp_path / "mkdocs.yml"
        config_file.write_text("- item1\n- item2\n", encoding="utf-8")

        with pytest.raises(ValueError):
            MkDocsConfig.from_file(config_file)


# ---------------------------------------------------------------------------
# _extract_plugin_names
# ---------------------------------------------------------------------------


class TestExtractPluginNames:
    """Tests for _extract_plugin_names — various plugin config formats."""

    def test_extract_plugins_string_list(self) -> None:
        """Extracts plugin names from a plain string list."""
        result = _extract_plugin_names(["search", "awesome-nav"])

        assert result == ["search", "awesome-nav"]

    def test_extract_plugins_dict_list(self) -> None:
        """Extracts plugin names when entries are dicts."""
        result = _extract_plugin_names([{"minify": {"minify_html": True}}])

        assert result == ["minify"]

    def test_extract_plugins_mixed(self) -> None:
        """Extracts all plugin names from a mixed list of strings and dicts."""
        result = _extract_plugin_names([
            "search",
            {"minify": {"minify_html": True}},
            "awesome-nav",
        ])

        assert result == ["search", "minify", "awesome-nav"]

    def test_extract_plugins_empty_list(self) -> None:
        """Returns empty list for an empty plugins config."""
        result = _extract_plugin_names([])

        assert result == []

    def test_extract_plugins_not_a_list(self) -> None:
        """Returns empty list when plugins_raw is not a list."""
        assert _extract_plugin_names(None) == []
        assert _extract_plugin_names("search") == []
        assert _extract_plugin_names({"search": {}}) == []

    def test_extract_plugins_polkadot(self, polkadot_config_path: Path) -> None:
        """The real polkadot config includes 'awesome-nav' in its plugins list."""
        config = MkDocsConfig.from_file(polkadot_config_path)
        assert "awesome-nav" in config.plugins
        assert "search" in config.plugins

    def test_extract_plugins_multiple_keys_in_dict(self) -> None:
        """Handles a dict entry with multiple keys (unusual but possible)."""
        result = _extract_plugin_names([{"plugin-a": {}, "plugin-b": {}}])

        assert "plugin-a" in result
        assert "plugin-b" in result


# ---------------------------------------------------------------------------
# parse_nav_yml
# ---------------------------------------------------------------------------


class TestParseNavYml:
    """Tests for parse_nav_yml — awesome-nav .nav.yml format."""

    def test_parse_nav_yml_basic(self, tmp_path: Path) -> None:
        """Parses a .nav.yml with file entries and verifies leaf nodes."""
        docs = tmp_path / "docs"
        docs.mkdir()

        (docs / "index.md").write_text("# Home\n", encoding="utf-8")
        (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")

        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Home': index.md\n"
            "  - 'Guide': guide.md\n",
            encoding="utf-8",
        )

        items = parse_nav_yml(docs)

        assert len(items) == 2
        home = items[0]
        assert home.title == "Home"
        assert home.path == "index.md"
        assert home.children == []

        guide = items[1]
        assert guide.title == "Guide"
        assert guide.path == "guide.md"
        assert guide.children == []

    def test_parse_nav_yml_nested(self, tmp_path: Path) -> None:
        """Parses nested .nav.yml files by recursing into subdirectories."""
        docs = tmp_path / "docs"
        docs.mkdir()

        (docs / "index.md").write_text("# Home\n", encoding="utf-8")

        subdir = docs / "tutorials"
        subdir.mkdir()
        (subdir / "intro.md").write_text("# Intro\n", encoding="utf-8")
        (subdir / "advanced.md").write_text("# Advanced\n", encoding="utf-8")
        (subdir / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Intro': intro.md\n"
            "  - 'Advanced': advanced.md\n",
            encoding="utf-8",
        )

        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Home': index.md\n"
            "  - 'Tutorials': tutorials\n",
            encoding="utf-8",
        )

        items = parse_nav_yml(docs)

        assert len(items) == 2

        home = items[0]
        assert home.title == "Home"
        assert home.path == "index.md"
        assert home.children == []

        tutorials = items[1]
        assert tutorials.title == "Tutorials"
        assert tutorials.children
        assert len(tutorials.children) == 2
        assert tutorials.children[0].title == "Intro"
        assert tutorials.children[0].path == "tutorials/intro.md"

    def test_parse_nav_yml_missing(self, tmp_path: Path) -> None:
        """Falls back to alphabetical directory listing when .nav.yml is absent."""
        docs = tmp_path / "docs"
        docs.mkdir()

        (docs / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
        (docs / "beta.md").write_text("# Beta\n", encoding="utf-8")

        # No .nav.yml — should fall back to directory listing
        items = parse_nav_yml(docs)

        paths = [item.path for item in items]
        assert "alpha.md" in paths
        assert "beta.md" in paths

    def test_parse_nav_yml_directory_with_index(self, tmp_path: Path) -> None:
        """Section nodes have path set to index.md when present."""
        docs = tmp_path / "docs"
        docs.mkdir()

        (docs / "index.md").write_text("# Home\n", encoding="utf-8")

        subdir = docs / "section"
        subdir.mkdir()
        (subdir / "index.md").write_text("# Section Index\n", encoding="utf-8")
        (subdir / "page.md").write_text("# Page\n", encoding="utf-8")

        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Home': index.md\n"
            "  - 'Section': section\n",
            encoding="utf-8",
        )

        items = parse_nav_yml(docs)
        section = items[1]

        assert section.title == "Section"
        assert section.path == "section/index.md"
        assert section.children

    def test_parse_nav_yml_bare_string_entry(self, tmp_path: Path) -> None:
        """Bare string entries in .nav.yml use the filename as title."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "about.md").write_text("# About\n", encoding="utf-8")
        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - about.md\n",
            encoding="utf-8",
        )

        items = parse_nav_yml(docs)

        assert len(items) == 1
        assert items[0].path == "about.md"
        assert items[0].title == "About"

    def test_parse_nav_yml_inline_list_section(self, tmp_path: Path) -> None:
        """Inline-list nav values become section headers with nested children.

        Regression test: previously an inline list target crashed with
        ``TypeError: unsupported operand type(s) for /: 'PosixPath' and 'list'``.
        """
        docs = tmp_path / "docs"
        docs.mkdir()

        (docs / "index.md").write_text("# Home\n", encoding="utf-8")

        reference = docs / "reference"
        reference.mkdir()
        (reference / "intro.md").write_text("# Intro\n", encoding="utf-8")
        advanced = reference / "advanced"
        advanced.mkdir()
        (advanced / "setup.md").write_text("# Setup\n", encoding="utf-8")

        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Home': index.md\n"
            "  - 'Reference':\n"
            "      - 'Intro': reference/intro.md\n"
            "      - 'Advanced':\n"
            "          - reference/advanced/setup.md\n",
            encoding="utf-8",
        )

        items = parse_nav_yml(docs)

        assert len(items) == 2

        home = items[0]
        assert home.title == "Home"
        assert home.path == "index.md"

        reference_item = items[1]
        assert reference_item.title == "Reference"
        # Section header defined inline has no path of its own.
        assert reference_item.path is None
        assert len(reference_item.children) == 2

        intro = reference_item.children[0]
        assert intro.title == "Intro"
        assert intro.path == "reference/intro.md"

        # Nested inline list — the previously-crashing nested shape.
        advanced_item = reference_item.children[1]
        assert advanced_item.title == "Advanced"
        assert advanced_item.path is None
        assert len(advanced_item.children) == 1

        setup = advanced_item.children[0]
        # Bare string leaf inside the nested inline list.
        assert setup.path == "reference/advanced/setup.md"

    def test_parse_nav_yml_invalid_yaml_falls_back(self, tmp_path: Path) -> None:
        """Falls back to directory listing when .nav.yml contains invalid YAML."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "page.md").write_text("# Page\n", encoding="utf-8")
        (docs / ".nav.yml").write_text(
            "nav: [\n"
            "  broken\n",
            encoding="utf-8",
        )

        # Should not raise; should fall back to directory listing
        items = parse_nav_yml(docs)
        paths = [item.path for item in items]
        assert "page.md" in paths


# ---------------------------------------------------------------------------
# parse_mkdocs_nav
# ---------------------------------------------------------------------------


class TestParseMkDocsNav:
    """Tests for parse_mkdocs_nav — inline nav from mkdocs.yml."""

    def test_parse_mkdocs_nav_basic(self, tmp_path: Path) -> None:
        """Parses a flat nav list with title: path entries."""
        docs = tmp_path / "docs"
        docs.mkdir()

        nav_config = [
            {"Home": "index.md"},
            {"Guide": "guide.md"},
        ]

        items = parse_mkdocs_nav(nav_config, docs)

        assert len(items) == 2
        assert items[0].title == "Home"
        assert items[0].path == "index.md"
        assert items[0].children == []
        assert items[1].title == "Guide"
        assert items[1].path == "guide.md"

    def test_parse_mkdocs_nav_nested(self, tmp_path: Path) -> None:
        """Parses nested sections with children."""
        docs = tmp_path / "docs"
        docs.mkdir()

        nav_config = [
            {"Home": "index.md"},
            {
                "Tutorials": [
                    {"Intro": "tutorials/intro.md"},
                    {"Advanced": "tutorials/advanced.md"},
                ]
            },
        ]

        items = parse_mkdocs_nav(nav_config, docs)

        assert len(items) == 2

        home = items[0]
        assert home.title == "Home"
        assert home.path == "index.md"
        assert home.children == []

        tutorials = items[1]
        assert tutorials.title == "Tutorials"
        assert tutorials.path is None
        assert len(tutorials.children) == 2
        assert tutorials.children[0].title == "Intro"
        assert tutorials.children[0].path == "tutorials/intro.md"
        assert tutorials.children[1].title == "Advanced"
        assert tutorials.children[1].path == "tutorials/advanced.md"

    def test_parse_mkdocs_nav_bare_string(self, tmp_path: Path) -> None:
        """Bare string entries use the path as both path and title."""
        docs = tmp_path / "docs"
        docs.mkdir()

        nav_config = ["about.md"]

        items = parse_mkdocs_nav(nav_config, docs)

        assert len(items) == 1
        assert items[0].path == "about.md"
        assert items[0].title == "About"

    def test_parse_mkdocs_nav_deeply_nested(self, tmp_path: Path) -> None:
        """Parses a three-level deep nav hierarchy."""
        docs = tmp_path / "docs"
        docs.mkdir()

        nav_config = [
            {
                "Reference": [
                    {
                        "API": [
                            {"Methods": "reference/api/methods.md"},
                        ]
                    }
                ]
            }
        ]

        items = parse_mkdocs_nav(nav_config, docs)

        assert len(items) == 1
        reference = items[0]
        assert reference.title == "Reference"
        assert reference.children

        api = reference.children[0]
        assert api.title == "API"
        assert api.children

        methods = api.children[0]
        assert methods.title == "Methods"
        assert methods.path == "reference/api/methods.md"

    def test_parse_mkdocs_nav_empty(self, tmp_path: Path) -> None:
        """Returns an empty list for an empty nav config."""
        items = parse_mkdocs_nav([], tmp_path / "docs")

        assert items == []


# ---------------------------------------------------------------------------
# MkDocsConfig.detect
# ---------------------------------------------------------------------------


class TestMkDocsConfigDetect:
    """Tests for MkDocsConfig.detect — end-to-end config finding and parsing."""

    def test_config_detect(self, tmp_mkdocs_project: Path) -> None:
        """Detects and parses config when searching from a subdirectory."""
        subdir = tmp_mkdocs_project / "docs" / "reference"

        config = MkDocsConfig.detect(subdir)

        assert config.site_name == "Test Site"
        assert config.project_root == tmp_mkdocs_project.resolve()

    def test_config_detect_from_project_root(self, tmp_mkdocs_project: Path) -> None:
        """Detects config when start_path is the project root itself."""
        config = MkDocsConfig.detect(tmp_mkdocs_project)

        assert config.site_name == "Test Site"

    def test_config_detect_not_found(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError when no config can be found."""
        empty_dir = tmp_path / "no_config"
        empty_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            MkDocsConfig.detect(empty_dir)

    def test_config_detect_returns_mkdocs_config_instance(
        self, tmp_mkdocs_project: Path
    ) -> None:
        """detect() returns an MkDocsConfig dataclass instance."""
        config = MkDocsConfig.detect(tmp_mkdocs_project)

        assert isinstance(config, MkDocsConfig)


# ---------------------------------------------------------------------------
# Nav integration — inline mkdocs.yml nav is used when no awesome-nav
# ---------------------------------------------------------------------------


class TestNavSelectionLogic:
    """Tests verifying which nav source is selected based on config."""

    def test_uses_inline_nav_when_defined(self, tmp_path: Path) -> None:
        """Uses inline nav from mkdocs.yml when nav key is present."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home\n", encoding="utf-8")
        (docs / "about.md").write_text("# About\n", encoding="utf-8")

        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Nav Test\n"
            "nav:\n"
            "  - Home: index.md\n"
            "  - About: about.md\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        titles = [item.title for item in config.nav]
        assert "Home" in titles
        assert "About" in titles

    def test_uses_nav_yml_when_awesome_nav_plugin(self, tmp_path: Path) -> None:
        """Uses .nav.yml when awesome-nav plugin is listed."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "index.md").write_text("# Home\n", encoding="utf-8")
        (docs / "page.md").write_text("# Page\n", encoding="utf-8")
        (docs / ".nav.yml").write_text(
            "nav:\n"
            "  - 'Custom Home': index.md\n"
            "  - 'Custom Page': page.md\n",
            encoding="utf-8",
        )

        (tmp_path / "mkdocs.yml").write_text(
            "site_name: AwesomeNav Test\n"
            "plugins:\n"
            "  - awesome-nav\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        titles = [item.title for item in config.nav]
        assert "Custom Home" in titles
        assert "Custom Page" in titles

    def test_falls_back_to_directory_listing(self, tmp_path: Path) -> None:
        """Falls back to directory listing when no nav or awesome-nav."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
        (docs / "beta.md").write_text("# Beta\n", encoding="utf-8")

        (tmp_path / "mkdocs.yml").write_text(
            "site_name: Dir List Test\n",
            encoding="utf-8",
        )

        config = MkDocsConfig.from_file(tmp_path / "mkdocs.yml")

        paths = [item.path for item in config.nav]
        assert "alpha.md" in paths
        assert "beta.md" in paths


# ---------------------------------------------------------------------------
# _title_from_path helper
# ---------------------------------------------------------------------------


class TestTitleFromPath:
    """Tests for the _title_from_path private helper."""

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("getting-started.md", "Getting Started"),
            ("run-a-node", "Run A Node"),
            ("index.md", "Index"),
            ("api_reference.md", "Api Reference"),
            ("simple.md", "Simple"),
            ("multi-word-title.md", "Multi Word Title"),
            ("no-extension", "No Extension"),
        ],
        ids=[
            "hyphenated-md",
            "dir-hyphenated",
            "index",
            "underscored",
            "simple",
            "multi-word",
            "no-extension",
        ],
    )
    def test_title_from_path(self, name: str, expected: str) -> None:
        assert _title_from_path(name) == expected


# ---------------------------------------------------------------------------
# Real-world polkadot-mkdocs tests
# ---------------------------------------------------------------------------


@pytest.fixture
def polkadot_config(polkadot_config_path: Path) -> MkDocsConfig:
    """Return a parsed MkDocsConfig for the polkadot-mkdocs project.

    The tolerant YAML loader handles !!python/name: and !ENV tags
    directly, so no preprocessing is needed.
    """
    return MkDocsConfig.from_file(polkadot_config_path)


class TestPolkadotConfig:
    """Integration tests against the real polkadot-mkdocs project."""

    def test_polkadot_config_handles_python_tags(self, polkadot_config_path: Path) -> None:
        """The tolerant loader handles !!python/name: and !ENV tags safely.

        These tags are used by mkdocs-material (emoji) and mkdocs env vars.
        The loader converts them to strings/defaults without code execution.
        """
        config = MkDocsConfig.from_file(polkadot_config_path)
        assert config.site_name == "Polkadot Developer Docs"
        # !ENV tags should resolve to their default values
        assert "awesome-nav" in config.plugins

    def test_polkadot_config_parses(self, polkadot_config: MkDocsConfig) -> None:
        """The preprocessed polkadot config (python tags stripped) parses cleanly."""
        assert polkadot_config is not None
        assert isinstance(polkadot_config, MkDocsConfig)

    def test_polkadot_docs_dir(self, polkadot_config: MkDocsConfig) -> None:
        """docs_dir is correctly resolved to the polkadot-docs directory."""
        assert polkadot_config.docs_dir.name == "polkadot-docs"
        assert polkadot_config.docs_dir.is_dir()

    def test_polkadot_theme(self, polkadot_config: MkDocsConfig) -> None:
        """theme_name is 'material'."""
        assert polkadot_config.theme_name == "material"

    def test_polkadot_site_name(self, polkadot_config: MkDocsConfig) -> None:
        """site_name matches the expected value."""
        assert polkadot_config.site_name == "Polkadot Developer Docs"

    def test_polkadot_nav_has_sections(self, polkadot_config: MkDocsConfig) -> None:
        """Nav tree has expected top-level sections from .nav.yml."""
        top_level_titles = [item.title for item in polkadot_config.nav]

        assert "Home" in top_level_titles
        assert "Smart Contracts" in top_level_titles
        assert "Parachains" in top_level_titles

    def test_polkadot_nav_leaf_nodes(self, polkadot_config: MkDocsConfig) -> None:
        """Leaf nodes have path set and no children."""
        # The 'Home' entry should be a leaf pointing to index.md
        home = next(
            (item for item in polkadot_config.nav if item.title == "Home"), None
        )

        assert home is not None
        assert home.path is not None
        assert home.children == []

    def test_polkadot_nav_section_nodes(self, polkadot_config: MkDocsConfig) -> None:
        """Section nodes have children."""
        smart_contracts = next(
            (item for item in polkadot_config.nav if item.title == "Smart Contracts"),
            None,
        )

        assert smart_contracts is not None
        assert len(smart_contracts.children) > 0

    def test_polkadot_plugins_list(self, polkadot_config: MkDocsConfig) -> None:
        """Plugins list contains expected entries."""
        assert "awesome-nav" in polkadot_config.plugins
        assert "search" in polkadot_config.plugins

    def test_polkadot_nav_uses_nav_yml(self, polkadot_config: MkDocsConfig) -> None:
        """Config with awesome-nav plugin reads nav from .nav.yml files."""
        assert "awesome-nav" in polkadot_config.plugins
        assert len(polkadot_config.nav) > 0
