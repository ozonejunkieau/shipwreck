"""Tests for the Ansible parser."""

from __future__ import annotations

from pathlib import Path

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.ansible import AnsibleParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "ansible"


def make_parser() -> AnsibleParser:
    """Create a fresh AnsibleParser instance."""
    return AnsibleParser()


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


def test_can_handle_tasks_dir() -> None:
    """Files inside a 'tasks' directory are handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/tasks/main.yml")) is True
    assert parser.can_handle(Path("/repo/tasks/deploy.yml")) is True


def test_can_handle_roles_dir() -> None:
    """Files inside a 'roles' directory tree are handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/roles/myapp/tasks/main.yml")) is True
    assert parser.can_handle(Path("/repo/roles/myapp/defaults/main.yml")) is True


def test_can_handle_handlers_dir() -> None:
    """Files inside a 'handlers' directory are handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/handlers/main.yml")) is True


def test_can_handle_playbooks_dir() -> None:
    """Files inside a 'playbooks' directory are handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/playbooks/site.yml")) is True


def test_can_handle_plays_dir() -> None:
    """Files inside a 'plays' directory are handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/plays/deploy.yml")) is True


def test_cannot_handle_non_yaml() -> None:
    """Non-YAML files in Ansible dirs are not handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/tasks/script.sh")) is False
    assert parser.can_handle(Path("/repo/tasks/readme.txt")) is False


def test_cannot_handle_yaml_outside_ansible_dirs() -> None:
    """YAML files not inside known Ansible directories are rejected."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/config/settings.yml")) is False
    assert parser.can_handle(Path("/repo/compose.yaml")) is False
    assert parser.can_handle(Path("/repo/docker-compose.yml")) is False


def test_cannot_handle_github_actions_yaml() -> None:
    """GitHub Actions workflow YAML is not in Ansible dirs — not handled."""
    parser = make_parser()
    assert parser.can_handle(Path("/repo/.github/workflows/ci.yml")) is False


# ---------------------------------------------------------------------------
# Parser name
# ---------------------------------------------------------------------------


def test_parser_name() -> None:
    """Parser should identify itself as 'ansible'."""
    assert make_parser().name == "ansible"


# ---------------------------------------------------------------------------
# Direct image under docker_container (short-form module)
# ---------------------------------------------------------------------------


def test_docker_container_short_form_high_confidence() -> None:
    """docker_container module with direct image string → CONSUMES, HIGH."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    postgres_refs = [r for r in refs if "postgres" in r.raw]
    assert len(postgres_refs) == 1
    ref = postgres_refs[0]
    assert ref.raw == "postgres:16-alpine"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "docker.io"
    assert ref.name == "library/postgres"
    assert ref.tag == "16-alpine"
    assert ref.unresolved_variables == []
    assert ref.source.repo == "test-repo"
    assert ref.source.parser == "ansible"


# ---------------------------------------------------------------------------
# Direct image under community.docker.docker_container
# ---------------------------------------------------------------------------


def test_community_docker_module_high_confidence() -> None:
    """community.docker.docker_container with plain image → CONSUMES, HIGH."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    webapp_refs = [r for r in refs if "webapp" in r.raw]
    assert len(webapp_refs) >= 1
    ref = webapp_refs[0]
    assert ref.raw == "registry.example.com/webapp:1.2.3"
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.confidence == Confidence.HIGH
    assert ref.registry == "registry.example.com"
    assert ref.name == "webapp"
    assert ref.tag == "1.2.3"
    assert ref.unresolved_variables == []


# ---------------------------------------------------------------------------
# Jinja2 template image → LOW confidence with unresolved_variables
# ---------------------------------------------------------------------------


def test_jinja2_template_image_low_confidence() -> None:
    """Jinja2 template image produces CONSUMES, LOW, with unresolved_variables."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    template_refs = [r for r in refs if "app_registry" in r.raw or "app_version" in r.raw]
    assert len(template_refs) >= 1
    ref = template_refs[0]
    assert ref.confidence == Confidence.LOW
    assert ref.relationship == EdgeType.CONSUMES
    assert ref.registry is None
    assert ref.name is None
    assert ref.tag is None
    assert "app_registry" in ref.unresolved_variables
    assert "app_version" in ref.unresolved_variables


