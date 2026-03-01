"""Tests for the enhanced HTML report output (Wave 4 UX overhaul)."""

from __future__ import annotations

from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    GraphSummary,
    ImageSource,
    SourceLocation,
)
from tests.conftest import make_graph

# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_rich_graph() -> Graph:
    """Build a richer Graph for HTML enhanced tests with staleness data."""
    nodes = {
        "registry.example.com/base-image": GraphNode(
            id="registry.example.com/base-image",
            canonical="registry.example.com/base-image",
            tags_referenced=["3.12-slim", "3.11"],
            classification="base",
            criticality=6.0,
            staleness="current",
            sources=[
                ImageSource(
                    repo="org/app",
                    file="Dockerfile",
                    line=1,
                    relationship=EdgeType.BUILDS_FROM,
                    tag="3.12-slim",
                )
            ],
        ),
        "registry.example.com/app": GraphNode(
            id="registry.example.com/app",
            canonical="registry.example.com/app",
            tags_referenced=["1.0.0", "latest"],
            classification="application",
            criticality=3.0,
            staleness="behind",
            sources=[
                ImageSource(
                    repo="org/app",
                    file="docker-compose.yml",
                    line=10,
                    relationship=EdgeType.PRODUCES,
                    tag="1.0.0",
                )
            ],
        ),
        "gcr.io/middleware": GraphNode(
            id="gcr.io/middleware",
            canonical="gcr.io/middleware",
            tags_referenced=["v2"],
            classification="middleware",
            criticality=2.0,
            staleness="major_behind",
            sources=[],
        ),
        "library/utility": GraphNode(
            id="library/utility",
            canonical="library/utility",
            tags_referenced=["alpine"],
            classification="utility",
            criticality=1.0,
            staleness=None,
            sources=[],
        ),
    }
    edges = [
        GraphEdge(
            source="registry.example.com/base-image",
            target="registry.example.com/app",
            relationship=EdgeType.BUILDS_FROM,
            confidence=Confidence.HIGH,
            source_location=SourceLocation(
                repo="org/app", file="Dockerfile", line=1, parser="dockerfile"
            ),
        ),
        GraphEdge(
            source="registry.example.com/app",
            target="gcr.io/middleware",
            relationship=EdgeType.CONSUMES,
            confidence=Confidence.MEDIUM,
            source_location=SourceLocation(
                repo="org/app",
                file="docker-compose.yml",
                line=5,
                parser="compose",
            ),
        ),
    ]
    return Graph(
        generated_at="2026-02-28T00:00:00Z",
        nodes=nodes,
        edges=edges,
        summary=GraphSummary(
            total_images=4,
            stale_images=2,
            classification_counts={
                "base": 1,
                "application": 1,
                "middleware": 1,
                "utility": 1,
            },
        ),
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_html_search_input_present() -> None:
    """The HTML output contains a search input element."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="search"' in result
    assert 'type="search"' in result


def test_html_classification_filter_dropdown_present() -> None:
    """The HTML output contains the classification filter dropdown."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="filter-classification"' in result
    # The select element should be present
    assert "<select" in result


def test_html_staleness_filter_dropdown_present() -> None:
    """The HTML output contains the staleness filter dropdown."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="filter-staleness"' in result
    assert "major_behind" in result


def test_html_registry_filter_dropdown_present() -> None:
    """The HTML output contains the registry filter dropdown."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="filter-registry"' in result
    # Registry hostnames from the graph should appear
    assert "registry.example.com" in result
    assert "gcr.io" in result


def test_html_staleness_badges_rendered() -> None:
    """Staleness badges appear in the output for nodes with staleness data."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "node-staleness-badge" in result
    # The three staleness states present in our graph
    assert "current" in result
    assert "behind" in result
    assert "major" in result


def test_html_node_cards_contain_image_names() -> None:
    """Node canonical names appear somewhere in the rendered output."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "base-image" in result
    assert "middleware" in result
    assert "utility" in result


def test_html_edge_relationship_types_present() -> None:
    """Edge relationship type values appear in the embedded graph data."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "builds_from" in result
    assert "consumes" in result


def test_html_staleness_summary_section_present() -> None:
    """The staleness summary section exists with pills for each category."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="staleness-bar"' in result
    assert "staleness-pill" in result


def test_html_empty_graph_renders_without_error() -> None:
    """An empty graph renders valid HTML without errors."""
    from shipwreck.output.html import export_html

    graph = Graph(generated_at="2026-02-28T00:00:00Z")
    result = export_html(graph)

    assert "<!DOCTYPE html>" in result
    assert "GRAPH_DATA" in result
    # No staleness bar rendered for empty graph
    assert 'id="staleness-bar"' not in result


def test_html_nodes_without_staleness_render() -> None:
    """Nodes without staleness data render correctly (unknown badge shown)."""
    from shipwreck.output.html import export_html

    graph = make_graph(2)
    result = export_html(graph)

    assert "<!DOCTYPE html>" in result
    # Unknown staleness badge should be present
    assert "node-staleness-badge" in result
    assert "unknown" in result


def test_html_css_grid_background_present() -> None:
    """The CSS includes the grid background pattern."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "background-image" in result
    assert "background-size" in result
    # The grid pattern uses 24px cells
    assert "24px" in result


def test_html_js_filter_functions_present() -> None:
    """JavaScript filter functions and event listeners are included."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "activeFilters" in result
    assert "computeVisible" in result
    assert "filter-classification" in result
    assert "filter-staleness" in result


def test_html_aria_attributes_present() -> None:
    """ARIA attributes are present on interactive elements."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "aria-label" in result
    assert "aria-live" in result
    assert "role=" in result


def test_html_font_stack_correct() -> None:
    """The Inter font stack is used as specified."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "'Inter'" in result or '"Inter"' in result
    assert "-apple-system" in result
    assert "BlinkMacSystemFont" in result
    assert "Segoe UI" in result


def test_html_staleness_counts_passed_to_template() -> None:
    """Staleness counts are embedded as a JS variable in the output."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "STALENESS_COUNTS" in result


def test_html_classification_badges_in_output() -> None:
    """Classification badge CSS classes appear in the output."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "node-cls-base" in result
    assert "node-cls-application" in result
    assert "node-cls-middleware" in result


def test_html_keyboard_search_shortcut_present() -> None:
    """The slash keyboard shortcut for search is present in the JS."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    # The shortcut key "/" should be referenced in JS
    assert '"/"\n' in result or '"/"' in result or "=== \"/\"" in result


def test_html_compute_staleness_counts_function() -> None:
    """_compute_staleness_counts returns correct counts for a known graph."""
    from shipwreck.output.html import _compute_staleness_counts

    graph = make_rich_graph()
    counts = _compute_staleness_counts(graph)

    assert counts["current"] == 1
    assert counts["behind"] == 1
    assert counts["major_behind"] == 1
    assert counts["unknown"] == 1  # library/utility has staleness=None


def test_html_extract_registries_function() -> None:
    """_extract_registries returns unique registry hostnames."""
    from shipwreck.output.html import _extract_registries

    graph = make_rich_graph()
    registries = _extract_registries(graph)

    assert "registry.example.com" in registries
    assert "gcr.io" in registries
    # library/utility has no registry prefix (plain name)
    assert "library" not in registries


def test_html_prepare_graph_data_staleness_field() -> None:
    """Node dicts in prepared graph data include a staleness field."""
    from shipwreck.output.html import _prepare_graph_data

    graph = make_rich_graph()
    data = _prepare_graph_data(graph)

    for node in data["nodes"]:
        assert "staleness" in node, f"Missing 'staleness' in node {node['id']!r}"


def test_html_diff_overlay_css_present() -> None:
    """Diff overlay CSS classes are present for added/removed/changed nodes."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "node-diff-added" in result
    assert "node-diff-removed" in result
    assert "node-diff-changed" in result


# ── UX improvement tests ─────────────────────────────────────────────────────


def test_html_edge_label_toggle_present() -> None:
    """The HTML output contains the edge label toggle checkbox."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert 'id="toggle-edge-labels"' in result
    assert "Show edge labels" in result


def test_html_hide_edge_labels_css_present() -> None:
    """The hide-edge-labels CSS class exists in the stylesheet."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert ".hide-edge-labels .edgeLabel" in result


def test_html_legend_edge_descriptions_present() -> None:
    """Legend contains descriptive text for edge types."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "Base image dependency (FROM)" in result
    assert "Build outputs this image" in result
    assert "Runtime dependency" in result
    assert "Variant (e.g. -slim, -alpine)" in result


def test_html_tag_overflow_indicator() -> None:
    """JS contains tag overflow logic and CSS class for +N pill."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    # CSS class for overflow pill exists
    assert "node-tag-overflow" in result
    # JS contains the overflow logic (adds +N when tags > 4)
    assert "allTags.length > 4" in result


def test_html_consumes_reversed_to_requires() -> None:
    """JS reverses consumes edges and labels them 'requires'."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert '"consumes"' in result  # relationship still in data
    assert 'label = "requires"' in result  # visual label changed


def test_html_source_file_filter_checkboxes_present() -> None:
    """Source file filter checkboxes appear with colored type dots."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "source-file-cb" in result
    assert "SOURCE_FILES" in result
    assert "source-dot" in result


def test_html_source_dedup() -> None:
    """Duplicate sources are deduplicated in prepared graph data."""
    from shipwreck.models import EdgeType, ImageSource
    from shipwreck.output.html import _dedup_sources

    sources = [
        ImageSource(repo="app", file="Dockerfile", line=1, relationship=EdgeType.BUILDS_FROM, tag="3.12"),
        ImageSource(repo="app", file="Dockerfile", line=1, relationship=EdgeType.BUILDS_FROM, tag="3.12"),
        ImageSource(repo="app", file="Dockerfile", line=1, relationship=EdgeType.PRODUCES, tag="3.12"),
    ]
    result = _dedup_sources(sources)

    assert len(result) == 2  # first two are duplicates, third differs by relationship


def test_html_tooltip_shows_source_dot() -> None:
    """Tooltip source display uses colored dots for file type."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "sourceDotClass" in result  # JS helper for tooltip source dots


def test_html_extract_source_repos() -> None:
    """_extract_source_repos returns unique repo names from node sources."""
    from shipwreck.output.html import _extract_source_repos

    graph = make_rich_graph()
    repos = _extract_source_repos(graph)

    assert "org/app" in repos


def test_html_classify_source_file() -> None:
    """_classify_source_file correctly categorizes file paths."""
    from shipwreck.output.html import _classify_source_file

    assert _classify_source_file("Dockerfile") == "Dockerfile"
    assert _classify_source_file("Dockerfile.test") == "Dockerfile"
    assert _classify_source_file("src/Dockerfile.prod") == "Dockerfile"
    assert _classify_source_file("docker-compose.yml") == "Compose"
    assert _classify_source_file("compose.yaml") == "Compose"
    assert _classify_source_file(".gitlab-ci.yml") == "GitLab CI"
    assert _classify_source_file("ci/.gitlab-ci/deploy.yml") == "GitLab CI"
    assert _classify_source_file(".github/workflows/test.yml") == "GitHub Actions"
    assert _classify_source_file("roles/web/tasks/main.yml") == "Ansible"
    assert _classify_source_file("docker-bake.hcl") == "Bake"
    assert _classify_source_file("k8s/deployment.yaml") == "YAML"
    assert _classify_source_file("Containerfile") == "Dockerfile"


def test_html_extract_source_types() -> None:
    """_extract_source_types returns unique file type categories."""
    from shipwreck.output.html import _extract_source_types

    graph = make_rich_graph()
    types = _extract_source_types(graph)

    assert "Dockerfile" in types
    assert "Compose" in types


def test_html_extract_source_files() -> None:
    """_extract_source_files returns unique repo/file entries with file_type."""
    from shipwreck.output.html import _extract_source_files

    graph = make_rich_graph()
    files = _extract_source_files(graph)

    keys = [f["key"] for f in files]
    assert "org/app/Dockerfile" in keys
    assert "org/app/docker-compose.yml" in keys

    # Each entry has a file_type
    for f in files:
        assert "file_type" in f


def test_html_dimmed_nodes_have_grayscale() -> None:
    """Dimmed nodes have grayscale filter in CSS."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "grayscale" in result
    assert ".node.dimmed" in result


# ── Round 3 UX tests ─────────────────────────────────────────────────────────


def test_build_source_tree_groups_by_type() -> None:
    """_build_source_tree groups source files by file_type."""
    from shipwreck.output.html import _build_source_tree

    source_files = [
        {"key": "app/Dockerfile", "repo": "app", "file": "Dockerfile", "file_type": "Dockerfile"},
        {"key": "app/compose.yaml", "repo": "app", "file": "compose.yaml", "file_type": "Compose"},
        {"key": "infra/Dockerfile", "repo": "infra", "file": "Dockerfile", "file_type": "Dockerfile"},
    ]
    tree = _build_source_tree(source_files)

    assert "Dockerfile" in tree
    assert "Compose" in tree
    assert len(tree["Dockerfile"]) == 2
    assert len(tree["Compose"]) == 1
    # Keys are sorted
    assert list(tree.keys()) == sorted(tree.keys())


def test_build_source_tree_empty() -> None:
    """_build_source_tree returns empty dict for empty input."""
    from shipwreck.output.html import _build_source_tree

    assert _build_source_tree([]) == {}


def test_html_source_tree_structure_present() -> None:
    """Source tree HTML structure renders with group, header, and type checkbox."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "source-tree-group" in result
    assert "source-tree-header" in result
    assert "source-type-cb" in result


def test_html_sync_parent_checkbox_js_present() -> None:
    """The syncParentCheckbox JS function is present."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "syncParentCheckbox" in result


def test_html_staleness_pill_cursor_pointer() -> None:
    """Staleness pills have cursor:pointer in CSS."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "cursor: pointer" in result or "cursor:pointer" in result


def test_html_staleness_pill_active_css() -> None:
    """The .staleness-pill.active CSS rule exists."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert ".staleness-pill.active" in result


def test_html_staleness_pill_click_handler_present() -> None:
    """Staleness pill click handler JS is present."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "staleness-pill" in result
    assert "pill.addEventListener" in result or 'pill.addEventListener("click"' in result


def test_html_badge_test_css_variables_dark() -> None:
    """--badge-test CSS variable exists in dark theme."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "--badge-test:" in result
    assert "--badge-test-text:" in result


def test_html_badge_test_css_variables_light() -> None:
    """--badge-test CSS variable exists in light theme (different value)."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    # Dark theme uses #422006, light uses #fffbeb — both should be present
    assert "#422006" in result
    assert "#fffbeb" in result


def test_html_test_in_legend() -> None:
    """Test classification appears in the legend."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert ">Test<" in result or "<span>Test</span>" in result


def test_html_source_tree_json_embedded() -> None:
    """SOURCE_TREE JS constant is embedded in the output."""
    from shipwreck.output.html import export_html

    graph = make_rich_graph()
    result = export_html(graph)

    assert "SOURCE_TREE" in result
