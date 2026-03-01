"""Scanner orchestrator — walks repos, runs parsers, builds the graph."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from shipwreck.config import RepositoryConfig, ShipwreckConfig
from shipwreck.models import Graph, ImageReference
from shipwreck.parsers.ansible import AnsibleParser
from shipwreck.parsers.bake import BakeParser
from shipwreck.parsers.base import Parser
from shipwreck.parsers.compose import ComposeParser
from shipwreck.parsers.dockerfile import DockerfileParser
from shipwreck.parsers.fallback import FallbackScanner
from shipwreck.parsers.github_actions import GitHubActionsParser
from shipwreck.parsers.gitlab_ci import GitLabCIParser

if TYPE_CHECKING:
    pass

console = Console()

# Parser priority order per PARSERS.md §Cross-Parser Coordination
# Fallback runs last and only on unclaimed files
_SPECIFIC_PARSERS: list[type[Parser]] = [
    DockerfileParser,
    BakeParser,
    ComposeParser,
    GitLabCIParser,
    GitHubActionsParser,
    AnsibleParser,
]

# Hidden directories that contain scannable files and must not be skipped
_ALLOWED_HIDDEN_DIRS: frozenset[str] = frozenset({".github", ".gitlab-ci"})

# Files/directories to always skip
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
    }
)

_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp4",
        ".mp3",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".lock",
    }
)


def _iter_repo_files(repo_path: Path) -> list[Path]:
    """Recursively yield all scannable files in a repository.

    Args:
        repo_path: Root of the repository.

    Returns:
        Sorted list of file paths.
    """
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        # Skip hidden dirs and known non-code dirs, but allow certain hidden dirs
        parts = path.relative_to(repo_path).parts
        if any(
            (part.startswith(".") and part not in _ALLOWED_HIDDEN_DIRS) or part in _SKIP_DIRS
            for part in parts[:-1]
        ):
            continue
        if path.suffix in _SKIP_EXTENSIONS:
            continue
        files.append(path)
    return sorted(files)


def scan_repo(
    repo_path: Path,
    repo_name: str,
    specific_parsers: list[Parser],
    fallback_parser: Parser,
) -> list[ImageReference]:
    """Scan a single repository for all image references.

    Runs specific parsers in priority order, then runs the fallback on
    any files that no specific parser claimed.

    Args:
        repo_path: Local path to the repository.
        repo_name: Short name for the repository.
        specific_parsers: Ordered list of specific parsers (no fallback).
        fallback_parser: The fallback scanner.

    Returns:
        All image references discovered in the repo.
    """
    all_references: list[ImageReference] = []
    claimed_files: set[Path] = set()

    all_files = _iter_repo_files(repo_path)

    for parser in specific_parsers:
        for file_path in all_files:
            if parser.can_handle(file_path):
                try:
                    refs = parser.parse(file_path, repo_name)
                    all_references.extend(refs)
                    claimed_files.add(file_path)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[yellow]Warning:[/yellow] Parser {parser.name} failed on {file_path}: {exc}")

    # Run fallback only on unclaimed files
    for file_path in all_files:
        if file_path not in claimed_files and fallback_parser.can_handle(file_path):
            try:
                refs = fallback_parser.parse(file_path, repo_name)
                all_references.extend(refs)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]Warning:[/yellow] Fallback scanner failed on {file_path}: {exc}")

    return all_references


def scan(
    config: ShipwreckConfig,
    cache_dir: Path,
    no_pull: bool = False,
    include_repos: list[str] | None = None,
    exclude_repos: list[str] | None = None,
    local_paths: dict[str, Path] | None = None,
) -> Graph:
    """Run a full scan of all configured repositories.

    Args:
        config: Validated shipwreck configuration.
        cache_dir: Directory for cached git clones.
        no_pull: If True, skip pulling updates to existing clones.
        include_repos: If set, only scan these named repos.
        exclude_repos: If set, skip these named repos.
        local_paths: Override repo paths (used in tests to avoid git operations).

    Returns:
        The built dependency graph.
    """
    from shipwreck.graph.builder import build_graph
    from shipwreck.graph.classifier import classify_nodes
    from shipwreck.graph.criticality import compute_criticality

    specific_parsers: list[Parser] = [cls() for cls in _SPECIFIC_PARSERS]
    fallback_parser: Parser = FallbackScanner()

    all_references: list[ImageReference] = []

    repos_to_scan = _resolve_repos(config, include_repos, exclude_repos)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for repo_cfg in repos_to_scan:
            repo_name = repo_cfg.effective_name()
            task = progress.add_task(f"Scanning {repo_name}...", total=None)

            # Determine the local path for this repo
            if local_paths and repo_name in local_paths:
                repo_path = local_paths[repo_name]
            elif repo_cfg.path:
                repo_path = Path(repo_cfg.path)
            elif repo_cfg.url:
                repo_path = _ensure_git_repo(repo_cfg, cache_dir, no_pull)
            else:
                progress.update(task, description=f"Skipping {repo_name} (no url/path)")
                continue

            if not repo_path.exists():
                console.print(f"[yellow]Warning:[/yellow] Repo path does not exist: {repo_path}")
                continue

            refs = scan_repo(repo_path, repo_name, specific_parsers, fallback_parser)
            all_references.extend(refs)
            progress.update(task, description=f"[green]✓[/green] {repo_name} ({len(refs)} refs)")

    # --- Resolution phase ---
    if config.resolve_env_vars:
        from shipwreck.resolution.env import resolve_env

        all_references = resolve_env(all_references)

    from shipwreck.resolution.bake import resolve_bake
    from shipwreck.resolution.compose import resolve_compose

    all_references = resolve_compose(all_references)
    all_references = resolve_bake(all_references)

    from shipwreck.resolution.ansible import resolve_ansible

    ansible_unresolved = [
        r for r in all_references
        if r.source.parser == "ansible" and r.unresolved_variables
    ]
    if ansible_unresolved:
        all_references = resolve_ansible(all_references, config.ansible)

    # Build graph
    config_hash = _hash_config(config)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    graph = build_graph(all_references, config, generated_at=generated_at)
    graph.config_hash = config_hash

    # Record Ansible environment in graph if configured
    if config.ansible:
        graph.environment.ansible_inventory = config.ansible.inventory
        graph.environment.ansible_limit = config.ansible.limit

    # Classify and score
    classify_nodes(graph, config.classification)
    compute_criticality(graph)

    # Update summary counts
    _update_summary(graph)

    return graph


def _resolve_repos(
    config: ShipwreckConfig,
    include_repos: list[str] | None,
    exclude_repos: list[str] | None,
) -> list[RepositoryConfig]:
    """Filter repositories based on include/exclude lists.

    Args:
        config: The config with repositories.
        include_repos: Whitelist of repo names.
        exclude_repos: Blacklist of repo names.

    Returns:
        Filtered list of RepositoryConfig.
    """
    repos = config.repositories
    if include_repos:
        repos = [r for r in repos if r.effective_name() in include_repos]
    if exclude_repos:
        repos = [r for r in repos if r.effective_name() not in exclude_repos]
    return repos


def _ensure_git_repo(repo_cfg: RepositoryConfig, cache_dir: Path, no_pull: bool) -> Path:
    """Clone or update a git repository.

    Args:
        repo_cfg: Repository configuration.
        cache_dir: Directory for caching clones.
        no_pull: Skip pulling if the repo already exists.

    Returns:
        Path to the local repository.
    """
    from shipwreck.git import ensure_repo

    assert repo_cfg.url is not None
    name = repo_cfg.effective_name()
    return ensure_repo(
        url=repo_cfg.url,
        cache_dir=cache_dir,
        name=name,
        ref=repo_cfg.ref,
        no_pull=no_pull,
    )


def _hash_config(config: ShipwreckConfig) -> str:
    """Compute a hash of the config for change detection.

    Args:
        config: The config to hash.

    Returns:
        SHA256 hex digest.
    """
    data = config.model_dump_json()
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()[:16]


def _update_summary(graph: Graph) -> None:
    """Update graph summary statistics in-place.

    Args:
        graph: The graph to update.
    """
    from collections import Counter

    class_counter: Counter[str] = Counter()
    unresolved = 0

    for node in graph.nodes.values():
        if node.classification:
            class_counter[node.classification] += 1
        # Detect unresolved templates by checking if node id looks like a template
        if "{{" in node.id or "${" in node.id:
            unresolved += 1

    graph.summary.total_images = len(graph.nodes)
    graph.summary.unresolved_references = unresolved
    graph.summary.classification_counts = dict(class_counter)
