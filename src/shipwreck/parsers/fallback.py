"""Fallback scanner parser for Shipwreck.

Processes files not claimed by any specific parser with best-effort extraction
at LOW confidence. Handles generic YAML ``image:`` fields and Containerfile
``FROM`` instructions.
"""

from __future__ import annotations

import re
from pathlib import Path

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import parse_image_string, validate_image_ref

# Matches a YAML `image:` field value on a single line.
# Captures the value, optionally quoted with single or double quotes.
_YAML_IMAGE_RE = re.compile(r"""^\s*image:\s*["']?(\S+?)["']?\s*$""")

# Matches a FROM instruction in Containerfile syntax, optionally with --platform.
# Group 1: the image reference.
_FROM_RE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE)

# Bare-word values that look like YAML booleans/scalars — not image references.
# These are values with no `/` and no `:` beyond a tag separator.
_BARE_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _is_containerfile(file_path: Path) -> bool:
    """Return True if the file follows Containerfile naming conventions.

    Args:
        file_path: Path to the candidate file.

    Returns:
        True if the file is a Containerfile or Containerfile variant.
    """
    name = file_path.name.lower()
    return name == "containerfile" or name.startswith("containerfile.") or name.endswith(".containerfile")


def _is_yaml(file_path: Path) -> bool:
    """Return True if the file is a YAML file.

    Args:
        file_path: Path to the candidate file.

    Returns:
        True if the file has a .yml or .yaml extension.
    """
    return file_path.suffix in (".yml", ".yaml")


def _looks_like_bare_non_image(value: str) -> bool:
    """Return True if the value is a bare word that is unlikely to be an image reference.

    A bare word (no ``/`` or ``:``) that is not a known Docker official image should
    be skipped, because it is probably a YAML string value that happened to appear
    under an ``image:`` key (e.g. a service name or environment name).

    Args:
        value: The candidate string extracted from an ``image:`` field.

    Returns:
        True if the value should be skipped.
    """
    if "/" in value or ":" in value or "@" in value:
        return False
    # It's a bare word — allow it only if it is a known Docker official image.
    # (Docker official images are valid single-component image refs.)
    # We intentionally do NOT import _DOCKER_OFFICIAL_IMAGES from base.py because
    # that set is a private implementation detail.  Instead we rely on validate_image_ref
    # combined with the bare-word heuristic: if it looks like a plain word with no
    # structural image markers, skip it.
    return bool(_BARE_WORD_RE.match(value))


def _extract_yaml_image_refs(
    lines: list[str],
    file_path: Path,
    repo_name: str,
    parser_name: str,
) -> list[ImageReference]:
    """Scan YAML lines for ``image:`` fields and return image references.

    Args:
        lines: File content split into lines (1-indexed by enumeration).
        file_path: Path to the source file.
        repo_name: Repository name for ``SourceLocation``.
        parser_name: Parser identifier for ``SourceLocation`` and metadata.

    Returns:
        List of ``ImageReference`` objects discovered.
    """
    refs: list[ImageReference] = []

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip comment lines
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        m = _YAML_IMAGE_RE.match(line)
        if not m:
            continue

        value = m.group(1)

        # Skip non-image values (booleans, nulls, empty, etc.)
        if not validate_image_ref(value):
            continue

        # Skip bare words that look like non-image scalar values
        if _looks_like_bare_non_image(value):
            continue

        registry, name, tag, unresolved = parse_image_string(value)

        source = SourceLocation(
            repo=repo_name,
            file=str(file_path),
            line=lineno,
            parser=parser_name,
        )

        ref = ImageReference(
            raw=value,
            registry=registry,
            name=name,
            tag=tag,
            source=source,
            relationship=EdgeType.CONSUMES,
            confidence=Confidence.LOW,
            unresolved_variables=unresolved,
            metadata={"parser": parser_name},
        )
        refs.append(ref)

    return refs


