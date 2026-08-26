"""Tests for conversation memory adapters."""

from __future__ import annotations

from src.adapters.memory.conversation_memory import (
    InMemoryConversationMemory,
    JsonFileConversationMemory,
)
from src.domain.models import ChatMessage


def test_in_memory_roundtrip():
    mem = InMemoryConversationMemory()
    mem.append("s1", ChatMessage(role="user", content="hi"))
    mem.append("s1", ChatMessage(role="assistant", content="hello"))
    assert [m.content for m in mem.load("s1")] == ["hi", "hello"]
    mem.clear("s1")
    assert mem.load("s1") == []


def test_json_file_persistence(tmp_path):
    JsonFileConversationMemory(tmp_path).append("abc", ChatMessage(role="user", content="q1"))
    mem2 = JsonFileConversationMemory(tmp_path)
    assert [m.content for m in mem2.load("abc")] == ["q1"]
