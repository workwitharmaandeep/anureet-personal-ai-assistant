"""
Lightweight Local SQLite + FTS5 Memory, Conversation History, Todo & Reminder Store.
Provides zero-bloat persistent long-term memory for Anureet without requiring PyTorch or ChromaDB.
"""

import contextlib
import datetime
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import config

logger = logging.getLogger("MemoryStore")

_DB_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()
_FTS5_SUPPORTED: Dict[str, bool] = {}

_EXPLICIT_SAVE_PATTERNS = [
    re.compile(r"^(?:hey\s+anureet\s+|anureet\s+)?(?:please\s+)?(?:remember\s+that|remember|note\s+that)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:hey\s+anureet\s+|anureet\s+)?(?:yaad\s+rakhna|yaad\s+rakh|ye\s+yaad\s+rakhna)\s+(?:ki\s+)?(.+)$", re.IGNORECASE),
]

_DECLARATIVE_FACT_PATTERNS = [
    re.compile(r"\b(?:my name is|i am called|call me)\s+([A-Za-z][A-Za-z\s]{1,40})$", re.IGNORECASE),
    re.compile(r"\bmera\s+naam\s+([A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F\s]{1,40}?)(?:\s+hai)?$", re.IGNORECASE),
    re.compile(r"\b(?:i live in|main)\s+([A-Za-z\u0900-\u097F\s]{2,40}?)(?:\s+mein\s+rehta\s+hoon|\s+mein\s+rehti\s+hoon)?$", re.IGNORECASE),
]

_QUESTION_STARTERS = (
    "what ", "who ", "where ", "when ", "why ", "how ",
    "kya ", "kaun ", "kahan ", "kab ", "kaise ", "mera naam kya",
)


def _resolve_db_path(db_path: Optional[Path] = None) -> Path:
    path = Path(db_path) if db_path is not None else Path(config.memory_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@contextlib.contextmanager
def _connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    path = _resolve_db_path(db_path)
    existed = path.exists()
    key = str(path.resolve())
    conn = sqlite3.connect(key, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if key not in _INITIALIZED_PATHS or not existed:
            _init_schema(conn, key)
            _INITIALIZED_PATHS.add(key)
        with conn:
            yield conn
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            due_at TEXT NOT NULL,
            triggered INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS widget_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state TEXT NOT NULL,
            user_text TEXT NOT NULL DEFAULT '',
            agent_text TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'voice',
            created_at TEXT NOT NULL
        )
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, memory_id UNINDEXED)"
        )
        conn.execute(
            """
            INSERT INTO memories_fts(content, memory_id)
            SELECT m.content, m.id FROM memories m
            WHERE NOT EXISTS (SELECT 1 FROM memories_fts f WHERE f.memory_id = m.id)
            """
        )
        _FTS5_SUPPORTED[key] = True
    except sqlite3.OperationalError:
        _FTS5_SUPPORTED[key] = False
    conn.commit()


