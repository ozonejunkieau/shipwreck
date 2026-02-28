"""Unit tests for the Docker Compose parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.compose import ComposeParser, _resolve_compose_vars

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "compose"

REPO = "test-repo"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_parser() -> ComposeParser:
    """Return a fresh ComposeParser instance."""
    return ComposeParser()


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


def test_can_handle_docker_compose_yml() -> None:
    """docker-compose.yml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/docker-compose.yml"))


def test_can_handle_docker_compose_yaml() -> None:
    """docker-compose.yaml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/docker-compose.yaml"))


def test_can_handle_compose_yaml() -> None:
    """compose.yaml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/compose.yaml"))


def test_can_handle_compose_yml() -> None:
    """compose.yml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/compose.yml"))


def test_can_handle_docker_compose_override() -> None:
    """docker-compose.override.yml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/docker-compose.override.yml"))


def test_can_handle_compose_prod_yaml() -> None:
    """compose.prod.yaml is recognised."""
    parser = make_parser()
    assert parser.can_handle(Path("/some/path/compose.prod.yaml"))


def test_cannot_handle_dockerfile() -> None:
    """Dockerfile is not a Compose file."""
    parser = make_parser()
    assert not parser.can_handle(Path("/some/path/Dockerfile"))


def test_cannot_handle_random_yaml() -> None:
    """An arbitrary YAML file is not treated as a Compose file."""
    parser = make_parser()
    assert not parser.can_handle(Path("/some/path/config.yaml"))


# ---------------------------------------------------------------------------
# simple.yaml — plain image references
# ---------------------------------------------------------------------------


def test_simple_image() -> None:
    """Direct image reference produces a CONSUMES relationship."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    raws = [r.raw for r in refs]
    assert "postgres:16" in raws

    pg = next(r for r in refs if r.raw == "postgres:16")
    assert pg.relationship == EdgeType.CONSUMES
    assert pg.confidence == Confidence.HIGH
    assert pg.registry == "docker.io"
    assert pg.name == "library/postgres"
    assert pg.tag == "16"


def test_multiple_services() -> None:
    """Each service with image: produces a separate ImageReference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    assert len(refs) == 3
    raws = {r.raw for r in refs}
    assert raws == {
        "registry.example.com/myapp:0.1.1",
        "postgres:16",
        "redis:7-alpine",
    }


def test_simple_image_registry_parsed() -> None:
    """A fully-qualified registry image is parsed correctly."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    web = next(r for r in refs if "myapp" in r.raw)
    assert web.registry == "registry.example.com"
    assert web.name == "myapp"
    assert web.tag == "0.1.1"


def test_simple_all_consumes() -> None:
    """All references in simple.yaml are CONSUMES."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    for ref in refs:
        assert ref.relationship == EdgeType.CONSUMES


def test_simple_all_high_confidence() -> None:
    """All references in simple.yaml have HIGH confidence."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    for ref in refs:
        assert ref.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# with_build.yaml — build + image combinations
# ---------------------------------------------------------------------------


def test_build_with_image_is_produces() -> None:
    """build: + image: together means PRODUCES."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_build.yaml", REPO)

    web = next(r for r in refs if r.raw == "myapp:latest")
    assert web.relationship == EdgeType.PRODUCES
    assert web.confidence == Confidence.HIGH


def test_build_with_image_has_build_context() -> None:
    """PRODUCES references include build_context in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_build.yaml", REPO)

    web = next(r for r in refs if r.raw == "myapp:latest")
    assert "build_context" in web.metadata
    assert web.metadata["build_context"] == {"context": ".", "dockerfile": "Dockerfile.prod"}


def test_build_without_image_no_ref() -> None:
    """build: without image: produces no ImageReference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_build.yaml", REPO)

    # The "worker" service has only build:, not image: — should be absent.
    # There should be exactly 2 refs: web (PRODUCES) and db (CONSUMES).
    assert len(refs) == 2
    raws = {r.raw for r in refs}
    assert raws == {"myapp:latest", "postgres:16"}


def test_db_in_with_build_is_consumes() -> None:
    """db service in with_build.yaml is still a CONSUMES reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_build.yaml", REPO)

    db = next(r for r in refs if r.raw == "postgres:16")
    assert db.relationship == EdgeType.CONSUMES


# ---------------------------------------------------------------------------
# with_env.yaml — variable interpolation
# ---------------------------------------------------------------------------


def test_env_file_loaded() -> None:
    """.env file values are used for interpolation."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_env.yaml", REPO)

    # REGISTRY and VERSION come from the .env file
    web = next(r for r in refs if "myapp" in (r.name or ""))
    assert web.registry == "registry.example.com"
    assert web.tag == "1.2.3"


def test_env_file_provides_registry() -> None:
    """REGISTRY from .env is substituted into the image string."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_env.yaml", REPO)

    web = next(r for r in refs if "myapp" in (r.name or ""))
    # raw still has the original template
    assert "${REGISTRY" in web.raw
    # but the registry is resolved
    assert web.registry == "registry.example.com"


def test_env_interpolation_with_default() -> None:
    """${VAR:-default} resolves to default when VAR not set."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_env.yaml", REPO)

    # PG_VERSION is NOT in the .env file → default "16" is used
    db = next(r for r in refs if "postgres" in (r.name or ""))
    assert db.tag == "16"
    assert db.confidence == Confidence.MEDIUM


