"""Thread-safe local persistence for BO1 conversation context across HTTP jobs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.domain.models import ChatMessage


class SqliteConversationMemory:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
                session_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL, content TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS messages_by_session ON conversation_messages(session_id, sequence)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def load(self, session_id: str) -> list[ChatMessage]:
        with self._connect() as db:
            rows = db.execute("SELECT role,content FROM conversation_messages WHERE session_id=? ORDER BY sequence",
                              (session_id,)).fetchall()
        return [ChatMessage(role=role, content=content) for role, content in rows]

    def append(self, session_id: str, message: ChatMessage) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO conversation_messages(session_id,role,content) VALUES (?,?,?)",
                       (session_id, message.role, message.content))

    def clear(self, session_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM conversation_messages WHERE session_id=?", (session_id,))
