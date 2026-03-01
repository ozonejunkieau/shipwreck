"""GitLab group auto-discovery for Shipwreck."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from shipwreck.config import RepositoryConfig

logger = logging.getLogger(__name__)


def discover_repos(
    url: str,
    group: str,
    auth_token: str,
    include_subgroups: bool = False,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
) -> list[RepositoryConfig]:
    """Discover repositories from a GitLab group.

    Uses GitLab REST API v4: GET /api/v4/groups/{id}/projects

    Args:
        url: GitLab instance URL (e.g. "https://gitlab.example.com")
        group: Group path (e.g. "my-org/containers")
        auth_token: GitLab access token
        include_subgroups: Include nested subgroups
        include_pattern: Regex pattern to include (matched against project path)
        exclude_pattern: Regex pattern to exclude

    Returns:
        List of RepositoryConfig objects for discovered repos.
    """
    base_url = url.rstrip("/")
    # URL-encode the group path for the API
    encoded_group = group.replace("/", "%2F")

    endpoint = f"{base_url}/api/v4/groups/{encoded_group}/projects"
    params: dict[str, Any] = {
        "per_page": 100,
        "include_subgroups": str(include_subgroups).lower(),
        "archived": "false",
    }
    headers = {"PRIVATE-TOKEN": auth_token}

    repos: list[RepositoryConfig] = []

    logger.info("GitLab API discovery: GET %s (group=%s, subgroups=%s)", endpoint, group, include_subgroups)
    with httpx.Client(timeout=30.0) as client:
        page = 1
        while True:
            params["page"] = page
            logger.info("GitLab API request: GET %s (page %d)", endpoint, page)
            response = client.get(endpoint, params=params, headers=headers)
            response.raise_for_status()

            projects = response.json()
            if not projects:
                break

            for project in projects:
                path = project.get("path_with_namespace", "")
                ssh_url = project.get("ssh_url_to_repo", "")
                name = project.get("path", "")
                default_branch = project.get("default_branch", "main")

                # Apply filters
                if include_pattern and not re.search(include_pattern, path):
                    continue
                if exclude_pattern and re.search(exclude_pattern, path):
                    continue

                repos.append(
                    RepositoryConfig(
                        url=ssh_url,
                        name=name,
                        ref=default_branch or "main",
                    )
                )

            page += 1

    return repos
