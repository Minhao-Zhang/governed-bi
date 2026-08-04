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
from governed_bi.serve.session import Session, _digest
from governed_bi.serve.tools import SYSTEM_PROMPT

#: The five tools ADR 0005 §3.5 declares. Named here rather than derived from ``build_tools``
#: on purpose: deriving the expectation from the thing under test is how "the tool set could
#: have been emptied" passed in the first place.
ADR_TOOLS = {"read_body", "inspect_schema", "sample_rows", "run_query", "ask_user"}


def _session(index: Any, assets: dict[str, Any], policy: Any, model: Any) -> Session:
    """A real ``Session``, so ``prompt_set_hash`` is the one the record publishes."""
    structure, _problems = build_structure(list(assets.values()))
    return Session(
        index=index, structure=structure, assets_by_id=assets, corpus=None, connector=None,
        policy=policy, corpus_content_hash="corpus-hash",
        prompt_set_hash=_digest(SYSTEM_PROMPT),
        knobs_resolved={}, db_id="ops_b", run_id="run-model-inputs", agent_model=model,
    )


def _served(index: Any, assets: dict[str, Any], policy: Any) -> tuple[ScriptedChatModel, dict]:
    """One turn served over the two-schema corpus, with a recording model."""
    model = ScriptedChatModel(responses=[AIMessage(content="one sensor")])
    session = _session(index, assets, policy, model)
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
    """
    model, _ = _served(two_schema_index, two_schema_assets, guard_off_policy)
    prompts = model.system_prompts()
    assert prompts, "no call recorded a system message list"
    for i, seen in enumerate(prompts):
        assert seen.strip(), (
            f"call {i} carried an empty system prompt. The agent ran with no instructions, and "
            "every other assertion about SYSTEM_PROMPT is vacuous."
        )
        assert seen == SYSTEM_PROMPT, (
            f"call {i} carried a system prompt of {len(seen)} chars, not the {len(SYSTEM_PROMPT)}"
            f"-char SYSTEM_PROMPT. Got {seen!r}"
        )
        for governed in ("run_query", "ask_user"):
            assert governed in seen, (
                f"the system prompt does not mention {governed!r}, so nothing tells the model "
                f"when to use it. seen={seen!r}"
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
    ``Session`` from the constant, which is one step removed from what ``create_agent`` handed
    the provider — and one step is where a v1 ladder's ``llm_reasoning_effort`` went missing
    and cleared a pair it should have separated. This closes the loop against the observation.
    """
    model, out = _served(two_schema_index, two_schema_assets, guard_off_policy)
    delivered = model.system_prompts()[0]
    claimed = out["answer"]["record"]["prompt_set_hash"]
    assert claimed == _digest(delivered), (
        f"the record claims prompt_set_hash={claimed}, but the prompt the model actually "
        f"received digests to {_digest(delivered)}. Two runs could agree on the field while "
        "having been prompted differently."
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
    assert block in model.prompt_text(0), (
        "the rendered context block is not in the messages the model was called with, though "
        f"the record publishes its hash. block={block[:120]!r}..."
    )
