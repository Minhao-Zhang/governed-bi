"""A guess about a phrase two retrieved terms bind differently is refused (ADR 0018 §3).

The scripted model is driven through ``agent_core_node``. An ``ask_user`` pause needs a
checkpointer, so that case compiles a one-node graph.
"""

from __future__ import annotations

import asyncio
from typing import Any, get_args, get_origin, get_type_hints

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import Binding, TermAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.registry.assets import AssetType
from governed_bi.serve.agent_state import GovernedAgentState, add_counts
from governed_bi.serve.assumptions import ambiguous_guesses
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.state import ServeState

_GROSS = TermAsset(
    id="term.gross",
    name="gross revenue",
    summary="Revenue before deductions.",
    synonyms=("revenue",),
    binding=Binding(target_type=AssetType.column, target_id="sales.orders.gross"),
)
_NET = TermAsset(
    id="term.net",
    name="net revenue",
    summary="Revenue after deductions.",
    synonyms=("revenue",),
    binding=Binding(target_type=AssetType.column, target_id="sales.orders.net"),
)
_TERMS = (_GROSS, _NET)

_AMBIGUOUS = {
    "statement": "Revenue means one of the two figures.",
    "about": " Revenue ",
    "basis": "guess",
    "asset_id": None,
}
_PLAIN = {
    "statement": "The figure is gross revenue.",
    "about": "gross revenue",
    "basis": "guess",
    "asset_id": None,
}


def _claim(about: str | None, basis: str = "guess", asset_id: str | None = None) -> dict[str, Any]:
    return {
        "statement": "The figure is the one the question means.",
        "about": about,
        "basis": basis,
        "asset_id": asset_id,
    }


def _same_binding() -> tuple[TermAsset, TermAsset]:
    binding = Binding(target_type=AssetType.column, target_id="sales.orders.amount")
    left = TermAsset(
        id="term.left", name="revenue", summary="Revenue.", synonyms=("takings",), binding=binding,
    )
    right = TermAsset(
        id="term.right", name="takings", summary="Takings.", synonyms=("revenue",), binding=binding,
    )
    return left, right


def test_two_terms_with_different_bindings_match() -> None:
    found = ambiguous_guesses([_claim("revenue")], _TERMS)

    assert len(found) == 1
    assert found[0]["about"] == "revenue"
    assert [term["id"] for term in found[0]["terms"]] == ["term.gross", "term.net"]
    assert found[0]["terms"][0]["binding"] == "column sales.orders.gross"
    assert found[0]["terms"][1]["name"] == "net revenue"


def test_the_same_binding_does_not_match() -> None:
    assert ambiguous_guesses([_claim("revenue")], _same_binding()) == []


def test_a_synonym_matches_and_case_does_not_matter() -> None:
    """The shared word is a synonym, and the guess's capitals and spaces still match."""
    found = ambiguous_guesses([_claim("  REVENUE ")], _TERMS)

    assert [term["id"] for term in found[0]["terms"]] == ["term.gross", "term.net"]


def test_an_empty_phrase_never_matches() -> None:
    assert ambiguous_guesses([_claim(None), _claim("   ")], _TERMS) == []


def test_a_convention_entry_is_not_checked() -> None:
    claims = [
        _claim("revenue", basis="convention"),
        _claim("revenue", basis="layer", asset_id="term.gross"),
    ]
    assert ambiguous_guesses(claims, _TERMS) == []


def test_refusal_writes_add() -> None:
    hints = get_type_hints(GovernedAgentState, include_extras=True)
    annotation = hints["ambiguous_guess_refusals"]
    assert get_origin(annotation) is not None
    assert add_counts in get_args(annotation)[1:]
    assert add_counts(1, 1) == 2


def _answer_call(text: str, assumptions: list[dict[str, Any]], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "answer",
            "args": {"answer_text": text, "assumptions": assumptions},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def _ask_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "ask_user",
            "args": {"question": "Which revenue do you mean?", "basis": "data_definition"},
            "id": call_id,
            "type": "tool_call",
        }],
    )


def _assets() -> dict[str, Any]:
    return {term.id: term for term in _TERMS}


def _config(model: Any, turn_id: str) -> dict[str, Any]:
    assets = _assets()
    return {
        "configurable": {
            "thread_id": turn_id,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        },
    }


def _state(turn_id: str, *, readable: list[str] | None = None) -> dict[str, Any]:
    ids = list(_assets()) if readable is None else readable
    return {
        "question": "what was revenue",
        "turn_id": turn_id,
        "turn_index": 1,
        "thread_id": turn_id,
        "run_id": "run-ambiguous",
        "question_id": "q-ambiguous",
        "db_id": "sales",
        "attempt_id": "attempt-ambiguous",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        "retrieved": {"selected": {asset_id: {} for asset_id in ids}},
        "messages": [],
        "usage": [],
    }


