"""Squash — compress multiple intent layers into a single layer using an LLM."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from murmurfs.intent import IntentLayer
from murmurfs.llm import LLMClient, LLMError
from murmurfs.meta import Manifest

if TYPE_CHECKING:
    from murmurfs.cost import CostTracker


def _build_squash_prompt(path: str, layers: list[IntentLayer], mode: str = "memory") -> str:
    """Build the squash prompt from layers.

    Args:
        path: File/topic path.
        layers: Intent layers to compress.
        mode: "memory" for agent memory compression, "code" for code intent compression.
    """
    if mode == "memory":
        lines = [
            "You are compressing an agent's memory layers into a single consolidated memory.",
            "Preserve all KEY FACTS, DECISIONS, and PREFERENCES. Discard redundant details.",
            "The compressed memory should be dense — every sentence must carry unique information.",
            "",
            f"Memory topic: {path}",
            "Layers (oldest → newest):",
        ]
        for layer in layers:
            imp = f" [importance={layer.importance}]" if layer.importance != 0.5 else ""
            tags = f" tags={layer.tags}" if layer.tags else ""
            lines.append(f"  {layer.id}{imp}{tags}: {layer.summary}")
            if layer.full != layer.summary:
                lines.append(f"    Detail: {layer.full}")
        lines.append("")
        lines.append("Compress into a single memory layer. Output EXACTLY in this format:")
        lines.append("SUMMARY: <one-line compressed summary>")
        lines.append("FULL: <dense memory — preserve facts, decisions, preferences; discard filler>")
        lines.append("IMPORTANCE: <0.0-1.0, where 1.0=critical>")
    else:
        lines = [
            "Given these intent layers for a file, compress them into a single comprehensive intent description.",
            "",
            f"File: {path}",
            "Layers:",
        ]
        for layer in layers:
            lines.append(f"Layer {layer.id}: {layer.summary}")
            lines.append(f"  Detail: {layer.full}")
        lines.append("")
        lines.append("Output EXACTLY in this format:")
        lines.append("SUMMARY: <one-line summary>")
        lines.append("FULL: <complete intent description>")
    return "\n".join(lines)


def _build_squash_retry_prompt(path: str, layers: list[IntentLayer], bad_response: str) -> str:
    """Build a retry prompt when the first response was malformed."""
    return (
        f"Your previous response for file '{path}' did not match the expected format.\n"
        f"Previous response:\n{bad_response}\n\n"
        f"Please try again. Output EXACTLY in this format:\n"
        f"SUMMARY: <one-line summary>\n"
        f"FULL: <complete intent description>"
    )


def _parse_squash_response(response: str) -> tuple[str, str, float]:
    """Parse the LLM response into (summary, full, importance).

    Returns (summary, full, importance). If the format markers are missing,
    falls back to using the entire response.
    """
    summary = ""
    full = ""
    importance = 0.5

    summary_match = re.search(r"^SUMMARY:\s*(.+)$", response, re.MULTILINE)
    if summary_match:
        summary = summary_match.group(1).strip()

    full_match = re.search(r"^FULL:\s*(.+)$", response, re.MULTILINE | re.DOTALL)
    if full_match:
        full = full_match.group(1).strip()

    importance_match = re.search(r"^IMPORTANCE:\s*([\d.]+)", response, re.MULTILINE)
    if importance_match:
        try:
            importance = max(0.0, min(1.0, float(importance_match.group(1))))
        except ValueError:
            importance = 0.5

    if not summary and not full:
        # Fallback: treat entire response as both
        summary = response.strip().split("\n")[0][:200]
        full = response.strip()

    if not full:
        full = summary

    return summary, full, importance


def _has_format_markers(response: str) -> bool:
    """Check if the response contains SUMMARY:/FULL: format markers."""
    return bool(re.search(r"^SUMMARY:", response, re.MULTILINE)) and bool(
        re.search(r"^FULL:", response, re.MULTILINE)
    )


def squash_file(
    manifest: Manifest,
    path: str,
    llm_client: LLMClient,
    cost_tracker: "CostTracker | None" = None,
    mode: str = "memory",
) -> IntentLayer:
    """Compress all intent layers for a file into a single layer.

    If the LLM response doesn't contain proper SUMMARY:/FULL: markers,
    retries once with an explicit format reminder.

    Args:
        manifest: The project manifest.
        path: Relative file path.
        llm_client: LLM client to use for compression.
        cost_tracker: Optional cost tracker to record token usage.

    Returns:
        The new squashed IntentLayer.

    Raises:
        ValueError: If file not found or no layers to squash.
        LLMError: If LLM call fails.
    """
    entry = manifest.get_file(path)
    if entry is None:
        raise ValueError(f"File not found in manifest: {path}")

    layers = entry.stack.layers
    if not layers:
        print(f"No layers to squash for: {path} — skipping.", file=sys.stderr)
        return entry.stack.append("empty", "empty") if entry.stack.count == 0 else layers[0]

    # If already single layer, return as-is
    if len(layers) == 1:
        entry.squashed = True
        manifest.save()
        return layers[0]

    prompt = _build_squash_prompt(path, layers, mode=mode)
    resp = llm_client.complete_with_usage(prompt)

    # If format is wrong, retry once with format reminder
    if not _has_format_markers(resp.text):
        print(
            f"Warning: LLM response for squash of '{path}' lacked SUMMARY:/FULL: markers, retrying...",
            file=sys.stderr,
        )
        retry_prompt = _build_squash_retry_prompt(path, layers, resp.text)
        resp2 = llm_client.complete_with_usage(retry_prompt)
        # Accumulate tokens from retry
        resp = type(resp)(
            text=resp2.text,
            prompt_tokens=resp.prompt_tokens + resp2.prompt_tokens,
            completion_tokens=resp.completion_tokens + resp2.completion_tokens,
            total_tokens=resp.total_tokens + resp2.total_tokens,
        )

    # Record cost
    if cost_tracker is not None:
        cost_tracker.record(
            operation="squash",
            file_path=path,
            model=getattr(llm_client, "model", "unknown"),
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )

    summary, full, importance = _parse_squash_response(resp.text)
    new_layer = entry.stack.squash(summary, full, importance=importance)
    entry.squashed = True
    manifest.save()

    return new_layer
