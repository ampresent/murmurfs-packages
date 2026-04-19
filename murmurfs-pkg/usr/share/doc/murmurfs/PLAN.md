# MurmurFS — Implementation Plan

## Phase 0: Project Setup

### Task 0.1: Initialize Python project
- Create `pyproject.toml` with dependencies: `fusepy`, `pyyaml`, `click`
- Create package structure: `murmurfs/` with `__init__.py`
- Create `tests/` directory with `conftest.py`
- Set up `pytest` configuration

**Deliverable:** Runnable `pip install -e .` with empty package

---

## Phase 1: Core Data Layer (No FUSE, No LLM)

### Task 1.1: Intent layer data model
- File: `murmurfs/intent.py`
- `IntentLayer` dataclass: `id`, `timestamp`, `summary`, `full`, `author`
- `IntentStack` class: manages a list of `IntentLayer`s
  - `append(summary, full, author)` → new layer
  - `read()` → list of summary strings
  - `squash(layers)` → replace range with single layer
  - Serialization to/from YAML

**Test:** Create stack, append layers, read summaries, squash, serialize/deserialize

### Task 1.2: Manifest manager
- File: `murmurfs/meta.py`
- `Manifest` class: loads/saves `.murmurfs/manifest.yaml`
- `add_file(path, description)` → register a file in the manifest
- `get_file(path)` → `FileEntry` (layers, sync status, etc.)
- `list_files()` → all registered files
- `add_directory(path, description)` → register directory metadata
- `get_directory(path)` → `DirectoryEntry`
- Auto-creates `.murmurfs/` on init

**Test:** Init manifest, add files/dirs, persist, reload, verify

### Task 1.3: File operations (CLI, not FUSE)
- File: `murmurfs/ops.py`
- `write_intent(manifest, path, summary, full)` → append layer
- `read_intent(manifest, path)` → print summaries
- `list_intents(manifest)` → show all files and their layer counts
- `init_project(path, description)` → create `.murmurfs/manifest.yaml`

**Test:** Full cycle — init project, write intents, read, list

---

## Phase 2: FUSE Mount (Read-Only Fuzzy View)

### Task 2.1: FUSE filesystem skeleton
- File: `murmurfs/fs.py`
- Implement `MurmurFS(fuse.Operations)`:
  - `getattr` — return file/directory attributes
  - `readdir` — list files (merge real + intended from manifest)
  - `open` / `read` — return intent summaries as file content
  - `write` — intercept and append intent layer
- Handle the "fuzzy directory" merging: real files on disk + intended files from manifest

**Test:** Mount filesystem, `ls`, `cat`, `echo >>`, verify behavior

### Task 2.2: Directory merging logic
- File: `murmurfs/fs.py` (extends Task 2.1)
- `readdir`: merge `os.listdir()` with `manifest.directories[path].intended`
- `getattr` for intended-only files: return synthetic attributes
- Handle nested directories: create implied directory structure from file paths

**Test:** Mount with mixed real/intended files, verify `ls -la` shows both

---

## Phase 3: LLM Integration

### Task 3.1: LLM client abstraction
- File: `murmurfs/llm.py`
- `LLMClient` abstract base with `complete(prompt, system) → str`
- Concrete implementation for OpenAI-compatible API (configurable base URL, model)
- Config via environment variables: `MURMURFS_LLM_BASE_URL`, `MURMURFS_LLM_MODEL`, `MURMURFS_LLM_API_KEY`

**Test:** Mock LLM client for unit tests; real integration test with actual API

### Task 3.2: Squash implementation
- File: `murmurfs/squash.py`
- `squash_file(manifest, path, llm_client)`:
  1. Read all layers from manifest
  2. Build squash prompt (see DESIGN.md)
  3. Call LLM
  4. Parse response into single `IntentLayer`
  5. Replace layers in manifest
  6. Mark `squashed: true`
- CLI: `murmurfs squash <file>`

**Test:** Mock LLM returns known response, verify manifest updated correctly

### Task 3.3: Sync implementation
- File: `murmurfs/sync.py`
- `sync_file(manifest, path, llm_client, project_root)`:
  1. Auto-squash if not already squashed
  2. Read squashed intent + project context
  3. Build sync prompt (see DESIGN.md)
  4. Call LLM
  5. Write generated content to `real/<path>`
  6. Update manifest: `synced: true`, `last_sync: timestamp`
- CLI: `murmurfs sync <file>` / `murmurfs sync --all`

**Test:** Mock LLM, verify file written to correct location, manifest updated

### Task 3.4: Merge implementation
- File: `murmurfs/merge.py`
- `merge_intents(manifest, path, llm_client)`:
  1. Detect layers from different authors that conflict
  2. Build merge prompt (see DESIGN.md)
  3. Call LLM
  4. Insert resolved intent layer
  5. Mark conflicting layers as superseded
- CLI: `murmurfs merge <file>`

**Test:** Create conflicting intents, mock LLM resolution, verify merge result

---

## Phase 4: CLI & UX

### Task 4.1: CLI tool
- File: `murmurfs/cli.py` (Click-based)
- Commands:
  - `murmurfs init [path] [--description]` — initialize project
  - `murmurfs mount <murmur> <real>` — mount FUSE filesystem
  - `murmurfs write <file> <intent>` — append intent layer
  - `murmurfs read <file>` — show intent stack
  - `murmurfs ls [path]` — list files with layer counts
  - `murmurfs squash <file>` — compress layers
  - `murmurfs sync <file|--all>` — generate real files
  - `murmurfs merge <file>` — resolve conflicts
  - `murmurfs status` — show project overview

**Test:** Integration tests using `click.testing.CliRunner`

### Task 4.2: Status & overview
- `murmurfs status` shows:
  - Total files, synced vs unsynced
  - Files with most layers (candidates for squash)
  - Estimated LLM cost (token count for pending squash/sync)
  - Conflicts needing resolution

---

## Phase 5: Polish & Edge Cases

### Task 5.1: Error handling
- LLM API failures → graceful fallback, preserve intent
- Malformed LLM responses → retry with error feedback
- Concurrent writes → file locking on manifest
- Missing sidecar → auto-initialize

### Task 5.2: Configuration
- `.murmurfs/config.yaml` for project-level settings:
  - `auto_squash_threshold: 5`
  - `sync_on_squash: false`
  - `llm_model: "gpt-4"`
  - `llm_base_url: "https://api.openai.com/v1"`

### Task 5.3: Documentation
- Update README.md with usage examples
- Add docstrings to all public functions
- Create `examples/` directory with sample project

---

## Dependency Graph

```
Phase 0 (setup)
  └── Phase 1 (data layer)
        ├── Phase 2 (FUSE)
        └── Phase 3 (LLM)
              └── Phase 4 (CLI)
                    └── Phase 5 (polish)
```

Phase 2 and Phase 3 can be developed in parallel after Phase 1.

## Estimated Scope

| Phase | Tasks | Complexity | Dependencies |
|-------|-------|------------|--------------|
| 0 | 1 | Low | None |
| 1 | 3 | Medium | Phase 0 |
| 2 | 2 | High | Phase 1 |
| 3 | 4 | Medium | Phase 1 |
| 4 | 2 | Low | Phase 2+3 |
| 5 | 3 | Low | Phase 4 |