def _settle(state: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    from governed_bi.api.graph_app import record_node
    from governed_bi.serve.nodes.finish import finish
    from governed_bi.serve.nodes.narrate import narrate_node

    merged = {**state, **core}
    merged.update(asyncio.run(narrate_node(merged, {})))
    merged.update(finish(merged))
    merged.update(record_node()(merged))
    return merged


def _run(model: Any, turn_id: str, *, readable: list[str] | None = None) -> dict[str, Any]:
    state = _state(turn_id, readable=readable)
    core = asyncio.run(agent_core_node(state, _config(model, turn_id)))
    return _settle(state, core)


def _tool_texts(state: dict[str, Any]) -> list[str]:
    return [
        str(message.content)
        for message in state.get("messages") or []
        if isinstance(message, ToolMessage)
    ]


def test_an_ambiguous_guess_is_refused_and_the_next_call_asks() -> None:
    """The refusal does not end the turn. The next scripted call is ``ask_user``, which pauses."""
    model = ScriptedChatModel(responses=[
        _answer_call("Revenue was 10.", [_AMBIGUOUS], "a1"),
        _ask_call("c1"),
        AIMessage(content="Using the gross figure."),
    ])
    graph = StateGraph(ServeState)
    graph.add_node("agent_core", agent_core_node)
    graph.add_edge(START, "agent_core")
    graph.add_edge("agent_core", END)
    app = graph.compile(checkpointer=InMemorySaver())
    state = _state("turn-ask")
    config = _config(model, "turn-ask")

    paused = asyncio.run(app.ainvoke(state, config))
    interrupts = [item.value for item in (paused.get("__interrupt__") or ())]

    assert interrupts and interrupts[0]["basis"] == "data_definition"
    assert interrupts[0]["question"] == "Which revenue do you mean?"
    assert len(model.prompts_seen) == 2
    refused = model.prompt_text(1)
    assert "term.gross" in refused and "term.net" in refused
    assert "data_definition" in refused
    assert "gross revenue" in refused and "net revenue" in refused

    done = asyncio.run(app.ainvoke(Command(resume="the gross figure"), config))
    record = _settle(state, done)["answer"]["record"]
    assert record["ambiguous_guess_refusals"] == 1
    assert record["guess_count"] == 0


def test_an_unambiguous_guess_is_accepted() -> None:
    model = ScriptedChatModel(responses=[
        _answer_call("Gross revenue was 10.", [_PLAIN], "a1"),
    ])
    out = _run(model, "turn-plain")
    record = out["answer"]["record"]

    assert _tool_texts(out)[-1] == "Answer recorded."
    assert record["guess_count"] == 1
    assert record["ambiguous_guess_refusals"] == 0
    assert record["assumptions"][0]["basis"] == "guess"


def test_a_turn_that_ends_in_prose_has_both_counts_at_zero() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="Revenue was 10.")])
    record = _run(model, "turn-prose")["answer"]["record"]

    assert record["guess_count"] == 0
    assert record["ambiguous_guess_refusals"] == 0
    assert record["assumptions"] == []


def test_a_downgraded_layer_claim_is_refused_every_time() -> None:
    """The checked basis is ``guess``, so a second call with the same claim is refused too."""
    claim = _claim("revenue", basis="layer", asset_id="term.missing")
    model = ScriptedChatModel(responses=[
        _answer_call("Revenue was 10.", [claim], "a1"),
        _answer_call("Revenue was 10.", [claim], "a2"),
        _answer_call("Gross revenue was 10.", [_PLAIN], "a3"),
    ])
    out = _run(model, "turn-downgraded")
    record = out["answer"]["record"]
    texts = _tool_texts(out)

    assert texts[-1] == "Answer recorded."
    assert sum("more than one way" in text for text in texts) == 2
    assert record["ambiguous_guess_refusals"] == 2
    assert record["guess_count"] == 1


def test_a_term_outside_the_retrieved_set_does_not_compete() -> None:
    model = ScriptedChatModel(responses=[
        _answer_call("Revenue was 10.", [_AMBIGUOUS], "a1"),
    ])
    out = _run(model, "turn-one-term", readable=["term.gross"])

    assert _tool_texts(out)[-1] == "Answer recorded."
    assert out["answer"]["record"]["ambiguous_guess_refusals"] == 0
    assert out["answer"]["record"]["guess_count"] == 1


def test_two_refusals_in_one_step_both_count() -> None:
    first = _answer_call("Revenue was 10.", [_AMBIGUOUS], "a1")
    second = _answer_call("Revenue was 10.", [_AMBIGUOUS], "a2")
    both = AIMessage(content="", tool_calls=[*first.tool_calls, *second.tool_calls])
    model = ScriptedChatModel(responses=[
        both,
        _answer_call("Gross revenue was 10.", [_PLAIN], "a3"),
    ])
    record = _run(model, "turn-parallel")["answer"]["record"]

    assert record["ambiguous_guess_refusals"] == 2
    assert record["guess_count"] == 1


def test_prose_after_a_refusal_is_reminded_then_recorded_as_no_answer() -> None:
    """The way around the refusal is closed: prose gets one reminder, then no answer."""
    model = ScriptedChatModel(responses=[
        _answer_call("Revenue was 10.", [_AMBIGUOUS], "a1"),
        AIMessage(content="Revenue was 10."),
    ])
    out = _run(model, "turn-around")

    assert out["answer"]["outcome"] == "crashed"
    assert out["answer"]["error_type"] == "NoAnswerCall"
    assert out["answer"]["answer_text"] is None
    assert out["answer"]["record"]["ambiguous_guess_refusals"] == 1
    assert len(model.prompts_seen) == 3
