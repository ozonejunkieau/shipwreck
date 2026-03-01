"""Unit tests for resolution.ansible — Ansible variable resolution via ansible-playbook."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from shipwreck.config import AnsibleConfig
from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.resolution.ansible import (
    _build_playbook,
    _find_playbook_dir,
    _parse_playbook_output,
    resolve_ansible,
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


def test_build_playbook_tasks_have_ignore_errors() -> None:
    """Generated tasks include ignore_errors so one failure doesn't stop the run."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    playbook_str = _build_playbook([ref])
    data = yaml.safe_load(playbook_str)
    task = data[0]["tasks"][0]
    assert task["ignore_errors"] is True


# ---------------------------------------------------------------------------
# _build_playbook — loop context pass-through
# ---------------------------------------------------------------------------


def test_build_playbook_includes_loop_context() -> None:
    """Generated playbook includes loop from ref metadata."""
    ref = _make_ref("{{ item.image }}", unresolved=["item"])
    ref.metadata["loop"] = [
        {"name": "a", "image": "nginx:1.25"},
        {"name": "b", "image": "redis:7"},
    ]
    playbook_str = _build_playbook([ref])
    data = yaml.safe_load(playbook_str)

    task = data[0]["tasks"][0]
    assert "loop" in task
    assert len(task["loop"]) == 2  # noqa: PLR2004
    assert task["loop"][0]["image"] == "nginx:1.25"


def test_build_playbook_includes_loop_control() -> None:
    """Generated playbook includes loop_control with loop_var from ref metadata."""
    ref = _make_ref("{{ svc.image }}", unresolved=["svc"])
    ref.metadata["loop"] = [{"name": "a", "image": "nginx:1.25"}]
    ref.metadata["loop_var"] = "svc"
    playbook_str = _build_playbook([ref])
    data = yaml.safe_load(playbook_str)

    task = data[0]["tasks"][0]
    assert "loop_control" in task
    assert task["loop_control"]["loop_var"] == "svc"


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
    assert resolved == {0: ["nginx:1.25"]}


def test_parse_playbook_output_multiple_refs() -> None:
    """Multiple SHIPWRECK_RESOLVE lines are all parsed."""
    stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|1|redis:7.0"}\n'
    )
    resolved = _parse_playbook_output(stdout)
    assert resolved[0] == ["reg.io/app:v1"]
    assert resolved[1] == ["redis:7.0"]


def test_parse_playbook_output_no_markers() -> None:
    """Output without SHIPWRECK_RESOLVE lines returns empty dict."""
    stdout = "PLAY [all] ****\nok: [localhost]\nPLAY RECAP\n"
    resolved = _parse_playbook_output(stdout)
    assert resolved == {}


def test_parse_playbook_output_ignores_error_context() -> None:
    """Markers echoed in ansible error context (raw YAML lines) are not matched."""
    # When a task fails with ignore_errors, ansible echoes the raw YAML line
    # containing the marker but without JSON quoting. The regex must only match
    # markers inside JSON strings (preceded by ").
    stdout = (
        '[ERROR]: Task failed: ...\n'
        '3   tasks:\n'
        '4   - debug:\n'
        '5       msg: SHIPWRECK_RESOLVE|0|{{ internal_registry }}/base/python:3.12-slim\n'
        '             ^ column 12\n'
        'fatal: [localhost]: FAILED! => {"msg": "error..."}\n'
        '...ignoring\n'
    )
    resolved = _parse_playbook_output(stdout)
    assert resolved == {}


def test_parse_playbook_output_multi_value_loop() -> None:
    """Multiple SHIPWRECK_RESOLVE lines with same idx are collected as a list."""
    stdout = (
        'ok: [localhost] => (item={...}) => {"msg": "SHIPWRECK_RESOLVE|0|nginx:1.25"}\n'
        'ok: [localhost] => (item={...}) => {"msg": "SHIPWRECK_RESOLVE|0|redis:7.0"}\n'
    )
    resolved = _parse_playbook_output(stdout)
    assert 0 in resolved
    assert resolved[0] == ["nginx:1.25", "redis:7.0"]


# ---------------------------------------------------------------------------
# resolve_ansible — subprocess mocking
# ---------------------------------------------------------------------------


def test_resolution_via_mocked_subprocess() -> None:
    """resolve_ansible uses parsed subprocess output to resolve refs."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
    )

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible([ref])

    assert len(result) == 1
    assert result[0].raw == "reg.io/app:v1"
    assert result[0].unresolved_variables == []
    assert result[0].confidence == Confidence.MEDIUM


def test_fallback_when_ansible_not_found() -> None:
    """When ansible-playbook is not installed, refs are returned unchanged."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    with patch(
        "shipwreck.resolution.ansible.subprocess.run",
        side_effect=FileNotFoundError("ansible-playbook not found"),
    ):
        result = resolve_ansible([ref])

    assert result[0].raw == "{{ registry }}/app:v1"
    assert "registry" in result[0].unresolved_variables


