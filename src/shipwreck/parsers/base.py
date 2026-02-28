"""Base parser protocol and shared image string parsing utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from shipwreck.models import ImageReference

# Variables that Docker embeds / that are CI-runtime-specific and never resolvable from static files
_RUNTIME_VARS: frozenset[str] = frozenset(
    {
        "CI_COMMIT_TAG",
        "CI_COMMIT_SHA",
        "CI_COMMIT_REF_NAME",
        "CI_COMMIT_SHORT_SHA",
        "CI_PIPELINE_ID",
        "CI_JOB_ID",
        "GITHUB_SHA",
        "GITHUB_REF",
        "GITHUB_RUN_ID",
    }
)

# Regex to extract ${VAR} and $VAR style references
_DOLLAR_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Regex to extract {{ var }} style Jinja2/Ansible references
_JINJA2_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")

# Regex to extract ${{ expr }} style GitHub Actions references
_GH_ACTIONS_VAR_RE = re.compile(r"\$\{\{\s*([A-Za-z_.][A-Za-z0-9_.]*)\s*\}\}")

# Known Docker official images (no namespace prefix)
_DOCKER_OFFICIAL_IMAGES: frozenset[str] = frozenset(
    {
        "alpine",
        "ubuntu",
        "debian",
        "centos",
        "fedora",
        "python",
        "node",
        "ruby",
        "golang",
        "java",
        "openjdk",
        "nginx",
        "apache",
        "httpd",
        "postgres",
        "mysql",
        "mariadb",
        "redis",
        "mongo",
        "elasticsearch",
        "rabbitmq",
        "memcached",
        "kafka",
        "zookeeper",
        "vault",
        "consul",
        "grafana",
        "prometheus",
        "traefik",
        "haproxy",
        "scratch",
        "busybox",
        "curl",
        "bash",
        "docker",
    }
)

# Values that are definitely not image references
_NON_IMAGE_VALUES: frozenset[str] = frozenset(
    {"true", "false", "null", "yes", "no", "on", "off", "none", ""}
)


def extract_variables(s: str) -> list[str]:
    """Extract variable names from a template string.

    Handles:
    - ``${VAR}`` and ``$VAR`` style (Docker, shell, Compose)
    - ``{{ var }}`` style (Jinja2/Ansible)
    - ``${{ expr }}`` style (GitHub Actions)

    Args:
        s: The template string to scan.

    Returns:
        List of unique variable names found (preserving order of first occurrence).
    """
    seen: set[str] = set()
    result: list[str] = []

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            result.append(name)

    for m in _GH_ACTIONS_VAR_RE.finditer(s):
        add(m.group(1))
    for m in _JINJA2_VAR_RE.finditer(s):
        add(m.group(1))
    for m in _DOLLAR_VAR_RE.finditer(s):
        add(m.group(1))
    return result


def is_template_string(s: str) -> bool:
    """Return True if the string contains unresolved template markers.

    Args:
        s: The string to check.
    """
    return bool(re.search(r"\{\{|\$\{|\$\{\{", s))


def validate_image_ref(s: str) -> bool:
    """Return True if the string plausibly looks like a Docker image reference.

    Args:
        s: The candidate string.
    """
    if not s or s.lower() in _NON_IMAGE_VALUES:
        return False
    # File paths
    if s.startswith(("/", "./", "../")):
        return False
    # URLs with other schemes
    if re.match(r"^[a-z][a-z0-9+.-]+://", s) and not s.startswith("docker://"):
        return False
    # Must contain reasonable image characters
    if not re.match(r"^[A-Za-z0-9_./:@{}\$\-][A-Za-z0-9_./:@{}\$\-]*$", s):
        return False
    return True


def parse_image_string(raw: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """Parse an image string into (registry, name, tag, unresolved_variables).

    Rules:
    - If the raw string contains template markers, returns all None + variable list.
    - If the first path component contains ``.`` or ``:`` (port), it's a registry.
    - Otherwise the registry defaults to ``docker.io``.
    - Single-component images with no explicit namespace get the ``library/`` prefix.
    - If no tag is present (no ``:``) the tag defaults to ``latest``.
    - ``scratch`` is a special Docker built-in — registry and name are None, tag is None.

    Args:
        raw: The raw image string.

    Returns:
        (registry, name, tag, unresolved_variables)
    """
    if not raw:
        return None, None, None, []

    # Handle unresolvable template strings
    if is_template_string(raw):
        return None, None, None, extract_variables(raw)

    # Special built-ins
    if raw == "scratch":
        return None, None, None, []

    # Strip docker:// prefix (from GitHub Actions uses:)
    if raw.startswith("docker://"):
        raw = raw[len("docker://"):]

    # Split off digest (@sha256:...)
    digest: str | None = None
    if "@" in raw:
        raw, digest = raw.rsplit("@", 1)
        _ = digest  # stored separately if needed

    # Split off tag
    tag: str | None = None
    # Find the last `:` that's not part of a port in the registry
    # Strategy: split on `/` first
    parts = raw.split("/")
    last_part = parts[-1]
    if ":" in last_part:
        colon_idx = last_part.rfind(":")
        tag = last_part[colon_idx + 1:]
        parts[-1] = last_part[:colon_idx]
    else:
        tag = "latest"

    raw_no_tag = "/".join(parts)

    # Determine registry
    registry: str
    name: str
    if len(parts) == 1:
        # e.g. "python" → docker.io library/python
        registry = "docker.io"
        name = f"library/{parts[0]}"
    elif len(parts) >= 2:
        first = parts[0]
        # First component is a registry if it contains a dot or a colon (port)
        if "." in first or ":" in first or first == "localhost":
            registry = first
            name = "/".join(parts[1:])
        else:
            # docker.io namespace/image or namespace/image
            registry = "docker.io"
            name = raw_no_tag
    else:
        registry = "docker.io"
        name = raw_no_tag

    return registry, name, tag, []


@runtime_checkable
class Parser(Protocol):
    """Protocol that all parsers must implement."""

    @property
    def name(self) -> str:
        """Unique parser identifier (e.g. 'dockerfile', 'bake')."""
        ...

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should process the given file."""
        ...

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse the file and return all discovered image references."""
        ...
