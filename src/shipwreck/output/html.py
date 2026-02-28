"""HTML report generation for Shipwreck."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from shipwreck.models import Graph


def export_html(graph: Graph, output_path: Path | None = None) -> str:
    """Generate an interactive HTML report for the dependency graph.

    The report is a self-contained HTML file with embedded CSS and JS.
    Uses dagre-d3 for graph layout and d3 for rendering.

    Args:
        graph: The graph to visualize.
        output_path: If provided, write the HTML to this path.

    Returns:
        HTML string.
    """
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    template = env.get_template("report.html.j2")

    # Prepare graph data as JSON for embedding in the template
    graph_data = _prepare_graph_data(graph)

    html = template.render(
        graph=graph,
        graph_data_json=json.dumps(graph_data, indent=2),
        total_images=len(graph.nodes),
        total_edges=len(graph.edges),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html)

    return html


def _prepare_graph_data(graph: Graph) -> dict:
    """Prepare graph data as a JSON-serializable dict for embedding.

    Args:
        graph: The graph.

    Returns:
        Dict with nodes and edges arrays for the JS renderer.
    """
    nodes = []
    for node_id, node in graph.nodes.items():
        nodes.append(
            {
                "id": node_id,
                "label": node.canonical.split("/")[-1],
                "canonical": node.canonical,
                "tags": node.tags_referenced,
                "classification": node.classification or "base",
                "criticality": node.criticality,
                "staleness": node.staleness,
                "sources": [
                    {
                        "repo": s.repo,
                        "file": s.file,
                        "line": s.line,
                        "relationship": s.relationship.value,
                        "tag": s.tag,
                    }
                    for s in node.sources
                ],
            }
        )

    edges = []
    for edge in graph.edges:
        edges.append(
            {
                "source": edge.source,
                "target": edge.target,
                "relationship": edge.relationship.value,
                "confidence": edge.confidence.value,
            }
        )

    return {"nodes": nodes, "edges": edges}
