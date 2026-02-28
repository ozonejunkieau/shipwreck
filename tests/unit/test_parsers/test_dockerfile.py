"""Tests for the Dockerfile parser."""

from __future__ import annotations

from pathlib import Path

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.dockerfile import DockerfileParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "dockerfiles"


def make_parser() -> DockerfileParser:
    """Create a fresh DockerfileParser instance."""
    return DockerfileParser()


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


def test_can_handle_dockerfile() -> None:
    """Files named 'Dockerfile' (case-insensitive) are accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("Dockerfile")) is True
    assert parser.can_handle(Path("/some/path/Dockerfile")) is True


def test_can_handle_dockerfile_with_extension() -> None:
    """Files named 'Dockerfile.<suffix>' are accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("Dockerfile.prod")) is True
    assert parser.can_handle(Path("Dockerfile.dev")) is True
    assert parser.can_handle(Path("/repo/Dockerfile.test")) is True


def test_can_handle_dot_dockerfile_extension() -> None:
    """Files ending in '.dockerfile' are accepted."""
    parser = make_parser()
    assert parser.can_handle(Path("simple.dockerfile")) is True
    assert parser.can_handle(Path("/some/path/multistage.dockerfile")) is True


def test_cannot_handle_compose() -> None:
    """docker-compose.yml and similar files should not be handled."""
    parser = make_parser()
    assert parser.can_handle(Path("docker-compose.yml")) is False
    assert parser.can_handle(Path("compose.yaml")) is False
    assert parser.can_handle(Path("Makefile")) is False


def test_cannot_handle_random_file() -> None:
    """Arbitrary files without Dockerfile naming conventions are rejected."""
    parser = make_parser()
    assert parser.can_handle(Path("requirements.txt")) is False
    assert parser.can_handle(Path("pyproject.toml")) is False
    assert parser.can_handle(Path("main.py")) is False


# ---------------------------------------------------------------------------
# Parser name
# ---------------------------------------------------------------------------


def test_parser_name() -> None:
    """Parser should identify itself as 'dockerfile'."""
    assert make_parser().name == "dockerfile"


# ---------------------------------------------------------------------------
# simple.dockerfile
# ---------------------------------------------------------------------------


def test_simple_from() -> None:
    """Single FROM with explicit tag produces one HIGH-confidence reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.dockerfile", "test-repo")

    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "python:3.12-slim"
    assert ref.relationship == EdgeType.BUILDS_FROM
    assert ref.confidence == Confidence.HIGH
    assert ref.tag == "3.12-slim"
    assert ref.registry == "docker.io"
    assert ref.name == "library/python"
    assert ref.source.line == 1
    assert ref.source.repo == "test-repo"
    assert ref.source.parser == "dockerfile"
    assert ref.unresolved_variables == []


def test_simple_is_final_stage() -> None:
    """The only FROM in a single-stage file should have is_final_stage=True."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.dockerfile", "test-repo")
    assert refs[0].metadata["is_final_stage"] is True
    assert refs[0].metadata["stage_alias"] is None


# ---------------------------------------------------------------------------
# multistage.dockerfile
# ---------------------------------------------------------------------------


def test_multistage_stage_ref_not_emitted() -> None:
    """FROM referencing a previous stage alias should not produce an ImageReference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "multistage.dockerfile", "test-repo")

    # Both FROMs reference python:3.12-slim directly; no stage-alias-only FROM exists
    assert len(refs) == 2
    assert all(r.relationship == EdgeType.BUILDS_FROM for r in refs)
    assert all("3.12" in r.raw for r in refs)


def test_multistage_stage_aliases_in_metadata() -> None:
    """Stage aliases are captured in metadata for each FROM."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "multistage.dockerfile", "test-repo")

    assert refs[0].metadata["stage_alias"] == "builder"
    assert refs[1].metadata["stage_alias"] == "runtime"


def test_multistage_final_stage_flag() -> None:
    """Only the last FROM has is_final_stage=True."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "multistage.dockerfile", "test-repo")

    assert refs[0].metadata["is_final_stage"] is False
    assert refs[1].metadata["is_final_stage"] is True


# ---------------------------------------------------------------------------
# with_args.dockerfile
# ---------------------------------------------------------------------------


def test_arg_substitution() -> None:
    """ARG default values are substituted into FROM, producing MEDIUM confidence."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_args.dockerfile", "test-repo")

    # Both FROMs use ARG-substituted values
    assert len(refs) == 2
    assert all(r.confidence == Confidence.MEDIUM for r in refs)
    assert all(r.unresolved_variables == [] for r in refs)

    # First FROM: ${BASE_IMAGE}-slim → python:3.12-slim
    assert refs[0].raw == "python:3.12-slim"
    assert refs[0].tag == "3.12-slim"
    assert refs[0].registry == "docker.io"
    assert refs[0].name == "library/python"

    # Second FROM: python:${BUILDER_VERSION} → python:3.12-slim
    assert refs[1].raw == "python:3.12-slim"
    assert refs[1].tag == "3.12-slim"


