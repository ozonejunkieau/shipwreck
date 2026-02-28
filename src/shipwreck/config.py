"""Pydantic models for shipwreck.yaml configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class RegistryConfig(BaseModel):
    """Configuration for a container registry."""

    name: str
    url: str
    auth_env: str | None = None
    internal: bool = False


class RegistryPolicy(BaseModel):
    """Policy controlling access to external registries."""

    prompt_external: bool = True
    external_allowlist: list[str] = Field(default_factory=list)


class RepositoryConfig(BaseModel):
    """A single repository to scan."""

    url: str | None = None
    path: str | None = None
    ref: str = "main"
    name: str | None = None

    @model_validator(mode="after")
    def validate_url_or_path(self) -> RepositoryConfig:
        if not self.url and not self.path:
            raise ValueError("Repository must have either 'url' or 'path'")
        return self

    def effective_name(self) -> str:
        """Return the repository name, deriving it from the URL/path if not set."""
        if self.name:
            return self.name
        if self.url:
            base = self.url.rstrip("/").split("/")[-1]
            return base.removesuffix(".git")
        if self.path:
            return Path(self.path).name
        return "unknown"


class DiscoveryConfig(BaseModel):
    """Auto-discovery configuration (e.g. GitLab group)."""

    type: str
    url: str
    group: str
    auth_env: str
    include_subgroups: bool = False
    include_pattern: str | None = None
    exclude_pattern: str | None = None


class AnsibleConfig(BaseModel):
    """Configuration for Ansible variable resolution."""

    inventory: str
    vault_password_file: str | None = None
    limit: str | None = None


class AliasRule(BaseModel):
    """A rule for resolving image aliases/variants."""

    pattern: str | None = None
    canonical: str | None = None
    variant: str | None = None
    from_image: str | None = Field(None, alias="from")

    model_config = {"populate_by_name": True}


class VersionSchemeConfig(BaseModel):
    """Version comparison scheme for a set of images."""

    image_pattern: str
    type: str
    format: str | None = None
    extract: str | None = None
    compare: str | None = None


class ClassificationRule(BaseModel):
    """A rule mapping a path or image pattern to a classification."""

    path_pattern: str | None = None
    image_pattern: str | None = None
    image_class: str = Field(alias="class")

    model_config = {"populate_by_name": True}


class ClassificationConfig(BaseModel):
    """Classification configuration."""

    rules: list[ClassificationRule] = Field(default_factory=list)


class ShipwreckConfig(BaseModel):
    """Root configuration model for shipwreck.yaml."""

    registries: list[RegistryConfig] = Field(default_factory=list)
    registry_policy: RegistryPolicy = Field(default_factory=RegistryPolicy)
    repositories: list[RepositoryConfig] = Field(default_factory=list)
    discovery: list[DiscoveryConfig] = Field(default_factory=list)
    ansible: AnsibleConfig | None = None
    resolve_env_vars: bool = False
    aliases: list[AliasRule] = Field(default_factory=list)
    version_schemes: list[VersionSchemeConfig] = Field(default_factory=list)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)


def load_config(path: Path) -> ShipwreckConfig:
    """Load and validate a shipwreck.yaml config file.

    Args:
        path: Path to the YAML config file.

    Returns:
        Validated ShipwreckConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    data: Any = yaml.safe_load(path.read_text())
    if data is None:
        data = {}
    return ShipwreckConfig.model_validate(data)