def test_fallback_when_ansible_returns_nonzero() -> None:
    """When ansible-playbook fails, successfully parsed refs are still used."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "some error"

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible([ref])

    # No markers parsed from empty stdout, refs returned unchanged
    assert result[0].raw == "{{ registry }}/app:v1"
    assert "registry" in result[0].unresolved_variables


def test_ansible_config_passed_to_subprocess() -> None:
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
        resolve_ansible([ref], ansible_config=config)

    call_args = mock_run.call_args[0][0]  # first positional arg is the cmd list
    assert "-i" in call_args
    assert "/etc/ansible/hosts" in call_args
    assert "--limit" in call_args
    assert "webservers" in call_args
    assert "--vault-password-file" in call_args
    assert "/etc/vault-pass" in call_args


def test_no_op_when_all_refs_resolved() -> None:
    """When no refs have unresolved variables, subprocess is never called."""
    ref = _make_ref("nginx:1.25", unresolved=[])

    with patch("shipwreck.resolution.ansible.subprocess.run") as mock_run:
        result = resolve_ansible([ref])

    mock_run.assert_not_called()
    assert result[0] is ref


def test_loop_expansion() -> None:
    """Loop ref is expanded into multiple resolved ImageReference objects."""
    ref = _make_ref("{{ item.image }}", unresolved=["item"])
    ref.metadata["loop"] = [
        {"name": "a", "image": "nginx:1.25"},
        {"name": "b", "image": "redis:7"},
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => (item={...}) => {"msg": "SHIPWRECK_RESOLVE|0|nginx:1.25"}\n'
        'ok: [localhost] => (item={...}) => {"msg": "SHIPWRECK_RESOLVE|0|redis:7"}\n'
    )

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible([ref])

    assert len(result) == 2  # noqa: PLR2004
    raws = [r.raw for r in result]
    assert "nginx:1.25" in raws
    assert "redis:7" in raws
    assert all(r.confidence == Confidence.MEDIUM for r in result)


def test_partial_resolution_with_ignore_errors() -> None:
    """Some tasks fail (ignore_errors) while others resolve successfully."""
    ref_ok = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])
    ref_fail = _make_ref("{{ unknown }}/app:v1", unresolved=["unknown"])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
        # ref 1 failed (ignore_errors), no SHIPWRECK_RESOLVE|1| line
    )

    with patch("shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc):
        result = resolve_ansible([ref_ok, ref_fail])

    assert len(result) == 2  # noqa: PLR2004
    assert result[0].raw == "reg.io/app:v1"
    assert result[0].confidence == Confidence.MEDIUM
    # Failed ref passes through unchanged
    assert result[1].raw == "{{ unknown }}/app:v1"
    assert "unknown" in result[1].unresolved_variables


# ---------------------------------------------------------------------------
# _find_playbook_dir helper
# ---------------------------------------------------------------------------


def test_find_playbook_dir_returns_role_root(tmp_path: Path) -> None:
    """Role root is returned when a ref's source file is inside roles/<name>/."""
    role_root = tmp_path / "roles" / "worker"
    tasks_dir = role_root / "tasks"
    tasks_dir.mkdir(parents=True)
    (role_root / "files").mkdir()

    ref = _make_ref("{{ x }}", unresolved=["x"])
    ref.source.file = str(tasks_dir / "main.yml")

    assert _find_playbook_dir([ref]) == role_root


def test_find_playbook_dir_returns_none_without_role() -> None:
    """None is returned when no role directory structure is detected."""
    ref = _make_ref("{{ x }}", unresolved=["x"])
    ref.source.file = "/some/playbooks/site.yml"

    assert _find_playbook_dir([ref]) is None


def test_playbook_dir_config_overrides_autodetect(tmp_path: Path) -> None:
    """AnsibleConfig.playbook_dir takes priority over auto-detected role root."""
    ref = _make_ref("{{ registry }}/app:v1", unresolved=["registry"])

    config = AnsibleConfig(
        inventory="localhost,",
        playbook_dir=str(tmp_path),
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = (
        'ok: [localhost] => {"msg": "SHIPWRECK_RESOLVE|0|reg.io/app:v1"}\n'
    )

    with patch(
        "shipwreck.resolution.ansible.subprocess.run", return_value=mock_proc
    ) as mock_run:
        resolve_ansible([ref], ansible_config=config)

    # The playbook path should be inside the configured playbook_dir
    playbook_path = mock_run.call_args[0][0][-1]  # last arg is the playbook path
    assert playbook_path.startswith(str(tmp_path))
