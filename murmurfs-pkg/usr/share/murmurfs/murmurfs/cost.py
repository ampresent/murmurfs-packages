"""Cost tracking for LLM token consumption in MurmurFS."""

from __future__ import annotations

import datetime
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murmurfs.meta import MURMUR_DIR

COST_FILE = "costs.yaml"

# Approximate cost per 1K tokens (USD) — conservative defaults
COST_PER_1K_PROMPT = 0.005
COST_PER_1K_COMPLETION = 0.015


@dataclass
class CostEntry:
    """A single LLM cost record."""

    operation: str  # "squash" | "sync" | "merge"
    timestamp: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    file_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CostEntry:
        return cls(
            operation=d["operation"],
            timestamp=d["timestamp"],
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            model=d.get("model", ""),
            file_path=d.get("file_path", ""),
        )


@dataclass
class CostTracker:
    """Track and query LLM token consumption."""

    project_root: str = ""
    _entries: list[CostEntry] = field(default_factory=list)
    _cost_path: str = ""

    def __init__(self, project_root: str):
        self.project_root = str(Path(project_root).resolve())
        self._cost_path = os.path.join(self.project_root, MURMUR_DIR, COST_FILE)
        self._entries: list[CostEntry] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._cost_path):
            return
        with open(self._cost_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for item in data.get("entries", []):
            self._entries.append(CostEntry.from_dict(item))

    def record(
        self,
        operation: str,
        file_path: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> CostEntry:
        """Record a single LLM call's token consumption."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entry = CostEntry(
            operation=operation,
            timestamp=now,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=model,
            file_path=file_path,
        )
        self._entries.append(entry)
        self.save()
        return entry

    def get_file_costs(self, file_path: str) -> list[CostEntry]:
        """Get all cost entries for a specific file."""
        return [e for e in self._entries if e.file_path == file_path]

    def get_total(self) -> dict[str, Any]:
        """Get total token consumption statistics."""
        prompt = sum(e.prompt_tokens for e in self._entries)
        completion = sum(e.completion_tokens for e in self._entries)
        total = sum(e.total_tokens for e in self._entries)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "estimated_cost_usd": (
                prompt * COST_PER_1K_PROMPT / 1000
                + completion * COST_PER_1K_COMPLETION / 1000
            ),
            "call_count": len(self._entries),
        }

    def get_by_operation(self) -> dict[str, dict[str, Any]]:
        """Get token consumption grouped by operation type."""
        result: dict[str, dict[str, Any]] = {}
        for op in ("squash", "sync", "merge"):
            op_entries = [e for e in self._entries if e.operation == op]
            if op_entries:
                tokens = sum(e.total_tokens for e in op_entries)
                result[op] = {
                    "total_tokens": tokens,
                    "call_count": len(op_entries),
                }
            else:
                result[op] = {
                    "total_tokens": 0,
                    "call_count": 0,
                }
        return result

    def get_recent(self, hours: int = 24) -> list[CostEntry]:
        """Get cost entries from the last N hours."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        result = []
        for e in self._entries:
            try:
                ts = datetime.datetime.fromisoformat(e.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
                if ts >= cutoff:
                    result.append(e)
            except (ValueError, TypeError):
                continue
        return result

    def get_file_cost_summary(self, file_path: str) -> dict[str, Any]:
        """Get a cost summary for a specific file suitable for manifest storage."""
        entries = self.get_file_costs(file_path)
        return {
            "total_tokens": sum(e.total_tokens for e in entries),
            "squash_count": sum(1 for e in entries if e.operation == "squash"),
            "sync_count": sum(1 for e in entries if e.operation == "sync"),
            "merge_count": sum(1 for e in entries if e.operation == "merge"),
        }

    def save(self) -> None:
        """Persist cost entries to .murmurfs/costs.yaml."""
        os.makedirs(os.path.dirname(self._cost_path), exist_ok=True)
        data = {
            "entries": [e.to_dict() for e in self._entries],
        }
        with open(self._cost_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
