"""Tests for conversation memory adapters."""

from __future__ import annotations

from src.adapters.memory.conversation_memory import InMemoryConversationMemory
from src.domain.models import ChatMessage


def test_in_memory_roundtrip():
    mem = InMemoryConversationMemory()
    mem.append("s1", ChatMessage(role="user", content="hi"))
    mem.append("s1", ChatMessage(role="assistant", content="hello"))
    assert [m.content for m in mem.load("s1")] == ["hi", "hello"]
    mem.clear("s1")
    assert mem.load("s1") == []