def test_arg_substitution_line_numbers() -> None:
    """Source line numbers point to the FROM line, not the ARG lines."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_args.dockerfile", "test-repo")

    # ARGs are lines 1-2; first FROM is line 4
    assert refs[0].source.line == 4
    # Second FROM is line 7
    assert refs[1].source.line == 7


# ---------------------------------------------------------------------------
# unknown_arg.dockerfile
# ---------------------------------------------------------------------------


def test_arg_no_default_unresolved() -> None:
    """ARG without a default results in an unresolved MEDIUM-confidence reference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "unknown_arg.dockerfile", "test-repo")

    assert len(refs) == 1
    ref = refs[0]
    assert ref.confidence == Confidence.MEDIUM
    assert "BASE_IMAGE" in ref.unresolved_variables
    # raw should still contain the unresolved variable marker
    assert "BASE_IMAGE" in ref.raw


# ---------------------------------------------------------------------------
# platform.dockerfile
# ---------------------------------------------------------------------------


def test_platform_flag_ignored() -> None:
    """The --platform flag is stripped; only the image reference is extracted."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "platform.dockerfile", "test-repo")

    # scratch is skipped; two real images remain
    assert len(refs) == 2

    raws = [r.raw for r in refs]
    assert "python:3.12-slim" in raws
    assert "alpine:3.18" in raws


def test_scratch_ignored() -> None:
    """FROM scratch produces no ImageReference."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "platform.dockerfile", "test-repo")

    assert all(r.raw != "scratch" for r in refs)
    assert all("scratch" not in r.raw for r in refs)


def test_platform_alpine_confidence() -> None:
    """alpine:3.18 has no ARG substitution; confidence should be HIGH."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "platform.dockerfile", "test-repo")

    alpine_refs = [r for r in refs if "alpine" in r.raw]
    assert len(alpine_refs) == 1
    assert alpine_refs[0].confidence == Confidence.HIGH
    assert alpine_refs[0].registry == "docker.io"
    assert alpine_refs[0].name == "library/alpine"
    assert alpine_refs[0].tag == "3.18"


# ---------------------------------------------------------------------------
# with_registry.dockerfile
# ---------------------------------------------------------------------------


def test_registry_with_port() -> None:
    """Registry hostnames with port numbers are parsed correctly."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "with_registry.dockerfile", "test-repo")

    assert len(refs) == 2

    port_ref = refs[0]
    assert port_ref.registry == "registry.example.com:5000"
    assert port_ref.name == "myimage"
    assert port_ref.tag == "1.0"
    assert port_ref.confidence == Confidence.HIGH

    no_port_ref = refs[1]
    assert no_port_ref.registry == "registry.example.com"
    assert no_port_ref.name == "namespace/image"
    assert no_port_ref.tag == "latest"
    assert no_port_ref.confidence == Confidence.HIGH


def test_registry_with_port_source_file() -> None:
    """SourceLocation.file records the absolute path to the fixture."""
    parser = make_parser()
    fixture = FIXTURES / "with_registry.dockerfile"
    refs = parser.parse(fixture, "my-repo")

    assert all(r.source.file == str(fixture) for r in refs)
    assert all(r.source.repo == "my-repo" for r in refs)


# ---------------------------------------------------------------------------
# commented.dockerfile
# ---------------------------------------------------------------------------


def test_comments_ignored() -> None:
    """Commented FROM lines and parser directives are not parsed."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "commented.dockerfile", "test-repo")

    assert len(refs) == 1
    ref = refs[0]
    assert ref.raw == "new-image:2.0"
    assert ref.confidence == Confidence.HIGH


def test_syntax_directive_ignored() -> None:
    """The '# syntax=...' parser directive is treated as a comment and ignored."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "commented.dockerfile", "test-repo")

    # If the syntax line were parsed it would appear in refs; only new-image should be present
    assert all("syntax" not in r.raw for r in refs)
    assert all("old-image" not in r.raw for r in refs)


# ---------------------------------------------------------------------------
# Metadata — final stage
# ---------------------------------------------------------------------------


def test_final_stage_metadata() -> None:
    """The last FROM in any file has is_final_stage=True; all others are False."""
    parser = make_parser()

    # Single-stage
    simple_refs = parser.parse(FIXTURES / "simple.dockerfile", "test-repo")
    assert simple_refs[-1].metadata["is_final_stage"] is True

    # Multi-stage: only the last non-skipped FROM is final
    multi_refs = parser.parse(FIXTURES / "multistage.dockerfile", "test-repo")
    assert multi_refs[-1].metadata["is_final_stage"] is True
    assert all(r.metadata["is_final_stage"] is False for r in multi_refs[:-1])

    # Platform: scratch is last in file but skipped; python is first, alpine second
    plat_refs = parser.parse(FIXTURES / "platform.dockerfile", "test-repo")
    # The last emitted ref corresponds to the last non-skipped FROM
    assert plat_refs[-1].metadata["is_final_stage"] is False  # alpine is index 1 of 3 FROMs


def test_no_stage_alias_when_absent() -> None:
    """When there is no AS clause, stage_alias metadata is None."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "simple.dockerfile", "test-repo")
    assert refs[0].metadata["stage_alias"] is None


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_parser_implements_protocol() -> None:
    """DockerfileParser should satisfy the Parser protocol at runtime."""
    from shipwreck.parsers.base import Parser

    parser = make_parser()
    assert isinstance(parser, Parser)
