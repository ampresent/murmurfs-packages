"""MurmurFS CLI — command-line interface for intent-stack filesystem."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from murmurfs.ops import init_project, write_intent, read_intent, list_intents, get_file_status, search_memories, forget_memories
from murmurfs.meta import Manifest, MURMUR_DIR
from murmurfs.llm import OpenAILLMClient, LLMError
from murmurfs.squash import squash_file
from murmurfs.sync import sync_file, sync_all
from murmurfs.merge import merge_intents
from murmurfs.config import Config


@click.group()
@click.version_option(package_name="murmurfs")
def main():
    """MurmurFS — A FUSE filesystem where AI agents store intent, not content."""


@main.command()
@click.argument("path", default=".")
@click.option("--description", "-d", default="", help="Project description")
def init(path: str, description: str):
    """Initialize a new MurmurFS project."""
    manifest = init_project(path, description)
    click.echo(f"✓ Initialized MurmurFS project at {manifest.project_root}")
    click.echo(f"  Manifest: {manifest.manifest_path}")


@main.command()
@click.argument("file_path")
@click.argument("intent")
@click.option("--full", "-f", default=None, help="Full intent description")
@click.option("--author", "-a", default="agent", help="Author agent id")
@click.option("--importance", "-i", default=0.5, type=float, help="Importance 0.0-1.0 (1.0=critical)")
@click.option("--tags", "-t", default=None, help="Comma-separated tags")
@click.option("--project", "-p", default=".", help="Project root")
def write(file_path: str, intent: str, full: str | None, author: str, importance: float, tags: str | None, project: str):
    """Append an intent layer to a file."""
    manifest = _load_manifest(project)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    write_intent(manifest, file_path, intent, full, author, importance=importance, tags=tag_list)
    entry = manifest.get_file(file_path)
    click.echo(f"✓ Appended {entry.stack.layers[-1].id} to {file_path}")
    click.echo(f"  Total layers: {entry.stack.count}")


@main.command("read")
@click.argument("file_path")
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--verbose", "-v", is_flag=True, help="Show importance and tags")
def read_cmd(file_path: str, project: str, verbose: bool):
    """Read the intent stack for a file."""
    manifest = _load_manifest(project)
    entry = manifest.get_file(file_path)
    if entry is None:
        click.echo(f"Error: file not found: {file_path}", err=True)
        sys.exit(1)

    for layer in entry.stack.layers:
        layer.touch()
        if verbose:
            tags = f" [{', '.join(layer.tags)}]" if layer.tags else ""
            imp = f" ★{layer.importance:.1f}" if layer.importance >= 0.7 else f"  {layer.importance:.1f}"
            click.echo(f"  {layer.id}{imp}{tags}: {layer.summary}")
        else:
            click.echo(f"  {layer.id}: {layer.summary}")

    manifest.save()  # save touched timestamps


@main.command("list")
@click.option("--project", "-p", default=".", help="Project root")
def list_cmd(project: str):
    """List all files and their intent layer counts."""
    manifest = _load_manifest(project)
    results = list_intents(manifest)
    if not results:
        click.echo("No files registered.")
        return
    for r in results:
        status = "✓" if r["synced"] else "○"
        squash = "⊡" if r["squashed"] else " "
        click.echo(f"  {status} {r['path']:40s} {r['layers']:3d} layers {squash}")
    click.echo(f"\n  {len(results)} file(s)")


@main.command()
@click.argument("file_path", required=False, default=None)
@click.option("--project", "-p", default=".", help="Project root")
def status(file_path: str | None, project: str):
    """Show project or file status.

    Without FILE_PATH: show project-wide overview.
    With FILE_PATH: show detailed status for that file.
    """
    manifest = _load_manifest(project)

    if file_path is None:
        # Project-wide status
        _show_project_status(manifest, project)
    else:
        # File-level status
        info = get_file_status(manifest, file_path)
        if info is None:
            click.echo(f"Error: file not found: {file_path}", err=True)
            sys.exit(1)
        click.echo(f"File:        {info['path']}")
        click.echo(f"Description: {info['description'] or '(none)'}")
        click.echo(f"Layers:      {info['layers']}")
        click.echo(f"Squashed:    {'yes' if info['squashed'] else 'no'}")
        click.echo(f"Synced:      {'yes' if info['synced'] else 'no'}")
        if info["sync_target"]:
            click.echo(f"Sync target: {info['sync_target']}")
        if info["last_sync"]:
            click.echo(f"Last sync:   {info['last_sync']}")
        click.echo()
        for line in info["summaries"]:
            click.echo(f"  {line}")


def _show_project_status(manifest, project_root: str) -> None:
    """Show project-wide status overview."""
    from murmurfs.cost import CostTracker

    files = manifest.list_files()
    total = len(files)
    synced = sum(1 for f in files if f.synced)
    unsynced = total - synced
    squashed = sum(1 for f in files if f.squashed)
    total_layers = sum(f.stack.count for f in files)

    click.echo(f"Project: {manifest.project_name}")
    if manifest.project_description:
        click.echo(f"  {manifest.project_description}")
    click.echo()

    # File overview
    click.echo(f"Files:      {total} total, {synced} synced, {unsynced} unsynced, {squashed} squashed")
    click.echo(f"Layers:     {total_layers} total across all files")

    # Top files by layer count
    if files:
        by_layers = sorted(files, key=lambda f: f.stack.count, reverse=True)
        top = [f for f in by_layers if f.stack.count > 1]
        if top:
            click.echo()
            click.echo("Top files by layers (squash candidates):")
            for f in top[:5]:
                click.echo(f"  {f.path:40s} {f.stack.count:3d} layers {'⊡ squashed' if f.squashed else ''}")

    # Conflicts: files with layers from different authors
    conflicts = []
    for f in files:
        if f.stack.count >= 2:
            authors = {layer.author for layer in f.stack.layers}
            if len(authors) > 1:
                conflicts.append((f.path, authors))
    if conflicts:
        click.echo()
        click.echo("Conflicts (multiple authors):")
        for path, authors in conflicts:
            click.echo(f"  {path:40s} authors: {', '.join(sorted(authors))}")

    # Cost tracking
    try:
        tracker = CostTracker(project_root)
        totals = tracker.get_total()
        if totals["call_count"] > 0:
            click.echo()
            click.echo(f"LLM Usage:  {totals['call_count']} calls, {totals['total_tokens']} tokens")
            click.echo(f"  Estimated cost: ${totals['estimated_cost_usd']:.4f}")
            by_op = tracker.get_by_operation()
            for op, data in by_op.items():
                if data["call_count"] > 0:
                    click.echo(f"  {op:10s} {data['call_count']:3d} calls, {data['total_tokens']:6d} tokens")
    except Exception:
        pass  # No cost file yet


@main.command()
@click.argument("murmur_dir")
@click.option("--real", "-r", default=None, help="Real files directory (default: <murmur_dir>/../real)")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground")
@click.option("--debug", "-d", is_flag=True, help="Enable FUSE debug output")
@click.option("--project", "-p", default=".", help="Project root")
def mount(murmur_dir: str, real: str | None, foreground: bool, debug: bool, project: str):
    """Mount a MurmurFS virtual filesystem."""
    manifest = _load_manifest(project)
    murmur_dir = str(Path(murmur_dir).resolve())
    if real is None:
        real = str(Path(murmur_dir).parent / "real")

    # Check if FUSE is available
    try:
        import fuse
    except (ImportError, OSError) as e:
        msg = (
            "Error: fusepy is not available.\n"
            "  - Install fusepy: pip install fusepy\n"
            "  - Install libfuse: sudo apt install fuse (Debian/Ubuntu)\n"
            f"  - Details: {e}"
        )
        click.echo(msg, err=True)
        sys.exit(1)

    # Check if mount point exists and is accessible
    try:
        os.makedirs(murmur_dir, exist_ok=True)
    except OSError as e:
        click.echo(
            f"Error: cannot create mount point '{murmur_dir}': {e}",
            err=True,
        )
        sys.exit(1)

    # Check if /dev/fuse exists (FUSE kernel module)
    if not os.path.exists("/dev/fuse"):
        click.echo(
            "Error: /dev/fuse not found. FUSE kernel module may not be loaded.\n"
            "  On Debian/Ubuntu: sudo apt install fuse\n"
            "  On RHEL/CentOS: sudo yum install fuse\n"
            "  On macOS: brew install macfuse",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Mounting MurmurFS at {murmur_dir}")

    try:
        from murmurfs.mount import mount as fuse_mount
        fuse_mount(murmur_dir, real, manifest, foreground=foreground, debug=debug)
    except RuntimeError as e:
        click.echo(f"Error: FUSE mount failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(
            f"Error: failed to mount FUSE filesystem: {e}\n"
            f"  Check that you have permission to mount FUSE filesystems.\n"
            f"  You may need to be in the 'fuse' group or run as root.",
            err=True,
        )
        sys.exit(1)


def _make_llm_client(project: str, mock: bool):
    """Create an LLM client — real or mock."""
    from murmurfs.llm import MockLLMClient
    if mock:
        return MockLLMClient()
    config = Config.load(os.path.abspath(project))
    return OpenAILLMClient(config=config)


@main.command()
@click.argument("file_path")
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--mock", is_flag=True, help="Use mock LLM (no API key needed)")
def squash(file_path: str, project: str, mock: bool):
    """Compress multiple intent layers into a single layer."""
    manifest = _load_manifest(project)
    entry = manifest.get_file(file_path)
    if entry is None:
        click.echo(f"Error: file not found: {file_path}", err=True)
        sys.exit(1)
    if entry.stack.count <= 1:
        click.echo(f"Note: {file_path} has only {entry.stack.count} layer(s), no squash needed.")

    client = _make_llm_client(project, mock)
    try:
        new_layer = squash_file(manifest, file_path, client)
    except (ValueError, LLMError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"✓ Squashed {file_path}")
    click.echo(f"  Summary: {new_layer.summary}")


@main.command()
@click.argument("file_path", required=False, default=None)
@click.option("--all", "sync_all_flag", is_flag=True, help="Sync all unsynced files")
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--from", "from_layer", default=None, help="Starting layer id (inclusive)")
@click.option("--to", "to_layer", default=None, help="Ending layer id (inclusive)")
@click.option("--skip-last", "skip_last", default=0, type=int, help="Skip last N layers")
@click.option("--format", "output_format", default="code", type=click.Choice(["code", "json", "yaml"]), help="Output format")
@click.option("--mock", is_flag=True, help="Use mock LLM (no API key needed)")
def sync(file_path: str, sync_all_flag: bool, project: str, from_layer: str | None, to_layer: str | None, skip_last: int, output_format: str, mock: bool):
    """Generate concrete file from intent layers."""
    manifest = _load_manifest(project)
    client = _make_llm_client(project, mock)

    if sync_all_flag:
        try:
            results = sync_all(
                manifest, client, os.path.abspath(project),
                from_layer=from_layer, to_layer=to_layer, skip_last=skip_last,
            )
        except (ValueError, LLMError) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        for path in results:
            click.echo(f"✓ Synced {path} → real/{path}")
        click.echo(f"\n  {len(results)} file(s) synced")
    elif file_path:
        try:
            content = sync_file(
                manifest, file_path, client, os.path.abspath(project),
                from_layer=from_layer, to_layer=to_layer, skip_last=skip_last,
                output_format=output_format,
            )
        except (ValueError, LLMError) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        click.echo(f"✓ Synced {file_path} → real/{file_path}")
        click.echo(f"  Generated {len(content)} characters")
    else:
        click.echo("Error: specify FILE_PATH or --all.", err=True)
        sys.exit(1)


@main.command()
@click.argument("file_path")
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--mock", is_flag=True, help="Use mock LLM (no API key needed)")
def merge(file_path: str, project: str, mock: bool):
    """Resolve conflicting intent layers from different authors."""
    manifest = _load_manifest(project)
    entry = manifest.get_file(file_path)
    if entry is None:
        click.echo(f"Error: file not found: {file_path}", err=True)
        sys.exit(1)

    authors = {layer.author for layer in entry.stack.layers}
    if len(authors) < 2:
        click.echo(f"Warning: only one author ({', '.join(authors) or 'none'}), no conflicts to resolve.", err=True)

    client = _make_llm_client(project, mock)
    try:
        new_layer = merge_intents(manifest, file_path, client)
    except (ValueError, LLMError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"✓ Merged {file_path}")
    click.echo(f"  Summary: {new_layer.summary}")


@main.command()
@click.argument("file_path")
@click.argument("base_layer", required=False, default=None)
@click.option("--name", "-n", default=None, help="Branch name")
@click.option("--list", "list_flag", is_flag=True, help="List all branches")
@click.option("--switch", "switch_name", default=None, help="Switch active branch")
@click.option("--merge", "merge_name", default=None, help="Merge branch into mainline")
@click.option("--delete", "delete_name", default=None, help="Delete a branch")
@click.option("--diff", "diff_name", default=None, help="Show diff between branch and mainline")
@click.option("--project", "-p", default=".", help="Project root")
def branch(
    file_path: str,
    base_layer: str | None,
    name: str | None,
    list_flag: bool,
    switch_name: str | None,
    merge_name: str | None,
    delete_name: str | None,
    diff_name: str | None,
    project: str,
):
    """Manage intent stack branches for a file.

    Create:   murmurfs branch <file> <base_layer> --name <branch_name>
    List:     murmurfs branch <file> --list
    Switch:   murmurfs branch <file> --switch <branch_name>
    Merge:    murmurfs branch <file> --merge <branch_name>
    Delete:   murmurfs branch <file> --delete <branch_name>
    Diff:     murmurfs branch <file> --diff <branch_name>
    """
    from murmurfs.branch import BranchManager, BranchError

    manifest = _load_manifest(project)
    try:
        bm = BranchManager(manifest, file_path)
    except BranchError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        if list_flag:
            branches = bm.list_branches()
            if not branches:
                click.echo("No branches.")
                return
            active = bm.active_branch()
            for b in branches:
                marker = " (active)" if b.name == active else ""
                merged = " [merged]" if b.merged else ""
                delta = b.stack.count - (1 if b.base_layer else 0)
                click.echo(
                    f"  {b.name:30s} base={b.base_layer}  "
                    f"+{delta} layers  {b.created_at}{merged}{marker}"
                )

        elif switch_name is not None:
            prev = bm.switch(switch_name if switch_name != "-" else None)
            target = switch_name if switch_name != "-" else "mainline"
            click.echo(f"✓ Switched to {target} (was {prev or 'mainline'})")

        elif merge_name:
            appended = bm.merge(merge_name)
            click.echo(f"✓ Merged branch '{merge_name}' into mainline")
            for layer in appended:
                click.echo(f"  + {layer.id}: {layer.summary}")

        elif delete_name:
            bm.delete(delete_name)
            click.echo(f"✓ Deleted branch '{delete_name}'")

        elif diff_name:
            diffs = bm.diff(diff_name)
            if not diffs:
                click.echo("No differences.")
                return
            for d in diffs:
                prefix = "+" if d["side"] == "branch-only" else "-"
                click.echo(f"  {prefix} {d['layer_id']}: {d['summary']}")

        elif base_layer and name:
            branch = bm.create(name, base_layer)
            click.echo(f"✓ Created branch '{name}' from {base_layer}")
            click.echo(f"  Layers: {branch.stack.count}")

        else:
            click.echo("Error: specify an action. See --help.", err=True)
            sys.exit(1)

    except BranchError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("query")
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--limit", "-n", default=10, type=int, help="Max results")
def search(query: str, project: str, limit: int):
    """Search memory layers by keyword relevance."""
    manifest = _load_manifest(project)
    results = search_memories(manifest, query)
    if not results:
        click.echo(f"No results for: {query}")
        return
    click.echo(f"Found {len(results)} result(s) for: {query}\n")
    for r in results[:limit]:
        imp = f"★{r['importance']:.1f}" if r['importance'] >= 0.7 else f" {r['importance']:.1f}"
        tags = f" [{', '.join(r['tags'])}]" if r['tags'] else ""
        click.echo(f"  [{r['score']:.2f}] {imp} {r['path']}/{r['layer_id']}{tags}")
        click.echo(f"         {r['summary']}")


@main.command()
@click.option("--project", "-p", default=".", help="Project root")
@click.option("--threshold", "-t", default=0.1, type=float, help="Remove layers below this importance")
@click.option("--decay", is_flag=True, help="Apply decay before thresholding")
@click.option("--decay-factor", default=0.9, type=float, help="Decay factor (0-1)")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without removing")
def forget(project: str, threshold: float, decay: bool, decay_factor: float, dry_run: bool):
    """Remove or decay low-importance memory layers."""
    manifest = _load_manifest(project)

    if dry_run:
        # Show what would happen without actually doing it
        from murmurfs.meta import Manifest as _M
        import copy
        total_below = 0
        for entry in manifest.list_files():
            for layer in entry.stack.layers:
                effective = layer.importance
                if decay:
                    effective = effective * decay_factor
                if effective < threshold:
                    total_below += 1
                    click.echo(f"  would remove: {entry.path}/{layer.id} (importance={layer.importance:.2f}{'→'+f'{effective:.2f}' if decay else ''}) {layer.summary}")
        click.echo(f"\n{total_below} layer(s) would be removed (threshold={threshold})")
        return

    result = forget_memories(manifest, threshold=threshold, decay=decay, decay_factor=decay_factor)
    if result["removed_count"] == 0:
        click.echo(f"No layers below threshold {threshold}.")
        return

    click.echo(f"Forgot {result['removed_count']} layer(s):")
    for r in result["removed_layers"]:
        click.echo(f"  {r['path']}/{r['layer_id']} (importance={r['importance']:.2f}) {r['summary']}")
    click.echo(f"\n{result['remaining_count']} layer(s) remaining.")


def _load_manifest(project_root: str) -> Manifest:
    """Load manifest, exit with error if not a MurmurFS project."""
    project_root = str(Path(project_root).resolve())
    manifest_path = Path(project_root) / MURMUR_DIR / "manifest.yaml"
    if not manifest_path.exists():
        click.echo(
            f"Error: not a MurmurFS project. Run 'murmurfs init' first.",
            err=True,
        )
        sys.exit(1)
    return Manifest.load_or_create(project_root)


if __name__ == "__main__":
    main()
