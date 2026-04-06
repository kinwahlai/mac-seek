# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mac-seek** (`seek`) is a macOS CLI tool that finds files by natural language description. It bridges the gap between keyword-based Spotlight/mdfind and how people actually remember files — by context, situation, and content rather than exact filenames.

## Architecture

Single-file Python CLI (`seek.py`) with this pipeline:

1. **Query Analysis** — LLM extracts keyword sets, filename fragments, date/type hints, and context summary from natural language input
2. **Multi-pass mdfind** — 3-4 passes (specific terms → broad terms → filename → date-scoped), merged and deduplicated, capped at 50 candidates
3. **Content Reading** — Top 30 candidates by recency: reads text/md/code (10KB), PDF via `pdftotext` (5 pages), docx via `pandoc`, xlsx/csv (10KB). Skips binary/large files (>5MB)
4. **LLM Ranking** — LLM ranks top 5 by semantic + contextual relevance
5. **Interactive Display** — Shows ranked results; user picks a number to `open` the file

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

# JSON output (for programmatic use / Raycast)
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

- **LLM**: `qwen3.5-plus` via Alibaba DashScope Anthropic-compatible endpoint (`anthropic` SDK with custom `base_url`)
- **API key**: `DASHSCOPE_API_KEY` environment variable (stored in `.env`, gitignored)
- **ThinkingBlock handling**: DashScope models return ThinkingBlock before TextBlock — response parsing filters for `type == "text"` blocks
- **mdfind scoped to `~`** to avoid system files; each pass has 5-second timeout
- **Graceful degradation**: pdftotext/pandoc are optional; warns once if missing, falls back to filename/metadata matching
- **Non-interactive mode**: skips the "Open file" prompt when stdin is not a TTY (e.g. Raycast)
- **`--json` flag**: outputs ranked results as JSON array for programmatic consumers
- **Installation**: symlinked to `~/.local/bin/seek` (not `~/bin`)

## Raycast Integration

Script command at `raycast/seek.sh` — add the `raycast/` directory as a Script Commands directory in Raycast settings. Set alias to `sk` for quick access. Outputs full text results (mode: fullOutput).

## Future Enhancements

- Build a proper Raycast extension (TypeScript/React) with interactive list UI — select a result and press Enter to open the file, instead of text-only output
- Cache recent searches in SQLite for instant re-retrieval
- `seek --last` to re-show previous results
- Index file contents locally with embeddings for faster repeat searches
- Interactive mode: refine query conversationally if top results aren't right
