"""Layer branching — fork intent stacks to explore alternative designs.

Allows an agent to branch an intent stack at a specific layer,
explore alternatives without losing the original stack, and later
merge back or discard the branch.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murmurfs.intent import IntentLayer, IntentStack
from murmurfs.meta import Manifest


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class Branch:
    """A single branch of an intent stack."""

    name: str
    base_layer: str
    stack: IntentStack = field(default_factory=IntentStack)
    created_at: str = ""
    merged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_layer": self.base_layer,
            "created_at": self.created_at,
            "merged": self.merged,
            "layers": self.stack.to_list(),
        }

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> Branch:
        stack = IntentStack.from_list(d.get("layers", []))
        return cls(
            name=name,
            base_layer=d["base_layer"],
            stack=stack,
            created_at=d.get("created_at", ""),
            merged=d.get("merged", False),
        )


class BranchError(Exception):
    """Raised when a branch operation fails."""


class BranchManager:
    """Manages branches for a single file's intent stack.

    Usage:
        manifest = Manifest.load_or_create("/path/to/project")
        bm = BranchManager(manifest, "src/auth.py")
        bm.create("oauth-approach", "v2")
    """

    def __init__(self, manifest: Manifest, file_path: str):
        self.manifest = manifest
        self.file_path = file_path
        self._entry = manifest.get_file(file_path)
        if self._entry is None:
            raise BranchError(f"file not found: {file_path}")

    def _branches_data(self) -> dict[str, Any]:
        """Return the branches dict from manifest file entry, or empty dict."""
        # We store branches in the raw manifest data. Access via a private attr on FileEntry.
        if not hasattr(self._entry, "_branches"):
            self._entry._branches = {}  # type: ignore[attr-defined]
        return self._entry._branches  # type: ignore[return-value]

    def create(self, name: str, base_layer_id: str) -> Branch:
        """Create a new branch from base_layer.

        Copies layers v1..base_layer_id into the branch's stack.
        The branch's next append will be base_layer_id's successor
        (e.g., if base=v2, branch layers are v1, v2, then next append is v3).

        Args:
            name: Branch name (must be unique across all branches for this file).
            base_layer_id: Layer id to fork from (e.g., "v2").

        Returns:
            The created Branch.

        Raises:
            BranchError: If branch name exists, base layer not found, or file not found.
        """
        branches = self._branches_data()
        if name in branches:
            raise BranchError(f"branch already exists: {name}")

        # Find the base layer in the main stack
        base_layer = self._entry.stack.get(base_layer_id)
        if base_layer is None:
            raise BranchError(f"base layer not found: {base_layer_id}")

        # Determine the base layer index so we copy v1..base_layer
        base_idx = None
        for i, layer in enumerate(self._entry.stack.layers):
            if layer.id == base_layer_id:
                base_idx = i
                break

        # Build branch stack from layers up to and including base
        branch_layers = []
        for layer in self._entry.stack.layers[: base_idx + 1]:
            branch_layers.append(
                IntentLayer(
                    id=layer.id,
                    timestamp=layer.timestamp,
                    summary=layer.summary,
                    full=layer.full,
                    author=layer.author,
                )
            )

        branch = Branch(
            name=name,
            base_layer=base_layer_id,
            stack=IntentStack(branch_layers),
            created_at=_now_iso(),
        )
        branches[name] = branch
        self.manifest.save()
        return branch

    def list_branches(self) -> list[Branch]:
        """List all branches for this file."""
        return list(self._branches_data().values())

    def get_branch(self, name: str) -> Branch:
        """Get a branch by name.

        Raises:
            BranchError: If branch not found.
        """
        branches = self._branches_data()
        if name not in branches:
            raise BranchError(f"branch not found: {name}")
        return branches[name]

    def _get_active_branch_key(self) -> str:
        return f"_active_branch:{self.file_path}"

    def switch(self, name: str | None) -> str | None:
        """Switch the active branch. None = mainline.

        Returns the previously active branch name.
        Raises:
            BranchError: If branch not found (only when name is not None).
        """
        key = self._get_active_branch_key()
        prev = getattr(self.manifest, key, None)
        if name is not None:
            self.get_branch(name)  # raises if not found
        setattr(self.manifest, key, name)
        return prev

    def active_branch(self) -> str | None:
        """Return the currently active branch name, or None for mainline."""
        return getattr(self.manifest, self._get_active_branch_key(), None)

    def append(
        self, branch_name: str, summary: str, full: str | None = None, author: str = "agent"
    ) -> IntentLayer:
        """Append a new layer to a branch's stack.

        Branch layers get a prefix in their id (e.g., "oauth/v3") to avoid
        collisions with mainline layer ids.

        Raises:
            BranchError: If branch not found or branch is merged.
        """
        branch = self.get_branch(branch_name)
        if branch.merged:
            raise BranchError(f"branch is already merged: {branch_name}")
        # Compute next id with branch prefix
        branch_layer_count = sum(
            1 for l in branch.stack.layers if l.id.startswith(f"{branch_name}/")
        )
        next_num = branch_layer_count + 1
        layer = IntentLayer(
            id=f"{branch_name}/v{next_num}",
            timestamp=_now_iso(),
            summary=summary,
            full=full if full is not None else summary,
            author=author,
        )
        branch.stack._layers.append(layer)
        self.manifest.save()
        return layer

    def merge(self, branch_name: str, llm_client: Any = None) -> list[IntentLayer]:
        """Merge a branch back into the mainline stack.

        If llm_client is provided, it will be called to intelligently merge.
        Otherwise, the branch's delta layers (layers after base_layer) are
        appended directly to the mainline stack.

        Args:
            branch_name: Name of the branch to merge.
            llm_client: Optional LLM client with a `complete(prompt) -> str` method.

        Returns:
            List of layers appended to the mainline.

        Raises:
            BranchError: If branch not found or already merged.
        """
        branch = self.get_branch(branch_name)
        if branch.merged:
            raise BranchError(f"branch is already merged: {branch_name}")

        # Determine delta: layers in branch after base_layer
        base_idx = None
        for i, layer in enumerate(branch.stack.layers):
            if layer.id == branch.base_layer:
                base_idx = i
                break

        if base_idx is None:
            raise BranchError(f"base layer {branch.base_layer} not found in branch stack")

        delta_layers = branch.stack.layers[base_idx + 1 :]

        if llm_client is not None:
            # Intelligent merge via LLM
            main_summaries = "\n".join(self._entry.stack.read())
            branch_summaries = "\n".join([f"{l.id}: {l.summary}" for l in delta_layers])
            prompt = (
                "You are merging a branch of intent layers back into the main stack.\n"
                f"Main stack:\n{main_summaries}\n\n"
                f"Branch delta layers:\n{branch_summaries}\n\n"
                "Produce a merged summary and full description that resolves conflicts "
                "and incorporates the branch changes into the mainline.\n"
                "Respond in the format:\nSUMMARY: <one-line>\nFULL: <detailed>"
            )
            response = llm_client.complete(prompt)
            summary = ""
            full = ""
            for line in response.strip().splitlines():
                if line.startswith("SUMMARY:"):
                    summary = line[len("SUMMARY:"):].strip()
                elif line.startswith("FULL:"):
                    full = line[len("FULL:"):].strip()
            if not summary:
                summary = f"Merged branch '{branch_name}'"
            if not full:
                full = summary
            layer = self._entry.stack.append(summary, full=full, author=f"merge:{branch_name}")
            appended = [layer]
        else:
            # Direct append of delta layers
            appended = []
            for dl in delta_layers:
                layer = self._entry.stack.append(
                    summary=dl.summary,
                    full=dl.full,
                    author=dl.author,
                )
                appended.append(layer)

        branch.merged = True
        self.manifest.save()
        return appended

    def delete(self, branch_name: str) -> None:
        """Delete a branch.

        Raises:
            BranchError: If branch not found.
        """
        branches = self._branches_data()
        if branch_name not in branches:
            raise BranchError(f"branch not found: {branch_name}")
        del branches[branch_name]
        # Clear active branch if it was this one
        if self.active_branch() == branch_name:
            self.switch(None)
        self.manifest.save()

    def diff(self, branch_name: str) -> list[dict[str, str]]:
        """Show the difference between a branch and the mainline.

        Returns list of dicts with keys: "layer_id", "summary", "side"
        where side is "branch-only" or "mainline-only".
        """
        branch = self.get_branch(branch_name)

        # Branch delta: layers after base_layer
        base_idx = None
        for i, layer in enumerate(branch.stack.layers):
            if layer.id == branch.base_layer:
                base_idx = i
                break

        if base_idx is None:
            raise BranchError(f"base layer {branch.base_layer} not found in branch stack")

        branch_delta_ids = {l.id for l in branch.stack.layers[base_idx + 1 :]}
        main_only_ids = set()

        # Layers in mainline after base_layer that are NOT in branch delta
        main_base_idx = None
        for i, layer in enumerate(self._entry.stack.layers):
            if layer.id == branch.base_layer:
                main_base_idx = i
                break

        if main_base_idx is not None:
            for layer in self._entry.stack.layers[main_base_idx + 1 :]:
                if layer.id not in branch_delta_ids:
                    main_only_ids.add(layer.id)

        result = []
        for layer in branch.stack.layers[base_idx + 1 :]:
            result.append({"layer_id": layer.id, "summary": layer.summary, "side": "branch-only"})

        if main_base_idx is not None:
            for layer in self._entry.stack.layers[main_base_idx + 1 :]:
                if layer.id in main_only_ids:
                    result.append(
                        {"layer_id": layer.id, "summary": layer.summary, "side": "mainline-only"}
                    )

        return result
