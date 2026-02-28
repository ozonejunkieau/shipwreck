"""Unit tests for the Shipwreck output layer (JSON export, Mermaid, snapshots)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    ImageSource,
    SourceLocation,
)


def make_test_graph() -> Graph:
    """Create a minimal test graph."""
    graph = Graph(generated_at="2025-01-01T00:00:00Z")
    src = SourceLocation(repo="test-repo", file="Dockerfile", line=1, parser="dockerfile")
    graph.nodes["docker.io/library/python"] = GraphNode(
        id="docker.io/library/python",
        canonical="docker.io/library/python",
        tags_referenced=["3.12-slim"],
        classification="base",
        criticality=2.0,
        sources=[
            ImageSource(
                repo="test-repo",
                file="Dockerfile",
                line=1,
                relationship=EdgeType.BUILDS_FROM,
                tag="3.12-slim",
            )
        ],
    )
    graph.nodes["registry.example.com/myapp"] = GraphNode(
        id="registry.example.com/myapp",
        canonical="registry.example.com/myapp",
        tags_referenced=["1.0", "latest"],
        classification="product",
        criticality=0.0,
        sources=[
            ImageSource(
                repo="test-repo",
                file="docker-bake.hcl",
                line=5,
                relationship=EdgeType.PRODUCES,
                tag="1.0",
            )
        ],
    )
    graph.edges.append(
        GraphEdge(
            source="registry.example.com/myapp",
            target="docker.io/library/python",
            relationship=EdgeType.BUILDS_FROM,
            confidence=Confidence.HIGH,
            source_location=src,
        )
    )
    graph.summary.total_images = 2
    return graph


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


class TestJsonExport:
    def test_json_is_valid(self):
        """Exported JSON is valid JSON."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        result = export_json(graph)
        data = json.loads(result)
        assert data["version"] == "1"

    def test_nodes_as_list(self):
        """JSON nodes field is a list, not a dict."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        assert isinstance(data["nodes"], list)

    def test_schema_url(self):
        """The $schema key is present and correct."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        assert data["$schema"] == "https://shipwreck.dev/schema/v1.json"

    def test_edges_serialized(self):
        """Edges are present and relationship is serialized as a string value."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        assert len(data["edges"]) == 1
        assert data["edges"][0]["relationship"] == "builds_from"

    def test_write_to_file(self, tmp_path: Path):
        """export_json writes the file when output_path is given."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        out = tmp_path / "graph.json"
        export_json(graph, output_path=out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "nodes" in data

    def test_node_count_matches(self):
        """Number of nodes in the JSON list equals the graph node dict size."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        assert len(data["nodes"]) == len(graph.nodes)

    def test_node_fields_present(self):
        """Each node object contains the required top-level fields."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        required_fields = {
            "id",
            "canonical",
            "tags_referenced",
            "latest_available",
            "staleness",
            "version_scheme",
            "classification",
            "criticality",
            "registry_metadata",
            "variants",
            "sources",
        }
        for node in data["nodes"]:
            assert required_fields.issubset(node.keys())

    def test_source_relationship_is_string(self):
        """ImageSource.relationship is serialized as its string value, not enum name."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        for node in data["nodes"]:
            for src in node["sources"]:
                assert isinstance(src["relationship"], str)
                assert src["relationship"] in {"builds_from", "produces", "consumes"}

    def test_summary_included(self):
        """The summary block is present in the output."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        data = json.loads(export_json(graph))
        assert "summary" in data
        assert data["summary"]["total_images"] == 2

    def test_creates_parent_dirs(self, tmp_path: Path):
        """export_json creates missing parent directories automatically."""
        from shipwreck.output.json_export import export_json

        graph = make_test_graph()
        out = tmp_path / "deep" / "nested" / "graph.json"
        export_json(graph, output_path=out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


class TestMermaid:
    def test_mermaid_has_flowchart_header(self):
        """Output begins with the 'flowchart' keyword."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        result = export_mermaid(graph)
        assert result.startswith("flowchart")

    def test_mermaid_contains_node_ids(self):
        """Output contains a recognisable portion of the node canonical names."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        result = export_mermaid(graph)
        assert "myapp" in result or "registry" in result

    def test_mermaid_edge_syntax(self):
        """Output contains at least one Mermaid edge arrow."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        result = export_mermaid(graph)
        assert "-->" in result or "==>" in result or ".->" in result

    def test_mermaid_classdef_present(self):
        """Mermaid output declares classDef blocks for known classifications."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        result = export_mermaid(graph)
        assert "classDef base" in result
        assert "classDef product" in result

    def test_mermaid_write_to_file(self, tmp_path: Path):
        """export_mermaid writes the file when output_path is provided."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        out = tmp_path / "graph.mermaid"
        export_mermaid(graph, output_path=out)
        assert out.exists()
        assert out.read_text().startswith("flowchart")

    def test_mermaid_per_repo_creates_files(self, tmp_path: Path):
        """export_mermaid_per_repo creates one file per repo."""
        from shipwreck.output.mermaid import export_mermaid_per_repo

        graph = make_test_graph()
        results = export_mermaid_per_repo(graph, tmp_path)
        assert "test-repo" in results
        assert (tmp_path / "test-repo.mermaid").exists()

    def test_mermaid_per_repo_content(self, tmp_path: Path):
        """Per-repo Mermaid diagrams start with the flowchart header."""
        from shipwreck.output.mermaid import export_mermaid_per_repo

        graph = make_test_graph()
        results = export_mermaid_per_repo(graph, tmp_path)
        for diagram in results.values():
            assert diagram.startswith("flowchart")

    def test_mermaid_classification_suffix(self):
        """Node declarations include :::classname for known classifications."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_test_graph()
        result = export_mermaid(graph)
        assert ":::base" in result or ":::product" in result

    def test_safe_id_no_special_chars(self):
        """_safe_id produces strings containing only alphanumeric characters and underscores."""
        from shipwreck.output.mermaid import _safe_id

        raw = "registry.example.com/my-app:1.0"
        safe = _safe_id(raw)
        import re

        assert re.fullmatch(r"[A-Za-z0-9_]+", safe)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        """A saved snapshot can be loaded back and has the same node count."""
        from shipwreck.output.snapshot import load_snapshot, save_snapshot

        graph = make_test_graph()
        path = save_snapshot(graph, tmp_path / "snapshots")
        assert path.exists()
        loaded = load_snapshot(path)
        assert len(loaded.nodes) == len(graph.nodes)

    def test_find_latest_snapshot(self, tmp_path: Path):
        """find_latest_snapshot returns the most recently created file."""
        from shipwreck.output.snapshot import find_latest_snapshot, save_snapshot

        graph = make_test_graph()
        snap_dir = tmp_path / "snapshots"
        save_snapshot(graph, snap_dir)
        latest = find_latest_snapshot(snap_dir)
        assert latest is not None

    def test_find_latest_snapshot_empty_dir(self, tmp_path: Path):
        """find_latest_snapshot returns None when the directory is empty."""
        from shipwreck.output.snapshot import find_latest_snapshot

        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        assert find_latest_snapshot(snap_dir) is None

    def test_find_latest_snapshot_missing_dir(self, tmp_path: Path):
        """find_latest_snapshot returns None when the directory does not exist."""
        from shipwreck.output.snapshot import find_latest_snapshot

        assert find_latest_snapshot(tmp_path / "nonexistent") is None

    def test_load_snapshot_missing_file(self, tmp_path: Path):
        """load_snapshot raises FileNotFoundError for a missing file."""
        from shipwreck.output.snapshot import load_snapshot

        with pytest.raises(FileNotFoundError):
            load_snapshot(tmp_path / "missing.json")

    def test_diff_snapshots_added(self):
        """diff_snapshots detects newly added images."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_test_graph()
        g2 = make_test_graph()
        g2.nodes["new-image"] = GraphNode(id="new-image", canonical="new-image")
        diff = diff_snapshots(g1, g2)
        assert "new-image" in diff["changes"]["added_images"]

    def test_diff_snapshots_removed(self):
        """diff_snapshots detects removed images."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_test_graph()
        g2 = make_test_graph()
        del g2.nodes["docker.io/library/python"]
        diff = diff_snapshots(g1, g2)
        assert "docker.io/library/python" in diff["changes"]["removed_images"]

    def test_diff_snapshots_version_change(self):
        """diff_snapshots detects tag changes on existing images."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_test_graph()
        g2 = make_test_graph()
        g2.nodes["docker.io/library/python"].tags_referenced = ["3.13-slim"]
        diff = diff_snapshots(g1, g2)
        assert any(v["image"] == "docker.io/library/python" for v in diff["changes"]["version_changes"])

    def test_diff_snapshots_staleness_change(self):
        """diff_snapshots detects staleness status changes."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_test_graph()
        g2 = make_test_graph()
        g2.nodes["docker.io/library/python"].staleness = "behind"
        diff = diff_snapshots(g1, g2)
        assert any(s["image"] == "docker.io/library/python" for s in diff["changes"]["staleness_changes"])

    def test_diff_snapshots_no_changes(self):
        """diff_snapshots reports empty change lists for identical graphs."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_test_graph()
        g2 = make_test_graph()
        diff = diff_snapshots(g1, g2)
        changes = diff["changes"]
        assert changes["added_images"] == []
        assert changes["removed_images"] == []
        assert changes["version_changes"] == []
        assert changes["staleness_changes"] == []

    def test_roundtrip_preserves_edges(self, tmp_path: Path):
        """Loaded snapshot retains the same number of edges as the original."""
        from shipwreck.output.snapshot import load_snapshot, save_snapshot

        graph = make_test_graph()
        path = save_snapshot(graph, tmp_path / "snapshots")
        loaded = load_snapshot(path)
        assert len(loaded.edges) == len(graph.edges)

    def test_roundtrip_preserves_schema_url(self, tmp_path: Path):
        """Loaded snapshot retains the schema_url field."""
        from shipwreck.output.snapshot import load_snapshot, save_snapshot

        graph = make_test_graph()
        path = save_snapshot(graph, tmp_path / "snapshots")
        loaded = load_snapshot(path)
        assert loaded.schema_url == "https://shipwreck.dev/schema/v1.json"

    def test_snapshot_filename_format(self, tmp_path: Path):
        """Saved snapshot filename matches the expected timestamp pattern."""
        import re

        from shipwreck.output.snapshot import save_snapshot

        graph = make_test_graph()
        snap_dir = tmp_path / "snapshots"
        path = save_snapshot(graph, snap_dir)
        assert re.match(r"\d{8}T\d{6}Z\.json$", path.name)
