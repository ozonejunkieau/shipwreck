"""Unit tests for shipwreck.graph.aliases."""

from __future__ import annotations

from shipwreck.config import AliasRule
from shipwreck.graph.aliases import apply_aliases
from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    ImageSource,
    SourceLocation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, tags: list[str] | None = None) -> GraphNode:
    node = GraphNode(id=node_id, canonical=node_id)
    if tags:
        node.tags_referenced = tags
    return node


def _source(file: str = "Dockerfile") -> ImageSource:
    return ImageSource(
        repo="repo",
        file=file,
        line=1,
        relationship=EdgeType.BUILDS_FROM,
        tag="latest",
    )


def _source_location() -> SourceLocation:
    return SourceLocation(repo="repo", file="Dockerfile", line=1, parser="dockerfile")


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relationship=EdgeType.BUILDS_FROM,
        confidence=Confidence.HIGH,
        source_location=_source_location(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPatternBasedAlias:
    """Pattern alias merges variant node into canonical."""

    def test_pattern_based_alias(self) -> None:
        graph = Graph()
        graph.nodes["docker.io/library/python-slim"] = _node("docker.io/library/python-slim")
        graph.nodes["docker.io/library/python"] = _node("docker.io/library/python")

        rule = AliasRule(
            pattern=r"docker\.io/library/python-slim",
            canonical="docker.io/library/python",
            variant="slim",
        )
        apply_aliases(graph, [rule])

        # Variant node absorbed into canonical.
        assert "docker.io/library/python-slim" not in graph.nodes
        assert "docker.io/library/python" in graph.nodes

    def test_variant_recorded_on_canonical(self) -> None:
        graph = Graph()
        graph.nodes["myrepo/app-alpine"] = _node("myrepo/app-alpine")
        graph.nodes["myrepo/app"] = _node("myrepo/app")

        rule = AliasRule(
            pattern=r"myrepo/app-alpine",
            canonical="myrepo/app",
            variant="alpine",
        )
        apply_aliases(graph, [rule])

        canonical = graph.nodes["myrepo/app"]
        assert any(v.variant_type == "alpine" for v in canonical.variants)

    def test_tags_merged_into_canonical(self) -> None:
        graph = Graph()
        graph.nodes["variant-node"] = _node("variant-node", tags=["v1", "v2"])
        graph.nodes["canonical-node"] = _node("canonical-node", tags=["v3"])

        rule = AliasRule(pattern=r"variant-node", canonical="canonical-node", variant="slim")
        apply_aliases(graph, [rule])

        canonical = graph.nodes["canonical-node"]
        assert "v1" in canonical.tags_referenced
        assert "v2" in canonical.tags_referenced
        assert "v3" in canonical.tags_referenced

    def test_sources_merged_into_canonical(self) -> None:
        graph = Graph()
        orig = _node("variant")
        orig.sources.append(_source("Dockerfile.variant"))
        canonical = _node("canonical")
        canonical.sources.append(_source("Dockerfile"))
        graph.nodes["variant"] = orig
        graph.nodes["canonical"] = canonical

        rule = AliasRule(pattern=r"variant", canonical="canonical")
        apply_aliases(graph, [rule])

        node = graph.nodes["canonical"]
        files = [s.file for s in node.sources]
        assert "Dockerfile.variant" in files
        assert "Dockerfile" in files

    def test_canonical_created_if_absent(self) -> None:
        """If the canonical node does not yet exist, it is created."""
        graph = Graph()
        graph.nodes["old-name"] = _node("old-name")

        rule = AliasRule(pattern=r"old-name", canonical="new-name")
        apply_aliases(graph, [rule])

        assert "old-name" not in graph.nodes
        assert "new-name" in graph.nodes


class TestPatternCaptureGroups:
    """Capture group {1} substitution works."""

    def test_pattern_capture_groups(self) -> None:
        graph = Graph()
        graph.nodes["docker.io/library/python-3.12"] = _node("docker.io/library/python-3.12")

        rule = AliasRule(
            pattern=r"docker\.io/library/python-(.+)",
            canonical="docker.io/library/python-{1}",
        )
        apply_aliases(graph, [rule])

        # The node id matches its own canonical, so nothing changes.
        assert "docker.io/library/python-3.12" in graph.nodes

    def test_capture_group_redirect(self) -> None:
        """Capture group remaps variant to a different canonical."""
        graph = Graph()
        graph.nodes["myregistry.io/app-slim"] = _node("myregistry.io/app-slim")

        rule = AliasRule(
            pattern=r"myregistry\.io/app-(.+)",
            canonical="myregistry.io/app",
            variant="{1}",
        )
        apply_aliases(graph, [rule])

        assert "myregistry.io/app-slim" not in graph.nodes
        assert "myregistry.io/app" in graph.nodes

    def test_multiple_capture_groups(self) -> None:
        graph = Graph()
        graph.nodes["reg.io/ns/img-variant"] = _node("reg.io/ns/img-variant")

        rule = AliasRule(
            pattern=r"reg\.io/(ns)/(img)-variant",
            canonical="reg.io/{1}/{2}",
        )
        apply_aliases(graph, [rule])

        assert "reg.io/ns/img-variant" not in graph.nodes
        assert "reg.io/ns/img" in graph.nodes


class TestNoMatchNoChange:
    """Nodes not matching any rule are untouched."""

    def test_no_match_no_change(self) -> None:
        graph = Graph()
        graph.nodes["docker.io/library/redis"] = _node("docker.io/library/redis")
        graph.nodes["docker.io/library/postgres"] = _node("docker.io/library/postgres")

        rule = AliasRule(pattern=r"mycompany\.io/.*", canonical="mycompany.io/canonical")
        apply_aliases(graph, [rule])

        assert "docker.io/library/redis" in graph.nodes
        assert "docker.io/library/postgres" in graph.nodes
        assert len(graph.nodes) == 2

    def test_empty_rules_no_change(self) -> None:
        graph = Graph()
        graph.nodes["some-image"] = _node("some-image")
        apply_aliases(graph, [])
        assert "some-image" in graph.nodes

    def test_empty_graph_no_error(self) -> None:
        graph = Graph()
        rule = AliasRule(pattern=r"docker\.io/.*", canonical="docker.io/canonical")
        apply_aliases(graph, [rule])
        assert len(graph.nodes) == 0


class TestEdgeRedirection:
    """Edges are redirected when a node is merged."""

    def test_edges_redirected_after_merge(self) -> None:
        graph = Graph()
        graph.nodes["old"] = _node("old")
        graph.nodes["canonical"] = _node("canonical")
        graph.nodes["consumer"] = _node("consumer")
        graph.edges.append(_edge("consumer", "old"))

        rule = AliasRule(pattern=r"old", canonical="canonical")
        apply_aliases(graph, [rule])

        # The edge target must now point to the canonical node.
        assert graph.edges[0].target == "canonical"

    def test_source_edge_redirected(self) -> None:
        graph = Graph()
        graph.nodes["old"] = _node("old")
        graph.nodes["canonical"] = _node("canonical")
        graph.nodes["base"] = _node("base")
        graph.edges.append(_edge("old", "base"))

        rule = AliasRule(pattern=r"old", canonical="canonical")
        apply_aliases(graph, [rule])

        assert graph.edges[0].source == "canonical"


class TestExplicitFromAlias:
    """Explicit from_image→canonical mapping merges nodes."""

    def test_explicit_from_alias(self) -> None:
        graph = Graph()
        graph.nodes["old.io/myapp"] = _node("old.io/myapp")
        graph.nodes["new.io/myapp"] = _node("new.io/myapp")

        rule = AliasRule(**{"from": "old.io/myapp", "canonical": "new.io/myapp"})
        apply_aliases(graph, [rule])

        assert "old.io/myapp" not in graph.nodes
        assert "new.io/myapp" in graph.nodes

    def test_explicit_from_missing_node_no_error(self) -> None:
        graph = Graph()
        graph.nodes["existing"] = _node("existing")

        rule = AliasRule(**{"from": "nonexistent", "canonical": "existing"})
        # Should not raise.
        apply_aliases(graph, [rule])
        assert "existing" in graph.nodes
