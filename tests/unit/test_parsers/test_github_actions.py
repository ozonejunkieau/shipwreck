"""Tests for the GitHub Actions workflow parser."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.github_actions import GitHubActionsParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "github_actions"

REPO = "test-repo"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_parser() -> GitHubActionsParser:
    """Return a fresh GitHubActionsParser instance."""
    return GitHubActionsParser()


def parse_text(content: str, filename: str = "ci.yml") -> list:
    """Parse a workflow from a string, using a synthetic .github/workflows path."""
    with tempfile.TemporaryDirectory() as tmp:
        wf_dir = Path(tmp) / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        wf_file = wf_dir / filename
        wf_file.write_text(textwrap.dedent(content), encoding="utf-8")
        return make_parser().parse(wf_file, REPO)


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


def test_can_handle_workflow_yml() -> None:
    """.github/workflows/ci.yml is accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.github/workflows/ci.yml")) is True


def test_can_handle_workflow_yaml() -> None:
    """.github/workflows/build.yaml is accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.github/workflows/build.yaml")) is True


def test_cannot_handle_non_workflow_yaml() -> None:
    """An arbitrary YAML file is not accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/config/settings.yml")) is False


def test_cannot_handle_workflow_in_wrong_dir() -> None:
    """A YAML file in .github/actions/ (not workflows/) is not accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.github/actions/my-action.yml")) is False


def test_cannot_handle_dockerfile() -> None:
    """A Dockerfile is not accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.github/workflows/Dockerfile")) is False


def test_cannot_handle_top_level_yml() -> None:
    """A top-level YAML file is not accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("ci.yml")) is False


# ---------------------------------------------------------------------------
# Parser name
# ---------------------------------------------------------------------------


def test_parser_name() -> None:
    """Parser should identify itself as 'github_actions'."""
    assert make_parser().name == "github_actions"


# ---------------------------------------------------------------------------
# simple.yml — container object form
# ---------------------------------------------------------------------------


def test_container_object_form() -> None:
    """container.image produces a CONSUMES reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/simple.yml", REPO)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "python:3.12-slim"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "docker.io"
    assert ref.name == "library/python"
    assert ref.tag == "3.12-slim"
    assert ref.source.parser == "github_actions"
    assert ref.source.repo == REPO


# ---------------------------------------------------------------------------
# Container string form
# ---------------------------------------------------------------------------


def test_container_string_form() -> None:
    """container: as a bare string (not a dict) produces a CONSUMES reference."""
    refs = parse_text(
        """\
        name: Test
        on: push
        jobs:
          run:
            runs-on: ubuntu-latest
            container: alpine:3.18
            steps:
              - run: echo hi
        """
    )
    container_refs = [r for r in refs if r.raw == "alpine:3.18"]
    assert len(container_refs) == 1
    ref = container_refs[0]
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.tag == "3.18"


# ---------------------------------------------------------------------------
# Job services
# ---------------------------------------------------------------------------


def test_job_services_multiple() -> None:
    """Multiple service images each produce a CONSUMES reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/ci.yml", REPO)

    raws = {r.raw for r in refs}
    assert "redis:7" in raws
    assert "postgres:16" in raws

    for raw in ("redis:7", "postgres:16"):
        ref = next(r for r in refs if r.raw == raw)
        assert ref.relationship == EdgeType.CONSUMES
        assert ref.confidence == Confidence.HIGH


def test_job_services_single() -> None:
    """A single service image produces a CONSUMES reference."""
    refs = parse_text(
        """\
        name: Test
        on: push
        jobs:
          test:
            runs-on: ubuntu-latest
            services:
              db:
                image: postgres:15
            steps:
              - run: echo hi
        """
    )
    svc_refs = [r for r in refs if r.raw == "postgres:15"]
    assert len(svc_refs) == 1
    assert svc_refs[0].relationship == EdgeType.CONSUMES
    assert svc_refs[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Docker action (uses: docker://)
# ---------------------------------------------------------------------------


def test_docker_action_uses() -> None:
    """uses: docker://alpine:3.18 produces a CONSUMES reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/ci.yml", REPO)

    alpine_refs = [r for r in refs if r.raw == "alpine:3.18"]
    assert len(alpine_refs) == 1
    ref = alpine_refs[0]
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "docker.io"
    assert ref.name == "library/alpine"
    assert ref.tag == "3.18"


