"""The live stage-event stream (ADR 0010).

**Why this file exists at all**, given AGENTS.md's rule that not everything needs a test.
:func:`~governed_bi.serve.events.emit` swallows every exception on purpose — an observability
layer that can fail a turn is worse than one that can go quiet — and the cost of that choice is
that a broken emitter is invisible in production. These tests are what pays the cost: they
assert the payload builder over every stage and every status, so a payload that would have
raised inside the ``try`` is caught here instead of never being emitted at all.

The second reason is the vocabulary. ``register/stages.py`` is the authority for ``step``, and a
step name emitted from ``serve/`` that is not a ``Stage`` member is precisely the "second,
competing vocabulary invented at the moment somebody needed it" that module exists to prevent.
:func:`test_every_emitted_step_is_a_declared_stage` is that check, and it is the one that will
fire if someone adds a step by hand later.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from governed_bi.register.stages import Stage
from governed_bi.serve import events
from governed_bi.serve.events import (
    TERMINAL_HANDLERS,
    rail_event_id,
    rail_observation,
    silenced_by_terminal_state,
    tool_event_id,
)
from governed_bi.serve.wrap import wrap_node

#: Every ``step`` the engine can put on the wire. Mirrors ADR 0010's table, and the point of
#: listing it here rather than importing one is that the ADR, the frontend and this file must
#: agree — a list derived from the code could not catch the code being wrong.
EMITTED_STEPS = (
    "accept",
    "guard",
    "rewrite",
    "negative_gate",
    "facet_schema",
    "facet_term",
    "facet_metric",
    "facet_entity",
    "facet_example",
    "route",
    "resolve",
    "connect",
    "assemble",
    "agent_core",
    # Emitted only when the observer ran, which the shipped configuration never does. Listed
    # anyway: the point of this table is that a step the engine *can* put on the wire is
    # declared, and "off by default" is a configuration rather than an absence.
    "reflect",
    "read_body",
    "inspect_schema",
    "sample_rows",
    "check",
    "execute",
    "cap",
    "ask_user",
    "refuse",
    "decline",
    "stamp",
)

#: The client validates on `typeof kind === "string" && typeof seq === "number"` and drops
#: anything else silently, so a malformed event is not an error anywhere — it is a step that
#: never appears. These are the statuses its union accepts.
VALID_STATUSES = frozenset(
    {"start", "ok", "blocked", "error", "refused", "declined", "hit", "miss", "cap"}
)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Collect what ``emit`` would have written.

    Patches the writer rather than ``emit`` itself, so the payload construction under test is
    the real one — patching ``emit`` would test the callers and nothing else.
    """
    seen: list[dict[str, Any]] = []
    # Patched on ``events``, not on ``langgraph.config``. ``events`` binds the name at import
    # (deliberately — see the comment on that import), so patching the source module would
    # leave the bound reference untouched and this fixture would silently capture nothing:
    # every assertion below would read an empty list and the tests would fail for the wrong
    # reason, or worse, pass vacuously.
    monkeypatch.setattr(events, "get_stream_writer", lambda: seen.append)
    return seen


def test_every_emitted_step_is_a_declared_stage() -> None:
    """No step name may exist that ``register/stages.py`` has not declared."""
    undeclared = [s for s in EMITTED_STEPS if s not in {m.value for m in Stage}]
    assert undeclared == [], (
        f"{undeclared} are emitted but absent from Stage. Add the member to "
        "register/stages.py — never a bare string here."
    )


def test_run_query_is_not_a_step() -> None:
    """``Stage`` deliberately has no ``run_query`` member, and the stream must not invent one.

    A SQL call emits ``check`` then ``execute``. The reason is in ``stages.py``: a passing query
    already emits that pair and a third record would double-count an action the ledger and every
    rate already agree on.
    """
    assert "run_query" not in EMITTED_STEPS
    assert "run_query" not in {m.value for m in Stage}


def test_emit_produces_a_well_formed_event(captured: list[dict[str, Any]]) -> None:
    events.emit(
        kind="rail",
        step="guard",
        status="blocked",
        event_id="guard:t1",
        detail={"rule_id": "r_pii"},
        serve_path="agent",
    )
    assert len(captured) == 1
    ev = captured[0]
    assert isinstance(ev["seq"], int)
    assert isinstance(ev["kind"], str)
    assert ev["step"] == "guard"
    assert ev["status"] == "blocked"
    assert ev["id"] == "guard:t1"
    assert ev["detail"] == {"rule_id": "r_pii"}
    assert ev["serve_path"] == "agent"


