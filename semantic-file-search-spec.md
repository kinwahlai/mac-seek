# Semantic File Search CLI — Project Spec

## What this is

A macOS CLI tool that finds files you can't name but can describe. You type a natural language description — including situational context like "during that meeting" or "notes I took at the onboarding session" — and it searches your filesystem, reads candidate content, and uses an LLM to rank results by semantic relevance.

## Why

Spotlight and `mdfind` are keyword-only. When you remember the context but not the filename — "a file I wrote during the onboarding session about the team structure" — they fail. This bridges that gap.

## Example queries

```
$ seek "the budget spreadsheet I put together for the Q3 review"
$ seek "notes I took during the onboarding session about the team structure"
$ seek "that python script for parsing the weekly CSV exports"
$ seek "the slide deck from last month's product planning meeting"
```

## How it works

1. **Analyse query** — Use Haiku to extract two things from the natural language query:
   - **Search keyword sets** — multiple sets for multi-pass search (see below)
   - **Context clues** — date hints, file type hints, situational context (e.g. "during a session" suggests notes/meeting docs)

2. **Multi-pass mdfind** — Run multiple searches and merge results (deduplicated by path):
   - Pass 1: Specific terms combined (e.g. `mdfind "onboarding team structure"`)
   - Pass 2: Key noun alone (e.g. `mdfind "onboarding"`)
   - Pass 3: Filename search (e.g. `mdfind "kMDItemDisplayName == '*onboarding*'cd"`)
   - Pass 4 (if date clues): Add date filter
   - Scope all passes to `~` to avoid system files
   - Collect up to 50 unique candidates across all passes

3. **Read candidates (deep)** — For the top 30 candidates (sorted by modification date, most recent first):
   - Plain text/markdown/code: read first 10KB
   - PDF: extract text via `pdftotext` (first 5 pages), fall back to filename-only if not available
   - .docx: extract text via `pandoc -t plain` (first 10KB), fall back to filename-only if not available
   - .xlsx/.csv: read first 10KB
   - Binary/image/video: skip content, use filename + metadata only
   - Collect: filename, full path, modified date, file size, content preview

