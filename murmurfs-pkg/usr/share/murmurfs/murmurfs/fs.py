"""FUSE filesystem implementation for MurmurFS.

Translates filesystem operations into intent-stack operations,
making the murmur/ directory a virtual view of the agent's plans.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import sys
import time
from pathlib import Path

import fuse
from fuse import FuseOSError

from murmurfs.meta import Manifest

fuse.fuse_python_api = (0, 2)


def _default_dir_attrs() -> dict:
    """Return default directory stat attributes."""
    now = time.time()
    return {
        "st_mode": stat.S_IFDIR | 0o755,
        "st_nlink": 2,
        "st_uid": os.getuid(),
        "st_gid": os.getgid(),
        "st_atime": now,
        "st_mtime": now,
        "st_ctime": now,
        "st_size": 4096,
    }


def _default_file_attrs(size: int = 0, mtime: float | None = None) -> dict:
    """Return default file stat attributes."""
    now = mtime if mtime is not None else time.time()
    return {
        "st_mode": stat.S_IFREG | 0o644,
        "st_nlink": 1,
        "st_uid": os.getuid(),
        "st_gid": os.getgid(),
        "st_atime": now,
        "st_mtime": now,
        "st_ctime": now,
        "st_size": size,
    }


class MurmurFS(fuse.Operations):
    """FUSE filesystem backed by MurmurFS intent stacks.

    All reads/writes on the murmur/ mount are translated to
    intent operations on the manifest.

    Uses file locking (fcntl.flock) to protect manifest reads/writes
    from concurrent access.
    """

    def __init__(self, murmur_dir: str, real_dir: str, manifest: Manifest):
        super().__init__()
        self.murmur_dir = Path(murmur_dir).resolve()
        self.real_dir = Path(real_dir).resolve()
        self.manifest = manifest
        self._fh_counter = 0
        self._lock_path = str(manifest.manifest_path) + ".lock"

    # --- helpers ---

    def _relative_path(self, path: str) -> str:
        """Convert FUSE path to manifest-relative path (strip leading /)."""
        return path.lstrip("/")

    def _intent_content(self, rel_path: str) -> bytes:
        """Build the readable content for an intent file."""
        entry = self.manifest.get_file(rel_path)
        if entry is None or entry.stack.count == 0:
            return b""
        lines = entry.stack.read()
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _real_path(self, rel_path: str) -> Path:
        """Absolute path for the synced real file."""
        return self.real_dir / rel_path

    def _is_intent_file(self, rel_path: str) -> bool:
        return self.manifest.get_file(rel_path) is not None

    def _is_real_file(self, rel_path: str) -> bool:
        return self._real_path(rel_path).is_file()

    def _locked_save(self) -> None:
        """Save manifest with an exclusive file lock to prevent concurrent corruption."""
        try:
            lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self.manifest.save()
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        except OSError as e:
            print(f"Warning: manifest lock failed: {e}", file=sys.stderr)
            # Fall back to saving without lock
            self.manifest.save()

    # --- FUSE operations ---

    def getattr(self, path: str, fh=None) -> dict:
        rel = self._relative_path(path)

        # Root directory
        if rel == "":
            return _default_dir_attrs()

        # Check if it's a directory in the manifest (with or without trailing /)
        dir_entry = self.manifest.get_directory(rel) or self.manifest.get_directory(rel + "/")
        if dir_entry is not None:
            return _default_dir_attrs()

        # Check if any manifest file lives under this path (implicit directory)
        for file_entry in self.manifest.list_files():
            if file_entry.path.startswith(rel + "/"):
                return _default_dir_attrs()

        # Check real filesystem for directories
        real = self._real_path(rel)
        if real.is_dir():
            return _default_dir_attrs()

        # Intent file — synthesize attributes
        file_entry = self.manifest.get_file(rel)
        if file_entry is not None:
            content = self._intent_content(rel)
            mtime = time.time()
            if file_entry.stack.count > 0:
                latest = file_entry.stack.layers[-1]
                try:
                    from datetime import datetime
                    mtime = datetime.fromisoformat(latest.timestamp).timestamp()
                except Exception:
                    pass
            return _default_file_attrs(size=len(content), mtime=mtime)

        # Real file on disk
        if real.is_file():
            st = real.stat()
            return {
                "st_mode": st.st_mode,
                "st_nlink": st.st_nlink,
                "st_uid": st.st_uid,
                "st_gid": st.st_gid,
                "st_atime": st.st_atime,
                "st_mtime": st.st_mtime,
                "st_ctime": st.st_ctime,
                "st_size": st.st_size,
            }

        raise FuseOSError(errno.ENOENT)

    def readdir(self, path: str, fh=None) -> list[str]:
        rel = self._relative_path(path)

        names: set[str] = set()

        # From manifest directories/files
        for file_entry in self.manifest.list_files():
            fpath = file_entry.path
            if rel == "":
                # Root: top-level entry
                parts = fpath.split("/")
                names.add(parts[0])
            elif fpath.startswith(rel + "/"):
                remainder = fpath[len(rel) + 1:]
                parts = remainder.split("/")
                names.add(parts[0])

        # From manifest registered directories
        for dir_entry in self.manifest.list_directories():
            dpath = dir_entry.path.rstrip("/")
            if rel == "":
                parts = dpath.split("/")
                names.add(parts[0])
            elif dpath.startswith(rel + "/"):
                remainder = dpath[len(rel) + 1:]
                parts = remainder.split("/")
                names.add(parts[0])

        # Intended files from directory entries
        for dir_entry in self.manifest.list_directories():
            dpath = dir_entry.path.rstrip("/")
            if dpath == rel:
                for intended in dir_entry.intended:
                    names.add(intended)

        # Real files from disk
        real = self._real_path(rel)
        if real.is_dir():
            for item in real.iterdir():
                names.add(item.name)

        return [".", ".."] + sorted(names)

    def open(self, path: str, flags: int) -> int:
        self._fh_counter += 1
        return self._fh_counter

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        rel = self._relative_path(path)

        # Intent file
        file_entry = self.manifest.get_file(rel)
        if file_entry is not None:
            content = self._intent_content(rel)
            return content[offset : offset + size]

        # Real file
        real = self._real_path(rel)
        if real.is_file():
            with open(real, "rb") as f:
                f.seek(offset)
                return f.read(size)

        raise FuseOSError(errno.ENOENT)

    def write(self, path: str, buf: bytes, offset: int, fh: int) -> int:
        rel = self._relative_path(path)
        text = buf.decode("utf-8", errors="replace").strip()
        if not text:
            return len(buf)

        entry = self.manifest.ensure_file(rel)
        entry.stack.append(text, text, author="fuse")
        entry.squashed = False
        entry.synced = False
        self._locked_save()
        return len(buf)

    def release(self, path: str, fh: int) -> None:
        pass  # nothing to do

    def mkdir(self, path: str, mode: int) -> None:
        rel = self._relative_path(path)
        if rel:
            self.manifest.add_directory(rel, description="")
            self._locked_save()

    def unlink(self, path: str) -> None:
        rel = self._relative_path(path)
        if not self.manifest.remove_file(rel):
            # Also try removing from real dir
            real = self._real_path(rel)
            if real.is_file():
                real.unlink()
            else:
                raise FuseOSError(errno.ENOENT)
        self._locked_save()

    def create(self, path: str, mode: int, fi=None) -> int:
        rel = self._relative_path(path)
        self.manifest.ensure_file(rel)
        self._locked_save()
        self._fh_counter += 1
        return self._fh_counter

    def truncate(self, path: str, length: int, fh=None) -> None:
        pass  # intent files are append-only; truncation is a no-op

    def statfs(self, path: str) -> dict:
        return {
            "f_bsize": 4096,
            "f_frsize": 4096,
            "f_blocks": 1024 * 1024,
            "f_bfree": 1024 * 1024,
            "f_bavail": 1024 * 1024,
            "f_files": 65536,
            "f_ffree": 65536,
            "f_favail": 65536,
        }
