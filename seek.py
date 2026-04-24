#!/opt/homebrew/anaconda3/bin/python3
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
MAX_CANDIDATES = 30
MAX_READ_CANDIDATES = 15
MAX_CONTENT_BYTES = 10240
MAX_FILE_SIZE = 5 * 1024 * 1024
MDFIND_TIMEOUT = 5

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tiff"}
INDEX_DB = Path.home() / ".local/share/seek/index.db"
CONFIG_FILE = Path.home() / ".config/seek/config.toml"
CAPTION_BINARY = Path(__file__).resolve().parent / "tools/caption/seek-caption"
CAPTION_BATCH = 20

DEFAULT_CONFIG = """\
[llm]
api_key_env = "OPENROUTER_API_KEY"
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


# ── LLM call with fallback ───────────────────────────────────────────────────

def _llm_call(client: OpenAI, model: str, fallback_models: list[str], max_tokens: int, messages: list[dict]) -> str:
    last_err: Exception | None = None
    for m in [model] + fallback_models:
        try:
            resp = client.chat.completions.create(model=m, max_tokens=max_tokens, messages=messages)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            print(f"  [model {m} failed ({type(e).__name__}), trying fallback]", file=sys.stderr)
    raise last_err


# ── Query analysis ──────────────────────────────────────────────────────────

def analyse_query(client: OpenAI, model: str, fallback_models: list[str], user_query: str) -> dict:
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

    text = _llm_call(client, model, fallback_models, 1024, [{"role": "user", "content": prompt}])
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)


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


def search_candidates(analysis: dict, db: sqlite3.Connection | None = None) -> list[str]:
    seen: set[str] = set()
    mdfind_results: list[str] = []
    image_hits: list[str] = []

    def _valid(p: str) -> bool:
        return (p not in seen
                and not any(s in p for s in SKIP_PATTERNS)
                and os.path.isfile(p))

    def add_mdfind(paths: list[str]):
        for p in paths:
            if _valid(p):
                seen.add(p)
                mdfind_results.append(p)

    def add_image(paths: list[str]):
        for p in paths:
            if _valid(p):
                seen.add(p)
                image_hits.append(p)

    keyword_sets = analysis.get("keyword_sets", [])

    queries = []
    for kw_set in keyword_sets:
        queries.append(" ".join(kw_set))
    for frag in analysis.get("filename_fragments", []):
        queries.append(f"kMDItemDisplayName == '*{frag}*'cd")

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
            queries.append(
                f"{main_keyword} && "
                f"kMDItemContentModificationDate >= $time.iso({start_date}) && "
                f"kMDItemContentModificationDate < $time.iso({end_date})"
            )

    # Image index hits first — so they're guaranteed a slot at the front of the read window
    if db is not None:
        # Use keyword_sets (concise, LLM-extracted) rather than verbose context_summary
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
                add_image([row[0] for row in rows])
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=len(queries) or 1) as pool:
        futures = [pool.submit(run_mdfind, q) for q in queries]
        for f in futures:
            add_mdfind(f.result())

    # Sort mdfind results by recency; image hits stay at front (already ranked by FTS)
    mdfind_results.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    combined = image_hits + mdfind_results
    return combined[:MAX_CANDIDATES]


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


def build_candidates_info(paths: list[str], db: sqlite3.Connection | None = None) -> list[dict]:
    top = paths[:MAX_READ_CANDIDATES]

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
    client: OpenAI,
    model: str,
    fallback_models: list[str],
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
  {{"rank": 1, "index": 0, "confidence": 95, "reason": "One-line explanation"}},
  ...
]

- confidence: 0-100 how confident this is the file the user wants. 90+ = almost certain, 70-89 = likely, 50-69 = possible, below 50 = weak match.
- If fewer than 5 candidates seem relevant, return only the relevant ones. Omit weak matches (confidence < 30).

Candidates:
{candidates_json}'''

    text = _llm_call(client, model, fallback_models, 1024, [{"role": "user", "content": prompt}])
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)


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


def _load_llm_config() -> tuple[str, str, str, list[str]]:
    """Return (api_key, base_url, model, fallback_models). Env vars override config, config overrides defaults."""
    config = _load_config()
    llm = config.get("llm", {})
    model = os.environ.get("SEEK_LLM_MODEL", llm.get("model", "google/gemini-2.0-flash-exp:free"))
    base_url = os.environ.get("SEEK_LLM_BASE_URL", llm.get("base_url", "https://openrouter.ai/api/v1"))
    api_key_env = os.environ.get("SEEK_LLM_API_KEY_ENV", llm.get("api_key_env", "OPENROUTER_API_KEY"))
    # Support key stored directly in config via `api_key` field, or via env var lookup
    api_key = os.environ.get(api_key_env) or llm.get("api_key")
    fallback_models = llm.get("fallback_models", ["meta-llama/llama-3.3-70b-instruct:free"])
    if not api_key:
        print(
            f"Error: {api_key_env} environment variable not set.\n"
            f"  Get a free key at https://openrouter.ai/keys",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key, base_url, model, fallback_models


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
        print('Example: seek "the grants playbook I made for Frank"')
        sys.exit(1)

    user_query = " ".join(args)

    api_key, base_url, model, fallback_models = _load_llm_config()

    t0 = time.time()
    log = (lambda msg: print(msg, file=sys.stderr)) if json_mode else print

    db = _get_db()
    client = OpenAI(api_key=api_key, base_url=base_url)

    log(f'Searching for: "{user_query}"')
    t1 = time.time()
    analysis = analyse_query(client, model, fallback_models, user_query)
    print(f"  [analyse: {time.time()-t1:.1f}s]", file=sys.stderr)

    keywords_display = " | ".join(
        " ".join(ks) for ks in analysis.get("keyword_sets", [])
    )
    filenames = analysis.get("filename_fragments", [])
    if filenames:
        keywords_display += " | filename:" + ",".join(filenames)
    log(f"Keywords: {keywords_display}")

    t2 = time.time()
    paths = search_candidates(analysis, db)
    print(f"  [mdfind+index: {time.time()-t2:.1f}s]", file=sys.stderr)
    if not paths:
        log("\nNo files found. Try different search terms.")
        if db:
            db.close()
        sys.exit(0)

    log(f"Found {len(paths)} candidates, reading content...")

    t3 = time.time()
    candidates = build_candidates_info(paths, db)
    print(f"  [read files: {time.time()-t3:.1f}s]", file=sys.stderr)
    if not candidates:
        print("\nCouldn't read any candidate files.")
        if db:
            db.close()
        sys.exit(0)

    context_summary = analysis.get("context_summary", user_query)
    t4 = time.time()
    rankings = rank_candidates(client, model, fallback_models, user_query, context_summary, candidates)
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
