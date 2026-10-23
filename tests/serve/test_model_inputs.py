"""The graph records the prompts and tools it handed the model. An empty prompt must fail.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.retrieve.structure import build_structure
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.session import Session
from governed_bi.serve.tools import analyst_prompt

#: The analyst prompt at its default variant. Was a module constant in ``serve/tools.py`` until
#: prompt-variant selection was wired: binding it at import meant a run could select a variant,
#: record its hash, and send this. The assertions below are unchanged — they are about the
SYSTEM_PROMPT = analyst_prompt()

#: Named here rather than derived from ``build_tools``: an expectation taken from the
#: implementation is how an emptied tool set passed. Five are ADR 0005 §3.5; ``answer`` is ADR 0018.
ADR_TOOLS = {"read_body", "inspect_schema", "sample_rows", "run_query", "ask_user", "answer"}


def _session(
    index: Any,
    assets: dict[str, Any],
    policy: Any,
    model: Any,
    variants: dict[str, str] | None = None,
) -> Session:
    """A real ``Session``, so ``prompt_set_hash`` is the one the record publishes."""
    from governed_bi.registry.prompts import prompt_set_hash

    structure, _problems = build_structure(list(assets.values()))
    return Session(
        index=index, structure=structure, assets_by_id=assets, corpus=None, connector=None,
        policy=policy, corpus_content_hash="corpus-hash",
        prompt_set_hash=prompt_set_hash(variants),
        settings_resolved={}, db_id="ops_b", run_id="run-model-inputs", agent_model=model,
        prompt_variants=dict(variants or {}),
    )


def _served(
    index: Any, assets: dict[str, Any], policy: Any, variants: dict[str, str] | None = None
) -> tuple[ScriptedChatModel, dict]:
    """One turn served over the two-schema corpus, with a recording model."""
    model = ScriptedChatModel(responses=[AIMessage(content="one sensor")])
    session = _session(index, assets, policy, model, variants)
    config = session.configurable()
    config["configurable"]["thread_id"] = "t-model-inputs"
    turn = {**session.turn("sensors voltage reading per device"), "route_top_n": 1}
    return model, compile_graph().invoke(turn, config)


def test_the_model_was_called_at_all(two_schema_index, two_schema_assets, guard_off_policy):
    """The precondition every other assertion here depends on."""
    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    assert model.prompts_seen, (
        f"the model was never called: path_kind={out.get('path_kind')!r} "
        f"terminal_reason={out.get('terminal_reason')!r}. Nothing below means anything."
    )


def test_the_system_prompt_reaches_the_model(two_schema_index, two_schema_assets, guard_off_policy):
    """``SYSTEM_PROMPT`` verbatim, on every call — **and it says something.**."""
    from governed_bi.registry.prompts import PROMPT_REGISTRY

    model, _ = _served(two_schema_index, two_schema_assets, guard_off_policy)
    prompts = model.system_prompts()
    assert prompts, "no call recorded a system message list"

    analyst_calls = model.calls_with_system(SYSTEM_PROMPT)
    assert analyst_calls, (
        "no call carried SYSTEM_PROMPT verbatim, so the analyst either never ran or was "
        f"prompted with something else. Seen: {[p[:60] for p in prompts]}"
    )
    seen = prompts[analyst_calls[0]]
    assert seen.strip(), (
        "the analyst call carried an empty system prompt. It ran with no instructions, and "
        "every other assertion about SYSTEM_PROMPT is vacuous."
    )
    for governed in ("run_query", "ask_user"):
        assert governed in seen, (
            f"the system prompt does not mention {governed!r}, so nothing tells the model "
            f"when to use it. seen={seen!r}"
        )

    declared = {p.text(v) for p in PROMPT_REGISTRY.values() for v in p.variants}
    for i, text in enumerate(prompts):
        assert text.strip(), f"call {i} carried an empty system prompt"
        assert text in declared, (
            f"call {i} carried a system prompt no registered prompt declares, so "
            f"prompt_set_hash does not cover it. Got {text[:120]!r}"
        )


def test_the_declared_tools_are_bound(two_schema_index, two_schema_assets, guard_off_policy):
    """ADR 0005 §3.5's five, plus the answer tool from ADR 0018."""
    model, _ = _served(two_schema_index, two_schema_assets, guard_off_policy)
    assert model.tools_seen, "bind_tools was never called: the model was handed no tools"
    assert model.tool_names == ADR_TOOLS, (
        f"bound {sorted(model.tool_names)}; the declared set is {sorted(ADR_TOOLS)}. "
        f"missing={sorted(ADR_TOOLS - model.tool_names)} "
        f"extra={sorted(model.tool_names - ADR_TOOLS)}"
    )


