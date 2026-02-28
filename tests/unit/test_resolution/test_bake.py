"""Unit tests for resolution.bake — Docker Bake HCL variable resolution."""

from __future__ import annotations

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.resolution.bake import resolve_bake


def _make_ref(
    raw: str,
    unresolved: list[str] | None = None,
    confidence: Confidence = Confidence.LOW,
) -> ImageReference:
    """Build a minimal ImageReference for testing."""
    return ImageReference(
        raw=raw,
        registry=None,
        name=None,
        tag=None,
        source=SourceLocation(
            repo="test-repo",
            file="/repo/docker-bake.hcl",
            line=10,
            parser="bake",
        ),
        relationship=EdgeType.PRODUCES,
        confidence=confidence,
        unresolved_variables=unresolved or [],
    )


# ---------------------------------------------------------------------------
# Basic substitution
# ---------------------------------------------------------------------------


def test_single_var_resolved() -> None:
    """${VAR} is replaced when VAR is present in variables."""
    ref = _make_ref("${REGISTRY}/myapp:latest", unresolved=["REGISTRY"])
    result = resolve_bake([ref], variables={"REGISTRY": "registry.example.com"})

    assert len(result) == 1
    resolved = result[0]
    assert resolved.raw == "registry.example.com/myapp:latest"
    assert resolved.registry == "registry.example.com"
    assert resolved.name == "myapp"
    assert resolved.tag == "latest"
    assert resolved.unresolved_variables == []


def test_multiple_vars_in_one_string() -> None:
    """All ${VAR} placeholders in a string are substituted."""
    ref = _make_ref(
        "${REGISTRY}/${ORG}/app:${TAG}",
        unresolved=["REGISTRY", "ORG", "TAG"],
    )
    variables = {"REGISTRY": "reg.io", "ORG": "acme", "TAG": "1.2.3"}
    result = resolve_bake([ref], variables=variables)

    assert result[0].raw == "reg.io/acme/app:1.2.3"
    assert result[0].unresolved_variables == []


def test_unknown_var_left_as_is() -> None:
    """Unknown variables remain as ${VAR} placeholders."""
    ref = _make_ref("${UNKNOWN}/app:v1", unresolved=["UNKNOWN"])
    result = resolve_bake([ref], variables={})

    assert result[0].raw == "${UNKNOWN}/app:v1"
    assert "UNKNOWN" in result[0].unresolved_variables


def test_partial_resolution() -> None:
    """Only known variables are substituted; unknown ones remain."""
    ref = _make_ref("${REGISTRY}/${MISSING}/app:v1", unresolved=["REGISTRY", "MISSING"])
    result = resolve_bake([ref], variables={"REGISTRY": "reg.io"})

    assert result[0].raw == "reg.io/${MISSING}/app:v1"
    remaining = result[0].unresolved_variables
    assert "REGISTRY" not in remaining
    assert "MISSING" in remaining


def test_confidence_raised_to_medium_on_full_resolution() -> None:
    """Confidence becomes MEDIUM when all variables are resolved."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    result = resolve_bake([ref], variables={"REGISTRY": "reg.io"})
    assert result[0].confidence == Confidence.MEDIUM


def test_confidence_unchanged_when_vars_remain() -> None:
    """Confidence stays LOW when some variables are still unresolved."""
    ref = _make_ref("${REGISTRY}/${UNKNOWN}:v1", unresolved=["REGISTRY", "UNKNOWN"])
    result = resolve_bake([ref], variables={"REGISTRY": "reg.io"})
    assert result[0].confidence == Confidence.LOW


def test_no_op_when_no_unresolved_variables() -> None:
    """Refs without unresolved_variables are passed through unchanged."""
    ref = _make_ref("reg.io/app:v1", unresolved=[])
    ref = ref.model_copy(
        update={
            "registry": "reg.io",
            "name": "app",
            "tag": "v1",
            "confidence": Confidence.HIGH,
        }
    )
    result = resolve_bake([ref], variables={"REGISTRY": "reg.io"})
    assert result[0] is ref


def test_input_refs_not_mutated() -> None:
    """Input ImageReference objects are never modified in-place."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    original_raw = ref.raw
    resolve_bake([ref], variables={"REGISTRY": "reg.io"})
    assert ref.raw == original_raw


def test_empty_input_list() -> None:
    """Empty input returns empty output."""
    assert resolve_bake([], variables={"REGISTRY": "reg.io"}) == []


def test_none_variables_treated_as_empty() -> None:
    """Passing variables=None behaves like an empty dict (no substitution)."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    result = resolve_bake([ref], variables=None)
    # Nothing resolved — original ref returned unchanged
    assert result[0].raw == "${REGISTRY}/app:v1"
    assert "REGISTRY" in result[0].unresolved_variables
