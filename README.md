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

### 1. Get a Google AI Studio API key

[aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free account, free-tier-friendly.

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

Export your API key:
```bash
export GEMINI_API_KEY="..."
```

Or paste it into `~/.config/seek/config.toml` (created automatically on first run):

```toml
[llm]
api_key = "..."
```

Full config reference:

```toml
[llm]
api_key_env = "GEMINI_API_KEY"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-2.5-flash-lite"

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
| Env var | `SEEK_LLM_MODEL=gemini-2.5-flash seek "..."` |
| Config `[llm]` section | `model = "gemini-2.5-flash"` in config.toml |
| Built-in default | `gemini-2.5-flash-lite` via Google AI Studio |

Override env vars: `SEEK_LLM_MODEL`, `SEEK_LLM_BASE_URL`, `SEEK_LLM_API_KEY_ENV`.

### Switching providers

Any OpenAI-compatible endpoint works. Edit `[llm]` in `~/.config/seek/config.toml`:

```toml
# Bigger Gemini model (still free tier, double the RPM)
model = "gemini-2.5-flash"

# OpenAI
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"

# OpenRouter
base_url = "https://openrouter.ai/api/v1"
model = "google/gemini-2.0-flash-lite-001"
api_key_env = "OPENROUTER_API_KEY"
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
| `openai` (pip) | Yes | OpenAI-compatible LLM API |
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
