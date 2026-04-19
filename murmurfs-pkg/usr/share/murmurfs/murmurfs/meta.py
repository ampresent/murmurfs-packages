"""Manifest manager — sidecar YAML storage for MurmurFS metadata.

Each project has a .murmurfs/manifest.yaml that tracks:
- Project metadata
- File entries (intent stacks, sync status)
- Directory descriptions and intended files
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murmurfs.intent import IntentStack

MURMUR_DIR = ".murmurfs"
MANIFEST_FILE = "manifest.yaml"


@dataclass
class FileEntry:
    """Metadata for a single file in the manifest."""

    path: str
    description: str = ""
    stack: IntentStack = field(default_factory=IntentStack)
    squashed: bool = False
    synced: bool = False
    sync_target: str | None = None
    last_sync: str | None = None
    sync_range: dict | None = None
    synced_layers: list[str] = field(default_factory=list)
    _branches: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "description": self.description,
            "layers": self.stack.to_list(),
            "squashed": self.squashed,
            "synced": self.synced,
        }
        if self.sync_target:
            d["sync_target"] = self.sync_target
        if self.last_sync:
            d["last_sync"] = self.last_sync
        if self.sync_range:
            d["sync_range"] = self.sync_range
        if self.synced_layers:
            d["synced_layers"] = self.synced_layers
        if self._branches:
            d["branches"] = {
                name: branch.to_dict() for name, branch in self._branches.items()
            }
        return d

    @classmethod
    def from_dict(cls, path: str, d: dict) -> FileEntry:
        stack = IntentStack.from_list(d.get("layers", []))
        entry = cls(
            path=path,
            description=d.get("description", ""),
            stack=stack,
            squashed=d.get("squashed", False),
            synced=d.get("synced", False),
            sync_target=d.get("sync_target"),
            last_sync=d.get("last_sync"),
            sync_range=d.get("sync_range"),
            synced_layers=d.get("synced_layers", []),
        )
        branches_data = d.get("branches", {})
        if branches_data:
            from murmurfs.branch import Branch
            for name, branch_dict in branches_data.items():
                entry._branches[name] = Branch.from_dict(name, branch_dict)
        return entry


@dataclass
class DirectoryEntry:
    """Metadata for a directory."""

    path: str
    description: str = ""
    intended: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "intended": self.intended,
        }

    @classmethod
    def from_dict(cls, path: str, d: dict) -> DirectoryEntry:
        return cls(
            path=path,
            description=d.get("description", ""),
            intended=d.get("intended", []),
        )


class Manifest:
    """Manages .murmurfs/manifest.yaml for a MurmurFS project.

    Usage:
        manifest = Manifest.load_or_create("/path/to/project", "My project")
        manifest.add_file("src/auth.py", "用户认证模块")
        manifest.get_file("src/auth.py").stack.append("实现JWT认证")
        manifest.save()
    """

    def __init__(self, project_root: str, manifest_path: str):
        self.project_root = Path(project_root).resolve()
        self.manifest_path = Path(manifest_path)
        self.version: int = 1
        self.project_name: str = ""
        self.project_description: str = ""
        self._files: dict[str, FileEntry] = {}
        self._directories: dict[str, DirectoryEntry] = {}

    @classmethod
    def load_or_create(cls, project_root: str, description: str = "") -> Manifest:
        """Load existing manifest or create a new one.

        Args:
            project_root: Path to the project root directory.
            description: Project description (used only for new manifests).
        """
        project_root = str(Path(project_root).resolve())
        murmur_dir = os.path.join(project_root, MURMUR_DIR)
        manifest_path = os.path.join(murmur_dir, MANIFEST_FILE)

        if os.path.exists(manifest_path):
            return cls._load(project_root, manifest_path)

        # Create new
        manifest = cls(project_root, manifest_path)
        manifest.project_name = Path(project_root).name
        manifest.project_description = description
        os.makedirs(murmur_dir, exist_ok=True)
        manifest.save()
        return manifest

    @classmethod
    def _load(cls, project_root: str, manifest_path: str) -> Manifest:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        manifest = cls(project_root, manifest_path)
        manifest.version = data.get("version", 1)

        project = data.get("project", {})
        manifest.project_name = project.get("name", "")
        manifest.project_description = project.get("description", "")

        for path, file_data in data.get("files", {}).items():
            manifest._files[path] = FileEntry.from_dict(path, file_data)

        for path, dir_data in data.get("directories", {}).items():
            manifest._directories[path] = DirectoryEntry.from_dict(path, dir_data)

        return manifest

    def save(self) -> None:
        """Write manifest to disk."""
        data = {
            "version": self.version,
            "project": {
                "name": self.project_name,
                "description": self.project_description,
            },
            "files": {path: entry.to_dict() for path, entry in self._files.items()},
            "directories": {path: entry.to_dict() for path, entry in self._directories.items()},
        }

        os.makedirs(self.manifest_path.parent, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def add_file(self, path: str, description: str = "") -> FileEntry:
        """Register a file in the manifest."""
        entry = FileEntry(path=path, description=description)
        self._files[path] = entry
        return entry

    def get_file(self, path: str) -> FileEntry | None:
        """Get a file entry by path."""
        return self._files.get(path)

    def remove_file(self, path: str) -> bool:
        """Remove a file entry."""
        if path in self._files:
            del self._files[path]
            return True
        return False

    def list_files(self) -> list[FileEntry]:
        """List all registered file entries."""
        return list(self._files.values())

    def add_directory(self, path: str, description: str = "") -> DirectoryEntry:
        """Register a directory in the manifest."""
        entry = DirectoryEntry(path=path, description=description)
        self._directories[path] = entry
        return entry

    def get_directory(self, path: str) -> DirectoryEntry | None:
        """Get a directory entry by path."""
        return self._directories.get(path)

    def list_directories(self) -> list[DirectoryEntry]:
        """List all registered directory entries."""
        return list(self._directories.values())

    def ensure_file(self, path: str, description: str = "") -> FileEntry:
        """Get existing file entry or create a new one."""
        entry = self.get_file(path)
        if entry is None:
            entry = self.add_file(path, description)
        return entry

    def __repr__(self) -> str:
        return (
            f"Manifest(project={self.project_name!r}, "
            f"files={len(self._files)}, dirs={len(self._directories)})"
        )
