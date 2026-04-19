"""Configuration management for MurmurFS.

Loads project-level settings from .murmurfs/config.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murmurfs.meta import MURMUR_DIR


@dataclass
class LLMConfig:
    """LLM-specific configuration."""

    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "MURMURFS_LLM_API_KEY"
    timeout: int = 60
    max_retries: int = 3

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> LLMConfig:
        if not d:
            return cls()
        return cls(
            model=d.get("model", "gpt-4o"),
            base_url=d.get("base_url", "https://api.openai.com/v1"),
            api_key_env=d.get("api_key_env", "MURMURFS_LLM_API_KEY"),
            timeout=d.get("timeout", 60),
            max_retries=d.get("max_retries", 3),
        )


@dataclass
class Config:
    """Project-level configuration for MurmurFS.

    Stored in .murmurfs/config.yaml. If the file doesn't exist,
    sensible defaults are used.

    Usage:
        config = Config.load("/path/to/project")
        print(config.auto_squash_threshold)
        config.auto_squash_threshold = 10
        config.save()
    """

    project_root: str = ""
    auto_squash_threshold: int = 5
    sync_on_squash: bool = False
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def config_path(self) -> str:
        return os.path.join(self.project_root, MURMUR_DIR, "config.yaml")

    @classmethod
    def load(cls, project_root: str) -> Config:
        """Load config from .murmurfs/config.yaml, or return defaults.

        Args:
            project_root: Path to the project root directory.
        """
        project_root = str(Path(project_root).resolve())
        config_path = os.path.join(project_root, MURMUR_DIR, "config.yaml")

        if not os.path.exists(config_path):
            return cls(project_root=project_root)

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(
            project_root=project_root,
            auto_squash_threshold=data.get("auto_squash_threshold", 5),
            sync_on_squash=data.get("sync_on_squash", False),
            llm=LLMConfig.from_dict(data.get("llm")),
        )

    def save(self) -> None:
        """Save config to .murmurfs/config.yaml."""
        data: dict[str, Any] = {
            "auto_squash_threshold": self.auto_squash_threshold,
            "sync_on_squash": self.sync_on_squash,
            "llm": self.llm.to_dict(),
        }
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def __repr__(self) -> str:
        return (
            f"Config(threshold={self.auto_squash_threshold}, "
            f"model={self.llm.model})"
        )
