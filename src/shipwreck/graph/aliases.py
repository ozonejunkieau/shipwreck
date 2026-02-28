"""Apply alias rules to merge variant image nodes into their canonical forms."""

from __future__ import annotations

import re

from shipwreck.config import AliasRule
from shipwreck.models import Graph, GraphNode, ImageVariant


def apply_aliases(graph: Graph, alias_rules: list[AliasRule]) -> Graph:
    """Apply alias rules to merge variant nodes into canonical nodes.

    Two kinds of rules are supported:

    - **Pattern-based**: ``rule.pattern`` is a regex applied to each node id.
      The ``rule.canonical`` string may contain ``{1}``, ``{2}`` … placeholders
      that are replaced with the corresponding capture-group values from the
      match, producing the canonical node id.
    - **Explicit mapping**: ``rule.from_image`` is an exact node id to merge
      into ``rule.canonical``.

    When a node is merged into a canonical node:

    1. The canonical node is created if it does not already exist.
    2. Tags and sources from the variant node are merged into the canonical.
    3. An :class:`~shipwreck.models.ImageVariant` entry is appended to the
       canonical node when ``rule.variant`` is set.
    4. All graph edges that referenced the old node id are updated to reference
       the canonical id.
    5. The old node is removed from the graph.

    Args:
        graph: The graph to process.
        alias_rules: List of alias rules from config.

    Returns:
        The modified graph (same object, returned for convenience).
    """
    for rule in alias_rules:
        if rule.pattern and rule.canonical:
            _apply_pattern_rule(graph, rule)
        elif rule.from_image and rule.canonical:
            _apply_explicit_rule(graph, rule)

    return graph


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_pattern_rule(graph: Graph, rule: AliasRule) -> None:
    """Merge nodes whose id matches ``rule.pattern`` into the computed canonical.

    Args:
        graph: The graph to modify in-place.
        rule: An alias rule with a ``pattern`` and ``canonical`` template.
    """
    assert rule.pattern is not None
    assert rule.canonical is not None

    # Collect matches first to avoid mutating the dict while iterating.
    nodes_to_merge: list[tuple[str, str]] = []
    for node_id in list(graph.nodes.keys()):
        m = re.match(rule.pattern, node_id)
        if m:
            canonical_id = rule.canonical
            for i, group in enumerate(m.groups(), start=1):
                canonical_id = canonical_id.replace("{" + str(i) + "}", group or "")
            nodes_to_merge.append((node_id, canonical_id))

    for orig_id, canonical_id in nodes_to_merge:
        if orig_id == canonical_id:
            continue
        _merge_node(graph, orig_id, canonical_id, rule.variant)


def _apply_explicit_rule(graph: Graph, rule: AliasRule) -> None:
    """Merge the node identified by ``rule.from_image`` into ``rule.canonical``.

    Args:
        graph: The graph to modify in-place.
        rule: An alias rule with a ``from_image`` and ``canonical`` value.
    """
    assert rule.from_image is not None
    assert rule.canonical is not None

    if rule.from_image not in graph.nodes:
        return
    if rule.from_image == rule.canonical:
        return

    _merge_node(graph, rule.from_image, rule.canonical, rule.variant)


def _merge_node(graph: Graph, orig_id: str, canonical_id: str, variant_type: str | None) -> None:
    """Merge *orig_id* into *canonical_id*, updating all edges.

    Args:
        graph: The graph to modify in-place.
        orig_id: The node id to absorb.
        canonical_id: The target canonical node id.
        variant_type: Optional variant label to record on the canonical node.
    """
    # Ensure canonical node exists.
    if canonical_id not in graph.nodes:
        graph.nodes[canonical_id] = GraphNode(id=canonical_id, canonical=canonical_id)

    canonical_node = graph.nodes[canonical_id]
    orig_node = graph.nodes.pop(orig_id)

    # Merge tags (deduplicate).
    for tag in orig_node.tags_referenced:
        if tag not in canonical_node.tags_referenced:
            canonical_node.tags_referenced.append(tag)

    # Merge sources (extend without dedup — sources record individual references).
    canonical_node.sources.extend(orig_node.sources)

    # Record variant if a type was specified.
    if variant_type:
        tag_suffix = orig_id.replace(canonical_id, "")
        canonical_node.variants.append(
            ImageVariant(tag_suffix=tag_suffix, variant_type=variant_type)
        )

    # Redirect all edges that referenced the old node id.
    for edge in graph.edges:
        if edge.source == orig_id:
            edge.source = canonical_id
        if edge.target == orig_id:
            edge.target = canonical_id
