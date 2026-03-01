"""Docker Bake (HCL) parser for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hcl2

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import parse_image_string

# Matches ${VAR} interpolation in HCL strings
_HCL_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(s: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``${VAR}`` references in *s* using *variables*.

    Args:
        s: The raw template string, e.g. ``"${REGISTRY}/myapp:${VERSION}"``.
        variables: Mapping of variable name to its resolved default value.

    Returns:
        A two-tuple ``(result, unresolved)`` where *result* is the string with
        all known variables substituted, and *unresolved* is the list of variable
        names that could not be resolved.
    """
    unresolved: list[str] = []

    def replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in variables:
            return variables[var]
        unresolved.append(var)
        return m.group(0)  # leave the placeholder in place

    result = _HCL_VAR_RE.sub(replacer, s)
    return result, unresolved


def _find_line(raw_lines: list[str], needle: str) -> int:
    """Search *raw_lines* for the first line containing *needle*.

    Args:
        raw_lines: The file contents split into lines (0-indexed).
        needle: The string to search for.

    Returns:
        1-based line number, or 0 if *needle* was not found.
    """
    for lineno, line in enumerate(raw_lines, start=1):
        if needle in line:
            return lineno
    return 0


def _resolve_targets(raw_targets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve ``inherits`` fields so that each target has its full set of fields.

    Target A inherits from target B: A gets all of B's fields unless A already
    defines them.  Resolution is done iteratively (handles chains), but cycles
    are silently ignored (the parent is skipped if it would cause infinite
    recursion).

    Args:
        raw_targets: Mapping of target name → target config dict as returned by
            python-hcl2.

    Returns:
        A new dict with the same keys but with inheritance resolved.
    """
    resolved: dict[str, dict[str, Any]] = {}

    def resolve_one(name: str, ancestors: frozenset[str]) -> dict[str, Any]:
        if name in resolved:
            return resolved[name]
        config = dict(raw_targets.get(name, {}))
        parents: list[str] = config.pop("inherits", []) or []
        merged: dict[str, Any] = {}
        for parent in parents:
            if parent in ancestors or parent not in raw_targets:
                continue
            parent_config = resolve_one(parent, ancestors | {name})
            for key, value in parent_config.items():
                if key not in merged:
                    merged[key] = value
        # Child fields take precedence over inherited ones
        merged.update(config)
        resolved[name] = merged
        return merged

    for target_name in raw_targets:
        resolve_one(target_name, frozenset({target_name}))

    return resolved


class BakeParser:
    """Parser that extracts image references from Docker Bake HCL files.

    Handles:
    - ``docker-bake.hcl`` and ``docker-bake.override.hcl``
    - Variable interpolation (``${VAR}``)
    - Target inheritance (``inherits = ["base"]``)
    - Tag extraction (``PRODUCES`` relationships)
    - ``docker-image://`` context extraction (``BUILDS_FROM`` relationships)
    - ``args`` and ``dockerfile`` metadata recording
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "bake"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should handle the given file.

        Matches:
        - ``docker-bake.hcl``
        - ``docker-bake.override.hcl``

        Args:
            file_path: Path to the candidate file.

        Returns:
            True if this parser can process the file.
        """
        name = file_path.name
        return name in ("docker-bake.hcl", "docker-bake.override.hcl")

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse a Docker Bake HCL file and return all discovered image references.

        Args:
            file_path: Absolute path to the ``.hcl`` bake file.
            repo_name: Repository name used to populate ``SourceLocation.repo``.

        Returns:
            List of ``ImageReference`` objects.  Tags produce ``PRODUCES``
            relationships; ``docker-image://`` contexts produce ``BUILDS_FROM``
            relationships.
        """
        raw_text = file_path.read_text(encoding="utf-8")
        raw_lines = raw_text.splitlines()

        with file_path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = hcl2.load(fh)  # pyright: ignore[reportPrivateImportUsage]

        # ------------------------------------------------------------------
        # Step 1 — Collect variables
        # ------------------------------------------------------------------
        variables: dict[str, str] = {}
        for var_block in data.get("variable", []):
            for var_name, var_config in var_block.items():
                if isinstance(var_config, dict) and "default" in var_config:
                    default = var_config["default"]
                    if default is not None:
                        variables[var_name] = str(default)

        # ------------------------------------------------------------------
        # Step 2 — Collect raw targets and resolve inheritance
        # ------------------------------------------------------------------
        raw_targets: dict[str, dict[str, Any]] = {}
        for target_block in data.get("target", []):
            for target_name, target_config in target_block.items():
                raw_targets[target_name] = dict(target_config) if isinstance(target_config, dict) else {}

        resolved_targets = _resolve_targets(raw_targets)

        # ------------------------------------------------------------------
        # Step 3 & 4 — Extract references from each resolved target
        # ------------------------------------------------------------------
        refs: list[ImageReference] = []

        for target_name, target_config in resolved_targets.items():
            # Shared metadata recorded on every ImageReference from this target
            shared_meta: dict[str, Any] = {"target": target_name}
            if "dockerfile" in target_config:
                shared_meta["dockerfile"] = target_config["dockerfile"]
            if "args" in target_config:
                shared_meta["args"] = target_config["args"]

            # ---- Tags → PRODUCES ----------------------------------------
            tags: list[str] = target_config.get("tags", []) or []
            for raw_tag in tags:
                if not isinstance(raw_tag, str) or not raw_tag:
                    continue

                resolved_tag, unresolved = _interpolate(raw_tag, variables)

                # Determine confidence
                if unresolved:
                    confidence = Confidence.LOW
                elif resolved_tag != raw_tag:
                    # At least one variable was substituted successfully
                    confidence = Confidence.MEDIUM
                else:
                    confidence = Confidence.HIGH

                # Determine the string to pass to parse_image_string:
                # if there are still unresolved placeholders, parse_image_string
                # will see template markers and return all-None components.
                registry, name, tag, parse_unresolved = parse_image_string(resolved_tag)

                # Merge unresolved lists (parse_image_string may extract more)
                all_unresolved = list(unresolved)
                for v in parse_unresolved:
                    if v not in all_unresolved:
                        all_unresolved.append(v)

                line = _find_line(raw_lines, raw_tag)
                source = SourceLocation(
                    repo=repo_name,
                    file=str(file_path),
                    line=line,
                    parser=self.name,
                    scope=target_name,
                )

                refs.append(
                    ImageReference(
                        raw=resolved_tag,
                        registry=registry,
                        name=name,
                        tag=tag,
                        source=source,
                        relationship=EdgeType.PRODUCES,
                        confidence=confidence,
                        unresolved_variables=all_unresolved,
                        metadata=dict(shared_meta),
                    )
                )

            # ---- Contexts → BUILDS_FROM (docker-image:// only) ----------
            contexts: dict[str, str] = target_config.get("contexts", {}) or {}
            for _ctx_name, ctx_value in contexts.items():
                if not isinstance(ctx_value, str):
                    continue
                if not ctx_value.startswith("docker-image://"):
                    continue

                raw_image_ref = ctx_value[len("docker-image://"):]
                resolved_ref, unresolved = _interpolate(raw_image_ref, variables)

                if unresolved:
                    confidence = Confidence.LOW
                elif resolved_ref != raw_image_ref:
                    confidence = Confidence.MEDIUM
                else:
                    confidence = Confidence.HIGH

                registry, name, tag, parse_unresolved = parse_image_string(resolved_ref)

                all_unresolved = list(unresolved)
                for v in parse_unresolved:
                    if v not in all_unresolved:
                        all_unresolved.append(v)

                line = _find_line(raw_lines, ctx_value)
                source = SourceLocation(
                    repo=repo_name,
                    file=str(file_path),
                    line=line,
                    parser=self.name,
                    scope=target_name,
                )

                refs.append(
                    ImageReference(
                        raw=resolved_ref,
                        registry=registry,
                        name=name,
                        tag=tag,
                        source=source,
                        relationship=EdgeType.BUILDS_FROM,
                        confidence=confidence,
                        unresolved_variables=all_unresolved,
                        metadata=dict(shared_meta),
                    )
                )

        return refs
