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

### 1. Install Python dependencies

```bash
pip install openai
# Optional — enables PDF and Word doc content reading:
brew install poppler pandoc
```

### 2. Build the image caption helper

```bash
cd tools/caption
swiftc -O seek-caption.swift -o seek-caption
```

This compiles a small Swift binary that uses Apple's Vision framework (on-device OCR + image classification). Requires macOS CLI tools (`xcode-select --install`).

### 3. Get an OpenRouter API key

[openrouter.ai/keys](https://openrouter.ai/keys) — free account, no credit card required for free-tier models.

### 4. Configure

Create `~/.config/seek/config.toml` (or let `seek index` create it on first run):

```toml
[llm]
api_key_env = "OPENROUTER_API_KEY"   # name of env var to read the key from
api_key = "sk-or-..."                # OR paste key directly here
base_url = "https://openrouter.ai/api/v1"
model = "google/gemini-2.0-flash-lite-001"
fallback_models = [
  "tencent/hy3-preview:free",
  "openrouter/free",
  "nvidia/nemotron-3-super-120b-a12b:free",
]

[index]
folders = [
  "~/Downloads",
  "~/Desktop",
]
extensions = ["jpg", "jpeg", "png", "heic", "gif", "webp", "tiff"]
max_image_bytes = 20_000_000
```

If you prefer env vars, set `OPENROUTER_API_KEY` in your shell profile or `.env` file instead of using `api_key` directly.

### 5. Install to PATH

```bash
ln -sf "$(pwd)/seek.py" ~/.local/bin/seek
chmod +x seek.py
```

### 6. Index your images (optional)

```bash
seek index          # caption images in configured folders
seek index --status # show index stats
```

### 7. Search

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

## Raycast extension

The `raycast-extension/` directory is a full Raycast extension with an interactive list view, confidence scores, detail panel, and file actions.

**Requirements:** `seek` must be installed to PATH (step 5 above).

```bash
cd raycast-extension
npm install
npm run dev    # registers in Raycast under "Development"
npm run build  # production build
```

**Actions per result:**
- `↵` Open file
- `⌘⇧F` Show in Finder
- `⌘.` Copy path
- `⌘⇧.` Open in VS Code
- `⌘,` Open config (`~/.config/seek/config.toml`)

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
