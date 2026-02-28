"""Unit tests for resolution.env — environment variable resolution."""

from __future__ import annotations

import os

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.resolution.env import resolve_env


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
        source=SourceLocation(repo="test-repo", file="/repo/Dockerfile", line=1, parser="dockerfile"),
        relationship=EdgeType.CONSUMES,
        confidence=confidence,
        unresolved_variables=unresolved or [],
    )


# ---------------------------------------------------------------------------
# Basic resolution
# ---------------------------------------------------------------------------


def test_resolve_single_var_braces() -> None:
    """${VAR} is replaced when VAR is present in env."""
    ref = _make_ref("${REGISTRY}/myapp:latest", unresolved=["REGISTRY"])
    result = resolve_env([ref], env={"REGISTRY": "registry.example.com"})

    assert len(result) == 1
    resolved = result[0]
    assert resolved.raw == "registry.example.com/myapp:latest"
    assert resolved.registry == "registry.example.com"
    assert resolved.name == "myapp"
    assert resolved.tag == "latest"
    assert resolved.unresolved_variables == []


def test_resolve_single_var_no_braces() -> None:
    """$VAR (without braces) is also substituted."""
    ref = _make_ref("$REGISTRY/myapp:1.0", unresolved=["REGISTRY"])
    result = resolve_env([ref], env={"REGISTRY": "reg.io"})

    assert len(result) == 1
    assert result[0].raw == "reg.io/myapp:1.0"
    assert result[0].unresolved_variables == []


def test_resolve_multiple_vars_in_one_string() -> None:
    """Multiple variables in a single raw string are all substituted."""
    ref = _make_ref(
        "${REGISTRY}/${ORG}/myapp:${VERSION}",
        unresolved=["REGISTRY", "ORG", "VERSION"],
    )
    env = {"REGISTRY": "reg.io", "ORG": "acme", "VERSION": "2.5"}
    result = resolve_env([ref], env=env)

    assert result[0].raw == "reg.io/acme/myapp:2.5"
    assert result[0].unresolved_variables == []


def test_confidence_raised_to_medium_on_full_resolution() -> None:
    """Confidence becomes MEDIUM when all variables are resolved."""
    ref = _make_ref("${REGISTRY}/app:latest", unresolved=["REGISTRY"])
    result = resolve_env([ref], env={"REGISTRY": "reg.io"})
    assert result[0].confidence == Confidence.MEDIUM


def test_confidence_unchanged_when_vars_remain() -> None:
    """Confidence stays LOW when some variables are still unresolved."""
    ref = _make_ref("${REGISTRY}/${UNKNOWN}:latest", unresolved=["REGISTRY", "UNKNOWN"])
    result = resolve_env([ref], env={"REGISTRY": "reg.io"})

    assert result[0].confidence == Confidence.LOW
    assert "UNKNOWN" in result[0].unresolved_variables


def test_no_op_when_no_unresolved_variables() -> None:
    """Refs without unresolved_variables are passed through unchanged."""
    ref = _make_ref("nginx:1.25", unresolved=[])
    ref = ref.model_copy(
        update={
            "registry": "docker.io",
            "name": "library/nginx",
            "tag": "1.25",
            "confidence": Confidence.HIGH,
        }
    )
    result = resolve_env([ref], env={"REGISTRY": "reg.io"})
    assert result[0] is ref  # same object returned


def test_uses_os_environ_when_env_is_none(monkeypatch: object) -> None:
    """When env=None, os.environ is consulted."""
    monkeypatch.setitem(os.environ, "MYREGISTRY", "os-env.io")
    ref = _make_ref("${MYREGISTRY}/app:v1", unresolved=["MYREGISTRY"])
    result = resolve_env([ref], env=None)
    assert result[0].raw == "os-env.io/app:v1"


def test_unknown_var_left_as_is() -> None:
    """If env does not contain the variable, the placeholder is preserved."""
    ref = _make_ref("${MISSING}/app:v1", unresolved=["MISSING"])
    result = resolve_env([ref], env={})
    assert result[0].raw == "${MISSING}/app:v1"
    assert "MISSING" in result[0].unresolved_variables


def test_input_refs_not_mutated() -> None:
    """Input ImageReference objects are not modified in-place."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    original_raw = ref.raw
    resolve_env([ref], env={"REGISTRY": "reg.io"})
    assert ref.raw == original_raw


def test_empty_input_list() -> None:
    """Empty input returns empty output."""
    assert resolve_env([], env={"REGISTRY": "reg.io"}) == []


def test_partial_resolution_preserves_remaining_unresolved() -> None:
    """Only the resolved variables are removed from unresolved_variables."""
    ref = _make_ref(
        "${KNOWN}/${UNKNOWN}:latest",
        unresolved=["KNOWN", "UNKNOWN"],
    )
    result = resolve_env([ref], env={"KNOWN": "reg.io"})
    remaining = result[0].unresolved_variables
    assert "KNOWN" not in remaining
    assert "UNKNOWN" in remaining
