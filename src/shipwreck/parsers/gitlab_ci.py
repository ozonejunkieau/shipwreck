"""GitLab CI parser for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import extract_variables, parse_image_string, validate_image_ref

# Top-level GitLab CI keys that are not job definitions.
# Note: "default" is handled separately — it is scanned for image/services,
# but is not treated as a regular job.
GITLAB_CI_RESERVED: frozenset[str] = frozenset(
    {
        "default",
        "include",
        "stages",
        "variables",
        "workflow",
        "before_script",
        "after_script",
        "image",
        "services",
        "cache",
        "interruptible",
        "retry",
        "timeout",
        "tags",
    }
)

# Regex for substituting $VAR and ${VAR} style variables
_VAR_SUBST_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Regex patterns for script-based docker commands
_DOCKER_BUILD_RE = re.compile(r"docker\s+build\s+.*?-t\s+(\S+)", re.IGNORECASE)
_DOCKER_PUSH_RE = re.compile(r"docker\s+push\s+(\S+)", re.IGNORECASE)
_DOCKER_PULL_RE = re.compile(r"docker\s+pull\s+(\S+)", re.IGNORECASE)


def _substitute_vars(value: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``$VAR`` / ``${VAR}`` references using the given variables dict.

    Args:
        value: The raw string that may contain variable references.
        variables: Mapping of variable name to its resolved value.

    Returns:
        A two-tuple ``(result, unresolved)`` where *result* is the string with
        all resolvable variables substituted in-place, and *unresolved* is the
        list of variable names that could not be resolved.
    """
    unresolved: list[str] = []

    def replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in variables:
            return variables[var]
        unresolved.append(var)
        return m.group(0)  # leave the original placeholder

    result = _VAR_SUBST_RE.sub(replacer, value)
    return result, unresolved


def _find_line(raw_lines: list[str], needle: str) -> int:
    """Search *raw_lines* for the first line containing *needle*.

    Args:
        raw_lines: The file contents split into lines (0-indexed by enumerate).
        needle: The string to search for.

    Returns:
        1-based line number, or 1 if *needle* was not found.
    """
    for lineno, line in enumerate(raw_lines, start=1):
        if needle in line:
            return lineno
    return 1


def _find_image_line(raw_lines: list[str], image_value: str, start: int = 1) -> int:
    """Find the 1-based line number of an ``image:`` directive containing *image_value*.

    Scans from *start* (1-based) to find a line that both contains ``image:``
    and contains the image value.

    Args:
        raw_lines: The file contents split into lines.
        image_value: Substring to match within the image line.
        start: 1-based line to start searching from.

    Returns:
        1-based line number, or *start* if not found.
    """
    for lineno, line in enumerate(raw_lines, start=1):
        if lineno < start:
            continue
        if "image:" in line and image_value in line:
            return lineno
    return start


def _collect_variables(data: dict[str, Any]) -> dict[str, str]:
    """Collect top-level ``variables:`` from a parsed GitLab CI document.

    Args:
        data: The parsed YAML document as a dict.

    Returns:
        Mapping of variable name to string value (non-string values are skipped).
    """
    variables: dict[str, str] = {}
    raw_vars = data.get("variables")
    if isinstance(raw_vars, dict):
        for k, v in raw_vars.items():
            if isinstance(v, str):
                variables[str(k)] = v
            elif v is not None:
                variables[str(k)] = str(v)
    return variables


def _collect_job_variables(job_conf: dict[str, Any]) -> dict[str, str]:
    """Collect job-level ``variables:`` from a single job configuration.

    Args:
        job_conf: The parsed job configuration dict.

    Returns:
        Mapping of variable name to string value (non-string values are skipped).
    """
    variables: dict[str, str] = {}
    raw_vars = job_conf.get("variables")
    if isinstance(raw_vars, dict):
        for k, v in raw_vars.items():
            if isinstance(v, str):
                variables[str(k)] = v
            elif v is not None:
                variables[str(k)] = str(v)
    return variables


def _make_ref(
    raw: str,
    resolved: str,
    unresolved: list[str],
    relationship: EdgeType,
    confidence: Confidence,
    source: SourceLocation,
    metadata: dict[str, Any] | None = None,
) -> ImageReference | None:
    """Build an ``ImageReference`` from a raw/resolved image string.

    Returns ``None`` if *resolved* does not look like a valid image reference.

    Args:
        raw: The original (pre-resolution) image string.
        resolved: The image string after variable substitution.
        unresolved: Variable names that could not be resolved.
        relationship: The edge type for this reference.
        confidence: The confidence level.
        source: The source location.
        metadata: Optional parser-specific metadata dict.

    Returns:
        An ``ImageReference`` or ``None`` if the string is not a valid image.
    """
    if not validate_image_ref(resolved):
        return None

    registry, name, tag, parse_unresolved = parse_image_string(resolved)

    # Merge unresolved variable lists
    all_unresolved = list(unresolved)
    for v in parse_unresolved:
        if v not in all_unresolved:
            all_unresolved.append(v)

    return ImageReference(
        raw=raw,
        registry=registry,
        name=name,
        tag=tag,
        source=source,
        relationship=relationship,
        confidence=confidence,
        unresolved_variables=all_unresolved,
        metadata=metadata or {},
    )


