"""Tests for the D8 memory stores: the working-memory store + protocol seams."""

from __future__ import annotations

import pytest

from governed_bi.memory import (
    CorrectionMemory,
    EpisodicMemory,
    InMemoryWorkingMemory,
    WorkingMemory,
)


def test_append_and_history_round_trip():
    wm = InMemoryWorkingMemory()
    wm.append("s1", "user", "total revenue?")
    wm.append("s1", "assistant", "18496.0")
    assert wm.history("s1") == [("user", "total revenue?"), ("assistant", "18496.0")]


def test_unknown_session_history_is_empty():
    assert InMemoryWorkingMemory().history("nope") == []


def test_sessions_are_isolated():
    wm = InMemoryWorkingMemory()
    wm.append("s1", "user", "a")
    wm.append("s2", "user", "b")
    assert wm.history("s1") == [("user", "a")]
    assert wm.history("s2") == [("user", "b")]


def test_clear_removes_only_that_session():
    wm = InMemoryWorkingMemory()
    wm.append("s1", "user", "a")
    wm.append("s2", "user", "b")
    wm.clear("s1")
    assert wm.history("s1") == []
    assert wm.history("s2") == [("user", "b")]


def test_history_is_a_copy():
    wm = InMemoryWorkingMemory()
    wm.append("s1", "user", "a")
    got = wm.history("s1")
    got.append(("user", "injected"))
    assert wm.history("s1") == [("user", "a")]  # internal state untouched


def test_max_turns_keeps_most_recent():
    wm = InMemoryWorkingMemory(max_turns=2)
    for i in range(4):
        wm.append("s1", "user", str(i))
    assert wm.history("s1") == [("user", "2"), ("user", "3")]


def test_max_turns_must_be_positive():
    with pytest.raises(ValueError):
        InMemoryWorkingMemory(max_turns=0)


def test_in_memory_store_satisfies_protocol():
    assert isinstance(InMemoryWorkingMemory(), WorkingMemory)


def test_durable_store_protocols_are_runtime_checkable():
    # The seams exist as interfaces; nothing implements them yet (off by default).
    assert not isinstance(InMemoryWorkingMemory(), EpisodicMemory)
    assert not isinstance(InMemoryWorkingMemory(), CorrectionMemory)
