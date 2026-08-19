"""Corpus mining fires for a resume through the compiled graph -- both real transports.

**This is the test the initial port's Phase 2/3 work never had.** ``_mine_clarification_draft``
lived in ``api/routes.py`` and had exactly one caller, ``POST /chat/resume`` -- itself a
"degradation path" its own docstring says streaming is the primary transport for. The real
``governed-bi-ui`` never calls it: it resumes a paused ``ask_user`` interrupt through
LangGraph Server's own ``/threads/{id}/runs/stream``, which builds the graph from
``api/graph_app.py`` and invokes it directly, never touching ``routes.py``'s FastAPI app.
Confirmed live, across two real browser-driven turns: zero ``/chat``/``/chat/resume`` calls,
only ``/threads/.../runs/stream``. So every test that only called
``_mine_clarification_draft`` directly, or only exercised ``/chat/resume``, proved nothing
about whether a real user's answer ever reached the corpus.

The fix moved mining into ``serve/nodes/mine_corpus.py``, a node in the compiled graph itself
(``serve/graph.py``) -- the one thing both ``/chat/resume`` (via
``serve/resume.py::resume_clarification``) and LangGraph Server's native resume endpoint
actually call: ``graph.invoke(Command(resume=...), config)``. This file drives that call
directly, the same way ``tests/serve/test_agent_tools_hitl.py``'s HITL harness does and the
same way LangGraph Server's own endpoint does -- ``compile_graph()`` + ``resume_clarification()``
+ ``ScriptedChatModel``, with **no import of, or call into, ``governed_bi.api.routes`` at
all** -- and asserts a corpus draft is written as a result. That is the one thing that proves
this gap is actually closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.state import PER_TURN_RESET


def _ask_user_then_answer(*, basis: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {
                            "question": "what does active customer mean?",
                            "basis": basis,
                        },
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok: a customer who ordered in the last 90 days"),
        ]
    )


def _config(model: Any, corpus_root: Path, thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "corpus_root": corpus_root,
        }
    }


def _turn(*, thread_id: str, turn_id: str, token: str) -> dict[str, Any]:
    return {
        "question": "who counts as an active customer?",
        "thread_id": thread_id,
        "turn_index": 1,
        "turn_id": turn_id,
        "run_id": "r",
        "question_id": "q",
        "db_id": "olist",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {"enable_clarification_to_draft": True},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", "olist", 1.0)],
        "messages": [],
        "usage": [],
        "identity": {"token": token},
        "clarifications": [],
    }


def test_a_resume_through_the_compiled_graph_writes_a_corpus_draft(tmp_path: Path) -> None:
    """The gap, closed: a resume driven straight through the compiled graph -- exactly what
    LangGraph Server's native ``/threads/{id}/runs/stream`` does, and what
    ``routes.chat_resume`` also does via ``resume_clarification`` -- must mine a draft,
    with no call anywhere in this test into ``api/routes.py``.
    """
    from governed_bi.corpus.store import load

    model = _ask_user_then_answer(basis="data_definition")
    graph = compile_graph()
    token = "identity-mining-transport"
    config = _config(model, tmp_path, "t-mining-transport")

    paused = graph.invoke(_turn(thread_id="t-mining-transport", turn_id="turn-1", token=token), config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    done = resume_clarification(
        graph, config=config, identity={"token": token}, answer="a customer who ordered in the last 90 days"
    )
    # `no_sql`, not only `answered`: the no-model stub executes no governed statement, and
    # since 2026-08-18 `stamp` no longer hardcodes `has_sql=True` for a finished loop with an
    # empty ledger (ADR 0014). The property under test -- the resume mines a corpus draft --
    # holds on either outcome.
    assert done["answer"]["outcome"] in {"answered", "clarification", "no_sql"}

    assets, problems = load(tmp_path)
    assert not problems, problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "90 days" in draft.summary, (
        f"no corpus draft carries the resumed answer: {[a.summary for a in assets]}"
    )


def test_ranking_ambiguity_still_mines_nothing_through_the_graph_path(tmp_path: Path) -> None:
    """Regression: relocating the trigger must not widen what gets mined. A ranking/superlative
    basis is still turn-scoped only, exactly as ``api/routes.py``'s version enforced.
    """
    from governed_bi.corpus.store import load

    model = _ask_user_then_answer(basis="ranking_ambiguity")
    graph = compile_graph()
    token = "identity-ranking"
    config = _config(model, tmp_path, "t-ranking")

    paused = graph.invoke(_turn(thread_id="t-ranking", turn_id="turn-2", token=token), config)
    assert paused.get("__interrupt__")

    resume_clarification(
        graph, config=config, identity={"token": token}, answer="a customer who ordered in the last 90 days"
    )

    assets, _ = load(tmp_path)
    assert assets == [], f"a ranking_ambiguity clarification was mined: {[a.id for a in assets]}"


def test_a_second_unrelated_turn_does_not_re_mine_the_first(tmp_path: Path) -> None:
    """The dedup guard this relocation needed and ``routes.py``'s version never did: a second
    turn on the same thread must not re-process the first turn's already-mined clarification --
    ``clarifications`` accumulates thread-wide, and without ``clarifications_mined`` this would
    call the Enhancer, and re-write the draft, again on every later turn.
    """
    from governed_bi.corpus.store import load

    model = _ask_user_then_answer(basis="data_definition")
    graph = compile_graph()
    token = "identity-no-remine"
    config = _config(model, tmp_path, "t-no-remine")

    paused = graph.invoke(_turn(thread_id="t-no-remine", turn_id="turn-3", token=token), config)
    assert paused.get("__interrupt__")
    resume_clarification(
        graph, config=config, identity={"token": token}, answer="a customer who ordered in the last 90 days"
    )
    assets_after_first, _ = load(tmp_path)
    assert len(assets_after_first) == 1
    written_at = (tmp_path / "olist" / f"{assets_after_first[0].id}.yaml").stat().st_mtime

    # A second, plain turn on the same thread -- no ask_user, nothing to resume.
    second_model = ScriptedChatModel(responses=[AIMessage(content="ok: 42")])
    second_config = {
        **config,
        "configurable": {**config["configurable"], "agent_model": second_model},
    }
    # `**PER_TURN_RESET`, the same way `Session.turn()` builds every real second turn: without
    # it this is exactly the footgun `test_the_turn_after_a_crashed_turn_is_still_servable`
    # (test_state_channels.py) documents -- a hand-built turn dict that skips the reset some
    # channel outliving its turn under the checkpointer.
    second_turn = {
        **_turn(thread_id="t-no-remine", turn_id="turn-4", token=token),
        "turn_index": 2,
        **PER_TURN_RESET,
    }
    graph.invoke(second_turn, second_config)

    assets_after_second, problems = load(tmp_path)
    assert not problems
    assert len(assets_after_second) == 1, (
        f"a second turn re-mined the first turn's clarification: {[a.id for a in assets_after_second]}"
    )
    assert (
        tmp_path / "olist" / f"{assets_after_second[0].id}.yaml"
    ).stat().st_mtime == written_at, "the draft file was rewritten on a turn that answered nothing new"
