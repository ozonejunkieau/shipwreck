"""Query engine for the 'dig' command."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from shipwreck.models import Graph, GraphNode


class QueryEngine:
    """Query engine for interrogating a graph snapshot.

    Args:
        graph: The graph to query.
    """

    def __init__(self, graph: Graph) -> None:
        self._graph = graph
        # Pre-compute adjacency for fast traversal
        self._outgoing: dict[str, list[str]] = defaultdict(list)
        self._incoming: dict[str, list[str]] = defaultdict(list)
        for edge in graph.edges:
            self._outgoing[edge.source].append(edge.target)
            self._incoming[edge.target].append(edge.source)

    def uses(self, image: str) -> list[GraphNode]:
        """Return all nodes that directly or transitively use/consume the given image.

        "Uses" means the node has a BUILDS_FROM or CONSUMES edge pointing to the image.

        Args:
            image: Partial or full node id to search for.

        Returns:
            List of nodes that use this image, sorted by criticality desc.
        """
        target_ids = self._find_nodes(image)
        result: set[str] = set()
        for target_id in target_ids:
            # BFS upstream (nodes that point TO target_id)
            queue = deque([target_id])
            visited: set[str] = {target_id}
            while queue:
                current = queue.popleft()
                for upstream in self._incoming.get(current, []):
                    if upstream not in visited:
                        visited.add(upstream)
                        result.add(upstream)
                        queue.append(upstream)
        return sorted(
            [self._graph.nodes[n] for n in result if n in self._graph.nodes],
            key=lambda n: n.criticality,
            reverse=True,
        )

    def used_by(self, image: str) -> list[GraphNode]:
        """Return all nodes that this image directly or transitively depends on.

        Args:
            image: Partial or full node id to search for.

        Returns:
            List of dependency nodes.
        """
        source_ids = self._find_nodes(image)
        result: set[str] = set()
        for source_id in source_ids:
            # BFS downstream (nodes that source_id points TO)
            queue = deque([source_id])
            visited: set[str] = {source_id}
            while queue:
                current = queue.popleft()
                for downstream in self._outgoing.get(current, []):
                    if downstream not in visited:
                        visited.add(downstream)
                        result.add(downstream)
                        queue.append(downstream)
        return sorted(
            [self._graph.nodes[n] for n in result if n in self._graph.nodes],
            key=lambda n: n.criticality,
            reverse=True,
        )

    def stale(self) -> list[GraphNode]:
        """Return all nodes with a non-current staleness status.

        Returns:
            List of stale nodes sorted by criticality desc.
        """
        return sorted(
            [n for n in self._graph.nodes.values() if n.staleness not in (None, "current", "unknown")],
            key=lambda n: n.criticality,
            reverse=True,
        )

    def critical(self) -> list[GraphNode]:
        """Return all nodes sorted by criticality score (highest first).

        Returns:
            All nodes sorted descending by criticality.
        """
        return sorted(
            self._graph.nodes.values(),
            key=lambda n: n.criticality,
            reverse=True,
        )

    def by_classification(self, classification: str) -> list[GraphNode]:
        """Return nodes matching a specific classification.

        Args:
            classification: One of 'base', 'intermediate', 'product', 'test'.

        Returns:
            Matching nodes sorted by criticality desc.
        """
        return sorted(
            [n for n in self._graph.nodes.values() if n.classification == classification],
            key=lambda n: n.criticality,
            reverse=True,
        )

    def _find_nodes(self, image: str) -> list[str]:
        """Find node IDs matching a partial or full image string.

        Args:
            image: Image name or partial name.

        Returns:
            List of matching node IDs.
        """
        exact = [nid for nid in self._graph.nodes if nid == image]
        if exact:
            return exact
        # Partial match (substring)
        return [nid for nid in self._graph.nodes if image in nid]


def load_query_engine(snapshot_path: Path | None, shipwreck_dir: Path) -> QueryEngine:
    """Load a query engine from a snapshot or the latest available.

    Args:
        snapshot_path: Explicit snapshot path, or None to use latest.
        shipwreck_dir: The .shipwreck directory.

    Returns:
        A QueryEngine backed by the loaded graph.

    Raises:
        FileNotFoundError: If no snapshot is found.
    """
    from shipwreck.output.snapshot import find_latest_snapshot, load_snapshot

    if snapshot_path is None:
        snapshot_path = find_latest_snapshot(shipwreck_dir / "snapshots")
        if snapshot_path is None:
            raise FileNotFoundError(
                "No snapshot found in .shipwreck/snapshots/. "
                "Run 'shipwreck hunt' followed by 'shipwreck map --snapshot' first."
            )

    graph = load_snapshot(snapshot_path)
    return QueryEngine(graph)
