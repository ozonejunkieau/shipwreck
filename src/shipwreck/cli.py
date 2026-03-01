"""Shipwreck CLI — all commands, pirate-themed.

🏴‍☠️ Mapping the buried treasure in your container stack.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="shipwreck",
    help="🏴‍☠️ Mapping the buried treasure in your container stack.",
    add_completion=False,
)

console = Console()


def _verbose_callback(value: bool) -> None:
    """Configure logging when --verbose is passed."""
    if value:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )


@app.callback()
def _app_callback(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Log outbound network requests and policy decisions.", callback=_verbose_callback, is_eager=True),
    ] = False,
) -> None:
    """🏴\u200d☠️ Mapping the buried treasure in your container stack."""

# --------------------------------------------------------------------------- #
# Shared option types
# --------------------------------------------------------------------------- #

ConfigOption = Annotated[
    Path,
    typer.Option("--config", "-c", help="Config file.", show_default=True),
]
OutputOption = Annotated[
    Path,
    typer.Option("--output", "-o", help="Output directory.", show_default=True),
]
FormatOption = Annotated[
    str,
    typer.Option("--format", help="Output format: html, mermaid, json, or all."),
]

_DEFAULT_CONFIG = Path("shipwreck.yaml")
_DEFAULT_CACHE_DIR = Path(".shipwreck/repos")
_DEFAULT_OUTPUT_DIR = Path(".shipwreck/output")
_DEFAULT_SNAPSHOT_DIR = Path(".shipwreck/snapshots")


# --------------------------------------------------------------------------- #
# hunt — scan repos and discover image references
# --------------------------------------------------------------------------- #


@app.command()
def hunt(
    config: ConfigOption = _DEFAULT_CONFIG,
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Cached repo dir.")] = _DEFAULT_CACHE_DIR,
    no_pull: Annotated[bool, typer.Option("--no-pull", help="Don't pull — use cached repos.")] = False,
    include_repo: Annotated[list[str] | None, typer.Option("--include-repo", help="Only scan these repos.")] = None,
    exclude_repo: Annotated[list[str] | None, typer.Option("--exclude-repo", help="Skip these repos.")] = None,
    snapshot: Annotated[bool, typer.Option("--snapshot", help="Save a timestamped snapshot.")] = False,
    output_dir: OutputOption = _DEFAULT_OUTPUT_DIR,
) -> None:
    """Scour the seas for containers. Scan repos and discover all image references."""
    from shipwreck.scanner import scan

    console.print("[bold blue]🏴‍☠️ Shipwreck Hunt[/bold blue] — scanning for buried treasure...")

    cfg = _load_config_or_exit(config)

    graph = scan(
        config=cfg,
        cache_dir=cache_dir,
        no_pull=no_pull,
        include_repos=include_repo,
        exclude_repos=exclude_repo,
    )

    console.print(f"\n[green]✓[/green] Discovered [bold]{graph.summary.total_images}[/bold] images, "
                  f"[bold]{len(graph.edges)}[/bold] relationships, "
                  f"[bold]{graph.summary.unresolved_references}[/bold] unresolved references.")

    if snapshot:
        from shipwreck.output.snapshot import save_snapshot

        snap_path = save_snapshot(graph, _DEFAULT_SNAPSHOT_DIR)
        console.print(f"[green]✓[/green] Snapshot saved to [cyan]{snap_path}[/cyan]")

    # Cache graph for map command
    _save_latest_graph(graph, output_dir)


# --------------------------------------------------------------------------- #
# map — generate reports
# --------------------------------------------------------------------------- #


@app.command(name="map")
def map_command(
    config: ConfigOption = _DEFAULT_CONFIG,
    output_dir: OutputOption = _DEFAULT_OUTPUT_DIR,
    format: FormatOption = "all",
    snapshot: Annotated[bool, typer.Option("--snapshot", help="Also save a timestamped snapshot.")] = False,
    mermaid_per_repo: Annotated[bool, typer.Option("--mermaid-per-repo", help="Generate per-repo Mermaid files.")] = False,
    diff_from: Annotated[Path | None, typer.Option("--diff-from", help="Previous snapshot JSON for diff.")] = None,
) -> None:
    """Chart the waters. Generate the dependency report (HTML, Mermaid, JSON)."""
    from shipwreck.output.html import export_html
    from shipwreck.output.json_export import export_json
    from shipwreck.output.mermaid import export_mermaid, export_mermaid_per_repo
    from shipwreck.output.snapshot import save_snapshot

    console.print("[bold blue]🏴‍☠️ Shipwreck Map[/bold blue] — charting the waters...")

    # Load the cached graph from hunt, or rebuild from config
    graph = _load_latest_graph(output_dir)
    if graph is None:
        cfg = _load_config_or_exit(config)
        from shipwreck.scanner import scan

        graph = scan(config=cfg, cache_dir=_DEFAULT_CACHE_DIR)

    output_dir.mkdir(parents=True, exist_ok=True)

    formats = _parse_formats(format)

    if "json" in formats:
        path = output_dir / "shipwreck.json"
        export_json(graph, output_path=path)
        console.print(f"[green]✓[/green] JSON → [cyan]{path}[/cyan]")

    if "mermaid" in formats:
        path = output_dir / "shipwreck.mermaid"
        export_mermaid(graph, output_path=path)
        console.print(f"[green]✓[/green] Mermaid → [cyan]{path}[/cyan]")
        if mermaid_per_repo:
            per_repo_dir = output_dir / "per-repo"
            export_mermaid_per_repo(graph, output_dir=per_repo_dir)
            console.print(f"[green]✓[/green] Per-repo Mermaid → [cyan]{per_repo_dir}[/cyan]")

    if "html" in formats:
        path = output_dir / "shipwreck.html"
        export_html(graph, output_path=path)
        console.print(f"[green]✓[/green] HTML → [cyan]{path}[/cyan]")

    if snapshot:
        snap_dir = output_dir.parent / "snapshots"
        snap_path = save_snapshot(graph, snap_dir)
        console.print(f"[green]✓[/green] Snapshot → [cyan]{snap_path}[/cyan]")


# --------------------------------------------------------------------------- #
# dig — query the metadata
# --------------------------------------------------------------------------- #


@app.command()
def dig(
    snapshot_path: Annotated[Path | None, typer.Option("--snapshot", "-s", help="Snapshot JSON to query.")] = None,
    uses: Annotated[str | None, typer.Option("--uses", help="What uses this image?")] = None,
    used_by: Annotated[str | None, typer.Option("--used-by", help="What does this image depend on?")] = None,
    stale: Annotated[bool, typer.Option("--stale", help="List all stale images.")] = False,
    critical: Annotated[bool, typer.Option("--critical", help="List images by criticality.")] = False,
    classify: Annotated[str | None, typer.Option("--classify", help="Filter by classification.")] = None,
    format: Annotated[str, typer.Option("--format", help="Output format: json, text, table.")] = "table",
) -> None:
    """What lies beneath? Query the metadata."""

    engine = _load_engine_or_exit(snapshot_path, _DEFAULT_SNAPSHOT_DIR.parent)

    nodes = None

    if uses:
        nodes = engine.uses(uses)
        if format != "json":
            console.print(f"\n[bold]Images that use [cyan]{uses}[/cyan]:[/bold]")
    elif used_by:
        nodes = engine.used_by(used_by)
        if format != "json":
            console.print(f"\n[bold]Dependencies of [cyan]{used_by}[/cyan]:[/bold]")
    elif stale:
        nodes = engine.stale()
        if format != "json":
            console.print("\n[bold]Stale images:[/bold]")
    elif critical:
        nodes = engine.critical()
        if format != "json":
            console.print("\n[bold]Images by criticality:[/bold]")
    elif classify:
        nodes = engine.by_classification(classify)
        if format != "json":
            console.print(f"\n[bold]Images classified as [cyan]{classify}[/cyan]:[/bold]")
    else:
        # Show summary
        graph = engine._graph
        console.print("\n[bold]Graph Summary[/bold]")
        console.print(f"  Total images:         {graph.summary.total_images}")
        console.print(f"  Stale images:         {graph.summary.stale_images}")
        console.print(f"  Unresolved refs:      {graph.summary.unresolved_references}")
        console.print(f"  Generated at:         {graph.generated_at}")
        for cls, count in sorted(graph.summary.classification_counts.items()):
            console.print(f"  {cls:20s}: {count}")
        return

    if nodes is not None:
        _display_nodes(nodes, format_=format)


# --------------------------------------------------------------------------- #
# log — compare snapshots and show changes
# --------------------------------------------------------------------------- #


@app.command()
def log(
    before: Annotated[Path | None, typer.Option("--before", help="Previous snapshot JSON.")] = None,
    after: Annotated[Path | None, typer.Option("--after", help="Current snapshot JSON (default: latest).")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output diff report path.")] = None,
    format: Annotated[str, typer.Option("--format", help="Output format: json, text, table.")] = "table",
) -> None:
    """The captain's log. Compare snapshots and show changes."""
    from shipwreck.output.snapshot import diff_snapshots, find_latest_snapshot, load_snapshot

    if format != "json":
        console.print("\u2764 Shipwreck Log \u2014 reviewing the captain's log...")

    snapshot_dir = _DEFAULT_SNAPSHOT_DIR

    # Load snapshots
    if before is None:
        # Find the two most recent snapshots
        snapshots = sorted(snapshot_dir.glob("*.json")) if snapshot_dir.exists() else []
        if len(snapshots) < 2:
            console.print(
                "[red]Error:[/red] Need at least 2 snapshots. "
                "Provide --before and --after or run hunt --snapshot twice."
            )
            raise typer.Exit(1)
        before_path = snapshots[-2]
        after_path = snapshots[-1] if after is None else after
    else:
        before_path = before
        if after is None:
            latest = find_latest_snapshot(snapshot_dir)
            if latest is None:
                console.print("[red]Error:[/red] No latest snapshot found. Provide --after.")
                raise typer.Exit(1)
            after_path = latest
        else:
            after_path = after

    try:
        prev_graph = load_snapshot(before_path)
        curr_graph = load_snapshot(after_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    diff = diff_snapshots(prev_graph, curr_graph)

    if format == "json":
        import json as _json

        output_str = _json.dumps(diff, indent=2, default=str)
        if output:
            output.write_text(output_str)
            console.print(f"[green]\u2713[/green] Diff saved to [cyan]{output}[/cyan]")
        else:
            typer.echo(output_str)
    else:
        # Table/text display
        changes = diff["changes"]

        if changes["added_images"]:
            console.print(f"\n[green]+[/green] Added images: {', '.join(changes['added_images'])}")
        if changes["removed_images"]:
            console.print(f"\n[red]-[/red] Removed images: {', '.join(changes['removed_images'])}")

        if changes["version_changes"]:
            console.print("\n[bold]Version changes:[/bold]")
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Image")
            table.add_column("Previous Tags")
            table.add_column("Current Tags")
            table.add_column("Consumers Affected")
            for vc in changes["version_changes"]:
                table.add_row(
                    vc["image"],
                    ", ".join(vc.get("previous_tags", [])),
                    ", ".join(vc.get("current_tags", [])),
                    ", ".join(vc.get("consumers_affected", [])) or "\u2014",
                )
            console.print(table)

        if changes["staleness_changes"]:
            console.print("\n[bold]Staleness changes:[/bold]")
            for sc in changes["staleness_changes"]:
                console.print(f"  {sc['image']}: {sc['previous']} \u2192 {sc['current']}")

        if changes.get("metadata_changes"):
            console.print(f"\n[dim]{len(changes['metadata_changes'])} metadata field(s) changed.[/dim]")

        edge_ch = changes.get("edge_changes", {})
        added_edges = edge_ch.get("added", [])
        removed_edges = edge_ch.get("removed", [])
        if added_edges or removed_edges:
            console.print(f"\n[dim]Edges: +{len(added_edges)} -{len(removed_edges)}[/dim]")

        if not any(
            [
                changes["added_images"],
                changes["removed_images"],
                changes["version_changes"],
                changes["staleness_changes"],
            ]
        ):
            console.print("\n[green]No changes detected.[/green]")

        console.print(f"\n[dim]Comparing {diff['previous']} \u2192 {diff['current']}[/dim]")


# --------------------------------------------------------------------------- #
# lookout — check registries for staleness
# --------------------------------------------------------------------------- #


@app.command()
def lookout(
    config: ConfigOption = _DEFAULT_CONFIG,
    snapshot_path: Annotated[Path | None, typer.Option("--snapshot", "-s", help="Existing snapshot to enrich.")] = None,
    registry: Annotated[str | None, typer.Option("--registry", help="Only check this registry.")] = None,
    include_external: Annotated[bool, typer.Option("--include-external", help="Also check external registries.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip approval prompts (CI mode).")] = False,
    output_dir: OutputOption = _DEFAULT_OUTPUT_DIR,
) -> None:
    """Scan the horizon. Check registries for staleness and updates."""
    from shipwreck.registry.client import RegistryClient
    from shipwreck.registry.policy import should_query_registry
    from shipwreck.registry.staleness import compute_staleness
    from shipwreck.registry.version import VersionSchemeEngine

    console.print("[bold blue]🏴‍☠️ Shipwreck Lookout[/bold blue] — scanning the horizon...")

    cfg = _load_config_or_exit(config)
    version_engine = VersionSchemeEngine(cfg.version_schemes or None)

    # Load graph from snapshot or latest scan
    graph = None
    if snapshot_path:
        from shipwreck.output.snapshot import load_snapshot
        graph = load_snapshot(snapshot_path)
    else:
        graph = _load_latest_graph(output_dir)

    if graph is None:
        console.print("[red]Error:[/red] No graph available. Run 'hunt' first or provide --snapshot.")
        raise typer.Exit(1)

    # Check each node against its registry
    stale_count = 0
    checked_count = 0

    for node_id, node in graph.nodes.items():
        # Determine registry for this node
        first_segment = node_id.split("/")[0] if "/" in node_id else ""
        node_registry = (
            first_segment
            if first_segment and ("." in first_segment or ":" in first_segment)
            else "docker.io"
        )

        # Filter by --registry if specified
        if registry and node_registry != registry:
            continue

        # Check policy
        if not include_external:
            allowed = should_query_registry(
                node_registry, cfg.registries, cfg.registry_policy, non_interactive=yes
            )
            if not allowed:
                continue

        # Query registry
        try:
            with RegistryClient(node_registry) as client:
                # Determine image name for registry query
                image_name = node.canonical.split("/", 1)[1] if "/" in node.canonical else node.canonical
                tags = client.list_tags(image_name)

                if not tags:
                    node.staleness = "unknown"
                    continue

                # Check each referenced tag
                for tag in node.tags_referenced:
                    staleness = compute_staleness(
                        referenced_tag=tag,
                        available_tags=tags,
                        image_name=node.canonical,
                        version_engine=version_engine,
                    )
                    # Use worst staleness
                    if node.staleness is None or _staleness_rank(staleness) > _staleness_rank(node.staleness):
                        node.staleness = staleness

                # Set latest available
                latest = version_engine.latest(tags, node.canonical)
                if latest:
                    node.latest_available = latest

                checked_count += 1
                if node.staleness in ("behind", "major_behind"):
                    stale_count += 1

        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] Registry query failed for {node_id}: {exc}")
            node.staleness = "unknown"

    # Update summary
    graph.summary.stale_images = stale_count

    # Display results as a Rich table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Image", style="white")
    table.add_column("Current Tags", style="dim")
    table.add_column("Latest", style="green")
    table.add_column("Staleness", justify="center")
    table.add_column("Scheme", style="dim")

    for node_id, node in sorted(graph.nodes.items()):
        if node.staleness is None:
            continue
        tags_str = ", ".join(node.tags_referenced[:3])
        staleness_style = {
            "current": "[green]current[/green]",
            "behind": "[yellow]behind[/yellow]",
            "major_behind": "[red]major behind[/red]",
            "unknown": "[dim]unknown[/dim]",
        }.get(node.staleness, node.staleness)

        table.add_row(
            node_id,
            tags_str,
            node.latest_available or "—",
            staleness_style,
            node.version_scheme or "—",
        )

    console.print(table)
    console.print(f"\n[green]✓[/green] Checked {checked_count} images, {stale_count} stale.")

    # Save enriched graph
    _save_latest_graph(graph, output_dir)


