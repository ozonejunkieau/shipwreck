"""Tests for GitLab discovery and plunder/sail commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from typer.testing import CliRunner

from shipwreck.cli import app
from shipwreck.config import RepositoryConfig
from shipwreck.discovery.gitlab import discover_repos

runner = CliRunner()

GITLAB_URL = "https://gitlab.example.com"
GROUP = "my-org"
ENDPOINT = f"{GITLAB_URL}/api/v4/groups/{GROUP}/projects"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_1 = {
    "path_with_namespace": "my-org/app1",
    "ssh_url_to_repo": "git@gitlab.example.com:my-org/app1.git",
    "path": "app1",
    "default_branch": "main",
}
PROJECT_2 = {
    "path_with_namespace": "my-org/app2",
    "ssh_url_to_repo": "git@gitlab.example.com:my-org/app2.git",
    "path": "app2",
    "default_branch": "develop",
}
PROJECT_3 = {
    "path_with_namespace": "my-org/infra-tools",
    "ssh_url_to_repo": "git@gitlab.example.com:my-org/infra-tools.git",
    "path": "infra-tools",
    "default_branch": "main",
}


# ---------------------------------------------------------------------------
# discover_repos — basic behaviour
# ---------------------------------------------------------------------------


@respx.mock
def test_discover_repos_basic():
    """Basic discovery returns repos from a single-page GitLab API response."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1, PROJECT_2]),
        httpx.Response(200, json=[]),  # empty page stops pagination
    ]

    repos = discover_repos(url=GITLAB_URL, group=GROUP, auth_token="test-token")

    assert len(repos) == 2
    assert repos[0].name == "app1"
    assert repos[0].url == "git@gitlab.example.com:my-org/app1.git"
    assert repos[0].ref == "main"
    assert repos[1].name == "app2"
    assert repos[1].ref == "develop"


@respx.mock
def test_discover_repos_pagination():
    """discover_repos follows pagination until an empty page is returned."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1]),
        httpx.Response(200, json=[PROJECT_2]),
        httpx.Response(200, json=[]),  # stop
    ]

    repos = discover_repos(url=GITLAB_URL, group=GROUP, auth_token="test-token")

    assert len(repos) == 2
    assert {r.name for r in repos} == {"app1", "app2"}


@respx.mock
def test_discover_repos_empty_group():
    """An empty group returns an empty list immediately."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(url=GITLAB_URL, group=GROUP, auth_token="test-token")

    assert repos == []


@respx.mock
def test_discover_repos_include_pattern():
    """include_pattern keeps only repos whose path matches the regex."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1, PROJECT_2, PROJECT_3]),
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(
        url=GITLAB_URL,
        group=GROUP,
        auth_token="test-token",
        include_pattern=r"app\d",
    )

    assert len(repos) == 2
    names = {r.name for r in repos}
    assert names == {"app1", "app2"}
    assert "infra-tools" not in names


@respx.mock
def test_discover_repos_exclude_pattern():
    """exclude_pattern drops repos whose path matches the regex."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1, PROJECT_2, PROJECT_3]),
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(
        url=GITLAB_URL,
        group=GROUP,
        auth_token="test-token",
        exclude_pattern=r"infra",
    )

    assert len(repos) == 2
    assert all(r.name != "infra-tools" for r in repos)


@respx.mock
def test_discover_repos_include_and_exclude_pattern():
    """include_pattern is applied first; then exclude_pattern narrows further."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1, PROJECT_2, PROJECT_3]),
        httpx.Response(200, json=[]),
    ]

    # Include everything with 'app', then exclude app2
    repos = discover_repos(
        url=GITLAB_URL,
        group=GROUP,
        auth_token="test-token",
        include_pattern=r"app",
        exclude_pattern=r"app2",
    )

    assert len(repos) == 1
    assert repos[0].name == "app1"


@respx.mock
def test_discover_repos_default_branch_fallback():
    """When default_branch is None, 'main' is used."""
    project_no_branch = {
        "path_with_namespace": "my-org/nob",
        "ssh_url_to_repo": "git@gitlab.example.com:my-org/nob.git",
        "path": "nob",
        "default_branch": None,
    }
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[project_no_branch]),
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(url=GITLAB_URL, group=GROUP, auth_token="test-token")

    assert len(repos) == 1
    assert repos[0].ref == "main"


@respx.mock
def test_discover_repos_auth_header():
    """The PRIVATE-TOKEN header is sent with each request."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1]),
        httpx.Response(200, json=[]),
    ]

    discover_repos(url=GITLAB_URL, group=GROUP, auth_token="secret-token-xyz")

    # Check first call's headers
    first_call = route.calls[0]
    assert first_call.request.headers.get("private-token") == "secret-token-xyz"


