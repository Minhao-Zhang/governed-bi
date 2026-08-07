"""``POST /chat``'s response shaping, and the deadlock it used to be.

``/chat`` returned ``out["answer"]`` and nothing else. When ``ask_user`` interrupted, no node
had written ``answer`` — so the route replied **HTTP 200** with ``{"answer_text": null}``,
dropped ``__interrupt__``, and left the graph paused forever. The client saw a successful empty
answer; nothing on screen was wrong. ``serve/tools.py`` already calls the payload version of
this "the worst failure shape available here", and ``/capabilities`` was reporting
``can_clarify: true`` over it. There was also no route to answer on.

The second half was quieter and total: ``resume_clarification`` compares the caller's identity
to the one checkpointed with the turn, ``resume_authorised`` refuses two ``None``s on purpose,
and **nothing in the repository ever supplied one** — so every clarification was unanswerable,
``ResumeRejected`` for every caller including the right one.

These are unit tests of the shaping functions rather than HTTP round-trips: the routes call
``session_from_environment``, which builds a Postgres connector and seeds a corpus. The graph
half of the interrupt is covered end to end by
``test_agent_tools_hitl.py::test_the_ledger_survives_the_interrupt``.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.api import routes
from governed_bi.register.stages import Outcome

CLARIFICATION = {
    "kind": "clarification",
    "clarification_id": "clar-turn-1-abc123",
    "question": "which fiscal year?",
    "why": "revenue is reported per fiscal year and the question names none",
}


class _Interrupt:
    """What LangGraph puts in ``__interrupt__`` — a value behind a ``.value``."""

    def __init__(self, value: Any) -> None:
        self.value = value


def test_a_paused_turn_is_reported_as_a_clarification() -> None:
    """The payload reaches the client, under a declared outcome.

    Before: this state produced ``{"answer_text": None}`` with HTTP 200 and no mention that a
    question was waiting.
    """
    shaped = routes._shape({"__interrupt__": [_Interrupt(CLARIFICATION)]})

    assert shaped["outcome"] == Outcome.clarification.value, (
        f"a paused turn reported outcome={shaped['outcome']!r}. It was reported as whatever "
        "`answer` happened to hold, which on this path is nothing at all."
    )
    assert shaped["clarification"] == CLARIFICATION
    assert shaped["text"] == CLARIFICATION["question"], (
        "the question must be in a field a client already renders, not only in the "
        "clarification envelope"
    )


def test_an_ordinary_answer_is_unchanged_and_says_no_clarification() -> None:
    """The key is always present, so a client never has to distinguish absent from false."""
    from langchain_core.messages import AIMessage

    out = {
        "answer": {"outcome": "answered", "text": None, "failed_stage": None,
                   "error_type": None, "refused_by": None, "record": {"generated_sql": "SELECT 1"}},
        "messages": [AIMessage(content="three customers")],
    }
    shaped = routes._shape(out)
    assert shaped["outcome"] == "answered"
    assert shaped["answer_text"] == "three customers"
    assert shaped["clarification"] is None
    assert shaped["record"]["generated_sql"] == "SELECT 1"


def test_shaping_an_answer_does_not_touch_the_checkpoint() -> None:
    """``_shape`` must be pure over the returned state.

    A draft of this consulted ``graph.get_state`` when no ``__interrupt__`` was present, which
    put a checkpoint read — and therefore a session build — on the answered path of every
    request. Caught by writing this test rather than by a failure.
    """
    called: list[int] = []

    original = routes._graph
    routes._graph = lambda: called.append(1)  # type: ignore[assignment]
    try:
        routes._shape({"answer": {"outcome": "answered", "record": {}}, "messages": []})
    finally:
        routes._graph = original  # type: ignore[assignment]
    assert not called, "_shape reached for the compiled graph on a turn that did not pause"


def test_an_interrupt_of_another_kind_is_not_answered_by_the_clarification_route() -> None:
    """``kind`` is checked, so a future interrupt type is not silently mis-answered."""
    assert routes._clarification([_Interrupt({"kind": "approval", "question": "ok?"})]) is None
    assert routes._clarification(None) is None
    assert routes._clarification([]) is None


def test_the_identity_falls_back_to_the_thread_and_prefers_a_supplied_one() -> None:
    """Without this, ``resume_authorised`` refuses every caller — including the right one.

    The fallback grants no authority that posting to ``/chat`` on the same thread does not
    already grant, because nothing authenticates either. It is a same-thread check, not a
    same-caller one, and a real identity is preferred when one is sent.
    """
    assert routes._identity({}, "thread-9") == {"token": "thread-9"}
    assert routes._identity({"identity": "user-42"}, "thread-9") == {"token": "user-42"}
    assert routes._identity({"identity": {"sub": "user-42"}}, "thread-9") == {"token": "user-42"}
    assert routes._identity({"identity": ""}, "thread-9") == {"token": "thread-9"}
    assert routes._identity({"identity": {}}, "thread-9") == {"token": "thread-9"}


def test_the_resume_route_exists_and_the_turn_can_carry_an_identity() -> None:
    """Both halves of the fix, asserted where they live.

    A route registered on the app, and a ``turn()`` that will checkpoint what
    ``resume_clarification`` reads back. ``Session.turn`` omits ``identity`` entirely when none
    is given, because an absent identity must fail closed rather than compare equal to another
    absence.
    """
    paths = {getattr(r, "path", None) for r in routes.app.routes}
    assert "/chat/resume" in paths, f"no resume route; the app exposes {sorted(p for p in paths if p)}"

    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import Session

    session = Session(
        index=None, structure=None, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="d", run_id="r",
    )
    assert "identity" not in session.turn("q"), "an absent identity must stay absent"
    assert session.turn("q", identity={"token": "u"})["identity"] == {"token": "u"}


def test_a_dropped_in_corpus_is_found_but_ambiguity_is_refused(tmp_path, monkeypatch) -> None:
    """``uv run langgraph dev`` with no environment, and the one case it must not guess.

    A curated corpus is dropped into ``corpora/`` and the server should find it, because
    typing three env vars before a dev command is how a wrong corpus gets served by accident.
    But *two* directories is a question only the operator can settle: picking one would make
    ``corpus_content_hash`` — the field every quotability gate reads — depend on directory
    ordering. So one is an answer and two is an error naming both.
    """
    from governed_bi.api import graph_app

    assert graph_app._dropped_in_corpus(tmp_path) is None, "no corpora/ at all is not an error"

    base = tmp_path / graph_app.CORPORA_DIR
    (base / "_build").mkdir(parents=True)       # underscore dirs are build output, not corpora
    assert graph_app._dropped_in_corpus(tmp_path) is None

    (base / "gold-20260804").mkdir()
    assert graph_app._dropped_in_corpus(tmp_path) == str(base / "gold-20260804")

    (base / "curated_sme_20260730").mkdir()
    with pytest.raises(RuntimeError, match="holds 2 corpora"):
        graph_app._dropped_in_corpus(tmp_path)


def test_chat_actually_answers_rather_than_raising() -> None:
    """`/chat` must reach `stamp`, not just be importable.

    Written because it did not exist and something silently broke. Every node became
    `async def` (the only shape LangGraph will attach a node timeout to) while this route
    still compiled the graph itself and called `.invoke()`, so every request raised
    `TypeError: No synchronous function provided to "guard"` — and the whole suite, 841
    passing, said nothing. The route's other tests assert response *shape* against stubs, so
    none of them drives a turn end to end.

    Deliberately asserts almost nothing about the answer. With no model configured this turn
    refuses or crashes-closed, and that is fine: the property under test is that the transport
    runs the graph and returns a shaped body, which is the part that broke.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import app

    response = TestClient(app).post(
        "/chat", json={"session_id": "t-transport", "question": "how many customers"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict) and body, "the route returned no body"
    assert "detail" not in body or "No synchronous function" not in str(body.get("detail")), (
        f"the graph could not be driven from this transport: {body.get('detail')!r}"
    )
