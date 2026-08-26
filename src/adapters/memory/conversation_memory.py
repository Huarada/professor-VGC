"""Conversation memory adapters (the *NECESSITA MEMÓRIA* requirement)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from src.domain.exceptions import ConversationMemoryError
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


class JsonFileConversationMemory:
    """Durable conversation store: one JSON file per session id."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConversationMemoryError(f"Cannot create memory dir: {exc}") from exc
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self._dir / f"{safe or 'default'}.json"

    def load(self, session_id: str) -> list[ChatMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return [ChatMessage.model_validate(item) for item in data]
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            raise ConversationMemoryError(f"Cannot read memory {path}: {exc}") from exc

    def append(self, session_id: str, message: ChatMessage) -> None:
        with self._lock:
            history = self.load(session_id)
            history.append(message)
            path = self._path(session_id)
            try:
                with path.open("w", encoding="utf-8") as handle:
                    json.dump([m.model_dump() for m in history], handle, ensure_ascii=False)
            except OSError as exc:
                raise ConversationMemoryError(f"Cannot write memory {path}: {exc}") from exc

    def clear(self, session_id: str) -> None:
        path = self._path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            raise ConversationMemoryError(f"Cannot clear memory {path}: {exc}") from exc
