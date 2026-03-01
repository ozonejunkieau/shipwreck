"""Ansible task file parser for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import (
    extract_variables,
    is_template_string,
    parse_image_string,
    validate_image_ref,
)

# Known Ansible modules that manage Docker/Podman containers and accept an `image:` argument.
DOCKER_MODULES: frozenset[str] = frozenset(
    {
        "community.docker.docker_container",
        "community.docker.docker_compose",
        "community.docker.docker_compose_v2",
        "docker_container",
        "containers.podman.podman_container",
    }
)

# Regex to detect a Jinja2 lookup() call inside a template expression.
_LOOKUP_RE = re.compile(r"\{\{.*?lookup\s*\(", re.DOTALL)

# Regex to detect loop variable references like {{ item }} or {{ item.something }}.
_ITEM_RE = re.compile(r"\{\{\s*item(\s|\.|,|\}\})")

# Regex to match a single Jinja2 {{ variable }} expression (used for substitution).
_JINJA2_EXPR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")

# Task-level structural metadata keys — not module invocations.
_TASK_STRUCTURAL: frozenset[str] = frozenset(
    {
        "block",
        "rescue",
        "always",
        "name",
        "when",
        "loop",
        "with_items",
        "tags",
        "notify",
        "register",
        "become",
        "become_user",
        "ignore_errors",
        "no_log",
        "vars",
        "environment",
        "args",
        "delegate_to",
        "delegate_facts",
        "changed_when",
        "failed_when",
        "until",
        "retries",
        "delay",
        "listen",
        "check_mode",
        "diff",
        "loop_control",
        "with_list",
    }
)

# Play-level task list keys — indicate a play dict rather than a task dict.
_PLAY_TASK_LISTS: tuple[str, ...] = ("tasks", "pre_tasks", "post_tasks", "handlers")

# Play-level keys that are not module invocations (combined with _TASK_STRUCTURAL).
_PLAY_AND_STRUCTURAL: frozenset[str] = _TASK_STRUCTURAL | frozenset(
    {
        "hosts",
        "gather_facts",
        "roles",
        "serial",
        "connection",
        "remote_user",
        "port",
        "any_errors_fatal",
        "max_fail_percentage",
        "run_once",
        "debugger",
        "module_defaults",
        "collections",
        "strategy",
        "order",
        "force_handlers",
        "tasks",
        "pre_tasks",
        "post_tasks",
        "handlers",
    }
)


def _load_role_vars(file_path: Path) -> dict[str, str]:
    """Load variable definitions from ``defaults/main.yml`` and ``vars/main.yml``
    of the role that contains *file_path*, if any.

    Walks up the directory tree looking for a ``roles/<role_name>/`` ancestor.
    When found, loads both ``defaults/main.yml`` and ``vars/main.yml`` (vars
    take precedence over defaults).

    Args:
        file_path: Path to the YAML file being parsed.

    Returns:
        Mapping of variable name to string value.
    """
    vars_map: dict[str, str] = {}

    # Walk upward to find a directory named "roles" with a role subdirectory.
    parts = file_path.parts
    for i, part in enumerate(parts):
        if part == "roles" and i + 1 < len(parts):
            role_dir = Path(*parts[: i + 2])
            for subdir in ("defaults", "vars"):
                candidate = role_dir / subdir / "main.yml"
                if candidate.is_file():
                    try:
                        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                    except yaml.YAMLError:
                        continue
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(k, str) and v is not None:
                                vars_map[k] = str(v)
            break

    return vars_map


def _find_image_line(raw_text: str, image_value: str) -> int:
    """Return the 1-indexed line number where *image_value* first appears as an
    ``image:`` YAML value in *raw_text*.

    Searches for lines matching ``image: <value>`` (with optional quotes).
    Falls back to line 1 if the value cannot be located.

    Args:
        raw_text: The full text content of the file.
        image_value: The raw image string to locate.

    Returns:
        1-indexed line number.
    """
    escaped = re.escape(image_value)
    pattern = re.compile(
        r"^\s*image\s*:\s*[\"']?" + escaped + r"[\"']?\s*$",
        re.MULTILINE,
    )
    m = pattern.search(raw_text)
    if m:
        return raw_text[: m.start()].count("\n") + 1
    return 1


def _resolve_simple_template(template: str, vars_map: dict[str, str]) -> str | None:
    """Attempt to resolve a Jinja2 template using *vars_map*.

    Only resolves templates where every ``{{ var }}`` token can be substituted
    from *vars_map* and there are no lookup() calls or ``item`` references.

    Args:
        template: The raw Jinja2 template string.
        vars_map: Available variable bindings.

    Returns:
        The resolved string, or ``None`` if any variable is missing.
    """
    if _LOOKUP_RE.search(template) or _ITEM_RE.search(template):
        return None

    def replacer(m: re.Match[str]) -> str:
        var = m.group(1).strip()
        # Handle dotted access (e.g. item.image) — can't resolve these statically.
        if "." in var:
            raise KeyError(var)
        return vars_map[var]

    try:
        return _JINJA2_EXPR_RE.sub(replacer, template)
    except KeyError:
        return None


def _extract_tasks(data: Any) -> list[dict[str, Any]]:
    """Recursively extract all task dicts from an Ansible data structure.

    Handles:
    - Plain lists of task dicts
    - ``block:`` / ``rescue:`` / ``always:`` structures
    - Playbook dicts (with ``tasks:``, ``pre_tasks:``, ``post_tasks:`` keys)
    - Lists of play dicts (playbook files — recursed into ``tasks`` of each play)

    Args:
        data: Parsed YAML data (may be a list, dict, or scalar).

    Returns:
        Flat list of task dicts.
    """
    tasks: list[dict[str, Any]] = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            # Block construct — recurse into block/rescue/always sections.
            for section in ("block", "rescue", "always"):
                if section in item:
                    tasks.extend(_extract_tasks(item[section]))
            # Play-level dict — recurse into its task list keys.
            for task_list_key in _PLAY_TASK_LISTS:
                if task_list_key in item:
                    tasks.extend(_extract_tasks(item[task_list_key]))
            # Only include this dict as a task candidate if it has keys that
            # could be module invocations (i.e. not purely structural/play-level).
            non_structural = set(item.keys()) - _PLAY_AND_STRUCTURAL
            if non_structural:
                tasks.append(item)
    elif isinstance(data, dict):
        # Single play dict (rare, but handle for robustness).
        for key in _PLAY_TASK_LISTS:
            if key in data:
                tasks.extend(_extract_tasks(data[key]))

    return tasks


def _extract_image_from_task(
    task: dict[str, Any],
) -> str | None:
    """Extract the ``image:`` value from a task dict if it uses a known Docker module.

    The module argument dict may be nested under the module key (fully-qualified
    form) or the image may appear as a sibling key when using ``args:`` notation.

    Args:
        task: A single Ansible task dict.

    Returns:
        The raw image string, or ``None`` if not found / wrong module.
    """
    for module_name in DOCKER_MODULES:
        if module_name not in task:
            continue
        module_args = task[module_name]
        if isinstance(module_args, dict):
            image = module_args.get("image")
            if image is not None:
                return str(image)
        # ``module_args`` may be a string (free-form) — skip for now.
    return None


class AnsibleParser:
    """Parser that extracts image references from Ansible task and playbook YAML files.

    Handles:

    - Direct ``image:`` fields under known Docker/Podman container modules
    - Jinja2 template strings (``{{ var }}``) — recorded as LOW-confidence with
      unresolved variable names
    - Lookup function calls — recorded as unresolved
    - Loop variables (``{{ item.image }}``) — recorded as unresolved
    - Role ``defaults/main.yml`` and ``vars/main.yml`` as a variable resolution context
      for simple single-level templates
    - ``include_tasks:`` / ``import_tasks:`` references recorded in metadata
    - ``block:`` / ``rescue:`` / ``always:`` sections are fully scanned
    - ``when:`` conditionals do not suppress extraction
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "ansible"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this file lives in an Ansible-structured directory tree.

        A file qualifies when its path contains at least one of the canonical
        Ansible directory names: ``tasks``, ``roles``, ``handlers``,
        ``playbooks``, or ``plays``.

        Args:
            file_path: Candidate file path.

        Returns:
            True when the path matches the Ansible directory convention.
        """
        if file_path.suffix not in (".yml", ".yaml"):
            return False
        parts = file_path.parts
        ansible_indicators = {"tasks", "roles", "handlers", "playbooks", "plays"}
        return bool(ansible_indicators & set(parts))

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse an Ansible YAML file and return all discovered image references.

        Walks all task lists in the file, looking for tasks that use a known
        Docker or Podman module and that have an ``image:`` argument.

        For each image value found:

        - If it is a plain string with no template markers: ``CONSUMES``,
          ``HIGH`` confidence.
        - If it contains Jinja2 ``{{ variable }}`` markers and can be fully
          resolved using role defaults/vars: ``CONSUMES``, ``MEDIUM``
          confidence.
        - If it contains unresolvable markers (lookup, item, missing vars):
          ``CONSUMES``, ``LOW`` confidence with ``unresolved_variables`` set.

        ``include_tasks:`` and ``import_tasks:`` references are collected and
        recorded in the metadata of the *first* returned reference (if any).

        Args:
            file_path: Absolute path to the Ansible YAML file.
            repo_name: Repository identifier stored in ``SourceLocation``.

        Returns:
            List of ``ImageReference`` objects discovered in the file.
        """
        raw_text = file_path.read_text(encoding="utf-8")

        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            return []

        if data is None:
            return []

        # Load role variable context (defaults + vars).
        role_vars = _load_role_vars(file_path)

        # Extract all task dicts from the parsed structure.
        tasks = _extract_tasks(data)

        refs: list[ImageReference] = []
        include_refs: list[str] = []

        for task in tasks:
            if not isinstance(task, dict):
                continue

            # Collect include/import task references for metadata.
            for inc_key in ("include_tasks", "import_tasks"):
                if inc_key in task:
                    val = task[inc_key]
                    if isinstance(val, str) and val:
                        include_refs.append(val)
                    elif isinstance(val, dict):
                        # include_tasks can be an object with a `file:` key.
                        inner = val.get("file") or val.get("name")
                        if isinstance(inner, str) and inner:
                            include_refs.append(inner)

            raw_image = _extract_image_from_task(task)
            if raw_image is None:
                continue

            # For plain (non-template) strings, validate that it looks like an image.
            # Template strings with {{ }} are allowed through even though they contain
            # characters (spaces) that would normally fail validation.
            if not is_template_string(raw_image) and not validate_image_ref(raw_image):
                continue

            metadata: dict[str, Any] = {}

            # Determine confidence and whether we can resolve the template.
            if is_template_string(raw_image):
                # Check for lookup() calls — always unresolved.
                if _LOOKUP_RE.search(raw_image):
                    unresolved = extract_variables(raw_image)
                    # Lookup calls use function syntax; ensure at least one marker
                    # is recorded so callers know the template is unresolvable.
                    if not unresolved:
                        unresolved = ["lookup"]
                    confidence = Confidence.LOW
                    resolved_image = raw_image
                # Check for loop item references.
                elif _ITEM_RE.search(raw_image):
                    unresolved = extract_variables(raw_image)
                    confidence = Confidence.LOW
                    resolved_image = raw_image
                    # Capture loop context so resolution.ansible can unwind it
                    loop_val = task.get("loop") or task.get("with_items") or task.get("with_list")
                    if loop_val is not None:
                        metadata["loop"] = loop_val
                    # loop_var may be at task level or under loop_control
                    loop_ctrl = task.get("loop_control")
                    if isinstance(loop_ctrl, dict) and "loop_var" in loop_ctrl:
                        metadata["loop_var"] = loop_ctrl["loop_var"]
                    elif "loop_var" in task:
                        metadata["loop_var"] = task["loop_var"]
                    task_vars = task.get("vars")
                    if isinstance(task_vars, dict):
                        metadata["task_vars"] = task_vars
                else:
                    # Attempt resolution using role variables.
                    resolved = _resolve_simple_template(raw_image, role_vars)
                    if resolved is not None and not is_template_string(resolved):
                        resolved_image = resolved
                        unresolved = []
                        confidence = Confidence.MEDIUM
                    else:
                        resolved_image = raw_image
                        unresolved = extract_variables(raw_image)
                        confidence = Confidence.LOW
            else:
                resolved_image = raw_image
                unresolved = []
                confidence = Confidence.HIGH

            registry, name, tag, parse_unresolved = parse_image_string(resolved_image)

            # Merge any extra unresolved variables surfaced by the image parser.
            for v in parse_unresolved:
                if v not in unresolved:
                    unresolved.append(v)

            line_number = _find_image_line(raw_text, raw_image)

            source = SourceLocation(
                repo=repo_name,
                file=str(file_path),
                line=line_number,
                parser=self.name,
            )

            ref = ImageReference(
                raw=raw_image,
                registry=registry,
                name=name,
                tag=tag,
                source=source,
                relationship=EdgeType.CONSUMES,
                confidence=confidence,
                unresolved_variables=unresolved,
                metadata=metadata,
            )
            refs.append(ref)

        # Attach include_refs to the metadata of all refs (or store separately
        # on the first ref for traceability).
        if include_refs:
            if refs:
                refs[0].metadata["include_tasks"] = include_refs
            # If no image refs were found but there are includes, we still want
            # to surface the include list — create a sentinel ref only if the
            # caller needs it.  For now, includes without image refs are silently
            # available via the return value's first metadata entry if refs exist.

        return refs
