"""``agent_core`` — create_agent loop + tools (ADR 0005 §3.1)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallLimitMiddleware,
    hook_config,
    wrap_model_call,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from governed_bi.serve.agent_state import CAP_LEDGER_KEY, GovernedAgentState
from governed_bi.serve.delivery import DeliveryTracker
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.ledger import answering_attempts, cap_attempt, execution_from_attempts
from governed_bi.serve.runtime import configurable
from governed_bi.serve.state import TERMINAL_PATH_KINDS
from governed_bi.serve.tools import analyst_prompt, build_tools, policy_from_config
from governed_bi.serve.usage import NO_TOKEN_USAGE, usage_row

__all__ = ["agent_core_node", "STUB_ANSWER", "NO_TOKEN_USAGE"]

STUB_ANSWER = "STUB_ANSWER"

# ``NO_TOKEN_USAGE`` is re-exported from ``serve/usage.py``, where the row builder lives.
# Kept on this module's ``__all__`` because that is what tests assert against.


async def agent_core_node(state: dict, config: RunnableConfig) -> dict:
    """Run the main model + tools, or the F1 stub when no ``agent_model`` is set.

    **No ``checkpointer`` parameter, and the nested agent needs none.** LangGraph propagates
    the checkpointer through ``config`` to a graph invoked inside a node and namespaces it, so
    the nested agent is checkpointed by the *outer* saver — probed: an agent given its own
    saver ends the run with zero checkpoints while the outer one has three.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = configurable(config)
    model = cfg.get("agent_model")
    if model is None:
        return _stub(state)

    tools = build_tools(state, config)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=analyst_prompt(config),
        # The ledger channels the tools write. Without the schema LangGraph drops updates
        # naming keys the graph does not declare, silently — see ``serve/agent_state.py``.
        state_schema=GovernedAgentState,
        # The delivered context, injected at model-call time rather than put in ``messages``,
        # plus the thing that actually stops the loop at the attempt cap.
        middleware=[
            *_context_middleware(state),
            _CapEndsTheTurn(policy_from_config(config).run_query_attempt_cap),
        ],
    )

    # ``messages`` is the conversation and nothing else — the context block must not enter it.
    # It is checkpointed and counted: appending the block re-sends every prior turn's context
    # to the provider, and makes the human-message count 2n-1, which ``turn_index`` derives
    # from. The block is already recorded, hashed, in ``delivery``.
    history = list(state.get("messages") or [])
    question = _question_message(state, history)
    inbound = history + ([question] if question is not None else [])

    # Hang-stop lives *here* (not ``wrap_node``) so a timeout still projects the streamed
    # ledger: ``wrap_node``'s ``wait_for`` would reduce the update to ``{failure, path_kind}``
    # and discard every attempt the loop already recorded. See :func:`_run` for how the bound
    # is applied, and what it costs.
    result, failure = await _run(
        agent,
        inbound,
        config,
        recursion_limit=_recursion_limit(state),
        timeout=_agent_node_timeout(state),
        grace=_hang_grace(state),
    )

    out_messages = list(result.get("messages") or [])
    fresh = out_messages[len(inbound) :]

    # The ledger, read from the agent's own checkpointed channels rather than from closures
    # on the tool objects. Ordered by insertion, which is chronological (``merge_by_call``
    # keeps the accumulated map first).
    attempts = list((result.get("attempts_by_call") or {}).values())
    clarifications = list((result.get("clarifications_by_call") or {}).values())
    delivered = dict(result.get("tool_delivered") or {})

    generated_sql = _last_executed_sql(attempts) or _last_run_query_sql(out_messages)
    delivery = DeliveryTracker(delivered).merge_into(state.get("delivery"))
    usage = [usage_row(stage="agent_core", model=model, messages=fresh,
                       turn_index=state.get("turn_index", 1))]
    execution = execution_from_attempts(attempts)

    update: dict[str, Any] = {
        # ``crashed`` when the loop died, and the ledger above is returned anyway: the two
        # facts are independent. See :func:`_run`. Stamp still re-reads ``execution.terminal``
        # when ``path_kind`` is ``answered`` (capped / all-refused).
        "path_kind": "crashed" if failure is not None else "answered",
        "messages": fresh,
        "usage": usage,
        "delivery": delivery,
        "clarification_requested": False,
        "execution": execution,
    }
    if failure is not None:
        # ``wrap_node`` never sees this exception, so the marker it would have written is
        # written here in the same shape. ``rail_observation`` reads ``path_kind`` before the
        # per-stage handler, so a self-handled crash still reports `error`.
        update["failure"] = failure
    if clarifications:
        update["clarifications"] = clarifications
    if generated_sql:
        update["generated_sql"] = generated_sql
    # Lifted out of the nested agent's channel, like the ledger above it: without this the rows
    # exist only inside a `ToolMessage`'s JSON, so a client can show the explanation but not
    # the data.
    result_table = result.get("result_table")
    if result_table:
        update["result_table"] = result_table
    return update


