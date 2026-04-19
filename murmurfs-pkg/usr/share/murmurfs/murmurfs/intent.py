"""Intent layer data model.

Each file in MurmurFS is an intent stack — a list of IntentLayers,
each describing what the agent plans to do, not what the file contains.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Iterator


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class IntentLayer:
    """A single layer of intent in a file's stack."""

    id: str
    timestamp: str
    summary: str
    full: str
    author: str = "agent"
    importance: float = 0.5
    last_accessed: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "full": self.full,
            "author": self.author,
            "importance": self.importance,
        }
        if self.last_accessed:
            d["last_accessed"] = self.last_accessed
        if self.tags:
            d["tags"] = self.tags
        return d

    @classmethod
    def from_dict(cls, d: dict) -> IntentLayer:
        return cls(
            id=d["id"],
            timestamp=d["timestamp"],
            summary=d["summary"],
            full=d["full"],
            author=d.get("author", "agent"),
            importance=d.get("importance", 0.5),
            last_accessed=d.get("last_accessed"),
            tags=d.get("tags", []),
        )

    def touch(self) -> None:
        """Update last_accessed to now."""
        self.last_accessed = _now_iso()

    def decay(self, factor: float = 0.9) -> None:
        """Reduce importance by a decay factor (simulates forgetting)."""
        self.importance = round(max(0.0, self.importance * factor), 3)


class IntentStack:
    """Manages a stack of intent layers for a single file.

    Usage:
        stack = IntentStack()
        stack.append("实现认证模块", "支持JWT和session两种方式")
        stack.append("砍掉session", "下游只支持bearer token")
        for line in stack.read():
            print(line)
    """

    def __init__(self, layers: list[IntentLayer] | None = None):
        self._layers: list[IntentLayer] = list(layers) if layers else []

    @property
    def layers(self) -> list[IntentLayer]:
        return list(self._layers)

    @property
    def count(self) -> int:
        return len(self._layers)

    def next_id(self) -> str:
        return f"v{self.count + 1}"

    def append(
        self,
        summary: str,
        full: str | None = None,
        author: str = "agent",
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> IntentLayer:
        """Append a new intent layer to the stack.

        Args:
            summary: One-line description (shown to agent on read).
            full: Complete intent description (used during squash/sync).
                  Defaults to summary if not provided.
            author: Agent or entity that created this layer.
            importance: Weight 0.0-1.0 (1.0 = critical, 0.0 = forgettable).
            tags: Optional labels for categorization/search.
        """
        layer = IntentLayer(
            id=self.next_id(),
            timestamp=_now_iso(),
            summary=summary,
            full=full if full is not None else summary,
            author=author,
            importance=importance,
            tags=tags or [],
        )
        self._layers.append(layer)
        return layer

    def read(self) -> list[str]:
        """Return list of summary lines, one per layer."""
        return [f"{layer.id}: {layer.summary}" for layer in self._layers]

    def get(self, layer_id: str) -> IntentLayer | None:
        """Get a layer by its id."""
        for layer in self._layers:
            if layer.id == layer_id:
                return layer
        return None

    def search(self, query: str) -> list[tuple[IntentLayer, float]]:
        """Search layers by keyword relevance.

        Returns list of (layer, score) sorted by score descending.
        Score is based on keyword overlap + importance weighting.
        """
        query_lower = query.lower()
        query_terms = set(query_lower.split())
        results = []

        for layer in self._layers:
            text = f"{layer.summary} {layer.full} {' '.join(layer.tags)}".lower()
            text_terms = set(text.split())

            # Keyword overlap (Jaccard-like)
            overlap = query_terms & text_terms
            if not overlap:
                # Fuzzy: check substring containment
                if any(qt in text for qt in query_terms):
                    overlap = query_terms  # partial credit

            if overlap:
                keyword_score = len(overlap) / max(len(query_terms), 1)
                # Boost by importance
                score = keyword_score * (0.5 + 0.5 * layer.importance)
                layer.touch()
                results.append((layer, round(score, 3)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def squash(
        self,
        new_summary: str,
        new_full: str,
        author: str = "agent",
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> IntentLayer:
        """Replace all layers with a single squashed layer.

        Args:
            new_summary: One-line compressed summary.
            new_full: Full compressed intent description.
            author: Author of the squashed layer.
            importance: Inherited importance (max of squashed layers).
            tags: Merged tags from all layers.
        """
        # Preserve highest importance and all tags
        if self._layers:
            max_importance = max(l.importance for l in self._layers)
            all_tags = list({t for l in self._layers for t in l.tags})
        else:
            max_importance = importance
            all_tags = tags or []

        layer = IntentLayer(
            id="v1",
            timestamp=_now_iso(),
            summary=new_summary,
            full=new_full,
            author=author,
            importance=max(max_importance, importance),
            tags=all_tags,
        )
        self._layers = [layer]
        return layer

    def forget(self, threshold: float = 0.1) -> list[IntentLayer]:
        """Remove layers below importance threshold.

        Returns list of removed layers.
        """
        removed = [l for l in self._layers if l.importance < threshold]
        self._layers = [l for l in self._layers if l.importance >= threshold]
        # Re-number remaining layers
        for i, layer in enumerate(self._layers):
            layer.id = f"v{i + 1}"
        return removed

    def decay_all(self, factor: float = 0.9) -> None:
        """Apply decay to all layers (simulates time-based forgetting)."""
        for layer in self._layers:
            layer.decay(factor)

    def to_list(self) -> list[dict]:
        """Serialize layers to a list of dicts."""
        return [layer.to_dict() for layer in self._layers]

    @classmethod
    def from_list(cls, data: list[dict]) -> IntentStack:
        """Deserialize from a list of dicts."""
        layers = [IntentLayer.from_dict(d) for d in data]
        return cls(layers=layers)

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[IntentLayer]:
        return iter(self._layers)

    def __repr__(self) -> str:
        return f"IntentStack({self.count} layers)"
