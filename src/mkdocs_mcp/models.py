from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HeadingInfo(BaseModel):
    """A heading within a document."""

    level: int = Field(ge=1, le=6, description="Heading level 1-6")
    text: str = Field(description="Heading text content")
    anchor: str = Field(description="URL-friendly slug for this heading")


class DocumentInfo(BaseModel):
    """Single document metadata for list_documents."""

    path: str = Field(description="Relative path from docs_dir")
    title: str = Field(description="Document title from frontmatter or first heading")
    description: str | None = Field(default=None, description="Document description from frontmatter")
    categories: list[str] = Field(default_factory=list, description="Categories from frontmatter")
    size: int = Field(description="File size in bytes")
    mtime: float = Field(description="Last modified timestamp")


class DocumentContent(BaseModel):
    """Full document with content for read_document."""

    path: str
    title: str
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    content: str = Field(description="Markdown body content (frontmatter stripped)")
    headings: list[HeadingInfo] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    size: int


class SearchMatch(BaseModel):
    """A single search result."""

    path: str
    title: str
    score: float = Field(description="Relevance score normalized to 0.0-1.0 (higher is better)")
    snippet: str = Field(description="Text snippet with match context")
    search_method: str = Field(description="'keyword', 'vector', or 'hybrid'")


class SearchResult(BaseModel):
    """Response from search tool."""

    query: str
    search_type: str
    results: list[SearchMatch] = Field(default_factory=list)
    total_count: int = Field(ge=0)


class NavItem(BaseModel):
    """Navigation tree node. Self-referencing for nested nav."""

    title: str
    path: str | None = Field(default=None, description="None for section headers without a page")
    children: list[NavItem] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    """Response from get_project_info tool."""

    site_name: str
    site_url: str | None = None
    docs_dir: str
    theme: str | None = None
    nav: list[NavItem] = Field(default_factory=list)
    document_count: int = Field(ge=0)
    index_status: str = Field(description="'ready', 'building', or 'stale'")


class DocumentOutline(BaseModel):
    """Response from get_document_outline tool."""

    path: str
    title: str
    headings: list[HeadingInfo] = Field(default_factory=list)


class IndexStatus(BaseModel):
    """Status of an indexing operation."""

    total_documents: int = Field(ge=0)
    indexed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    removed: int = Field(ge=0)
    failed: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    is_fresh: bool = Field(description="True if no stale documents remain")
