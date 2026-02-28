"""Dockerfile parser for Shipwreck."""

from __future__ import annotations

import re
from pathlib import Path

from shipwreck.models import Confidence, EdgeType, ImageReference, SourceLocation
from shipwreck.parsers.base import extract_variables, is_template_string, parse_image_string

# Matches a FROM instruction, optionally with --platform and an AS alias.
# Groups: (1) image reference, (2) alias name or None
_FROM_RE = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?",
    re.IGNORECASE,
)

# Matches ARG NAME=default or ARG NAME
_ARG_RE = re.compile(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$", re.IGNORECASE)

# Matches $VAR or ${VAR} for substitution
_VAR_SUBST_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _substitute_args(image: str, args: dict[str, str]) -> str:
    """Substitute ``$VAR`` / ``${VAR}`` references using collected ARG defaults.

    Args:
        image: The raw image string from a FROM instruction.
        args: Mapping of ARG name to its default value.

    Returns:
        The image string with all resolvable variables substituted in-place.
    """

    def replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        return args[var] if var in args else m.group(0)

    return _VAR_SUBST_RE.sub(replacer, image)


class DockerfileParser:
    """Parser that extracts image references from Dockerfile syntax files.

    Handles standard ``FROM`` instructions including:
    - Multi-stage builds with stage alias references (skipped as internal deps)
    - ``ARG``-based image variable substitution
    - ``--platform`` flags
    - ``scratch`` built-in (skipped)
    - Commented-out ``FROM`` lines
    """

    @property
    def name(self) -> str:
        """Unique parser identifier."""
        return "dockerfile"

    def can_handle(self, file_path: Path) -> bool:
        """Return True if this parser should handle the given file.

        Matches:
        - Files named ``Dockerfile`` (case-insensitive)
        - Files named ``Dockerfile.<suffix>`` (e.g. ``Dockerfile.prod``)
        - Files ending in ``.dockerfile``

        Args:
            file_path: Path to the candidate file.

        Returns:
            True if this parser can process the file.
        """
        name = file_path.name.lower()
        return name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile")

    def parse(self, file_path: Path, repo_name: str) -> list[ImageReference]:
        """Parse a Dockerfile and return all discovered external image references.

        Args:
            file_path: Absolute path to the Dockerfile.
            repo_name: Repository name used to populate ``SourceLocation.repo``.

        Returns:
            List of ``ImageReference`` objects for each external ``FROM`` image.
        """
        lines = file_path.read_text(encoding="utf-8").splitlines()

        # Phase 1: collect global ARGs (before the first FROM)
        pre_from_args: dict[str, str] = {}
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if _FROM_RE.match(stripped):
                break
            arg_match = _ARG_RE.match(stripped)
            if arg_match:
                var_name = arg_match.group(1)
                default = arg_match.group(2)
                if default is not None:
                    pre_from_args[var_name] = default.strip()

        # Phase 2: scan all FROM instructions
        # Collect (line_number, image, alias) tuples first so we know the last FROM
        from_entries: list[tuple[int, str, str | None]] = []
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            m = _FROM_RE.match(stripped)
            if m:
                from_entries.append((lineno, m.group(1), m.group(2)))

        # Phase 3: build references, tracking stage aliases
        known_aliases: set[str] = set()
        refs: list[ImageReference] = []
        total_froms = len(from_entries)

        for idx, (lineno, raw_image, alias) in enumerate(from_entries):
            is_final = idx == total_froms - 1

            # Resolve ARG substitution
            resolved_image = _substitute_args(raw_image, pre_from_args)

            # Skip scratch (Docker built-in)
            if resolved_image.lower() == "scratch":
                if alias:
                    known_aliases.add(alias.lower())
                continue

            # Skip internal stage references
            if resolved_image.lower() in known_aliases:
                if alias:
                    known_aliases.add(alias.lower())
                continue

            # Register alias for future FROM lookups
            if alias:
                known_aliases.add(alias.lower())

            # Determine confidence and unresolved variables
            was_substituted = resolved_image != raw_image
            still_has_template = is_template_string(resolved_image)
            unresolved: list[str] = []

            if still_has_template:
                unresolved = extract_variables(resolved_image)
                confidence = Confidence.MEDIUM
            elif was_substituted:
                confidence = Confidence.MEDIUM
            else:
                confidence = Confidence.HIGH

            # Parse into registry/name/tag components
            registry, name, tag, parse_unresolved = parse_image_string(resolved_image)
            # Merge unresolved variables (template strings yield extras from parse_image_string)
            if parse_unresolved:
                for v in parse_unresolved:
                    if v not in unresolved:
                        unresolved.append(v)
            # For unresolved ARG (no default), confidence is MEDIUM even with no template markers
            if not still_has_template and not was_substituted:
                # Check if the raw image had variables that weren't substituted due to missing defaults
                raw_vars = extract_variables(raw_image)
                missing = [v for v in raw_vars if v not in pre_from_args]
                if missing:
                    unresolved = missing
                    confidence = Confidence.MEDIUM

            source = SourceLocation(
                repo=repo_name,
                file=str(file_path),
                line=lineno,
                parser=self.name,
            )

            metadata: dict[str, object] = {
                "stage_alias": alias if alias else None,
                "is_final_stage": is_final,
            }

            ref = ImageReference(
                raw=resolved_image,
                registry=registry,
                name=name,
                tag=tag,
                source=source,
                relationship=EdgeType.BUILDS_FROM,
                confidence=confidence,
                unresolved_variables=unresolved,
                metadata=metadata,
            )
            refs.append(ref)

        return refs