def test_seq_is_monotonic(captured: list[dict[str, Any]]) -> None:
    for _ in range(5):
        events.emit(kind="rail", step="route", status="ok", event_id="route:t1")
    seqs = [e["seq"] for e in captured]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_empty_detail_is_omitted_not_sent_as_an_empty_object(
    captured: list[dict[str, Any]],
) -> None:
    """An absent fact is absent. A ``{}`` on the wire reads as "observed nothing", which is a
    different claim from "did not observe"."""
    events.emit(kind="rail", step="agent_core", status="ok", event_id="a:1", detail={})
    assert "detail" not in captured[0]


def test_emit_outside_a_runnable_context_is_a_noop() -> None:
    """``get_stream_writer()`` raises ``RuntimeError`` outside a graph run — which is
    ``eval/harness.py`` and ``python -m governed_bi.serve``. Both must keep working."""
    events.emit(kind="rail", step="guard", status="ok", event_id="guard:t1")


@pytest.mark.parametrize("step", EMITTED_STEPS)
@pytest.mark.parametrize("status", sorted(VALID_STATUSES))
def test_every_step_status_pair_builds(
    step: str, status: str, captured: list[dict[str, Any]]
) -> None:
    """The whole grid. This is the test that pays for ``emit`` swallowing."""
    events.emit(kind="tool", step=step, status=status, event_id=f"{step}:x")
    assert captured[-1]["status"] in VALID_STATUSES


# ── rail_observation: status is read out of the update, never declared ──


@pytest.mark.parametrize(
    ("stage", "update", "expected"),
    [
        ("guard", {"guard": {"outcome": "blocked", "rule_id": "r_pii"}}, "blocked"),
        ("guard", {"guard": {"outcome": "clear"}}, "ok"),
        ("negative_gate", {"negative": {"outcome": "hit", "matched_id": "n1"}}, "hit"),
        ("negative_gate", {"negative": {"outcome": "miss"}}, "miss"),
        # The gate ships disabled. `miss` would claim it looked and found nothing.
        ("negative_gate", {"negative": {"outcome": "disabled"}}, "ok"),
        ("route", {"schemas": [], "path_kind": "decline"}, "declined"),
        ("route", {"schemas": ["sales_a"]}, "ok"),
        ("refuse", {"path_kind": "refuse", "terminal_reason": "guard"}, "refused"),
        ("decline", {"path_kind": "decline", "terminal_reason": "no_schema_matched"}, "declined"),
        ("connect", {"path_kind": "crashed", "failure": {"error_type": "KeyError"}}, "error"),
        ("assemble", {}, "ok"),
    ],
)
def test_rail_status_is_observed(stage: str, update: dict[str, Any], expected: str) -> None:
    status, _ = rail_observation(stage, update)
    assert status == expected
    assert status in VALID_STATUSES


# ── the seven defects adversarial review found. Each one shipped, briefly. ──


def test_a_gate_that_errored_open_is_not_reported_as_clear() -> None:
    """``error_failed_open`` means the gate ran, a rule threw, and the question went through.

    ``register/record.py`` makes it a countable security event and gates a run on it, so
    reporting it as ``ok`` is the stream disagreeing with the record on the one outcome where
    "nothing happened" is the dangerous reading.
    """
    status, detail = rail_observation("guard", {"guard": {"outcome": "error_failed_open"}})
    assert status == "error"
    assert detail == {"gate": "error_failed_open"}

    status, detail = rail_observation("negative_gate", {"negative": {"outcome": "error_failed_open"}})
    assert status == "error"
    assert detail == {"gate": "error_failed_open"}


def test_a_facet_whose_channel_never_ran_is_not_reported_as_zero_hits() -> None:
    """"0 hits" and "the channel was never wired up" are different facts.

    This is ``_channels_for``'s own defect one layer out: its predecessor reported the
    configuration instead of the observation, so a facet that consulted nothing claimed it had.
    An operator reading "Terms: 0 hits" would conclude the corpus is empty.
    """
    degraded = {"facets": {"facet_term": {"hits": [], "channels": {"lexical": "failed", "semantic": "not_configured"}}}}
    status, detail = rail_observation("facet_term", degraded)
    assert status == "error"
    assert detail == {"n_hits": 0, "failed_channels": ["lexical"]}

    healthy = {"facets": {"facet_term": {"hits": [], "channels": {"lexical": "ran", "semantic": "not_configured"}}}}
    status, detail = rail_observation("facet_term", healthy)
    assert status == "ok", "a channel that ran and found nothing really did find nothing"
    assert detail == {"n_hits": 0}


