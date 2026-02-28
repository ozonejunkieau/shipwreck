"""Unit tests for the registry client, policy, and staleness modules."""

from __future__ import annotations

import warnings

import httpx
import pytest
import respx

from shipwreck.config import RegistryConfig, RegistryPolicy
from shipwreck.registry.client import RegistryClient
from shipwreck.registry.policy import should_query_registry
from shipwreck.registry.staleness import (
    STATUS_BEHIND,
    STATUS_CURRENT,
    STATUS_MAJOR_BEHIND,
    STATUS_UNKNOWN,
    compute_staleness,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REGISTRY = "registry.example.com"
IMAGE = "myorg/myapp"
BASE_URL = f"https://{REGISTRY}"


def make_client(auth_token: str | None = None) -> RegistryClient:
    return RegistryClient(registry_url=REGISTRY, auth_token=auth_token)


# ---------------------------------------------------------------------------
# client.py — list_tags
# ---------------------------------------------------------------------------


class TestListTags:
    @respx.mock
    def test_list_tags(self) -> None:
        """list_tags returns the tags array from the registry response."""
        respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": IMAGE, "tags": ["latest", "1.0", "2.0"]})
        )
        client = make_client()
        tags = client.list_tags(IMAGE)
        assert tags == ["latest", "1.0", "2.0"]

    @respx.mock
    def test_list_tags_empty(self) -> None:
        """list_tags returns an empty list when the registry returns null tags."""
        respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": IMAGE, "tags": None})
        )
        client = make_client()
        tags = client.list_tags(IMAGE)
        assert tags == []

    @respx.mock
    def test_list_tags_empty_array(self) -> None:
        """list_tags returns an empty list when the registry returns an empty array."""
        respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": IMAGE, "tags": []})
        )
        client = make_client()
        tags = client.list_tags(IMAGE)
        assert tags == []

    @respx.mock
    def test_list_tags_raises_on_error(self) -> None:
        """list_tags raises on non-2xx HTTP responses."""
        respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        client = make_client()
        with pytest.raises(httpx.HTTPStatusError):
            client.list_tags(IMAGE)


# ---------------------------------------------------------------------------
# client.py — get_manifest
# ---------------------------------------------------------------------------


class TestGetManifest:
    @respx.mock
    def test_get_manifest(self) -> None:
        """get_manifest returns the manifest dict from the registry."""
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "size": 7023, "digest": "sha256:abc"},
            "layers": [],
        }
        respx.get(f"{BASE_URL}/v2/{IMAGE}/manifests/latest").mock(
            return_value=httpx.Response(200, json=manifest)
        )
        client = make_client()
        result = client.get_manifest(IMAGE, "latest")
        assert result["schemaVersion"] == 2
        assert "config" in result

    @respx.mock
    def test_get_manifest_raises_on_404(self) -> None:
        """get_manifest raises on 404."""
        respx.get(f"{BASE_URL}/v2/{IMAGE}/manifests/nonexistent").mock(
            return_value=httpx.Response(404, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})
        )
        client = make_client()
        with pytest.raises(httpx.HTTPStatusError):
            client.get_manifest(IMAGE, "nonexistent")


# ---------------------------------------------------------------------------
# client.py — tag_exists
# ---------------------------------------------------------------------------


class TestTagExists:
    @respx.mock
    def test_tag_exists_true(self) -> None:
        """tag_exists returns True when the registry responds with 200."""
        respx.head(f"{BASE_URL}/v2/{IMAGE}/manifests/latest").mock(
            return_value=httpx.Response(200)
        )
        client = make_client()
        assert client.tag_exists(IMAGE, "latest") is True

    @respx.mock
    def test_tag_exists_false(self) -> None:
        """tag_exists returns False when the registry responds with 404."""
        respx.head(f"{BASE_URL}/v2/{IMAGE}/manifests/does-not-exist").mock(
            return_value=httpx.Response(404)
        )
        client = make_client()
        assert client.tag_exists(IMAGE, "does-not-exist") is False

    @respx.mock
    def test_tag_exists_raises_on_5xx(self) -> None:
        """tag_exists raises on server errors."""
        respx.head(f"{BASE_URL}/v2/{IMAGE}/manifests/latest").mock(
            return_value=httpx.Response(503)
        )
        client = make_client()
        with pytest.raises(httpx.HTTPStatusError):
            client.tag_exists(IMAGE, "latest")


# ---------------------------------------------------------------------------
# client.py — Bearer auth flow
# ---------------------------------------------------------------------------


