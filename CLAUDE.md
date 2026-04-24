# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mac-seek** (`seek`) is a macOS CLI tool that finds files by natural language description. It bridges the gap between keyword-based Spotlight/mdfind and how people actually remember files — by context, situation, and content rather than exact filenames.

## Architecture

Single-file Python CLI (`seek.py`) with this pipeline:

1. **Query Analysis** — LLM extracts keyword sets, filename fragments, date/type hints, and context summary from natural language input
2. **Multi-pass mdfind + Image Index** — mdfind passes run in parallel via ThreadPoolExecutor; image index (SQLite FTS5) is queried in parallel; results merged and deduplicated, capped at 30 candidates
3. **iCloud auto-download** — Candidates identified as iCloud stubs (not yet local) are downloaded via `brctl download` before content reading
4. **Content Reading** — Top 15 candidates by recency: reads text/md/code (10KB), PDF via `pdftotext` (5 pages), docx via `pandoc`, xlsx/csv (10KB). For indexed images, injects cached caption. Skips binary/large files (>5MB). Content previews capped at 500 chars for LLM payload efficiency.
5. **LLM Ranking** — LLM ranks top 5 by semantic + contextual relevance, each result includes a confidence score (0-100%)
6. **Interactive Display** — Shows ranked results with confidence bars; user picks a number to `open` the file

## File Structure

```
seek.py                        # Main CLI — single-file, all logic here
tools/caption/
  seek-caption.swift           # Vision OCR+classification captioner source
  seek-caption                 # Compiled binary (gitignored — build with swiftc below)
raycast/seek.sh                # Raycast script command wrapper (alias: sk)
raycast-extension/             # Raycast extension (TypeScript/React)
  src/seek.tsx                 # Extension source — List with detail panel
  package.json                 # Extension manifest and dependencies
  assets/command-icon.png      # Extension icon
.env                           # DASHSCOPE_API_KEY (gitignored)
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

# JSON output (used by Raycast extension)
python seek.py --json "your query"

# If installed to PATH (~/.local/bin/seek):
seek "your natural language query"

# Image index management
seek index                     # incremental: caption new/changed images only
seek index --rebuild           # wipe and re-caption everything
seek index --status            # show row count and last indexed date

# Raycast extension: build production
cd raycast-extension && npm run build

# Raycast extension: development mode
cd raycast-extension && npm run dev
```

## Dependencies

```bash
pip install anthropic        # Required — used as SDK even with DashScope endpoint
brew install poppler pandoc  # Optional — for PDF/docx content extraction

# Build the caption helper (one-time, needs macOS CLI tools with Swift)
cd tools/caption && swiftc -O seek-caption.swift -o seek-caption

# Raycast extension
cd raycast-extension && npm install
```

**Note:** The caption helper uses Apple's Vision framework (OCR + image classification) which ships with macOS — no extra deps. Full generative captions via Foundation Models would require Xcode installed, which is not currently set up.

## Key Technical Decisions

- **LLM**: `qwen3-coder-plus` via Alibaba DashScope Anthropic-compatible endpoint (`anthropic` SDK with custom `base_url`). Chosen over `qwen3.5-plus` for speed (no thinking overhead, ~2s vs ~7s per call)
- **API key**: `DASHSCOPE_API_KEY` environment variable (stored in `.env`, gitignored)
- **ThinkingBlock handling**: Some DashScope models return ThinkingBlock before TextBlock — response parsing filters for `type == "text"` blocks
- **Path filtering**: Extensive `SKIP_PATTERNS` set (top-level constant, shared by both search and indexer) excludes macOS system dirs, caches, build artifacts, package managers, IDE files, and Python/Node/Rust caches to keep candidates relevant
- **iCloud stubs**: Detected via `com.apple.fileprovider.fpfs#P` xattr; top candidates downloaded via `brctl download` in parallel before content reading, with 4s timeout per file
- **Image index**: SQLite at `~/.local/share/seek/index.db` with FTS5 virtual table. Caption = OCR text (if any) + Vision classification labels. Run `seek index` to populate; incremental by default (mtime comparison). Config at `~/.config/seek/config.toml`.
- **Caption helper**: `tools/caption/seek-caption` — Swift binary using Vision `VNRecognizeTextRequest` (OCR) + `VNClassifyImageRequest` (scene labels, threshold 0.1). Accepts multiple paths as args, outputs one JSON line per image. Invoked in batches of 20 from Python.
- **mdfind scoped to `~`** to avoid system files; each pass has 5-second timeout; all passes run in parallel
- **Graceful degradation**: pdftotext/pandoc are optional; warns once if missing, falls back to filename/metadata matching
- **`--json` flag**: outputs clean JSON to stdout, all status lines to stderr. Used by the Raycast extension
- **Python path**: hardcoded to `/opt/homebrew/anaconda3/bin/python3` where `anthropic` SDK is installed
- **Installation**: symlinked to `~/.local/bin/seek`
- **Timing diagnostics**: printed to stderr for performance monitoring

## Raycast Integration

Two options available:

### Script Command (simple)
`raycast/seek.sh` — text output in fullOutput mode. Add the `raycast/` directory as a Script Commands directory in Raycast settings. Set alias to `sk`.

### Extension (interactive)
`raycast-extension/` — TypeScript/React extension with:
- In-view search bar with 1.5s debounce (prevents cancellation during ~10s search)
- Interactive list with confidence-colored icons and % tags
- Detail panel showing LLM reasoning, full path, and metadata
- Actions: Open File, Show in Finder, Copy Path, Open in VS Code
- `npm run dev` to register in Raycast Development section
- `npm run build` for production build

## Future Enhancements

- Cache recent searches in SQLite for instant re-retrieval
- `seek --last` to re-show previous results
- Index file contents locally with embeddings for faster repeat searches
- Interactive mode: refine query conversationally if top results aren't right
