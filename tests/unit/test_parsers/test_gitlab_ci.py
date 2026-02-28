"""Tests for the GitLab CI parser."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from shipwreck.models import Confidence, EdgeType, ImageReference
from shipwreck.parsers.gitlab_ci import GitLabCIParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "gitlab_ci"


def make_parser() -> GitLabCIParser:
    """Create a fresh GitLabCIParser instance."""
    return GitLabCIParser()


def parse_text(text: str, filename: str = ".gitlab-ci.yml") -> list[ImageReference]:
    """Parse inline YAML text via a temporary file written to FIXTURES dir.

    Uses a tmp_path-style approach: writes to a unique temp path under the
    system temp directory.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=filename,
        dir=FIXTURES,
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(textwrap.dedent(text))
        tmp = Path(f.name)

    try:
        return make_parser().parse(tmp, "test-repo")
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


def test_can_handle_dotgitlab_ci_yml() -> None:
    """.gitlab-ci.yml (the canonical name) is accepted."""
    parser = make_parser()
    assert parser.can_handle(Path(".gitlab-ci.yml")) is True
    assert parser.can_handle(Path("/repo/.gitlab-ci.yml")) is True


def test_can_handle_named_gitlab_ci_yml() -> None:
    """Files ending in .gitlab-ci.yml are accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("build.gitlab-ci.yml")) is True
    assert parser.can_handle(Path("deploy.gitlab-ci.yml")) is True
    assert parser.can_handle(Path("/repo/ci/build.gitlab-ci.yml")) is True


def test_can_handle_directory_component() -> None:
    """Files inside a .gitlab-ci/ directory are accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.gitlab-ci/build.yml")) is True
    assert parser.can_handle(Path(".gitlab-ci/shared.yml")) is True


def test_cannot_handle_github_actions() -> None:
    """GitHub Actions workflow files should not be accepted."""
    parser = make_parser()
    assert parser.can_handle(Path(".github/workflows/ci.yml")) is False


def test_cannot_handle_compose() -> None:
    """Docker Compose files should not be accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("docker-compose.yml")) is False
    assert parser.can_handle(Path("compose.yaml")) is False


def test_cannot_handle_random_yaml() -> None:
    """Arbitrary YAML files are not accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("config.yml")) is False
    assert parser.can_handle(Path("values.yaml")) is False


# ---------------------------------------------------------------------------
# Parser name
# ---------------------------------------------------------------------------


def test_parser_name() -> None:
    """Parser should identify itself as 'gitlab_ci'."""
    assert make_parser().name == "gitlab_ci"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_parser_implements_protocol() -> None:
    """GitLabCIParser should satisfy the Parser protocol at runtime."""
    from shipwreck.parsers.base import Parser

    parser = make_parser()
    assert isinstance(parser, Parser)


# ---------------------------------------------------------------------------
# Job-level image — string form
# ---------------------------------------------------------------------------


