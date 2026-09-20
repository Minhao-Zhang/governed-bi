"""``turn_started_at`` is the *turn's* start, not the thread's. Asserted as behaviour.

**The declaration was already tested and the wiring was not.**
``tests/serve/test_state_channels.py`` asserts that ``turn_started_at`` is a member of
``PER_TURN_RESET`` and says exactly why in a comment beside it — "a carried-over clock makes
turn two's ``latency_sec`` span turn one plus everything the user did in between". That test
passed the whole time the clock never reset, because membership of a dict is not the same
statement as the dict reaching the channel.

What sat between them: ``accept`` returns ``PER_TURN_RESET``, and ``serve/wrap.py`` *stripped*
the null ``turn_started_at`` out of every node update before merging it. That strip closed a
real defect of its own — the reset used to overwrite a stamp taken microseconds earlier, so a
0.266 s turn recorded 0.010 s — and in closing it removed the only thing that ever cleared the
channel. From turn two onward the incoming state always held turn one's stamp, the "stamp only
if absent" branch could never fire, and ``serve/nodes/stamp.py``'s ``latency_sec`` reported the
whole conversation's wall clock. The two functions' docstrings each cited the other as the
reason it was safe.

Only the **served** graph could show this: ``eval/harness.py`` builds no ``accept`` node, so
``PER_TURN_RESET`` arrives through the input channel rather than as a node update and never
passes through the wrapper. The measured arms could not observe the bug, which is why these
tests drive the wrapper directly rather than the harness.
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
    """The headline. Two ``accept``-shaped calls, one thread, two clocks.

    ``accept`` is the only node that returns ``PER_TURN_RESET``, so it is the only node that
    says "a turn starts here" — and the wrapper now reads that null as the signal rather than
    discarding it.
    """
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
    """The property the strip was protecting, kept. A turn has one start, not one per node.

    Without this the fix would trade an over-long ``latency_sec`` for an absurdly short one,
    which is the defect the stripped null was introduced to close (0.266 s recorded as 0.010 s).
    """
    ordinary = wrap_node("route", lambda state: {"schemas": ["s"]}, stream=False)

    started = 1_700_000_000.0
    update = _run(ordinary, {"turn_started_at": started})

    assert "turn_started_at" not in update or update["turn_started_at"] == started


def test_the_first_node_of_a_fresh_thread_starts_the_clock() -> None:
    """The CLI and eval entry points start at ``guard``, which returns no reset.

    They have no ``accept``, so nothing on those paths ever writes the null — the clock has to
    start from the empty channel instead. Dropping that branch would leave every non-served
    path with no clock at all, and ``stamp`` raises rather than guessing one.
    """
    guard = wrap_node("guard", lambda state: {"guard": {"outcome": "clear"}}, stream=False)

    update = _run(guard, {})

    assert update["turn_started_at"] is not None


def test_a_node_that_writes_a_real_timestamp_still_wins() -> None:
    """The wrapper's ownership is a default, not a seizure.

    No node does this today. The case exists so that "the wrapper owns the clock" stays a
    statement about *defaults*, and so a future node that genuinely knows better than the
    wrapper — a resume path re-establishing a suspended turn's original start, say — does not
    have to fight it.
    """
    deliberate = wrap_node("odd", lambda state: {"turn_started_at": 42.0}, stream=False)

    assert _run(deliberate, {})["turn_started_at"] == 42.0
    assert _run(deliberate, {"turn_started_at": 1.0})["turn_started_at"] == 42.0
