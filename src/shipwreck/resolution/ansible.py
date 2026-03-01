"""Ansible variable resolution for Shipwreck image references."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from shipwreck.models import Confidence, ImageReference
from shipwreck.parsers.base import parse_image_string

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from shipwreck.config import AnsibleConfig

# Marker prefix written to stdout by the generated playbook
_MARKER = "SHIPWRECK_RESOLVE"

# Regex to extract SHIPWRECK_RESOLVE|idx|value from ansible-playbook stdout.
# The leading " ensures we only match inside JSON strings from successful task
# output (e.g. "msg": "SHIPWRECK_RESOLVE|0|nginx:1.25") and NOT from error
# context where ansible echoes the raw YAML line containing the marker.
_MARKER_RE = re.compile(r"\"SHIPWRECK_RESOLVE\|(\d+)\|([^\s\"'}]+)")


def _build_playbook(refs: list[ImageReference]) -> str:
    """Generate a temporary Ansible playbook that emits SHIPWRECK_RESOLVE lines.

    For each ref with unresolved variables, the playbook includes a
    ``debug`` task that renders the image template and prints it with the
    ``SHIPWRECK_RESOLVE|<index>|<value>`` format so it can be parsed from
    ``ansible-playbook`` stdout.

    Args:
        refs: List of image references with templates to resolve.

    Returns:
        YAML string of the generated playbook.
    """
    tasks = []
    for idx, ref in enumerate(refs):
        if not ref.unresolved_variables:
            continue
        task: dict = {
            "name": f"Resolve shipwreck ref {idx}",
            "debug": {"msg": f"{_MARKER}|{idx}|{ref.raw}"},
            "ignore_errors": True,
        }
        # Pass through loop context so ansible-playbook iterates and
        # emits one SHIPWRECK_RESOLVE line per loop item.
        if "loop" in ref.metadata:
            task["loop"] = ref.metadata["loop"]
        if "loop_var" in ref.metadata:
            task["loop_control"] = {"loop_var": ref.metadata["loop_var"]}
        if "task_vars" in ref.metadata:
            task["vars"] = ref.metadata["task_vars"]
        tasks.append(task)

    if not tasks:
        return yaml.dump([{"hosts": "all", "gather_facts": False, "tasks": []}])

    play = {
        "hosts": "all",
        "gather_facts": False,
        "tasks": tasks,
    }
    return yaml.dump([play])


def _parse_playbook_output(stdout: str) -> dict[int, list[str]]:
    """Extract resolved values from ``ansible-playbook`` stdout.

    Scans for ``SHIPWRECK_RESOLVE|<index>|<value>`` tokens anywhere in the
    output (e.g. inside ``"msg": "SHIPWRECK_RESOLVE|0|nginx:1.25"`` lines).

    The value is terminated by any of: whitespace, ``"``, ``'``, ``}``.

    The same index may appear multiple times when a task uses ``loop:``,
    producing one entry per loop iteration.

    Args:
        stdout: Full stdout text from ``ansible-playbook``.

    Returns:
        Mapping of integer ref index to list of resolved image strings.
    """
    resolved: dict[int, list[str]] = {}
    for m in _MARKER_RE.finditer(stdout):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        resolved.setdefault(idx, []).append(m.group(2))
    return resolved


def _find_playbook_dir(refs: list[ImageReference]) -> Path | None:
    """Find the best directory for the generated playbook.

    Walks up from each unresolved ref's source file to find a role root
    (a directory whose parent is named ``roles``).  Writing the playbook
    to the role root allows ansible's ``lookup('file', ...)`` to resolve
    files from the role's ``files/`` subdirectory.

    Args:
        refs: Image references to inspect.

    Returns:
        Path to a role root directory, or ``None`` if no role context is found.
    """
    for ref in refs:
        if not ref.unresolved_variables:
            continue
        parts = Path(ref.source.file).parts
        for i, part in enumerate(parts):
            if part == "roles" and i + 1 < len(parts):
                role_root = Path(*parts[: i + 2])
                if role_root.is_dir():
                    return role_root
    return None


def resolve_ansible(
    refs: list[ImageReference],
    ansible_config: AnsibleConfig | None = None,
) -> list[ImageReference]:
    """Resolve Ansible Jinja2 templates via ``ansible-playbook``.

    Generates a temporary playbook that renders each unresolved image template
    and emits ``SHIPWRECK_RESOLVE|<index>|<value>`` markers to stdout.
    Parses those markers to build the resolved image strings.

    When ``ansible-playbook`` is not available or the run fails, unresolved
    refs are returned unchanged.

    A new ``ImageReference`` object is returned for every input ref — input
    objects are never mutated.

    Args:
        refs: List of image references to process.
        ansible_config: Optional Ansible connection configuration.  When
            ``None``, defaults to ``localhost`` with no inventory.

    Returns:
        New list of ``ImageReference`` objects with as many variables
        resolved as possible.
    """
    # Refs that actually need resolution
    unresolved_refs = [ref for ref in refs if ref.unresolved_variables]
    if not unresolved_refs:
        return list(refs)

    playbook_content = _build_playbook(refs)
    logger.debug("Generated playbook:\n%s", playbook_content)

    cmd = ["ansible-playbook"]
    if ansible_config is not None:
        cmd += ["-i", ansible_config.inventory]
        if ansible_config.limit:
            cmd += ["--limit", ansible_config.limit]
        if ansible_config.vault_password_file:
            cmd += ["--vault-password-file", ansible_config.vault_password_file]
    else:
        # Default: run against localhost without gathering facts
        cmd += ["-i", "localhost,", "--connection=local"]

    # Place the generated playbook in a role directory when possible so that
    # ansible's lookup('file', ...) searches the role's files/ subdirectory.
    # An explicit playbook_dir in the config takes priority over auto-detection.
    if ansible_config and ansible_config.playbook_dir:
        playbook_dir: Path | None = Path(ansible_config.playbook_dir)
    else:
        playbook_dir = _find_playbook_dir(refs)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yml",
        delete=False,
        prefix="shipwreck_resolve_",
        dir=playbook_dir,
    ) as tmp:
        tmp.write(playbook_content)
        playbook_path = tmp.name

    full_cmd = [*cmd, playbook_path]
    logger.info("Ansible playbook execution: %s", " ".join(full_cmd))
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = proc.stdout
    except FileNotFoundError:
        logger.info("Ansible resolution SKIPPED: ansible-playbook not installed")
        return list(refs)
    finally:
        Path(playbook_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        logger.info("Ansible playbook FAILED (rc=%d): %s", proc.returncode, proc.stderr.strip() or proc.stdout.strip())

    resolved_map = _parse_playbook_output(stdout)

    if not resolved_map:
        return list(refs)

    result: list[ImageReference] = []
    for idx, ref in enumerate(refs):
        if not ref.unresolved_variables or idx not in resolved_map:
            result.append(ref)
            continue

        values = resolved_map[idx]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_values: list[str] = []
        for v in values:
            if v not in seen:
                seen.add(v)
                unique_values.append(v)

        for resolved_raw in unique_values:
            registry, name, tag, parse_unresolved = parse_image_string(resolved_raw)
            still_unresolved = list(parse_unresolved)
            confidence = Confidence.MEDIUM if not still_unresolved else ref.confidence

            result.append(
                ImageReference(
                    raw=resolved_raw,
                    registry=registry,
                    name=name,
                    tag=tag,
                    source=ref.source,
                    relationship=ref.relationship,
                    confidence=confidence,
                    unresolved_variables=still_unresolved,
                    metadata=dict(ref.metadata),
                )
            )

    return result
