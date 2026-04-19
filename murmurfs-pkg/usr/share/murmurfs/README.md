# MurmurFS

> A FUSE filesystem where AI agents store **intent**, not content.

Files are fuzzy until you decide they're not. Every write is a layer of intention — what the agent *plans* to do, not what it *has* done. When you're ready, sync materializes the final file by asking an LLM to synthesize all layers.

```
read("auth.py") →
  v1: 实现用户认证模块，JWT方式
  v2: 砍掉session，只保留bearer token
  v3: 增加refresh token逻辑，过期时间15分钟

write("auth.py", "考虑加上 OAuth2 支持") → 追加 v4

sync("auth.py") → LLM 读取 v1-v4 → 生成完整 auth.py
```

## Why

AI agents are verbose. They write full files when they're still figuring out what to write. MurmurFS flips the model:

- **Defer precision** — Plan first, implement later
- **Reduce context** — Summaries are shorter than source code
- **Preserve reasoning** — Every decision is captured in the intent stack
- **Batch LLM costs** — Squash (cheap) and sync (expensive) are separate

## Install

```bash
# From source:
git clone https://github.com/ampresent/murmurfs.git
cd murmurfs
pip install -e .

# For development:
pip install -e ".[dev]"
```

**Requirements:** Python ≥ 3.10, `fusepy`, `pyyaml`, `click`, `requests`
**FUSE mount** additionally requires `libfuse` (e.g., `sudo apt install fuse` on Debian/Ubuntu)

## Quick Start

```bash
# Initialize a project
murmurfs init my-project -d "A web backend"

# Write intent (not code)
murmurfs write src/auth.py "实现用户认证模块，JWT方式"
murmurfs write src/auth.py "砍掉session，只保留bearer token"

# Read the intent stack
murmurfs read src/auth.py

# Compress layers (cheap LLM call)
murmurfs squash src/auth.py

# Generate real file (expensive LLM call)
murmurfs sync src/auth.py

# Sync all unsynced files
murmurfs sync --all

# Resolve conflicting intents from different agents
murmurfs merge src/auth.py

# Or mount as a FUSE filesystem
murmurfs mount ./murmur ./real
```

## Commands

| Command | Description |
|---------|-------------|
| `murmurfs init [path] [-d "desc"]` | Initialize a MurmurFS project |
| `murmurfs write <file> <intent> [-f "full"] [-a author]` | Append an intent layer |
| `murmurfs read <file>` | Show intent stack |
| `murmurfs list` | List all files with layer counts |
| `murmurfs status [file]` | Show file or project-wide status |
| `murmurfs squash <file> [--mock]` | Compress layers (cheap LLM call) |
| `murmurfs sync <file> [--mock]` | Generate real file (expensive LLM call) |
| `murmurfs sync --all [--mock]` | Sync all unsynced files |
| `murmurfs merge <file> [--mock]` | Resolve conflicting intents from different authors |
| `murmurfs mount <murmur> <real>` | Mount FUSE virtual filesystem |
| `murmurfs branch <file> --name <name> <base>` | Create intent stack branch |
| `murmurfs branch <file> --list` | List branches |
| `murmurfs branch <file> --merge <name>` | Merge branch into mainline |

> **Tip:** Use `--mock` on squash/sync/merge to test without an API key.

## Configuration

Configuration is stored in `.murmurfs/config.yaml`:

```yaml
auto_squash_threshold: 5    # Auto-squash after N layers
sync_on_squash: false        # Auto-sync after squash

llm:
  model: "gpt-4o"            # LLM model name
  base_url: "https://api.openai.com/v1"
  api_key_env: "MURMURFS_LLM_API_KEY"  # Env var for API key
  timeout: 60                # Request timeout in seconds
  max_retries: 3             # Retry count with exponential backoff
```

**Environment variables** (override config):

- `MURMURFS_LLM_API_KEY` — API key for the LLM service
- `MURMURFS_LLM_BASE_URL` — Base URL for OpenAI-compatible API
- `MURMURFS_LLM_MODEL` — Model name

## How It Works

```
project/
├── murmur/          # FUSE mount — the "fuzzy" view
├── real/            # Synced concrete files
└── .murmurfs/       # Metadata + intent storage
    ├── manifest.yaml   # Intent layers, sync status
    └── config.yaml     # Project configuration
```

Each file in `murmur/` is an **intent stack** — a list of what the agent planned to do. When you `cat` a file, you see summaries. When you `echo >>`, you append a new intent layer. When you `sync`, an LLM reads all layers and generates the real file in `real/`.

### Operations

- **Write** — Append an intent layer (cheap, no LLM)
- **Read** — View the intent stack as summaries
- **Squash** — Compress layers into one (cheap LLM call)
- **Sync** — Generate real code from intent (expensive LLM call)
- **Merge** — Resolve conflicts between different agents' intents

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  CLI     │     │  FUSE    │     │  LLM     │
│ (cli.py) │     │ (fs.py)  │     │ (llm.py) │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     └────────┬───────┘                │
              │                        │
     ┌────────▼────────┐    ┌─────────▼─────────┐
     │  Core Ops       │    │  Squash / Sync     │
     │  (ops.py)       │    │  (squash.py,       │
     │                 │    │   sync.py,         │
     │                 │    │   merge.py)        │
     └────────┬────────┘    └─────────┬─────────┘
              │                       │
     ┌────────▼───────────────────────▼─────────┐
     │  Manifest Manager (meta.py)              │
     │  .murmurfs/manifest.yaml                 │
     └──────────────────────────────────────────┘

Supporting: config.py (settings), cost.py (token tracking),
            branch.py (intent stack branching), mount.py (FUSE helpers)
```

## Examples

See [`examples/demo.sh`](examples/demo.sh) for a complete workflow demonstration.

## License

MIT
