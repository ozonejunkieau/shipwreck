"""Integration tests: graph → all output formats."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import make_graph


class TestJsonOutputIntegration:
    """Integration: graph → JSON → re-parsed dict."""

    def test_json_round_trip(self):
        """Full JSON export + parse produces expected structure."""
        from shipwreck.output.json_export import export_json

        graph = make_graph(3)
        json_str = export_json(graph)
        data = json.loads(json_str)

        assert data["version"] == "1"
        assert "$schema" in data
        assert isinstance(data["nodes"], list)
        assert len(data["nodes"]) == 3
        assert isinstance(data["edges"], list)

    def test_json_node_structure(self):
        """Each node in JSON has required fields."""
        from shipwreck.output.json_export import export_json

        graph = make_graph(2)
        data = json.loads(export_json(graph))

        required_fields = {
            "id", "canonical", "tags_referenced", "classification",
            "criticality", "sources", "variants", "registry_metadata",
        }
        for node in data["nodes"]:
            for field in required_fields:
                assert field in node, f"Node missing field: {field}"

    def test_json_edge_structure(self):
        """Each edge in JSON has required fields."""
        from shipwreck.output.json_export import export_json

        graph = make_graph(2)
        data = json.loads(export_json(graph))

        assert len(data["edges"]) > 0
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "relationship" in edge
            assert edge["relationship"] in ("builds_from", "produces", "consumes")

    def test_json_written_to_disk(self, tmp_path: Path):
        """Graph JSON is written to the specified path."""
        from shipwreck.output.json_export import export_json

        graph = make_graph(2)
        out = tmp_path / "output" / "shipwreck.json"
        export_json(graph, output_path=out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert len(data["nodes"]) == 2

    def test_json_snapshot_round_trip(self, tmp_path: Path):
        """Save and reload snapshot preserves all nodes and edges."""
        from shipwreck.output.snapshot import load_snapshot, save_snapshot

        graph = make_graph(3)
        snap_dir = tmp_path / "snapshots"
        path = save_snapshot(graph, snap_dir)
        loaded = load_snapshot(path)

        assert len(loaded.nodes) == len(graph.nodes)
        assert len(loaded.edges) == len(graph.edges)
        assert loaded.generated_at == graph.generated_at


class TestMermaidOutputIntegration:
    """Integration: graph → Mermaid."""

    def test_mermaid_full_graph(self):
        """Full graph Mermaid output starts with flowchart."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_graph(3)
        result = export_mermaid(graph)
        assert result.startswith("flowchart")

    def test_mermaid_contains_all_nodes(self):
        """Mermaid output contains identifiers for all graph nodes."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_graph(2)
        result = export_mermaid(graph)
        for node_id in graph.nodes:
            # Node id is sanitized for Mermaid — at least part of it should appear
            name_part = node_id.split("/")[-1].replace("-", "_")
            assert name_part in result, f"Node {node_id} not found in Mermaid output"

    def test_mermaid_written_to_disk(self, tmp_path: Path):
        """Mermaid file is written to the specified path."""
        from shipwreck.output.mermaid import export_mermaid

        graph = make_graph(2)
        out = tmp_path / "shipwreck.mermaid"
        export_mermaid(graph, output_path=out)
        assert out.exists()
        assert "flowchart" in out.read_text()

    def test_mermaid_per_repo(self, tmp_path: Path):
        """Per-repo Mermaid files are generated for each unique source repo."""
        from shipwreck.output.mermaid import export_mermaid_per_repo

        graph = make_graph(2)
        per_repo_dir = tmp_path / "per-repo"
        result = export_mermaid_per_repo(graph, per_repo_dir)
        assert isinstance(result, dict)
        # All repos represented in the graph should have a file
        repos = {s.repo for n in graph.nodes.values() for s in n.sources}
        for repo in repos:
            assert repo in result


class TestHtmlOutputIntegration:
    """Integration: graph → HTML."""

    def test_html_is_well_formed(self):
        """HTML output is a valid-ish HTML document."""
        from shipwreck.output.html import export_html

        graph = make_graph(2)
        html = export_html(graph)
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html

    def test_html_embeds_graph_data(self):
        """HTML output embeds GRAPH_DATA JSON."""
        from shipwreck.output.html import export_html

        graph = make_graph(2)
        html = export_html(graph)
        assert "GRAPH_DATA" in html

    def test_html_written_to_disk(self, tmp_path: Path):
        """HTML report is written to the specified path."""
        from shipwreck.output.html import export_html

        graph = make_graph(2)
        out = tmp_path / "shipwreck.html"
        export_html(graph, output_path=out)
        assert out.exists()
        assert "<!DOCTYPE html>" in out.read_text()


class TestSnapshotDiff:
    """Integration: snapshot diff."""

    def test_diff_detects_added_nodes(self):
        """diff_snapshots detects newly added nodes."""
        from shipwreck.models import GraphNode
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_graph(2)
        g2 = make_graph(2)
        g2.nodes["new.example.com/extra"] = GraphNode(
            id="new.example.com/extra",
            canonical="new.example.com/extra",
            tags_referenced=["1.0"],
        )

        diff = diff_snapshots(g1, g2)
        assert "new.example.com/extra" in diff["changes"]["added_images"]

    def test_diff_detects_removed_nodes(self):
        """diff_snapshots detects removed nodes."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_graph(2)
        g2 = make_graph(1)

        diff = diff_snapshots(g1, g2)
        assert len(diff["changes"]["removed_images"]) > 0

    def test_diff_detects_tag_changes(self):
        """diff_snapshots detects version/tag changes on unchanged nodes."""
        from shipwreck.output.snapshot import diff_snapshots

        g1 = make_graph(2)
        g2 = make_graph(2)

        # Modify a tag on one node
        first_node_id = list(g2.nodes.keys())[0]
        g2.nodes[first_node_id].tags_referenced = ["2.0"]

        diff = diff_snapshots(g1, g2)
        version_changes = diff["changes"]["version_changes"]
        assert any(vc["image"] == first_node_id for vc in version_changes)