class TestBearerAuthFlow:
    @respx.mock
    def test_bearer_auth_flow(self) -> None:
        """A 401 triggers token acquisition and the original request is retried."""
        auth_header = (
            'Bearer realm="https://auth.example.com/token",'
            'service="registry.example.com",'
            'scope="repository:myorg/myapp:pull"'
        )

        # First call returns 401 with www-authenticate header.
        # Second call (after token acquisition) returns 200.
        tags_route = respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list")
        tags_route.side_effect = [
            httpx.Response(401, headers={"www-authenticate": auth_header}),
            httpx.Response(200, json={"tags": ["v1"]}),
        ]

        # Token endpoint returns a token.
        respx.get("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={"token": "mytoken123"})
        )

        client = make_client()
        tags = client.list_tags(IMAGE)
        assert tags == ["v1"]
        # The token should be cached on the client.
        assert client._auth_token == "mytoken123"

    @respx.mock
    def test_bearer_auth_flow_token_key_access_token(self) -> None:
        """Token endpoint may use 'access_token' instead of 'token'."""
        auth_header = 'Bearer realm="https://auth.example.com/token",service="svc",scope="scope"'

        tags_route = respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list")
        tags_route.side_effect = [
            httpx.Response(401, headers={"www-authenticate": auth_header}),
            httpx.Response(200, json={"tags": []}),
        ]
        respx.get("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "access_tok"})
        )

        client = make_client()
        client.list_tags(IMAGE)
        assert client._auth_token == "access_tok"

    @respx.mock
    def test_basic_auth_header(self) -> None:
        """When auth_token is provided it is sent as an Authorization: Bearer header."""
        route = respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(200, json={"tags": ["stable"]})
        )
        client = make_client(auth_token="pre-issued-token")
        client.list_tags(IMAGE)

        sent_headers = route.calls.last.request.headers
        assert sent_headers.get("authorization") == "Bearer pre-issued-token"

    @respx.mock
    def test_no_retry_when_token_fetch_fails(self) -> None:
        """If the token endpoint fails, the original 401 response is returned as-is."""
        auth_header = 'Bearer realm="https://auth.example.com/token",service="svc",scope="s"'

        respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(401, headers={"www-authenticate": auth_header})
        )
        respx.get("https://auth.example.com/token").mock(
            return_value=httpx.Response(401)
        )

        client = make_client()
        with pytest.raises(httpx.HTTPStatusError):
            client.list_tags(IMAGE)


# ---------------------------------------------------------------------------
# client.py — URL construction
# ---------------------------------------------------------------------------


class TestRegistryUrlConstruction:
    @respx.mock
    def test_registry_url_construction_tags(self) -> None:
        """The correct URL is built for the tags/list endpoint."""
        route = respx.get("https://index.docker.io/v2/library/python/tags/list").mock(
            return_value=httpx.Response(200, json={"tags": ["3.12"]})
        )
        client = RegistryClient("index.docker.io")
        client.list_tags("library/python")
        assert route.called

    @respx.mock
    def test_registry_url_trailing_slash_stripped(self) -> None:
        """Trailing slashes in the registry URL are stripped."""
        route = respx.get(f"{BASE_URL}/v2/{IMAGE}/tags/list").mock(
            return_value=httpx.Response(200, json={"tags": []})
        )
        client = RegistryClient(f"{REGISTRY}/")
        client.list_tags(IMAGE)
        assert route.called


# ---------------------------------------------------------------------------
# policy.py
# ---------------------------------------------------------------------------


class TestShouldQueryRegistry:
    def _policy(self, **kwargs: object) -> RegistryPolicy:
        return RegistryPolicy(**kwargs)  # type: ignore[arg-type]

    def _regs(self, *entries: tuple[str, bool]) -> list[RegistryConfig]:
        return [RegistryConfig(name=url, url=url, internal=internal) for url, internal in entries]

    def test_internal_registry_always_allowed(self) -> None:
        """An internal=True registry is allowed regardless of policy."""
        regs = self._regs(("registry.internal.example.com", True))
        policy = self._policy(prompt_external=False, external_allowlist=[])
        assert should_query_registry("registry.internal.example.com", regs, policy) is True

    def test_internal_registry_allowed_in_non_interactive(self) -> None:
        """An internal=True registry is allowed even in non-interactive mode."""
        regs = self._regs(("registry.internal.example.com", True))
        policy = self._policy(prompt_external=False)
        assert should_query_registry("registry.internal.example.com", regs, policy, non_interactive=True) is True

    def test_external_allowlist_allowed(self) -> None:
        """A registry on the external allowlist is allowed."""
        policy = self._policy(prompt_external=False, external_allowlist=["docker.io", "ghcr.io"])
        assert should_query_registry("ghcr.io", [], policy) is True

    def test_external_not_allowed_noninteractive(self) -> None:
        """Unknown external registries in non-interactive mode emit a warning and return False."""
        policy = self._policy(prompt_external=True, external_allowlist=[])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = should_query_registry("unknown.registry.io", [], policy, non_interactive=True)
        assert result is False
        assert any("non-interactive" in str(w.message) for w in caught)

    def test_external_prompt_when_policy_set(self) -> None:
        """prompt_external=True returns True so the caller can prompt the user."""
        policy = self._policy(prompt_external=True, external_allowlist=[])
        result = should_query_registry("docker.io", [], policy, non_interactive=False)
        assert result is True

    def test_unknown_registry_denied_by_default(self) -> None:
        """An unknown registry with prompt_external=False is denied."""
        policy = self._policy(prompt_external=False, external_allowlist=[])
        result = should_query_registry("random.registry.io", [], policy)
        assert result is False

    def test_non_internal_config_entry_not_automatically_allowed(self) -> None:
        """A registry in config but with internal=False does not get auto-allowed."""
        regs = self._regs(("docker.io", False))
        policy = self._policy(prompt_external=False, external_allowlist=[])
        result = should_query_registry("docker.io", regs, policy)
        assert result is False


