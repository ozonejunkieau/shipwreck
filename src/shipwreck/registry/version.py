"""Version scheme engine for comparing and sorting image tags."""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime
from functools import cmp_to_key
from typing import Any

import semver

from shipwreck.config import VersionSchemeConfig

_DEFAULT_SCHEME = VersionSchemeConfig(image_pattern="*", type="semver")


class VersionSchemeEngine:
    """Handles version tag comparison using configurable schemes.

    Supports: semver, numeric, date, regex.
    """

    def __init__(self, schemes: list[VersionSchemeConfig] | None = None) -> None:
        """Initialize with scheme configs. Default scheme is semver for '*'.

        Args:
            schemes: Ordered list of scheme configs. The first scheme whose
                ``image_pattern`` matches an image name is used. A catch-all
                scheme with ``image_pattern="*"`` and ``type="semver"`` is
                appended automatically if none of the provided schemes is
                already a catch-all.
        """
        if schemes is None:
            self._schemes = [_DEFAULT_SCHEME]
        else:
            # Ensure there is always a fallback catch-all at the end.
            self._schemes = list(schemes)
            if not any(s.image_pattern == "*" for s in self._schemes):
                self._schemes.append(_DEFAULT_SCHEME)

    def _find_scheme(self, image_name: str) -> VersionSchemeConfig:
        """Find the first matching scheme for an image using fnmatch.

        Args:
            image_name: The image name (without tag) to match.

        Returns:
            The first :class:`~shipwreck.config.VersionSchemeConfig` whose
            ``image_pattern`` matches *image_name*.
        """
        for scheme in self._schemes:
            if fnmatch.fnmatch(image_name, scheme.image_pattern):
                return scheme
        # This is unreachable because __init__ guarantees a '*' scheme exists,
        # but we return the default defensively.
        return _DEFAULT_SCHEME  # pragma: no cover

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(self, tag_a: str, tag_b: str, image_name: str = "*") -> int:
        """Compare two version tags.

        Args:
            tag_a: First tag.
            tag_b: Second tag.
            image_name: Image name used to select the matching scheme.

        Returns:
            -1 if *tag_a* < *tag_b*, 0 if equal (or either is unparseable),
            1 if *tag_a* > *tag_b*.
        """
        scheme = self._find_scheme(image_name)
        parsed_a = self._parse_with_scheme(tag_a, scheme)
        parsed_b = self._parse_with_scheme(tag_b, scheme)

        if parsed_a is None or parsed_b is None:
            return 0

        return self._compare_parsed(parsed_a, parsed_b, scheme)

    def sort_tags(
        self,
        tags: list[str],
        image_name: str = "*",
        reverse: bool = True,
    ) -> list[str]:
        """Sort tags by version.

        Unparseable tags are treated as the smallest possible value and sorted
        to the end when *reverse=True* (newest first).

        Args:
            tags: List of version tag strings to sort.
            image_name: Image name used to select the matching scheme.
            reverse: When ``True`` (the default) the newest tag comes first.

        Returns:
            A new sorted list of tag strings.
        """
        scheme = self._find_scheme(image_name)

        def _cmp(a: str, b: str) -> int:
            pa = self._parse_with_scheme(a, scheme)
            pb = self._parse_with_scheme(b, scheme)

            # Both unparseable — preserve relative order.
            if pa is None and pb is None:
                return 0
            # Unparseable tags sink to the bottom (treat as smaller than everything).
            if pa is None:
                return -1
            if pb is None:
                return 1

            return self._compare_parsed(pa, pb, scheme)

        return sorted(tags, key=cmp_to_key(_cmp), reverse=reverse)

    def latest(self, tags: list[str], image_name: str = "*") -> str | None:
        """Return the latest tag from a list.

        Args:
            tags: List of version tag strings.
            image_name: Image name used to select the matching scheme.

        Returns:
            The tag string that represents the newest version, or ``None`` if
            *tags* is empty.
        """
        if not tags:
            return None
        sorted_tags = self.sort_tags(tags, image_name=image_name, reverse=True)
        return sorted_tags[0]

    def parse_tag(self, tag: str, image_name: str = "*") -> object | None:
        """Parse a tag according to its scheme.

        Args:
            tag: The tag string to parse.
            image_name: Image name used to select the matching scheme.

        Returns:
            A parsed representation of the tag, or ``None`` if the tag cannot
            be parsed under the selected scheme.
        """
        scheme = self._find_scheme(image_name)
        return self._parse_with_scheme(tag, scheme)

    def scheme_for(self, image_name: str) -> str:
        """Return the version scheme type string for *image_name*.

        Args:
            image_name: The image name (without tag) to match.

        Returns:
            The scheme type string (e.g. ``"semver"``, ``"numeric"``,
            ``"date"``, ``"regex"``) for the first matching scheme.
        """
        return self._find_scheme(image_name).type

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_with_scheme(self, tag: str, scheme: VersionSchemeConfig) -> Any:
        """Parse *tag* using the rules in *scheme*.

        Returns ``None`` when the tag cannot be interpreted.
        """
        if scheme.type == "semver":
            return _parse_semver(tag)
        if scheme.type == "numeric":
            return _parse_numeric(tag)
        if scheme.type == "date":
            return _parse_date(tag, scheme.format)
        if scheme.type == "regex":
            return _parse_regex(tag, scheme)
        return None  # unknown scheme type

    @staticmethod
    def _compare_parsed(a: Any, b: Any, scheme: VersionSchemeConfig) -> int:
        """Compare two already-parsed values.

        For regex schemes the inner *compare* type determines how the extracted
        values are ordered.  For all other schemes the parsed objects support
        direct comparison via ``<`` / ``>``.

        Returns -1, 0, or 1.
        """
        if scheme.type == "regex":
            inner = scheme.compare or "semver"
            if inner == "semver":
                return _cmp_semver(a, b)
            # Fall through to generic comparison for numeric / date inner types.

        if a < b:
            return -1
        if a > b:
            return 1
        return 0


