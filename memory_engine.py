# ──────────────────────────────────────────────────────────────────────────────
# memory_engine.py — Long-Term Memory Engine for Varic
#
# Manages persistent memories, user preferences, project facts, and context
# across application restarts using SQLite.
# ──────────────────────────────────────────────────────────────────────────────
from config import BASE_DIR
import os
import json
import sqlite3
import time
import threading
from typing import List, Dict, Optional, Any

VARIC_DIR = os.environ.get("VARIC_DIR", str(BASE_DIR))
DB_PATH = os.path.join(VARIC_DIR, "varic_memory.db")


class MemoryEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self):
        # Use thread-local storage for connections to avoid threading issues
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # Enable foreign keys and set other pragmas for better performance
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging for better concurrency
            conn.execute("PRAGMA synchronous = NORMAL")  # Balance between safety and speed
            self._local.connection = conn
        return self._local.connection

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    arguments TEXT,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def save_memory(self, category: str, key: str, content: str) -> bool:
        """Save or update a persistent memory."""
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT INTO memories (category, key, content, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        category = excluded.category,
                        content = excluded.content,
                        updated_at = CURRENT_TIMESTAMP;
                """, (category.lower().strip(), key.strip(), content.strip()))
                conn.commit()
                print(f"[MEMORY] Saved memory [{category}] '{key}'")
                return True
        except Exception as e:
            print(f"[MEMORY ERROR] Failed to save memory: {e}")
            return False

    def get_memory(self, key: str) -> Optional[str]:
        """Fetch a specific memory content by key."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT content FROM memories WHERE key = ?", (key.strip(),))
                row = cur.fetchone()
                return row["content"] if row else None
        except Exception as e:
            print(f"[MEMORY ERROR] Get failed: {e}")
            return None

    def search_memories(self, query: str, limit: int = 10) -> List[Dict]:
        """Search memories using keyword matching."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                like_q = f"%{query.strip()}%"
                cur.execute("""
                    SELECT id, category, key, content, updated_at
                    FROM memories
                    WHERE key LIKE ? OR content LIKE ? OR category LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, (like_q, like_q, like_q, limit))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[MEMORY ERROR] Search failed: {e}")
            return []

    def get_all_memories(self, category: Optional[str] = None) -> List[Dict]:
        """Retrieve all stored memories, optionally filtered by category."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                if category:
                    cur.execute("""
                        SELECT id, category, key, content, updated_at
                        FROM memories
                        WHERE category = ?
                        ORDER BY updated_at DESC
                    """, (category.lower().strip(),))
                else:
                    cur.execute("""
                        SELECT id, category, key, content, updated_at
                        FROM memories
                        ORDER BY updated_at DESC
                    """)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[MEMORY ERROR] Fetch failed: {e}")
            return []

    def delete_memory(self, key_or_id) -> bool:
        """Delete a memory by key or ID."""
        try:
            with self._get_conn() as conn:
                if str(key_or_id).isdigit():
                    conn.execute("DELETE FROM memories WHERE id = ?", (int(key_or_id),))
                else:
                    conn.execute("DELETE FROM memories WHERE key = ?", (str(key_or_id).strip(),))
                conn.commit()
                return True
        except Exception as e:
            print(f"[MEMORY ERROR] Delete failed: {e}")
            return False

    def get_context_for_prompt(self, max_items: int = 8) -> str:
        """Format active memories for prompt context injection."""
        memories = self.get_all_memories()[:max_items]
        if not memories:
            return ""
        lines = ["[VARIC MEMORY CONTEXT]"]
        for m in memories:
            lines.append(f"- [{m['category'].upper()}] {m['key']}: {m['content']}")
    def log_tool_call(self, tool_name: str, arguments: Any = None, status: str = "success", result: str = "") -> bool:
        """Log a tool execution event to the tool_audit table."""
        try:
            arg_str = json.dumps(arguments) if isinstance(arguments, (dict, list)) else str(arguments or "")
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT INTO tool_audit (tool_name, arguments, status, result)
                    VALUES (?, ?, ?, ?)
                """, (tool_name, arg_str, status, result[:1000]))
                conn.commit()
                return True
        except Exception as e:
            print(f"[AUDIT LOG ERROR] {e}")
            return False

    def get_tool_audit_logs(self, limit: int = 30) -> List[Dict]:
        """Fetch recent tool execution audit logs."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, tool_name, arguments, status, result, created_at
                    FROM tool_audit
                    ORDER BY id DESC
                    LIMIT ?
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[AUDIT FETCH ERROR] {e}")
            return []

memory_engine = MemoryEngine()


def close_connections():
    """Close all thread-local database connections."""
    # This would require tracking all threads, which is complex
    # For now, we'll rely on SQLite's connection cleanup when threads end
    # In a more complex application, we might want to track connections
    pass


if __name__ == "__main__":
    memory_engine.save_memory("preference", "user_name", "Developer")
    memory_engine.save_memory("project", "default_tech", "Python + FastAPI + PySide6")
    print("Memories count:", len(memory_engine.get_all_memories()))
    print("Prompt Context:\n", memory_engine.get_context_for_prompt())
