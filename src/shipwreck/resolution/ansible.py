"""Ansible variable resolution for Shipwreck image references."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from shipwreck.models import Confidence, ImageReference
from shipwreck.parsers.base import parse_image_string

if TYPE_CHECKING:
    from shipwreck.config import AnsibleConfig

# Matches {{ var }} style Jinja2 expressions (single identifier, no dots/calls)
_JINJA2_EXPR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# Marker prefix written to stdout by the generated playbook
_MARKER = "SHIPWRECK_RESOLVE"

# Regex to extract SHIPWRECK_RESOLVE|idx|value from ansible-playbook stdout
_MARKER_RE = re.compile(r"SHIPWRECK_RESOLVE\|(\d+)\|([^\s\"'}]+)")


def _substitute_simple(value: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``{{ var }}`` expressions in *value* using *variables*.

    Only resolves simple single-identifier tokens.  Dotted access and Jinja2
    filters are left in place and reported as unresolved.

    Args:
        value: The raw Jinja2 template string.
        variables: Flat mapping of variable name to value.

    Returns:
        ``(result, unresolved)`` where *result* is the substituted string and
        *unresolved* lists variable names that could not be resolved.
    """
    unresolved: list[str] = []

    def _replacer(m: re.Match[str]) -> str:
        var = m.group(1).strip()
        if var in variables:
            return variables[var]
        if var not in unresolved:
            unresolved.append(var)
        return m.group(0)

    result = _JINJA2_EXPR_RE.sub(_replacer, value)
    return result, unresolved


def resolve_ansible_simple(
    refs: list[ImageReference],
    variables: dict[str, str],
) -> list[ImageReference]:
    """Resolve ``{{ var }}`` Jinja2 references from a flat variable dict.

    Performs simple single-token substitution only.  Complex expressions
    (dotted access, filters, lookups) are left unmodified and reported as
    unresolved.

    A new ``ImageReference`` object is returned for every input ref — input
    objects are never mutated.  Confidence is raised to ``MEDIUM`` when at
    least one variable was resolved and no variables remain unresolved.

    Args:
        refs: List of image references to process.
        variables: Flat mapping of Ansible variable names to values.

    Returns:
        New list of ``ImageReference`` objects with as many variables
        substituted as possible.
    """
    result: list[ImageReference] = []

    for ref in refs:
        if not ref.unresolved_variables:
            result.append(ref)
            continue

        resolved_raw, still_unresolved = _substitute_simple(ref.raw, variables)

        if resolved_raw == ref.raw:
            result.append(ref)
            continue

        registry, name, tag, parse_unresolved = parse_image_string(resolved_raw)

        for v in parse_unresolved:
            if v not in still_unresolved:
                still_unresolved.append(v)

        if still_unresolved:
            confidence = ref.confidence
        else:
            confidence = Confidence.MEDIUM

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
        tasks.append(
            {
                "name": f"Resolve shipwreck ref {idx}",
                "debug": {"msg": f"{_MARKER}|{idx}|{ref.raw}"},
            }
        )

    if not tasks:
        return yaml.dump([{"hosts": "all", "gather_facts": False, "tasks": []}])

    play = {
        "hosts": "all",
        "gather_facts": False,
        "tasks": tasks,
    }
    return yaml.dump([play])


def _parse_playbook_output(stdout: str) -> dict[int, str]:
    """Extract resolved values from ``ansible-playbook`` stdout.

    Scans for ``SHIPWRECK_RESOLVE|<index>|<value>`` tokens anywhere in the
    output (e.g. inside ``"msg": "SHIPWRECK_RESOLVE|0|nginx:1.25"`` lines).

    The value is terminated by any of: whitespace, ``"``, ``'``, ``}``.

    Args:
        stdout: Full stdout text from ``ansible-playbook``.

    Returns:
        Mapping of integer ref index to resolved image string.
    """
    resolved: dict[int, str] = {}
    for m in _MARKER_RE.finditer(stdout):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        resolved[idx] = m.group(2)
    return resolved


def resolve_ansible_playbook(
    refs: list[ImageReference],
    ansible_config: AnsibleConfig | None = None,
) -> list[ImageReference]:
    """Resolve Ansible Jinja2 templates via a generated ``ansible-playbook`` run.

    Generates a temporary playbook that renders each unresolved image template
    and emits ``SHIPWRECK_RESOLVE|<index>|<value>`` markers to stdout.
    Parses those markers to build the resolved image strings.

    Falls back to :func:`resolve_ansible_simple` with an empty variable dict
    (i.e. a no-op) when ``ansible-playbook`` is not available or the run
    fails.

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

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yml",
        delete=False,
        prefix="shipwreck_resolve_",
    ) as tmp:
        tmp.write(playbook_content)
        playbook_path = tmp.name

    try:
        proc = subprocess.run(
            [*cmd, playbook_path],
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = proc.stdout
    except FileNotFoundError:
        # ansible-playbook not installed — fall back gracefully
        return resolve_ansible_simple(refs, {})
    finally:
        Path(playbook_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        # Playbook failed — fall back to simple (no-op) resolution
        return resolve_ansible_simple(refs, {})

    resolved_map = _parse_playbook_output(stdout)

    result: list[ImageReference] = []
    for idx, ref in enumerate(refs):
        if not ref.unresolved_variables or idx not in resolved_map:
            result.append(ref)
            continue

        resolved_raw = resolved_map[idx]
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