def test_docker_action_metadata_contains_uses() -> None:
    """docker:// action references carry the original uses: value in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/ci.yml", REPO)

    alpine_ref = next(r for r in refs if r.raw == "alpine:3.18")
    assert alpine_ref.metadata.get("uses") == "docker://alpine:3.18"


# ---------------------------------------------------------------------------
# docker build / push in run: blocks
# ---------------------------------------------------------------------------


def test_docker_build_in_run_produces() -> None:
    """docker build -t IMAGE in a run: block produces a PRODUCES reference with LOW confidence."""
    refs = parse_text(
        """\
        name: Build
        on: push
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - name: Build
                run: docker build -t myapp:1.0 .
        """
    )
    build_refs = [r for r in refs if r.raw == "myapp:1.0"]
    assert len(build_refs) == 1
    ref = build_refs[0]
    assert ref.relationship == EdgeType.PRODUCES
    assert ref.confidence == Confidence.LOW


def test_docker_push_in_run_produces() -> None:
    """docker push IMAGE in a run: block produces a PRODUCES reference with LOW confidence."""
    refs = parse_text(
        """\
        name: Push
        on: push
        jobs:
          push:
            runs-on: ubuntu-latest
            steps:
              - name: Push
                run: docker push myapp:1.0
        """
    )
    push_refs = [r for r in refs if r.raw == "myapp:1.0"]
    assert len(push_refs) == 1
    ref = push_refs[0]
    assert ref.relationship == EdgeType.PRODUCES
    assert ref.confidence == Confidence.LOW


def test_docker_pull_in_run_consumes() -> None:
    """docker pull IMAGE in a run: block produces a CONSUMES reference with LOW confidence."""
    refs = parse_text(
        """\
        name: Pull
        on: push
        jobs:
          pull:
            runs-on: ubuntu-latest
            steps:
              - name: Pull
                run: docker pull ubuntu:22.04
        """
    )
    pull_refs = [r for r in refs if r.raw == "ubuntu:22.04"]
    assert len(pull_refs) == 1
    ref = pull_refs[0]
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Multi-line run: blocks
# ---------------------------------------------------------------------------


def test_multiline_run_block_fully_scanned() -> None:
    """All docker commands across a multi-line run: block are extracted."""
    refs = parse_text(
        """\
        name: Multi
        on: push
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - name: Build and push
                run: |
                  docker build -t myapp:latest .
                  docker push myapp:latest
        """
    )
    raws = [r.raw for r in refs]
    assert raws.count("myapp:latest") == 2

    build_ref = next(r for r in refs if r.raw == "myapp:latest" and r.relationship == EdgeType.PRODUCES and r.metadata.get("source") == "docker_build")
    push_ref = next(r for r in refs if r.raw == "myapp:latest" and r.relationship == EdgeType.PRODUCES and r.metadata.get("source") == "docker_push")
    assert build_ref.confidence == Confidence.LOW
    assert push_ref.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Environment variable resolution
# ---------------------------------------------------------------------------


def test_env_resolution_top_level() -> None:
    """${{ env.VAR }} is resolved from the top-level env: block."""
    refs = parse_text(
        """\
        name: Env
        on: push
        env:
          REGISTRY: registry.example.com
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: docker build -t ${{ env.REGISTRY }}/myapp:1.0 .
        """
    )
    build_refs = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(build_refs) == 1
    ref = build_refs[0]
    assert ref.registry == "registry.example.com"
    assert ref.name == "myapp"
    assert ref.tag == "1.0"
    assert ref.confidence == Confidence.LOW


def test_env_resolution_job_level() -> None:
    """${{ env.VAR }} is resolved from a job-level env: block."""
    refs = parse_text(
        """\
        name: JobEnv
        on: push
        jobs:
          build:
            runs-on: ubuntu-latest
            env:
              TAG: "2.0"
            steps:
              - run: docker build -t myapp:${{ env.TAG }} .
        """
    )
    build_refs = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(build_refs) == 1
    assert build_refs[0].tag == "2.0"


# ---------------------------------------------------------------------------
# Secrets — never resolved
# ---------------------------------------------------------------------------


def test_secrets_not_resolved() -> None:
    """${{ secrets.* }} expressions are left unresolved."""
    refs = parse_text(
        """\
        name: Secret
        on: push
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: docker build -t ${{ secrets.PRIVATE_REGISTRY }}/myapp:1.0 .
        """
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.unresolved_variables
    assert any("secrets." in v for v in ref.unresolved_variables)


# ---------------------------------------------------------------------------
# GitHub context variables — unresolved
# ---------------------------------------------------------------------------


def test_github_context_vars_unresolved() -> None:
    """${{ github.* }} expressions are marked as unresolved."""
    refs = parse_text(
        """\
        name: GithubCtx
        on: push
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: docker build -t myapp:${{ github.sha }} .
        """
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.unresolved_variables
    assert any("github." in v for v in ref.unresolved_variables)


# ---------------------------------------------------------------------------
# workflow_dispatch inputs with defaults
# ---------------------------------------------------------------------------


def test_workflow_dispatch_input_default() -> None:
    """workflow_dispatch input defaults populate the resolution context."""
    refs = parse_text(
        """\
        name: Dispatch
        on:
          workflow_dispatch:
            inputs:
              image_tag:
                description: Image tag
                default: "3.0"
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: docker build -t myapp:${{ inputs.image_tag }} .
        """
    )
    build_refs = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(build_refs) == 1
    assert build_refs[0].tag == "3.0"


# ---------------------------------------------------------------------------
# Multiple jobs
# ---------------------------------------------------------------------------


def test_multiple_jobs_different_images() -> None:
    """Images from multiple jobs are all extracted."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/ci.yml", REPO)

    raws = {r.raw for r in refs}
    # container images from two different jobs
    assert "registry.example.com/ci/tester:2.0" in raws
    assert "registry.example.com/tools/deployer:latest" in raws


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------


def test_source_location_repo() -> None:
    """SourceLocation carries the supplied repo name."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/simple.yml", "my-org/my-repo")

    for ref in refs:
        assert ref.source.repo == "my-org/my-repo"


def test_source_location_file() -> None:
    """SourceLocation.file is the absolute path to the workflow file."""
    fixture = FIXTURES / ".github/workflows/simple.yml"
    refs = make_parser().parse(fixture, REPO)

    for ref in refs:
        assert ref.source.file == str(fixture)


def test_source_location_line_positive() -> None:
    """SourceLocation.line is a positive integer."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".github/workflows/ci.yml", REPO)

    for ref in refs:
        assert ref.source.line >= 1


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_parser_implements_protocol() -> None:
    """GitHubActionsParser satisfies the Parser protocol at runtime."""
    from shipwreck.parsers.base import Parser

    parser = make_parser()
    assert isinstance(parser, Parser)
