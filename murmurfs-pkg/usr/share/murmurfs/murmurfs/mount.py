"""Mount / unmount helpers for MurmurFS FUSE filesystem."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/tmp")
from fuse3_wrapper import FUSE3

from murmurfs.fs import MurmurFS
from murmurfs.meta import Manifest


def mount(
    murmur_dir: str,
    real_dir: str,
    manifest: Manifest,
    foreground: bool = False,
    debug: bool = False,
) -> None:
    murmur_dir = str(Path(murmur_dir).resolve())
    real_dir = str(Path(real_dir).resolve())
    os.makedirs(murmur_dir, exist_ok=True)
    os.makedirs(real_dir, exist_ok=True)

    fs = MurmurFS(murmur_dir, real_dir, manifest)
    FUSE3(fs, murmur_dir, foreground=foreground, debug=debug)


def unmount(murmur_dir: str) -> None:
    murmur_dir = str(Path(murmur_dir).resolve())
    subprocess.run(["fusermount", "-u", murmur_dir], check=True)
