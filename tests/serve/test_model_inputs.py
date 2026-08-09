"""What the graph actually hands the model. Decision #1's ``prompts_seen`` / ``tools_seen``.

**Written because the system prompt could be emptied with a green suite.** Setting
``SYSTEM_PROMPT = ""`` left the suite at 358 passed / 27 xfailed — byte-identical to baseline
— and so did dropping tools from the bound set. Nothing observed what reached the model,
because ``ScriptedChatModel`` discarded both of its inputs.

Decision #1 recorded that as v1's defect and specified the remedy by name. It was never
built, so every test that ran a turn through the fake was evidence about the graph's plumbing
and none about the model's instructions. These four assertions are the ones that fail when
the instructions go missing.
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
#: prompt reaching the model, not about where it is resolved.
SYSTEM_PROMPT = analyst_prompt()

#: The five tools ADR 0005 §3.5 declares. Named here rather than derived from ``build_tools``
#: on purpose: deriving the expectation from the thing under test is how "the tool set could
#: have been emptied" passed in the first place.
ADR_TOOLS = {"read_body", "inspect_schema", "sample_rows", "run_query", "ask_user"}


def _session(
    index: Any,
    assets: dict[str, Any],
    policy: Any,
    model: Any,
    variants: dict[str, str] | None = None,
) -> Session:
    """A real ``Session``, so ``prompt_set_hash`` is the one the record publishes.

    The hash comes from ``register/prompts.py``, the same way ``session.from_assets`` computes
    it. It used to be ``_digest(SYSTEM_PROMPT)`` here — a hand-built copy of the derivation
    production used at the time — and a fixture that recomputes a value instead of asking for it
    is a fixture that keeps passing after production stops agreeing with it. It did: the engine
    moved to hashing the whole registry and this line went on digesting one prompt.
    """
    from governed_bi.register.prompts import prompt_set_hash

    structure, _problems = build_structure(list(assets.values()))
    return Session(
        index=index, structure=structure, assets_by_id=assets, corpus=None, connector=None,
        policy=policy, corpus_content_hash="corpus-hash",
        prompt_set_hash=prompt_set_hash(variants),
        knobs_resolved={}, db_id="ops_b", run_id="run-model-inputs", agent_model=model,
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
    """The precondition every other assertion here depends on.

    Separate from them deliberately: if the turn declines before ``agent_core``, the three
    below hold vacuously over an empty ``prompts_seen``, which is this file passing for the
    reason it exists to catch.
    """
    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    assert model.prompts_seen, (
        f"the model was never called: path_kind={out.get('path_kind')!r} "
        f"terminal_reason={out.get('terminal_reason')!r}. Nothing below means anything."
    )


def test_the_system_prompt_reaches_the_model(two_schema_index, two_schema_assets, guard_off_policy):
    """``SYSTEM_PROMPT`` verbatim, on every call — **and it says something.**

    ``create_agent(system_prompt=...)`` is the whole mechanism and it had no observer. The
    equality check alone is not enough, and finding that out is why this docstring is longer
    than the test: the first version of this asserted only ``seen == SYSTEM_PROMPT``, and it
    **passed with SYSTEM_PROMPT set to ""** — both sides of the comparison came from the same
    module, so emptying the constant emptied the expectation with it. That is the identical
    self-referential hole the file was written to close, reproduced inside the fix for it.

    So the assertions are layered. Non-empty catches the constant being gutted. Equality
    catches the graph replacing or mangling it in transit. Naming the two tools whose *use* is
    conditional — prefer ``run_query``, call ``ask_user`` only when blocked — catches a rewrite
    that keeps a prompt but drops the discipline, which is what makes a turn answer from
    delivered context alone and still record ``answered``.

    **"Every call" stopped meaning "the analyst".** The guard's scope gate and the five facet
    query rewriters now invoke a model too, sharing this one because no separate utility model is
    configured here, so the loop that asserted ``SYSTEM_PROMPT`` on every call was asserting it
    of a rewriter. The upper bound is kept and made stronger instead: every system prompt the
    model was given must be one ``register/prompts.py`` declares. That catches a prompt invented
    at a call site, which is exactly what the registry exists to prevent and is a wider net than
    the old loop ever cast.
    """
    from governed_bi.register.prompts import PROMPT_REGISTRY

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


def test_all_five_declared_tools_are_bound(two_schema_index, two_schema_assets, guard_off_policy):
    """ADR 0005 §3.5's five, and no sixth.

    The upper bound matters as much as the lower one: ADR 0006's governance boundary is
    enforced by the *absence* of a tool, so an extra bound tool is a hole in it, not a
    feature. This is the assertion that would have caught a generic ``write_file`` arriving
    from a middleware.
    """
    model, _ = _served(two_schema_index, two_schema_assets, guard_off_policy)
    assert model.tools_seen, "bind_tools was never called: the model was handed no tools"
    assert model.tool_names == ADR_TOOLS, (
        f"bound {sorted(model.tool_names)}; ADR 0005 §3.5 declares {sorted(ADR_TOOLS)}. "
        f"missing={sorted(ADR_TOOLS - model.tool_names)} "
        f"extra={sorted(model.tool_names - ADR_TOOLS)}"
    )


def test_prompt_set_hash_digests_the_prompt_the_model_was_given(
    two_schema_index, two_schema_assets, guard_off_policy
):
    """The record's claim about the prompt must match what was delivered.

    ``prompt_set_hash`` is a ``Role.comparability`` field: two runs agreeing on it are treated
    as having asked the model the same way, and a quotability gate reads it. It is computed by
    ``Session``, which is one step removed from what ``create_agent`` handed the provider — and
    one step is where a v1 ladder's ``llm_reasoning_effort`` went missing and cleared a pair it
    should have separated. This closes the loop against the observation.

    **The hash is now over the registry, not over one prompt**, so the loop closes in two
    places: the analyst text the model was handed must be the registered analyst text, and the
    record's hash must be the registry's. Digesting the delivered prompt directly, as this used
    to, would now be asserting that a six-prompt engine hashes like a one-prompt one — the exact
    thing ``register/prompts.py`` was built to stop.
    """
    from governed_bi.register.prompts import prompt_set_hash, prompt_text

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
    """``delivery.context_block`` is what ``assemble`` rendered; the model must have got it.

    ``context_hash`` is a hash of the block, and the record publishes it — but a hash proves
    the block was *rendered*, not that it was *delivered*. Those came apart once already:
    ``connect`` ran on an empty edge set for every turn ever served while the hashes beside it
    looked healthy.
    """
    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    block = (out.get("delivery") or {}).get("context_block") or ""
    assert block, "precondition: assemble rendered a context block"
    # The **analyst** call, not call 0. The facet rewriters and the guard gate share this model
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
    """The other half of the test above: delivered to the model, **never** to the transcript.

    Stated at the wire rather than at the node, because the leak was at the wire and the node
    looked innocent. ``agent_core`` puts the block in the *nested* agent's inbound ``messages``
    and slices it back out of its update, so the outer channel and the persisted conversation
    were both clean — and the block still rendered in the live chat as the user's own bubble
    for the whole of every turn.

    LangGraph streams the nested agent's entire state under ``values|agent_core:<task_id>``,
    and the JS SDK applies the values of any namespace it does not recognise as a subagent
    straight onto root state (``@langchain/langgraph-sdk`` ``dist/ui/manager.js:413``, whose
    test for "subagent" is a ``tools:`` segment that ``agent_core:<task_id>`` does not have).
    ``stream.messages`` therefore became the nested list, index 1 of which was the 8.6 KB
    block. Measured in a captured run: 4–10 such frames per turn.

    So the invariant is not "the outer ``messages`` is clean", it is **``messages`` is the
    conversation at every namespace** — a client cannot tell them apart. Nothing may put
    scaffolding in a ``messages`` channel anywhere in the graph. ``subgraphs=True`` is what
    makes this test able to see the namespaced frames at all; without it the leaking frames are
    invisible and the assertion is vacuous, which is the same trap ADR 0010 M2 records.
    """
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
    """``eval/datalake.py`` has always loaded ``evidence``; nothing ever read it.

    BIRD ships one hint per question naming the value vocabulary and the metric formula the
    question refers to without stating — *"residential areas refers to type = 'Residential'"*
    — which is precisely what the corpus cannot supply for a column whose ``sample_values``
    is empty (0 of 5 947 in the gold layer). ``harness._run_one`` passed only the question
    text and ``ServeState`` had no channel for it, so every EX this repository has produced
    is a *no-evidence* number and is not comparable to any published BIRD figure.
    """
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
    """Position, because position was the defect.

    The block used to be appended last. Last is also *newest*, so after a tool result it reads
    as the newest thing the user said — and the model sometimes answered the block instead of
    the question. Observed live: the tool returned ``whuber`` and the agent's final text was
    "Understood. I'll use the specified joins, bindings, and non-suspect columns for subsequent
    queries", with the record still stamping ``answered``.

    It is intermittent, so a behavioural test would be flaky in the direction that matters —
    passing while broken. This pins the shape instead.
    """
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
    """The whole graph, not the accessor: session -> configurable -> agent_core -> create_agent.

    ``tests/conformance/test_every_prompt_carries_its_variant.py`` refuses a call site that
    drops the selection, and ``test_prompt_registry`` checks the accessor. Neither would catch
    the selection being lost *between* them — ``Session.configurable`` omitting the key, or the
    eval harness building a turn that never carries it. This drives the real graph and reads
    what the model was handed.

    The failure being refused is silent: the run records ``v1``'s ``prompt_set_hash`` and the
    model receives ``v2``, so a paired A/B measures a prompt against itself and reports zero.
    """
    from governed_bi.register.prompts import prompt_text

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
