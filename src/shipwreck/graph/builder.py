"""Build a dependency graph from collected image references."""

from __future__ import annotations

from collections import defaultdict

from shipwreck.config import ShipwreckConfig
from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    GraphSummary,
    ImageReference,
    ImageSource,
)


def build_graph(
    references: list[ImageReference],
    config: ShipwreckConfig,
    generated_at: str = "",
) -> Graph:
    """Build a dependency graph from a list of image references.

    Nodes are keyed by canonical image name (registry/name, without tag).
    Tags are accumulated per node.

    Each reference is used to create or update a node. Edges are created by
    pairing PRODUCES and BUILDS_FROM references from the same file context:

    - If a file has both PRODUCES and BUILDS_FROM refs, create BUILDS_FROM
      edges from each produced-image node to each base-image node.
    - If a file has only BUILDS_FROM refs (no paired PRODUCES), create a
      synthetic source node keyed by ``file:{repo}:{file_path}``.

    Args:
        references: All image references collected by parsers.
        config: Shipwreck configuration (for aliases, classification).
        generated_at: ISO8601 timestamp for the graph metadata.

    Returns:
        A populated Graph instance.
    """
    graph = Graph(generated_at=generated_at)

    # Group references by (repo, file) for edge construction.
    by_file: dict[tuple[str, str], list[ImageReference]] = defaultdict(list)
    for ref in references:
        by_file[(ref.source.repo, ref.source.file)].append(ref)

    # --- Pass 1: Create / update image nodes ---
    for ref in references:
        node_id = _make_node_id(ref)
        if node_id not in graph.nodes:
            graph.nodes[node_id] = GraphNode(id=node_id, canonical=node_id)
        node = graph.nodes[node_id]

        if ref.tag and ref.tag not in node.tags_referenced:
            node.tags_referenced.append(ref.tag)

        node.sources.append(
            ImageSource(
                repo=ref.source.repo,
                file=ref.source.file,
                line=ref.source.line,
                relationship=ref.relationship,
                tag=ref.tag,
            )
        )

    # --- Pass 2: Create edges from file-level groupings ---
    for (repo, file_path), file_refs in by_file.items():
        produces_refs = [r for r in file_refs if r.relationship == EdgeType.PRODUCES]
        builds_from_refs = [r for r in file_refs if r.relationship == EdgeType.BUILDS_FROM]

        if produces_refs and builds_from_refs:
            # Pair each produced image with each base image.
            for p_ref in produces_refs:
                for b_ref in builds_from_refs:
                    p_id = _make_node_id(p_ref)
                    b_id = _make_node_id(b_ref)
                    if p_id == b_id:
                        continue
                    graph.edges.append(
                        GraphEdge(
                            source=p_id,
                            target=b_id,
                            relationship=EdgeType.BUILDS_FROM,
                            confidence=_min_confidence(p_ref, b_ref),
                            source_location=b_ref.source,
                        )
                    )

        elif builds_from_refs:
            # No paired PRODUCES ref — use a synthetic source node for this file.
            synthetic_id = f"file:{repo}:{file_path}"
            if synthetic_id not in graph.nodes:
                graph.nodes[synthetic_id] = GraphNode(id=synthetic_id, canonical=synthetic_id)

            for b_ref in builds_from_refs:
                b_id = _make_node_id(b_ref)
                graph.edges.append(
                    GraphEdge(
                        source=synthetic_id,
                        target=b_id,
                        relationship=EdgeType.BUILDS_FROM,
                        confidence=b_ref.confidence,
                        source_location=b_ref.source,
                    )
                )

    _update_summary(graph)
    return graph


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CONFIDENCE_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


def _make_node_id(ref: ImageReference) -> str:
    """Return the canonical node identifier for an image reference.

    Args:
        ref: The image reference to identify.

    Returns:
        ``"{registry}/{name}"`` when both fields are resolved, or ``ref.raw``
        for unresolved template strings.
    """
    if ref.registry and ref.name:
        return f"{ref.registry}/{ref.name}"
    return ref.raw


def _min_confidence(a: ImageReference, b: ImageReference) -> Confidence:
    """Return the lower of two confidence values.

    Args:
        a: First image reference.
        b: Second image reference.

    Returns:
        The confidence value with the higher index in (high, medium, low).
    """
    if _CONFIDENCE_ORDER[a.confidence.value] >= _CONFIDENCE_ORDER[b.confidence.value]:
        return a.confidence
    return b.confidence


def _update_summary(graph: Graph) -> None:
    """Recompute and assign the graph summary statistics.

    Args:
        graph: The graph to summarise (modified in-place).
    """
    unresolved = sum(
        1 for n in graph.nodes.values() if "{{" in n.id or "${" in n.id
    )

    classification_counts: dict[str, int] = {}
    for node in graph.nodes.values():
        if node.classification:
            classification_counts[node.classification] = (
                classification_counts.get(node.classification, 0) + 1
            )

    graph.summary = GraphSummary(
        total_images=len(graph.nodes),
        unresolved_references=unresolved,
        classification_counts=classification_counts,
    )
