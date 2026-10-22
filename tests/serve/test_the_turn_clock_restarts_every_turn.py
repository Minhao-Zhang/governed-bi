"""``turn_started_at`` is the turn's start, not the thread's.

The wrapper treats a null in ``PER_TURN_RESET`` as the reset signal and still keeps one start
per turn. These tests drive the wrapper because the harness has no ``accept`` node.
"""

from __future__ import annotations

import asyncio
import time

from governed_bi.serve.state import PER_TURN_RESET
from governed_bi.serve.wrap import wrap_node


def _run(node, state):
    """One wrapped node, as LangGraph would call it."""
    return asyncio.run(node(state))


def test_a_second_turn_does_not_inherit_the_first_turns_clock() -> None:
    """``accept`` is the node that writes the null the wrapper reads as a new turn."""
    accept = wrap_node("accept", lambda state: {**PER_TURN_RESET, "question": "q"}, stream=False)

    first = _run(accept, {})
    assert first["turn_started_at"] is not None, "turn one never started its clock"

    time.sleep(0.05)
    second = _run(accept, {"turn_started_at": first["turn_started_at"]})

    assert second["turn_started_at"] is not None, (
        "turn two carried turn one's clock. `latency_sec` now measures the conversation, and "
        "`_LIST_VIEW_KEYS` projects it to the audit list, so the number is served as well as "
        "stored"
    )
    assert second["turn_started_at"] > first["turn_started_at"]


def test_a_later_node_in_the_same_turn_does_not_restart_the_clock() -> None:
    """A later node in the same turn must not restart the clock."""
    ordinary = wrap_node("route", lambda state: {"schemas": ["s"]}, stream=False)

    started = 1_700_000_000.0
    update = _run(ordinary, {"turn_started_at": started})

    assert "turn_started_at" not in update or update["turn_started_at"] == started


def test_the_first_node_of_a_fresh_thread_starts_the_clock() -> None:
    """Paths with no ``accept`` start the clock from an empty channel."""
    guard = wrap_node("guard", lambda state: {"guard": {"outcome": "clear"}}, stream=False)

    update = _run(guard, {})

    assert update["turn_started_at"] is not None


def test_a_node_that_writes_a_real_timestamp_still_wins() -> None:
    """A node that writes a real timestamp keeps it; the wrapper only fills a missing clock."""
    deliberate = wrap_node("odd", lambda state: {"turn_started_at": 42.0}, stream=False)

    assert _run(deliberate, {})["turn_started_at"] == 42.0
    assert _run(deliberate, {"turn_started_at": 1.0})["turn_started_at"] == 42.0
