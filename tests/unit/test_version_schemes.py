"""Unit tests for VersionSchemeEngine."""

from __future__ import annotations

from shipwreck.config import VersionSchemeConfig
from shipwreck.registry.version import VersionSchemeEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(*schemes: VersionSchemeConfig) -> VersionSchemeEngine:
    return VersionSchemeEngine(list(schemes) if schemes else None)


def _scheme(image_pattern: str, type: str, **kwargs: str) -> VersionSchemeConfig:
    return VersionSchemeConfig(image_pattern=image_pattern, type=type, **kwargs)


# ---------------------------------------------------------------------------
# Semver tests
# ---------------------------------------------------------------------------


def test_compare_semver_basic() -> None:
    engine = _engine()
    assert engine.compare("1.2.3", "1.2.2") == 1
    assert engine.compare("1.2.2", "1.2.3") == -1
    assert engine.compare("1.2.3", "1.2.3") == 0


def test_compare_semver_with_v_prefix() -> None:
    engine = _engine()
    # v-prefixed and bare should be treated identically.
    assert engine.compare("v1.2.3", "1.2.3") == 0
    assert engine.compare("v1.2.3", "v1.2.2") == 1
    assert engine.compare("1.2.3", "v2.0.0") == -1


def test_compare_semver_prerelease() -> None:
    engine = _engine()
    # Pre-release is less than the release.
    assert engine.compare("1.0.0-rc1", "1.0.0") == -1
    assert engine.compare("1.0.0", "1.0.0-rc1") == 1


def test_sort_tags_semver() -> None:
    engine = _engine()
    tags = ["1.0.0", "2.0.0", "0.5.0", "1.5.0"]
    result = engine.sort_tags(tags)
    assert result == ["2.0.0", "1.5.0", "1.0.0", "0.5.0"]


def test_sort_tags_semver_ascending() -> None:
    engine = _engine()
    tags = ["1.0.0", "2.0.0", "0.5.0"]
    result = engine.sort_tags(tags, reverse=False)
    assert result == ["0.5.0", "1.0.0", "2.0.0"]


def test_latest_semver() -> None:
    engine = _engine()
    assert engine.latest(["1.0.0", "2.0.0", "0.5.0"]) == "2.0.0"


def test_semver_invalid_tag_returns_zero() -> None:
    engine = _engine()
    # If either tag is unparseable, compare returns 0.
    assert engine.compare("not-a-version", "1.0.0") == 0
    assert engine.compare("1.0.0", "not-a-version") == 0
    assert engine.compare("foo", "bar") == 0


def test_semver_invalid_tag_sorted_to_end() -> None:
    engine = _engine()
    tags = ["1.0.0", "latest", "2.0.0"]
    result = engine.sort_tags(tags)
    # Unparseable "latest" should be at the end (newest first ordering).
    assert result[-1] == "latest"
    assert result[0] == "2.0.0"


# ---------------------------------------------------------------------------
# Numeric tests
# ---------------------------------------------------------------------------


def test_compare_numeric_int() -> None:
    engine = _engine(_scheme("*", "numeric"))
    assert engine.compare("1709251200", "1709164800") == 1
    assert engine.compare("1709164800", "1709251200") == -1
    assert engine.compare("42", "42") == 0


def test_compare_numeric_float() -> None:
    engine = _engine(_scheme("*", "numeric"))
    assert engine.compare("2.5", "1.0") == 1
    assert engine.compare("1.0", "2.5") == -1
    assert engine.compare("1.0", "1.0") == 0


def test_sort_tags_numeric() -> None:
    engine = _engine(_scheme("*", "numeric"))
    tags = ["3", "1", "2", "10"]
    result = engine.sort_tags(tags)
    assert result == ["10", "3", "2", "1"]


def test_latest_numeric() -> None:
    engine = _engine(_scheme("*", "numeric"))
    assert engine.latest(["100", "200", "50"]) == "200"


# ---------------------------------------------------------------------------
# Date tests
# ---------------------------------------------------------------------------


def test_compare_date_yyyymmdd() -> None:
    engine = _engine(_scheme("*", "date"))
    assert engine.compare("20250228", "20250227") == 1
    assert engine.compare("20250227", "20250228") == -1
    assert engine.compare("20250228", "20250228") == 0


def test_compare_date_with_format() -> None:
    engine = _engine(_scheme("*", "date", format="%Y-%m-%d"))
    assert engine.compare("2025-02-28", "2025-02-27") == 1
    assert engine.compare("2025-01-01", "2025-02-28") == -1


