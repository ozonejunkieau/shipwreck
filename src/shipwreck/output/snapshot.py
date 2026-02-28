"""Snapshot save/load for Shipwreck."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from shipwreck.models import Graph
from shipwreck.output.json_export import export_json


def save_snapshot(graph: Graph, snapshot_dir: Path) -> Path:
    """Save a graph snapshot with a timestamped filename.

    Args:
        graph: The graph to save.
        snapshot_dir: Directory to save snapshots in (.shipwreck/snapshots/).

    Returns:
        Path to the saved snapshot file.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = snapshot_dir / f"{ts}.json"
    export_json(graph, output_path=path)
    return path


def load_snapshot(path: Path) -> Graph:
    """Load a graph from a snapshot JSON file.

    Args:
        path: Path to the snapshot JSON file.

    Returns:
        Reconstructed Graph instance.

    Raises:
        FileNotFoundError: If the snapshot file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Snapshot not found: {path}")

    data = json.loads(path.read_text())

    # Convert nodes list back to dict keyed by id
    nodes_list = data.pop("nodes", [])
    data["nodes"] = {n["id"]: n for n in nodes_list}

    # Rename the JSON "$schema" key to the Pydantic field name "schema_url"
    data["schema_url"] = data.pop("$schema", "https://shipwreck.dev/schema/v1.json")

    return Graph.model_validate(data)


def find_latest_snapshot(snapshot_dir: Path) -> Path | None:
    """Find the most recent snapshot in a directory.

    Snapshots are named with ISO-8601 timestamps so lexicographic sort gives
    chronological order.

    Args:
        snapshot_dir: Directory containing snapshot files.

    Returns:
        Path to the latest snapshot, or None if none exist.
    """
    if not snapshot_dir.exists():
        return None
    snapshots = sorted(snapshot_dir.glob("*.json"))
    return snapshots[-1] if snapshots else None


def diff_snapshots(previous: Graph, current: Graph) -> dict:
    """Compute a diff between two graph snapshots.

    Returns a dict matching the §4.4 snapshot diff format.

    Args:
        previous: The older graph snapshot.
        current: The newer graph snapshot.

    Returns:
        Dict with keys: previous, current, changes.  The ``changes`` value
        contains ``added_images``, ``removed_images``, ``version_changes``, and
        ``staleness_changes``.
    """
    prev_nodes = set(previous.nodes.keys())
    curr_nodes = set(current.nodes.keys())

    added = list(curr_nodes - prev_nodes)
    removed = list(prev_nodes - curr_nodes)

    version_changes: list[dict] = []
    staleness_changes: list[dict] = []

    for node_id in prev_nodes & curr_nodes:
        prev_node = previous.nodes[node_id]
        curr_node = current.nodes[node_id]

        prev_tags = set(prev_node.tags_referenced)
        curr_tags = set(curr_node.tags_referenced)
        if prev_tags != curr_tags:
            version_changes.append(
                {
                    "image": node_id,
                    "previous_tags": list(prev_tags),
                    "current_tags": list(curr_tags),
                }
            )

        if prev_node.staleness != curr_node.staleness:
            staleness_changes.append(
                {
                    "image": node_id,
                    "previous": prev_node.staleness,
                    "current": curr_node.staleness,
                }
            )

    return {
        "previous": previous.generated_at,
        "current": current.generated_at,
        "changes": {
            "added_images": added,
            "removed_images": removed,
            "version_changes": version_changes,
            "staleness_changes": staleness_changes,
        },
    }
