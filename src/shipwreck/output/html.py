"""HTML report generation for Shipwreck."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from shipwreck.models import Graph, ImageSource


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

    # Compute staleness summary counts
    staleness_counts = _compute_staleness_counts(graph)

    # Compute staleness percentages for the visual bar (avoid division by zero)
    total = len(graph.nodes)
    if total > 0:
        staleness_pct = {
            k: round(v / total * 100, 1)
            for k, v in staleness_counts.items()
        }
    else:
        staleness_pct = {k: 0.0 for k in staleness_counts}

    # Unique classifications (in a stable, sorted order)
    classifications = sorted(
        {n.classification or "unknown" for n in graph.nodes.values()}
    )

    # Unique registries extracted from canonical names
    registries = _extract_registries(graph)

    # Source file entries for the source filter sidebar
    source_files = _extract_source_files(graph)

    # Source tree: files grouped by type for collapsible tree view
    source_tree = _build_source_tree(source_files)

    html = template.render(
        graph=graph,
        graph_data_json=json.dumps(graph_data, indent=2),
        staleness_counts=staleness_counts,
        staleness_counts_json=json.dumps(staleness_counts),
        staleness_pct=staleness_pct,
        classifications=classifications,
        classifications_json=json.dumps(classifications),
        registries=registries,
        registries_json=json.dumps(registries),
        source_files=source_files,
        source_files_json=json.dumps(source_files),
        source_tree=source_tree,
        source_tree_json=json.dumps(source_tree),
        total_images=len(graph.nodes),
        total_edges=len(graph.edges),
        warnings=graph.warnings,
        warnings_json=json.dumps(graph.warnings),
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
                "classification": node.classification or "unknown",
                "criticality": node.criticality,
                "staleness": node.staleness,
                "sources": _dedup_sources(node.sources),
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


def _classify_source_file(file_path: str) -> str:
    """Classify a source file path into a human-readable category."""
    name = file_path.rsplit("/", 1)[-1].lower()
    path_lower = file_path.lower()

    if name.startswith("dockerfile") or name == "containerfile":
        return "Dockerfile"
    if name in (
        "docker-bake.hcl",
        "docker-bake.override.hcl",
    ):
        return "Bake"
    if name in (
        "compose.yaml",
        "compose.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
    ) or "docker-compose" in name:
        return "Compose"
    if ".gitlab-ci" in name or "/.gitlab-ci/" in path_lower:
        return "GitLab CI"
    if ".github/workflows/" in path_lower:
        return "GitHub Actions"
    if any(
        seg in path_lower
        for seg in ("/tasks/", "/roles/", "/handlers/", "/playbooks/", "/plays/")
    ):
        return "Ansible"
    if name.endswith((".yaml", ".yml")):
        return "YAML"
    return "Other"


def _dedup_sources(sources: list[ImageSource]) -> list[dict]:
    """Deduplicate sources by (repo, file, line, relationship, tag)."""
    seen: set[tuple] = set()
    result: list[dict] = []
    for s in sources:
        key = (s.repo, s.file, s.line, s.relationship.value, s.tag)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "repo": s.repo,
                "file": s.file,
                "line": s.line,
                "relationship": s.relationship.value,
                "tag": s.tag,
                "file_type": _classify_source_file(s.file),
            }
        )
    return result


def _compute_staleness_counts(graph: Graph) -> dict[str, int]:
    """Count nodes by staleness category.

    Args:
        graph: The graph.

    Returns:
        Dict mapping staleness category to count.
    """
    counts: dict[str, int] = {
        "current": 0,
        "behind": 0,
        "major_behind": 0,
        "unknown": 0,
    }
    for node in graph.nodes.values():
        key = node.staleness if node.staleness in counts else "unknown"
        counts[key] += 1
    return counts


def _extract_registries(graph: Graph) -> list[str]:
    """Extract unique registry hostnames from node canonical names.

    A registry is identified as the first path segment of a canonical name
    when that segment contains a dot or colon (e.g. ``gcr.io``,
    ``registry.example.com:5000``).

    Args:
        graph: The graph.

    Returns:
        Sorted list of unique registry strings.
    """
    registries: set[str] = set()
    for node in graph.nodes.values():
        canonical = node.canonical or ""
        slash = canonical.find("/")
        if slash > 0:
            first = canonical[:slash]
            if "." in first or ":" in first:
                registries.add(first)
    return sorted(registries)


def _extract_source_repos(graph: Graph) -> list[str]:
    """Extract unique source repo names from all nodes.

    Returns:
        Sorted list of unique repo strings found in node sources.
    """
    repos: set[str] = set()
    for node in graph.nodes.values():
        for s in node.sources:
            repos.add(s.repo)
    return sorted(repos)


def _extract_source_types(graph: Graph) -> list[str]:
    """Extract unique source file type categories from all nodes.

    Returns:
        Sorted list of unique file type strings found in node sources.
    """
    types: set[str] = set()
    for node in graph.nodes.values():
        for s in node.sources:
            types.add(_classify_source_file(s.file))
    return sorted(types)


def _extract_source_files(graph: Graph) -> list[dict]:
    """Extract unique source file entries for the sidebar filter.

    Returns:
        List of dicts with key, repo, file, and file_type, sorted by key.
    """
    seen: set[str] = set()
    entries: list[dict] = []
    for node in graph.nodes.values():
        for s in node.sources:
            key = s.repo + "/" + s.file
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "key": key,
                    "repo": s.repo,
                    "file": s.file,
                    "file_type": _classify_source_file(s.file),
                }
            )
    return sorted(entries, key=lambda e: e["key"])


def _build_source_tree(source_files: list[dict]) -> dict[str, list[dict]]:
    """Group source file entries by file_type for the tree view.

    Args:
        source_files: Flat list of source file dicts (from _extract_source_files).

    Returns:
        Dict mapping file_type → list of source file dicts, keys sorted.
    """
    tree: dict[str, list[dict]] = {}
    for sf in source_files:
        ft = sf["file_type"]
        tree.setdefault(ft, []).append(sf)
    return dict(sorted(tree.items()))
