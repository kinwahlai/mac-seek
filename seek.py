#!/opt/homebrew/anaconda3/bin/python3
"""Semantic file search for macOS — find files by natural language description."""

import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic

MODEL = "qwen3-coder-plus"
BASE_URL = "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic"
HOME = str(Path.home())
MAX_CANDIDATES = 30
MAX_READ_CANDIDATES = 15
MAX_CONTENT_BYTES = 10240
MAX_FILE_SIZE = 5 * 1024 * 1024
MDFIND_TIMEOUT = 5

_warned_tools = set()


def warn_tool_missing(tool: str, install_hint: str):
    if tool not in _warned_tools:
        print(f"  Note: {tool} not installed. Install with: {install_hint}", file=sys.stderr)
        _warned_tools.add(tool)


def analyse_query(client: anthropic.Anthropic, user_query: str) -> dict:
    prompt = f'''Analyse this file search query. The user is trying to find a file on their Mac.

Query: "{user_query}"

Return JSON only, no explanation:
{{
  "keyword_sets": [
    ["most", "specific", "terms"],
    ["broader", "terms"],
    ["single_key_noun"]
  ],
  "filename_fragments": ["snaic", "notes"],
  "date_hint": "2025-03" or null,
  "file_type_hint": "document" or "code" or "spreadsheet" or null,
  "context_summary": "One sentence describing what the user is actually looking for, including situational context"
}}

Rules:
- keyword_sets: 2-4 sets, from most specific to broadest. Include proper nouns, acronyms, technical terms.
- filename_fragments: likely substrings in the filename (lowercase). Can be empty.
- date_hint: if the user mentions a time period, convert to YYYY-MM or YYYY format. null if no time reference.
- file_type_hint: infer from context. "notes I took" → document. "that script" → code. null if unclear.
- context_summary: preserve the user's situational context — "notes taken during an info session", "a playbook prepared for someone", etc. This helps with ranking.'''

    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next(b.text for b in resp.content if b.type == "text").strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)


def run_mdfind(query: str) -> list[str]:
    try:
        result = subprocess.run(
            ["mdfind", "-onlyin", HOME, query],
            capture_output=True, text=True, timeout=MDFIND_TIMEOUT,
        )
        return [p for p in result.stdout.strip().split("\n") if p]
    except (subprocess.TimeoutExpired, Exception):
        return []


def search_candidates(analysis: dict) -> list[str]:
    seen = set()
    results = []

    skip_dirs = {"/Library/", "/Caches/", "/logs/", "/.Trash/", "/.cache/",
                  "/node_modules/", "/.git/", "/CachedData/", "/CachedProfilesData/"}

    def add(paths: list[str]):
        for p in paths:
            if p not in seen and os.path.exists(p) and not any(s in p for s in skip_dirs):
                seen.add(p)
                results.append(p)

    keyword_sets = analysis.get("keyword_sets", [])

    # Pass 1-2: keyword sets
    for kw_set in keyword_sets:
        query = " ".join(kw_set)
        add(run_mdfind(query))
        if len(results) >= MAX_CANDIDATES:
            break

    # Pass 3: filename search
    for frag in analysis.get("filename_fragments", []):
        add(run_mdfind(f"kMDItemDisplayName == '*{frag}*'cd"))

    # Pass 4: date-scoped search
    date_hint = analysis.get("date_hint")
    if date_hint and keyword_sets:
        main_keyword = keyword_sets[0][0] if keyword_sets[0] else None
        if main_keyword:
            if len(date_hint) == 7:  # YYYY-MM
                year, month = date_hint.split("-")
                month_int = int(month)
                if month_int == 12:
                    end_date = f"{int(year) + 1}-01-01"
                else:
                    end_date = f"{year}-{month_int + 1:02d}-01"
                start_date = f"{date_hint}-01"
            else:  # YYYY
                start_date = f"{date_hint}-01-01"
                end_date = f"{int(date_hint) + 1}-01-01"
            date_query = (
                f"{main_keyword} && "
                f"kMDItemContentModificationDate >= $time.iso({start_date}) && "
                f"kMDItemContentModificationDate < $time.iso({end_date})"
            )
            add(run_mdfind(date_query))

    # Sort by modification date (most recent first) and cap
    results.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return results[:MAX_CANDIDATES]


def read_file_content(path: str) -> str | None:
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            return None
    except OSError:
        return None

    ext = Path(path).suffix.lower()

    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)

    # Text-like files: try reading as UTF-8
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(MAX_CONTENT_BYTES)
    except (OSError, PermissionError):
        return None


