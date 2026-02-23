"""MkDocs configuration detection and parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mkdocs_mcp.models import NavItem
from mkdocs_mcp.utils import is_path_contained


_MAX_WALK_LEVELS = 10


class _SafeMkDocsLoader(yaml.SafeLoader):
    """YAML loader that tolerates mkdocs-specific tags without executing them.

    Real-world mkdocs.yml files use:
    - !!python/name:module.func  (mkdocs-material emoji extensions)
    - !ENV [VAR, default]        (mkdocs environment variables)

    yaml.safe_load() rejects these. This loader returns them as plain strings
    or their default values — safe, no code execution.
    """


def _handle_python_name(loader: yaml.Loader, suffix: str, node: yaml.Node) -> str:
    """Convert !!python/name:X to the string 'X' (no execution).

    For !!python/name:module.func, the module path is entirely in the suffix.
    The scalar value is always empty for these tags.
    """
    return suffix


def _handle_env_tag(loader: yaml.Loader, node: yaml.Node) -> Any:
    """Convert !ENV [VAR, default] to the default value, or the var name."""
    if isinstance(node, yaml.SequenceNode):
        values = loader.construct_sequence(node)
        # !ENV [VAR_NAME, default_value] → return default
        return values[-1] if len(values) > 1 else values[0] if values else None
    return loader.construct_scalar(node)  # type: ignore[arg-type]


# Register handlers for mkdocs-specific tags
_SafeMkDocsLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/name:", _handle_python_name
)
_SafeMkDocsLoader.add_constructor("!ENV", _handle_env_tag)


@dataclass
class MkDocsConfig:
    """Parsed MkDocs project configuration."""

    config_path: Path
    project_root: Path
    docs_dir: Path
    site_name: str
    site_url: str | None = None
    theme_name: str | None = None
    nav: list[NavItem] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def detect(cls, start_path: Path | None = None) -> MkDocsConfig:
        """Find and parse mkdocs.yml by walking up from start_path.

        Raises FileNotFoundError if no config found within 10 levels.
        """
        start = start_path or Path.cwd()
        config_path = find_config_file(start)
        if config_path is None:
            raise FileNotFoundError(
                f"No mkdocs.yml or mkdocs.yaml found within {_MAX_WALK_LEVELS} "
                f"levels above {start}"
            )
        return cls.from_file(config_path)

    @classmethod
    def from_file(cls, config_path: Path) -> MkDocsConfig:
        """Parse a specific mkdocs.yml file.

        Raises FileNotFoundError if file doesn't exist.
        Raises ValueError if YAML is invalid or missing required fields.
        """
        config_path = config_path.resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            raw = yaml.load(  # noqa: S506
                config_path.read_text(encoding="utf-8"),
                Loader=_SafeMkDocsLoader,
            )
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping in {config_path}")

        project_root = config_path.parent

        # Required field
        site_name = raw.get("site_name", "")
        if not site_name:
            raise ValueError(f"Missing 'site_name' in {config_path}")

        # docs_dir: default is 'docs', resolve relative to project root
        docs_dir_str = raw.get("docs_dir", "docs")
        docs_dir = (project_root / docs_dir_str).resolve()
        if not docs_dir.is_dir():
            raise ValueError(
                f"docs_dir '{docs_dir_str}' does not exist at {docs_dir}"
            )

        # Theme
        theme_name = None
        theme_raw = raw.get("theme")
        if isinstance(theme_raw, dict):
            theme_name = theme_raw.get("name")
        elif isinstance(theme_raw, str):
            theme_name = theme_raw

        # Plugins — extract just the names
        plugins = _extract_plugin_names(raw.get("plugins", []))

        # Site URL
        site_url = raw.get("site_url")

        # Extra
        extra = raw.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}

        # Navigation: explicit nav in mkdocs.yml always wins, then fall back
        # to .nav.yml files if awesome-nav plugin is used, then dir listing
        if "nav" in raw and isinstance(raw["nav"], list):
            nav = parse_mkdocs_nav(raw["nav"], docs_dir)
        elif "awesome-nav" in plugins and docs_dir.is_dir():
            nav = parse_nav_yml(docs_dir)
        else:
            nav = _nav_from_directory(docs_dir)

        return cls(
            config_path=config_path,
            project_root=project_root,
            docs_dir=docs_dir,
            site_name=site_name,
            site_url=site_url,
            theme_name=theme_name,
            nav=nav,
            plugins=plugins,
            extra=extra,
        )


def find_config_file(start_path: Path) -> Path | None:
    """Walk up from start_path looking for mkdocs.yml or mkdocs.yaml.

    Stops after _MAX_WALK_LEVELS levels or at filesystem root.
    Returns None if not found.
    """
    current = start_path.resolve()
    for _ in range(_MAX_WALK_LEVELS):
        for name in ("mkdocs.yml", "mkdocs.yaml"):
            candidate = current / name
            if candidate.is_file():
                return candidate
        parent = current.parent
        if parent == current:
            break  # reached filesystem root
        current = parent
    return None


def parse_nav_yml(
    docs_dir: Path, rel_dir: Path | None = None
) -> list[NavItem]:
    """Recursively parse .nav.yml files to build navigation tree.

    Handles the awesome-nav plugin format:
    - 'Title': filename.md  -> leaf node
    - 'Title': dirname      -> recurse into subdirectory

    Falls back to alphabetical .md file listing if no .nav.yml exists.
    """
    if rel_dir is None:
        rel_dir = Path(".")

    abs_dir = (docs_dir / rel_dir).resolve()
    nav_file = abs_dir / ".nav.yml"

    if not nav_file.is_file():
        return _nav_from_directory(docs_dir, rel_dir)

    try:
        raw = yaml.safe_load(nav_file.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return _nav_from_directory(docs_dir, rel_dir)

    if not isinstance(raw, dict) or "nav" not in raw:
        return _nav_from_directory(docs_dir, rel_dir)

    nav_list = raw["nav"]
    if not isinstance(nav_list, list):
        return _nav_from_directory(docs_dir, rel_dir)

    items: list[NavItem] = []
    for entry in nav_list:
        if isinstance(entry, dict):
            for title, target in entry.items():
                item = _parse_nav_entry(title, target, docs_dir, rel_dir)
                if item is not None:
                    items.append(item)
        elif isinstance(entry, str):
            # Bare string entry — use filename as title
            path = str(rel_dir / entry) if rel_dir != Path(".") else entry
            items.append(NavItem(title=_title_from_path(entry), path=path))

    return items


def parse_mkdocs_nav(
    nav_config: list, docs_dir: Path
) -> list[NavItem]:
    """Parse nav from mkdocs.yml (when nav is defined there instead of .nav.yml).

    Handles the nested dict/list structure:
    - {'Title': 'path.md'}  -> leaf
    - {'Section': [...]}    -> section with children
    """
    items: list[NavItem] = []
    for entry in nav_config:
        if isinstance(entry, dict):
            for title, value in entry.items():
                if isinstance(value, str):
                    items.append(NavItem(title=title, path=value))
                elif isinstance(value, list):
                    children = parse_mkdocs_nav(value, docs_dir)
                    items.append(NavItem(title=title, children=children))
        elif isinstance(entry, str):
            items.append(NavItem(title=_title_from_path(entry), path=entry))

    return items


def _parse_nav_entry(
    title: str, target: str, docs_dir: Path, rel_dir: Path
) -> NavItem | None:
    """Parse a single nav entry from .nav.yml."""
    target_path = (docs_dir / rel_dir / target)

    # Containment check: skip entries that escape docs_dir
    if not is_path_contained(target_path, docs_dir):
        return None

    target_path = target_path.resolve()

    if target_path.is_file() and target.endswith(".md"):
        rel_path = str(rel_dir / target)
        return NavItem(title=title, path=rel_path)

    # Guard against symlinked directories (prevents infinite recursion from loops)
    if target_path.is_dir() and not target_path.is_symlink():
        child_rel = rel_dir / target if rel_dir != Path(".") else Path(target)
        children = parse_nav_yml(docs_dir, child_rel)
        index_path = target_path / "index.md"
        section_path = None
        if index_path.is_file():
            section_path = str(child_rel / "index.md")
        return NavItem(title=title, path=section_path, children=children)

    # Target doesn't exist or is a symlinked dir — include with path but don't recurse
    rel_path = str(rel_dir / target)
    return NavItem(title=title, path=rel_path)


def _nav_from_directory(
    docs_dir: Path, rel_dir: Path | None = None
) -> list[NavItem]:
    """Build navigation from directory listing (fallback when no nav config).

    Lists .md files alphabetically, recurses into subdirectories.
    """
    if rel_dir is None:
        rel_dir = Path(".")

    abs_dir = (docs_dir / rel_dir).resolve()
    if not abs_dir.is_dir():
        return []

    items: list[NavItem] = []
    entries = sorted(abs_dir.iterdir(), key=lambda p: p.name)

    for entry in entries:
        # Skip hidden files/dirs and non-doc files
        if entry.name.startswith("."):
            continue

        if entry.is_file() and entry.suffix.lower() == ".md":
            rel_path = str(rel_dir / entry.name) if rel_dir != Path(".") else entry.name
            items.append(NavItem(
                title=_title_from_path(entry.name),
                path=rel_path,
            ))
        elif entry.is_dir() and not entry.is_symlink():
            child_rel = rel_dir / entry.name if rel_dir != Path(".") else Path(entry.name)
            children = _nav_from_directory(docs_dir, child_rel)
            if children:
                index_path = entry / "index.md"
                section_path = str(child_rel / "index.md") if index_path.is_file() else None
                items.append(NavItem(
                    title=_title_from_path(entry.name),
                    path=section_path,
                    children=children,
                ))

    return items


def _extract_plugin_names(plugins_raw: Any) -> list[str]:
    """Extract plugin names from mkdocs.yml plugins config.

    Plugins can be:
    - A list of strings: ['search', 'awesome-nav']
    - A list of dicts: [{'search': {}}, {'minify': {minify_html: true}}]
    - Mixed: ['search', {'minify': {minify_html: true}}]
    """
    if not isinstance(plugins_raw, list):
        return []

    names: list[str] = []
    for entry in plugins_raw:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            names.extend(entry.keys())

    return names


def _title_from_path(name: str) -> str:
    """Generate a display title from a filename or directory name.

    'getting-started.md' -> 'Getting Started'
    'run-a-node' -> 'Run A Node'
    'index.md' -> 'Index'
    """
    stem = name.removesuffix(".md")
    return stem.replace("-", " ").replace("_", " ").title()