def test_env_vars_substituted_become_medium_confidence() -> None:
    """Images with substituted variables get MEDIUM confidence."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_env.yaml", REPO)

    for ref in refs:
        assert ref.confidence == Confidence.MEDIUM


def test_unresolved_var_is_low_confidence() -> None:
    """An image with an unresolvable variable gets LOW confidence."""
    content = "services:\n  app:\n    image: ${MISSING_VAR}/myapp:latest\n"
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False, dir=FIXTURES
    ) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        parser = make_parser()
        refs = parser.parse(tmp_path, REPO)
        assert len(refs) == 1
        assert refs[0].confidence == Confidence.LOW
        assert "MISSING_VAR" in refs[0].unresolved_variables
    finally:
        tmp_path.unlink()


# ---------------------------------------------------------------------------
# with_profiles.yaml — service profiles
# ---------------------------------------------------------------------------


def test_service_with_profile() -> None:
    """Profiled services are still included with profile recorded in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_profiles.yaml", REPO)

    assert len(refs) == 2
    debug = next(r for r in refs if r.raw == "debug:latest")
    assert "profiles" in debug.metadata
    assert debug.metadata["profiles"] == ["debug"]


def test_service_without_profile_has_no_profiles_metadata() -> None:
    """Services without profiles have no profiles key in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_profiles.yaml", REPO)

    app = next(r for r in refs if r.raw == "myapp:1.0")
    assert "profiles" not in app.metadata


# ---------------------------------------------------------------------------
# docker-compose.override.yaml — compose override file
# ---------------------------------------------------------------------------


def test_override_file_parsed() -> None:
    """Override compose files are handled as regular compose files."""
    parser = make_parser()
    assert parser.can_handle(FIXTURES / "docker-compose.override.yaml")

    refs = parser.parse(FIXTURES / "docker-compose.override.yaml", REPO)
    assert len(refs) == 2
    raws = {r.raw for r in refs}
    assert raws == {"myapp:dev", "postgres:15"}


def test_override_all_consumes() -> None:
    """All services in docker-compose.override.yaml are CONSUMES references."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "docker-compose.override.yaml", REPO)

    for ref in refs:
        assert ref.relationship == EdgeType.CONSUMES


# ---------------------------------------------------------------------------
# source location
# ---------------------------------------------------------------------------


def test_source_location_repo() -> None:
    """SourceLocation carries the repo name."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", "my-org/my-repo")

    for ref in refs:
        assert ref.source.repo == "my-org/my-repo"


def test_source_location_parser_name() -> None:
    """SourceLocation.parser is 'compose'."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    for ref in refs:
        assert ref.source.parser == "compose"


def test_source_location_line_number() -> None:
    """SourceLocation.line is a positive integer."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.yaml", REPO)

    for ref in refs:
        assert ref.source.line >= 1


# ---------------------------------------------------------------------------
# _resolve_compose_vars unit tests
# ---------------------------------------------------------------------------


def test_resolve_plain_var() -> None:
    """${VAR} is replaced when present in env."""
    resolved, unresolved = _resolve_compose_vars("${FOO}/bar", {"FOO": "hello"})
    assert resolved == "hello/bar"
    assert unresolved == []


def test_resolve_missing_plain_var() -> None:
    """${VAR} is left unresolved when absent from env."""
    resolved, unresolved = _resolve_compose_vars("${FOO}/bar", {})
    assert "${FOO}" in resolved
    assert "FOO" in unresolved


def test_resolve_default_colon_dash() -> None:
    """${VAR:-default} uses default when VAR is absent."""
    resolved, unresolved = _resolve_compose_vars("${FOO:-mydefault}", {})
    assert resolved == "mydefault"
    assert unresolved == []


def test_resolve_default_colon_dash_present() -> None:
    """${VAR:-default} uses VAR value when it is set."""
    resolved, unresolved = _resolve_compose_vars("${FOO:-mydefault}", {"FOO": "actual"})
    assert resolved == "actual"
    assert unresolved == []


def test_resolve_default_dash_only_when_unset() -> None:
    """${VAR-default} uses default only when VAR is absent (not when empty)."""
    resolved_absent, _ = _resolve_compose_vars("${FOO-fallback}", {})
    assert resolved_absent == "fallback"

    resolved_empty, _ = _resolve_compose_vars("${FOO-fallback}", {"FOO": ""})
    assert resolved_empty == ""


def test_resolve_default_colon_dash_empty() -> None:
    """${VAR:-default} uses default when VAR is empty string."""
    resolved, unresolved = _resolve_compose_vars("${FOO:-mydefault}", {"FOO": ""})
    assert resolved == "mydefault"
    assert unresolved == []


def test_resolve_multiple_vars() -> None:
    """Multiple variables in the same string are all resolved."""
    resolved, unresolved = _resolve_compose_vars(
        "${REG:-docker.io}/ns:${TAG:-latest}",
        {"REG": "ghcr.io", "TAG": "1.0"},
    )
    assert resolved == "ghcr.io/ns:1.0"
    assert unresolved == []


def test_resolve_partial_unresolved() -> None:
    """Only the missing variable appears in unresolved list."""
    resolved, unresolved = _resolve_compose_vars(
        "${REG}/ns:${TAG}",
        {"REG": "ghcr.io"},
    )
    assert "ghcr.io" in resolved
    assert "${TAG}" in resolved
    assert unresolved == ["TAG"]
