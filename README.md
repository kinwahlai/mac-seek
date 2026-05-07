# seek

Find files on your Mac by describing what you remember, not what they're named.

```
seek "the order summary for the laptop I bought last month"
seek "notes from the team retro in February"
seek "that Python script for parsing the CSV exports"
seek "the image with hold and let go written on it"
```

`seek` runs multi-pass Spotlight searches, reads candidate file content, and uses an LLM to rank results by semantic relevance — including images via on-device Apple Vision OCR.

---

## Quick start

### 1. Get an OpenRouter API key

[openrouter.ai/keys](https://openrouter.ai/keys) — free account, no credit card required for free-tier models.

### 2. Install

```bash
make install
```

This creates a Python venv, installs dependencies, builds the image caption helper (requires macOS CLI tools — run `xcode-select --install` first if prompted), and puts `seek` on your PATH at `~/.local/bin/seek`.

Optional — enables PDF and Word doc content reading:
```bash
brew install poppler pandoc
```

### 3. Configure

Add your API key to `~/.config/seek/config.toml` (created automatically on first run):

```toml
[llm]
api_key = "sk-or-..."   # paste your OpenRouter key here
```

Full config reference (two-tier provider + search filters):

```toml
# Primary provider — OpenRouter (cloud)
[llm]
api_key_env = "OPENROUTER_API_KEY"
base_url = "https://openrouter.ai/api/v1"
model = "google/gemini-2.0-flash-lite-001"
fallback_models = [
  "tencent/hy3-preview:free",
  "openrouter/free",
  "nvidia/nemotron-3-super-120b-a12b:free",
]

# Optional fallback — local Rapid-MLX (Apple Silicon).
# Triggered when the primary errors out, returns malformed JSON, or shape-mismatches.
# Run `make install-mlx` first; seek auto-spawns the daemon on demand.
[llm.fallback]
base_url = "http://localhost:8000/v1"
model = "default"
local_model = "llama3-3b"      # ~1.9 GB, no thinking mode
idle_timeout_seconds = 900     # daemon self-exits after this many idle seconds

[index]
folders = ["~/Downloads", "~/Desktop"]
extensions = ["jpg", "jpeg", "png", "heic", "gif", "webp", "tiff"]
max_image_bytes = 20_000_000

[search]
max_candidates = 30          # files collected from mdfind + image index
max_read_candidates = 15     # how many to read content for
top_results = 5              # how many ranked results to return
skip_dirs = ["~/dev_repo"]   # directory trees to exclude entirely
```

**Filename matches always surface**: when your query implies a filename fragment (e.g. "NRIC"), seek runs a `kMDItemDisplayName` pass and puts those hits in a priority bucket that bypasses the recency sort — so an old-but-exact-name file isn't crowded out by recent unrelated activity.

### 4. Index your images (optional)

```bash
seek index          # caption images in configured folders
seek index --status # show index stats
```

### 5. Search

```bash
seek "the slide deck from the product review"
seek "notes I took during the onboarding session"
seek "that photo with inspirational text on it"
```

Pick a number to open the file, or `q` to quit.

---

## Configuration

`~/.config/seek/config.toml` is created with defaults on first run. All settings are optional — the built-in defaults work out of the box with just an API key.

### LLM override priority (highest → lowest)

| Method | Example |
|---|---|
| Env var | `SEEK_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free seek "..."` |
| Config `[llm]` section | `model = "gpt-4o-mini"` in config.toml |
| Built-in default | `google/gemini-2.0-flash-lite-001` via OpenRouter |

Override env vars: `SEEK_LLM_MODEL`, `SEEK_LLM_BASE_URL`, `SEEK_LLM_API_KEY_ENV`. These apply only to `[llm]` (primary) — `[llm.fallback]` is untouched.

### Switching providers

Edit the `[llm]` section in `~/.config/seek/config.toml`:

```toml
# OpenAI
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"

# Any OpenAI-compatible endpoint
base_url = "https://your-endpoint/v1"
model = "your-model-name"
api_key = "your-key"
```

---

## Local inference (Rapid-MLX, Apple Silicon)

Run inference on-device — no per-search cost, no network — with cloud as a safety net.

```bash
make install-mlx
```

This installs [Rapid-MLX](https://github.com/raullenchai/Rapid-MLX) (an OpenAI-compatible local server) into the existing venv and symlinks `~/.local/bin/rapid-mlx`.

### Two ways to wire it up

**Cloud-primary, local fallback** (the default config above): the local daemon is only spawned when OpenRouter errors out, returns malformed JSON, or shape-mismatches. Free safety net at the cost of an occasional ~6s cold boot.

**Local-primary, cloud fallback**: swap the two sections — put Rapid-MLX in `[llm]` and OpenRouter in `[llm.fallback]`. Best for sustained offline use.

```toml
[llm]
base_url = "http://localhost:8000/v1"
model = "default"
local_model = "llama3-3b"      # ~1.9 GB, no thinking mode
idle_timeout_seconds = 900     # daemon self-exits after N idle seconds
```

The first query downloads the model (~1.9 GB) and boots the daemon (~6s cold, ~1s warm). A watchdog process self-terminates the daemon after `idle_timeout_seconds` (default 15 min) of inactivity. No API key needed — seek auto-injects a dummy `"local"` key for localhost endpoints.

### Server commands

```bash
seek server start    # spawn the daemon explicitly (otherwise auto-spawned on demand)
seek server stop     # kill the daemon
seek server status   # show pid, port, last-used time
```

State files: `~/.local/share/seek/rapid-mlx.{pid,last_used,log}`.

### Local model options

| `local_model` | Size | Notes |
|---|---|---|
| `llama3-3b` | ~1.9 GB | **Recommended** — empirical winner for this task |
| `ministral-3b` | ~1.9 GB | Verbose JSON; can hit `max_tokens` on the rank step |
| `phi4-14b` | ~8.5 GB | Higher quality, slower load, more RAM |

`llama3-3b` is the default because `qwen3.5-4b`'s mandatory thinking mode (3072 tokens, 99s) blocked usage and `ministral-3b`'s verbosity truncated the rank step. Models live in Rapid-MLX's cache directory.

---

## Image search

`seek` indexes images using Apple Vision (on-device OCR + scene classification — no image data leaves your machine). The index lives at `~/.local/share/seek/index.db`.

```bash
seek index             # incremental — only captions new/changed images
seek index --rebuild   # wipe and re-index everything
seek index --status    # show count and last-indexed date
```

Configure which folders and file types to index in `~/.config/seek/config.toml` under `[index]`.

---

## iCloud

Files offloaded to iCloud (not yet downloaded locally) are automatically downloaded before content reading, with a 4-second timeout per file. If the download times out, the file is ranked on filename and metadata only.

---

## Dependencies

| Dependency | Required | Purpose |
|---|---|---|
| Python 3.9+ | Yes | Runtime |
| `openai` (pip) | Yes | LLM API (OpenRouter-compatible) |
| `pdftotext` (`poppler`) | No | PDF content reading |
| `pandoc` | No | Word doc content reading |
| macOS CLI tools | Yes (for image index) | Compile `seek-caption` Swift binary |

---

## Ideas to revisit

- **VLM image captioning** — replace the Apple Vision Swift binary with a vision-language model (e.g. via `mlx-vlm`) for richer semantic captions ("a signed MOU between two parties" vs OCR text alone). Apple Vision is instant and great at OCR; a VLM would add scene understanding at the cost of slower batch indexing. Best explored as an optional `seek index --rich` mode or for targeted re-captioning.
- **Cache recent searches** — SQLite cache for instant re-retrieval of recent queries (`seek --last`)
- **Embeddings index** — local embeddings for file contents to speed up repeat searches without LLM calls
- **Interactive refinement** — if top results aren't right, type a follow-up ("no, it was a PDF" / "more recent than that") and seek reruns with the original query + correction fed back to the LLM. No new mdfind pass needed — candidates are already read. UI: a persistent input bar at the bottom (Claude Code-style) using `prompt_toolkit` or `curses`, with results scrolled above.

---

## License

MIT — see [LICENSE](LICENSE).