def _extract_from_refs(
    lines: list[str],
    file_path: Path,
    repo_name: str,
    parser_name: str,
) -> list[ImageReference]:
    """Scan Containerfile lines for ``FROM`` instructions and return image references.

    Internal stage aliases (``FROM base AS dev``) are tracked and skipped.

    Args:
        lines: File content split into lines (1-indexed by enumeration).
        file_path: Path to the source file.
        repo_name: Repository name for ``SourceLocation``.
        parser_name: Parser identifier for ``SourceLocation`` and metadata.

    Returns:
        List of ``ImageReference`` objects discovered.
    """
    refs: list[ImageReference] = []
    known_aliases: set[str] = set()

    # Matches optional AS alias after the image reference.
    _as_re = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip comment lines
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        m = _as_re.match(stripped)
        if not m:
            continue

        raw_image = m.group(1)
        alias = m.group(2)

        # Skip scratch
        if raw_image.lower() == "scratch":
            if alias:
                known_aliases.add(alias.lower())
            continue

        # Skip internal stage references
        if raw_image.lower() in known_aliases:
            if alias:
                known_aliases.add(alias.lower())
            continue

        # Register alias for future stage references
        if alias:
            known_aliases.add(alias.lower())

        if not validate_image_ref(raw_image):
            continue

        registry, name, tag, unresolved = parse_image_string(raw_image)

        source = SourceLocation(
            repo=repo_name,
            file=str(file_path),
            line=lineno,
            parser=parser_name,
        )

        ref = ImageReference(
            raw=raw_image,
            registry=registry,
            name=name,
            tag=tag,
            source=source,
            relationship=EdgeType.BUILDS_FROM,
            confidence=Confidence.LOW,
            unresolved_variables=unresolved,
            metadata={"parser": parser_name},
        )
        refs.append(ref)

    return refs


class FallbackScanner:
    """Best-effort image reference scanner for unclaimed files.

    Applied by the scanner orchestrator to files that no specific parser has
    claimed. All extracted references carry ``confidence=LOW`` and
    ``metadata={"parser": "fallback"}``.

    Supported extraction strategies:
    - YAML ``image:`` field scanning (``.yml``, ``.yaml``)
    - ``FROM`` instruction scanning for Containerfile variants
    - Both strategies applied to other file types (``.json``, ``.toml``, etc.)
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "fallback"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser can potentially handle the given file.

        The scanner orchestrator ensures this parser only receives files that
        no specific parser has claimed. This method declares what file types
        are eligible.

        Matches:
        - ``.yml`` / ``.yaml`` files
        - ``.json``, ``.toml``, ``.cfg``, ``.conf`` files
        - Files with no extension
        - Containerfile variants (``Containerfile``, ``Containerfile.*``,
          ``*.containerfile``)

        Args:
            file_path: Path to the candidate file.

        Returns:
            True if this parser can process the file.
        """
        if _is_containerfile(file_path):
            return True
        return file_path.suffix in (".yml", ".yaml", ".json", ".toml", ".cfg", ".conf", "")

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse the file and return all discovered image references.

        Dispatches to the appropriate extraction strategy based on the file's
        name and extension:

        - ``.yml`` / ``.yaml`` → YAML ``image:`` field scanning
        - Containerfile variants → ``FROM`` instruction scanning
        - Other types → both strategies applied

        Args:
            file_path: Absolute path to the file to parse.
            repo_name: Repository name used to populate ``SourceLocation.repo``.

        Returns:
            List of ``ImageReference`` objects, all with ``confidence=LOW``.
        """
        lines = file_path.read_text(encoding="utf-8").splitlines()
        refs: list[ImageReference] = []

        if _is_yaml(file_path):
            refs.extend(_extract_yaml_image_refs(lines, file_path, repo_name, self.name))
        elif _is_containerfile(file_path):
            refs.extend(_extract_from_refs(lines, file_path, repo_name, self.name))
        else:
            # Unknown type — try both strategies
            refs.extend(_extract_yaml_image_refs(lines, file_path, repo_name, self.name))
            refs.extend(_extract_from_refs(lines, file_path, repo_name, self.name))

        return refs
