"""Docker Bake HCL variable resolution for Shipwreck image references."""

from __future__ import annotations

import re

from shipwreck.models import Confidence, ImageReference
from shipwreck.parsers.base import parse_image_string

# Matches ${VAR} interpolation in HCL strings (no operator support)
_HCL_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _substitute(value: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``${VAR}`` placeholders in *value* using *variables*.

    Args:
        value: The raw template string, e.g. ``"${REGISTRY}/myapp:${VERSION}"``.
        variables: Mapping of variable name to its resolved value.

    Returns:
        ``(result, unresolved)`` where *result* is the substituted string and
        *unresolved* lists variable names that could not be resolved.
    """
    unresolved: list[str] = []

    def _replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in variables:
            return variables[var]
        if var not in unresolved:
            unresolved.append(var)
        return m.group(0)

    result = _HCL_VAR_RE.sub(_replacer, value)
    return result, unresolved


def resolve_bake(
    refs: list[ImageReference],
    variables: dict[str, str] | None = None,
) -> list[ImageReference]:
    """Resolve ``${VAR}`` placeholders from HCL variable block defaults.

    A new ``ImageReference`` object is returned for every input ref — input
    objects are never mutated.  Confidence is raised to ``MEDIUM`` when at
    least one variable was resolved and no variables remain unresolved.

    Args:
        refs: List of image references to process.
        variables: Mapping of variable name to its default value (as collected
            from ``variable { default = "..." }`` blocks in the HCL file).
            Defaults to ``{}``.

    Returns:
        New list of ``ImageReference`` objects with as many variables
        substituted as possible.
    """
    effective_vars: dict[str, str] = variables or {}
    result: list[ImageReference] = []

    for ref in refs:
        if not ref.unresolved_variables:
            result.append(ref)
            continue

        resolved_raw, still_unresolved = _substitute(ref.raw, effective_vars)

        if resolved_raw == ref.raw:
            # Nothing changed — keep the original ref unchanged
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
