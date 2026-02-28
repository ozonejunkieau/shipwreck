"""Unit tests for the FallbackScanner parser."""

from __future__ import annotations

from pathlib import Path

from shipwreck.models import Confidence, EdgeType
from shipwreck.parsers.fallback import FallbackScanner

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "fallback"


class TestYamlImageField:
    def test_yaml_image_field(self) -> None:
        """image: in unclaimed YAML file produces low-confidence refs."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        image_names = [r.raw for r in refs]
        assert "registry.example.com/myapp:1.0" in image_names
        assert "prom/prometheus:v2.48.0" in image_names
        assert all(r.confidence == Confidence.LOW for r in refs)

    def test_grafana_ref_extracted(self) -> None:
        """grafana/grafana:10.0.0 is extracted from misc_config.yaml."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        image_names = [r.raw for r in refs]
        assert "grafana/grafana:10.0.0" in image_names

    def test_boolean_image_value_ignored(self) -> None:
        """image: true at the top level is not extracted."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        raw_values = [r.raw for r in refs]
        assert "true" not in raw_values

    def test_yaml_relationship_is_consumes(self) -> None:
        """All refs from YAML image: fields have CONSUMES relationship."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        assert all(r.relationship == EdgeType.CONSUMES for r in refs)

    def test_yaml_metadata_parser_key(self) -> None:
        """All refs carry metadata={"parser": "fallback"}."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        assert all(r.metadata.get("parser") == "fallback" for r in refs)

    def test_registry_parsed(self) -> None:
        """Registry is correctly extracted from a fully-qualified image ref."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        myapp = next(r for r in refs if r.raw == "registry.example.com/myapp:1.0")
        assert myapp.registry == "registry.example.com"
        assert myapp.name == "myapp"
        assert myapp.tag == "1.0"


class TestContainerfile:
    def test_containerfile(self) -> None:
        """Containerfile FROM statements are caught."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert len(refs) == 1
        assert refs[0].raw == "ubi9/ubi-minimal:9.3"
        assert refs[0].relationship == EdgeType.BUILDS_FROM

    def test_containerfile_confidence(self) -> None:
        """FROM in Containerfile has LOW confidence."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].confidence == Confidence.LOW

    def test_containerfile_metadata(self) -> None:
        """FROM in Containerfile carries correct metadata."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].metadata.get("parser") == "fallback"

    def test_containerfile_line_number(self) -> None:
        """FROM on line 1 of Containerfile is reported at line 1."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].source.line == 1

    def test_containerfile_repo_name(self) -> None:
        """Source location carries the provided repo name."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].source.repo == "test-repo"

    def test_containerfile_parser_in_source(self) -> None:
        """Source location carries parser name 'fallback'."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].source.parser == "fallback"


class TestContainerfileDev:
    def test_containerfile_dev(self) -> None:
        """Containerfile.dev variant produces only the external base image."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile.dev", "test-repo")

        # Only the external ubi9 base image; `base` alias FROM is skipped
        assert len(refs) == 1
        assert refs[0].raw == "ubi9/ubi-minimal:9.3"

    def test_containerfile_dev_internal_alias_skipped(self) -> None:
        """FROM base AS dev does not produce a reference (internal alias)."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile.dev", "test-repo")

        raw_values = [r.raw for r in refs]
        assert "base" not in raw_values

    def test_containerfile_dev_relationship(self) -> None:
        """Containerfile.dev FROM reference has BUILDS_FROM relationship."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile.dev", "test-repo")

        assert refs[0].relationship == EdgeType.BUILDS_FROM


class TestIgnoredYaml:
    def test_comments_ignored(self) -> None:
        """Commented lines are not extracted."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "ignored.yaml", "test-repo")

        raw_values = [r.raw for r in refs]
        assert "old-image:1.0" not in raw_values

    def test_boolean_yaml_values_ignored(self) -> None:
        """image: true / image: false are not image references."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "ignored.yaml", "test-repo")

        raw_values = [r.raw for r in refs]
        assert "true" not in raw_values
        assert "false" not in raw_values

    def test_no_refs_from_ignored_yaml(self) -> None:
        """No valid image refs are produced from ignored.yaml."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "ignored.yaml", "test-repo")

        assert len(refs) == 0


class TestCanHandle:
    def test_can_handle_yaml(self) -> None:
        """can_handle returns True for .yaml files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("some/path/deployment.yaml")) is True

    def test_can_handle_yml(self) -> None:
        """can_handle returns True for .yml files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("config.yml")) is True

    def test_can_handle_containerfile(self) -> None:
        """can_handle returns True for plain Containerfile."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("Containerfile")) is True

    def test_can_handle_containerfile_dot_variant(self) -> None:
        """can_handle returns True for Containerfile.dev style variants."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("Containerfile.dev")) is True

    def test_can_handle_dot_containerfile_suffix(self) -> None:
        """can_handle returns True for .containerfile suffix files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("myapp.containerfile")) is True

    def test_can_handle_json(self) -> None:
        """can_handle returns True for .json files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("config.json")) is True

    def test_can_handle_toml(self) -> None:
        """can_handle returns True for .toml files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("config.toml")) is True

    def test_can_handle_cfg(self) -> None:
        """can_handle returns True for .cfg files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("app.cfg")) is True

    def test_can_handle_conf(self) -> None:
        """can_handle returns True for .conf files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("nginx.conf")) is True

    def test_can_handle_no_extension(self) -> None:
        """can_handle returns True for files with no extension."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("Makefile")) is True

    def test_cannot_handle_python(self) -> None:
        """can_handle returns False for .py files."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("main.py")) is False

    def test_cannot_handle_dockerfile(self) -> None:
        """can_handle returns False for .dockerfile files (handled by DockerfileParser)."""
        parser = FallbackScanner()
        assert parser.can_handle(Path("app.dockerfile")) is False


class TestLineNumbersTracked:
    def test_line_numbers_tracked(self) -> None:
        """Line numbers are correctly reported for YAML image: fields."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "misc_config.yaml", "test-repo")

        myapp = next(r for r in refs if r.raw == "registry.example.com/myapp:1.0")
        prometheus = next(r for r in refs if r.raw == "prom/prometheus:v2.48.0")
        grafana = next(r for r in refs if r.raw == "grafana/grafana:10.0.0")

        # Verify ordering — myapp appears before prometheus and grafana
        assert myapp.source.line < prometheus.source.line
        assert prometheus.source.line < grafana.source.line

    def test_containerfile_line_numbers(self) -> None:
        """FROM on line 1 of Containerfile reports line 1."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile", "test-repo")

        assert refs[0].source.line == 1

    def test_containerfile_dev_line_number(self) -> None:
        """First external FROM in Containerfile.dev reports the correct line."""
        parser = FallbackScanner()
        refs = parser.parse(FIXTURES / "Containerfile.dev", "test-repo")

        # ubi9 base is on line 1
        assert refs[0].source.line == 1


class TestParserName:
    def test_parser_name(self) -> None:
        """Parser name property returns 'fallback'."""
        parser = FallbackScanner()
        assert parser.name == "fallback"
