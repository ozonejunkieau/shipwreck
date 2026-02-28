"""Unit tests for shipwreck.graph.builder."""

from __future__ import annotations

from shipwreck.config import ShipwreckConfig
from shipwreck.graph.builder import _make_node_id, build_graph
from shipwreck.models import (
    Confidence,
    EdgeType,
    ImageReference,
    SourceLocation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source(repo: str = "myrepo", file: str = "Dockerfile", line: int = 1) -> SourceLocation:
    return SourceLocation(repo=repo, file=file, line=line, parser="dockerfile")


def _ref(
    raw: str,
    registry: str | None,
    name: str | None,
    tag: str | None,
    relationship: EdgeType,
    *,
    repo: str = "myrepo",
    file: str = "Dockerfile",
    line: int = 1,
    confidence: Confidence = Confidence.HIGH,
) -> ImageReference:
    return ImageReference(
        raw=raw,
        registry=registry,
        name=name,
        tag=tag,
        source=_source(repo=repo, file=file, line=line),
        relationship=relationship,
        confidence=confidence,
    )


_CONFIG = ShipwreckConfig()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSingleBuildsFrom:
    """Single BUILDS_FROM reference creates a node."""

    def test_single_builds_from(self) -> None:
        refs = [
            _ref(
                "python:3.12-slim",
                "docker.io",
                "library/python",
                "3.12-slim",
                EdgeType.BUILDS_FROM,
            )
        ]
        graph = build_graph(refs, _CONFIG)

        assert "docker.io/library/python" in graph.nodes
        node = graph.nodes["docker.io/library/python"]
        assert node.id == "docker.io/library/python"
        assert node.canonical == "docker.io/library/python"
        assert "3.12-slim" in node.tags_referenced

    def test_tag_recorded_on_node(self) -> None:
        refs = [
            _ref("alpine:3.18", "docker.io", "library/alpine", "3.18", EdgeType.BUILDS_FROM)
        ]
        graph = build_graph(refs, _CONFIG)
        node = graph.nodes["docker.io/library/alpine"]
        assert node.tags_referenced == ["3.18"]

    def test_source_recorded_on_node(self) -> None:
        refs = [
            _ref("python:3.12", "docker.io", "library/python", "3.12", EdgeType.BUILDS_FROM)
        ]
        graph = build_graph(refs, _CONFIG)
        node = graph.nodes["docker.io/library/python"]
        assert len(node.sources) == 1
        assert node.sources[0].relationship == EdgeType.BUILDS_FROM
        assert node.sources[0].file == "Dockerfile"


class TestDeduplication:
    """Same image with different tags → one node with multiple tags."""

    def test_deduplication_same_image_different_tags(self) -> None:
        refs = [
            _ref("python:3.11", "docker.io", "library/python", "3.11", EdgeType.BUILDS_FROM),
            _ref("python:3.12", "docker.io", "library/python", "3.12", EdgeType.BUILDS_FROM),
        ]
        graph = build_graph(refs, _CONFIG)

        assert len(graph.nodes) >= 1
        node = graph.nodes["docker.io/library/python"]
        assert "3.11" in node.tags_referenced
        assert "3.12" in node.tags_referenced

    def test_tag_not_duplicated(self) -> None:
        refs = [
            _ref("redis:7", "docker.io", "library/redis", "7", EdgeType.BUILDS_FROM),
            _ref("redis:7", "docker.io", "library/redis", "7", EdgeType.CONSUMES),
        ]
        graph = build_graph(refs, _CONFIG)
        node = graph.nodes["docker.io/library/redis"]
        assert node.tags_referenced.count("7") == 1


class TestProducesBuildsFromEdge:
    """PRODUCES + BUILDS_FROM in same file → edge between produced and base."""

    def test_produces_builds_from_creates_edge(self) -> None:
        refs = [
            _ref(
                "myapp:latest",
                "docker.io",
                "myorg/myapp",
                "latest",
                EdgeType.PRODUCES,
                file="docker-bake.hcl",
            ),
            _ref(
                "python:3.12-slim",
                "docker.io",
                "library/python",
                "3.12-slim",
                EdgeType.BUILDS_FROM,
                file="docker-bake.hcl",
            ),
        ]
        graph = build_graph(refs, _CONFIG)

        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.source == "docker.io/myorg/myapp"
        assert edge.target == "docker.io/library/python"
        assert edge.relationship == EdgeType.BUILDS_FROM

    def test_cross_file_no_edge(self) -> None:
        """References in different files do NOT produce pairing edges."""
        refs = [
            _ref(
                "myapp:latest",
                "docker.io",
                "myorg/myapp",
                "latest",
                EdgeType.PRODUCES,
                file="bake.hcl",
            ),
            _ref(
                "python:3.12",
                "docker.io",
                "library/python",
                "3.12",
                EdgeType.BUILDS_FROM,
                file="Dockerfile",
            ),
        ]
        graph = build_graph(refs, _CONFIG)
        # No direct PRODUCES↔BUILDS_FROM pairing across different files.
        paired_edges = [
            e for e in graph.edges if e.source == "docker.io/myorg/myapp"
        ]
        assert len(paired_edges) == 0

    def test_confidence_is_minimum(self) -> None:
        refs = [
            _ref(
                "myapp:latest",
                "docker.io",
                "myorg/myapp",
                "latest",
                EdgeType.PRODUCES,
                file="bake.hcl",
                confidence=Confidence.HIGH,
            ),
            _ref(
                "python:3.12",
                "docker.io",
                "library/python",
                "3.12",
                EdgeType.BUILDS_FROM,
                file="bake.hcl",
                confidence=Confidence.LOW,
            ),
        ]
        graph = build_graph(refs, _CONFIG)
        assert graph.edges[0].confidence == Confidence.LOW


class TestConsumesCreatesNode:
    """CONSUMES reference creates a node."""

    def test_consumes_creates_node(self) -> None:
        refs = [
            _ref(
                "nginx:1.25",
                "docker.io",
                "library/nginx",
                "1.25",
                EdgeType.CONSUMES,
                file="deploy.yml",
            )
        ]
        graph = build_graph(refs, _CONFIG)

        assert "docker.io/library/nginx" in graph.nodes
        node = graph.nodes["docker.io/library/nginx"]
        assert node.sources[0].relationship == EdgeType.CONSUMES

    def test_consumes_no_paired_edge(self) -> None:
        """A standalone CONSUMES ref does not create a BUILDS_FROM edge."""
        refs = [
            _ref("nginx:1.25", "docker.io", "library/nginx", "1.25", EdgeType.CONSUMES)
        ]
        graph = build_graph(refs, _CONFIG)
        builds_from_edges = [e for e in graph.edges if e.relationship == EdgeType.BUILDS_FROM]
        assert len(builds_from_edges) == 0


class TestUnresolvedTemplateNodeId:
    """Unresolved template string becomes node id."""

    def test_unresolved_template_node_id(self) -> None:
        refs = [
            ImageReference(
                raw="${IMAGE_NAME}:${IMAGE_TAG}",
                registry=None,
                name=None,
                tag=None,
                source=_source(),
                relationship=EdgeType.BUILDS_FROM,
                confidence=Confidence.LOW,
                unresolved_variables=["IMAGE_NAME", "IMAGE_TAG"],
            )
        ]
        graph = build_graph(refs, _CONFIG)
        assert "${IMAGE_NAME}:${IMAGE_TAG}" in graph.nodes

    def test_unresolved_counted_in_summary(self) -> None:
        refs = [
            ImageReference(
                raw="${BASE_IMAGE}",
                registry=None,
                name=None,
                tag=None,
                source=_source(),
                relationship=EdgeType.BUILDS_FROM,
                confidence=Confidence.LOW,
                unresolved_variables=["BASE_IMAGE"],
            )
        ]
        graph = build_graph(refs, _CONFIG)
        assert graph.summary.unresolved_references >= 1


class TestSummaryCounts:
    """Graph summary reflects node count."""

    def test_summary_counts(self) -> None:
        refs = [
            _ref("python:3.12", "docker.io", "library/python", "3.12", EdgeType.BUILDS_FROM),
            _ref("redis:7", "docker.io", "library/redis", "7", EdgeType.CONSUMES),
        ]
        graph = build_graph(refs, _CONFIG)
        assert graph.summary.total_images == len(graph.nodes)

    def test_empty_graph_summary(self) -> None:
        graph = build_graph([], _CONFIG)
        assert graph.summary.total_images == 0
        assert graph.summary.unresolved_references == 0

    def test_generated_at_stored(self) -> None:
        graph = build_graph([], _CONFIG, generated_at="2024-01-01T00:00:00Z")
        assert graph.generated_at == "2024-01-01T00:00:00Z"


class TestStandaloneBuildsFromSynthetic:
    """BUILDS_FROM with no paired PRODUCES creates a synthetic source node."""

    def test_synthetic_node_created(self) -> None:
        refs = [
            _ref(
                "python:3.12",
                "docker.io",
                "library/python",
                "3.12",
                EdgeType.BUILDS_FROM,
                repo="myrepo",
                file="Dockerfile",
            )
        ]
        graph = build_graph(refs, _CONFIG)
        synthetic_id = "file:myrepo:Dockerfile"
        assert synthetic_id in graph.nodes

    def test_synthetic_edge_created(self) -> None:
        refs = [
            _ref(
                "python:3.12",
                "docker.io",
                "library/python",
                "3.12",
                EdgeType.BUILDS_FROM,
                repo="myrepo",
                file="Dockerfile",
            )
        ]
        graph = build_graph(refs, _CONFIG)
        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.source == "file:myrepo:Dockerfile"
        assert edge.target == "docker.io/library/python"


class TestMakeNodeId:
    """Unit tests for the _make_node_id helper."""

    def test_resolved_ref(self) -> None:
        ref = _ref("python:3.12", "docker.io", "library/python", "3.12", EdgeType.BUILDS_FROM)
        assert _make_node_id(ref) == "docker.io/library/python"

    def test_unresolved_ref_uses_raw(self) -> None:
        ref = ImageReference(
            raw="${MY_IMAGE}",
            registry=None,
            name=None,
            tag=None,
            source=_source(),
            relationship=EdgeType.BUILDS_FROM,
            confidence=Confidence.LOW,
        )
        assert _make_node_id(ref) == "${MY_IMAGE}"