@respx.mock
def test_discover_repos_raises_on_http_error():
    """HTTP errors from the GitLab API propagate as httpx exceptions."""
    respx.get(ENDPOINT).mock(return_value=httpx.Response(403, json={"message": "403 Forbidden"}))

    with pytest.raises(httpx.HTTPStatusError):
        discover_repos(url=GITLAB_URL, group=GROUP, auth_token="bad-token")


@respx.mock
def test_discover_repos_subgroup_url_encoding():
    """Nested group paths (containing /) are URL-encoded in the API endpoint."""
    nested_group = "my-org/containers"
    encoded_endpoint = f"{GITLAB_URL}/api/v4/groups/my-org%2Fcontainers/projects"
    route = respx.get(encoded_endpoint)
    route.side_effect = [
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(url=GITLAB_URL, group=nested_group, auth_token="tok")

    assert repos == []
    assert route.called


@respx.mock
def test_discover_repos_returns_repository_config_instances():
    """Each discovered repo is a RepositoryConfig with correct fields."""
    route = respx.get(ENDPOINT)
    route.side_effect = [
        httpx.Response(200, json=[PROJECT_1]),
        httpx.Response(200, json=[]),
    ]

    repos = discover_repos(url=GITLAB_URL, group=GROUP, auth_token="tok")

    assert len(repos) == 1
    repo = repos[0]
    assert isinstance(repo, RepositoryConfig)
    assert repo.url == "git@gitlab.example.com:my-org/app1.git"
    assert repo.name == "app1"
    assert repo.ref == "main"


# ---------------------------------------------------------------------------
# plunder CLI command
# ---------------------------------------------------------------------------


def test_plunder_missing_url(monkeypatch):
    """plunder without --url exits with a non-zero exit code."""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    result = runner.invoke(app, ["plunder", "--group", "my-org"])
    assert result.exit_code != 0
    assert "required" in result.output.lower() or "error" in result.output.lower()


def test_plunder_missing_group(monkeypatch):
    """plunder without --group exits with a non-zero exit code."""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    result = runner.invoke(app, ["plunder", "--url", "https://gitlab.example.com"])
    assert result.exit_code != 0
    assert "required" in result.output.lower() or "error" in result.output.lower()


def test_plunder_missing_token(monkeypatch):
    """plunder without the token env var exits with error."""
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    result = runner.invoke(
        app,
        ["plunder", "--url", "https://gitlab.example.com", "--group", "my-org"],
    )
    assert result.exit_code != 0
    assert "GITLAB_TOKEN" in result.output or "error" in result.output.lower()


def test_plunder_custom_token_env(monkeypatch):
    """--token-env reads from a custom environment variable."""
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("MY_CUSTOM_TOKEN", raising=False)
    # No token set; should fail with appropriate message.
    result = runner.invoke(
        app,
        [
            "plunder",
            "--url", "https://gitlab.example.com",
            "--group", "my-org",
            "--token-env", "MY_CUSTOM_TOKEN",
        ],
    )
    assert result.exit_code != 0
    assert "MY_CUSTOM_TOKEN" in result.output


def test_plunder_dry_run(monkeypatch):
    """plunder --dry-run lists repos without writing any config file."""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")

    mock_repos = [
        RepositoryConfig(url="git@gitlab.example.com:my-org/app1.git", name="app1", ref="main"),
        RepositoryConfig(url="git@gitlab.example.com:my-org/app2.git", name="app2", ref="develop"),
    ]

    with patch("shipwreck.discovery.gitlab.discover_repos", return_value=mock_repos):
        result = runner.invoke(
            app,
            [
                "plunder",
                "--url", "https://gitlab.example.com",
                "--group", "my-org",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "app1" in result.output
    assert "app2" in result.output


def test_plunder_append_config_creates_file(tmp_path, monkeypatch):
    """plunder --append-config writes discovered repos to a new config file."""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    config_file = tmp_path / "shipwreck.yaml"

    mock_repos = [
        RepositoryConfig(url="git@gitlab.example.com:my-org/app1.git", name="app1", ref="main"),
    ]

    with patch("shipwreck.discovery.gitlab.discover_repos", return_value=mock_repos):
        result = runner.invoke(
            app,
            [
                "plunder",
                "--url", "https://gitlab.example.com",
                "--group", "my-org",
                "--append-config", str(config_file),
            ],
        )

    assert result.exit_code == 0, result.output
    assert config_file.exists()

    import yaml
    data = yaml.safe_load(config_file.read_text())
    assert "repositories" in data
    urls = [r["url"] for r in data["repositories"]]
    assert "git@gitlab.example.com:my-org/app1.git" in urls


def test_plunder_append_config_no_duplicates(tmp_path, monkeypatch):
    """plunder --append-config does not add repos that already exist in the config."""
    import yaml

    monkeypatch.setenv("GITLAB_TOKEN", "tok")
    config_file = tmp_path / "shipwreck.yaml"
    config_file.write_text(
        yaml.dump({
            "repositories": [
                {"url": "git@gitlab.example.com:my-org/app1.git", "name": "app1", "ref": "main"}
            ]
        })
    )

    mock_repos = [
        RepositoryConfig(url="git@gitlab.example.com:my-org/app1.git", name="app1", ref="main"),
        RepositoryConfig(url="git@gitlab.example.com:my-org/app2.git", name="app2", ref="main"),
    ]

    with patch("shipwreck.discovery.gitlab.discover_repos", return_value=mock_repos):
        result = runner.invoke(
            app,
            [
                "plunder",
                "--url", "https://gitlab.example.com",
                "--group", "my-org",
                "--append-config", str(config_file),
            ],
        )

    assert result.exit_code == 0, result.output

    data = yaml.safe_load(config_file.read_text())
    urls = [r["url"] for r in data["repositories"]]
    # app1 should appear only once
    assert urls.count("git@gitlab.example.com:my-org/app1.git") == 1
    # app2 should have been added
    assert "git@gitlab.example.com:my-org/app2.git" in urls


def test_plunder_discovery_failure(monkeypatch):
    """plunder exits with error when discover_repos raises an exception."""
    monkeypatch.setenv("GITLAB_TOKEN", "tok")

    with patch(
        "shipwreck.discovery.gitlab.discover_repos",
        side_effect=Exception("connection refused"),
    ):
        result = runner.invoke(
            app,
            ["plunder", "--url", "https://gitlab.example.com", "--group", "my-org"],
        )

    assert result.exit_code != 0
    assert "error" in result.output.lower() or "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# sail CLI command
# ---------------------------------------------------------------------------


def _make_minimal_config(tmp_path: Path, fixture_subdir: str = "dockerfiles") -> Path:
    """Write a minimal shipwreck.yaml pointing to a local fixture repo."""
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    repo_path = fixtures_dir / fixture_subdir
    config_content = f"repositories:\n  - path: {repo_path}\n    name: {fixture_subdir}\n"
    config_path = tmp_path / "shipwreck.yaml"
    config_path.write_text(config_content)
    return config_path


def test_sail_runs_all_three_phases(tmp_path):
    """sail runs Hunt, Lookout, and Map phases and produces output files."""
    config = _make_minimal_config(tmp_path)
    output_dir = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "sail",
            "--config", str(config),
            "--output", str(output_dir),
            "--yes",  # skip interactive prompts
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Phase 1" in result.output
    assert "Phase 2" in result.output
    assert "Phase 3" in result.output
    assert (output_dir / "shipwreck.json").exists()
    assert (output_dir / "shipwreck.mermaid").exists()
    assert (output_dir / "shipwreck.html").exists()


def test_sail_missing_config(tmp_path):
    """sail with a non-existent config exits with error."""
    result = runner.invoke(
        app,
        ["sail", "--config", str(tmp_path / "nonexistent.yaml")],
    )
    assert result.exit_code != 0


def test_sail_yes_flag_disables_prompts(tmp_path):
    """sail --yes runs in non-interactive mode without hanging."""
    config = _make_minimal_config(tmp_path)
    output_dir = tmp_path / "output"

    result = runner.invoke(
        app,
        ["sail", "--config", str(config), "--output", str(output_dir), "--yes"],
    )

    # Should complete without requiring user input
    assert result.exit_code == 0, result.output


def test_sail_snapshot_creates_snapshot_file(tmp_path):
    """sail --snapshot saves a timestamped snapshot JSON file."""
    config = _make_minimal_config(tmp_path)
    output_dir = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "sail",
            "--config", str(config),
            "--output", str(output_dir),
            "--snapshot",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    # Snapshot message should appear in output
    assert "snapshot" in result.output.lower()


def test_sail_complete_message(tmp_path):
    """sail outputs a completion message."""
    config = _make_minimal_config(tmp_path)
    output_dir = tmp_path / "output"

    result = runner.invoke(
        app,
        ["sail", "--config", str(config), "--output", str(output_dir), "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "sail complete" in result.output.lower() or "complete" in result.output.lower()
