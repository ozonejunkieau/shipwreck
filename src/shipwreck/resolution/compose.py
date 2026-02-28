"""Docker Compose variable resolution for Shipwreck image references."""

from __future__ import annotations

import re

from shipwreck.models import Confidence, ImageReference
from shipwreck.parsers.base import parse_image_string

# Matches all Compose variable substitution forms:
#   ${VAR:-default}  ${VAR-default}  ${VAR:?err}  ${VAR?err}  ${VAR}
_COMPOSE_VAR_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<colon>:?)(?P<op>[-?])(?P<default>[^}]*)?\}"
    r"|\$\{(?P<plain>[A-Za-z_][A-Za-z0-9_]*)\}"
)


def _substitute(value: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute Compose-style ``${VAR}`` expressions in *value*.

    Resolution semantics:

    - ``${VAR}``          — substitute from *env*; mark unresolved if absent.
    - ``${VAR:-default}`` — use *default* when VAR is unset **or** empty.
    - ``${VAR-default}``  — use *default* only when VAR is unset.
    - ``${VAR:?error}``   — mark unresolved when VAR is unset or empty.
    - ``${VAR?error}``    — mark unresolved when VAR is unset.

    Args:
        value: Raw string that may contain substitution expressions.
        env: Combined environment mapping (dotenv + env).

    Returns:
        ``(resolved_value, unresolved_var_names)``
    """
    unresolved: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        plain = m.group("plain")
        if plain is not None:
            v = env.get(plain)
            if v is None:
                if plain not in unresolved:
                    unresolved.append(plain)
                return m.group(0)
            return v

        name: str = m.group("name")
        colon: str = m.group("colon")  # "" or ":"
        op: str = m.group("op")        # "-" or "?"
        default_val: str = m.group("default") or ""

        v = env.get(name)

        if op == "-":
            if colon == ":":
                # ${VAR:-default}: use default when unset or empty
                if not v:
                    return default_val
                return v
            else:
                # ${VAR-default}: use default only when unset
                if v is None:
                    return default_val
                return v
        else:
            # op == "?"
            if colon == ":":
                # ${VAR:?error}: unresolved when unset or empty
                if not v:
                    if name not in unresolved:
                        unresolved.append(name)
                    return m.group(0)
                return v
            else:
                # ${VAR?error}: unresolved when unset
                if v is None:
                    if name not in unresolved:
                        unresolved.append(name)
                    return m.group(0)
                return v

    resolved = _COMPOSE_VAR_RE.sub(_replace, value)
    return resolved, unresolved


def resolve_compose(
    refs: list[ImageReference],
    env: dict[str, str] | None = None,
    dotenv: dict[str, str] | None = None,
) -> list[ImageReference]:
    """Resolve Docker Compose ``${VAR:-default}`` variable syntax.

    Resolution order: *dotenv* values override *env* values; both together
    override the inline ``:-`` / ``-`` defaults embedded in the template.

    A new ``ImageReference`` object is returned for every input ref — input
    objects are never mutated.  Confidence is raised to ``MEDIUM`` when at
    least one variable was resolved and no variables remain unresolved.

    Args:
        refs: List of image references to process.
        env: Optional base environment mapping.  Defaults to ``{}``.
        dotenv: Optional ``.env`` file values that take precedence over *env*.

    Returns:
        New list of ``ImageReference`` objects with as many variables
        substituted as possible.
    """
    base_env: dict[str, str] = dict(env) if env else {}
    if dotenv:
        base_env.update(dotenv)

    result: list[ImageReference] = []

    for ref in refs:
        if not ref.unresolved_variables:
            result.append(ref)
            continue

        resolved_raw, still_unresolved = _substitute(ref.raw, base_env)

        if resolved_raw == ref.raw and still_unresolved == ref.unresolved_variables:
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