# --------------------------------------------------------------------------- #
# plunder — GitLab group auto-discovery
# --------------------------------------------------------------------------- #


@app.command()
def plunder(
    url: Annotated[str, typer.Option("--url", help="GitLab instance URL.")] = "",
    group: Annotated[str, typer.Option("--group", help="Group path.")] = "",
    token_env: Annotated[str, typer.Option("--token-env", help="Env var holding access token.")] = "GITLAB_TOKEN",
    include_subgroups: Annotated[bool, typer.Option("--include-subgroups", help="Include nested subgroups.")] = False,
    include_pattern: Annotated[str | None, typer.Option("--include-pattern", help="Regex include filter.")] = None,
    exclude_pattern: Annotated[str | None, typer.Option("--exclude-pattern", help="Regex exclude filter.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Just list discovered repos.")] = False,
    append_config: Annotated[Path | None, typer.Option("--append-config", help="Append discovered repos to config.")] = None,
) -> None:
    """Raid the GitLab seas. Auto-discover repos from a GitLab group."""
    import os

    import yaml

    from shipwreck.discovery.gitlab import discover_repos

    console.print("[bold blue]Shipwreck Plunder[/bold blue] — raiding the GitLab seas...")

    if not url or not group:
        console.print("[red]Error:[/red] --url and --group are required.")
        raise typer.Exit(1)

    token = os.environ.get(token_env)
    if not token:
        console.print(f"[red]Error:[/red] Environment variable {token_env} not set.")
        raise typer.Exit(1)

    try:
        repos = discover_repos(
            url=url,
            group=group,
            auth_token=token,
            include_subgroups=include_subgroups,
            include_pattern=include_pattern,
            exclude_pattern=exclude_pattern,
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red] Discovery failed: {exc}")
        raise typer.Exit(1)

    console.print(f"\n[green]✓[/green] Discovered [bold]{len(repos)}[/bold] repositories.")

    if dry_run or not append_config:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Name")
        table.add_column("URL")
        table.add_column("Branch")
        for r in repos:
            table.add_row(r.name or "—", r.url or "—", r.ref)
        console.print(table)
        if dry_run:
            return

    if append_config:
        # Append to config file
        existing: dict = {}
        if append_config.exists():
            existing = yaml.safe_load(append_config.read_text()) or {}

        existing_repos = existing.get("repositories", [])
        existing_urls = {r.get("url") for r in existing_repos if r.get("url")}

        new_repos = []
        for r in repos:
            if r.url not in existing_urls:
                entry = {"url": r.url, "name": r.name, "ref": r.ref}
                new_repos.append(entry)

        existing_repos.extend(new_repos)
        existing["repositories"] = existing_repos

        append_config.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=False))
        console.print(f"[green]✓[/green] Added {len(new_repos)} new repos to [cyan]{append_config}[/cyan]")


