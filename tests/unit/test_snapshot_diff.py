"""Tests for enhanced snapshot diff and log command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from shipwreck.cli import app
from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    RegistryMetadata,
    SourceLocation,
)
from shipwreck.output.snapshot import diff_snapshots, save_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _src() -> SourceLocation:
    return SourceLocation(repo="test-repo", file="Dockerfile", line=1, parser="dockerfile")


def _make_two_node_graph(generated_at: str = "2025-01-01T00:00:00Z") -> Graph:
    """Two-node graph: app builds_from base."""
    graph = Graph(generated_at=generated_at)
    graph.nodes["example.com/base"] = GraphNode(
        id="example.com/base",
        canonical="example.com/base",
        tags_referenced=["1.0"],
        registry_metadata=RegistryMetadata(size_bytes=100, build_date="2025-01-01", digest="sha256:aaa"),
    )
    graph.nodes["example.com/app"] = GraphNode(
        id="example.com/app",
        canonical="example.com/app",
        tags_referenced=["2.0"],
        registry_metadata=RegistryMetadata(size_bytes=200, build_date="2025-01-02", digest="sha256:bbb"),
    )
    graph.edges.append(
        GraphEdge(
            source="example.com/app",
            target="example.com/base",
            relationship=EdgeType.BUILDS_FROM,
            confidence=Confidence.HIGH,
            source_location=_src(),
        )
    )
    graph.summary.total_images = 2
    return graph


def _save_two_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    """Save two snapshots to tmp_path/snapshots and return (older, newer) paths.

    Uses hard-coded timestamp names so the pair is always lexicographically ordered
    regardless of how fast the test runs.
    """
    from shipwreck.output.json_export import export_json

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    g1 = _make_two_node_graph("2025-01-01T00:00:00Z")
    p1 = snap_dir / "20250101T000000Z.json"
    export_json(g1, output_path=p1)

    g2 = _make_two_node_graph("2025-01-02T00:00:00Z")
    p2 = snap_dir / "20250102T000000Z.json"
    export_json(g2, output_path=p2)

    return p1, p2


# ---------------------------------------------------------------------------
# Tests — diff_snapshots enhancements
# ---------------------------------------------------------------------------


class TestMetadataChanges:
    def test_metadata_changes_detected(self):
        """Changes in registry_metadata are captured in metadata_changes."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].registry_metadata = RegistryMetadata(
            size_bytes=999, build_date="2025-06-01", digest="sha256:new"
        )

        diff = diff_snapshots(g1, g2)
        meta = diff["changes"]["metadata_changes"]

        images = [m["image"] for m in meta]
        assert "example.com/base" in images

        fields = {m["field"] for m in meta if m["image"] == "example.com/base"}
        assert fields == {"size_bytes", "build_date", "digest"}

    def test_metadata_changes_only_when_different(self):
        """Identical metadata produces no metadata_changes entries."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()

        diff = diff_snapshots(g1, g2)
        assert diff["changes"]["metadata_changes"] == []

    def test_metadata_change_records_previous_and_current(self):
        """Each metadata_change entry has image, field, previous, and current."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].registry_metadata = RegistryMetadata(size_bytes=500)

        diff = diff_snapshots(g1, g2)
        size_entries = [
            m for m in diff["changes"]["metadata_changes"]
            if m["image"] == "example.com/base" and m["field"] == "size_bytes"
        ]
        assert len(size_entries) == 1
        entry = size_entries[0]
        assert entry["previous"] == 100
        assert entry["current"] == 500

    def test_metadata_change_none_to_value(self):
        """Transition from None to a value is reported."""
        g1 = _make_two_node_graph()
        g1.nodes["example.com/base"].registry_metadata = RegistryMetadata()  # all None
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].registry_metadata = RegistryMetadata(digest="sha256:xyz")

        diff = diff_snapshots(g1, g2)
        digest_entries = [
            m for m in diff["changes"]["metadata_changes"]
            if m["field"] == "digest" and m["image"] == "example.com/base"
        ]
        assert len(digest_entries) == 1
        assert digest_entries[0]["previous"] is None
        assert digest_entries[0]["current"] == "sha256:xyz"

    def test_metadata_both_none_not_reported(self):
        """Fields that are None in both snapshots are not reported."""
        g1 = _make_two_node_graph()
        g1.nodes["example.com/base"].registry_metadata = RegistryMetadata()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].registry_metadata = RegistryMetadata()

        diff = diff_snapshots(g1, g2)
        base_entries = [
            m for m in diff["changes"]["metadata_changes"]
            if m["image"] == "example.com/base"
        ]
        assert base_entries == []


