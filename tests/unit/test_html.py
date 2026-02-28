"""Tests for the HTML report output."""

from __future__ import annotations

from pathlib import Path

from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    GraphSummary,
    ImageSource,
    SourceLocation,
)

# ── Fixture ───────────────────────────────────────────────────────────────────


def make_test_graph() -> Graph:
    """Build a minimal Graph suitable for HTML export tests."""
    python_node = GraphNode(
        id="library/python",
        canonical="library/python",
        tags_referenced=["3.12-slim", "3.11-slim"],
        classification="base",
        criticality=8.5,
        staleness="stale",
        sources=[
            ImageSource(
                repo="my-org/myapp",
                file="Dockerfile",
                line=1,
                relationship=EdgeType.BUILDS_FROM,
                tag="3.12-slim",
            )
        ],
    )
    myapp_node = GraphNode(
        id="my-org/myapp",
        canonical="my-org/myapp",
        tags_referenced=["latest", "1.0.0"],
        classification="product",
        criticality=5.0,
        staleness=None,
        sources=[
            ImageSource(
                repo="my-org/myapp",
                file="docker-compose.yml",
                line=10,
                relationship=EdgeType.PRODUCES,
                tag="latest",
            )
        ],
    )
    test_node = GraphNode(
        id="my-org/myapp-test",
        canonical="my-org/myapp-test",
        tags_referenced=["ci"],
        classification="test",
        criticality=1.0,
        staleness=None,
        sources=[],
    )
    edge = GraphEdge(
        source="library/python",
        target="my-org/myapp",
        relationship=EdgeType.BUILDS_FROM,
        confidence=Confidence.HIGH,
        source_location=SourceLocation(
            repo="my-org/myapp",
            file="Dockerfile",
            line=1,
            parser="dockerfile",
        ),
    )
    return Graph(
        generated_at="2026-02-27T00:00:00Z",
        nodes={
            "library/python": python_node,
            "my-org/myapp": myapp_node,
            "my-org/myapp-test": test_node,
        },
        edges=[edge],
        summary=GraphSummary(total_images=3, stale_images=1, classification_counts={"base": 1, "product": 1, "test": 1}),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_html_is_valid_html() -> None:
    """Export produces a document that starts with the HTML doctype declaration."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "<!DOCTYPE html>" in result
    assert "<html" in result
    assert "</html>" in result


def test_html_contains_node_data() -> None:
    """Node canonical names are embedded somewhere in the output."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "myapp" in result
    assert "python" in result


def test_html_contains_graph_data_json() -> None:
    """The GRAPH_DATA JavaScript variable is present in the output."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "GRAPH_DATA" in result


def test_html_embeds_stats() -> None:
    """Total image and edge counts appear in the rendered HTML."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "3" in result   # total_images
    assert "1" in result   # total_edges


def test_html_write_to_file(tmp_path: Path) -> None:
    """When output_path is given the HTML file is written to disk."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    out = tmp_path / "report.html"
    export_html(graph, output_path=out)

    assert out.exists()
    content = out.read_text()
    assert "<!DOCTYPE html>" in content


def test_html_write_creates_parent_dirs(tmp_path: Path) -> None:
    """export_html creates any missing parent directories automatically."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    out = tmp_path / "nested" / "deep" / "report.html"
    export_html(graph, output_path=out)

    assert out.exists()


def test_html_returns_same_as_file(tmp_path: Path) -> None:
    """The returned string matches what was written to disk."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    out = tmp_path / "report.html"
    result = export_html(graph, output_path=out)

    assert result == out.read_text()


def test_html_generated_at_present() -> None:
    """The graph generation timestamp is embedded in the output."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "2026-02-27T00:00:00Z" in result


def test_html_classification_classes_present() -> None:
    """The CSS classes for each classification type exist in the output."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    result = export_html(graph)

    assert "node-product" in result
    assert "node-base" in result
    assert "node-test" in result


def test_html_empty_graph() -> None:
    """An empty graph produces valid HTML without errors."""
    from shipwreck.output.html import export_html

    graph = Graph(generated_at="2026-02-27T00:00:00Z")
    result = export_html(graph)

    assert "<!DOCTYPE html>" in result
    assert "GRAPH_DATA" in result


def test_html_prepare_graph_data_structure() -> None:
    """_prepare_graph_data returns the expected shape."""
    from shipwreck.output.html import _prepare_graph_data

    graph = make_test_graph()
    data = _prepare_graph_data(graph)

    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 1


def test_html_node_fields_in_json() -> None:
    """Each node dict in the prepared data has the required fields."""
    from shipwreck.output.html import _prepare_graph_data

    graph = make_test_graph()
    data = _prepare_graph_data(graph)

    required_fields = {"id", "label", "canonical", "tags", "classification", "criticality", "staleness", "sources"}
    for node in data["nodes"]:
        assert required_fields <= node.keys(), f"Missing fields in node {node!r}"


def test_html_edge_fields_in_json() -> None:
    """Each edge dict in the prepared data has the required fields."""
    from shipwreck.output.html import _prepare_graph_data

    graph = make_test_graph()
    data = _prepare_graph_data(graph)

    required_fields = {"source", "target", "relationship", "confidence"}
    for edge in data["edges"]:
        assert required_fields <= edge.keys(), f"Missing fields in edge {edge!r}"


def test_html_label_is_last_path_segment() -> None:
    """Node label is the final slash-separated segment of the canonical name."""
    from shipwreck.output.html import _prepare_graph_data

    graph = make_test_graph()
    data = _prepare_graph_data(graph)

    node_by_id = {n["id"]: n for n in data["nodes"]}
    assert node_by_id["library/python"]["label"] == "python"
    assert node_by_id["my-org/myapp"]["label"] == "myapp"


def test_html_no_output_path_does_not_create_file(tmp_path: Path) -> None:
    """Without output_path no file is written."""
    from shipwreck.output.html import export_html

    graph = make_test_graph()
    export_html(graph, output_path=None)

    # Nothing in tmp_path
    assert list(tmp_path.iterdir()) == []
