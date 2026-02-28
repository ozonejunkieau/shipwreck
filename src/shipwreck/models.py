"""Core domain models for Shipwreck."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EdgeType(StrEnum):
    """The type of relationship between two images in the graph."""

    BUILDS_FROM = "builds_from"
    PRODUCES = "produces"
    CONSUMES = "consumes"


class Confidence(StrEnum):
    """How confident the parser is in this image reference extraction."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceLocation(BaseModel):
    """Where in the source tree a reference was found."""

    repo: str
    file: str
    line: int
    parser: str


class ImageReference(BaseModel):
    """A single image reference discovered in a file."""

    # The raw image string as found in the file (before any resolution)
    raw: str

    # Parsed components (None if raw contains unresolvable templates)
    registry: str | None = None
    name: str | None = None
    tag: str | None = None

    # Where this reference was found
    source: SourceLocation

    # What kind of relationship this represents
    relationship: EdgeType

    # How confident we are in this extraction
    confidence: Confidence

    # For unresolved templates — the variable names that need resolution
    unresolved_variables: list[str] = Field(default_factory=list)

    # Parser-specific metadata
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegistryMetadata(BaseModel):
    """Registry-sourced metadata about an image."""

    size_bytes: int | None = None
    build_date: str | None = None
    digest: str | None = None


class ImageVariant(BaseModel):
    """A variant of an image produced by alias resolution."""

    tag_suffix: str
    variant_type: str


class ImageSource(BaseModel):
    """A source entry recording where a node was referenced."""

    repo: str
    file: str
    line: int
    relationship: EdgeType
    tag: str | None = None
    resolution: dict[str, Any] | None = None


class GraphNode(BaseModel):
    """A node in the dependency graph representing a unique image (sans tag)."""

    id: str
    canonical: str
    tags_referenced: list[str] = Field(default_factory=list)
    latest_available: str | None = None
    staleness: str | None = None
    version_scheme: str | None = None
    classification: str | None = None
    criticality: float = 0.0
    registry_metadata: RegistryMetadata = Field(default_factory=RegistryMetadata)
    variants: list[ImageVariant] = Field(default_factory=list)
    sources: list[ImageSource] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed edge in the dependency graph."""

    source: str
    target: str
    relationship: EdgeType
    confidence: Confidence
    source_location: SourceLocation


class GraphSummary(BaseModel):
    """Summary statistics for the graph."""

    total_images: int = 0
    stale_images: int = 0
    unresolved_references: int = 0
    classification_counts: dict[str, int] = Field(default_factory=dict)


class GraphEnvironment(BaseModel):
    """Environment context recorded when the graph was built."""

    resolved_env_vars: list[str] = Field(default_factory=list)
    ansible_inventory: str | None = None
    ansible_limit: str | None = None


class Graph(BaseModel):
    """The complete dependency graph produced by a scan."""

    schema_url: str = "https://shipwreck.dev/schema/v1.json"
    version: str = "1"
    generated_at: str = ""
    config_hash: str | None = None
    environment: GraphEnvironment = Field(default_factory=GraphEnvironment)
    nodes: dict[str, GraphNode] = Field(default_factory=dict)
    edges: list[GraphEdge] = Field(default_factory=list)
    summary: GraphSummary = Field(default_factory=GraphSummary)
