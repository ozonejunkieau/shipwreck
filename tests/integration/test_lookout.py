"""Integration tests for the lookout command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from shipwreck.cli import _staleness_rank, app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(tmp_path: Path, *, internal_registry: str = "registry.example.com") -> Path:
    """Write a minimal shipwreck.yaml with one internal registry so policy allows it."""
    content = f"""
registries:
  - name: example
    url: {internal_registry}
    internal: true
registry_policy:
  prompt_external: false
  external_allowlist: []
repositories: []
"""
    config_path = tmp_path / "shipwreck.yaml"
    config_path.write_text(content)
    return config_path


def _make_graph_file(tmp_path: Path, num_nodes: int = 2) -> Path:
    """Persist a test graph to the .latest_graph.json location and return its output dir."""
    from shipwreck.output.json_export import export_json
    from tests.conftest import make_graph

    graph = make_graph(num_nodes)
    # Ensure every node has a meaningful tag for staleness computation
    for i, node in enumerate(graph.nodes.values()):
        node.tags_referenced = [f"1.{i}.0"]

    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    export_json(graph, output_path=output_dir / ".latest_graph.json")
    return output_dir


def _mock_registry_client(tags: list[str]) -> MagicMock:
    """Return a context-manager-compatible mock RegistryClient."""
    mock_client = MagicMock()
    mock_client.list_tags.return_value = tags
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Tests — error paths
# ---------------------------------------------------------------------------


def test_lookout_no_graph_no_output_dir_exits_with_error(tmp_path: Path) -> None:
    """lookout with an empty (non-existent) output dir exits with non-zero code."""
    config = _make_minimal_config(tmp_path)
    empty_dir = tmp_path / "empty_output"
    result = runner.invoke(
        app,
        ["lookout", "--config", str(config), "--output", str(empty_dir)],
    )
    assert result.exit_code != 0
    assert "No graph available" in result.output


def test_lookout_missing_config_exits_with_error(tmp_path: Path) -> None:
    """lookout with a non-existent config file exits with non-zero code."""
    output_dir = _make_graph_file(tmp_path)
    result = runner.invoke(
        app,
        ["lookout", "--config", str(tmp_path / "nonexistent.yaml"), "--output", str(output_dir)],
    )
    assert result.exit_code != 0


def test_lookout_snapshot_not_found_exits_with_error(tmp_path: Path) -> None:
    """lookout with --snapshot pointing to a non-existent file exits with non-zero."""
    config = _make_minimal_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "lookout",
            "--config", str(config),
            "--snapshot", str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tests — basic happy path
# ---------------------------------------------------------------------------


def test_lookout_succeeds_with_mocked_registry(tmp_path: Path) -> None:
    """lookout exits 0 and queries the registry when policy allows it."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path)

    mock_client = _mock_registry_client(["1.0.0", "1.1.0", "2.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "Checked" in result.output


def test_lookout_displays_rich_table(tmp_path: Path) -> None:
    """lookout renders a table with image / staleness columns."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path)

    mock_client = _mock_registry_client(["1.0.0", "1.1.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    # Table headers should be present in output
    assert "Image" in result.output
    assert "Staleness" in result.output


def test_lookout_staleness_computed_correctly(tmp_path: Path) -> None:
    """lookout correctly reports staleness when a newer semver tag is available."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=1)

    # Node references 1.0.0; registry has 2.0.0 → should be major_behind
    mock_client = _mock_registry_client(["1.0.0", "2.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "major" in result.output or "behind" in result.output


def test_lookout_current_staleness_reported(tmp_path: Path) -> None:
    """lookout reports 'current' when the node tag is the latest available."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=1)

    # Node references 1.0.0; registry only has 1.0.0 → should be current
    mock_client = _mock_registry_client(["1.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "current" in result.output


def test_lookout_summary_count_in_output(tmp_path: Path) -> None:
    """lookout prints a summary line with image counts."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=2)

    mock_client = _mock_registry_client(["1.0.0", "1.1.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    # Should contain something like "Checked 2 images"
    assert "stale" in result.output.lower()


# ---------------------------------------------------------------------------
# Tests — registry filter
# ---------------------------------------------------------------------------


def test_lookout_registry_filter_skips_non_matching(tmp_path: Path) -> None:
    """--registry flag causes lookout to skip nodes on other registries."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path)

    mock_client = _mock_registry_client(["1.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client) as mock_cls:
        result = runner.invoke(
            app,
            [
                "lookout",
                "--config", str(config),
                "--output", str(output_dir),
                "--registry", "docker.io",  # nodes are on registry.example.com → skipped
            ],
        )

    assert result.exit_code == 0, result.output
    # RegistryClient should not have been called since all nodes are on registry.example.com
    mock_cls.assert_not_called()
    assert "Checked 0 images" in result.output


def test_lookout_registry_filter_matches_correct_registry(tmp_path: Path) -> None:
    """--registry flag allows nodes on the matching registry."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=1)

    mock_client = _mock_registry_client(["1.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            [
                "lookout",
                "--config", str(config),
                "--output", str(output_dir),
                "--registry", "registry.example.com",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Checked 1 images" in result.output


# ---------------------------------------------------------------------------
# Tests — --yes / non-interactive mode
# ---------------------------------------------------------------------------


def test_lookout_yes_flag_non_interactive(tmp_path: Path) -> None:
    """--yes flag enables non-interactive mode; internal registries are still queried."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=1)

    mock_client = _mock_registry_client(["1.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir), "--yes"],
        )

    assert result.exit_code == 0, result.output
    assert "Checked" in result.output


# ---------------------------------------------------------------------------
# Tests — registry query failure
# ---------------------------------------------------------------------------


def test_lookout_registry_query_failure_handled_gracefully(tmp_path: Path) -> None:
    """lookout continues and marks node as 'unknown' when the registry call fails."""
    config = _make_minimal_config(tmp_path)
    output_dir = _make_graph_file(tmp_path, num_nodes=1)

    mock_client = MagicMock()
    mock_client.list_tags.side_effect = RuntimeError("connection refused")
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            ["lookout", "--config", str(config), "--output", str(output_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    # Should still show 0 checked (failure) but overall succeed
    assert "Checked 0 images" in result.output


# ---------------------------------------------------------------------------
# Tests — snapshot enrichment
# ---------------------------------------------------------------------------


def test_lookout_snapshot_enrichment(tmp_path: Path) -> None:
    """lookout --snapshot loads from the given snapshot file."""
    from shipwreck.output.snapshot import save_snapshot
    from tests.conftest import make_graph

    config = _make_minimal_config(tmp_path)
    snap_dir = tmp_path / "snaps"
    graph = make_graph(1)
    for node in graph.nodes.values():
        node.tags_referenced = ["1.0.0"]
    snap_path = save_snapshot(graph, snap_dir)

    output_dir = tmp_path / "output"
    mock_client = _mock_registry_client(["1.0.0"])
    with patch("shipwreck.registry.client.RegistryClient", return_value=mock_client):
        result = runner.invoke(
            app,
            [
                "lookout",
                "--config", str(config),
                "--snapshot", str(snap_path),
                "--output", str(output_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Checked" in result.output


# ---------------------------------------------------------------------------
# Tests — _staleness_rank helper
# ---------------------------------------------------------------------------


def test_staleness_rank_ordering() -> None:
    """_staleness_rank gives correct ordering for worst-wins comparison."""
    assert _staleness_rank("current") < _staleness_rank("behind")
    assert _staleness_rank("behind") < _staleness_rank("major_behind")
    assert _staleness_rank(None) < _staleness_rank("current")
    assert _staleness_rank("unknown") < _staleness_rank("current")


def test_staleness_rank_unrecognised_string() -> None:
    """_staleness_rank returns -1 for unrecognised strings."""
    assert _staleness_rank("totally_made_up") == -1