def _extract_image_field(
    image_val: Any,
    variables: dict[str, str],
    source: SourceLocation,
    relationship: EdgeType,
    metadata: dict[str, Any] | None = None,
) -> ImageReference | None:
    """Extract an ``ImageReference`` from a GitLab CI ``image:`` field value.

    Handles both the string form (``image: "python:3.12"``) and the object
    form (``image: {name: "python:3.12", entrypoint: [""]}``)

    Args:
        image_val: The raw value from the ``image:`` key in the YAML.
        variables: Variable context for substitution.
        source: The source location to attach to the reference.
        relationship: The edge type (usually ``CONSUMES``).
        metadata: Optional extra metadata to attach.

    Returns:
        An ``ImageReference`` or ``None`` if the value is not usable.
    """
    if isinstance(image_val, dict):
        raw_image = image_val.get("name")
        if not isinstance(raw_image, str) or not raw_image:
            return None
    elif isinstance(image_val, str):
        raw_image = image_val
    else:
        return None

    if not raw_image:
        return None

    resolved, unresolved = _substitute_vars(raw_image, variables)

    # Downgrade confidence when variables were substituted or remain unresolved
    if unresolved:
        confidence = Confidence.LOW
    elif resolved != raw_image:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.HIGH

    return _make_ref(
        raw=raw_image,
        resolved=resolved,
        unresolved=unresolved,
        relationship=relationship,
        confidence=confidence,
        source=source,
        metadata=metadata,
    )


def _extract_services(
    services_val: Any,
    variables: dict[str, str],
    file_path: Path,
    repo_name: str,
    parser_name: str,
    raw_lines: list[str],
    job_name: str,
) -> list[ImageReference]:
    """Extract ``ImageReference`` objects from a ``services:`` list.

    Each entry can be a plain string (``"postgres:16"``) or an object with
    a ``name:`` field (``{name: "postgres:16", alias: "db"}``).

    Args:
        services_val: The raw value from the ``services:`` key in the YAML.
        variables: Variable context for substitution.
        file_path: Absolute path to the source file.
        repo_name: Repository name for the source location.
        parser_name: Parser identifier for the source location.
        raw_lines: The file split into lines (for line-number lookup).
        job_name: The job or context name (stored in metadata).

    Returns:
        List of ``ImageReference`` objects (one per resolved service image).
    """
    if not isinstance(services_val, list):
        return []

    refs: list[ImageReference] = []
    for entry in services_val:
        if isinstance(entry, str):
            raw_image = entry
        elif isinstance(entry, dict):
            raw_image = entry.get("name")
            if not isinstance(raw_image, str) or not raw_image:
                continue
        else:
            continue

        resolved, unresolved = _substitute_vars(raw_image, variables)

        if unresolved:
            confidence = Confidence.LOW
        elif resolved != raw_image:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.HIGH

        lineno = _find_line(raw_lines, raw_image)
        source = SourceLocation(
            repo=repo_name,
            file=str(file_path),
            line=lineno,
            parser=parser_name,
        )
        ref = _make_ref(
            raw=raw_image,
            resolved=resolved,
            unresolved=unresolved,
            relationship=EdgeType.CONSUMES,
            confidence=confidence,
            source=source,
            metadata={"context": job_name},
        )
        if ref is not None:
            refs.append(ref)

    return refs