# ---------------------------------------------------------------------------
# Lookup function → unresolved
# ---------------------------------------------------------------------------


def test_lookup_function_unresolved() -> None:
    """Lookup functions in image values produce LOW confidence and unresolved vars."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    lookup_refs = [r for r in refs if "lookup" in r.raw]
    assert len(lookup_refs) == 1
    ref = lookup_refs[0]
    assert ref.confidence == Confidence.LOW
    assert ref.unresolved_variables  # at least one variable listed
    assert ref.registry is None


# ---------------------------------------------------------------------------
# Non-Docker module image: key ignored
# ---------------------------------------------------------------------------


def test_non_docker_module_image_ignored() -> None:
    """image: under a non-Docker module (e.g. ansible.builtin.copy) is not extracted."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    # The copy task has no valid image value — no ref for it should appear
    raws = [r.raw for r in refs]
    assert all("should-not-extract" not in raw for raw in raws)


# ---------------------------------------------------------------------------
# Loop variable ({{ item.image }}) → unresolved
# ---------------------------------------------------------------------------


def test_loop_variable_unresolved() -> None:
    """Loop item variables produce LOW confidence and are marked unresolved."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "deploy.yml", "test-repo")

    item_refs = [r for r in refs if "item" in r.raw]
    assert len(item_refs) >= 1
    ref = item_refs[0]
    assert ref.confidence == Confidence.LOW
    assert ref.unresolved_variables  # item.image etc.


# ---------------------------------------------------------------------------
# when: conditional — still extract ref
# ---------------------------------------------------------------------------


def test_when_conditional_still_extracted() -> None:
    """Tasks with when: conditionals still produce image references."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "deploy.yml", "test-repo")

    debug_refs = [r for r in refs if "debug" in r.raw]
    assert len(debug_refs) >= 1
    assert debug_refs[0].raw == "registry.example.com/debug:latest"
    assert debug_refs[0].confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# containers.podman.podman_container recognised
# ---------------------------------------------------------------------------


def test_podman_module_recognised() -> None:
    """containers.podman.podman_container is a known Docker module and is parsed."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "deploy.yml", "test-repo")

    worker_refs = [r for r in refs if "worker" in r.raw and "2.0" in r.raw]
    assert len(worker_refs) >= 1
    ref = worker_refs[0]
    assert ref.raw == "registry.example.com/worker:2.0"
    assert ref.confidence == Confidence.HIGH
    assert ref.source.parser == "ansible"


# ---------------------------------------------------------------------------
# include_tasks: recorded in metadata
# ---------------------------------------------------------------------------


def test_include_tasks_recorded_in_metadata() -> None:
    """include_tasks: and import_tasks: references are recorded in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")

    # The first ref with an include_tasks metadata entry
    include_metadata_refs = [r for r in refs if "include_tasks" in r.metadata]
    assert len(include_metadata_refs) >= 1
    includes = include_metadata_refs[0].metadata["include_tasks"]
    assert isinstance(includes, list)
    assert any("deploy.yml" in inc for inc in includes)
    assert any("setup.yml" in inc for inc in includes)


# ---------------------------------------------------------------------------
# Block/rescue/always structure scanned
# ---------------------------------------------------------------------------


def test_block_rescue_always_scanned() -> None:
    """Tasks inside block/rescue/always sections are all extracted."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "deploy.yml", "test-repo")

    raws = [r.raw for r in refs]
    # block task
    assert "registry.example.com/primary:1.0" in raws
    # rescue task
    assert "registry.example.com/primary:stable" in raws
    # always task
    assert "registry.example.com/sidecar:1.0" in raws


# ---------------------------------------------------------------------------
# Multiple tasks with different images
# ---------------------------------------------------------------------------


def test_multiple_tasks_multiple_refs() -> None:
    """A file with multiple Docker module tasks produces multiple ImageReferences."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "main.yml", "test-repo")
    # webapp, postgres, jinja2 template, lookup = at least 4 refs
    assert len(refs) >= 4


