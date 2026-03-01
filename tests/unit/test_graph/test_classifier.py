"""Unit tests for shipwreck.graph.classifier."""

from __future__ import annotations

from shipwreck.config import ClassificationConfig, ClassificationRule
from shipwreck.graph.classifier import classify_nodes
from shipwreck.models import EdgeType, Graph, GraphNode, ImageSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str) -> GraphNode:
    return GraphNode(id=node_id, canonical=node_id)


def _source(relationship: EdgeType, file: str = "Dockerfile") -> ImageSource:
    return ImageSource(
        repo="repo",
        file=file,
        line=1,
        relationship=relationship,
        tag="latest",
    )


def _empty_config() -> ClassificationConfig:
    return ClassificationConfig(rules=[])


def _rule(*, path_pattern: str | None = None, image_pattern: str | None = None, cls: str) -> ClassificationRule:
    data: dict = {"class": cls}
    if path_pattern:
        data["path_pattern"] = path_pattern
    if image_pattern:
        data["image_pattern"] = image_pattern
    return ClassificationRule.model_validate(data)


# ---------------------------------------------------------------------------
# Tests: heuristic classification
# ---------------------------------------------------------------------------


class TestBuildsFromOnlyIsBase:
    """Node only in FROM → classified as base."""

    def test_builds_from_only_is_base(self) -> None:
        graph = Graph()
        node = _node("docker.io/library/python")
        node.sources.append(_source(EdgeType.BUILDS_FROM))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/library/python"].classification == "base"

    def test_multiple_builds_from_still_base(self) -> None:
        graph = Graph()
        node = _node("docker.io/library/alpine")
        node.sources.append(_source(EdgeType.BUILDS_FROM, "Dockerfile"))
        node.sources.append(_source(EdgeType.BUILDS_FROM, "Dockerfile.prod"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/library/alpine"].classification == "base"


class TestConsumesOnly:
    """Node only consumed (not produced) → classified as external."""

    def test_consumes_only_is_external(self) -> None:
        graph = Graph()
        node = _node("docker.io/library/postgres")
        node.sources.append(_source(EdgeType.CONSUMES, "roles/db/tasks/main.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/library/postgres"].classification == "external"

    def test_consumes_plus_builds_from_no_produces_is_external(self) -> None:
        """CONSUMES without PRODUCES → external, even with BUILDS_FROM."""
        graph = Graph()
        node = _node("myapp")
        node.sources.append(_source(EdgeType.BUILDS_FROM))
        node.sources.append(_source(EdgeType.CONSUMES, "deploy.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["myapp"].classification == "external"


class TestConsumesAndProducesIsProduct:
    """Node both produced and consumed → classified as product."""

    def test_consumes_and_produces_is_product(self) -> None:
        graph = Graph()
        node = _node("docker.io/myorg/myapp")
        node.sources.append(_source(EdgeType.PRODUCES, "docker-bake.hcl"))
        node.sources.append(_source(EdgeType.CONSUMES, "deploy.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/myorg/myapp"].classification == "product"


class TestIntermediateClassification:
    """Node that is produced AND builds from something, but not consumed → intermediate."""

    def test_produces_and_builds_from_is_intermediate(self) -> None:
        graph = Graph()
        node = _node("docker.io/myorg/builder")
        node.sources.append(_source(EdgeType.PRODUCES, "docker-bake.hcl"))
        node.sources.append(_source(EdgeType.BUILDS_FROM, "docker-bake.hcl"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/myorg/builder"].classification == "intermediate"


class TestTestClassification:
    """Node only referenced from test/CI paths → classified as test."""

    def test_ci_path_is_test(self) -> None:
        graph = Graph()
        node = _node("docker.io/myorg/testrunner")
        node.sources.append(_source(EdgeType.BUILDS_FROM, ".github/workflows/ci.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/myorg/testrunner"].classification == "test"

    def test_test_dir_is_test(self) -> None:
        graph = Graph()
        node = _node("docker.io/myorg/fixtures")
        node.sources.append(_source(EdgeType.BUILDS_FROM, "tests/Dockerfile"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        assert graph.nodes["docker.io/myorg/fixtures"].classification == "test"

    def test_mixed_paths_not_test(self) -> None:
        """If even one path is non-test, it should not be classified as test."""
        graph = Graph()
        node = _node("docker.io/myorg/myapp")
        node.sources.append(_source(EdgeType.BUILDS_FROM, ".github/workflows/ci.yml"))
        node.sources.append(_source(EdgeType.CONSUMES, "deploy.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        # deploy.yml is not a test path, so should not be "test"
        assert graph.nodes["docker.io/myorg/myapp"].classification != "test"


# ---------------------------------------------------------------------------
# Tests: config rule classification
# ---------------------------------------------------------------------------


class TestConfigRuleOverridesHeuristic:
    """Config rules take precedence over heuristics."""

    def test_config_rule_overrides_heuristic(self) -> None:
        graph = Graph()
        # Heuristic would classify this as "base" (only BUILDS_FROM).
        node = _node("docker.io/library/python")
        node.sources.append(_source(EdgeType.BUILDS_FROM, "Dockerfile"))
        graph.nodes[node.id] = node

        config = ClassificationConfig(
            rules=[_rule(image_pattern="docker.io/library/*", cls="upstream")]
        )
        classify_nodes(graph, config)
        assert graph.nodes["docker.io/library/python"].classification == "upstream"

    def test_path_pattern_rule(self) -> None:
        graph = Graph()
        node = _node("myapp")
        node.sources.append(_source(EdgeType.BUILDS_FROM, "deploy/kubernetes/base.yml"))
        graph.nodes[node.id] = node

        config = ClassificationConfig(
            rules=[_rule(path_pattern="deploy/*", cls="deployment")]
        )
        classify_nodes(graph, config)
        assert graph.nodes["myapp"].classification == "deployment"

    def test_first_matching_rule_wins(self) -> None:
        graph = Graph()
        node = _node("docker.io/library/redis")
        node.sources.append(_source(EdgeType.CONSUMES, "deploy.yml"))
        graph.nodes[node.id] = node

        config = ClassificationConfig(
            rules=[
                _rule(image_pattern="docker.io/library/*", cls="external"),
                _rule(image_pattern="docker.io/library/redis", cls="cache"),
            ]
        )
        classify_nodes(graph, config)
        # First rule matches, second is never evaluated.
        assert graph.nodes["docker.io/library/redis"].classification == "external"

    def test_unmatched_rule_falls_through_to_heuristic(self) -> None:
        graph = Graph()
        node = _node("docker.io/library/python")
        node.sources.append(_source(EdgeType.BUILDS_FROM, "Dockerfile"))
        graph.nodes[node.id] = node

        config = ClassificationConfig(
            rules=[_rule(image_pattern="mycompany.io/*", cls="internal")]
        )
        classify_nodes(graph, config)
        # No rule matched → heuristic kicks in → base
        assert graph.nodes["docker.io/library/python"].classification == "base"


# ---------------------------------------------------------------------------
# Tests: summary updated
# ---------------------------------------------------------------------------


class TestSummaryUpdated:
    """classify_nodes refreshes the graph summary classification counts."""

    def test_summary_updated(self) -> None:
        graph = Graph()
        for i in range(3):
            node = _node(f"base-{i}")
            node.sources.append(_source(EdgeType.BUILDS_FROM))
            graph.nodes[node.id] = node

        node = _node("external-img")
        node.sources.append(_source(EdgeType.CONSUMES, "deploy.yml"))
        graph.nodes[node.id] = node

        classify_nodes(graph, _empty_config())
        counts = graph.summary.classification_counts
        assert counts.get("base", 0) == 3
        assert counts.get("external", 0) == 1

    def test_empty_graph_no_error(self) -> None:
        graph = Graph()
        classify_nodes(graph, _empty_config())
        assert graph.summary.classification_counts == {}
