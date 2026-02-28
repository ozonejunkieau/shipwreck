"""Docker Compose file parser for Shipwreck."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import is_template_string, parse_image_string

# Regex matching all Compose variable substitution forms:
#   ${VAR:-default}  ${VAR-default}  ${VAR:?err}  ${VAR?err}  ${VAR}
# Group "name"  — variable name (with operator)
# Group "colon" — optional ":" before the operator
# Group "op"    — "-" or "?"
# Group "default" — text after the operator (may be empty string)
# Group "plain" — variable name when there is NO operator (${VAR} only)
_COMPOSE_VAR_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<colon>:?)(?P<op>[-?])(?P<default>[^}]*)?\}"
    r"|\$\{(?P<plain>[A-Za-z_][A-Za-z0-9_]*)\}"
)


def _load_env_file(env_file: Path) -> dict[str, str]:
    """Load a .env file into a dictionary.

    Lines are parsed as ``KEY=VALUE`` pairs.  Comments (``#``) and blank lines
    are skipped.  Surrounding single or double quotes on values are stripped.

    Args:
        env_file: Path to the ``.env`` file.

    Returns:
        Mapping of variable name to value.
    """
    env: dict[str, str] = {}
    if not env_file.is_file():
        return env

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            env[key] = value

    return env


def _resolve_compose_vars(value: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Resolve Compose-style ``${VAR}`` substitutions in a string.

    Handles the following Compose variable syntaxes:

    - ``${VAR}``          — substitute VAR; mark unresolved if VAR absent
    - ``${VAR:-default}`` — use default when VAR is unset or empty
    - ``${VAR-default}``  — use default when VAR is unset (empty string is kept)
    - ``${VAR:?error}``   — treated as unresolved (VAR must be non-empty)
    - ``${VAR?error}``    — treated as unresolved (VAR must be set)

    Args:
        value: The raw string that may contain substitution expressions.
        env: Environment mapping used for resolution.

    Returns:
        A tuple of (resolved_value, unresolved_var_names).
    """
    unresolved_names: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        plain = m.group("plain")
        if plain is not None:
            # ${VAR} — plain substitution, no operator
            v = env.get(plain)
            if v is None:
                unresolved_names.append(plain)
                return m.group(0)
            return v

        name: str = m.group("name")
        colon: str = m.group("colon")   # "" or ":"
        op: str = m.group("op")         # "-" or "?"
        default_val: str = m.group("default") or ""

        v = env.get(name)

        if op == "-":
            if colon == ":":
                # ${VAR:-default}: use default when VAR is unset or empty
                if not v:
                    return default_val
                return v
            else:
                # ${VAR-default}: use default only when VAR is unset
                if v is None:
                    return default_val
                return v
        else:
            # op == "?"
            if colon == ":":
                # ${VAR:?error}: treat as unresolved when VAR is unset or empty
                if not v:
                    unresolved_names.append(name)
                    return m.group(0)
                return v
            else:
                # ${VAR?error}: treat as unresolved when VAR is unset
                if v is None:
                    unresolved_names.append(name)
                    return m.group(0)
                return v

    resolved = _COMPOSE_VAR_RE.sub(_replace, value)
    return resolved, unresolved_names


def _find_image_lines(raw_text: str) -> dict[str, list[int]]:
    """Scan raw YAML text and return a mapping of service name to image line numbers.

    Walks the file line-by-line tracking which ``services:`` child block is
    currently active, then records the 1-indexed line number of every
    ``image:`` directive encountered inside that block.

    Args:
        raw_text: The full content of the Compose YAML file.

    Returns:
        Dict mapping service name to a list of line numbers where ``image:``
        appears inside that service block.
    """
    # Service keys appear at exactly 2-space indentation inside `services:`
    service_key_re = re.compile(r"^  ([A-Za-z0-9_][A-Za-z0-9_.:\-]*)\s*:\s*$")
    # image: lines appear at 4+ spaces of indentation
    image_line_re = re.compile(r"^\s{4,}image:\s*(.+?)\s*$")

    result: dict[str, list[int]] = {}
    in_services = False
    current_service: str | None = None

    for lineno, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.rstrip()

        if line == "services:":
            in_services = True
            continue

        if not in_services:
            continue

        # A non-indented non-empty line ends the services block
        if line and not line.startswith(" "):
            in_services = False
            current_service = None
            continue

        svc_match = service_key_re.match(line)
        if svc_match:
            current_service = svc_match.group(1)
            result.setdefault(current_service, [])
            continue

        img_match = image_line_re.match(line)
        if img_match and current_service is not None:
            result[current_service].append(lineno)

    return result


class ComposeParser:
    """Parser for Docker Compose files.

    Extracts image references from ``services`` entries, handling:

    - Plain ``image:`` fields (``CONSUMES`` relationship)
    - Combined ``build:`` + ``image:`` fields (``PRODUCES`` relationship)
    - Variable interpolation via a ``.env`` file and ``${VAR:-default}`` syntax
    - Service profiles recorded in metadata
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "compose"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should process the given file.

        Recognised filenames:

        - ``docker-compose.yml`` / ``docker-compose.yaml``
        - ``compose.yml`` / ``compose.yaml``
        - ``docker-compose.<variant>.yml`` / ``docker-compose.<variant>.yaml``
        - ``compose.<variant>.yml`` / ``compose.<variant>.yaml``

        Args:
            file_path: Path to the candidate file.

        Returns:
            True when the filename matches a known Compose pattern.
        """
        name = file_path.name
        return (
            name
            in (
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            )
            or (name.startswith("docker-compose.") and name.endswith((".yml", ".yaml")))
            or (name.startswith("compose.") and name.endswith((".yml", ".yaml")))
        )

    def parse(
        self,
        file_path: Path,
        repo_name: str,
        resolve_env_vars: bool = False,
    ) -> list[ImageReference]:
        """Parse a Docker Compose file and return all discovered image references.

        For each service:

        - ``image:`` only  → ``CONSUMES`` reference
        - ``build:`` + ``image:`` → ``PRODUCES`` reference (image built by the project)
        - ``build:`` only  → skipped (handled by the Dockerfile parser)

        Variable resolution order:

        1. ``.env`` file in the same directory as the compose file
        2. Defaults embedded in the ``${VAR:-default}`` syntax
        3. ``os.environ`` (only when ``resolve_env_vars=True``)

        Args:
            file_path: Absolute path to the Compose YAML file.
            repo_name: Repository identifier stored in ``SourceLocation``.
            resolve_env_vars: When ``True``, fall back to ``os.environ`` after
                checking the ``.env`` file.

        Returns:
            List of ``ImageReference`` objects, one per resolved ``image:`` field.
        """
        raw_text = file_path.read_text(encoding="utf-8")

        try:
            data: Any = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            return []

        if not isinstance(data, dict):
            return []

        services: Any = data.get("services")
        if not isinstance(services, dict):
            return []

        # Build the resolution environment
        env: dict[str, str] = {}
        env_file = file_path.parent / ".env"
        env.update(_load_env_file(env_file))

        if resolve_env_vars:
            # os.environ is the base; .env values override it
            merged = dict(os.environ)
            merged.update(env)
            env = merged

        image_lines = _find_image_lines(raw_text)
        references: list[ImageReference] = []

        for service_name, service_conf in services.items():
            if not isinstance(service_conf, dict):
                continue

            raw_image: str | None = service_conf.get("image")
            build_conf: Any = service_conf.get("build")
            profiles: list[str] = list(service_conf.get("profiles") or [])

            if raw_image is None:
                # Pure build: entry — Dockerfile parser handles it
                continue

            raw_image = str(raw_image)

            # Line tracking
            svc_lines = image_lines.get(service_name, [])
            line_number = svc_lines[0] if svc_lines else 1

            # Resolve Compose variables
            resolved_image, unresolved_vars = _resolve_compose_vars(raw_image, env)

            # Relationship
            relationship = EdgeType.PRODUCES if build_conf is not None else EdgeType.CONSUMES

            # Confidence
            originally_templated = is_template_string(raw_image)
            still_unresolved = bool(unresolved_vars)

            if still_unresolved:
                confidence = Confidence.LOW
            elif originally_templated:
                confidence = Confidence.MEDIUM
            else:
                confidence = Confidence.HIGH

            # Parse the fully-resolved (or partially-resolved) image string
            registry, img_name, tag, parse_unresolved = parse_image_string(resolved_image)

            # Merge unresolved variable names from both resolution phases
            all_unresolved = list(dict.fromkeys(unresolved_vars + parse_unresolved))

            metadata: dict[str, Any] = {}
            if profiles:
                metadata["profiles"] = profiles
            if build_conf is not None:
                metadata["build_context"] = build_conf

            source = SourceLocation(
                repo=repo_name,
                file=str(file_path),
                line=line_number,
                parser=self.name,
            )

            references.append(
                ImageReference(
                    raw=raw_image,
                    registry=registry,
                    name=img_name,
                    tag=tag,
                    source=source,
                    relationship=relationship,
                    confidence=confidence,
                    unresolved_variables=all_unresolved,
                    metadata=metadata,
                )
            )

        return references
