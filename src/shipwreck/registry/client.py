"""Docker Registry HTTP API v2 client."""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)


class RegistryClient:
    """Docker Registry HTTP API v2 client.

    Implements tag listing, manifest fetching, and tag existence checking.
    Handles Bearer token auth flow (www-authenticate challenge -> token endpoint -> retry).
    """

    MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"

    def __init__(self, registry_url: str, auth_token: str | None = None) -> None:
        """Initialize with registry URL and optional auth token.

        Args:
            registry_url: Registry hostname (e.g. "registry.example.com")
            auth_token: Pre-authenticated token (for basic auth). If None,
                        will attempt Bearer token flow on 401.
        """
        self._registry = registry_url.rstrip("/")
        self._auth_token = auth_token
        self._client = httpx.Client(timeout=30.0)
        self._token_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_tags(self, name: str) -> list[str]:
        """List all tags for an image.

        GET /v2/{name}/tags/list
        Returns list of tag strings.
        """
        url = f"https://{self._registry}/v2/{name}/tags/list"
        logger.info("Registry HTTP GET %s (list tags for %s)", url, name)
        response = self._auth_request("GET", url)
        response.raise_for_status()
        data = response.json()
        return data.get("tags") or []

    def get_manifest(self, name: str, reference: str) -> dict:
        """Get manifest for an image:tag.

        GET /v2/{name}/manifests/{reference}
        Accept: application/vnd.docker.distribution.manifest.v2+json
        Returns manifest dict with config, layers, etc.
        """
        url = f"https://{self._registry}/v2/{name}/manifests/{reference}"
        logger.info("Registry HTTP GET %s (manifest for %s:%s)", url, name, reference)
        response = self._auth_request("GET", url, headers={"Accept": self.MANIFEST_MEDIA_TYPE})
        response.raise_for_status()
        return response.json()

    def tag_exists(self, name: str, tag: str) -> bool:
        """Check if a specific tag exists.

        HEAD /v2/{name}/manifests/{tag}
        Returns True if 200, False if 404.
        """
        url = f"https://{self._registry}/v2/{name}/manifests/{tag}"
        logger.info("Registry HTTP HEAD %s (check tag %s:%s)", url, name, tag)
        response = self._auth_request("HEAD", url, headers={"Accept": self.MANIFEST_MEDIA_TYPE})
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """Build the Authorization header from the current auth token."""
        if self._auth_token:
            return {"Authorization": f"Bearer {self._auth_token}"}
        return {}

    def _auth_request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Make an authenticated request, handling 401 Bearer token flow.

        1. Try request with current auth headers.
        2. If 401, parse www-authenticate header for Bearer realm/service/scope.
        3. GET ``{realm}?service={service}&scope={scope}`` to obtain a token.
        4. Retry with ``Authorization: Bearer {token}``.
        """
        headers: dict[str, str] = dict(kwargs.pop("headers", {}))  # type: ignore[arg-type]
        headers.update(self._build_headers())

        response = self._client.request(method, url, headers=headers, **kwargs)  # type: ignore[arg-type]

        if response.status_code != 401:
            return response

        # --- Bearer token flow ---
        www_auth = response.headers.get("www-authenticate", "")
        token = self._fetch_bearer_token(www_auth)
        if token is None:
            # Cannot acquire token — return the 401 as-is.
            return response

        # Cache and retry with the obtained token.
        self._auth_token = token
        headers["Authorization"] = f"Bearer {token}"
        return self._client.request(method, url, headers=headers, **kwargs)  # type: ignore[arg-type]

    def _fetch_bearer_token(self, www_authenticate: str) -> str | None:
        """Parse a ``www-authenticate`` Bearer header and fetch a token.

        Expected header format::

            Bearer realm="https://auth.docker.io/token",service="...",scope="..."

        Returns the token string on success, or ``None`` if the header cannot
        be parsed or the token endpoint returns a non-200 status.
        """
        realm = _parse_bearer_param(www_authenticate, "realm")
        if realm is None:
            return None

        params: dict[str, str] = {}
        for param in ("service", "scope"):
            value = _parse_bearer_param(www_authenticate, param)
            if value is not None:
                params[param] = value

        logger.info("Registry auth token request GET %s (service=%s)", realm, params.get("service", "n/a"))
        try:
            token_response = self._client.get(realm, params=params)
        except httpx.HTTPError:
            return None

        if token_response.status_code != 200:
            return None

        data = token_response.json()
        return data.get("token") or data.get("access_token")

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> RegistryClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_bearer_param(header: str, param: str) -> str | None:
    """Extract a named parameter from a ``www-authenticate: Bearer`` header.

    Returns the unquoted value or ``None`` if not present.
    """
    pattern = rf'{re.escape(param)}="([^"]*)"'
    match = re.search(pattern, header)
    return match.group(1) if match else None
