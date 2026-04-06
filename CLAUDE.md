# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mac-seek** (`seek`) is a macOS CLI tool that finds files by natural language description. It bridges the gap between keyword-based Spotlight/mdfind and how people actually remember files — by context, situation, and content rather than exact filenames.

## Architecture

Single-file Python CLI (`seek.py`) with this pipeline:

1. **Query Analysis** — LLM extracts keyword sets, filename fragments, date/type hints, and context summary from natural language input
2. **Multi-pass mdfind** — All passes run in parallel via ThreadPoolExecutor, merged and deduplicated, capped at 30 candidates
3. **Content Reading** — Top 15 candidates by recency: reads text/md/code (10KB), PDF via `pdftotext` (5 pages), docx via `pandoc`, xlsx/csv (10KB). Skips binary/large files (>5MB). Content previews capped at 500 chars for LLM payload efficiency.
4. **LLM Ranking** — LLM ranks top 5 by semantic + contextual relevance, each result includes a confidence score (0-100%)
5. **Interactive Display** — Shows ranked results with confidence bars; user picks a number to `open` the file

## File Structure

```
seek.py              # Main CLI — single-file, all logic here
raycast/seek.sh      # Raycast script command wrapper (alias: sk)
.env                 # DASHSCOPE_API_KEY (gitignored)
semantic-file-search-spec.md  # Original project spec
```

## Commands

```bash
# Run from project dir
python seek.py "your natural language query"

# JSON output (for programmatic use / future Raycast extension)
python seek.py --json "your query"

# If installed to PATH (~/.local/bin/seek):
seek "your natural language query"

# Raycast: type "sk" → Tab → type query → Enter
```

## Dependencies

```bash
pip install anthropic        # Required — used as SDK even with DashScope endpoint
brew install poppler pandoc  # Optional — for PDF/docx content extraction
```

## Key Technical Decisions

- **LLM**: `qwen3-coder-plus` via Alibaba DashScope Anthropic-compatible endpoint (`anthropic` SDK with custom `base_url`). Chosen over `qwen3.5-plus` for speed (no thinking overhead, ~2s vs ~7s per call)
- **API key**: `DASHSCOPE_API_KEY` environment variable (stored in `.env`, gitignored)
- **ThinkingBlock handling**: Some DashScope models return ThinkingBlock before TextBlock — response parsing filters for `type == "text"` blocks
- **Path filtering**: Extensive skip patterns exclude macOS system dirs, caches, build artifacts, package managers, IDE files, and Python/Node/Rust caches to keep candidates relevant
- **mdfind scoped to `~`** to avoid system files; each pass has 5-second timeout; all passes run in parallel
- **Graceful degradation**: pdftotext/pandoc are optional; warns once if missing, falls back to filename/metadata matching
- **Non-interactive mode**: skips the "Open file" prompt when stdin is not a TTY (e.g. Raycast)
- **`--json` flag**: outputs ranked results as JSON array for programmatic consumers (reserved for future Raycast extension)
- **Python path**: hardcoded to `/opt/homebrew/anaconda3/bin/python3` where `anthropic` SDK is installed
- **Installation**: symlinked to `~/.local/bin/seek`
- **Timing diagnostics**: printed to stderr for performance monitoring

## Raycast Integration

Script command at `raycast/seek.sh` — uses human-readable output (fullOutput mode), not `--json`. Add the `raycast/` directory as a Script Commands directory in Raycast settings. Set alias to `sk` for quick access.

## Future Enhancements

- Build a proper Raycast extension (TypeScript/React) with interactive list UI — select a result and press Enter to open the file. Use `--json` output for structured data
- Cache recent searches in SQLite for instant re-retrieval
- `seek --last` to re-show previous results
- Index file contents locally with embeddings for faster repeat searches
- Interactive mode: refine query conversationally if top results aren't right