def save_memory(fact: str, category: str = "fact", db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Save a durable user fact, preference, or contact note into local SQLite memory."""
    cleaned = (fact or "").strip()
    if not cleaned:
        return {"status": "error", "message": "Memory content cannot be empty."}

    key = str(_resolve_db_path(db_path).resolve())
    with _DB_LOCK:
        with _connect(db_path) as conn:
            existing = conn.execute(
                "SELECT id, content FROM memories WHERE LOWER(content) = LOWER(?) LIMIT 1",
                (cleaned,),
            ).fetchone()
            if existing:
                return {
                    "status": "success",
                    "already_exists": True,
                    "memory_id": existing["id"],
                    "content": existing["content"],
                    "message": f"Already remembered: {existing['content']}",
                }

            cur = conn.execute(
                "INSERT INTO memories (category, content, created_at) VALUES (?, ?, ?)",
                (category or "fact", cleaned, _now_iso()),
            )
            mem_id = cur.lastrowid
            if _FTS5_SUPPORTED.get(key):
                conn.execute(
                    "INSERT INTO memories_fts (content, memory_id) VALUES (?, ?)",
                    (cleaned, mem_id),
                )
            conn.commit()

    return {
        "status": "success",
        "already_exists": False,
        "memory_id": mem_id,
        "category": category or "fact",
        "content": cleaned,
        "message": f"Saved to memory: {cleaned}",
    }


def recall_memory(query: str = "", limit: int = 5, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Search stored memories by keyword (FTS5 BM25 ranking + token LIKE fallback) returning top <=5 snippets."""
    cleaned = (query or "").strip()
    effective_limit = max(1, min(int(limit or 5), 5))
    key = str(_resolve_db_path(db_path).resolve())
    rows: List[sqlite3.Row] = []

    with _DB_LOCK:
        with _connect(db_path) as conn:
            if not cleaned:
                rows = conn.execute(
                    "SELECT id, category, content, created_at FROM memories ORDER BY id DESC LIMIT ?",
                    (effective_limit,),
                ).fetchall()
            else:
                tokens = [t for t in re.findall(r"[A-Za-z0-9\u0900-\u097F]+", cleaned) if len(t) >= 2]
                if _FTS5_SUPPORTED.get(key) and tokens:
                    fts_expr = " OR ".join(f'"{t}"*' for t in tokens[:8])
                    try:
                        rows = conn.execute(
                            """
                            SELECT m.id, m.category, m.content, m.created_at, bm25(memories_fts) AS rank
                            FROM memories_fts f
                            JOIN memories m ON m.id = f.memory_id
                            WHERE memories_fts MATCH ?
                            ORDER BY rank ASC, m.id DESC
                            LIMIT ?
                            """,
                            (fts_expr, effective_limit),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []

                if not rows:
                    terms = tokens or [cleaned]
                    clauses = " OR ".join("LOWER(content) LIKE ?" for _ in terms[:6])
                    params = [f"%{t.lower()}%" for t in terms[:6]] + [effective_limit]
                    rows = conn.execute(
                        f"SELECT id, category, content, created_at FROM memories WHERE {clauses} ORDER BY id DESC LIMIT ?",
                        params,
                    ).fetchall()

    items = [dict(r) for r in rows]
    return {
        "status": "success",
        "count": len(items),
        "memories": items,
        "summary": "; ".join(item["content"] for item in items) if items else "No matching memories found.",
    }


def forget_memory(query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Delete matching memory entries from local SQLite storage."""
    cleaned = (query or "").strip()
    if not cleaned:
        return {"status": "error", "message": "Specify what memory to forget."}

    key = str(_resolve_db_path(db_path).resolve())
    with _DB_LOCK:
        with _connect(db_path) as conn:
            matched = conn.execute(
                "SELECT id FROM memories WHERE LOWER(content) LIKE ?",
                (f"%{cleaned.lower()}%",),
            ).fetchall()
            ids = [r["id"] for r in matched]
            if not ids:
                return {"status": "success", "deleted_count": 0, "message": f"No memory matched '{cleaned}'."}
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
            if _FTS5_SUPPORTED.get(key):
                conn.execute(f"DELETE FROM memories_fts WHERE memory_id IN ({placeholders})", ids)
            conn.commit()

    return {
        "status": "success",
        "deleted_count": len(ids),
        "message": f"Forgot {len(ids)} memory item(s) matching '{cleaned}'.",
    }


def auto_extract_memories(user_query: str, db_path: Optional[Path] = None) -> List[str]:
    """Deterministically capture explicit facts ('remember...', 'yaad rakhna...', 'my name is...') without an LLM."""
    text = (user_query or "").strip()
    if len(text) < 4 or len(text) > 260:
        return []

    lowered = text.lower()
    if text.endswith("?") or any(lowered.startswith(q) for q in _QUESTION_STARTERS):
        return []

    saved_items: List[str] = []
    for pat in _EXPLICIT_SAVE_PATTERNS:
        m = pat.match(text)
        if m:
            fact = m.group(1).strip(" .")
            if len(fact) >= 3:
                res = save_memory(fact, category="explicit", db_path=db_path)
                if res.get("status") == "success":
                    saved_items.append(res["content"])
            return saved_items

    for pat in _DECLARATIVE_FACT_PATTERNS:
        if pat.search(text):
            res = save_memory(text.rstrip("."), category="profile", db_path=db_path)
            if res.get("status") == "success":
                saved_items.append(res["content"])
            break

    return saved_items


def append_conversation_turn(
    session_id: str,
    role: str,
    content: str,
    db_path: Optional[Path] = None,
) -> None:
    """Persist a user or assistant message in SQLite conversation history."""
    cleaned = (content or "").strip()
    if not cleaned:
        return
    with _DB_LOCK:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO conversations (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id or "default", role, cleaned, _now_iso()),
            )
            conn.commit()


_SYNCED_SESSIONS = ("web", "widget", "voice", "default")


def get_recent_conversation(
    session_id: str = "default",
    limit: int = 6,
    db_path: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """Fetch the most recent conversation turns for a session in chronological order."""
    sid = session_id or "default"
    with _DB_LOCK:
        with _connect(db_path) as conn:
            if sid in _SYNCED_SESSIONS:
                placeholders = ",".join("?" for _ in _SYNCED_SESSIONS)
                rows = conn.execute(
                    f"""
                    SELECT role, content FROM (
                        SELECT id, role, content FROM conversations
                        WHERE session_id IN ({placeholders})
                        ORDER BY id DESC LIMIT ?
                    ) sub ORDER BY id ASC
                    """,
                    (*_SYNCED_SESSIONS, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT role, content FROM (
                        SELECT id, role, content FROM conversations
                        WHERE session_id = ?
                        ORDER BY id DESC LIMIT ?
                    ) sub ORDER BY id ASC
                    """,
                    (sid, limit),
                ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_synced_history(
    limit: int = 40,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch unified chronological conversation turns across web, widget, voice, and default sessions."""
    placeholders = ",".join("?" for _ in _SYNCED_SESSIONS)
    with _DB_LOCK:
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, session_id, role, content, created_at FROM (
                    SELECT id, session_id, role, content, created_at FROM conversations
                    WHERE session_id IN ({placeholders})
                    ORDER BY id DESC LIMIT ?
                ) sub ORDER BY id ASC
                """,
                (*_SYNCED_SESSIONS, max(1, int(limit))),
            ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "source": r["session_id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def add_todo_item(task: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    cleaned = (task or "").strip()
    if not cleaned:
        return {"status": "error", "message": "Todo task cannot be empty."}
    with _DB_LOCK:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO todos (task, completed, created_at) VALUES (?, 0, ?)",
                (cleaned, _now_iso()),
            )
            conn.commit()
            return {"status": "success", "id": cur.lastrowid, "task": cleaned, "message": f"Added todo: {cleaned}"}


def list_todo_items(include_completed: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    with _DB_LOCK:
        with _connect(db_path) as conn:
            if include_completed:
                rows = conn.execute("SELECT id, task, completed, created_at FROM todos ORDER BY id ASC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, task, completed, created_at FROM todos WHERE completed = 0 ORDER BY id ASC"
                ).fetchall()
    items = [dict(r) for r in rows]
    return {"status": "success", "count": len(items), "todos": items}


def complete_todo_item(task_query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    cleaned = (task_query or "").strip()
    if not cleaned:
        return {"status": "error", "message": "Specify which todo to complete."}
    with _DB_LOCK:
        with _connect(db_path) as conn:
            if cleaned.isdigit():
                row = conn.execute("SELECT id, task FROM todos WHERE id = ?", (int(cleaned),)).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, task FROM todos WHERE completed = 0 AND LOWER(task) LIKE ? ORDER BY id ASC LIMIT 1",
                    (f"%{cleaned.lower()}%",),
                ).fetchone()
            if not row:
                return {"status": "error", "message": f"No pending todo matched '{cleaned}'."}
            conn.execute("UPDATE todos SET completed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return {"status": "success", "id": row["id"], "task": row["task"], "message": f"Completed todo: {row['task']}"}


def delete_todo_item(task_query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    cleaned = (task_query or "").strip()
    with _DB_LOCK:
        with _connect(db_path) as conn:
            if cleaned.isdigit():
                cur = conn.execute("DELETE FROM todos WHERE id = ?", (int(cleaned),))
            else:
                cur = conn.execute("DELETE FROM todos WHERE LOWER(task) LIKE ?", (f"%{cleaned.lower()}%",))
            conn.commit()
            return {"status": "success", "deleted_count": cur.rowcount, "message": f"Deleted {cur.rowcount} todo(s)."}


def add_reminder_item(title: str, due_at_iso: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    cleaned = (title or "").strip()
    if not cleaned:
        return {"status": "error", "message": "Reminder title cannot be empty."}
    with _DB_LOCK:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO reminders (title, due_at, triggered, created_at) VALUES (?, ?, 0, ?)",
                (cleaned, due_at_iso, _now_iso()),
            )
            conn.commit()
            return {
                "status": "success",
                "id": cur.lastrowid,
                "title": cleaned,
                "due_at": due_at_iso,
                "message": f"Reminder set: '{cleaned}' at {due_at_iso}.",
            }


def list_reminder_items(include_triggered: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    with _DB_LOCK:
        with _connect(db_path) as conn:
            if include_triggered:
                rows = conn.execute("SELECT id, title, due_at, triggered, created_at FROM reminders ORDER BY due_at ASC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, due_at, triggered, created_at FROM reminders WHERE triggered = 0 ORDER BY due_at ASC"
                ).fetchall()
    items = [dict(r) for r in rows]
    return {"status": "success", "count": len(items), "reminders": items}


def fetch_due_reminders(mark_triggered: bool = True, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    now = _now_iso()
    with _DB_LOCK:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT id, title, due_at FROM reminders WHERE triggered = 0 AND due_at <= ? ORDER BY due_at ASC",
                (now,),
            ).fetchall()
            items = [dict(r) for r in rows]
            if mark_triggered and items:
                ids = [item["id"] for item in items]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"UPDATE reminders SET triggered = 1 WHERE id IN ({placeholders})", ids)
                conn.commit()
    return items


def publish_widget_event(
    state: str,
    user_text: str = "",
    agent_text: str = "",
    source: str = "voice",
    db_path: Optional[Path] = None,
) -> int:
    """Record a cross-process state/instruction update for the floating Anureet desktop widget."""
    with _DB_LOCK:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO widget_events (state, user_text, agent_text, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    (state or "idle").strip(),
                    (user_text or "").strip(),
                    (agent_text or "").strip(),
                    (source or "voice").strip(),
                    _now_iso(),
                ),
            )
            event_id = int(cur.lastrowid or 0)
            if event_id > 50:
                conn.execute("DELETE FROM widget_events WHERE id <= ?", (event_id - 50,))
            conn.commit()
    return event_id


def get_widget_head_id(db_path: Optional[Path] = None) -> int:
    """Return the current max widget_events ID so a fresh widget only tracks new events."""
    with _DB_LOCK:
        with _connect(db_path) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM widget_events").fetchone()
    return int(row["max_id"]) if row else 0


def get_latest_widget_events(
    after_id: int = 0,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch widget events strictly newer than after_id in chronological order."""
    with _DB_LOCK:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT id, state, user_text, agent_text, source, created_at FROM widget_events WHERE id > ? ORDER BY id ASC",
                (after_id,),
            ).fetchall()
    return [dict(r) for r in rows]
