#!/usr/bin/env python3
"""Semantic file search for macOS — find files by natural language description."""

import json
import os
import sqlite3
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI
HOME = str(Path.home())
MAX_CANDIDATES = 30       # default, overridden by [search] config
MAX_READ_CANDIDATES = 15  # default, overridden by [search] config
MAX_TOP_RESULTS = 5       # default, overridden by [search] config
MAX_CONTENT_BYTES = 10240
MAX_FILE_SIZE = 5 * 1024 * 1024
MDFIND_TIMEOUT = 5

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tiff"}
TYPE_EXTS: dict[str, set[str]] = {
    "document":   {".pdf", ".docx", ".doc", ".txt", ".md", ".pages", ".odt", ".rtf"},
    "spreadsheet": {".xlsx", ".xls", ".csv", ".numbers", ".ods"},
    "image":      IMAGE_EXTS,
    "code":       {".py", ".js", ".ts", ".swift", ".go", ".rb", ".java", ".c", ".cpp", ".h", ".rs", ".sh"},
}
INDEX_DB = Path.home() / ".local/share/seek/index.db"
CONFIG_FILE = Path.home() / ".config/seek/config.toml"
CAPTION_BINARY = Path(__file__).resolve().parent / "tools/caption/seek-caption"
CAPTION_BATCH = 20

DEFAULT_CONFIG = """\
# Google AI Studio free tier.
# Get a key at https://aistudio.google.com/apikey and set GEMINI_API_KEY in your env,
# or paste the key as api_key below.
[llm]
api_key_env = "GEMINI_API_KEY"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-2.5-flash-lite"

[index]
folders = [
  "~/Downloads",
  "~/Desktop",
]
extensions = ["jpg", "jpeg", "png", "heic", "gif", "webp", "tiff"]
max_image_bytes = 20_000_000

[search]
max_candidates = 30      # total candidates collected from mdfind + image index
max_read_candidates = 15 # how many files to read content for (most recent first)
top_results = 5          # how many ranked results to return
skip_dirs = ["~/dev_repo"]   # directory trees to exclude entirely from search results
"""

SKIP_PATTERNS = {
    "/Library/", "/.Trash/", "/.Spotlight-V100/", "/.fseventsd/",
    "/Photos Library.photoslibrary/",
    "/Caches/", "/cache/", "/.cache/", "/logs/", "/CachedData/",
    "/CachedProfilesData/", "/CachedExtensions/",
    "/.git/", "/.svn/", "/.hg/",
    "/__pycache__/", "/.venv/", "/venv/", "/site-packages/",
    "/.eggs/", "/.tox/", "/.mypy_cache/", "/.pytest_cache/",
    "/node_modules/", "/.next/", "/dist/", "/.nuxt/", "/bower_components/",
    "/build/", "/.build/", "/DerivedData/",
    "/.idea/", "/.vscode/", "/.eclipse/",
    "/.npm/", "/.yarn/", "/.pnpm/", "/.cargo/", "/.rustup/",
    "/.gradle/", "/.m2/", "/.pub-cache/", "/Pods/",
    "/.docker/", "/tmp/", "/.local/share/",
}

_warned_tools = set()


def warn_tool_missing(tool: str, install_hint: str):
    if tool not in _warned_tools:
        print(f"  Note: {tool} not installed. Install with: {install_hint}", file=sys.stderr)
        _warned_tools.add(tool)


# ── iCloud helpers ──────────────────────────────────────────────────────────

