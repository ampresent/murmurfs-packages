"""High-level file operations for MurmurFS.

These are the core operations that the CLI and FUSE layer both use.
"""

from __future__ import annotations

from pathlib import Path

from murmurfs.intent import IntentStack
from murmurfs.meta import Manifest


def init_project(path: str, description: str = "") -> Manifest:
    """Initialize a new MurmurFS project.

    Creates .murmurfs/manifest.yaml in the given directory.

    Args:
        path: Project root directory.
        description: Human-readable project description.

    Returns:
        The created Manifest.
    """
    project_root = Path(path).resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    return Manifest.load_or_create(str(project_root), description)


def write_intent(
    manifest: Manifest,
    path: str,
    summary: str,
    full: str | None = None,
    author: str = "agent",
    importance: float = 0.5,
    tags: list[str] | None = None,
) -> None:
    """Append an intent layer to a file.

    If the file isn't registered in the manifest, it will be added automatically.

    Args:
        manifest: The project manifest.
        path: Relative file path (e.g., "src/auth.py").
        summary: One-line intent description.
        full: Full intent description (defaults to summary).
        author: Agent or entity creating this layer.
        importance: Weight 0.0-1.0 (1.0 = critical, 0.0 = forgettable).
        tags: Optional labels for categorization/search.
    """
    entry = manifest.ensure_file(path)
    entry.stack.append(summary, full, author, importance=importance, tags=tags)
    entry.squashed = False  # adding a layer un-squashes
    entry.synced = False    # adding a layer un-syncs
    manifest.save()


def read_intent(manifest: Manifest, path: str) -> list[str]:
    """Read the intent stack for a file.

    Args:
        manifest: The project manifest.
        path: Relative file path.

    Returns:
        List of summary lines (e.g., ["v1: 实现认证", "v2: 砍掉session"]).
        Returns empty list if file not found.
    """
    entry = manifest.get_file(path)
    if entry is None:
        return []
    return entry.stack.read()


def list_intents(manifest: Manifest) -> list[dict]:
    """List all files and their intent layer counts.

    Returns:
        List of dicts with keys: path, description, layers, squashed, synced.
    """
    results = []
    for entry in manifest.list_files():
        results.append({
            "path": entry.path,
            "description": entry.description,
            "layers": entry.stack.count,
            "squashed": entry.squashed,
            "synced": entry.synced,
        })
    return sorted(results, key=lambda x: x["path"])


def get_file_status(manifest: Manifest, path: str) -> dict | None:
    """Get detailed status for a single file.

    Returns:
        Dict with file metadata, or None if not found.
    """
    entry = manifest.get_file(path)
    if entry is None:
        return None

    return {
        "path": entry.path,
        "description": entry.description,
        "layers": entry.stack.count,
        "squashed": entry.squashed,
        "synced": entry.synced,
        "sync_target": entry.sync_target,
        "last_sync": entry.last_sync,
        "summaries": entry.stack.read(),
    }


def get_project_status(manifest: Manifest) -> dict:
    """Get project-wide status summary.

    Returns:
        Dict with keys: total_files, synced, unsynced, squashed, total_layers,
        top_by_layers, conflicts.
    """
    files = manifest.list_files()
    total = len(files)
    synced = sum(1 for f in files if f.synced)
    squashed = sum(1 for f in files if f.squashed)
    total_layers = sum(f.stack.count for f in files)

    # Files sorted by layer count (descending)
    by_layers = sorted(files, key=lambda f: f.stack.count, reverse=True)
    top_by_layers = [
        {"path": f.path, "layers": f.stack.count, "squashed": f.squashed}
        for f in by_layers
        if f.stack.count > 0
    ]

    # Detect conflicts: files with layers from different authors
    conflicts = []
    for f in files:
        if f.stack.count >= 2:
            authors = {layer.author for layer in f.stack.layers}
            if len(authors) > 1:
                conflicts.append({
                    "path": f.path,
                    "authors": sorted(authors),
                    "layers": f.stack.count,
                })

    return {
        "project_name": manifest.project_name,
        "project_description": manifest.project_description,
        "total_files": total,
        "synced": synced,
        "unsynced": total - synced,
        "squashed": squashed,
        "total_layers": total_layers,
        "top_by_layers": top_by_layers,
        "conflicts": conflicts,
    }


def search_memories(manifest: Manifest, query: str) -> list[dict]:
    """Search all memory layers across all files by keyword relevance.

    Args:
        manifest: The project manifest.
        query: Search query string.

    Returns:
        List of dicts with keys: path, layer_id, summary, score, importance, tags.
    """
    results = []
    for entry in manifest.list_files():
        matches = entry.stack.search(query)
        for layer, score in matches:
            results.append({
                "path": entry.path,
                "layer_id": layer.id,
                "summary": layer.summary,
                "full": layer.full,
                "score": score,
                "importance": layer.importance,
                "tags": layer.tags,
                "timestamp": layer.timestamp,
                "last_accessed": layer.last_accessed,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    manifest.save()  # save updated last_accessed timestamps
    return results


def forget_memories(
    manifest: Manifest,
    threshold: float = 0.1,
    decay: bool = False,
    decay_factor: float = 0.9,
) -> dict:
    """Remove or decay low-importance memory layers.

    Args:
        manifest: The project manifest.
        threshold: Remove layers with importance below this value.
        decay: If True, apply decay to all layers before thresholding.
        decay_factor: Factor to multiply importance by (0-1).

    Returns:
        Dict with keys: removed_count, removed_layers, remaining_count.
    """
    total_removed = []
    total_remaining = 0

    for entry in manifest.list_files():
        if decay:
            entry.stack.decay_all(decay_factor)
        removed = entry.stack.forget(threshold)
        total_removed.extend([
            {"path": entry.path, "layer_id": l.id, "summary": l.summary, "importance": l.importance}
            for l in removed
        ])
        total_remaining += entry.stack.count

    manifest.save()
    return {
        "removed_count": len(total_removed),
        "removed_layers": total_removed,
        "remaining_count": total_remaining,
    }