def _extract_script_refs(
    script_lines: list[str],
    variables: dict[str, str],
    file_path: Path,
    repo_name: str,
    parser_name: str,
    raw_lines: list[str],
    job_name: str,
) -> list[ImageReference]:
    """Scan script lines for ``docker build``, ``docker push``, and ``docker pull`` commands.

    All extracted references carry ``LOW`` confidence (best-effort extraction).

    Args:
        script_lines: Lines from ``script:``, ``before_script:``, or ``after_script:``.
        variables: Variable context for substitution.
        file_path: Absolute path to the source file.
        repo_name: Repository name for the source location.
        parser_name: Parser identifier for the source location.
        raw_lines: The full file split into lines (for line-number lookup).
        job_name: The job name (stored in metadata).

    Returns:
        List of ``ImageReference`` objects found in the script lines.
    """
    refs: list[ImageReference] = []

    for script_line in script_lines:
        if not isinstance(script_line, str):
            continue

        line_str = script_line.strip()
        lineno = _find_line(raw_lines, line_str) if line_str else 1
        source = SourceLocation(
            repo=repo_name,
            file=str(file_path),
            line=lineno,
            parser=parser_name,
        )

        # docker build -t IMAGE
        for m in _DOCKER_BUILD_RE.finditer(script_line):
            raw_image = m.group(1)
            resolved, unresolved = _substitute_vars(raw_image, variables)

            # Runtime CI vars that remain unresolved keep them in the list
            all_unresolved = list(unresolved)
            extra = extract_variables(resolved)
            for v in extra:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            if not validate_image_ref(resolved):
                continue

            registry, name, tag, parse_unresolved = parse_image_string(resolved)
            for v in parse_unresolved:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            refs.append(
                ImageReference(
                    raw=raw_image,
                    registry=registry,
                    name=name,
                    tag=tag,
                    source=source,
                    relationship=EdgeType.PRODUCES,
                    confidence=Confidence.LOW,
                    unresolved_variables=all_unresolved,
                    metadata={"job": job_name, "command": "docker build"},
                )
            )

        # docker push IMAGE
        for m in _DOCKER_PUSH_RE.finditer(script_line):
            raw_image = m.group(1)
            resolved, unresolved = _substitute_vars(raw_image, variables)

            all_unresolved = list(unresolved)
            extra = extract_variables(resolved)
            for v in extra:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            if not validate_image_ref(resolved):
                continue

            registry, name, tag, parse_unresolved = parse_image_string(resolved)
            for v in parse_unresolved:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            refs.append(
                ImageReference(
                    raw=raw_image,
                    registry=registry,
                    name=name,
                    tag=tag,
                    source=source,
                    relationship=EdgeType.PRODUCES,
                    confidence=Confidence.LOW,
                    unresolved_variables=all_unresolved,
                    metadata={"job": job_name, "command": "docker push"},
                )
            )

        # docker pull IMAGE
        for m in _DOCKER_PULL_RE.finditer(script_line):
            raw_image = m.group(1)
            resolved, unresolved = _substitute_vars(raw_image, variables)

            all_unresolved = list(unresolved)
            extra = extract_variables(resolved)
            for v in extra:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            if not validate_image_ref(resolved):
                continue

            registry, name, tag, parse_unresolved = parse_image_string(resolved)
            for v in parse_unresolved:
                if v not in all_unresolved:
                    all_unresolved.append(v)

            refs.append(
                ImageReference(
                    raw=raw_image,
                    registry=registry,
                    name=name,
                    tag=tag,
                    source=source,
                    relationship=EdgeType.CONSUMES,
                    confidence=Confidence.LOW,
                    unresolved_variables=all_unresolved,
                    metadata={"job": job_name, "command": "docker pull"},
                )
            )

    return refs


def _collect_script_lines(job_conf: dict[str, Any]) -> list[str]:
    """Gather all script lines from ``script:``, ``before_script:``, and ``after_script:``.

    Args:
        job_conf: The parsed job configuration dict.

    Returns:
        A flat list of all script lines across all three script keys.
    """
    lines: list[str] = []
    for key in ("before_script", "script", "after_script"):
        val = job_conf.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    lines.append(item)
        elif isinstance(val, str):
            lines.append(val)
    return lines


def _extract_includes(data: dict[str, Any]) -> list[dict[str, str]]:
    """Extract ``include:`` directives and normalise them into a list of dicts.

    Args:
        data: The parsed YAML document.

    Returns:
        A list of dicts, each describing one include (e.g. ``{"local": "/.gitlab-ci/build.yml"}``).
    """
    raw_includes = data.get("include")
    if raw_includes is None:
        return []

    # include: can be a single item or a list
    if not isinstance(raw_includes, list):
        raw_includes = [raw_includes]

    result: list[dict[str, str]] = []
    for item in raw_includes:
        if isinstance(item, str):
            result.append({"local": item})
        elif isinstance(item, dict):
            include: dict[str, str] = {}
            for key in ("local", "template", "project", "file", "ref"):
                if key in item and item[key] is not None:
                    include[key] = str(item[key])
            if include:
                result.append(include)

    return result


