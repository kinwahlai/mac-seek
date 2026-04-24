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

## License

MIT — see [LICENSE](LICENSE).
