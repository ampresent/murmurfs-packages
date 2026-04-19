"""Sync — generate concrete file contents from intent layers using an LLM."""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from murmurfs.intent import IntentStack, IntentLayer
from murmurfs.llm import LLMClient, LLMError
from murmurfs.meta import Manifest
from murmurfs.squash import squash_file

if TYPE_CHECKING:
    from murmurfs.cost import CostTracker


def _build_sync_prompt(
    path: str,
    full_intent: str,
    project_description: str,
    related_files: list[dict],
    output_format: str = "code",
) -> str:
    """Build the sync prompt.

    Args:
        output_format: "code" for code files, "json" for structured JSON, "yaml" for YAML.
    """
    if output_format == "json":
        lines = [
            "Generate a structured JSON object from the following agent memory.",
            "The JSON should capture all key facts, decisions, and preferences.",
            "Use nested objects for logical grouping. Include metadata fields.",
            "",
            f"Memory topic: {path}",
            f"Project: {project_description}",
            "",
            "Memory content:",
            full_intent,
            "",
            "Related memories:",
        ]
        for rf in related_files:
            lines.append(f"- {rf['path']}: {rf['summary']}")
        lines.append("")
        lines.append("Output ONLY valid JSON. No markdown fences, no explanations.")
    elif output_format == "yaml":
        lines = [
            "Generate a structured YAML document from the following agent memory.",
            "The YAML should capture all key facts, decisions, and preferences.",
            "Use nested keys for logical grouping.",
            "",
            f"Memory topic: {path}",
            f"Project: {project_description}",
            "",
            "Memory content:",
            full_intent,
            "",
            "Related memories:",
        ]
        for rf in related_files:
            lines.append(f"- {rf['path']}: {rf['summary']}")
        lines.append("")
        lines.append("Output ONLY valid YAML. No markdown fences, no explanations.")
    else:
        lines = [
            "Generate the file contents for the following intent.",
            "",
            f"Project: {project_description}",
            f"File: {path}",
            "",
            "Intent:",
            full_intent,
            "",
            "Related files:",
        ]
        for rf in related_files:
            lines.append(f"{rf['path']}: {rf['summary']}")
        lines.append("")
        lines.append("Generate the complete file content. Output ONLY the file content, no explanations.")
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _resolve_layer_range(
    layers: list[IntentLayer],
    from_layer: str | None,
    to_layer: str | None,
    skip_last: int,
) -> tuple[list[IntentLayer], str | None, str | None]:
    """Filter layers based on partial sync parameters.

    Args:
        layers: Full list of intent layers.
        from_layer: Starting layer id (inclusive). None = from beginning.
        to_layer: Ending layer id (inclusive). None = until end.
        skip_last: Number of layers to skip from the end.

    Returns:
        Tuple of (filtered_layers, resolved_from, resolved_to).

    Raises:
        ValueError: If layer ids don't exist or range is invalid.
    """
    if not layers:
        raise ValueError("No layers available")

    layer_ids = [l.id for l in layers]

    if from_layer is not None and from_layer not in layer_ids:
        raise ValueError(f"Layer id '{from_layer}' not found. Available: {layer_ids}")

    if to_layer is not None and to_layer not in layer_ids:
        raise ValueError(f"Layer id '{to_layer}' not found. Available: {layer_ids}")

    # Find indices
    start_idx = 0
    if from_layer is not None:
        start_idx = layer_ids.index(from_layer)

    end_idx = len(layers) - 1
    if to_layer is not None:
        end_idx = layer_ids.index(to_layer)

    # Apply skip_last (from the end of the full list, not the filtered range)
    if skip_last > 0:
        skip_end_idx = len(layers) - skip_last
        if skip_end_idx <= 0:
            raise ValueError(
                f"skip_last={skip_last} exceeds total layer count ({len(layers)})"
            )
        end_idx = min(end_idx, skip_end_idx - 1)

    if start_idx > end_idx:
        raise ValueError(
            f"Invalid layer range: from='{from_layer}' (index {start_idx}) "
            f"is after to='{to_layer}' (index {end_idx})"
        )

    filtered = layers[start_idx : end_idx + 1]
    resolved_from = filtered[0].id
    resolved_to = filtered[-1].id

    return filtered, resolved_from, resolved_to


