"""Integration tests: fixtures → scanner → graph."""

from __future__ import annotations

from pathlib import Path

from shipwreck.config import RepositoryConfig, ShipwreckConfig
from shipwreck.models import EdgeType, Graph
from shipwreck.parsers.ansible import AnsibleParser
from shipwreck.parsers.bake import BakeParser
from shipwreck.parsers.compose import ComposeParser
from shipwreck.parsers.dockerfile import DockerfileParser
from shipwreck.parsers.fallback import FallbackScanner
from shipwreck.parsers.github_actions import GitHubActionsParser
from shipwreck.parsers.gitlab_ci import GitLabCIParser
from shipwreck.scanner import scan, scan_repo

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

GITLAB_CI_DIR = FIXTURES_DIR / "gitlab_ci"
GITHUB_ACTIONS_DIR = FIXTURES_DIR / "github_actions"
ANSIBLE_DIR = FIXTURES_DIR / "ansible"


def _make_parsers():
    """Return all specific parsers and fallback."""
    specific = [
        DockerfileParser(),
        BakeParser(),
        ComposeParser(),
        GitLabCIParser(),
        GitHubActionsParser(),
        AnsibleParser(),
    ]
    fallback = FallbackScanner()
    return specific, fallback


class TestScanRepo:
    """Integration tests for scan_repo() against fixture directories."""

    def test_scan_dockerfiles_dir(self):
        """scan_repo on dockerfiles fixture dir finds all FROM references."""
        specific, fallback = _make_parsers()
        refs = scan_repo(FIXTURES_DIR / "dockerfiles", "test-repo", specific, fallback)
        # Should find image refs from all .dockerfile files
        assert len(refs) > 0
        raw_images = [r.raw for r in refs]
        assert "python:3.12-slim" in raw_images

    def test_scan_bake_dir(self):
        """scan_repo on bake fixture dir finds PRODUCES references."""
        specific, fallback = _make_parsers()
        refs = scan_repo(FIXTURES_DIR / "bake", "test-repo", specific, fallback)
        produces = [r for r in refs if r.relationship == EdgeType.PRODUCES]
        assert len(produces) > 0

    def test_scan_compose_dir(self):
        """scan_repo on compose fixture dir finds CONSUMES references."""
        specific, fallback = _make_parsers()
        refs = scan_repo(FIXTURES_DIR / "compose", "test-repo", specific, fallback)
        consumes = [r for r in refs if r.relationship == EdgeType.CONSUMES]
        assert len(consumes) > 0

    def test_fallback_does_not_duplicate_claimed_files(self):
        """Files claimed by specific parsers are not re-processed by fallback."""
        specific, fallback = _make_parsers()
        # Scan dockerfiles dir — Dockerfile files should be claimed by dockerfile parser
        refs = scan_repo(FIXTURES_DIR / "dockerfiles", "test-repo", specific, fallback)
        # All dockerfile refs should come from the dockerfile parser
        fallback_refs = [r for r in refs if r.source.parser == "fallback"]
        # Fallback refs should not include Dockerfile-matching files
        for ref in fallback_refs:
            # The source file should not be a file that the dockerfile parser claims
            assert not (
                ref.source.file.lower() == "dockerfile"
                or ref.source.file.lower().startswith("dockerfile.")
                or ref.source.file.lower().endswith(".dockerfile")
            ), f"Dockerfile file was processed by fallback: {ref.source.file}"

    def test_scan_repo_returns_correct_repo_name(self):
        """All references have the correct repo name in source."""
        specific, fallback = _make_parsers()
        refs = scan_repo(FIXTURES_DIR / "dockerfiles", "my-test-repo", specific, fallback)
        assert all(r.source.repo == "my-test-repo" for r in refs)

    def test_scan_gitlab_ci_dir(self):
        """scan_repo on gitlab_ci fixtures finds CONSUMES references."""
        specific, fallback = _make_parsers()
        refs = scan_repo(GITLAB_CI_DIR, "test-repo", specific, fallback)
        assert len(refs) > 0
        consumes = [r for r in refs if r.relationship == EdgeType.CONSUMES]
        assert len(consumes) > 0

    def test_scan_github_actions_dir(self):
        """scan_repo on github_actions fixtures finds refs through .github/ dir."""
        specific, fallback = _make_parsers()
        refs = scan_repo(GITHUB_ACTIONS_DIR, "test-repo", specific, fallback)
        assert len(refs) > 0

    def test_scan_ansible_dir(self):
        """scan_repo on ansible fixtures finds CONSUMES references."""
        specific, fallback = _make_parsers()
        refs = scan_repo(ANSIBLE_DIR, "test-repo", specific, fallback)
        consumes = [r for r in refs if r.relationship == EdgeType.CONSUMES]
        assert len(consumes) > 0

    def test_no_fallback_duplication_for_gitlab_ci(self):
        """GitLab CI files should be claimed by gitlab_ci parser, not fallback."""
        specific, fallback = _make_parsers()
        refs = scan_repo(GITLAB_CI_DIR, "test-repo", specific, fallback)
        fallback_refs = [r for r in refs if r.source.parser == "fallback"]
        # .gitlab-ci.yml should be claimed by the specific parser
        gitlab_ci_fallback = [r for r in fallback_refs if ".gitlab-ci" in r.source.file]
        assert len(gitlab_ci_fallback) == 0

    def test_hidden_dir_github_workflows_not_skipped(self):
        """_iter_repo_files should include files in .github/workflows/."""
        from shipwreck.scanner import _iter_repo_files

        files = _iter_repo_files(GITHUB_ACTIONS_DIR)
        workflow_files = [f for f in files if ".github" in str(f)]
        assert len(workflow_files) > 0


