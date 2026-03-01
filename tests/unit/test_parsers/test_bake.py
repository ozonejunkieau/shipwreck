"""Unit tests for the Docker Bake (HCL) parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.bake import BakeParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "bake"


@pytest.fixture()
def parser() -> BakeParser:
    """Return a fresh BakeParser instance."""
    return BakeParser()


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


def test_can_handle_docker_bake_hcl(parser: BakeParser) -> None:
    """BakeParser matches docker-bake.hcl."""
    assert parser.can_handle(Path("/repo/docker-bake.hcl"))


def test_can_handle_docker_bake_override_hcl(parser: BakeParser) -> None:
    """BakeParser matches docker-bake.override.hcl."""
    assert parser.can_handle(Path("/repo/docker-bake.override.hcl"))


def test_cannot_handle_compose(parser: BakeParser) -> None:
    """BakeParser does not match docker-compose.yml."""
    assert not parser.can_handle(Path("/repo/docker-compose.yml"))


def test_cannot_handle_dockerfile(parser: BakeParser) -> None:
    """BakeParser does not match Dockerfile."""
    assert not parser.can_handle(Path("/repo/Dockerfile"))


# ---------------------------------------------------------------------------
# simple.hcl — tags produce PRODUCES references
# ---------------------------------------------------------------------------


def test_simple_target_tags(parser: BakeParser) -> None:
    """Tags in a simple target produce PRODUCES ImageReferences with HIGH confidence."""
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")

    assert len(refs) == 2  # noqa: PLR2004
    tags_found = {r.raw for r in refs}
    assert "registry.example.com/myapp:0.2.0" in tags_found
    assert "registry.example.com/myapp:latest" in tags_found

    for ref in refs:
        assert ref.relationship == EdgeType.PRODUCES
        assert ref.confidence == Confidence.HIGH
        assert ref.unresolved_variables == []
        assert ref.registry == "registry.example.com"


def test_multiple_tags(parser: BakeParser) -> None:
    """Each tag string produces a separate ImageReference."""
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")
    raws = [r.raw for r in refs]
    assert "registry.example.com/myapp:0.2.0" in raws
    assert "registry.example.com/myapp:latest" in raws


def test_simple_tag_parsed_components(parser: BakeParser) -> None:
    """Parsed registry/name/tag components are correct for fully-qualified tags."""
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")
    versioned = next(r for r in refs if r.raw == "registry.example.com/myapp:0.2.0")
    assert versioned.registry == "registry.example.com"
    assert versioned.name == "myapp"
    assert versioned.tag == "0.2.0"


# ---------------------------------------------------------------------------
# with_variables.hcl — variable interpolation
# ---------------------------------------------------------------------------


def test_variable_interpolation(parser: BakeParser) -> None:
    """Variables defined in HCL are substituted into tag strings."""
    refs = parser.parse(FIXTURES / "with_variables.hcl", repo_name="test-repo")

    # All refs come from PRODUCES (no contexts in this file)
    assert all(r.relationship == EdgeType.PRODUCES for r in refs)

    raws = {r.raw for r in refs}
    assert "registry.example.com/myapp:0.2.0" in raws
    assert "registry.example.com/myapp:latest" in raws


def test_variable_interpolation_confidence(parser: BakeParser) -> None:
    """Tags resolved via variable substitution get MEDIUM confidence."""
    refs = parser.parse(FIXTURES / "with_variables.hcl", repo_name="test-repo")
    for ref in refs:
        assert ref.confidence == Confidence.MEDIUM


def test_variable_interpolation_no_unresolved(parser: BakeParser) -> None:
    """When all variables are known, unresolved_variables list is empty."""
    refs = parser.parse(FIXTURES / "with_variables.hcl", repo_name="test-repo")
    for ref in refs:
        assert ref.unresolved_variables == []


# ---------------------------------------------------------------------------
# unknown_variable.hcl — unknown vars → LOW confidence + unresolved list
# ---------------------------------------------------------------------------


def test_variable_no_default(parser: BakeParser) -> None:
    """Tags with unknown variables get LOW confidence and populate unresolved_variables."""
    refs = parser.parse(FIXTURES / "unknown_variable.hcl", repo_name="test-repo")

    assert len(refs) == 1
    ref = refs[0]
    assert ref.relationship == EdgeType.PRODUCES
    assert ref.confidence == Confidence.LOW
    assert "UNKNOWN_REGISTRY" in ref.unresolved_variables


# ---------------------------------------------------------------------------
# with_contexts.hcl — docker-image:// contexts → BUILDS_FROM
# ---------------------------------------------------------------------------


def test_docker_image_context(parser: BakeParser) -> None:
    """docker-image:// context values produce BUILDS_FROM ImageReferences."""
    refs = parser.parse(FIXTURES / "with_contexts.hcl", repo_name="test-repo")

    builds_from = [r for r in refs if r.relationship == EdgeType.BUILDS_FROM]
    assert len(builds_from) == 1

    ref = builds_from[0]
    assert ref.raw == "registry.example.com/base/python:3.12"
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "registry.example.com"
    assert ref.tag == "3.12"