def sync_file(
    manifest: Manifest,
    path: str,
    llm_client: LLMClient,
    project_root: str,
    from_layer: str | None = None,
    to_layer: str | None = None,
    skip_last: int = 0,
    cost_tracker: "CostTracker | None" = None,
    output_format: str = "code",
) -> str:
    """Generate and write the concrete file from its intent layers.

    If the file hasn't been squashed yet, squash it first automatically.

    Args:
        manifest: The project manifest.
        path: Relative file path.
        llm_client: LLM client to use.
        project_root: Path to the project root directory.
        from_layer: Starting layer id for partial sync.
        to_layer: Ending layer id for partial sync.
        skip_last: Number of layers to skip from the end.
        cost_tracker: Optional cost tracker to record token usage.
        output_format: "code", "json", or "yaml".

    Returns:
        The generated file content.

    Raises:
        ValueError: If file not found, no layers, empty intent, or invalid range.
        LLMError: If LLM call fails.
    """
    entry = manifest.get_file(path)
    if entry is None:
        raise ValueError(f"File not found in manifest: {path}")

    if not entry.stack.layers:
        raise ValueError(f"No layers to sync for: {path}")

    is_partial = from_layer is not None or to_layer is not None or skip_last > 0

    if is_partial:
        # Partial sync: filter layers and work with a subset
        filtered_layers, resolved_from, resolved_to = _resolve_layer_range(
            entry.stack.layers, from_layer, to_layer, skip_last
        )

        # Check intent is not empty
        has_content = any(
            (layer.summary.strip() or layer.full.strip())
            for layer in filtered_layers
        )
        if not has_content:
            raise ValueError(f"All intent layers for '{path}' are empty — nothing to sync.")

        # Squash the filtered layers into a temporary stack
        if len(filtered_layers) > 1:
            temp_stack = IntentStack(layers=filtered_layers)
            prompt_lines = [
                "Given these intent layers for a file, compress them into a single comprehensive intent description.",
                "",
                f"File: {path}",
                "Layers:",
            ]
            for layer in filtered_layers:
                prompt_lines.append(f"Layer {layer.id}: {layer.summary}")
                prompt_lines.append(f"  Detail: {layer.full}")
            prompt_lines.append("")
            prompt_lines.append("Output EXACTLY in this format:")
            prompt_lines.append("SUMMARY: <one-line summary>")
            prompt_lines.append("FULL: <complete intent description>")
            squash_prompt = "\n".join(prompt_lines)
            squash_resp = llm_client.complete_with_usage(squash_prompt)
            response = squash_resp.text

            # Parse squash response
            import re
            summary = ""
            full = ""
            summary_match = re.search(r"^SUMMARY:\s*(.+)$", response, re.MULTILINE)
            if summary_match:
                summary = summary_match.group(1).strip()
            full_match = re.search(r"^FULL:\s*(.+)$", response, re.MULTILINE | re.DOTALL)
            if full_match:
                full = full_match.group(1).strip()
            if not summary and not full:
                summary = response.strip().split("\n")[0][:200]
                full = response.strip()
            if not full:
                full = summary
            full_intent = full
        else:
            full_intent = filtered_layers[0].full

        synced_layer_ids = [l.id for l in filtered_layers]

        if not full_intent or not full_intent.strip():
            raise ValueError(f"Intent for '{path}' is empty — nothing to sync.")

        # Collect related file context
        related_files = []
        for other in manifest.list_files():
            if other.path != path and other.stack.count > 0:
                latest = other.stack.layers[-1]
                related_files.append({
                    "path": other.path,
                    "summary": latest.summary,
                })

        prompt = _build_sync_prompt(
            path=path,
            full_intent=full_intent,
            project_description=manifest.project_description,
            related_files=related_files,
            output_format=output_format,
        )

        sync_resp = llm_client.complete_with_usage(prompt)
        content = sync_resp.text

        # Record cost for sync
        if cost_tracker is not None:
            total_prompt = getattr(squash_resp, 'prompt_tokens', 0) + sync_resp.prompt_tokens
            total_completion = getattr(squash_resp, 'completion_tokens', 0) + sync_resp.completion_tokens
            cost_tracker.record(
                operation="sync",
                file_path=path,
                model=getattr(llm_client, "model", "unknown"),
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
            )

        if not content or not content.strip():
            print(
                f"Warning: LLM generated empty content for '{path}'. "
                f"Writing empty file anyway.",
                file=sys.stderr,
            )
            content = ""

        # Write to real/<path>
        real_dir = os.path.join(project_root, "real")
        real_file = os.path.join(real_dir, path)
        os.makedirs(os.path.dirname(real_file), exist_ok=True)
        with open(real_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Update manifest with partial sync info
        entry.synced = True
        entry.sync_target = f"real/{path}"
        entry.last_sync = _now_iso()
        entry.sync_range = {"from": resolved_from, "to": resolved_to}
        entry.synced_layers = synced_layer_ids
        manifest.save()

        return content

    else:
        # Full sync (original logic)
        # Capture original layer IDs before squash
        original_layer_ids = [l.id for l in entry.stack.layers]

        # Check intent is not empty
        has_content = any(
            (layer.summary.strip() or layer.full.strip())
            for layer in entry.stack.layers
        )
        if not has_content:
            raise ValueError(f"All intent layers for '{path}' are empty — nothing to sync.")

        # Auto-squash if not already squashed and has multiple layers
        if not entry.squashed and entry.stack.count > 1:
            squash_file(manifest, path, llm_client)
            # Reload entry after squash
            entry = manifest.get_file(path)

        # Get the (now single) intent
        full_intent = entry.stack.layers[0].full

        if not full_intent or not full_intent.strip():
            raise ValueError(f"Squashed intent for '{path}' is empty — nothing to sync.")

        # Collect related file context
        related_files = []
        for other in manifest.list_files():
            if other.path != path and other.stack.count > 0:
                latest = other.stack.layers[-1]
                related_files.append({
                    "path": other.path,
                    "summary": latest.summary,
                })

        prompt = _build_sync_prompt(
            path=path,
            full_intent=full_intent,
            project_description=manifest.project_description,
            related_files=related_files,
            output_format=output_format,
        )

        content = llm_client.complete(prompt)

        if not content or not content.strip():
            print(
                f"Warning: LLM generated empty content for '{path}'. "
                f"Writing empty file anyway.",
                file=sys.stderr,
            )
            content = ""

        # Write to real/<path>
        real_dir = os.path.join(project_root, "real")
        real_file = os.path.join(real_dir, path)
        os.makedirs(os.path.dirname(real_file), exist_ok=True)
        with open(real_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Update manifest
        entry.synced = True
        entry.sync_target = f"real/{path}"
        entry.last_sync = _now_iso()
        # Record full sync range using original layer IDs (before squash)
        entry.sync_range = {"from": original_layer_ids[0], "to": original_layer_ids[-1]}
        entry.synced_layers = original_layer_ids
        manifest.save()

        return content


def sync_all(
    manifest: Manifest,
    llm_client: LLMClient,
    project_root: str,
    from_layer: str | None = None,
    to_layer: str | None = None,
    skip_last: int = 0,
) -> dict[str, str]:
    """Sync all files that haven't been synced yet.

    Args:
        manifest: The project manifest.
        llm_client: LLM client to use.
        project_root: Path to the project root directory.
        from_layer: Starting layer id for partial sync (applied to all files).
        to_layer: Ending layer id for partial sync (applied to all files).
        skip_last: Number of layers to skip from the end (applied to all files).

    Returns:
        Dict mapping file path to generated content.
    """
    results = {}
    for entry in manifest.list_files():
        if not entry.synced:
            content = sync_file(
                manifest, entry.path, llm_client, project_root,
                from_layer=from_layer, to_layer=to_layer, skip_last=skip_last,
            )
            results[entry.path] = content
    return results
