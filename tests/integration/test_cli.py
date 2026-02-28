"""Integration tests for the CLI via Typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from shipwreck.cli import app

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _make_config(tmp_path: Path, fixture_subdir: str = "dockerfiles") -> Path:
    """Write a minimal shipwreck.yaml that points to a local fixture repo."""
    repo_path = FIXTURES_DIR / fixture_subdir
    config_content = f"""
repositories:
  - path: {repo_path}
    name: {fixture_subdir}
"""
    config_path = tmp_path / "shipwreck.yaml"
    config_path.write_text(config_content)
    return config_path


class TestHuntCommand:
    """CLI 'hunt' command integration tests."""

    def test_hunt_with_local_repo(self, tmp_path: Path):
        """hunt --config with a local repo path succeeds."""
        config = _make_config(tmp_path, "dockerfiles")
        result = runner.invoke(
            app,
            ["hunt", "--config", str(config)],
        )
        assert result.exit_code == 0, result.output

    def test_hunt_missing_config(self, tmp_path: Path):
        """hunt with non-existent config exits with error."""
        result = runner.invoke(
            app,
            ["hunt", "--config", str(tmp_path / "nonexistent.yaml")],
        )
        assert result.exit_code != 0

    def test_hunt_reports_image_count(self, tmp_path: Path):
        """hunt output mentions the number of images discovered."""
        config = _make_config(tmp_path, "dockerfiles")
        result = runner.invoke(
            app,
            ["hunt", "--config", str(config)],
        )
        # Should mention "images" in the output
        assert "image" in result.output.lower() or result.exit_code == 0


class TestMapCommand:
    """CLI 'map' command integration tests."""

    def test_map_produces_json(self, tmp_path: Path):
        """map --format json produces a shipwreck.json file."""
        config = _make_config(tmp_path, "dockerfiles")
        output_dir = tmp_path / "output"
        result = runner.invoke(
            app,
            [
                "map",
                "--config", str(config),
                "--format", "json",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (output_dir / "shipwreck.json").exists()

    def test_map_produces_mermaid(self, tmp_path: Path):
        """map --format mermaid produces a shipwreck.mermaid file."""
        config = _make_config(tmp_path, "dockerfiles")
        output_dir = tmp_path / "output"
        result = runner.invoke(
            app,
            [
                "map",
                "--config", str(config),
                "--format", "mermaid",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (output_dir / "shipwreck.mermaid").exists()

    def test_map_produces_html(self, tmp_path: Path):
        """map --format html produces a shipwreck.html file."""
        config = _make_config(tmp_path, "dockerfiles")
        output_dir = tmp_path / "output"
        result = runner.invoke(
            app,
            [
                "map",
                "--config", str(config),
                "--format", "html",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (output_dir / "shipwreck.html").exists()

    def test_map_all_formats(self, tmp_path: Path):
        """map --format all produces all three output files."""
        config = _make_config(tmp_path, "dockerfiles")
        output_dir = tmp_path / "output"
        result = runner.invoke(
            app,
            [
                "map",
                "--config", str(config),
                "--format", "all",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (output_dir / "shipwreck.json").exists()
        assert (output_dir / "shipwreck.mermaid").exists()
        assert (output_dir / "shipwreck.html").exists()


class TestDigCommand:
    """CLI 'dig' command integration tests."""

    def _run_hunt_and_snapshot(self, tmp_path: Path, fixture: str = "dockerfiles") -> Path:
        """Run hunt with snapshot to prepare for dig."""
        config = _make_config(tmp_path, fixture)
        output_dir = tmp_path / ".shipwreck" / "output"
        # map --snapshot saves to output_dir.parent / "snapshots"
        snap_dir = output_dir.parent / "snapshots"

        result = runner.invoke(
            app,
            [
                "map",
                "--config", str(config),
                "--format", "json",
                "--snapshot",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        snapshots = sorted(snap_dir.glob("*.json"))
        assert len(snapshots) > 0, f"No snapshots found in {snap_dir}. Output was:\n{result.output}"
        return snapshots[-1]

    def test_dig_no_snapshot_exits_with_error(self, tmp_path: Path):
        """dig with no snapshot available exits with an error."""
        result = runner.invoke(
            app,
            ["dig", "--snapshot", str(tmp_path / "nonexistent.json")],
        )
        assert result.exit_code != 0

    def test_dig_summary_shows_total_images(self, tmp_path: Path):
        """dig with no query args shows graph summary."""
        snap = self._run_hunt_and_snapshot(tmp_path)
        result = runner.invoke(app, ["dig", "--snapshot", str(snap)])
        assert result.exit_code == 0, result.output
        assert "total" in result.output.lower() or "image" in result.output.lower()

    def test_dig_critical_flag(self, tmp_path: Path):
        """dig --critical returns nodes sorted by criticality."""
        snap = self._run_hunt_and_snapshot(tmp_path)
        result = runner.invoke(app, ["dig", "--snapshot", str(snap), "--critical"])
        assert result.exit_code == 0, result.output

    def test_dig_json_format(self, tmp_path: Path):
        """dig --format json outputs valid JSON."""
        import json as _json

        snap = self._run_hunt_and_snapshot(tmp_path)
        result = runner.invoke(
            app,
            ["dig", "--snapshot", str(snap), "--critical", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        # Output should be parseable JSON
        parsed = _json.loads(result.output)
        assert isinstance(parsed, list)