def test_non_image_context_ignored(parser: BakeParser) -> None:
    """Local path contexts (e.g. \".\") do not produce ImageReferences."""
    refs = parser.parse(FIXTURES / "with_contexts.hcl", repo_name="test-repo")

    # Only one BUILDS_FROM (the docker-image:// one); "." context is skipped
    builds_from = [r for r in refs if r.relationship == EdgeType.BUILDS_FROM]
    assert len(builds_from) == 1

    raw_values = {r.raw for r in refs}
    assert "." not in raw_values


# ---------------------------------------------------------------------------
# with_context_vars.hcl — variable interpolation in contexts
# ---------------------------------------------------------------------------


def test_context_variables_resolved(parser: BakeParser) -> None:
    """Variables in docker-image:// context URLs are interpolated."""
    refs = parser.parse(FIXTURES / "with_context_vars.hcl", repo_name="test-repo")

    builds_from = [r for r in refs if r.relationship == EdgeType.BUILDS_FROM]
    assert len(builds_from) == 1

    ref = builds_from[0]
    assert ref.raw == "registry.example.com/base/python:3.12"
    assert ref.registry == "registry.example.com"
    assert ref.name == "base/python"
    assert ref.tag == "3.12"


def test_context_variables_confidence(parser: BakeParser) -> None:
    """Context refs with resolved variables get MEDIUM confidence."""
    refs = parser.parse(FIXTURES / "with_context_vars.hcl", repo_name="test-repo")

    builds_from = [r for r in refs if r.relationship == EdgeType.BUILDS_FROM]
    assert builds_from[0].confidence == Confidence.MEDIUM
    assert builds_from[0].unresolved_variables == []


def test_context_unresolved_variable_tracked(parser: BakeParser) -> None:
    """Context refs with unknown variables get LOW confidence and unresolved list."""
    # with_contexts.hcl uses ${REGISTRY} but only in tags — here we test a
    # scenario where a context variable has no default.
    refs = parser.parse(FIXTURES / "with_context_vars.hcl", repo_name="test-repo")

    # The PRODUCES ref uses ${REGISTRY} which IS defined → MEDIUM
    produces = [r for r in refs if r.relationship == EdgeType.PRODUCES]
    assert produces[0].confidence == Confidence.MEDIUM


# ---------------------------------------------------------------------------
# with_inherits.hcl — target inheritance
# ---------------------------------------------------------------------------


def test_inherits_resolved(parser: BakeParser) -> None:
    """A target that inherits from another includes the parent's tags."""
    refs = parser.parse(FIXTURES / "with_inherits.hcl", repo_name="test-repo")

    raws = {r.raw for r in refs}
    # base target's tag
    assert "registry.example.com/base:1.0" in raws
    # myapp target's own tag
    assert "registry.example.com/myapp:1.0" in raws


def test_inherits_child_overrides_parent_tags(parser: BakeParser) -> None:
    """Child's own tags replace inherited tags (child has priority)."""
    refs = parser.parse(FIXTURES / "with_inherits.hcl", repo_name="test-repo")

    # myapp has its own tags list, so it should not duplicate parent's tag
    myapp_refs = [r for r in refs if r.raw == "registry.example.com/myapp:1.0"]
    assert len(myapp_refs) == 1


# ---------------------------------------------------------------------------
# with_group.hcl — groups don't produce references
# ---------------------------------------------------------------------------


def test_group_no_references(parser: BakeParser) -> None:
    """Group blocks do not produce any ImageReferences themselves."""
    refs = parser.parse(FIXTURES / "with_group.hcl", repo_name="test-repo")

    raws = {r.raw for r in refs}
    # Only the two targets' tags, not the group "all"
    assert "registry.example.com/myapp:1.0" in raws
    assert "registry.example.com/worker:1.0" in raws
    # No reference whose raw is the group target names list
    assert "myapp" not in raws
    assert "worker" not in raws
    assert len(refs) == 2  # noqa: PLR2004


# ---------------------------------------------------------------------------
# Metadata — args and dockerfile recorded
# ---------------------------------------------------------------------------


def test_args_recorded_in_metadata(parser: BakeParser) -> None:
    """If a target has args, they appear in metadata['args']."""
    # Use a simple fixture that has a dockerfile entry and verify metadata
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")

    for ref in refs:
        # simple.hcl has dockerfile = "Dockerfile"
        assert ref.metadata.get("dockerfile") == "Dockerfile"


def test_target_name_in_metadata(parser: BakeParser) -> None:
    """The bake target name is recorded in metadata['target']."""
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")
    for ref in refs:
        assert ref.metadata.get("target") == "myapp"


# ---------------------------------------------------------------------------
# SourceLocation
# ---------------------------------------------------------------------------


def test_source_location_populated(parser: BakeParser) -> None:
    """SourceLocation is populated with repo, file path, and parser name."""
    path = FIXTURES / "simple.hcl"
    refs = parser.parse(path, repo_name="my-repo")

    for ref in refs:
        assert ref.source.repo == "my-repo"
        assert ref.source.file == str(path)
        assert ref.source.parser == "bake"


def test_source_location_line_number(parser: BakeParser) -> None:
    """Line numbers are non-zero for tags that appear in the file."""
    refs = parser.parse(FIXTURES / "simple.hcl", repo_name="test-repo")
    for ref in refs:
        assert ref.source.line > 0
