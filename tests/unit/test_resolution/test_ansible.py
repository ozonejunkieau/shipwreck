"""Unit tests for resolution.ansible — Ansible variable resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import yaml

from shipwreck.config import AnsibleConfig
from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.resolution.ansible import (
    _build_playbook,
    _parse_playbook_output,
    resolve_ansible_playbook,
    resolve_ansible_simple,
)


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
            file="/repo/roles/myapp/tasks/main.yml",
            line=5,
            parser="ansible",
        ),
        relationship=EdgeType.CONSUMES,
        confidence=confidence,
        unresolved_variables=unresolved or [],
    )


# ---------------------------------------------------------------------------
# resolve_ansible_simple
# ---------------------------------------------------------------------------


def test_simple_single_var_resolved() -> None:
    """{{ var }} is replaced when var is present in variables dict."""
    ref = _make_ref("{{ registry }}/myapp:latest", unresolved=["registry"])
    result = resolve_ansible_simple([ref], variables={"registry": "reg.io"})

    assert len(result) == 1
    resolved = result[0]
    assert resolved.raw == "reg.io/myapp:latest"
    assert resolved.unresolved_variables == []


def test_simple_multiple_vars_in_one_string() -> None:
    """Multiple {{ var }} tokens are all substituted."""
    ref = _make_ref(
        "{{ registry }}/{{ org }}/app:{{ tag }}",
        unresolved=["registry", "org", "tag"],
    )
    variables = {"registry": "reg.io", "org": "acme", "tag": "1.0.0"}
    result = resolve_ansible_simple([ref], variables=variables)

    assert result[0].raw == "reg.io/acme/app:1.0.0"
    assert result[0].unresolved_variables == []


def test_simple_unresolvable_var_remains() -> None:
    """Unknown variables are left as {{ var }} and reported as unresolved."""
    ref = _make_ref("{{ unknown_registry }}/app:v1", unresolved=["unknown_registry"])
    result = resolve_ansible_simple([ref], variables={})

    assert result[0].raw == "{{ unknown_registry }}/app:v1"
    assert "unknown_registry" in result[0].unresolved_variables


def test_simple_confidence_raised_to_medium() -> None:
    """Confidence becomes MEDIUM when all variables are resolved."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    result = resolve_ansible_simple([ref], variables={"registry": "reg.io"})
    assert result[0].confidence == Confidence.MEDIUM


def test_simple_confidence_unchanged_when_unresolved_remain() -> None:
    """Confidence stays LOW when some variables are still unresolved."""
    ref = _make_ref("{{ registry }}/{{ missing }}/app:v1", unresolved=["registry", "missing"])
    result = resolve_ansible_simple([ref], variables={"registry": "reg.io"})
    assert result[0].confidence == Confidence.LOW


def test_simple_no_op_when_no_unresolved_variables() -> None:
    """Refs with no unresolved_variables pass through unchanged."""
    ref = _make_ref("nginx:1.25", unresolved=[])
    ref = ref.model_copy(
        update={
            "registry": "docker.io",
            "name": "library/nginx",
            "tag": "1.25",
            "confidence": Confidence.HIGH,
        }
    )
    result = resolve_ansible_simple([ref], variables={"registry": "reg.io"})
    assert result[0] is ref


def test_simple_input_refs_not_mutated() -> None:
    """Input ImageReference objects are never modified in-place."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    original_raw = ref.raw
    resolve_ansible_simple([ref], variables={"registry": "reg.io"})
    assert ref.raw == original_raw


# ---------------------------------------------------------------------------
# _build_playbook helper
# ---------------------------------------------------------------------------


def test_build_playbook_contains_marker() -> None:
    """Generated playbook includes SHIPWRECK_RESOLVE markers for each unresolved ref."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    playbook_str = _build_playbook([ref])
    data = yaml.safe_load(playbook_str)

    assert isinstance(data, list)
    play = data[0]
    tasks = play["tasks"]
    assert len(tasks) == 1
    msg = tasks[0]["debug"]["msg"]
    assert "SHIPWRECK_RESOLVE" in msg
    assert "{{ registry }}/app:v1" in msg