def test_a_failed_rewrite_is_not_reported_as_rewritten() -> None:
    """``rewritten`` was ``outcome != "unchanged"``, which made an error look like a success."""
    status, detail = rail_observation("rewrite", {"rewrite": {"outcome": "error"}})
    assert (status, detail) == ("error", {"rewritten": False})
    assert rail_observation("rewrite", {"rewrite": {"outcome": "unchanged"}}) == ("ok", {"rewritten": False})
    assert rail_observation("rewrite", {"rewrite": {"outcome": "rewritten"}}) == ("ok", {"rewritten": True})


def test_a_decline_carries_its_reason_under_both_names() -> None:
    """The engine's channel calls it ``terminal_reason``; the contract and the client read
    ``reason``. Emitting one left the most important row on a failed turn unexplained."""
    _, detail = rail_observation("route", {"path_kind": "decline", "terminal_reason": "no_schema_matched"})
    assert detail["reason"] == "no_schema_matched"
    assert detail["terminal_reason"] == "no_schema_matched"


def test_a_declining_node_still_reports_what_it_measured() -> None:
    """A declining ``connect`` dropped its counts, so the row that most needs explaining was the
    least informative — while the same node on a success reported them."""
    _, detail = rail_observation(
        "connect",
        {"path_kind": "decline", "terminal_reason": "missing_join_path",
         "crossings": [], "licensed": ["a.t1", "a.t2"]},
    )
    assert detail["reason"] == "missing_join_path"
    assert detail["n_licensed"] == 2
    assert detail["n_crossings"] == 0


def test_accept_is_not_silenced_by_the_previous_turns_terminal_state() -> None:
    """``path_kind`` is checkpointed and ``accept`` is the node that clears it, so on turn N+1 of
    a thread whose turn N declined, ``accept`` reads its predecessor's value on entry.

    Silencing it there cost the first row of the timeline *and* the turn's only ``serve_path``
    tag, which the wire contract says rides the first event — on every follow-up question after
    a refusal, a decline or a crash.
    """
    for stale in ("refuse", "decline", "crashed"):
        assert not silenced_by_terminal_state("accept", {"path_kind": stale}), (
            f"accept was silenced by a stale {stale} from the previous turn"
        )


def test_crash_detail_carries_the_type_and_never_the_message() -> None:
    """ADR 0006 §11 retention: exceptions as ``type(exc).__name__``, never ``str(exc)``.
    libpq embeds the offending statement in its error text."""
    _, detail = rail_observation("connect", {"path_kind": "crashed", "failure": {
        "error_type": "UndefinedTable", "detail": 'relation "secret" does not exist'
    }})
    assert detail == {"error_type": "UndefinedTable"}


def test_rail_detail_is_read_from_the_update() -> None:
    _, detail = rail_observation(
        "route", {"schemas": ["a", "b"], "retrieved": {"schema_ranking": [1, 2, 3, 4]}}
    )
    assert detail == {"schemas": ["a", "b"], "n_candidates": 4}

    _, detail = rail_observation("facet_metric", {"facets": {"facet_metric": {"hits": [1, 2]}}})
    assert detail == {"n_hits": 2}

    _, detail = rail_observation("assemble", {"delivery": {"context_block": "abcd"}})
    assert detail == {"n_chars": 4}


def test_rail_observation_tolerates_a_missing_channel() -> None:
    """A node that wrote nothing still ran. Every reader degrades to ``("ok", {})`` rather than
    raising inside ``wrap.py``, which is outside the node's own ``try``."""
    for stage in EMITTED_STEPS:
        status, detail = rail_observation(stage, {})
        assert status in VALID_STATUSES
        assert isinstance(detail, dict)


# ── ids are stable across a resume replay ──


def test_rail_event_id_is_keyed_on_the_turn_not_the_sequence() -> None:
    """``ask_user`` makes the pending node re-execute on resume, so ``start`` is emitted twice
    for one logical step. A seq-derived id would open a second row."""
    state = {"turn_id": "t-abc"}
    assert rail_event_id("agent_core", state) == rail_event_id("agent_core", state)
    assert rail_event_id("agent_core", {"turn_id": "t-abc"}) != rail_event_id(
        "agent_core", {"turn_id": "t-def"}
    )


def test_accept_has_no_turn_id_yet_and_still_gets_an_id() -> None:
    """``accept`` mints ``turn_id``, so it cannot read one on entry. Safe because ``accept``
    is never the node a resume replays."""
    assert rail_event_id("accept", {}) == "accept:accept"


