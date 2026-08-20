"""The read half of the trust boundary, and the saver that grew without limit.

Two defects found by the same audit, both of the same shape: a deliberate decision was made on
one side of something and the other side was left at its default, where nothing marks an
omission as an omission.

* ``input_schema=ServeInput`` closed what a client may **write**. Nothing closed what it may
  **read**, so the served graph returned every state channel on ``invoke`` — ``identity`` (the
  token clarification-resume authorises against) and ``delivery`` (the whole rendered corpus
  context block) among them. ``output_schema=ServeOutput`` closed the ``invoke`` half **only**,
  which is where these tests stop: re-measured on langgraph 1.2.11, ``stream_channels_asis`` is
  still all 47 channels (46 before ``turns``), so ``values`` frames and ``get_state`` are
  unchanged. That remainder is B1 and is open — nothing below asserts
  otherwise.
* ``compile_graph`` defaults to an ``InMemorySaver`` that nothing ever empties, and
  ``eval/harness.py`` keeps one compiled graph per worker for a whole arm.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import _evict
from governed_bi.serve.graph import build_graph, compile_graph

#: Channels a client must never be handed back. Not exhaustive — the schema is an allowlist and
#: this is a tripwire on the specific keys that motivated it.
_MUST_NOT_ESCAPE = ("identity", "delivery", "knobs_resolved", "query_vector", "retrieve_hooks")


def test_the_served_graph_returns_only_what_the_interface_reads() -> None:
    served = build_graph(accept=lambda s: {}).compile()
    assert set(served.output_channels) == {"messages", "answer"}, sorted(served.output_channels)


def test_no_internal_channel_escapes_on_the_served_path() -> None:
    served = build_graph(accept=lambda s: {}).compile()
    leaked = [k for k in _MUST_NOT_ESCAPE if k in served.output_channels]
    assert leaked == [], (
        f"{leaked} reach the client again. `identity` is what resume_authorised() gates on and "
        "`delivery` carries the rendered corpus; see ServeOutput."
    )


def test_the_in_process_path_still_sees_everything() -> None:
    """The other half of the same decision, and the reason the schemas are conditional.

    ``eval/`` and the CLI build the turn in-process and project the record out of channels no
    client sees. Narrowing their output too would not have tightened a boundary — there is no
    boundary there — it would have silently emptied every measurement row.
    """
    inproc = build_graph().compile()
    assert len(inproc.output_channels) > 40, len(inproc.output_channels)
    for key in _MUST_NOT_ESCAPE:
        assert key in inproc.output_channels, key


def test_no_checkpointer_is_expressible() -> None:
    """``None`` means "make me an InMemorySaver", so "do not persist" needed its own value.

    An option that cannot be said through the front door gets said by going around it — and
    going around this one also skips ``as_sync``, which until today returned a fabricated
    crashed turn instead of raising.
    """
    assert compile_graph(checkpointer=False)._app.checkpointer is None
    assert compile_graph()._app.checkpointer is not None


def test_a_finished_question_stops_costing_memory() -> None:
    """Per thread, because with ``workers > 1`` a blunt clear would delete a sibling mid-flight."""
    app = compile_graph()
    saver: Any = app._app.checkpointer

    def depth() -> int:
        return sum(1 for _ in saver.list(None))

    for index in range(3):
        thread_id = f"run-arm-q{index}"
        app._app.update_state({"configurable": {"thread_id": thread_id}}, {"question": "q"})
        assert depth() == 1, f"question {index} wrote nothing to evict"
        _evict(app, thread_id)
        assert depth() == 0, (
            f"question {index} left checkpoints behind; over an arm this is ~100 KB per "
            "question retained until the process exits"
        )


def test_eviction_never_fails_a_turn() -> None:
    """Housekeeping must not be able to kill an arm 900 questions in."""
    _evict(compile_graph(), "never-existed")
    _evict(object(), "no-saver-at-all")
    _evict(compile_graph(checkpointer=False), "no-saver-compiled")
