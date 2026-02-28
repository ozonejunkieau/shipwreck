"""Mermaid diagram generation for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path

from shipwreck.models import EdgeType, Graph, GraphNode

# Mermaid classDef styles per classification
_CLASS_STYLES: dict[str, str] = {
    "base": "fill:#374151,stroke:#6b7280,color:#9ca3af",
    "intermediate": "fill:#1e3a5f,stroke:#3b82f6,color:#bfdbfe",
    "product": "fill:#14532d,stroke:#22c55e,color:#bbf7d0",
    "test": "fill:#422006,stroke:#d97706,color:#fde68a",
}

_EDGE_STYLES: dict[EdgeType, str] = {
    EdgeType.BUILDS_FROM: "-->",
    EdgeType.PRODUCES: "==>",
    EdgeType.CONSUMES: "-.->",
}

_STALENESS_EMOJI: dict[str | None, str] = {
    "current": "✅",
    "behind": "⚠️",
    "major_behind": "🔴",
    "unknown": "❓",
    None: "",
}


def _safe_id(node_id: str) -> str:
    """Make a node_id safe for Mermaid by replacing non-alphanumeric characters.

    Args:
        node_id: The raw node identifier string.

    Returns:
        A sanitized string suitable for use as a Mermaid node ID.
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def _node_label(node: GraphNode) -> str:
    """Build a human-readable Mermaid node label.

    Shows up to three tags (with overflow count), the short image name, and a
    staleness emoji.

    Args:
        node: The graph node to label.

    Returns:
        A label string suitable for embedding in a Mermaid node declaration.
    """
    if node.tags_referenced:
        tags_str = ", ".join(node.tags_referenced[:3])
        if len(node.tags_referenced) > 3:
            tags_str += f" +{len(node.tags_referenced) - 3}"
    else:
        tags_str = "latest"

    staleness = _STALENESS_EMOJI.get(node.staleness, "")
    canonical = node.canonical.split("/")[-1]  # just the image name part
    return f"{canonical}\\n[{tags_str}]{staleness}"


def _build_mermaid(nodes: dict[str, GraphNode], edges_subset: list, all_node_ids: set[str]) -> str:
    """Build a Mermaid flowchart string from the provided nodes and edges.

    Args:
        nodes: Mapping of node id to GraphNode for nodes to include.
        edges_subset: List of GraphEdge objects whose source and target are both in nodes.
        all_node_ids: Set of node ids present in this diagram (used to filter edges).

    Returns:
        A complete Mermaid flowchart string.
    """
    lines: list[str] = ["flowchart LR"]

    # classDef declarations
    for cls_name, style in _CLASS_STYLES.items():
        lines.append(f"    classDef {cls_name} {style}")

    lines.append("")

    # Node declarations
    for node in nodes.values():
        safe = _safe_id(node.id)
        label = _node_label(node)
        cls = node.classification if node.classification in _CLASS_STYLES else None
        cls_suffix = f":::{node.classification}" if cls else ""
        lines.append(f'    {safe}["{label}"]{cls_suffix}')

    lines.append("")

    # Edge declarations — only include edges where both endpoints are in this diagram
    for edge in edges_subset:
        if edge.source in all_node_ids and edge.target in all_node_ids:
            arrow = _EDGE_STYLES.get(edge.relationship, "-->")
            lines.append(f"    {_safe_id(edge.source)} {arrow} {_safe_id(edge.target)}")

    return "\n".join(lines)


def export_mermaid(graph: Graph, output_path: Path | None = None) -> str:
    """Generate a Mermaid flowchart for the full dependency graph.

    Args:
        graph: The graph to export.
        output_path: If provided, write the .mermaid file here.

    Returns:
        Mermaid diagram as a string.
    """
    result = _build_mermaid(graph.nodes, graph.edges, set(graph.nodes.keys()))

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result)

    return result


def export_mermaid_per_repo(graph: Graph, output_dir: Path) -> dict[str, str]:
    """Generate per-repository Mermaid subgraphs.

    For each repo, generates a Mermaid diagram showing only nodes that have
    sources from that repo.

    Args:
        graph: The full graph.
        output_dir: Directory to write per-repo .mermaid files.

    Returns:
        Dict mapping repo name to mermaid string.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect the set of repos across all node sources
    repos: set[str] = set()
    for node in graph.nodes.values():
        for src in node.sources:
            repos.add(src.repo)

    results: dict[str, str] = {}

    for repo in repos:
        # Nodes that have at least one source from this repo
        repo_nodes = {
            node_id: node
            for node_id, node in graph.nodes.items()
            if any(s.repo == repo for s in node.sources)
        }
        repo_node_ids = set(repo_nodes.keys())

        # Edges where both endpoints belong to nodes referenced by this repo
        repo_edges = [e for e in graph.edges if e.source in repo_node_ids and e.target in repo_node_ids]

        diagram = _build_mermaid(repo_nodes, repo_edges, repo_node_ids)

        # Sanitize the repo name for use as a filename
        safe_repo = re.sub(r"[^A-Za-z0-9_\-]", "_", repo)
        out_path = output_dir / f"{safe_repo}.mermaid"
        out_path.write_text(diagram)

        results[repo] = diagram

    return results