def test_job_image_string_form() -> None:
    """Job-level image: string produces CONSUMES, HIGH confidence."""
    refs = parse_text("""\
        build:
          image: python:3.12-slim
          script: [echo hi]
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "python:3.12-slim"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "docker.io"
    assert ref.name == "library/python"
    assert ref.tag == "3.12-slim"
    assert ref.unresolved_variables == []
    assert ref.source.parser == "gitlab_ci"
    assert ref.source.repo == "test-repo"


def test_job_image_with_registry() -> None:
    """Job image with explicit registry is parsed correctly."""
    refs = parse_text("""\
        build:
          image: registry.example.com/ci/builder:1.2.0
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.registry == "registry.example.com"
    assert ref.name == "ci/builder"
    assert ref.tag == "1.2.0"
    assert ref.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Job-level image — object form
# ---------------------------------------------------------------------------


def test_job_image_object_form() -> None:
    """Job-level image: with name: field is parsed correctly."""
    refs = parse_text("""\
        test:
          image:
            name: python:3.11-alpine
            entrypoint: [""]
          script: [pytest]
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "python:3.11-alpine"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.tag == "3.11-alpine"


def test_job_image_object_without_name_skipped() -> None:
    """Object form image: without name: produces no reference."""
    refs = parse_text("""\
        test:
          image:
            entrypoint: [""]
          script: [pytest]
    """)
    assert refs == []


# ---------------------------------------------------------------------------
# Default image
# ---------------------------------------------------------------------------


def test_default_image() -> None:
    """default.image produces a CONSUMES reference with HIGH confidence."""
    refs = parse_text("""\
        default:
          image: python:3.12

        build:
          script: [echo hi]
    """)
    image_refs = [r for r in refs if r.raw == "python:3.12"]
    assert len(image_refs) == 1
    ref = image_refs[0]
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.metadata.get("context") == "default"


def test_default_image_object_form() -> None:
    """default.image in object form (name:) is also extracted."""
    refs = parse_text("""\
        default:
          image:
            name: node:20-alpine
            entrypoint: [""]
    """)
    assert len(refs) == 1
    assert refs[0].raw == "node:20-alpine"
    assert refs[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def test_services_string_form() -> None:
    """String service entries produce CONSUMES references."""
    refs = parse_text("""\
        test:
          image: python:3.12
          services:
            - postgres:16
            - redis:7-alpine
    """)
    service_refs = [r for r in refs if r.raw in ("postgres:16", "redis:7-alpine")]
    assert len(service_refs) == 2
    for ref in service_refs:
        assert ref.relationship == EdgeType.CONSUMES
        assert ref.confidence == Confidence.HIGH


def test_services_object_form() -> None:
    """Object service entries with name: produce CONSUMES references."""
    refs = parse_text("""\
        test:
          image: python:3.12
          services:
            - name: postgres:16
              alias: db
    """)
    service_refs = [r for r in refs if r.raw == "postgres:16"]
    assert len(service_refs) == 1
    ref = service_refs[0]
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH


def test_services_fixture_file() -> None:
    """services.yml fixture contains expected service references."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "services.yml", "test-repo")

    raws = [r.raw for r in refs]
    assert "postgres:16" in raws
    assert "redis:7-alpine" in raws
    assert "elasticsearch:8.11.0" in raws
    assert "mysql:8.0" in raws
    assert "docker:24.0-dind" in raws
    for ref in refs:
        assert ref.relationship == EdgeType.CONSUMES
        assert ref.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Script-based docker commands
# ---------------------------------------------------------------------------


def test_docker_build_in_script_produces() -> None:
    """docker build -t in script produces PRODUCES with LOW confidence."""
    refs = parse_text("""\
        build:
          image: docker:24.0
          script:
            - docker build -t registry.example.com/myapp:1.0 .
    """)
    produces_refs = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(produces_refs) == 1
    ref = produces_refs[0]
    assert ref.raw == "registry.example.com/myapp:1.0"
    assert ref.confidence == Confidence.LOW
    assert ref.metadata.get("command") == "docker build"


def test_docker_push_in_script_produces() -> None:
    """docker push in script produces PRODUCES with LOW confidence."""
    refs = parse_text("""\
        release:
          image: docker:24.0
          script:
            - docker push registry.example.com/myapp:latest
    """)
    produces_refs = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(produces_refs) == 1
    ref = produces_refs[0]
    assert ref.raw == "registry.example.com/myapp:latest"
    assert ref.confidence == Confidence.LOW
    assert ref.metadata.get("command") == "docker push"


def test_docker_pull_in_script_consumes() -> None:
    """docker pull in script produces CONSUMES with LOW confidence."""
    refs = parse_text("""\
        deploy:
          image: docker:24.0
          script:
            - docker pull registry.example.com/myapp:stable
    """)
    pull_refs = [r for r in refs if r.metadata.get("command") == "docker pull"]
    assert len(pull_refs) == 1
    ref = pull_refs[0]
    assert ref.raw == "registry.example.com/myapp:stable"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------


def test_variable_resolution_in_job_image() -> None:
    """Variables from the variables: block are substituted in job image."""
    refs = parse_text("""\
        variables:
          REGISTRY: registry.example.com

        build:
          image: $REGISTRY/ci/builder:1.0
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "$REGISTRY/ci/builder:1.0"
    assert ref.registry == "registry.example.com"
    assert ref.name == "ci/builder"
    assert ref.tag == "1.0"
    assert ref.confidence == Confidence.MEDIUM
    assert ref.unresolved_variables == []


def test_job_level_variables_override_global() -> None:
    """Job-level variables override top-level variables for that job."""
    refs = parse_text("""\
        variables:
          TAG: global

        build:
          variables:
            TAG: "1.0.0"
          image: python:$TAG
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.tag == "1.0.0"
    assert ref.confidence == Confidence.MEDIUM


def test_unresolvable_variables_marked() -> None:
    """Variables not defined in any block remain as unresolved."""
    refs = parse_text("""\
        build:
          image: $UNDEFINED_VAR/myapp:latest
    """)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.confidence == Confidence.LOW
    assert "UNDEFINED_VAR" in ref.unresolved_variables


# ---------------------------------------------------------------------------
# Runtime CI variables (unresolved)
# ---------------------------------------------------------------------------


def test_runtime_ci_vars_unresolved() -> None:
    """$CI_COMMIT_TAG and similar runtime vars are marked as unresolved."""
    refs = parse_text("""\
        build:
          image: docker:24.0
          script:
            - docker build -t registry.example.com/myapp:$CI_COMMIT_TAG .
    """)
    produces = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(produces) == 1
    ref = produces[0]
    assert "CI_COMMIT_TAG" in ref.unresolved_variables


def test_runtime_ci_vars_in_push_unresolved() -> None:
    """$CI_COMMIT_SHA in docker push is marked unresolved."""
    refs = parse_text("""\
        release:
          image: docker:24.0
          script:
            - docker push registry.example.com/myapp:$CI_COMMIT_SHA
    """)
    produces = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(produces) == 1
    assert "CI_COMMIT_SHA" in produces[0].unresolved_variables


# ---------------------------------------------------------------------------
# Include directives
# ---------------------------------------------------------------------------


def test_include_recorded_in_metadata() -> None:
    """include: directives are recorded in the first ref's metadata."""
    refs = parse_text("""\
        include:
          - local: '/.gitlab-ci/shared.yml'
          - template: 'Auto-DevOps.gitlab-ci.yml'

        build:
          image: python:3.12
    """)
    assert len(refs) >= 1
    includes = refs[0].metadata.get("includes", [])
    assert any(inc.get("local") == "/.gitlab-ci/shared.yml" for inc in includes)
    assert any(inc.get("template") == "Auto-DevOps.gitlab-ci.yml" for inc in includes)


def test_project_include_recorded() -> None:
    """project: includes are recorded in metadata."""
    refs = parse_text("""\
        include:
          - project: 'my-org/ci-templates'
            file: '/templates/build.yml'

        build:
          image: alpine:3.18
    """)
    assert len(refs) >= 1
    includes = refs[0].metadata.get("includes", [])
    assert any(inc.get("project") == "my-org/ci-templates" for inc in includes)


# ---------------------------------------------------------------------------
# Reserved keys not treated as jobs
# ---------------------------------------------------------------------------


def test_reserved_keys_not_treated_as_jobs() -> None:
    """Top-level reserved keys (stages, variables, etc.) are not scanned as jobs."""
    refs = parse_text("""\
        stages:
          - build

        variables:
          MY_IMAGE: python:3.12

        build:
          image: python:3.12
    """)
    # Only the actual "build" job produces a ref — not "stages" or "variables"
    assert len(refs) == 1
    assert refs[0].raw == "python:3.12"


# ---------------------------------------------------------------------------
# Hidden/template jobs still scanned
# ---------------------------------------------------------------------------


def test_hidden_job_still_scanned() -> None:
    """Hidden/template jobs (starting with .) are still scanned for image refs."""
    refs = parse_text("""\
        .docker-base:
          image: docker:24.0-dind
          services:
            - docker:24.0-dind
    """)
    # Both the image and the service should be extracted
    raws = [r.raw for r in refs]
    assert "docker:24.0-dind" in raws


# ---------------------------------------------------------------------------
# Multiple jobs with different images
# ---------------------------------------------------------------------------


def test_multiple_jobs_different_images() -> None:
    """Each job with a different image produces a separate reference."""
    refs = parse_text("""\
        build:
          image: python:3.12

        test:
          image: node:20-alpine

        deploy:
          image: alpine:3.18
    """)
    raws = [r.raw for r in refs]
    assert "python:3.12" in raws
    assert "node:20-alpine" in raws
    assert "alpine:3.18" in raws
    assert len(refs) == 3


# ---------------------------------------------------------------------------
# Comprehensive fixture file
# ---------------------------------------------------------------------------


def test_comprehensive_fixture() -> None:
    """Parsing the comprehensive .gitlab-ci.yml fixture yields expected refs."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / ".gitlab-ci.yml", "my-repo")

    raws = [r.raw for r in refs]

    # default image
    assert "python:3.12-slim" in raws

    # Hidden job image
    assert "docker:24.0-dind" in raws

    # build-app uses a variable-interpolated image
    consumes = [r for r in refs if r.relationship == EdgeType.CONSUMES]
    assert len(consumes) >= 3

    # test-unit uses object form
    obj_refs = [r for r in refs if r.raw == "python:3.11-alpine"]
    assert len(obj_refs) == 1

    # deploy-prod: explicit registry image
    assert "registry.example.com/tools/deployer:latest" in raws

    # Source repo and parser are set correctly
    assert all(r.source.repo == "my-repo" for r in refs)
    assert all(r.source.parser == "gitlab_ci" for r in refs)


def test_build_fixture() -> None:
    """build.gitlab-ci.yml fixture parses build and release jobs."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "build.gitlab-ci.yml", "build-repo")

    # The release job has a plain image
    raws = [r.raw for r in refs]
    assert "alpine:3.18" in raws

    # Script commands: docker build and docker push produce PRODUCES refs
    produces = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert len(produces) >= 1


# ---------------------------------------------------------------------------
# Comments in YAML
# ---------------------------------------------------------------------------


def test_yaml_comments_ignored() -> None:
    """YAML comments are ignored and do not produce spurious references."""
    refs = parse_text("""\
        # This is a comment: image: should-not-appear:latest

        build:
          # Another comment
          image: python:3.12
    """)
    raws = [r.raw for r in refs]
    assert "should-not-appear:latest" not in raws
    assert "python:3.12" in raws
    assert len(refs) == 1


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------


def test_source_location_fields() -> None:
    """SourceLocation records repo, file, line, and parser correctly."""
    parser = make_parser()
    fixture = FIXTURES / ".gitlab-ci.yml"
    refs = parser.parse(fixture, "test-repo")

    for ref in refs:
        assert ref.source.repo == "test-repo"
        assert ref.source.file == str(fixture)
        assert ref.source.line >= 1
        assert ref.source.parser == "gitlab_ci"
