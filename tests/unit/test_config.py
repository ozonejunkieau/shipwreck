"""Unit tests for shipwreck.config — Pydantic model validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shipwreck.config import (
    AliasRule,
    ClassificationConfig,
    ClassificationRule,
    RegistryConfig,
    RegistryPolicy,
    RepositoryConfig,
    ShipwreckConfig,
    VersionSchemeConfig,
    load_config,
)


class TestRepositoryConfig:
    def test_url_only(self):
        repo = RepositoryConfig(url="git@gitlab.example.com:infra/base.git")
        assert repo.url == "git@gitlab.example.com:infra/base.git"
        assert repo.ref == "main"

    def test_path_only(self):
        repo = RepositoryConfig(path="/local/repo")
        assert repo.path == "/local/repo"

    def test_neither_url_nor_path_raises(self):
        with pytest.raises(Exception):
            RepositoryConfig()

    def test_effective_name_from_url(self):
        repo = RepositoryConfig(url="git@gitlab.example.com:infra/base-images.git")
        assert repo.effective_name() == "base-images"

    def test_effective_name_explicit(self):
        repo = RepositoryConfig(url="git@gitlab.example.com:infra/base.git", name="my-repo")
        assert repo.effective_name() == "my-repo"

    def test_effective_name_from_path(self):
        repo = RepositoryConfig(path="/some/path/my-project")
        assert repo.effective_name() == "my-project"

    def test_default_ref_is_main(self):
        repo = RepositoryConfig(url="https://example.com/repo.git")
        assert repo.ref == "main"


class TestRegistryConfig:
    def test_basic(self):
        reg = RegistryConfig(name="internal", url="registry.example.com")
        assert reg.internal is False
        assert reg.auth_env is None

    def test_internal_flag(self):
        reg = RegistryConfig(name="internal", url="registry.example.com", internal=True)
        assert reg.internal is True


class TestShipwreckConfig:
    def test_empty_config(self):
        cfg = ShipwreckConfig()
        assert cfg.repositories == []
        assert cfg.registries == []
        assert cfg.resolve_env_vars is False

    def test_full_config(self):
        cfg = ShipwreckConfig(
            registries=[RegistryConfig(name="internal", url="registry.example.com", internal=True)],
            repositories=[RepositoryConfig(url="git@example.com/repo.git", ref="main")],
            resolve_env_vars=True,
        )
        assert len(cfg.registries) == 1
        assert len(cfg.repositories) == 1
        assert cfg.resolve_env_vars is True

    def test_alias_rule_pattern(self):
        rule = AliasRule(pattern=r"^(.+):(.+)-clean$", canonical="{1}:{2}", variant="optimised")
        assert rule.pattern is not None
        assert rule.variant == "optimised"

    def test_alias_rule_explicit_from(self):
        rule = AliasRule.model_validate(
            {"from": "registry.example.com/releases/myapp", "canonical": "registry.example.com/builds/myapp", "variant": "release"}
        )
        assert rule.from_image == "registry.example.com/releases/myapp"

    def test_version_scheme_semver(self):
        scheme = VersionSchemeConfig(image_pattern="*", type="semver")
        assert scheme.type == "semver"

    def test_version_scheme_date_with_format(self):
        scheme = VersionSchemeConfig(image_pattern="registry.example.com/snapshots/*", type="date", format="%Y%m%d")
        assert scheme.format == "%Y%m%d"

    def test_classification_rule_path_pattern(self):
        rule = ClassificationRule.model_validate({"path_pattern": "**/ansible/**", "class": "product"})
        assert rule.image_class == "product"
        assert rule.path_pattern == "**/ansible/**"

    def test_classification_config_empty(self):
        cfg = ClassificationConfig()
        assert cfg.rules == []

    def test_registry_policy_defaults(self):
        policy = RegistryPolicy()
        assert policy.prompt_external is True
        assert policy.external_allowlist == []


class TestLoadConfig:
    def test_load_minimal_yaml(self, tmp_path: Path):
        config_file = tmp_path / "shipwreck.yaml"
        config_file.write_text("repositories:\n  - path: /tmp/test\n    name: test\n")
        cfg = load_config(config_file)
        assert len(cfg.repositories) == 1
        assert cfg.repositories[0].effective_name() == "test"

    def test_load_empty_yaml(self, tmp_path: Path):
        config_file = tmp_path / "shipwreck.yaml"
        config_file.write_text("")
        cfg = load_config(config_file)
        assert cfg.repositories == []

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_load_full_config(self, tmp_path: Path):
        """Full config YAML is correctly parsed and validated."""
        config_data = {
            "registries": [
                {"name": "internal", "url": "registry.example.com", "auth_env": "REG_TOKEN", "internal": True}
            ],
            "registry_policy": {
                "prompt_external": True,
                "external_allowlist": ["docker.io", "ghcr.io"],
            },
            "repositories": [
                {"url": "git@gitlab.example.com:infra/base.git", "ref": "main", "name": "base"},
                {"path": "/local/myapp", "name": "myapp"},
            ],
            "resolve_env_vars": False,
            "aliases": [
                {"pattern": "^(.+):(.+)-clean$", "canonical": "{1}:{2}", "variant": "optimised"}
            ],
            "version_schemes": [
                {"image_pattern": "*", "type": "semver"}
            ],
            "classification": {
                "rules": [
                    {"path_pattern": "**/ansible/**", "class": "product"},
                    {"path_pattern": "**/.github/**", "class": "test"},
                ]
            },
        }
        config_file = tmp_path / "shipwreck.yaml"
        config_file.write_text(yaml.dump(config_data))
        cfg = load_config(config_file)

        assert len(cfg.registries) == 1
        assert cfg.registries[0].internal is True
        assert len(cfg.repositories) == 2
        assert cfg.repositories[0].effective_name() == "base"
        assert cfg.registry_policy.prompt_external is True
        assert "docker.io" in cfg.registry_policy.external_allowlist
        assert len(cfg.aliases) == 1
        assert len(cfg.version_schemes) == 1
        assert len(cfg.classification.rules) == 2
