"""Merge — resolve conflicting intent layers from different authors using an LLM."""

from __future__ import annotations

import re

from murmurfs.intent import IntentLayer
from murmurfs.llm import LLMClient
from murmurfs.meta import Manifest


def _build_merge_prompt(path: str, layers: list[IntentLayer]) -> str:
    """Build the merge prompt for conflict resolution."""
    lines = [
        f"Intent layers for file '{path}' were written by different agents and may conflict.",
        "Analyze them and produce a unified intent that resolves conflicts reasonably.",
        "If a true conflict exists that can't be auto-resolved, flag it.",
        "",
        "Layers:",
    ]
    for layer in layers:
        lines.append(f"[{layer.author}] Layer {layer.id}: {layer.summary}")
        lines.append(f"  Detail: {layer.full}")
    lines.append("")
    lines.append("Output EXACTLY in this format:")
    lines.append("SUMMARY: <one-line summary>")
    lines.append("FULL: <complete intent description resolving all conflicts>")
    return "\n".join(lines)


def _parse_merge_response(response: str) -> tuple[str, str]:
    """Parse LLM response into (summary, full)."""
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

    return summary, full


def merge_intents(
    manifest: Manifest,
    path: str,
    llm_client: LLMClient,
) -> IntentLayer:
    """Detect and resolve semantic conflicts in intent layers from different authors.

    Args:
        manifest: The project manifest.
        path: Relative file path.
        llm_client: LLM client to use for conflict resolution.

    Returns:
        The resolved IntentLayer inserted into the stack.
    """
    entry = manifest.get_file(path)
    if entry is None:
        raise ValueError(f"File not found in manifest: {path}")

    layers = entry.stack.layers
    if len(layers) < 2:
        return layers[0] if layers else entry.stack.append("empty", "empty")

    # Check if there are actually different authors
    authors = {layer.author for layer in layers}
    if len(authors) < 2:
        # No conflict possible with single author
        return layers[-1]

    prompt = _build_merge_prompt(path, layers)
    response = llm_client.complete(prompt)

    summary, full = _parse_merge_response(response)

    # Insert the resolved layer
    new_layer = entry.stack.squash(summary, full, author="merge-resolver")
    manifest.save()

    return new_layer
