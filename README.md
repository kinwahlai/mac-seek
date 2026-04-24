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

Full config reference:

```toml
[llm]
api_key = "sk-or-..."
base_url = "https://openrouter.ai/api/v1"
model = "google/gemini-2.0-flash-lite-001"
fallback_models = [
  "tencent/hy3-preview:free",
  "openrouter/free",
  "nvidia/nemotron-3-super-120b-a12b:free",
]

[index]
folders = ["~/Downloads", "~/Desktop"]
extensions = ["jpg", "jpeg", "png", "heic", "gif", "webp", "tiff"]
max_image_bytes = 20_000_000

[search]
max_candidates = 30      # files collected from mdfind + image index
max_read_candidates = 15 # how many to read content for (most recent first)
top_results = 5          # how many ranked results to return
```

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

Override env vars: `SEEK_LLM_MODEL`, `SEEK_LLM_BASE_URL`, `SEEK_LLM_API_KEY_ENV`.

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

## Local inference (MLX, Apple Silicon)

Run inference fully on-device — no API key, no network, no per-search cost:

```bash
make install-mlx    # adds mlx-lm to the existing venv
```

Then edit `~/.config/seek/config.toml`:

```toml
[llm]
provider = "mlx"
model = "mlx-community/Qwen3-1.7B-4bit"
```

The model (~1.1GB) is downloaded during `make install-mlx` into `~/.cache/huggingface/hub/`. To use a different model: `make install-mlx MLX_MODEL=mlx-community/Qwen3-4B-4bit`. Thinking mode is automatically disabled for fast, direct JSON output.

**Recommended models** (4-bit quantized, comfortable on 16GB Macs):

| Model | Size | Notes |
|---|---|---|
| `mlx-community/Qwen3-1.7B-4bit` | ~1.1GB | Recommended — best quality/size ratio |
| `mlx-community/Qwen3-4B-4bit` | ~2.5GB | Better ranking quality, slower load |
| `mlx-community/Qwen2.5-3B-Instruct-4bit` | ~2GB | Fallback if Qwen3 unavailable |

To switch back to OpenRouter, remove the `provider` and `model` lines (or comment them out).

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
- **Interactive refinement** — if top results aren't right, refine the query conversationally

---

## License

MIT — see [LICENSE](LICENSE).