class TestConsumersAffected:
    def test_consumers_affected_empty_when_no_consumers(self):
        """Version changes with no consumer edges produce an empty consumers_affected list."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].tags_referenced = ["2.0"]

        diff = diff_snapshots(g1, g2)
        base_vc = [v for v in diff["changes"]["version_changes"] if v["image"] == "example.com/base"]
        assert len(base_vc) == 1
        assert base_vc[0]["consumers_affected"] == []

    def test_consumers_affected_populated_for_consumes_edge(self):
        """Version changes include consumers_affected based on 'consumes' edges."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].tags_referenced = ["2.0"]

        # Add a 'consumes' edge: app consumes base
        g2.edges.append(
            GraphEdge(
                source="example.com/app",
                target="example.com/base",
                relationship=EdgeType.CONSUMES,
                confidence=Confidence.HIGH,
                source_location=_src(),
            )
        )

        diff = diff_snapshots(g1, g2)
        base_vc = [v for v in diff["changes"]["version_changes"] if v["image"] == "example.com/base"]
        assert len(base_vc) == 1
        assert "example.com/app" in base_vc[0]["consumers_affected"]

    def test_consumers_affected_key_always_present(self):
        """Every version_change entry has a consumers_affected key."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].tags_referenced = ["9.9"]

        diff = diff_snapshots(g1, g2)
        for vc in diff["changes"]["version_changes"]:
            assert "consumers_affected" in vc


class TestEdgeChanges:
    def test_edge_changes_added(self):
        """New edges between snapshots are detected in edge_changes.added."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        # Add a new consumes edge in g2
        g2.edges.append(
            GraphEdge(
                source="example.com/app",
                target="example.com/base",
                relationship=EdgeType.CONSUMES,
                confidence=Confidence.MEDIUM,
                source_location=_src(),
            )
        )

        diff = diff_snapshots(g1, g2)
        added = diff["changes"]["edge_changes"]["added"]
        assert any(
            e["source"] == "example.com/app"
            and e["target"] == "example.com/base"
            and e["relationship"] == "consumes"
            for e in added
        )

    def test_edge_changes_removed(self):
        """Removed edges between snapshots are detected in edge_changes.removed."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        # Remove the builds_from edge in g2
        g2.edges = []

        diff = diff_snapshots(g1, g2)
        removed = diff["changes"]["edge_changes"]["removed"]
        assert any(
            e["source"] == "example.com/app"
            and e["target"] == "example.com/base"
            and e["relationship"] == "builds_from"
            for e in removed
        )

    def test_edge_changes_empty_when_identical(self):
        """No edge changes are reported when both snapshots have the same edges."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()

        diff = diff_snapshots(g1, g2)
        assert diff["changes"]["edge_changes"]["added"] == []
        assert diff["changes"]["edge_changes"]["removed"] == []

    def test_edge_changes_keys_present(self):
        """edge_changes always has 'added' and 'removed' keys."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()

        diff = diff_snapshots(g1, g2)
        ec = diff["changes"]["edge_changes"]
        assert "added" in ec
        assert "removed" in ec


class TestExistingDiffFieldsUnchanged:
    def test_existing_diff_fields_still_present(self):
        """All original diff fields remain present in the changes dict."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()

        diff = diff_snapshots(g1, g2)
        changes = diff["changes"]
        assert "added_images" in changes
        assert "removed_images" in changes
        assert "version_changes" in changes
        assert "staleness_changes" in changes

    def test_existing_diff_added_images_works(self):
        """added_images still detects new nodes."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/new"] = GraphNode(
            id="example.com/new", canonical="example.com/new"
        )

        diff = diff_snapshots(g1, g2)
        assert "example.com/new" in diff["changes"]["added_images"]

    def test_existing_diff_removed_images_works(self):
        """removed_images still detects deleted nodes."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        del g2.nodes["example.com/app"]

        diff = diff_snapshots(g1, g2)
        assert "example.com/app" in diff["changes"]["removed_images"]

    def test_existing_diff_version_changes_works(self):
        """version_changes still detects tag mutations."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].tags_referenced = ["3.0"]

        diff = diff_snapshots(g1, g2)
        assert any(v["image"] == "example.com/base" for v in diff["changes"]["version_changes"])

    def test_existing_diff_staleness_works(self):
        """staleness_changes still detects staleness mutations."""
        g1 = _make_two_node_graph()
        g2 = _make_two_node_graph()
        g2.nodes["example.com/base"].staleness = "behind"

        diff = diff_snapshots(g1, g2)
        assert any(s["image"] == "example.com/base" for s in diff["changes"]["staleness_changes"])


# ---------------------------------------------------------------------------
# Tests — log CLI command
# ---------------------------------------------------------------------------


class TestLogCommand:
    def test_log_no_snapshots_error(self, tmp_path: Path, monkeypatch):
        """log without any snapshots in the default dir exits with error code 1."""
        import shipwreck.cli as cli_module
        monkeypatch.setattr(cli_module, "_DEFAULT_SNAPSHOT_DIR", tmp_path / "empty_snaps")

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 1
        assert "Error" in result.output or "error" in result.output.lower()

    def test_log_only_one_snapshot_error(self, tmp_path: Path, monkeypatch):
        """log with only one snapshot available exits with an error."""
        snap_dir = tmp_path / "snapshots"
        g = _make_two_node_graph()
        save_snapshot(g, snap_dir)

        import shipwreck.cli as cli_module
        monkeypatch.setattr(cli_module, "_DEFAULT_SNAPSHOT_DIR", snap_dir)

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 1

    def test_log_with_explicit_before_and_after(self, tmp_path: Path):
        """log with explicit --before and --after loads both snapshots correctly."""
        p1, p2 = _save_two_snapshots(tmp_path)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2)],
        )
        assert result.exit_code == 0, result.output

    def test_log_json_output_is_valid_json(self, tmp_path: Path):
        """log --format json outputs valid JSON with expected top-level keys."""
        p1, p2 = _save_two_snapshots(tmp_path)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "previous" in data
        assert "current" in data
        assert "changes" in data

    def test_log_json_output_contains_new_fields(self, tmp_path: Path):
        """log --format json output includes metadata_changes and edge_changes."""
        p1, p2 = _save_two_snapshots(tmp_path)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        changes = data["changes"]
        assert "metadata_changes" in changes
        assert "edge_changes" in changes

    def test_log_table_output_no_changes_message(self, tmp_path: Path):
        """log table format says 'No changes detected' for identical snapshots."""
        p1, p2 = _save_two_snapshots(tmp_path)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2)],
        )
        assert result.exit_code == 0, result.output
        assert "No changes detected" in result.output

    def test_log_table_shows_version_change(self, tmp_path: Path):
        """log table format shows a version-change row when tags differ."""
        from shipwreck.output.json_export import export_json

        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        g1 = _make_two_node_graph("2025-01-01T00:00:00Z")
        p1 = snap_dir / "20250101T000000Z.json"
        export_json(g1, output_path=p1)

        g2 = _make_two_node_graph("2025-01-02T00:00:00Z")
        g2.nodes["example.com/base"].tags_referenced = ["9.9"]
        p2 = snap_dir / "20250102T000000Z.json"
        export_json(g2, output_path=p2)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2)],
        )
        assert result.exit_code == 0, result.output
        assert "example.com/base" in result.output

    def test_log_table_shows_added_image(self, tmp_path: Path):
        """log table format reports newly added images."""
        from shipwreck.output.json_export import export_json

        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        g1 = _make_two_node_graph("2025-01-01T00:00:00Z")
        p1 = snap_dir / "20250101T000000Z.json"
        export_json(g1, output_path=p1)

        g2 = _make_two_node_graph("2025-01-02T00:00:00Z")
        g2.nodes["example.com/new"] = GraphNode(
            id="example.com/new", canonical="example.com/new"
        )
        p2 = snap_dir / "20250102T000000Z.json"
        export_json(g2, output_path=p2)

        result = runner.invoke(
            app,
            ["log", "--before", str(p1), "--after", str(p2)],
        )
        assert result.exit_code == 0, result.output
        assert "example.com/new" in result.output

    def test_log_output_to_file(self, tmp_path: Path):
        """log --output writes the diff JSON to the specified file."""
        p1, p2 = _save_two_snapshots(tmp_path)
        out_file = tmp_path / "diff.json"

        result = runner.invoke(
            app,
            [
                "log",
                "--before", str(p1),
                "--after", str(p2),
                "--format", "json",
                "--output", str(out_file),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "changes" in data

    def test_log_auto_finds_latest_two(self, tmp_path: Path, monkeypatch):
        """log without --before/--after auto-selects the two most recent snapshots."""
        snap_dir = tmp_path / "snapshots"
        p1, p2 = _save_two_snapshots(tmp_path)

        import shipwreck.cli as cli_module
        monkeypatch.setattr(cli_module, "_DEFAULT_SNAPSHOT_DIR", snap_dir)

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0, result.output

    def test_log_missing_before_file_exits_with_error(self, tmp_path: Path):
        """log with a non-existent --before file exits with code 1."""
        snap_dir = tmp_path / "snapshots"
        g2 = _make_two_node_graph()
        p2 = save_snapshot(g2, snap_dir)

        result = runner.invoke(
            app,
            ["log", "--before", str(tmp_path / "nonexistent.json"), "--after", str(p2)],
        )
        assert result.exit_code == 1
        assert "Error" in result.output