# --------------------------------------------------------------------------- #
# sail — combined hunt + lookout + map pipeline
# --------------------------------------------------------------------------- #


@app.command()
def sail(
    config: ConfigOption = _DEFAULT_CONFIG,
    output_dir: OutputOption = _DEFAULT_OUTPUT_DIR,
    snapshot: Annotated[bool, typer.Option("--snapshot", help="Save snapshot after run.")] = False,
    diff_from_latest: Annotated[bool, typer.Option("--diff-from-latest", help="Auto-diff against most recent snapshot.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Non-interactive mode.")] = False,
) -> None:
    """Full speed ahead! Combined hunt + lookout + map pipeline."""
    console.print("[bold blue]Shipwreck Sail[/bold blue] — full speed ahead!")

    cfg = _load_config_or_exit(config)

    # Phase 1: Hunt
    from shipwreck.scanner import scan

    console.print("\n[bold]Phase 1: Hunt[/bold]")
    graph = scan(config=cfg, cache_dir=_DEFAULT_CACHE_DIR)
    console.print(
        f"[green]✓[/green] Discovered {graph.summary.total_images} images, {len(graph.edges)} relationships."
    )

    # Phase 2: Lookout (best-effort, don't fail if registries unreachable)
    console.print("\n[bold]Phase 2: Lookout[/bold]")
    try:
        from shipwreck.registry.client import RegistryClient
        from shipwreck.registry.policy import should_query_registry
        from shipwreck.registry.staleness import compute_staleness
        from shipwreck.registry.version import VersionSchemeEngine

        version_engine = VersionSchemeEngine(cfg.version_schemes or None)
        stale_count = 0

        for node_id, node in graph.nodes.items():
            first_segment = node_id.split("/")[0] if "/" in node_id else ""
            node_registry = (
                first_segment
                if first_segment and ("." in first_segment or ":" in first_segment)
                else "docker.io"
            )

            allowed = should_query_registry(node_registry, cfg.registries, cfg.registry_policy, non_interactive=yes)
            if not allowed:
                continue

            try:
                with RegistryClient(node_registry) as client:
                    image_name = node.canonical.split("/", 1)[1] if "/" in node.canonical else node.canonical
                    tags = client.list_tags(image_name)
                    if tags:
                        for tag in node.tags_referenced:
                            staleness = compute_staleness(tag, tags, node.canonical, version_engine)
                            if node.staleness is None or _staleness_rank(staleness) > _staleness_rank(node.staleness):
                                node.staleness = staleness
                        latest = version_engine.latest(tags, node.canonical)
                        if latest:
                            node.latest_available = latest
                        if node.staleness in ("behind", "major_behind"):
                            stale_count += 1
            except Exception:
                node.staleness = "unknown"

        graph.summary.stale_images = stale_count
        console.print(f"[green]✓[/green] {stale_count} stale images found.")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Lookout phase failed: {exc}")

    # Phase 3: Map
    console.print("\n[bold]Phase 3: Map[/bold]")
    from shipwreck.output.html import export_html
    from shipwreck.output.json_export import export_json
    from shipwreck.output.mermaid import export_mermaid

    output_dir.mkdir(parents=True, exist_ok=True)
    export_json(graph, output_path=output_dir / "shipwreck.json")
    export_mermaid(graph, output_path=output_dir / "shipwreck.mermaid")
    export_html(graph, output_path=output_dir / "shipwreck.html")
    console.print(f"[green]✓[/green] Reports saved to [cyan]{output_dir}[/cyan]")

    # Snapshot + diff
    if snapshot or diff_from_latest:
        from shipwreck.output.snapshot import diff_snapshots, find_latest_snapshot, load_snapshot, save_snapshot

        prev_snapshot = None
        if diff_from_latest:
            prev_path = find_latest_snapshot(_DEFAULT_SNAPSHOT_DIR)
            if prev_path:
                try:
                    prev_snapshot = load_snapshot(prev_path)
                except Exception:
                    pass

        if snapshot:
            snap_path = save_snapshot(graph, _DEFAULT_SNAPSHOT_DIR)
            console.print(f"[green]✓[/green] Snapshot -> [cyan]{snap_path}[/cyan]")

        if prev_snapshot and diff_from_latest:
            diff = diff_snapshots(prev_snapshot, graph)
            changes = diff["changes"]
            added = len(changes.get("added_images", []))
            removed = len(changes.get("removed_images", []))
            version_ch = len(changes.get("version_changes", []))
            console.print(f"\n[bold]Diff:[/bold] +{added} images, -{removed} images, {version_ch} version changes")

    _save_latest_graph(graph, output_dir)
    console.print("\n[green]Sail complete![/green]")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_config_or_exit(config_path: Path):  # type: ignore[return]
    """Load and validate config, exiting on error."""
    from shipwreck.config import load_config

    try:
        return load_config(config_path)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Config file not found: {config_path}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] Invalid config: {exc}")
        raise typer.Exit(1)