class TestScanToGraph:
    """Integration tests for the full scan → graph pipeline using local paths."""

    def _make_config_with_local(self, path: Path, name: str) -> ShipwreckConfig:
        return ShipwreckConfig(
            repositories=[RepositoryConfig(path=str(path), name=name)],
        )

    def test_dockerfile_fixtures_produce_graph(self):
        """Dockerfile fixtures → graph with nodes."""
        cfg = self._make_config_with_local(FIXTURES_DIR / "dockerfiles", "dockerfiles")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"dockerfiles": FIXTURES_DIR / "dockerfiles"},
        )
        assert isinstance(graph, Graph)
        assert graph.summary.total_images > 0

    def test_compose_fixtures_produce_consumes_edges(self):
        """Compose fixtures → graph has nodes with CONSUMES sources."""
        cfg = self._make_config_with_local(FIXTURES_DIR / "compose", "compose")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"compose": FIXTURES_DIR / "compose"},
        )
        has_consumes = any(
            any(s.relationship == EdgeType.CONSUMES for s in n.sources)
            for n in graph.nodes.values()
        )
        assert has_consumes

    def test_bake_fixtures_produce_produces_edges(self):
        """Bake fixtures → graph has nodes with PRODUCES sources."""
        cfg = self._make_config_with_local(FIXTURES_DIR / "bake", "bake")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"bake": FIXTURES_DIR / "bake"},
        )
        has_produces = any(
            any(s.relationship == EdgeType.PRODUCES for s in n.sources)
            for n in graph.nodes.values()
        )
        assert has_produces

    def test_graph_summary_populated(self):
        """Graph summary is correctly populated after scan."""
        cfg = self._make_config_with_local(FIXTURES_DIR / "dockerfiles", "dockerfiles")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"dockerfiles": FIXTURES_DIR / "dockerfiles"},
        )
        assert graph.summary.total_images == len(graph.nodes)

    def test_multi_repo_scan(self):
        """Multiple repos are scanned and combined into one graph."""
        cfg = ShipwreckConfig(
            repositories=[
                RepositoryConfig(path=str(FIXTURES_DIR / "dockerfiles"), name="dockerfiles"),
                RepositoryConfig(path=str(FIXTURES_DIR / "compose"), name="compose"),
            ]
        )
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={
                "dockerfiles": FIXTURES_DIR / "dockerfiles",
                "compose": FIXTURES_DIR / "compose",
            },
        )
        # Should have refs from both repos
        repos = {s.repo for n in graph.nodes.values() for s in n.sources}
        assert "dockerfiles" in repos
        assert "compose" in repos

    def test_gitlab_ci_fixtures_produce_graph(self):
        """GitLab CI fixtures → graph with nodes."""
        cfg = self._make_config_with_local(GITLAB_CI_DIR, "gitlab-ci")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"gitlab-ci": GITLAB_CI_DIR},
        )
        assert isinstance(graph, Graph)
        assert graph.summary.total_images > 0

    def test_ansible_fixtures_produce_graph(self):
        """Ansible fixtures → graph with nodes."""
        cfg = self._make_config_with_local(ANSIBLE_DIR, "ansible")
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={"ansible": ANSIBLE_DIR},
        )
        assert graph.summary.total_images > 0

    def test_multi_parser_pipeline(self):
        """Full multi-parser scan combines refs from all parser types."""
        cfg = ShipwreckConfig(
            repositories=[
                RepositoryConfig(path=str(FIXTURES_DIR / "dockerfiles"), name="dockerfiles"),
                RepositoryConfig(path=str(FIXTURES_DIR / "compose"), name="compose"),
                RepositoryConfig(path=str(GITLAB_CI_DIR), name="gitlab-ci"),
                RepositoryConfig(path=str(ANSIBLE_DIR), name="ansible"),
            ]
        )
        graph = scan(
            config=cfg,
            cache_dir=Path("/tmp/shipwreck-test-cache"),
            local_paths={
                "dockerfiles": FIXTURES_DIR / "dockerfiles",
                "compose": FIXTURES_DIR / "compose",
                "gitlab-ci": GITLAB_CI_DIR,
                "ansible": ANSIBLE_DIR,
            },
        )
        repos = {s.repo for n in graph.nodes.values() for s in n.sources}
        assert "dockerfiles" in repos
        assert "compose" in repos
        assert "gitlab-ci" in repos
        assert "ansible" in repos
