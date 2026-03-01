"""JSON metadata export for Shipwreck."""

from __future__ import annotations

import json
from pathlib import Path

from shipwreck.models import Graph


def export_json(graph: Graph, output_path: Path | None = None) -> str:
    """Export the graph to JSON format.

    Produces the full metadata JSON per §4.3 of the spec. Schema URL is
    "https://shipwreck.dev/schema/v1.json".

    Args:
        graph: The graph to export.
        output_path: If provided, write the JSON to this path.

    Returns:
        The JSON string.
    """
    data = {
        "$schema": graph.schema_url,
        "version": graph.version,
        "generated_at": graph.generated_at,
        "config_hash": graph.config_hash,
        "environment": graph.environment.model_dump(),
        "nodes": [
            {
                "id": node.id,
                "canonical": node.canonical,
                "tags_referenced": node.tags_referenced,
                "latest_available": node.latest_available,
                "staleness": node.staleness,
                "version_scheme": node.version_scheme,
                "classification": node.classification,
                "criticality": node.criticality,
                "registry_metadata": node.registry_metadata.model_dump(),
                "variants": [v.model_dump() for v in node.variants],
                "sources": [
                    {
                        "repo": s.repo,
                        "file": s.file,
                        "line": s.line,
                        "relationship": s.relationship.value,
                        "tag": s.tag,
                        "resolution": s.resolution,
                    }
                    for s in node.sources
                ],
            }
            for node in graph.nodes.values()
        ],
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "relationship": e.relationship.value,
                "confidence": e.confidence.value,
                "source_location": {
                    "repo": e.source_location.repo,
                    "file": e.source_location.file,
                    "line": e.source_location.line,
                    "parser": e.source_location.parser,
                },
            }
            for e in graph.edges
        ],
        "summary": graph.summary.model_dump(),
        "warnings": graph.warnings,
    }
    result = json.dumps(data, indent=2, default=str)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result)
    return result