def _is_icloud_stub(path: str) -> bool:
    """Return True if the file is an iCloud stub (not yet downloaded locally)."""
    try:
        r = subprocess.run(
            ["xattr", "-p", "com.apple.fileprovider.fpfs#P", path],
            capture_output=True, timeout=0.5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ensure_local(path: str, timeout: float = 4.0) -> bool:
    """Trigger iCloud download if the file is a stub. Returns True if file is local."""
    if not _is_icloud_stub(path):
        return True
    try:
        subprocess.run(["brctl", "download", path], capture_output=True, timeout=timeout)
        return not _is_icloud_stub(path)
    except (subprocess.TimeoutExpired, Exception):
        return False


# ── SQLite image index ──────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection | None:
    try:
        INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(INDEX_DB))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                caption TEXT NOT NULL,
                indexed_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS images_fts
                USING fts5(path, caption, content='images', content_rowid='rowid');
            CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
                INSERT INTO images_fts(rowid, path, caption)
                VALUES (new.rowid, new.path, new.caption);
            END;
            CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, path, caption)
                VALUES ('delete', old.rowid, old.path, old.caption);
            END;
            CREATE TRIGGER IF NOT EXISTS images_au AFTER UPDATE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, path, caption)
                VALUES ('delete', old.rowid, old.path, old.caption);
                INSERT INTO images_fts(rowid, path, caption)
                VALUES (new.rowid, new.path, new.caption);
            END;
        """)
        conn.commit()
        return conn
    except Exception:
        return None


# ── JSON extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str, expected: str = "auto") -> str:
    """Extract a JSON object/array from text. expected: 'object', 'array', or 'auto'."""
    import re
    text = text.strip()
    # Strip <think>...</think> blocks (Qwen3, DeepSeek-R1, etc.) — even unclosed ones
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    brace = text.find("{")
    bracket = text.find("[")
    if brace == -1 and bracket == -1:
        return text
    if expected == "object":
        pairs = [("{", "}")]
    elif expected == "array":
        pairs = [("[", "]")]
    elif brace == -1 or (bracket != -1 and bracket < brace):
        pairs = [("[", "]"), ("{", "}")]
    else:
        pairs = [("{", "}"), ("[", "]")]
    for open_ch, close_ch in pairs:
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return text


# ── LLM call ─────────────────────────────────────────────────────────────────

def _llm_call_json(
    provider: dict,
    max_tokens: int,
    messages: list[dict],
    expected: str,
):
    """Call the LLM and parse JSON of the expected shape ('object' or 'array').
    Raises on HTTP error, parse error, or shape mismatch."""
    resp = provider["client"].chat.completions.create(
        model=provider["model"], max_tokens=max_tokens, messages=messages,
    )
    text = resp.choices[0].message.content.strip()
    parsed = json.loads(_extract_json(text, expected=expected))
    if expected == "object" and not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    if expected == "array" and not isinstance(parsed, list):
        raise ValueError(f"expected JSON array, got {type(parsed).__name__}")
    return parsed


# ── Query analysis ──────────────────────────────────────────────────────────

def analyse_query(provider: dict, user_query: str) -> dict:
    prompt = f'''Analyse this file search query. The user is trying to find a file on their Mac.

Query: "{user_query}"

Return JSON only with this shape:
{{
  "keyword_sets": [[<specific terms>], [<broader terms>], [<single noun>]],
  "filename_fragments": [<lowercase substrings, or empty list>],
  "date_hint": <"YYYY-MM" or "YYYY" or null>,
  "file_type_hint": <"document" or "code" or "spreadsheet" or "image" or null>,
  "context_summary": <one sentence describing what the user is looking for>
}}

Worked example — for the UNRELATED query "the slides Anjali made for the all-hands last March":
{{
  "keyword_sets": [["Anjali", "all-hands", "slides"], ["all-hands", "presentation"], ["slides"]],
  "filename_fragments": ["all-hands", "slides", "anjali"],
  "date_hint": "2026-03",
  "file_type_hint": "document",
  "context_summary": "A slide deck Anjali prepared for an all-hands gathering in March."
}}

Rules:
- Derive every value from the user's query above. Do NOT reuse any strings from the example.
- keyword_sets: 2-4 sets, most specific first. Include proper nouns, acronyms, technical terms.
- filename_fragments: lowercase substrings likely to appear in the filename. Empty list if unclear.
- date_hint: only when a time period is mentioned, otherwise null.
- file_type_hint: infer from query language; null if unclear.
- context_summary: preserve the user's situational framing.'''

    return _llm_call_json(provider, 512, [{"role": "user", "content": prompt}], expected="object")


# ── File discovery ──────────────────────────────────────────────────────────

def run_mdfind(query: str) -> list[str]:
    try:
        result = subprocess.run(
            ["mdfind", "-onlyin", HOME, query],
            capture_output=True, text=True, timeout=MDFIND_TIMEOUT,
        )
        return [p for p in result.stdout.strip().split("\n") if p]
    except (subprocess.TimeoutExpired, Exception):
        return []


def search_candidates(
    analysis: dict,
    db: sqlite3.Connection | None = None,
    max_candidates: int = MAX_CANDIDATES,
    skip_dirs: list[str] | None = None,
) -> list[str]:
    if skip_dirs is None:
        skip_dirs = []
    seen: set[str] = set()
    fn_hits: list[str] = []       # filename-fragment matches — always surfaced
    image_hits: list[str] = []    # SQLite FTS image caption matches
    kw_hits: list[str] = []       # keyword / date mdfind results

    def _valid(p: str) -> bool:
        return (p not in seen
                and not any(s in p for s in SKIP_PATTERNS)
                and not any(p.startswith(sd) for sd in skip_dirs)
                and os.path.isfile(p))

    def _add(bucket: list[str], paths: list[str]):
        for p in paths:
            if _valid(p):
                seen.add(p)
                bucket.append(p)

    keyword_sets = analysis.get("keyword_sets", [])
    filename_fragments = analysis.get("filename_fragments", [])

    kw_queries = [" ".join(ks) for ks in keyword_sets]
    fn_queries = [f"kMDItemDisplayName == '*{frag}*'cd" for frag in filename_fragments]

    date_hint = analysis.get("date_hint")
    if date_hint and keyword_sets:
        main_keyword = keyword_sets[0][0] if keyword_sets[0] else None
        if main_keyword:
            if len(date_hint) == 7:
                year, month = date_hint.split("-")
                month_int = int(month)
                if month_int == 12:
                    end_date = f"{int(year) + 1}-01-01"
                else:
                    end_date = f"{year}-{month_int + 1:02d}-01"
                start_date = f"{date_hint}-01"
            else:
                start_date = f"{date_hint}-01-01"
                end_date = f"{int(date_hint) + 1}-01-01"
            kw_queries.append(
                f"{main_keyword} && "
                f"kMDItemContentModificationDate >= $time.iso({start_date}) && "
                f"kMDItemContentModificationDate < $time.iso({end_date})"
            )

    all_queries = kw_queries + fn_queries
    if db is not None:
        fts_terms = list(dict.fromkeys(
            w for ks in keyword_sets for w in ks if len(w) >= 2
        ))
        if fts_terms:
            try:
                fts_query = " OR ".join(fts_terms)
                rows = db.execute(
                    "SELECT path FROM images_fts WHERE images_fts MATCH ? ORDER BY rank LIMIT 15",
                    (fts_query,),
                ).fetchall()
                _add(image_hits, [row[0] for row in rows])
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=len(all_queries) or 1) as pool:
        kw_futures = [pool.submit(run_mdfind, q) for q in kw_queries]
        fn_futures = [pool.submit(run_mdfind, q) for q in fn_queries]
        for f in fn_futures:
            _add(fn_hits, f.result())
        for f in kw_futures:
            _add(kw_hits, f.result())

    file_type_hint = analysis.get("file_type_hint")
    is_visual = file_type_hint == "image"

    # fn_hits and image_hits bypass recency sort — they're always surfaced first
    kw_sorted = sorted(kw_hits, key=lambda p: os.path.getmtime(p), reverse=True)

    if is_visual:
        combined = image_hits + fn_hits + kw_sorted
    else:
        combined = fn_hits + image_hits + kw_sorted
        # Within the bulk, surface files matching the hinted type first
        if file_type_hint and file_type_hint in TYPE_EXTS:
            target_exts = TYPE_EXTS[file_type_hint]
            priority = fn_hits  # already in front, keep order
            bulk = image_hits + kw_sorted
            bulk_matched = [p for p in bulk if Path(p).suffix.lower() in target_exts]
            bulk_others = [p for p in bulk if Path(p).suffix.lower() not in target_exts]
            combined = list(dict.fromkeys(priority + bulk_matched + bulk_others))

    return list(dict.fromkeys(combined))[:max_candidates]


# ── Content reading ─────────────────────────────────────────────────────────

def read_file_content(path: str, db: sqlite3.Connection | None = None) -> str | None:
    ext = Path(path).suffix.lower()

    # Return cached caption for indexed images
    if ext in IMAGE_EXTS:
        if db is not None:
            try:
                row = db.execute("SELECT caption FROM images WHERE path = ?", (path,)).fetchone()
                if row:
                    return f"[image] {row[0]}"
            except Exception:
                pass
        return None

    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            return None
    except OSError:
        return None

    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)

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


def build_candidates_info(paths: list[str], db: sqlite3.Connection | None = None, max_read_candidates: int = MAX_READ_CANDIDATES) -> list[dict]:
    top = paths[:max_read_candidates]

    # Trigger iCloud downloads in parallel before reading
    icloud_paths = [p for p in top if _is_icloud_stub(p)]
    if icloud_paths:
        with ThreadPoolExecutor(max_workers=min(len(icloud_paths), 8)) as pool:
            list(pool.map(lambda p: _ensure_local(p), icloud_paths))

    candidates = []
    for i, path in enumerate(top):
        try:
            stat = os.stat(path)
        except OSError:
            continue

        content = read_file_content(path, db)
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


# ── LLM ranking ─────────────────────────────────────────────────────────────

def rank_candidates(
    provider: dict,
    user_query: str,
    context_summary: str,
    candidates: list[dict],
    top_results: int = MAX_TOP_RESULTS,
) -> list[dict]:
    candidates_json = json.dumps(candidates, indent=2, default=str)
    prompt = f'''The user is looking for a file on their Mac.

Their description: "{user_query}"
Context: {context_summary}

Below are candidate files. Rank the top {top_results} by how likely each is the file the user wants.

Consider:
- Content match: does the file's content match the user's description?
- Type match: does the filetype match what the user implied?
- Recency: prefer files from the implied time period
- Path clues: folder names hint at project or context

Return JSON only with this shape:
[
  {{"rank": <int>, "index": <candidate index>, "confidence": <0-100>, "reason": <one sentence grounded in this specific file>}},
  ...
]

Worked example — UNRELATED query "the budget spreadsheet Priya emailed me last week":
[
  {{"rank": 1, "index": 4, "confidence": 92, "reason": "Excel file in /Inbox modified 3 days ago, content_preview shows 'Q3 budget' in row 1"}},
  {{"rank": 2, "index": 0, "confidence": 65, "reason": "Same folder but modified 2 months ago — too old for 'last week'"}}
]

Rules:
- Each reason must reference concrete evidence from the candidate's path, content_preview, or modified date. Do NOT reuse strings from the example.
- confidence: 90+ almost certain, 70-89 likely, 50-69 possible, <50 weak.
- If fewer than {top_results} candidates fit, return only the relevant ones. Omit confidence < 30.

Candidates:
{candidates_json}'''

    return _llm_call_json(provider, 1024, [{"role": "user", "content": prompt}], expected="array")


# ── Formatting helpers ───────────────────────────────────────────────────────

def _format_date(timestamp: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# ── Image indexer ────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(DEFAULT_CONFIG)
        print(f"  Created config: {CONFIG_FILE}", file=sys.stderr)
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _load_llm_config() -> dict:
    """Return a single provider dict. Env vars override [llm] in config.toml."""
    config = _load_config()
    section = {
        **config.get("llm", {}),
        **{k: v for k, v in {
            "model": os.environ.get("SEEK_LLM_MODEL"),
            "base_url": os.environ.get("SEEK_LLM_BASE_URL"),
            "api_key_env": os.environ.get("SEEK_LLM_API_KEY_ENV"),
        }.items() if v},
    }
    base_url = section.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
    model = section.get("model", "gemini-2.5-flash-lite")
    api_key_env = section.get("api_key_env", "GEMINI_API_KEY")
    api_key = os.environ.get(api_key_env) or section.get("api_key")
    if not api_key:
        print(
            f"Error: no API key found. Set ${api_key_env} in your env, or add\n"
            f"  api_key = \"...\" under [llm] in {CONFIG_FILE}.\n"
            f"  Get a Google AI Studio key at https://aistudio.google.com/apikey",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"api_key": api_key, "base_url": base_url, "model": model}


def _load_search_config() -> tuple[int, int, int, list[str]]:
    """Return (max_candidates, max_read_candidates, top_results, skip_dirs)."""
    s = _load_config().get("search", {})
    raw_dirs = s.get("skip_dirs", ["~/dev_repo"])
    skip_dirs = [str(Path(d).expanduser()) for d in raw_dirs]
    return (
        int(s.get("max_candidates", MAX_CANDIDATES)),
        int(s.get("max_read_candidates", MAX_READ_CANDIDATES)),
        int(s.get("top_results", MAX_TOP_RESULTS)),
        skip_dirs,
    )


def _caption_batch(paths: list[str]) -> dict[str, str]:
    """Call seek-caption binary on a batch of paths. Returns {path: caption}."""
    if not CAPTION_BINARY.exists():
        print(
            f"  Error: caption binary not found at {CAPTION_BINARY}\n"
            f"  Build it with: cd tools/caption && swiftc -O seek-caption.swift -o seek-caption",
            file=sys.stderr,
        )
        return {}
    try:
        result = subprocess.run(
            [str(CAPTION_BINARY)] + paths,
            capture_output=True, text=True, timeout=60,
        )
        out = {}
        for line in result.stdout.strip().splitlines():
            try:
                d = json.loads(line)
                if "caption" in d:
                    out[d["path"]] = d["caption"]
            except json.JSONDecodeError:
                pass
        return out
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  Warning: caption batch failed: {e}", file=sys.stderr)
        return {}


def run_index(args: list[str]):
    rebuild = "--rebuild" in args
    status_only = "--status" in args

    config = _load_config()
    idx = config.get("index", {})
    folders = [Path(f).expanduser() for f in idx.get("folders", ["~/Downloads", "~/Desktop"])]
    extensions = {"." + e.lstrip(".").lower() for e in idx.get("extensions", list(IMAGE_EXTS))}
    max_bytes = idx.get("max_image_bytes", 20_000_000)

    db = _get_db()
    if db is None:
        print("Error: cannot open index database.", file=sys.stderr)
        sys.exit(1)

    if status_only:
        rows = db.execute("SELECT COUNT(*), MAX(indexed_at) FROM images").fetchone()
        count, last = rows
        last_str = _format_date(last) if last else "never"
        print(f"Index: {count} images, last indexed {last_str}")
        for folder in folders:
            n = db.execute(
                "SELECT COUNT(*) FROM images WHERE path LIKE ?", (str(folder) + "%",)
            ).fetchone()[0]
            print(f"  {folder}: {n} images")
        return

    if rebuild:
        db.execute("DELETE FROM images")
        db.commit()
        print("  Rebuilt: index cleared.", file=sys.stderr)

    # Walk folders and collect candidates
    queue: list[str] = []
    skipped = 0
    for folder in folders:
        if not folder.exists():
            print(f"  Skipping (not found): {folder}", file=sys.stderr)
            continue
        for root, dirs, files in os.walk(folder):
            # Prune skip patterns in-place
            dirs[:] = [
                d for d in dirs
                if not any(s in (root + "/" + d + "/") for s in SKIP_PATTERNS)
            ]
            for fname in files:
                path = os.path.join(root, fname)
                ext = Path(path).suffix.lower()
                if ext not in extensions:
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if stat.st_size > max_bytes:
                    skipped += 1
                    continue
                row = db.execute(
                    "SELECT mtime FROM images WHERE path = ?", (path,)
                ).fetchone()
                if row and abs(row[0] - stat.st_mtime) < 1:
                    continue  # up to date
                queue.append(path)

    total = len(queue)
    print(f"  {total} images to caption ({skipped} skipped, too large).", file=sys.stderr)
    if total == 0:
        print("  Index is up to date.")
        db.close()
        return

    indexed = 0
    for i in range(0, total, CAPTION_BATCH):
        batch = queue[i : i + CAPTION_BATCH]
        captions = _caption_batch(batch)
        now = time.time()
        for path in batch:
            caption = captions.get(path)
            if caption is None:
                continue
            try:
                stat = os.stat(path)
                db.execute(
                    "INSERT OR REPLACE INTO images (path, mtime, size, caption, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (path, stat.st_mtime, stat.st_size, caption, now),
                )
            except OSError:
                pass
        db.commit()
        indexed += len(captions)
        print(f"  [indexed {indexed}/{total}]", file=sys.stderr)

    # Garbage-collect removed files
    all_paths = [row[0] for row in db.execute("SELECT path FROM images").fetchall()]
    removed = 0
    for path in all_paths:
        if not os.path.isfile(path):
            db.execute("DELETE FROM images WHERE path = ?", (path,))
            removed += 1
    if removed:
        db.commit()
        print(f"  Removed {removed} stale entries.", file=sys.stderr)

    print(f"Done. Indexed {indexed} images.")
    db.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if args and args[0] == "index":
        run_index(args[1:])
        return

    json_mode = "--json" in args
    if json_mode:
        args.remove("--json")

    if not args:
        print("Usage: seek <natural language description>")
        print('       seek index [--rebuild] [--status]')
        print('Example: seek "the budget spreadsheet from the Q3 review"')
        sys.exit(1)

    user_query = " ".join(args)

    provider = _load_llm_config()
    provider["client"] = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
    max_candidates, max_read_candidates, top_results, skip_dirs = _load_search_config()

    t0 = time.time()
    log = (lambda msg: print(msg, file=sys.stderr)) if json_mode else print

    db = _get_db()

    log(f'Searching for: "{user_query}"')
    t1 = time.time()
    analysis = analyse_query(provider, user_query)
    print(f"  [analyse: {time.time()-t1:.1f}s]", file=sys.stderr)

    keywords_display = " | ".join(
        " ".join(ks) for ks in analysis.get("keyword_sets", [])
    )
    filenames = analysis.get("filename_fragments", [])
    if filenames:
        keywords_display += " | filename:" + ",".join(filenames)
    log(f"Keywords: {keywords_display}")

    t2 = time.time()
    paths = search_candidates(analysis, db, max_candidates, skip_dirs)
    print(f"  [mdfind+index: {time.time()-t2:.1f}s]", file=sys.stderr)
    if not paths:
        log("\nNo files found. Try different search terms.")
        if db:
            db.close()
        sys.exit(0)

    log(f"Found {len(paths)} candidates, reading content...")

    t3 = time.time()
    candidates = build_candidates_info(paths, db, max_read_candidates)
    print(f"  [read files: {time.time()-t3:.1f}s]", file=sys.stderr)
    if not candidates:
        print("\nCouldn't read any candidate files.")
        if db:
            db.close()
        sys.exit(0)

    context_summary = analysis.get("context_summary", user_query)
    t4 = time.time()
    rankings = rank_candidates(provider, user_query, context_summary, candidates, top_results)
    print(f"  [rank: {time.time()-t4:.1f}s]", file=sys.stderr)
    print(f"  [total: {time.time()-t0:.1f}s]", file=sys.stderr)

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
            "confidence": r.get("confidence", 0),
            "reason": r["reason"],
        })

    if db:
        db.close()

    if json_mode:
        print(json.dumps(results, indent=2))
        return

    print()
    print("Top results:")
    print()

    if not results:
        print("  No relevant results found. Try different search terms.")
        sys.exit(0)

    for r in results:
        conf = r["confidence"]
        bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
        print(f"  {r['rank']}. {r['path']}")
        print(f"     Modified: {r['modified']}  |  Size: {r['size']}  |  Confidence: {bar} {conf}%")
        print(f"     → {r['reason']}")
        print()

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