def _load_engine_or_exit(snapshot_path: Path | None, shipwreck_dir: Path):  # type: ignore[return]
    """Load the query engine, exiting on error."""
    from shipwreck.query.engine import load_query_engine

    try:
        return load_query_engine(snapshot_path, shipwreck_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


def _parse_formats(format_str: str) -> list[str]:
    """Parse the --format option into a list of formats."""
    if format_str == "all":
        return ["html", "mermaid", "json"]
    return [f.strip() for f in format_str.split(",")]


def _display_nodes(nodes: list, format_: str) -> None:
    """Display nodes in the requested format."""
    if format_ == "json":
        data = [
            {
                "id": n.id,
                "classification": n.classification,
                "criticality": n.criticality,
                "tags": n.tags_referenced,
                "staleness": n.staleness,
            }
            for n in nodes
        ]
        typer.echo(json.dumps(data, indent=2))
    elif format_ == "text":
        for node in nodes:
            console.print(f"  {node.id} ({', '.join(node.tags_referenced[:3])})")
    else:
        # Table format (default)
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Image", style="white")
        table.add_column("Tags", style="dim")
        table.add_column("Class", style="yellow")
        table.add_column("Criticality", justify="right", style="red")
        table.add_column("Staleness", style="green")

        for node in nodes:
            tags = ", ".join(node.tags_referenced[:3])
            if len(node.tags_referenced) > 3:
                tags += f" +{len(node.tags_referenced) - 3}"
            table.add_row(
                node.id,
                tags,
                node.classification or "—",
                f"{node.criticality:.1f}",
                node.staleness or "—",
            )
        console.print(table)


def _save_latest_graph(graph, output_dir: Path) -> None:
    """Persist the latest graph to a temp JSON for map to pick up."""
    from shipwreck.output.json_export import export_json

    output_dir.mkdir(parents=True, exist_ok=True)
    export_json(graph, output_path=output_dir / ".latest_graph.json")


def _load_latest_graph(output_dir: Path):
    """Load the most recently scanned graph, if any."""
    latest = output_dir / ".latest_graph.json"
    if not latest.exists():
        return None
    from shipwreck.output.snapshot import load_snapshot

    try:
        return load_snapshot(latest)
    except Exception:
        return None


def _staleness_rank(s: str | None) -> int:
    """Rank staleness for worst-wins comparison."""
    return {"current": 0, "behind": 1, "major_behind": 2, "unknown": -1}.get(s or "", -1)


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
