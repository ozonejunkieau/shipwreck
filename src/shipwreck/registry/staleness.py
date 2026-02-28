"""Staleness computation for image tags."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shipwreck.registry.version import VersionSchemeEngine  # pragma: no cover

# Returned staleness status constants.
STATUS_CURRENT = "current"
STATUS_BEHIND = "behind"
STATUS_MAJOR_BEHIND = "major_behind"
STATUS_UNKNOWN = "unknown"

# How far behind (as a fraction of the numeric range) before we call it "major".
_NUMERIC_MAJOR_THRESHOLD = 0.10

# Days before a date-versioned tag is considered "major_behind".
_DATE_MAJOR_DAYS = 90


def compute_staleness(
    referenced_tag: str,
    available_tags: list[str],
    image_name: str = "*",
    version_engine: VersionSchemeEngine | None = None,
) -> str:
    """Compute staleness status for a referenced tag against available tags.

    Args:
        referenced_tag: The tag currently referenced (e.g. ``"3.11"``, ``"20231005"``).
        available_tags: All tags available for the image from the registry.
        image_name: The image name, used to resolve the applicable version scheme.
        version_engine: Optional :class:`~shipwreck.registry.version.VersionSchemeEngine`
            instance.  If ``None`` a default engine is created when the module is
            available; otherwise raw string ordering is used for heuristics.

    Returns:
        One of ``"current"``, ``"behind"``, ``"major_behind"``, or ``"unknown"``.
    """
    if not available_tags:
        return STATUS_UNKNOWN

    if referenced_tag not in available_tags:
        # If the referenced tag is not even in the list we cannot reason about it.
        return STATUS_UNKNOWN

    # Resolve the version scheme type for this image.
    scheme_type = _resolve_scheme_type(image_name, version_engine)

    if scheme_type == "semver":
        return _staleness_semver(referenced_tag, available_tags)
    if scheme_type == "numeric":
        return _staleness_numeric(referenced_tag, available_tags)
    if scheme_type == "date":
        return _staleness_date(referenced_tag, available_tags)

    # No engine or unrecognised scheme — try heuristics in order.
    # Date is tried before numeric so that compact date strings (e.g. "20231005")
    # are not misidentified as plain build numbers.
    result = _staleness_semver(referenced_tag, available_tags)
    if result != STATUS_UNKNOWN:
        return result

    result = _staleness_date(referenced_tag, available_tags)
    if result != STATUS_UNKNOWN:
        return result

    result = _staleness_numeric(referenced_tag, available_tags)
    if result != STATUS_UNKNOWN:
        return result

    return STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# Scheme-specific helpers
# ---------------------------------------------------------------------------


def _staleness_semver(referenced_tag: str, available_tags: list[str]) -> str:
    """Compute staleness using semver comparison."""
    try:
        import semver  # type: ignore[import-untyped]
    except ImportError:
        return STATUS_UNKNOWN

    ref_ver = _try_parse_semver(referenced_tag, semver)
    if ref_ver is None:
        return STATUS_UNKNOWN

    parsed: list[object] = []
    for t in available_tags:
        v = _try_parse_semver(t, semver)
        if v is not None:
            parsed.append(v)

    if not parsed:
        return STATUS_UNKNOWN

    latest = max(parsed)  # type: ignore[type-var]

    if ref_ver == latest:
        return STATUS_CURRENT

    if ref_ver.major < latest.major:  # type: ignore[union-attr]
        return STATUS_MAJOR_BEHIND

    return STATUS_BEHIND


def _staleness_numeric(referenced_tag: str, available_tags: list[str]) -> str:
    """Compute staleness for purely numeric tags (e.g. build numbers)."""
    ref_num = _try_parse_int(referenced_tag)
    if ref_num is None:
        return STATUS_UNKNOWN

    nums: list[int] = []
    for t in available_tags:
        n = _try_parse_int(t)
        if n is not None:
            nums.append(n)

    if not nums:
        return STATUS_UNKNOWN

    latest = max(nums)
    if ref_num == latest:
        return STATUS_CURRENT

    if latest == 0:
        return STATUS_BEHIND

    gap_fraction = (latest - ref_num) / latest
    if gap_fraction > _NUMERIC_MAJOR_THRESHOLD:
        return STATUS_MAJOR_BEHIND

    return STATUS_BEHIND


def _staleness_date(referenced_tag: str, available_tags: list[str]) -> str:
    """Compute staleness for date-versioned tags (e.g. ``"20231005"``, ``"2023-10-05"``)."""
    ref_date = _try_parse_date(referenced_tag)
    if ref_date is None:
        return STATUS_UNKNOWN

    dates: list[datetime] = []
    for t in available_tags:
        d = _try_parse_date(t)
        if d is not None:
            dates.append(d)

    if not dates:
        return STATUS_UNKNOWN

    latest = max(dates)
    if ref_date == latest:
        return STATUS_CURRENT

    delta = latest - ref_date
    if delta.days > _DATE_MAJOR_DAYS:
        return STATUS_MAJOR_BEHIND

    return STATUS_BEHIND


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _resolve_scheme_type(image_name: str, engine: VersionSchemeEngine | None) -> str | None:
    """Ask the engine (if available) what scheme applies to *image_name*.

    When *engine* is ``None``, returns ``None`` so the caller falls through to
    heuristic detection. A default engine is **not** created because the
    catch-all ``"*" → semver`` rule would mask valid numeric/date tags.
    """
    if engine is None:
        return None

    try:
        return engine.scheme_for(image_name)
    except Exception:
        return None


def _try_parse_semver(tag: str, semver_module: object) -> object:
    """Return a ``semver.Version`` or ``None``."""
    try:
        return semver_module.Version.parse(tag)  # type: ignore[union-attr]
    except (ValueError, TypeError):
        pass
    # Some tags use a leading "v" (e.g. "v1.2.3").
    if tag.startswith("v"):
        try:
            return semver_module.Version.parse(tag[1:])  # type: ignore[union-attr]
        except (ValueError, TypeError):
            pass
    return None


def _try_parse_int(tag: str) -> int | None:
    """Return an ``int`` if *tag* is a purely numeric string, else ``None``."""
    try:
        return int(tag)
    except (ValueError, TypeError):
        return None


# Date formats to attempt when parsing a date-versioned tag.
_DATE_FORMATS = [
    "%Y%m%d",    # 20231005
    "%Y-%m-%d",  # 2023-10-05
    "%Y.%m.%d",  # 2023.10.05
]


def _try_parse_date(tag: str) -> datetime | None:
    """Return a ``datetime`` if *tag* looks like a date version, else ``None``."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(tag, fmt)
        except (ValueError, TypeError):
            pass
    return None