def test_prompt_set_hash_digests_the_prompt_the_model_was_given(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The record's claim about the prompt must match what was delivered."""
    from governed_bi.registry.prompts import prompt_set_hash, prompt_text

    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    analyst_calls = model.calls_with_system(SYSTEM_PROMPT)
    assert analyst_calls, "the analyst was never called with the registered prompt"

    delivered = model.system_prompts()[analyst_calls[0]]
    assert delivered == prompt_text("analyst"), (
        "the analyst received a prompt the registry does not produce, so the hash describes "
        f"something else. Got {delivered[:120]!r}"
    )
    claimed = out["answer"]["record"]["prompt_set_hash"]
    assert claimed == prompt_set_hash(), (
        f"the record claims prompt_set_hash={claimed}, but the registry's active set digests to "
        f"{prompt_set_hash()}. Two runs could agree on the field while having been prompted "
        "differently."
    )


def test_the_delivered_context_reaches_the_model(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """``delivery.context_block`` is what ``assemble`` rendered; the model must have got it."""
    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    block = (out.get("delivery") or {}).get("context_block") or ""
    assert block, "precondition: assemble rendered a context block"
    # The **analyst** call, not call 0. The channel rewriters and the guard check share this model
    # when no utility model is configured, and they are called first — so `prompt_text(0)` is a
    # rewriter's messages, which of course never carried the context block.
    analyst_calls = model.calls_with_system(SYSTEM_PROMPT)
    assert analyst_calls, "the analyst was never called, so nothing below means anything"
    assert block in model.prompt_text(analyst_calls[0]), (
        "the rendered context block is not in the messages the model was called with, though "
        f"the record publishes its hash. block={block[:120]!r}..."
    )


def test_the_context_block_never_enters_a_streamed_messages_channel(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The other half of the test above: delivered to the model, **never** to the transcript."""
    model = ScriptedChatModel(responses=[AIMessage(content="one sensor")])
    session = _session(two_schema_index, two_schema_assets, guard_off_policy, model)
    config = session.configurable()
    config["configurable"]["thread_id"] = "t-context-leak"
    turn = {**session.turn("sensors voltage reading per device"), "route_top_n": 1}

    frames: list[tuple[tuple[str, ...], dict]] = []
    for chunk in compile_graph().stream(turn, config, stream_mode="values", subgraphs=True):
        namespace, values = chunk if isinstance(chunk, tuple) else ((), chunk)
        if isinstance(values, dict):
            frames.append((tuple(namespace), values))

    assert any(ns for ns, _ in frames), (
        "no namespaced frame was streamed at all, so this test cannot observe the channel the "
        f"leak was in. namespaces={sorted({ns for ns, _ in frames})}"
    )
    blocks = [
        b
        for ns, values in frames
        if not ns
        for b in [((values.get("delivery") or {}).get("context_block") or "")]
        if b
    ]
    assert blocks, "precondition: assemble rendered a context block and streamed it"
    block = blocks[-1]

    leaked = [
        (ns, i, str(getattr(m, "type", "?")))
        for ns, values in frames
        for i, m in enumerate(values.get("messages") or ())
        if block in str(getattr(m, "content", m))
    ]
    assert not leaked, (
        "the delivered context is inside a streamed `messages` channel, so a client rendering "
        f"`messages` renders {len(block)} characters of scaffolding as a chat turn. "
        f"Carriers (namespace, index, role): {leaked}"
    )


def test_the_dataset_evidence_hint_reaches_the_model() -> None:
    """``eval/datalake.py`` has always loaded ``evidence``; nothing ever read it."""
    from governed_bi.serve.nodes.agent_core import _question_message

    with_hint = _question_message(
        {"question": "how many residential areas", "evidence": "residential means type = 'R'"},
        [],
    )
    assert with_hint is not None
    assert "residential means type = 'R'" in str(with_hint.content), with_hint.content
    assert "Question: how many residential areas" in str(with_hint.content)


def test_a_turn_with_no_evidence_is_byte_identical_to_before() -> None:
    """Every production path has no hint, so the line must be a no-op there."""
    from governed_bi.serve.nodes.agent_core import _question_message

    for state in (
        {"question": "how many customers"},
        {"question": "how many customers", "evidence": ""},
        {"question": "how many customers", "evidence": "   "},
    ):
        message = _question_message(state, [])
        assert str(message.content) == "Question: how many customers", state


def test_the_context_block_is_never_the_last_thing_the_model_sees() -> None:
    """Position, because position was the defect."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    from governed_bi.serve.nodes.agent_core import _with_block

    block = "CONTEXT BLOCK"

    # Call one of the loop: the question is the last word.
    first = _with_block([SystemMessage("sys"), HumanMessage("Q")], block)
    assert [m.content for m in first] == ["sys", block, "Q"]

    # Call two, after a tool result: the data is the last word, the block is not.
    second = _with_block(
        [SystemMessage("sys"), HumanMessage("Q"), AIMessage("call"),
         ToolMessage("rows", tool_call_id="c1")],
        block,
    )
    assert [m.content for m in second] == ["sys", block, "Q", "call", "rows"]
    assert second[-1].content != block, "the block is the newest message again"

    # Turn two of a conversation: it anchors to *this* turn's question, not turn one's.
    third = _with_block(
        [HumanMessage("Q1"), AIMessage("A1"), HumanMessage("Q2")], block
    )
    assert [m.content for m in third] == ["Q1", "A1", block, "Q2"]

    # Degenerate: nothing human to anchor to. Appending is the only option and must not crash.
    assert [m.content for m in _with_block([SystemMessage("sys")], block)] == ["sys", block]


def test_a_selected_prompt_variant_reaches_the_model_end_to_end(
    two_schema_index, two_schema_assets, guard_off_policy
) -> None:
    """The whole graph, not the accessor: session -> configurable -> agent_core -> create_agent."""
    from governed_bi.registry.prompts import prompt_text

    v1, v2 = prompt_text("analyst", {"analyst": "v1"}), prompt_text("analyst")
    assert v1 != v2, "the fixture variants are identical, so this test cannot fail"

    model, out = _served(
        two_schema_index, two_schema_assets, guard_off_policy, {"analyst": "v1"}
    )
    assert model.prompts_seen, f"the model was never called: path_kind={out.get('path_kind')!r}"

    assert model.calls_with_system(v1), (
        "no call carried the selected variant; the run would record v1's prompt_set_hash "
        f"having sent something else. Seen: {[p[:60] for p in model.system_prompts()]}"
    )
    assert not model.calls_with_system(v2), (
        "the default analyst prompt was sent on a turn that selected v1"
    )