def test_build_playbook_skips_already_resolved_refs() -> None:
    """Refs with no unresolved_variables are excluded from the playbook."""
    resolved_ref = _make_ref("nginx:1.25", unresolved=[])
    unresolved_ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    playbook_str = _build_playbook([resolved_ref, unresolved_ref])
    data = yaml.safe_load(playbook_str)
    tasks = data[0]["tasks"]
    # Only one task (for the unresolved ref)
    assert len(tasks) == 1


# ---------------------------------------------------------------------------
# _parse_playbook_output helper
# ---------------------------------------------------------------------------


def test_parse_playbook_output_basic() -> None:
    """SHIPWRECK_RESOLVE|idx|value lines are parsed correctly."""
    stdout = (
        'ok: [localhost] => {\n'
        '    "msg": "SHIPWRECK_RESOLVE|0|nginx:1.25"\n'
        '}\n'
    )
    resolved = _parse_playbook_output(stdout)
    assert resolved == {0: "nginx:1.25"}


def test_parse_playbook_output_multiple_refs() -> None:
    """Multiple SHIPWRECK_RESOLVE lines are all parsed."""
    stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|1|redis:7.0"}\n'
    )
    resolved = _parse_playbook_output(stdout)
    assert resolved[0] == "reg.io/app:v1"
    assert resolved[1] == "redis:7.0"


def test_parse_playbook_output_no_markers() -> None:
    """Output without SHIPWRECK_RESOLVE lines returns empty dict."""
    stdout = "PLAY [all] ****\nok: [localhost]\nPLAY RECAP\n"
    resolved = _parse_playbook_output(stdout)
    assert resolved == {}


# ---------------------------------------------------------------------------
# resolve_ansible_playbook — subprocess mocking
# ---------------------------------------------------------------------------


def test_playbook_resolution_via_mocked_subprocess() -> None:
    """resolve_ansible_playbook uses parsed subprocess output to resolve refs."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
    )

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible_playbook([ref])

    assert len(result) == 1
    assert result[0].raw == "reg.io/app:v1"
    assert result[0].unresolved_variables == []
    assert result[0].confidence == Confidence.MEDIUM


def test_playbook_fallback_when_ansible_not_found() -> None:
    """When ansible-playbook is not installed, falls back to simple (no-op) resolution."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    with patch(
        "shipwreck.resolution.ansible.subprocess.run",
        side_effect=FileNotFoundError("ansible-playbook not found"),
    ):
        result = resolve_ansible_playbook([ref])

    # Simple fallback with empty vars = no-op, original ref unchanged
    assert result[0].raw == "{{ registry }}/app:v1"
    assert "registry" in result[0].unresolved_variables


def test_playbook_fallback_when_ansible_returns_nonzero() -> None:
    """When ansible-playbook fails (nonzero exit), falls back to simple (no-op) resolution."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible_playbook([ref])

    assert result[0].raw == "{{ registry }}/app:v1"
    assert "registry" in result[0].unresolved_variables


def test_playbook_ansible_config_passed_to_subprocess() -> None:
    """AnsibleConfig inventory/limit/vault args are forwarded to ansible-playbook."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    config = AnsibleConfig(
        inventory="/etc/ansible/hosts",
        limit="webservers",
        vault_password_file="/etc/vault-pass",
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
    )

    with patch(
        "shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc
    ) as mock_run:
        resolve_ansible_playbook([ref], ansible_config=config)

    call_args = mock_run.call_args[0][0]  # first positional arg is the cmd list
    assert "-i" in call_args
    assert "/etc/ansible/hosts" in call_args
    assert "--limit" in call_args
    assert "webservers" in call_args
    assert "--vault-password-file" in call_args
    assert "/etc/vault-pass" in call_args


def test_playbook_no_op_when_all_refs_resolved() -> None:
    """When no refs have unresolved variables, subprocess is never called."""
    ref = _make_ref("nginx:1.25", unresolved=[])

    with patch("shipwreck.resolution.ansible.subprocess.run") as mock_run:
        result = resolve_ansible_playbook([ref])

    mock_run.assert_not_called()
    assert result[0] is ref
