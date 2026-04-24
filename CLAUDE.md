# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mac-seek** (`seek`) is a macOS CLI tool that finds files by natural language description. It bridges the gap between keyword-based Spotlight/mdfind and how people actually remember files — by context, situation, and content rather than exact filenames.

## Architecture

Single-file Python CLI (`seek.py`) with this pipeline:

1. **Query Analysis** — LLM extracts keyword sets, filename fragments, date/type hints, and context summary from natural language input
2. **Multi-pass mdfind + Image Index** — mdfind passes run in parallel via ThreadPoolExecutor; image index (SQLite FTS5) is queried in parallel; results merged and deduplicated, sorted by recency, capped at `max_candidates` (default 30, configurable)
3. **iCloud auto-download** — Candidates identified as iCloud stubs (not yet local) are downloaded via `brctl download` before content reading
4. **Content Reading** — Top `max_read_candidates` (default 15) by recency: reads text/md/code (10KB), PDF via `pdftotext` (5 pages), docx via `pandoc`, xlsx/csv (10KB). For indexed images, injects cached caption. Skips binary/large files (>5MB). Content previews capped at 500 chars for LLM payload efficiency.
5. **LLM Ranking** — LLM ranks top `top_results` (default 5) by semantic + contextual relevance, each result includes a confidence score (0-100%)
6. **Interactive Display** — Shows ranked results with confidence bars; user picks a number to `open` the file

## File Structure

```
seek.py                        # Main CLI — single-file, all logic here
tools/caption/
  seek-caption.swift           # Vision OCR+classification captioner source
  seek-caption                 # Compiled binary (gitignored — build with swiftc below)
requirements.txt               # Required: openai>=1.0
requirements-mlx.txt           # Optional: mlx-lm>=0.20.0 (Apple Silicon local inference)
.env                           # OPENROUTER_API_KEY (gitignored)
semantic-file-search-spec.md   # Original project spec
```

Runtime data (not in repo):
```
~/.config/seek/config.toml     # Index config: folders, extensions, size limit
~/.local/share/seek/index.db   # SQLite image caption index
```

## Commands

```bash
# Run from project dir
python seek.py "your natural language query"

# JSON output mode (machine-readable)
python seek.py --json "your query"

# If installed to PATH (~/.local/bin/seek):
seek "your natural language query"

# Image index management
seek index                     # incremental: caption new/changed images only
seek index --rebuild           # wipe and re-caption everything
seek index --status            # show row count and last indexed date
```

## Dependencies

```bash
make install            # Recommended: sets up venv, installs openai, builds caption helper, installs seek to PATH
make install-mlx        # Optional: adds mlx-lm + downloads Qwen3-1.7B-4bit for local inference
brew install poppler pandoc  # Optional — for PDF/docx content extraction
```

Manual install:
```bash
pip install openai           # Required
pip install mlx-lm           # Optional — Apple Silicon local inference only
cd tools/caption && swiftc -O seek-caption.swift -o seek-caption  # Optional — image captioning
```

**Note:** The caption helper uses Apple's Vision framework (OCR + image classification) which ships with macOS — no extra deps. Full generative captions via Foundation Models would require Xcode installed, which is not currently set up.

## Key Technical Decisions

- **LLM**: `google/gemini-2.0-flash-lite-001` via OpenRouter by default (`openai` SDK with custom `base_url`). Fast (~1s/call) and cheap. Get a key at https://openrouter.ai/keys
- **Local inference**: Set `provider = "mlx"` and `model = "mlx-community/Qwen3-1.7B-4bit"` in `[llm]` config for fully on-device inference via `mlx-lm`. No API key needed. Thinking mode auto-disabled via `enable_thinking=False` in chat template. Falls back gracefully to standard `apply_chat_template` for non-Qwen3 models.
- **API key**: `OPENROUTER_API_KEY` environment variable (stored in `.env`, gitignored). Skipped entirely when `provider = "mlx"`.
- **LLM config override** (highest → lowest priority):
  1. Env vars: `SEEK_LLM_MODEL`, `SEEK_LLM_BASE_URL`, `SEEK_LLM_API_KEY_ENV`
  2. `[llm]` section in `~/.config/seek/config.toml`
  3. Built-in defaults (OpenRouter + Gemini Flash free)
- **Switching providers**: Set `provider = "mlx"` for local inference, or set `base_url`/`model`/`api_key_env` for any OpenAI-compatible endpoint
- **Search limits**: Configurable via `[search]` in `~/.config/seek/config.toml` — `max_candidates` (default 30), `max_read_candidates` (default 15), `top_results` (default 5). All candidates sorted by recency; images no longer front-loaded.
- **JSON extraction**: `_extract_json()` helper robustly extracts the first JSON object/array from LLM output, stripping prose, fences, and thinking-mode preamble
- **Path filtering**: Extensive `SKIP_PATTERNS` set (top-level constant, shared by both search and indexer) excludes macOS system dirs, caches, build artifacts, package managers, IDE files, and Python/Node/Rust caches to keep candidates relevant
- **iCloud stubs**: Detected via `com.apple.fileprovider.fpfs#P` xattr; top candidates downloaded via `brctl download` in parallel before content reading, with 4s timeout per file
- **Image index**: SQLite at `~/.local/share/seek/index.db` with FTS5 virtual table. Caption = OCR text (if any) + Vision classification labels. Run `seek index` to populate; incremental by default (mtime comparison). Config at `~/.config/seek/config.toml`.
- **Caption helper**: `tools/caption/seek-caption` — Swift binary using Vision `VNRecognizeTextRequest` (OCR) + `VNClassifyImageRequest` (scene labels, threshold 0.1). Accepts multiple paths as args, outputs one JSON line per image. Invoked in batches of 20 from Python.
- **mdfind scoped to `~`** to avoid system files; each pass has 5-second timeout; all passes run in parallel
- **Graceful degradation**: pdftotext/pandoc are optional; warns once if missing, falls back to filename/metadata matching
- **`--json` flag**: outputs clean JSON to stdout, all status lines to stderr. Useful for scripting or future integrations
- **Installation**: symlinked to `~/.local/bin/seek` — shebang uses `#!/usr/bin/env python3`
- **Timing diagnostics**: printed to stderr for performance monitoring

## Future Enhancements

- Cache recent searches in SQLite for instant re-retrieval
- `seek --last` to re-show previous results
- Index file contents locally with embeddings for faster repeat searches
- Interactive mode: refine query conversationally if top results aren't right
