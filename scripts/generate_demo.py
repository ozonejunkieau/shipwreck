"""Generate a rich demo graph and export it to HTML, JSON, and Mermaid.

This script builds a realistic Graph with ~15 nodes covering all classification
types, staleness states, multiple registries, and a variety of edge types, then
exports everything to demo_output/.

Usage:
    cd /path/to/shipwreck
    uv run python scripts/generate_demo.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shipwreck.models import (
    EdgeType,
    Confidence,
    Graph,
    GraphEdge,
    GraphNode,
    GraphSummary,
    ImageSource,
    ImageVariant,
    RegistryMetadata,
    SourceLocation,
)
from shipwreck.output.html import export_html
from shipwreck.output.json_export import export_json
from shipwreck.output.mermaid import export_mermaid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _src(repo: str, file: str, line: int, rel: EdgeType, tag: str | None = None) -> ImageSource:
    return ImageSource(repo=repo, file=file, line=line, relationship=rel, tag=tag)


def _loc(repo: str, file: str, line: int, parser: str = "dockerfile") -> SourceLocation:
    return SourceLocation(repo=repo, file=file, line=line, parser=parser)


def _edge(
    source: str,
    target: str,
    rel: EdgeType,
    conf: Confidence,
    repo: str,
    file: str,
    line: int,
    parser: str = "dockerfile",
) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relationship=rel,
        confidence=conf,
        source_location=_loc(repo, file, line, parser),
    )


# ---------------------------------------------------------------------------
# Build the demo graph
# ---------------------------------------------------------------------------

def build_demo_graph() -> Graph:
    nodes: dict[str, GraphNode] = {}

    # ── 1. docker.io/library/python:3.12 — external base image ───────────
    nodes["docker.io/library/python"] = GraphNode(
        id="docker.io/library/python",
        canonical="docker.io/library/python",
        tags_referenced=["3.12", "3.12-slim", "3.11"],
        latest_available="3.13",
        staleness="behind",
        version_scheme="semver",
        classification="external",
        criticality=0.95,
        registry_metadata=RegistryMetadata(
            size_bytes=987_654_321,
            build_date="2024-09-01T00:00:00Z",
            digest="sha256:aaaa1111",
        ),
        variants=[
            ImageVariant(tag_suffix="-slim", variant_type="slim"),
            ImageVariant(tag_suffix="-alpine", variant_type="alpine"),
        ],
        sources=[
            _src("backend-api", "Dockerfile", 1, EdgeType.BUILDS_FROM, "3.12"),
            _src("data-pipeline", "Dockerfile", 1, EdgeType.BUILDS_FROM, "3.12-slim"),
            _src("ml-trainer", "Dockerfile", 1, EdgeType.BUILDS_FROM, "3.11"),
        ],
    )

    # ── 2. docker.io/library/python:3.12-slim — variant node ─────────────
    nodes["docker.io/library/python-slim"] = GraphNode(
        id="docker.io/library/python-slim",
        canonical="docker.io/library/python-slim",
        tags_referenced=["3.12-slim", "3.11-slim"],
        latest_available="3.13-slim",
        staleness="behind",
        version_scheme="semver",
        classification="external",
        criticality=0.80,
        registry_metadata=RegistryMetadata(
            size_bytes=123_456_789,
            build_date="2024-09-01T00:00:00Z",
            digest="sha256:bbbb2222",
        ),
        sources=[
            _src("frontend-svc", "Dockerfile", 1, EdgeType.BUILDS_FROM, "3.12-slim"),
        ],
    )

    # ── 3. ghcr.io/acme/base-python — internal base layer ────────────────
    nodes["ghcr.io/acme/base-python"] = GraphNode(
        id="ghcr.io/acme/base-python",
        canonical="ghcr.io/acme/base-python",
        tags_referenced=["3.12-v2", "3.12-v1", "3.11-v3", "3.11-v2"],
        latest_available="3.12-v2",
        staleness="current",
        version_scheme="semver",
        classification="base",
        criticality=0.90,
        registry_metadata=RegistryMetadata(
            size_bytes=456_123_000,
            build_date="2025-01-15T10:00:00Z",
            digest="sha256:cccc3333",
        ),
        sources=[
            _src("backend-api", "Dockerfile", 1, EdgeType.PRODUCES, "3.12-v2"),
            _src("backend-api", "Dockerfile", 1, EdgeType.BUILDS_FROM, "3.12-v2"),
        ],
    )

    # ── 4. ghcr.io/acme/backend-api — application image ──────────────────
    nodes["ghcr.io/acme/backend-api"] = GraphNode(
        id="ghcr.io/acme/backend-api",
        canonical="ghcr.io/acme/backend-api",
        tags_referenced=["2.4.1", "2.4.0", "2.3.9", "latest"],
        latest_available="2.4.1",
        staleness="current",
        version_scheme="semver",
        classification="application",
        criticality=0.85,
        registry_metadata=RegistryMetadata(
            size_bytes=512_000_000,
            build_date="2025-02-10T08:30:00Z",
            digest="sha256:dddd4444",
        ),
        sources=[
            _src("backend-api", "Dockerfile", 1, EdgeType.PRODUCES, "2.4.1"),
            _src("infra-deploy", "docker-compose.yml", 5, EdgeType.CONSUMES, "2.4.1"),
            _src("k8s-manifests", "k8s/deployment.yaml", 12, EdgeType.CONSUMES, "2.4.1"),
        ],
    )

    # ── 5. ghcr.io/acme/frontend-svc — application image, slightly stale ─
    nodes["ghcr.io/acme/frontend-svc"] = GraphNode(
        id="ghcr.io/acme/frontend-svc",
        canonical="ghcr.io/acme/frontend-svc",
        tags_referenced=["1.9.2", "1.9.1"],
        latest_available="1.10.0",
        staleness="behind",
        version_scheme="semver",
        classification="application",
        criticality=0.75,
        registry_metadata=RegistryMetadata(
            size_bytes=280_000_000,
            build_date="2025-01-20T12:00:00Z",
            digest="sha256:eeee5555",
        ),
        sources=[
            _src("frontend-svc", "Dockerfile", 1, EdgeType.PRODUCES, "1.9.2"),
            _src("infra-deploy", "docker-compose.yml", 15, EdgeType.CONSUMES, "1.9.2"),
        ],
    )

    # ── 6. ghcr.io/acme/data-pipeline — application, major behind ────────
    nodes["ghcr.io/acme/data-pipeline"] = GraphNode(
        id="ghcr.io/acme/data-pipeline",
        canonical="ghcr.io/acme/data-pipeline",
        tags_referenced=["0.8.3"],
        latest_available="2.1.0",
        staleness="major_behind",
        version_scheme="semver",
        classification="application",
        criticality=0.60,
        registry_metadata=RegistryMetadata(
            size_bytes=620_000_000,
            build_date="2024-06-01T00:00:00Z",
            digest="sha256:ffff6666",
        ),
        sources=[
            _src("data-pipeline", "Dockerfile", 1, EdgeType.PRODUCES, "0.8.3"),
            _src("infra-deploy", "docker-compose.yml", 30, EdgeType.CONSUMES, "0.8.3"),
        ],
    )

    # ── 7. docker.io/library/postgres — middleware, current ───────────────
    nodes["docker.io/library/postgres"] = GraphNode(
        id="docker.io/library/postgres",
        canonical="docker.io/library/postgres",
        tags_referenced=["16", "16-alpine", "15"],
        latest_available="16",
        staleness="current",
        version_scheme="numeric",
        classification="middleware",
        criticality=0.88,
        registry_metadata=RegistryMetadata(
            size_bytes=370_000_000,
            build_date="2024-11-01T00:00:00Z",
            digest="sha256:gggg7777",
        ),
        sources=[
            _src("infra-deploy", "docker-compose.yml", 45, EdgeType.CONSUMES, "16"),
            _src("backend-api", "docker-compose.yml", 20, EdgeType.CONSUMES, "16"),
        ],
    )

    # ── 8. docker.io/library/redis — middleware, behind ───────────────────
    nodes["docker.io/library/redis"] = GraphNode(
        id="docker.io/library/redis",
        canonical="docker.io/library/redis",
        tags_referenced=["7.2", "7.0"],
        latest_available="7.4",
        staleness="behind",
        version_scheme="semver",
        classification="middleware",
        criticality=0.70,
        registry_metadata=RegistryMetadata(
            size_bytes=130_000_000,
            build_date="2024-08-15T00:00:00Z",
            digest="sha256:hhhh8888",
        ),
        sources=[
            _src("infra-deploy", "docker-compose.yml", 60, EdgeType.CONSUMES, "7.2"),
            _src("backend-api", "docker-compose.yml", 25, EdgeType.CONSUMES, "7.2"),
        ],
    )

    # ── 9. registry.example.com/ops/nginx-proxy — utility, current ────────
    nodes["registry.example.com/ops/nginx-proxy"] = GraphNode(
        id="registry.example.com/ops/nginx-proxy",
        canonical="registry.example.com/ops/nginx-proxy",
        tags_referenced=["1.25-custom", "1.24-custom"],
        latest_available="1.25-custom",
        staleness="current",
        version_scheme="semver",
        classification="utility",
        criticality=0.65,
        registry_metadata=RegistryMetadata(
            size_bytes=92_000_000,
            build_date="2025-01-05T00:00:00Z",
            digest="sha256:iiii9999",
        ),
        sources=[
            _src("infra-deploy", "docker-compose.yml", 80, EdgeType.CONSUMES, "1.25-custom"),
        ],
    )

    # ── 10. registry.example.com/ops/vault-agent — utility, major behind ──
    nodes["registry.example.com/ops/vault-agent"] = GraphNode(
        id="registry.example.com/ops/vault-agent",
        canonical="registry.example.com/ops/vault-agent",
        tags_referenced=["1.12.0"],
        latest_available="1.17.0",
        staleness="major_behind",
        version_scheme="semver",
        classification="utility",
        criticality=0.72,
        registry_metadata=RegistryMetadata(
            size_bytes=210_000_000,
            build_date="2023-11-01T00:00:00Z",
            digest="sha256:jjjjaaaa",
        ),
        sources=[
            _src("k8s-manifests", "k8s/vault-agent.yaml", 8, EdgeType.CONSUMES, "1.12.0"),
        ],
    )

    # ── 11. ghcr.io/acme/ml-trainer — application, staleness unknown ──────
    nodes["ghcr.io/acme/ml-trainer"] = GraphNode(
        id="ghcr.io/acme/ml-trainer",
        canonical="ghcr.io/acme/ml-trainer",
        tags_referenced=["20240601", "20240501"],
        latest_available=None,
        staleness="unknown",
        version_scheme="date",
        classification="application",
        criticality=0.45,
        registry_metadata=RegistryMetadata(),
        sources=[
            _src("ml-trainer", "Dockerfile", 1, EdgeType.PRODUCES, "20240601"),
        ],
    )

    # ── 12. docker.io/library/node — external base, major behind ──────────
    nodes["docker.io/library/node"] = GraphNode(
        id="docker.io/library/node",
        canonical="docker.io/library/node",
        tags_referenced=["18-alpine", "18"],
        latest_available="22-alpine",
        staleness="major_behind",
        version_scheme="numeric",
        classification="external",
        criticality=0.78,
        registry_metadata=RegistryMetadata(
            size_bytes=180_000_000,
            build_date="2024-04-01T00:00:00Z",
            digest="sha256:kkkkbbbb",
        ),
        sources=[
            _src("frontend-svc", "Dockerfile", 1, EdgeType.BUILDS_FROM, "18-alpine"),
        ],
    )

    # ── 13. ghcr.io/acme/base-node — internal base, current ──────────────
    nodes["ghcr.io/acme/base-node"] = GraphNode(
        id="ghcr.io/acme/base-node",
        canonical="ghcr.io/acme/base-node",
        tags_referenced=["18-v3", "18-v2"],
        latest_available="18-v3",
        staleness="current",
        version_scheme="numeric",
        classification="base",
        criticality=0.80,
        registry_metadata=RegistryMetadata(
            size_bytes=200_000_000,
            build_date="2025-01-10T00:00:00Z",
            digest="sha256:llllcccc",
        ),
        sources=[
            _src("frontend-svc", "Dockerfile", 1, EdgeType.PRODUCES, "18-v3"),
            _src("frontend-svc", "Dockerfile", 1, EdgeType.BUILDS_FROM, "18-v3"),
        ],
    )

    # ── 14. registry.example.com/legacy/auth-proxy — unknown, no staleness
    nodes["registry.example.com/legacy/auth-proxy"] = GraphNode(
        id="registry.example.com/legacy/auth-proxy",
        canonical="registry.example.com/legacy/auth-proxy",
        tags_referenced=["v0.3.1"],
        latest_available=None,
        staleness=None,
        version_scheme=None,
        classification="unknown",
        criticality=0.30,
        registry_metadata=RegistryMetadata(),
        sources=[
            _src("k8s-manifests", "k8s/legacy.yaml", 22, EdgeType.CONSUMES, "v0.3.1"),
        ],
    )

    # ── 15. docker.io/library/alpine — external base, current ─────────────
    nodes["docker.io/library/alpine"] = GraphNode(
        id="docker.io/library/alpine",
        canonical="docker.io/library/alpine",
        tags_referenced=["3.19", "3.18", "latest"],
        latest_available="3.21",
        staleness="behind",
        version_scheme="semver",
        classification="external",
        criticality=0.55,
        registry_metadata=RegistryMetadata(
            size_bytes=7_800_000,
            build_date="2024-12-01T00:00:00Z",
            digest="sha256:mmmmdddd",
        ),
        sources=[
            _src("infra-deploy", "Dockerfile.nginx", 1, EdgeType.BUILDS_FROM, "3.19"),
        ],
    )

    # ── 16. ghcr.io/acme/backend-api-test — test image for backend ───────
    nodes["ghcr.io/acme/backend-api-test"] = GraphNode(
        id="ghcr.io/acme/backend-api-test",
        canonical="ghcr.io/acme/backend-api-test",
        tags_referenced=["2.4.1-test", "2.4.0-test", "latest"],
        latest_available="2.4.1-test",
        staleness="current",
        version_scheme="semver",
        classification="test",
        criticality=0.30,
        registry_metadata=RegistryMetadata(
            size_bytes=680_000_000,
            build_date="2025-02-10T09:00:00Z",
            digest="sha256:tttt1111",
        ),
        sources=[
            _src("backend-api", "Dockerfile.test", 1, EdgeType.PRODUCES, "2.4.1-test"),
            _src("ci-pipelines", ".gitlab-ci.yml", 42, EdgeType.CONSUMES, "2.4.1-test"),
        ],
    )

    # ── 17. ghcr.io/acme/integration-tests — shared test runner ────────
    nodes["ghcr.io/acme/integration-tests"] = GraphNode(
        id="ghcr.io/acme/integration-tests",
        canonical="ghcr.io/acme/integration-tests",
        tags_referenced=["1.2.0", "1.1.0", "1.0.0", "0.9.0", "latest"],
        latest_available="1.2.0",
        staleness="current",
        version_scheme="semver",
        classification="test",
        criticality=0.40,
        registry_metadata=RegistryMetadata(
            size_bytes=920_000_000,
            build_date="2025-02-15T14:00:00Z",
            digest="sha256:tttt2222",
        ),
        sources=[
            _src("test-infra", "Dockerfile", 1, EdgeType.PRODUCES, "1.2.0"),
            _src("ci-pipelines", ".gitlab-ci.yml", 78, EdgeType.CONSUMES, "1.2.0"),
            _src("ci-pipelines", ".github/workflows/test.yml", 15, EdgeType.CONSUMES, "1.2.0"),
        ],
    )

    # ── Edges ─────────────────────────────────────────────────────────────

    edges: list[GraphEdge] = [
        # python base → acme base-python (builds_from, high confidence)
        _edge(
            "ghcr.io/acme/base-python",
            "docker.io/library/python",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "backend-api", "Dockerfile", 1,
        ),
        # acme base-python → backend-api (builds_from, high)
        _edge(
            "ghcr.io/acme/backend-api",
            "ghcr.io/acme/base-python",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "backend-api", "Dockerfile", 3,
        ),
        # python slim → acme base-node (represents the slim variant relationship)
        _edge(
            "docker.io/library/python-slim",
            "docker.io/library/python",
            EdgeType.PRODUCES,
            Confidence.MEDIUM,
            "backend-api", "Dockerfile", 2,
        ),
        # node base → acme base-node (builds_from, high)
        _edge(
            "ghcr.io/acme/base-node",
            "docker.io/library/node",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "frontend-svc", "Dockerfile", 1,
        ),
        # acme base-node → frontend-svc (builds_from, high)
        _edge(
            "ghcr.io/acme/frontend-svc",
            "ghcr.io/acme/base-node",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "frontend-svc", "Dockerfile", 3,
        ),
        # python → data-pipeline (builds_from, high)
        _edge(
            "ghcr.io/acme/data-pipeline",
            "docker.io/library/python",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "data-pipeline", "Dockerfile", 1,
        ),
        # python → ml-trainer (builds_from, medium — via variable resolution)
        _edge(
            "ghcr.io/acme/ml-trainer",
            "docker.io/library/python",
            EdgeType.BUILDS_FROM,
            Confidence.MEDIUM,
            "ml-trainer", "Dockerfile", 1,
        ),
        # infra-deploy consumes backend-api
        _edge(
            "ghcr.io/acme/backend-api",
            "docker.io/library/postgres",
            EdgeType.CONSUMES,
            Confidence.HIGH,
            "infra-deploy", "docker-compose.yml", 45,
            "compose",
        ),
        # infra-deploy consumes redis
        _edge(
            "ghcr.io/acme/backend-api",
            "docker.io/library/redis",
            EdgeType.CONSUMES,
            Confidence.HIGH,
            "infra-deploy", "docker-compose.yml", 60,
            "compose",
        ),
        # infra-deploy consumes nginx-proxy
        _edge(
            "ghcr.io/acme/frontend-svc",
            "registry.example.com/ops/nginx-proxy",
            EdgeType.CONSUMES,
            Confidence.HIGH,
            "infra-deploy", "docker-compose.yml", 80,
            "compose",
        ),
        # k8s consumes vault-agent (low confidence — fallback parser)
        _edge(
            "ghcr.io/acme/backend-api",
            "registry.example.com/ops/vault-agent",
            EdgeType.CONSUMES,
            Confidence.LOW,
            "k8s-manifests", "k8s/vault-agent.yaml", 8,
            "fallback",
        ),
        # k8s consumes legacy auth-proxy (low confidence)
        _edge(
            "ghcr.io/acme/frontend-svc",
            "registry.example.com/legacy/auth-proxy",
            EdgeType.CONSUMES,
            Confidence.LOW,
            "k8s-manifests", "k8s/legacy.yaml", 22,
            "fallback",
        ),
        # alpine → data-pipeline build context (builds_from, medium)
        _edge(
            "registry.example.com/ops/nginx-proxy",
            "docker.io/library/alpine",
            EdgeType.BUILDS_FROM,
            Confidence.MEDIUM,
            "infra-deploy", "Dockerfile.nginx", 1,
        ),
        # python slim is used directly by frontend-svc scripts (builds_from, medium)
        _edge(
            "ghcr.io/acme/frontend-svc",
            "docker.io/library/python-slim",
            EdgeType.BUILDS_FROM,
            Confidence.MEDIUM,
            "frontend-svc", "Dockerfile", 5,
        ),
        # backend-api-test builds from base-python
        _edge(
            "ghcr.io/acme/backend-api-test",
            "ghcr.io/acme/base-python",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "backend-api", "Dockerfile.test", 1,
        ),
        # backend-api-test consumes backend-api (tests against it)
        _edge(
            "ghcr.io/acme/backend-api-test",
            "ghcr.io/acme/backend-api",
            EdgeType.CONSUMES,
            Confidence.MEDIUM,
            "ci-pipelines", ".gitlab-ci.yml", 42,
            "gitlab_ci",
        ),
        # integration-tests builds from base-python
        _edge(
            "ghcr.io/acme/integration-tests",
            "ghcr.io/acme/base-python",
            EdgeType.BUILDS_FROM,
            Confidence.HIGH,
            "test-infra", "Dockerfile", 1,
        ),
        # integration-tests consumes postgres (needs DB for tests)
        _edge(
            "ghcr.io/acme/integration-tests",
            "docker.io/library/postgres",
            EdgeType.CONSUMES,
            Confidence.HIGH,
            "ci-pipelines", ".gitlab-ci.yml", 80,
            "gitlab_ci",
        ),
        # integration-tests consumes redis (needs cache for tests)
        _edge(
            "ghcr.io/acme/integration-tests",
            "docker.io/library/redis",
            EdgeType.CONSUMES,
            Confidence.MEDIUM,
            "ci-pipelines", ".gitlab-ci.yml", 85,
            "gitlab_ci",
        ),
    ]

    # ── Summary ───────────────────────────────────────────────────────────
    classification_counts: dict[str, int] = {}
    staleness_counts: dict[str, int] = {}
    for node in nodes.values():
        cls = node.classification or "unknown"
        classification_counts[cls] = classification_counts.get(cls, 0) + 1
        st = node.staleness or "unknown"
        staleness_counts[st] = staleness_counts.get(st, 0) + 1

    summary = GraphSummary(
        total_images=len(nodes),
        stale_images=staleness_counts.get("behind", 0)
            + staleness_counts.get("major_behind", 0),
        unresolved_references=1,  # ml-trainer uses a date-tagged image with no registry info
        classification_counts=classification_counts,
    )

    graph = Graph(
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        config_hash="demo-hardcoded-no-config",
        nodes=nodes,
        edges=edges,
        summary=summary,
    )

    return graph


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    output_dir = Path(__file__).parent.parent / "demo_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Building demo graph...")
    graph = build_demo_graph()
    print(f"  {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    json_path = output_dir / "shipwreck.json"
    html_path = output_dir / "shipwreck.html"
    mermaid_path = output_dir / "shipwreck.mermaid"

    print(f"Exporting JSON  → {json_path}")
    export_json(graph, json_path)

    print(f"Exporting HTML  → {html_path}")
    export_html(graph, html_path)

    print(f"Exporting Mermaid → {mermaid_path}")
    export_mermaid(graph, mermaid_path)

    print()
    print("Done. Classification breakdown:")
    for cls, count in sorted(graph.summary.classification_counts.items()):
        print(f"  {cls:15s} {count}")
    print()
    print("Staleness breakdown:")
    for node in graph.nodes.values():
        print(f"  {node.canonical.split('/')[-1]:30s} {node.staleness or 'None':15s} {node.classification or 'None'}")


if __name__ == "__main__":
    main()
