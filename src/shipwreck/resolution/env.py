"""Environment variable resolution for Shipwreck image references."""

from __future__ import annotations

import os
import re

from shipwreck.models import Confidence, ImageReference
from shipwreck.parsers.base import parse_image_string

# Matches ${VAR} and $VAR style references
_ENV_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def resolve_env(
    refs: list[ImageReference],
    env: dict[str, str] | None = None,
) -> list[ImageReference]:
    """Resolve ``${VAR}``/``$VAR`` references from environment variables.

    For each ``ImageReference`` that has ``unresolved_variables``, attempt to
    substitute their values from *env* (or ``os.environ`` when *env* is
    ``None``).  After substitution, re-parse the resulting string with
    :func:`~shipwreck.parsers.base.parse_image_string` to update
    ``registry``, ``name``, and ``tag``.

    A new ``ImageReference`` object is returned for every input ref — input
    objects are never mutated.  Confidence is raised to ``MEDIUM`` when at
    least one variable was resolved and no variables remain unresolved.

    Args:
        refs: List of image references to process.
        env: Optional environment mapping.  Defaults to ``os.environ``.

    Returns:
        New list of ``ImageReference`` objects with as many variables
        substituted as possible.
    """
    effective_env: dict[str, str] = os.environ if env is None else env
    result: list[ImageReference] = []

    for ref in refs:
        if not ref.unresolved_variables:
            result.append(ref)
            continue

        still_unresolved: list[str] = []
        any_resolved = False

        def _replacer(m: re.Match[str]) -> str:
            nonlocal any_resolved
            var = m.group(1)
            if var in effective_env:
                any_resolved = True
                return effective_env[var]
            if var not in still_unresolved:
                still_unresolved.append(var)
            return m.group(0)

        resolved_raw = _ENV_VAR_RE.sub(_replacer, ref.raw)

        if not any_resolved:
            result.append(ref)
            continue

        registry, name, tag, parse_unresolved = parse_image_string(resolved_raw)

        # Merge any extra unresolved variables surfaced by the image parser.
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