4. **Rank with LLM** — Send the original query (including all situational context) + candidate list to Haiku. Ask it to rank top 5 by semantic relevance, considering both content match AND contextual match (e.g. "looks like session notes" even if keywords don't perfectly align).

5. **Display results** — Show ranked results with path, modified date, relevance explanation. User can type a number to open the file with `open`.

## Technical details

- **Language:** Python 3 (single file, no framework)
- **LLM:** Any OpenAI-compatible endpoint via `openai` Python SDK
  - Default: `google/gemini-2.0-flash-lite-001` via [OpenRouter](https://openrouter.ai) (configurable)
  - API key and model configured in `~/.config/seek/config.toml` or env vars
- **macOS APIs:** `mdfind` via subprocess
- **File reading:** UTF-8 with error ignoring. Skip files > 5MB.
- **Rich format extraction:** `pdftotext` and `pandoc` via subprocess. Optional — if not installed, fall back to filename/metadata only and print a one-time warning suggesting `brew install poppler pandoc`.
- **Config:** None needed for v1. Hardcode sane defaults.

## Prompt templates

### Query analysis prompt

```
Analyse this file search query. The user is trying to find a file on their Mac.

Query: "{user_query}"

Return JSON only, no explanation:
{{
  "keyword_sets": [
    ["most", "specific", "terms"],
    ["broader", "terms"],
    ["single_key_noun"]
  ],
  "filename_fragments": ["onboarding", "notes"],
  "date_hint": "2025-03" or null,
  "file_type_hint": "document" or "code" or "spreadsheet" or null,
  "context_summary": "One sentence describing what the user is actually looking for, including situational context"
}}

Rules:
- keyword_sets: 2-4 sets, from most specific to broadest. Include proper nouns, acronyms, technical terms.
- filename_fragments: likely substrings in the filename (lowercase). Can be empty.
- date_hint: if the user mentions a time period, convert to YYYY-MM or YYYY format. null if no time reference.
- file_type_hint: infer from context. "notes I took" → document. "that script" → code. null if unclear.
- context_summary: preserve the user's situational context — "notes taken during an info session", "a playbook prepared for someone", etc. This helps with ranking.
```

### Ranking prompt

```
The user is looking for a file on their Mac.

Their description: "{user_query}"
Context: {context_summary}

Below are candidate files found on their machine. Rank the top 5 by how likely each is to be the file the user is describing.

Consider:
- Content match: does the file content match what the user describes?
- Context match: does the file look like the TYPE of document the user describes (e.g. notes, playbook, script)?
- Recency: if the user implies a time period, prefer files from that period
- Path clues: folder names can indicate project or context

Return JSON only:
[
  {{"rank": 1, "index": 0, "reason": "One-line explanation"}},
  ...
]

If fewer than 5 candidates seem relevant, return only the relevant ones.

Candidates:
{candidates_json}
```

## mdfind details

```bash
# Pass 1: Specific multi-keyword
mdfind -onlyin ~ "onboarding team structure"

# Pass 2: Broader single-keyword
mdfind -onlyin ~ "onboarding"

# Pass 3: Filename search (case-insensitive, diacritic-insensitive)
mdfind -onlyin ~ "kMDItemDisplayName == '*onboarding*'cd"

# Pass 4: Date-scoped (if date_hint present, e.g. 2025-03)
mdfind -onlyin ~ "onboarding && kMDItemContentModificationDate >= $time.iso(2025-03-01) && kMDItemContentModificationDate < $time.iso(2025-04-01)"
```

Each pass has a 5-second timeout. Results are merged and deduplicated by absolute path.

## Output format

```
Searching for: "notes I took during the onboarding session about the team structure"
Keywords: onboarding, team structure | onboarding | filename:onboarding
Found 18 candidates, reading content...

Top results:

1. ~/Documents/Work/onboarding-notes.md
   Modified: 2025-03-12  |  Size: 4.2 KB  |  Confidence: ██████████ 95%
   → Notes from onboarding session covering team structure, reporting lines, and key contacts

2. ~/Documents/Work/team-overview.md
   Modified: 2025-03-14  |  Size: 8.1 KB  |  Confidence: ████████░░ 80%
   → Team structure document referenced during onboarding

3. ~/Desktop/org-chart-draft.pdf
   Modified: 2025-03-11  |  Size: 2.3 KB  |  Confidence: ██████░░░░ 60%
   → Org chart PDF shared during onboarding week

Open file [1-5] or [q]uit:
```

## Edge cases

- No results from any mdfind pass → tell the user, suggest they try different terms
- mdfind returns > 100 results across passes → take 50 most recently modified
- File no longer exists at found path → skip silently
- Permission denied → skip silently
- pdftotext/pandoc not installed → warn once, fall back to filename/metadata matching
- LLM returns fewer than 5 results → display what it returns, that's fine
- Query is too vague (e.g. "that file") → Haiku should still try, but warn if keyword sets are weak

## Installation

```bash
pip install openai
# Optional but recommended for rich file search:
brew install poppler pandoc
# Symlink to PATH
ln -sf "$(pwd)/seek.py" ~/.local/bin/seek
chmod +x seek.py
# Set your OpenRouter API key in ~/.config/seek/config.toml or env
```

## Stretch goals (don't build yet)

- Cache recent searches in SQLite for instant re-retrieval
- `seek --last` to re-show previous results
- Index file contents locally with embeddings for faster repeat searches
- Search inside email (`.emlx` in `~/Library/Mail`)
- Interactive mode: if top results aren't right, refine query conversationally
- Watch mode: "tell me when a file matching this description appears"

## Success criteria

- Sub-8-second response for typical queries (2 LLM calls + mdfind + file reading)
- Finds the right file in top 3 results at least 80% of the time
- Total cost per search: < $0.02 (Haiku pricing, two calls)
- Works with zero config — just needs an OpenRouter API key in `~/.config/seek/config.toml`
