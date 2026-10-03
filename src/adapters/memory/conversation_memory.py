"""Conversation memory adapters (the *NECESSITA MEMÓRIA* requirement)."""

from __future__ import annotations

import threading

from src.domain.models import ChatMessage


class InMemoryConversationMemory:
    """Thread-safe, process-local conversation store."""

    def __init__(self) -> None:
        self._store: dict[str, list[ChatMessage]] = {}
        self._lock = threading.Lock()

    def load(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            return list(self._store.get(session_id, []))

    def append(self, session_id: str, message: ChatMessage) -> None:
        with self._lock:
            self._store.setdefault(session_id, []).append(message)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)