async def _run(
    agent: Any,
    inbound: list[Any],
    config: RunnableConfig,
    *,
    recursion_limit: int,
    timeout: float | None = None,
    grace: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Drive the agent, keeping the last committed state. Returns ``(state, failure or None)``.

    **``stream`` rather than ``invoke``, so a crash does not erase work that really happened.**
    When ``agent_core`` raises, the outer node's super-step discards its writes, so a turn whose
    SQL had reached the database recorded ``attempts: []``. ``agent.aget_state(config)`` recovers
    the same ledger (measured: identical attempts), but streaming also works with **no
    checkpointer at all**, which is the eval and CLI configuration.

    ``stream_mode="values"`` and not ``"updates"``: a snapshot needs no reducers applied by hand,
    and folding updates here would be a second implementation of ``merge_by_call``.

    **``GraphInterrupt`` is re-raised untouched and returns no partial state** — capturing a
    partial update would commit half a paused turn. On resume the **outer** ``agent_core_node``
    body re-runs (measured: twice), which is why ``AttemptBook`` must take its count from the
    checkpointed channel and not from an in-memory set. Sibling tool tasks do *not* re-run: each
    tool call is its own ``Send``, so a ``run_query`` completed before the pause stays completed
    (probed on langgraph 1.2.10).

    **``recursion_limit`` is passed at the top level of config** (not under ``configurable``).
    ``create_agent`` binds 9999 via ``with_config``; a non-default value here is what actually
    bounds exploration tools that ``_CapEndsTheTurn`` does not cover.

    **Two bounds, because one cannot hold both guarantees.** ``timeout`` is the soft wall and is
    checked **between** frames; ``grace`` extends the wait for a single frame past it, and only
    that wait is ever cancelled.

    * *Between frames* is what keeps the ledger honest. Cancelling mid-``run_query`` leaves a
      statement that reached the database off the projected ledger, because the ``to_thread``
      worker keeps running — the cancel-vs-thread hazard ``wrap.py`` refuses for sync nodes.
      Executed-but-unrecorded is an audit break, and worse than being slow
      (``test_a_timeout_overlapping_in_flight_run_query_still_records_the_attempt``).
    * *But the soft wall alone cannot fire during a hang*, which is the only thing a wall exists
      for: no frame arrives, so ``async for`` never reaches the loop body. Measured — a stub whose
      first superstep never yields ran past a 0.3 s wall indefinitely. With ``wrap_node`` no
      longer bounding this node, that left ``agent_core`` with no wall clock at all.

    So: a tool still running when the soft wall expires gets ``grace`` to finish and record, and
    the turn ends at the next frame with its ledger intact. Only a wedge — nothing at all for
    ``timeout + grace`` — is cancelled, and there the missing attempt row is accepted because the
    alternative is a turn that never returns. See :func:`_hang_grace` for the sizing.

    Provider calls stay separately bounded by ``llm_timeout_s``.
    """
    from langgraph.errors import GraphInterrupt

    run_config: dict[str, Any] = {**config, "recursion_limit": int(recursion_limit)}
    last: dict[str, Any] = {}
    soft = (time.monotonic() + float(timeout)) if timeout is not None else None
    # `grace=None` collapses the ceiling onto the soft wall rather than removing it. A caller
    # that asks for a wall and forgets the grace gets a bound that fires — losing the in-flight
    # tool's chance to record, but never silently restoring the unbounded wait this fixed.
    hard = (soft + max(float(grace or 0.0), 0.0)) if soft is not None else None
    stream = agent.astream({"messages": inbound}, run_config, stream_mode="values")
    try:
        while True:
            # Soft wall first: reached only after a frame, so nothing is in flight to cut off.
            if soft is not None and time.monotonic() >= soft:
                return last, {"stage": "agent_core", "error_type": "TimeoutError"}
            if hard is None:
                frame = await anext(stream)
            else:
                frame = await asyncio.wait_for(anext(stream), max(hard - time.monotonic(), 0.0))
            if isinstance(frame, Mapping):
                last = dict(frame)
    except StopAsyncIteration:
        return last, None
    except GraphInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 — the ledger is the point, not the traceback
        # ``wait_for``'s ``TimeoutError`` lands here too, and wants no separate clause: it
        # already records ``error_type="TimeoutError"``, which is what the wall firing means.
        # ``type(exc).__name__`` and never ``str(exc)``: ADR 0006 §11 keeps exceptions as their
        # class because driver error text echoes the statement and its literals.
        return last, {"stage": "agent_core", "error_type": type(exc).__name__}
    finally:
        # The generator is abandoned on every path but the exhausted one; closing it lets
        # langgraph release the checkpointer work it holds instead of waiting for the GC.
        await _aclose(stream)


#: Budget for closing an abandoned ``astream``. Small and bounded because :func:`_aclose` runs
#: in a ``finally`` on the timeout path: anything that can block there would defeat the wall
#: clock it is cleaning up after, which is the defect this whole mechanism exists to fix.
_ACLOSE_BUDGET_S = 5.0


async def _aclose(stream: Any) -> None:
    """Best-effort close of an abandoned ``astream`` generator.

    Bounded and exception-swallowing on purpose. ``aclose`` throws ``GeneratorExit`` in at the
    suspension point, which on the timeout path is inside a cancelled tool call — it can raise,
    and it can block. Neither may replace the outcome the caller already decided.

    ``CancelledError`` is **not** caught: it is a ``BaseException``, and if the surrounding task
    is being cancelled the right thing is to let that through.
    """
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await asyncio.wait_for(close(), _ACLOSE_BUDGET_S)
    except Exception:  # noqa: BLE001 — cleanup is not an outcome
        pass


def _agent_node_timeout(state: Mapping[str, Any]) -> float | None:
    """Soft wall for the whole agent loop. Owned here, not by ``wrap_node``.

    ``wrap_node``'s ``wait_for`` turns a timeout into ``{failure, path_kind}`` only — the ledger
    that ``_run`` already streamed would be discarded. Applying the bound inside this node keeps
    the partial ledger; ``_run`` is where it fires, and it fires **between frames** so a tool that
    has already reached the database is never cut off before recording.

    **Resolved through :func:`~governed_bi.serve.runtime.float_knob`, like every other knob.**
    This previously took no state and read ``knob_default`` directly, which made
    ``agent_node_timeout_s`` a ``Role.comparability`` knob that no arm could set: two arms
    declaring different values recorded two configurations that had behaved identically.

    ``<= 0`` means **no wall**, which is why the return type is optional. ``"0"`` used to parse as
    a zero-second deadline that failed every turn on its first frame — a plausible thing to type
    for "off".
    """
    import os

    from governed_bi.serve.runtime import float_knob

    raw = os.environ.get("GOVERNED_BI_AGENT_NODE_TIMEOUT_S")
    if raw is not None and str(raw).strip() != "":
        return _positive(float(raw))
    return _positive(float_knob(state, "agent_node_timeout_s"))


def _hang_grace(state: Mapping[str, Any]) -> float:
    """Extra time one in-flight operation gets to finish *after* the soft wall expired.

    This is what keeps the two guarantees from trading against each other. The soft wall is
    checked between frames, which is audit-safe but cannot fire while the agent is hung, because
    no frame arrives. So the wait for each frame is additionally bounded by
    ``soft + grace`` — and only *that* cancels.

    Sized on ``llm_timeout_s``, the longest **bounded** single operation. A ``run_query`` has no
    bound of its own (there is no ``statement_timeout`` on the connection), so this is a ceiling
    on being wedged, not a promise that nothing was in flight when it fired. Reaching it means
    accepting that a statement may have executed without reaching the ledger — the lesser of two
    evils only because the alternative is a turn that never returns at all.
    """
    from governed_bi.serve.runtime import float_knob

    return max(float_knob(state, "llm_timeout_s"), 1.0)


def _positive(seconds: float) -> float | None:
    """``seconds``, or ``None`` when it is not a usable bound. See :func:`_agent_node_timeout`."""
    return seconds if seconds > 0 else None


def _recursion_limit(state: Mapping[str, Any]) -> int:
    """Nested-agent superstep ceiling from env, then knobs_resolved, then the register default."""
    import os

    from governed_bi.register.knobs import knob_default

    raw = os.environ.get("GOVERNED_BI_AGENT_RECURSION_LIMIT")
    if raw is not None and str(raw).strip() != "":
        return int(raw)
    knobs = state.get("knobs_resolved") or {}
    if knobs.get("agent_recursion_limit") is not None:
        return int(knobs["agent_recursion_limit"])
    return int(knob_default("agent_recursion_limit"))


def _question_message(state: dict, history: list[Any]) -> HumanMessage | None:
    """The turn's question as a human turn, or ``None`` if the history already carries it.

    On the server path the client's own human message *is* the question, so restating it would
    send it twice; on the CLI and eval paths ``Session.turn`` seeds an empty ``messages``, so
    this is the only place the question appears at all.
    """
    question = str(state.get("question") or "").strip()
    if not question:
        return None
    asked = any(
        str(getattr(m, "type", "")) == "human"
        and str(getattr(m, "content", "")).strip() == question
        for m in history
    )
    if asked:
        return None
    # The dataset hint, when a dataset supplied one. Empty on every production path. BIRD's
    # `evidence` names the value vocabulary and metric formula a question refers to without
    # stating ("residential areas refers to type = 'Residential'").
    evidence = str(state.get("evidence") or "").strip()
    if evidence:
        return HumanMessage(content=f"Question: {question}\nEvidence: {evidence}")
    return HumanMessage(content=f"Question: {question}")


def _context_middleware(state: dict) -> list[AgentMiddleware]:
    """Deliver ``delivery.context_block`` on every model call, without it entering ``messages``.

    Empty list when the turn rendered no block, so a turn with nothing to deliver builds exactly
    the agent it built before.

    **The block must not be a ``HumanMessage`` in the agent's inbound ``messages``**, or it
    renders in the live chat as the user's own bubble: LangGraph streams a nested graph's whole
    state under ``values|agent_core:<task_id>``, and the JS SDK applies the values of any
    namespace it does not recognise as a subagent straight onto *root* state
    (``@langchain/langgraph-sdk`` ``dist/ui/manager.js:413`` — the test for "subagent" is a
    ``tools:`` segment, which ``agent_core:<task_id>`` has none of). No SDK option suppresses
    it, and ``stream_subgraphs`` cannot be turned off (ADR 0010 M2).

    Not a ``SystemMessage`` and not appended to ``system_prompt``: ``prompt_set_hash`` is a
    ``Role.comparability`` field digesting the prompt registry, and
    ``tests/serve/test_model_inputs.py`` asserts every system prompt the model receives is one
    ``register/prompts.py`` declares. Per-turn text there makes the published hash describe
    something that was never sent.

    **The block goes immediately before the turn's question, not after it.** Last also means
    *newest*, and a wall of governance text arriving after a tool result reads as the newest
    thing the user said — observed live, the agent replied "Understood. I'll use the specified
    joins…" and the turn still stamped ``answered``. Anchoring to the question keeps the block
    off the end of both calls of a loop. Appended only when the request carries no human message
    at all, which no real path produces.
    """
    block = str((state.get("delivery") or {}).get("context_block") or "")
    if not block:
        return []

    # ``async def`` is load-bearing: ``wrap_model_call`` branches on ``iscoroutinefunction``, so
    # a sync body registers ``wrap_model_call`` only and the ``astream``-driven agent dies on
    # ``NotImplementedError: Asynchronous implementation of awrap_model_call is not available``.
    # ``_run`` cannot tell that from a provider error, so it surfaces as the turn crashing.
    @wrap_model_call
    async def deliver_context(
        request: ModelRequest, handler: Callable[[ModelRequest], Any]
    ) -> ModelResponse:
        return await handler(request.override(messages=_with_block(request.messages, block)))

    return [deliver_context]


def _with_block(messages: list[Any], block: str) -> list[Any]:
    """``messages`` with the context block inserted just before the last human turn."""
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if str(getattr(out[i], "type", "")) == "human":
            out.insert(i, HumanMessage(block))
            return out
    out.append(HumanMessage(block))
    return out


class _CapEndsTheTurn(ToolCallLimitMiddleware):
    """Make ``run_query_attempt_cap`` end the loop, which a refusing tool cannot do.

    A tool that returns a "capped" message hands back a ``Command`` with no ``goto``, so control
    returns to the model unchanged. Measured at ``cap=5, recursion_limit=60``: five statements,
    then **25** further model calls, then ``GraphRecursionError`` — and the crash discards the
    super-step, so the turn loses its ledger too. ``ToolCallLimitMiddleware``'s ``after_model``
    hook can ``jump_to`` end instead. The cost is one model call, because the cap fires on the
    *proposal* that would exceed it; measured, six model calls instead of thirty.

    **``thread_limit`` and not ``run_limit``.** ``run_tool_call_count`` is an ``UntrackedValue``
    and resets on every invocation, including the re-invocation an ``ask_user`` resume performs
    — a paused turn would get a second full budget. ``thread_tool_call_count`` is a
    ``PrivateStateAttr`` on the nested agent's checkpointed state, beside ``attempts_by_call``.

    **It writes the cap ledger row, because on this path the tool never runs.** Blocking happens
    before the tool node, so ``AttemptBook`` never sees the refused call and
    ``execution_from_attempts`` would report ``answered`` for a turn the cap ended. The row
    shares :data:`CAP_LEDGER_KEY` with the book's, so both enforcers firing still leaves one.

    **Constructed ``"continue"`` and ended here, not constructed ``"end"``.** Native's ``"end"``
    raises ``NotImplementedError`` when the AI message also calls a different tool, which
    ``_run`` records as ``crashed``; falling back to ``"continue"`` there restores the original
    defect. Ending anyway needs the stranded sibling calls answered, since a tool call with no
    ``ToolMessage`` is a history most providers reject on the *next* turn.
    """

    def __init__(self, cap: int) -> None:
        super().__init__(tool_name="run_query", thread_limit=int(cap), exit_behavior="continue")

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Native's counting, plus the jump and the ledger row it does not do.

        ``aafter_model`` delegates to this, so overriding the sync hook covers the async path.
        The decorator must be repeated: ``factory._get_can_jump_to`` reads ``__can_jump_to__``
        off whichever method the *subclass* defines, so an undecorated override silently loses
        the conditional edge.
        """
        update = super().after_model(state, runtime)
        blocked = [m for m in (update or {}).get("messages") or () if isinstance(m, ToolMessage)]
        if not update or not blocked:
            return update
        stranded = [
            ToolMessage(
                content=f"Not executed: the turn ended at the {self.tool_name} attempt cap.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
            for call in _sibling_tool_calls(state, self.tool_name)
        ]
        final = AIMessage(
            f"Stopped: {self.tool_name} reached its attempt limit of {self.thread_limit}."
        )
        emit(
            kind="tool",
            step="cap",
            status="cap",
            event_id=tool_event_id("cap", str(blocked[0].tool_call_id)),
            detail={"cap": self.thread_limit},
        )
        return {
            **update,
            "jump_to": "end",
            "messages": [*update["messages"], *stranded, final],
            "attempts_by_call": {CAP_LEDGER_KEY: cap_attempt()},
        }


def _sibling_tool_calls(state: Any, tool_name: str | None) -> list[Any]:
    """The last AI message's calls to tools *other* than ``tool_name`` — the stranded ones."""
    for message in reversed(list((state or {}).get("messages") or ())):
        if isinstance(message, AIMessage):
            return [tc for tc in message.tool_calls or () if tc.get("name") != tool_name]
    return []


def _stub(state: dict) -> dict:
    return {
        "path_kind": "answered",
        "messages": [{"role": "assistant", "content": STUB_ANSWER}],
        "usage": [
            {
                "turn_index": state.get("turn_index", 1),
                "stage": "agent_core",
                "model": "stub",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        ],
        "clarification_requested": False,
    }


def _last_executed_sql(attempts: Any) -> str | None:
    """The last statement the engine actually **sent** on the answering path, from the ledger.

    Filtered to :data:`~governed_bi.serve.ledger.ANSWERING_PATH`: a ``sample`` row also carries
    an ``executed_sql``, so a turn that sampled a column after its last ``run_query`` would
    record the sample's ``SELECT DISTINCT`` as ``generated_sql`` — which an eval re-executes and
    grades as the answer.

    Preferred over the model's ``run_query`` argument: ``canonicalise`` (ADR 0008 D2) and
    ``apply_row_limit`` rewrite it, so the two differ on every mixed-case identifier, and the
    ledger hashed the executed one. Falls back to the tool argument when nothing executed — a
    refused attempt still produced SQL worth recording.
    """
    last: str | None = None
    for attempt in answering_attempts(list(attempts or ())):
        if not isinstance(attempt, Mapping):
            continue
        sql = attempt.get("executed_sql")
        if sql:
            last = str(sql)
    return last


def _last_run_query_sql(messages: list[Any]) -> str | None:
    last: str | None = None
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for tc in m.tool_calls or ():
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name != "run_query":
                continue
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            sql = (args or {}).get("sql")
            if sql:
                last = str(sql)
    return last