# ---------------------------------------------------------------------------
# Per-scheme parse helpers
# ---------------------------------------------------------------------------


def _parse_semver(tag: str) -> semver.Version | None:
    """Parse a semver string, stripping a leading 'v' if present."""
    cleaned = tag.lstrip("v") if tag.startswith("v") else tag
    try:
        return semver.Version.parse(cleaned)
    except ValueError:
        return None


def _parse_numeric(tag: str) -> int | float | None:
    """Parse a tag as an integer or float."""
    try:
        return int(tag)
    except ValueError:
        pass
    try:
        return float(tag)
    except ValueError:
        return None


def _parse_date(tag: str, fmt: str | None) -> datetime | None:
    """Parse a tag as a date using *fmt* (defaults to ``%Y%m%d``)."""
    date_fmt = fmt or "%Y%m%d"
    try:
        return datetime.strptime(tag, date_fmt)
    except (ValueError, TypeError):
        return None


def _parse_regex(tag: str, scheme: VersionSchemeConfig) -> Any:
    """Extract a value from *tag* using ``scheme.extract``, then parse it.

    The extracted substring is parsed according to ``scheme.compare``
    (defaults to ``"semver"``).  Returns ``None`` if the regex doesn't match
    or the extracted value cannot be parsed.
    """
    if not scheme.extract:
        return None

    m = re.search(scheme.extract, tag)
    if not m:
        return None

    extracted = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
    inner = scheme.compare or "semver"

    if inner == "semver":
        return _parse_semver(extracted)
    if inner == "numeric":
        return _parse_numeric(extracted)
    if inner == "date":
        return _parse_date(extracted, None)
    return None


def _cmp_semver(a: semver.Version, b: semver.Version) -> int:
    """Compare two :class:`semver.Version` objects."""
    if a < b:
        return -1
    if a > b:
        return 1
    return 0
