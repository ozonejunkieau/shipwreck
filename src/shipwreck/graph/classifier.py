"""Classify graph nodes by their role in the image supply chain."""

from __future__ import annotations

import fnmatch

from shipwreck.config import ClassificationConfig
from shipwreck.models import EdgeType, Graph, GraphNode


def classify_nodes(graph: Graph, classification_config: ClassificationConfig) -> None:
    """Assign a classification to each node based on config rules and heuristics.

    Classification precedence (first match wins):

    1. Config rules — each :class:`~shipwreck.config.ClassificationRule` may
       specify a ``path_pattern`` (matched against source file paths) or an
       ``image_pattern`` (matched against the node id using
       :func:`fnmatch.fnmatch`).
    2. Heuristic fallback:
       - Node referenced **only** from test/CI paths → ``"test"``
       - Node referenced only via BUILDS_FROM (never produced or consumed)
         → ``"base"``
       - Node that is both PRODUCED and appears in BUILDS_FROM, but never
         CONSUMED → ``"intermediate"``
       - Node that appears in any CONSUMES reference → ``"product"``
       - Default → ``"base"``

    Args:
        graph: Graph to classify (modified in-place).
        classification_config: Classification rules from config.
    """
    for node in graph.nodes.values():
        classification = _classify_by_rules(node, classification_config)
        if classification is None:
            classification = _classify_heuristic(node)
        node.classification = classification

    # Refresh the summary classification counts.
    classification_counts: dict[str, int] = {}
    for node in graph.nodes.values():
        if node.classification:
            classification_counts[node.classification] = (
                classification_counts.get(node.classification, 0) + 1
            )
    graph.summary.classification_counts = classification_counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TEST_PATH_INDICATORS: frozenset[str] = frozenset(
    {
        "test/",
        "tests/",
        ".gitlab-ci",
        ".github/workflows",
        "ci/",
    }
)


def _classify_by_rules(node: GraphNode, config: ClassificationConfig) -> str | None:
    """Return the first matching config rule classification, or None.

    Args:
        node: The node to classify.
        config: Classification configuration containing ordered rules.

    Returns:
        Classification string if a rule matched, otherwise ``None``.
    """
    for rule in config.rules:
        if rule.path_pattern:
            if any(fnmatch.fnmatch(src.file, rule.path_pattern) for src in node.sources):
                return rule.image_class
        if rule.image_pattern:
            if fnmatch.fnmatch(node.id, rule.image_pattern):
                return rule.image_class
    return None


def _classify_heuristic(node: GraphNode) -> str:
    """Apply heuristic classification rules when no config rule matched.

    Args:
        node: The node to classify.

    Returns:
        A classification string: one of ``"test"``, ``"base"``,
        ``"intermediate"``, or ``"product"``.
    """
    relationships = {src.relationship for src in node.sources}
    paths = [src.file for src in node.sources]

    # Test-only: every source path contains a test/CI indicator.
    if paths and all(
        any(indicator in p for indicator in _TEST_PATH_INDICATORS) for p in paths
    ):
        return "test"

    has_produces = EdgeType.PRODUCES in relationships
    has_consumes = EdgeType.CONSUMES in relationships
    has_builds_from = EdgeType.BUILDS_FROM in relationships

    if has_consumes and has_produces:
        return "product"  # we build AND deploy it
    if has_produces and has_builds_from and not has_consumes:
        return "intermediate"
    if has_builds_from and not has_produces and not has_consumes:
        return "base"
    if has_consumes and not has_produces:
        return "external"  # consumed but not built locally

    # Default: treat as base (external / upstream image with no local context).
    return "base"