def test_sort_tags_date() -> None:
    engine = _engine(_scheme("*", "date"))
    tags = ["20250101", "20250301", "20250201"]
    result = engine.sort_tags(tags)
    assert result == ["20250301", "20250201", "20250101"]


def test_date_invalid_tag_returns_zero() -> None:
    engine = _engine(_scheme("*", "date"))
    assert engine.compare("notadate", "20250228") == 0
    assert engine.compare("20250228", "notadate") == 0


# ---------------------------------------------------------------------------
# Regex tests
# ---------------------------------------------------------------------------


def test_regex_extract_semver() -> None:
    # Extract semver from a tag like "v1.2.3-alpine".
    engine = _engine(
        _scheme("*", "regex", extract=r"v?(\d+\.\d+\.\d+)", compare="semver")
    )
    assert engine.compare("v1.2.3-alpine", "v1.2.2-alpine") == 1
    assert engine.compare("v1.2.2-alpine", "v1.2.3-alpine") == -1
    assert engine.compare("v1.2.3-alpine", "v1.2.3-alpine") == 0


def test_regex_no_match_returns_zero() -> None:
    engine = _engine(
        _scheme("*", "regex", extract=r"(\d+\.\d+\.\d+)", compare="semver")
    )
    # Tag with no version substring should not parse.
    assert engine.compare("alpine", "latest") == 0
    assert engine.compare("v1.2.3", "alpine") == 0


# ---------------------------------------------------------------------------
# Scheme matching
# ---------------------------------------------------------------------------


def test_image_pattern_matching() -> None:
    """Specific patterns should be matched before the catch-all '*'."""
    engine = VersionSchemeEngine(
        [
            _scheme("myrepo/*", "numeric"),
            _scheme("*", "semver"),
        ]
    )
    # "myrepo/app" matches numeric scheme.
    assert engine.compare("100", "200", image_name="myrepo/app") == -1
    # "otherapp" falls through to the semver scheme.
    assert engine.compare("1.0.0", "2.0.0", image_name="otherapp") == -1


def test_default_scheme_is_semver() -> None:
    """No config → engine defaults to semver for all images."""
    engine = VersionSchemeEngine()
    assert engine.compare("1.0.0", "2.0.0") == -1
    assert engine.compare("2.0.0", "1.0.0") == 1


def test_multiple_schemes_first_match_wins() -> None:
    """When multiple patterns match, the first one in the list wins."""
    engine = VersionSchemeEngine(
        [
            _scheme("myapp", "numeric"),
            _scheme("myapp", "semver"),  # should never be reached for "myapp"
        ]
    )
    # Under numeric, "20" > "9".
    assert engine.compare("20", "9", image_name="myapp") == 1
    # Under semver "20" and "9" are not valid semver, so compare → 0; but
    # we use numeric here so result is 1 (confirmed above).


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_tags_returns_none() -> None:
    engine = _engine()
    assert engine.latest([]) is None


def test_sort_preserves_unparseable_at_end() -> None:
    """Unparseable tags are sorted to the end (after parseable ones)."""
    engine = _engine()
    tags = ["latest", "1.0.0", "stable", "2.0.0"]
    result = engine.sort_tags(tags)
    parseable = [t for t in result if t in ("1.0.0", "2.0.0")]
    unparseable = [t for t in result if t in ("latest", "stable")]
    # All parseable tags must appear before all unparseable ones.
    last_parseable_idx = max(result.index(t) for t in parseable)
    first_unparseable_idx = min(result.index(t) for t in unparseable)
    assert last_parseable_idx < first_unparseable_idx


def test_parse_tag_returns_parsed_object() -> None:
    engine = _engine()
    import semver as _semver

    parsed = engine.parse_tag("1.2.3")
    assert isinstance(parsed, _semver.Version)


def test_parse_tag_returns_none_for_invalid() -> None:
    engine = _engine()
    assert engine.parse_tag("not-semver") is None


def test_catch_all_appended_when_missing() -> None:
    """If no '*' scheme is provided, a semver catch-all is automatically added."""
    engine = VersionSchemeEngine([_scheme("myrepo/*", "numeric")])
    # An image that doesn't match "myrepo/*" should fall through to semver.
    assert engine.compare("1.0.0", "2.0.0", image_name="otherapp") == -1
