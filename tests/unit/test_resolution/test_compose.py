"""Unit tests for resolution.compose — Docker Compose variable resolution."""

from __future__ import annotations

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.resolution.compose import resolve_compose


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
        source=SourceLocation(repo="test-repo", file="/repo/compose.yaml", line=5, parser="compose"),
        relationship=EdgeType.CONSUMES,
        confidence=confidence,
        unresolved_variables=unresolved or [],
    )


# ---------------------------------------------------------------------------
# ${VAR:-default} — use default when unset or empty
# ---------------------------------------------------------------------------


def test_colon_dash_fallback_used_when_var_absent() -> None:
    """${VAR:-default} resolves to default when VAR is not in env."""
    ref = _make_ref("${REGISTRY:-registry.example.com}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={})

    assert len(result) == 1
    assert result[0].raw == "registry.example.com/app:v1"
    assert result[0].unresolved_variables == []


def test_colon_dash_fallback_used_when_var_empty() -> None:
    """${VAR:-default} resolves to default when VAR is set to empty string."""
    ref = _make_ref("${REGISTRY:-fallback.io}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={"REGISTRY": ""})

    assert result[0].raw == "fallback.io/app:v1"


def test_colon_dash_var_used_when_set() -> None:
    """${VAR:-default} uses VAR value when VAR is set and non-empty."""
    ref = _make_ref("${REGISTRY:-fallback.io}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={"REGISTRY": "actual.io"})

    assert result[0].raw == "actual.io/app:v1"


# ---------------------------------------------------------------------------
# ${VAR-default} — use default only when unset (empty string is kept)
# ---------------------------------------------------------------------------


def test_dash_fallback_used_when_var_absent() -> None:
    """${VAR-default} resolves to default when VAR is absent."""
    ref = _make_ref("${REGISTRY-fallback.io}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={})

    assert result[0].raw == "fallback.io/app:v1"


def test_dash_empty_string_kept() -> None:
    """${VAR-default} keeps empty string when VAR is explicitly set to ''."""
    ref = _make_ref("${TAG-latest}", unresolved=["TAG"])
    result = resolve_compose([ref], env={"TAG": ""})

    # VAR is set (to ""), so the empty string is used, not the default
    assert result[0].raw == ""


# ---------------------------------------------------------------------------
# ${VAR} — plain substitution
# ---------------------------------------------------------------------------


def test_plain_var_resolved_from_env() -> None:
    """${VAR} resolves when VAR is in env."""
    ref = _make_ref("${REGISTRY}/myapp:latest", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={"REGISTRY": "reg.io"})

    assert result[0].raw == "reg.io/myapp:latest"
    assert result[0].unresolved_variables == []


def test_plain_var_unresolved_when_absent() -> None:
    """${VAR} stays unresolved when VAR is missing from env."""
    ref = _make_ref("${MISSING}/app:v1", unresolved=["MISSING"])
    result = resolve_compose([ref], env={})

    assert result[0].unresolved_variables == ["MISSING"]


# ---------------------------------------------------------------------------
# ${VAR:?error} — mark unresolved when unset or empty
# ---------------------------------------------------------------------------


def test_colon_question_left_as_is_when_unresolved() -> None:
    """${VAR:?error} is left in place when VAR is absent."""
    ref = _make_ref("${REGISTRY:?must be set}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={})

    assert "REGISTRY" in result[0].unresolved_variables


# ---------------------------------------------------------------------------
# dotenv takes precedence over env
# ---------------------------------------------------------------------------


def test_dotenv_overrides_env() -> None:
    """dotenv values take precedence over env values."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose(
        [ref],
        env={"REGISTRY": "from-env.io"},
        dotenv={"REGISTRY": "from-dotenv.io"},
    )

    assert result[0].raw == "from-dotenv.io/app:v1"


def test_env_used_when_dotenv_absent() -> None:
    """env values are used when dotenv does not define the variable."""
    ref = _make_ref("${REGISTRY}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={"REGISTRY": "env.io"}, dotenv={})

    assert result[0].raw == "env.io/app:v1"


# ---------------------------------------------------------------------------
# Confidence and immutability
# ---------------------------------------------------------------------------


def test_confidence_raised_to_medium_on_full_resolution() -> None:
    """Confidence becomes MEDIUM when all variables are resolved."""
    ref = _make_ref("${REGISTRY:-reg.io}/app:v1", unresolved=["REGISTRY"])
    result = resolve_compose([ref], env={})
    assert result[0].confidence == Confidence.MEDIUM


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
    result = resolve_compose([ref], env={"REGISTRY": "reg.io"})
    assert result[0] is ref


def test_input_refs_not_mutated() -> None:
    """Input ImageReference objects are never modified in-place."""
    ref = _make_ref("${REGISTRY:-default.io}/app:v1", unresolved=["REGISTRY"])
    original_raw = ref.raw
    resolve_compose([ref], env={})
    assert ref.raw == original_raw


def test_empty_input_list() -> None:
    """Empty input returns empty output."""
    assert resolve_compose([], env={}) == []