def test_tool_event_id_is_the_tool_call_id() -> None:
    """The same key ``attempts_by_call`` and ``tool_delivered`` are filed under, so the
    timeline and the record agree on what one call was."""
    assert tool_event_id("check", "call-1") != tool_event_id("check", "call-2")
    assert tool_event_id("check", "call-1") == tool_event_id("check", "call-1")


# ── nodes that no-op on a terminal turn must not claim to have run ──


@pytest.mark.parametrize("path_kind", ["refuse", "decline", "crashed"])
def test_skipped_nodes_are_silent(path_kind: str) -> None:
    assert silenced_by_terminal_state("route", {"path_kind": path_kind})
    assert silenced_by_terminal_state("agent_core", {"path_kind": path_kind})


@pytest.mark.parametrize("stage", sorted(TERMINAL_HANDLERS))
def test_terminal_handlers_are_never_silenced(stage: str) -> None:
    """``refuse`` and ``decline`` run *because* the turn ended. Silencing them on a terminal
    state would drop the only row that says why."""
    assert not silenced_by_terminal_state(stage, {"path_kind": "decline"})


def test_a_healthy_turn_silences_nothing() -> None:
    assert not silenced_by_terminal_state("route", {})
    assert not silenced_by_terminal_state("route", {"path_kind": "answered"})


# ── wrap_node: the emitter cannot change what the node returns ──


def test_wrap_node_emits_start_then_the_observed_status(
    captured: list[dict[str, Any]],
) -> None:
    import time

    wrapped = wrap_node("guard", lambda state: {"guard": {"outcome": "blocked"}})
    out = asyncio.run(wrapped({"turn_id": "t1", "question": "q"}))
    # The node's own keys pass through untouched, which is this test's name. The wrapper adds
    # exactly one of its own: the turn's clock, because it is the one place every rail passes
    # through and `latency_sec` needs a single start (audit §10 — no clock was read anywhere in
    # `src/` at all). A second added key would be a wrapper deciding something.
    assert {k: v for k, v in out.items() if k != "turn_started_at"} == {
        "guard": {"outcome": "blocked"}
    }
    assert out["turn_started_at"] == pytest.approx(time.time(), abs=10)
    # Already started: the wrapper must not restamp, or `latency_sec` measures the last node.
    again = asyncio.run(wrapped({"turn_id": "t1", "question": "q", "turn_started_at": 1.0}))
    assert "turn_started_at" not in again
    assert [(e["step"], e["status"]) for e in captured][:2] == [
        ("guard", "start"),
        ("guard", "blocked"),
    ]
    assert captured[0]["id"] == captured[1]["id"]


def test_wrap_node_reports_a_crash_as_error(captured: list[dict[str, Any]]) -> None:
    def boom(state: Any) -> dict[str, Any]:
        raise KeyError("policy")

    out = asyncio.run(wrap_node("connect", boom)({"turn_id": "t1"}))
    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "connect", "error_type": "KeyError"}
    assert [e["status"] for e in captured] == ["start", "error"]
    assert captured[-1]["detail"] == {"error_type": "KeyError"}


def test_wrap_node_emits_nothing_for_a_skipped_node(captured: list[dict[str, Any]]) -> None:
    wrapped = wrap_node("route", lambda state: {})
    asyncio.run(wrapped({"turn_id": "t1", "path_kind": "decline"}))
    assert captured == []


def test_stream_false_suppresses_both_events(captured: list[dict[str, Any]]) -> None:
    """The ``fanout`` passthrough is registered under ``facet_schema``; emitting for it put a
    phantom row immediately before the real facet's."""
    asyncio.run(wrap_node("facet_schema", lambda state: {}, stream=False)({"turn_id": "t1"}))
    assert captured == []


def test_an_interrupt_produces_no_resolve_event(captured: list[dict[str, Any]]) -> None:
    """A suspended node has not ended. The row stays ``running`` while a human is asked."""
    from langgraph.errors import GraphInterrupt

    def asks(state: Any) -> dict[str, Any]:
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        asyncio.run(wrap_node("agent_core", asks)({"turn_id": "t1"}))
    assert [e["status"] for e in captured] == ["start"]


def test_a_failing_writer_cannot_fail_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule from ``events.py``: a stream event that fails to send is not a governance event
    that failed to happen."""
    def exploding_writer() -> Any:
        def w(_: Any) -> None:
            raise RuntimeError("the stream is gone")

        return w

    monkeypatch.setattr(events, "get_stream_writer", exploding_writer)
    out = asyncio.run(
        wrap_node("guard", lambda state: {"guard": {"outcome": "clear"}})({"turn_id": "t1"})
    )
    assert {k: v for k, v in out.items() if k != "turn_started_at"} == {
        "guard": {"outcome": "clear"}
    }