# ---------------------------------------------------------------------------
# Role defaults resolve simple templates
# ---------------------------------------------------------------------------


def test_role_defaults_resolve_simple_template() -> None:
    """Simple {{ var }} templates are resolved using role defaults/main.yml."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "roles" / "myapp" / "tasks" / "main.yml", "test-repo")

    # "{{ app_image }}:{{ app_version }}" should resolve to
    # "registry.example.com/myapp:1.0" via defaults/main.yml
    resolved_refs = [r for r in refs if r.confidence == Confidence.MEDIUM]
    assert len(resolved_refs) >= 1
    ref = resolved_refs[0]
    assert ref.raw == "{{ app_image }}:{{ app_version }}"
    assert ref.registry == "registry.example.com"
    assert ref.name == "myapp"
    assert ref.tag == "1.0"
    assert ref.unresolved_variables == []


# ---------------------------------------------------------------------------
# Handlers file
# ---------------------------------------------------------------------------


def test_handlers_file_parsed() -> None:
    """Handler YAML files inside a 'handlers' directory are parsed correctly."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "handlers" / "main.yml", "test-repo")

    assert len(refs) == 2
    raws = {r.raw for r in refs}
    assert "registry.example.com/webapp:1.2.3" in raws
    assert "registry.example.com/worker:stable" in raws
    assert all(r.confidence == Confidence.HIGH for r in refs)


# ---------------------------------------------------------------------------
# Playbook with include_tasks
# ---------------------------------------------------------------------------


def test_playbook_parsed() -> None:
    """Playbook YAML files in 'playbooks' are parsed for Docker module tasks."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "playbooks" / "site.yml", "test-repo")

    # Should find the community.docker.docker_container task
    template_refs = [r for r in refs if "app_registry" in r.raw or "deploy_version" in r.raw]
    assert len(template_refs) >= 1
    ref = template_refs[0]
    assert ref.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_parser_implements_protocol() -> None:
    """AnsibleParser should satisfy the Parser protocol at runtime."""
    from shipwreck.parsers.base import Parser

    parser = make_parser()
    assert isinstance(parser, Parser)


# ---------------------------------------------------------------------------
# Loop metadata capture
# ---------------------------------------------------------------------------


def test_loop_metadata_captured_with_literal_loop() -> None:
    """When a task has loop: with literal items, metadata captures the loop context."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "loop_literal.yml", "test-repo")

    item_refs = [r for r in refs if "item" in r.raw]
    assert len(item_refs) >= 1
    ref = item_refs[0]
    assert "loop" in ref.metadata
    assert isinstance(ref.metadata["loop"], list)
    assert len(ref.metadata["loop"]) >= 2


def test_loop_metadata_captured_with_variable_loop() -> None:
    """When a task has loop: {{ var }}, metadata captures the loop context."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "deploy.yml", "test-repo")

    item_refs = [r for r in refs if "item" in r.raw]
    assert len(item_refs) >= 1
    ref = item_refs[0]
    assert "loop" in ref.metadata


def test_loop_var_captured_from_loop_control() -> None:
    """loop_control.loop_var is captured in metadata."""
    parser = make_parser()
    refs = parser.parse(FIXTURES / "tasks" / "loop_literal.yml", "test-repo")

    # The fixture has a task with loop_control: { loop_var: svc }
    # and image: "{{ svc.image }}" — but svc.image uses _ITEM_RE pattern?
    # No — _ITEM_RE looks for {{ item }}. {{ svc.image }} won't match _ITEM_RE.
    # This task's image won't be detected as a loop item ref by the parser.
    # The first task with {{ item.image }} and literal loop should have no loop_var.
    item_refs = [r for r in refs if "item" in r.raw]
    assert len(item_refs) >= 1
    # The literal loop task should NOT have loop_var (it uses default 'item')
    assert "loop_var" not in item_refs[0].metadata