# ---------------------------------------------------------------------------
# staleness.py
# ---------------------------------------------------------------------------


class TestComputeStaleness:
    # --- Semver ---

    def test_current_when_latest_semver(self) -> None:
        """Tag matching the highest semver is 'current'."""
        result = compute_staleness("2.0.0", ["1.0.0", "1.5.0", "2.0.0"])
        assert result == STATUS_CURRENT

    def test_behind_same_major_semver(self) -> None:
        """Tag on the same semver major but not latest is 'behind'."""
        result = compute_staleness("2.0.0", ["2.0.0", "2.1.0", "2.2.0"])
        assert result == STATUS_BEHIND

    def test_major_behind_different_major_semver(self) -> None:
        """Tag on an older major version is 'major_behind'."""
        result = compute_staleness("1.9.9", ["1.9.9", "2.0.0", "3.0.0"])
        assert result == STATUS_MAJOR_BEHIND

    def test_semver_with_leading_v(self) -> None:
        """Tags with a leading 'v' are handled correctly."""
        result = compute_staleness("v1.0.0", ["v1.0.0", "v1.1.0", "v2.0.0"])
        assert result == STATUS_MAJOR_BEHIND

    # --- Numeric ---

    def test_staleness_numeric_current(self) -> None:
        """Current highest build number is 'current'."""
        result = compute_staleness("1000", ["900", "950", "1000"])
        assert result == STATUS_CURRENT

    def test_staleness_numeric_scheme_behind(self) -> None:
        """A build number within 10% of the max is 'behind'."""
        # 990 is 1% behind 1000 — within the 10% threshold.
        result = compute_staleness("990", ["990", "995", "1000"])
        assert result == STATUS_BEHIND

    def test_staleness_numeric_scheme_major_behind(self) -> None:
        """A build number more than 10% behind the max is 'major_behind'."""
        # 500 is 50% behind 1000.
        result = compute_staleness("500", ["500", "750", "1000"])
        assert result == STATUS_MAJOR_BEHIND

    # --- Date ---

    def test_staleness_date_current(self) -> None:
        """Tag matching the most recent date is 'current'."""
        result = compute_staleness("20231005", ["20231001", "20231003", "20231005"])
        assert result == STATUS_CURRENT

    def test_staleness_date_scheme_behind(self) -> None:
        """A date tag within 90 days of the latest is 'behind'."""
        # 30 days behind
        result = compute_staleness("20231005", ["20231005", "20231025", "20231104"])
        assert result == STATUS_BEHIND

    def test_staleness_date_scheme_major_behind(self) -> None:
        """A date tag more than 90 days behind the latest is 'major_behind'."""
        result = compute_staleness("20230101", ["20230101", "20230401", "20230701"])
        assert result == STATUS_MAJOR_BEHIND

    # --- Unknown / edge cases ---

    def test_unknown_when_unparseable(self) -> None:
        """Non-semver, non-numeric, non-date tags yield 'unknown'."""
        result = compute_staleness("alpine", ["alpine", "slim", "latest"])
        assert result == STATUS_UNKNOWN

    def test_unknown_when_no_tags(self) -> None:
        """Empty available_tags list yields 'unknown'."""
        result = compute_staleness("1.0.0", [])
        assert result == STATUS_UNKNOWN

    def test_unknown_when_referenced_not_in_available(self) -> None:
        """A referenced tag absent from the available list yields 'unknown'."""
        result = compute_staleness("3.0.0", ["1.0.0", "2.0.0"])
        assert result == STATUS_UNKNOWN

    def test_date_hyphenated_format(self) -> None:
        """Hyphenated date tags (2023-10-05) are parsed correctly."""
        result = compute_staleness("2023-01-01", ["2023-01-01", "2023-04-01", "2023-07-01"])
        assert result == STATUS_MAJOR_BEHIND

    def test_mixed_tags_semver_wins(self) -> None:
        """Semver-parseable tags are preferred over non-semver noise tags."""
        result = compute_staleness("1.0.0", ["latest", "stable", "1.0.0", "2.0.0"])
        assert result == STATUS_MAJOR_BEHIND