def _read_pdf(path: str) -> str | None:
    try:
        result = subprocess.run(
            ["pdftotext", "-l", "5", path, "-"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout[:MAX_CONTENT_BYTES]
    except FileNotFoundError:
        warn_tool_missing("pdftotext", "brew install poppler")
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


def _read_docx(path: str) -> str | None:
    try:
        result = subprocess.run(
            ["pandoc", "-t", "plain", path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout[:MAX_CONTENT_BYTES]
    except FileNotFoundError:
        warn_tool_missing("pandoc", "brew install pandoc")
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


def build_candidates_info(paths: list[str]) -> list[dict]:
    candidates = []
    for i, path in enumerate(paths[:MAX_READ_CANDIDATES]):
        try:
            stat = os.stat(path)
        except OSError:
            continue

        content = read_file_content(path)
        info = {
            "index": i,
            "filename": os.path.basename(path),
            "path": path,
            "modified": _format_date(stat.st_mtime),
            "size": _format_size(stat.st_size),
        }
        if content:
            info["content_preview"] = content[:500]
        candidates.append(info)
    return candidates


def rank_candidates(
    client: anthropic.Anthropic,
    user_query: str,
    context_summary: str,
    candidates: list[dict],
) -> list[dict]:
    candidates_json = json.dumps(candidates, indent=2, default=str)
    prompt = f'''The user is looking for a file on their Mac.

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
{candidates_json}'''

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)


def _format_date(timestamp: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def display_results(
    user_query: str,
    analysis: dict,
    candidates: list[dict],
    rankings: list[dict],
):
    print()
    keywords_display = " | ".join(
        " ".join(ks) for ks in analysis.get("keyword_sets", [])
    )
    filenames = analysis.get("filename_fragments", [])
    if filenames:
        keywords_display += " | filename:" + ",".join(filenames)
    print(f'Searching for: "{user_query}"')
    print(f"Keywords: {keywords_display}")
    print(f"Found {len(candidates)} candidates, reading content...")
    print()
    print("Top results:")
    print()

    result_paths = []
    for r in rankings:
        idx = r["index"]
        c = next((c for c in candidates if c["index"] == idx), None)
        if not c:
            continue
        rank = r["rank"]
        result_paths.append(c["path"])
        print(f"  {rank}. {c['path']}")
        print(f"     Modified: {c['modified']}  |  Size: {c['size']}")
        print(f"     → {r['reason']}")
        print()

    if not result_paths:
        print("  No relevant results found. Try different search terms.")
        return

    # Interactive selection
    while True:
        try:
            choice = input(f"Open file [1-{len(result_paths)}] or [q]uit: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice.lower() in ("q", "quit", ""):
            break
        try:
            num = int(choice)
            if 1 <= num <= len(result_paths):
                subprocess.run(["open", result_paths[num - 1]])
            else:
                print(f"  Enter 1-{len(result_paths)} or q")
        except ValueError:
            print(f"  Enter 1-{len(result_paths)} or q")


def main():
    args = sys.argv[1:]
    json_mode = "--json" in args
    if json_mode:
        args.remove("--json")

    if not args:
        print("Usage: seek <natural language description>")
        print('Example: seek "the grants playbook I made for Frank"')
        sys.exit(1)

    user_query = " ".join(args)

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("Error: DASHSCOPE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    import time
    t0 = time.time()

    client = anthropic.Anthropic(api_key=api_key, base_url=BASE_URL)

    # Step 1: Analyse query
    print(f'Searching for: "{user_query}"')
    t1 = time.time()
    analysis = analyse_query(client, user_query)
    print(f"  [analyse: {time.time()-t1:.1f}s]", file=sys.stderr)

    keywords_display = " | ".join(
        " ".join(ks) for ks in analysis.get("keyword_sets", [])
    )
    filenames = analysis.get("filename_fragments", [])
    if filenames:
        keywords_display += " | filename:" + ",".join(filenames)
    print(f"Keywords: {keywords_display}")

    # Step 2: Multi-pass mdfind
    t2 = time.time()
    paths = search_candidates(analysis)
    print(f"  [mdfind: {time.time()-t2:.1f}s]", file=sys.stderr)
    if not paths:
        print("\nNo files found. Try different search terms.")
        sys.exit(0)

    print(f"Found {len(paths)} candidates, reading content...")

    # Step 3: Read candidates
    t3 = time.time()
    candidates = build_candidates_info(paths)
    print(f"  [read files: {time.time()-t3:.1f}s]", file=sys.stderr)
    if not candidates:
        print("\nCouldn't read any candidate files.")
        sys.exit(0)

    # Step 4: Rank with LLM
    context_summary = analysis.get("context_summary", user_query)
    t4 = time.time()
    rankings = rank_candidates(client, user_query, context_summary, candidates)
    print(f"  [rank: {time.time()-t4:.1f}s]", file=sys.stderr)
    print(f"  [total: {time.time()-t0:.1f}s]", file=sys.stderr)

    # Step 5: Build results
    results = []
    for r in rankings:
        idx = r["index"]
        c = next((c for c in candidates if c["index"] == idx), None)
        if not c:
            continue
        results.append({
            "rank": r["rank"],
            "path": c["path"],
            "filename": c["filename"],
            "modified": c["modified"],
            "size": c["size"],
            "reason": r["reason"],
        })

    # JSON mode for Raycast / programmatic use
    if json_mode:
        print(json.dumps(results, indent=2))
        return

    # Display results
    print()
    print("Top results:")
    print()

    if not results:
        print("  No relevant results found. Try different search terms.")
        sys.exit(0)

    for r in results:
        print(f"  {r['rank']}. {r['path']}")
        print(f"     Modified: {r['modified']}  |  Size: {r['size']}")
        print(f"     → {r['reason']}")
        print()

    # Interactive prompt only in terminal
    if not sys.stdin.isatty():
        return

    result_paths = [r["path"] for r in results]
    while True:
        try:
            choice = input(f"Open file [1-{len(result_paths)}] or [q]uit: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice.lower() in ("q", "quit", ""):
            break
        try:
            num = int(choice)
            if 1 <= num <= len(result_paths):
                subprocess.run(["open", result_paths[num - 1]])
            else:
                print(f"  Enter 1-{len(result_paths)} or q")
        except ValueError:
            print(f"  Enter 1-{len(result_paths)} or q")


if __name__ == "__main__":
    main()
