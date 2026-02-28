"""Shipwreck CLI — all commands, pirate-themed.

🏴‍☠️ Mapping the buried treasure in your container stack.
"""

from __future__ import annotations

import json
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


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