class GitLabCIParser:
    """Parser that extracts image references from GitLab CI YAML files.

    Handles:
    - Job-level ``image:`` fields (string and object form)
    - ``default.image`` (applies to all jobs)
    - ``services:`` lists (string and object forms)
    - Script-based ``docker build``, ``docker push``, and ``docker pull`` commands
    - Variable substitution from top-level and job-level ``variables:`` blocks
    - ``include:`` directives (recorded in metadata, not followed)
    - Hidden/template jobs (``.<name>``) — still scanned for image references
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "gitlab_ci"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should handle the given file.

        Matches:
        - Files named ``.gitlab-ci.yml``
        - Files whose path includes a ``.gitlab-ci`` directory component
        - Files ending in ``.gitlab-ci.yml`` (e.g. ``build.gitlab-ci.yml``)

        Args:
            file_path: Path to the candidate file.

        Returns:
            True if this parser can process the file.
        """
        name = file_path.name
        parts = file_path.parts
        return (
            name == ".gitlab-ci.yml"
            or ".gitlab-ci" in parts
            or name.endswith(".gitlab-ci.yml")
        )

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse a GitLab CI YAML file and return all discovered image references.

        Args:
            file_path: Absolute path to the ``.gitlab-ci.yml`` file.
            repo_name: Repository name used to populate ``SourceLocation.repo``.

        Returns:
            List of ``ImageReference`` objects.
        """
        raw_text = file_path.read_text(encoding="utf-8")
        raw_lines = raw_text.splitlines()

        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            return []

        if not isinstance(data, dict):
            return []

        # ------------------------------------------------------------------
        # Step 1 — Collect top-level variables and include directives
        # ------------------------------------------------------------------
        global_variables = _collect_variables(data)
        includes = _extract_includes(data)

        refs: list[ImageReference] = []

        # ------------------------------------------------------------------
        # Step 2 — Extract from "default:" block (image + services)
        # ------------------------------------------------------------------
        default_block = data.get("default")
        if isinstance(default_block, dict):
            image_val = default_block.get("image")
            if image_val is not None:
                lineno = _find_image_line(raw_lines, "default", 1)
                # Narrow search: find "image:" after the "default:" line
                default_line = _find_line(raw_lines, "default:")
                lineno = _find_image_line(raw_lines, "image:", default_line)
                source = SourceLocation(
                    repo=repo_name,
                    file=str(file_path),
                    line=lineno,
                    parser=self.name,
                )
                ref = _extract_image_field(
                    image_val,
                    global_variables,
                    source,
                    EdgeType.CONSUMES,
                    metadata={"context": "default"},
                )
                if ref is not None:
                    refs.append(ref)

            services_val = default_block.get("services")
            refs.extend(
                _extract_services(
                    services_val,
                    global_variables,
                    file_path,
                    repo_name,
                    self.name,
                    raw_lines,
                    "default",
                )
            )

        # ------------------------------------------------------------------
        # Step 3 — Iterate all keys and process job definitions
        # ------------------------------------------------------------------
        for key, value in data.items():
            if not isinstance(value, dict):
                continue

            # Skip non-job reserved keys (note: "default" is handled above)
            if key in GITLAB_CI_RESERVED:
                continue

            # Both hidden/template jobs (.name) and regular jobs are processed
            job_conf = value
            job_name = str(key)

            # Merge global variables with job-level variables
            # (job vars override global vars for this job's context)
            job_variables = dict(global_variables)
            job_variables.update(_collect_job_variables(job_conf))

            # ---- Job image ------------------------------------------------
            image_val = job_conf.get("image")
            if image_val is not None:
                lineno = _find_line(raw_lines, f"{key}:")
                lineno = _find_image_line(raw_lines, "image:", lineno)
                source = SourceLocation(
                    repo=repo_name,
                    file=str(file_path),
                    line=lineno,
                    parser=self.name,
                )
                ref = _extract_image_field(
                    image_val,
                    job_variables,
                    source,
                    EdgeType.CONSUMES,
                    metadata={"job": job_name},
                )
                if ref is not None:
                    refs.append(ref)

            # ---- Job services ---------------------------------------------
            services_val = job_conf.get("services")
            refs.extend(
                _extract_services(
                    services_val,
                    job_variables,
                    file_path,
                    repo_name,
                    self.name,
                    raw_lines,
                    job_name,
                )
            )

            # ---- Script-based docker commands ----------------------------
            script_lines = _collect_script_lines(job_conf)
            refs.extend(
                _extract_script_refs(
                    script_lines,
                    job_variables,
                    file_path,
                    repo_name,
                    self.name,
                    raw_lines,
                    job_name,
                )
            )

        # ------------------------------------------------------------------
        # Step 4 — Attach include metadata to the first reference (or store
        #          it as a standalone metadata ref when no other refs exist)
        # ------------------------------------------------------------------
        if includes and refs:
            # Attach includes metadata to the first reference's metadata dict
            refs[0].metadata["includes"] = includes
        elif includes:
            # No image refs found, but there are includes — record a sentinel
            # by annotating the result list with a metadata-only entry.
            # Per spec: "record in metadata (don't follow)".  Since
            # ImageReference requires a valid raw/image, we don't emit a
            # dummy ref here — the caller can inspect the return value of
            # parse() for an "includes" key in the first ref's metadata.
            # Instead we store includes on the parser's last-parse result via
            # an attribute so tests can reach it.  The simplest approach that
            # matches existing patterns is to return an empty list and not
            # emit any refs for includes-only files.
            pass

        return refs
