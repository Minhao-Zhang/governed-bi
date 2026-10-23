"""See ADR 0005."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Container, Mapping
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

from governed_bi.serve.agent_state import CAP_LEDGER_KEY, NO_ANSWER_CALL, GovernedAgentState
from governed_bi.serve.delivery import DeliveryTracker
from governed_bi.serve.events import emit, tool_event_id
from governed_bi.serve.ledger import answering_attempts, cap_attempt, execution_from_attempts
from governed_bi.serve.runtime import configurable
from governed_bi.serve.state import TERMINAL_PATH_KINDS
from governed_bi.serve.tools import analyst_prompt, build_tools, policy_from_config
from governed_bi.serve.usage import NO_TOKEN_USAGE, usage_row

__all__ = ["agent_core_node", "STUB_ANSWER", "NO_TOKEN_USAGE", "ANSWER_REMINDER", "NO_ANSWER_CALL"]

STUB_ANSWER = "STUB_ANSWER"

# Re-exported from ``serve/usage.py``. On ``__all__`` because tests import it here.


async def agent_core_node(state: dict, config: RunnableConfig) -> dict:
    """LangGraph namespaces the outer saver onto the nested agent. A saver here leaves zero checkpoints."""
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = configurable(config)
    model = cfg.get("agent_model")
    if model is None:
        return _stub(state)

    # Shared with the tool. A checkpointed accept from another turn carries a different id.
    epoch = str(state.get("turn_id") or "")
    tools = build_tools(state, config, answer_epoch=epoch)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=analyst_prompt(config),
        # Without the schema, LangGraph drops updates for keys the graph does not declare.
        state_schema=GovernedAgentState,
        middleware=[
            *_context_middleware(state, model),
            _CapEndsTheTurn(policy_from_config(config).run_query_attempt_cap, epoch),
            # One before_model node. A second spends a recursion step each loop, and the
            # sample-then-query cap then dies on the default limit before it can record itself.
            _EndsTheTurn(epoch),
        ],
    )

    # Out of ``messages``: it is checkpointed, so it would be re-sent.
    # The human-message count is what ``turn_index`` uses.
    history = list(state.get("messages") or [])
    question = _question_message(state, history)
    inbound = history + ([question] if question is not None else [])

    # The wall is applied in ``_run``, not ``wrap_node``, which would keep only
    # ``{failure, path_kind}`` and drop the streamed ledger.
    result, failure = await _run(
        agent,
        inbound,
        config,
        recursion_limit=_recursion_limit(state),
        timeout=_agent_node_timeout(state),
        grace=_hang_grace(state),
    )

    out_messages = list(result.get("messages") or [])
    fresh = [m for m in out_messages[len(inbound) :] if not _is_reminder(m)]

    # From the checkpointed channels, not closures on the tool objects.
    attempts = list((result.get("attempts_by_call") or {}).values())
    clarifications = list((result.get("clarifications_by_call") or {}).values())
    delivered = dict(result.get("tool_delivered") or {})

    # Eval re-executes this field, so a proposal that never reached ``prepare`` would be ungoverned SQL.
    generated_sql = _last_executed_sql(attempts)
    delivery = DeliveryTracker(delivered).merge_into(state.get("delivery"))
    usage = [usage_row(stage="agent_core", model=model, messages=fresh, turn_index=state.get("turn_index", 1))]
    execution = execution_from_attempts(attempts)

    update: dict[str, Any] = {
        # ``crashed`` and the ledger are independent. Finish still reads ``execution.terminal``
        # when ``path_kind`` is ``answered``.
        "path_kind": "crashed" if failure is not None else "answered",
        # Both ways of dying pass through this write, so unanswered ``tool_use`` is sealed here.
        "messages": _sealed(fresh, failure, paused=bool(result.get("__interrupt__"))),
        "usage": usage,
        "delivery": delivery,
        "clarification_requested": bool(result.get("clarification_requested")),
        "execution": execution,
    }
    if failure is not None:
        # ``wrap_node`` never sees this exception, so the marker is written in its shape.
        update["failure"] = failure
    if clarifications:
        update["clarifications"] = clarifications
    if generated_sql:
        update["generated_sql"] = generated_sql
    # Otherwise the rows exist only inside a tool message.
    result_table = result.get("result_table")
    if result_table:
        update["result_table"] = result_table
    accepted = bool(epoch) and result.get("answer_accepted_epoch") == epoch
    assumptions = result.get("assumptions")
    stored = list(assumptions) if isinstance(assumptions, list) else []
    if accepted:
        text = result.get("answer_text")
        if isinstance(text, str) and text.strip():
            update["answer_text"] = text.strip()
        update["assumptions"] = stored
        update["answer_tool_called"] = True
        update["guess_count"] = _guess_count(stored)
    else:
        update["guess_count"] = 0
    # Copied on every ending, including a refusal that then asks.
    update["ambiguous_guess_refusals"] = _as_count(result.get("ambiguous_guess_refusals"))
    if _ended_without_answer(result, failure, accepted=accepted, execution=execution):
        # Reminded once and still prose: that text is not the answer (ADR 0018 §1).
        update["path_kind"] = "crashed"
        update["failure"] = {"stage": "agent_core", "error_type": NO_ANSWER_CALL}
    return update


def _ended_without_answer(
    result: Mapping[str, Any], failure: Any, *, accepted: bool, execution: Mapping[str, Any]
) -> bool:
    if failure is not None or accepted or result.get("__interrupt__"):
        return False
    if result.get("clarification_requested") or execution.get("terminal") in ("refused", "capped"):
        return False
    return any(_is_reminder(m) for m in result.get("messages") or ())


async def _run(
    agent: Any,
    inbound: list[Any],
    config: RunnableConfig,
    *,
    recursion_limit: int,
    timeout: float | None = None,
    grace: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``stream`` keeps work ``invoke`` would discard; ``timeout`` is between frames and ``grace`` bounds one."""
    from langgraph.errors import GraphInterrupt

    run_config: dict[str, Any] = {**config, "recursion_limit": int(recursion_limit)}
    last: dict[str, Any] = {}
    soft = (time.monotonic() + float(timeout)) if timeout is not None else None
    # ``grace=None`` means the ceiling is the soft wall, not an unbounded wait.
    hard = (soft + max(float(grace or 0.0), 0.0)) if soft is not None else None
    stream = agent.astream({"messages": inbound}, run_config, stream_mode="values")
    try:
        while True:
            # Reached only after a frame, so nothing in flight is cut off.
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
        # Class name only: driver text echoes the statement and its literals.
        return last, {"stage": "agent_core", "error_type": type(exc).__name__}
    finally:
        # Close an abandoned generator so the checkpointer is not left to collection.
        await _aclose(stream)


_ACLOSE_BUDGET_S = 5.0


async def _aclose(stream: Any) -> None:
    """A raise must not replace the outcome; ``CancelledError`` is a ``BaseException`` and must propagate."""
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await asyncio.wait_for(close(), _ACLOSE_BUDGET_S)
    except Exception:  # noqa: BLE001 — cleanup is not an outcome
        pass


def _agent_node_timeout(state: Mapping[str, Any]) -> float | None:
    """``<= 0`` means no wall: a zero deadline would fail the turn on its first frame."""
    from governed_bi.registry.settings import effective_settings

    settings = state.get("settings_resolved") or {}
    if settings.get("agent_node_timeout_s") is not None:
        return _positive(float(settings["agent_node_timeout_s"]))
    return _positive(float(effective_settings()["agent_node_timeout_s"]))


def _hang_grace(state: Mapping[str, Any]) -> float:
    """No statement timeout, so this bounds a wedge and can leave an attempt off the ledger."""
    from governed_bi.serve.runtime import float_setting

    return max(float_setting(state, "llm_timeout_s"), 1.0)


def _positive(seconds: float) -> float | None:
    return seconds if seconds > 0 else None


def _recursion_limit(state: Mapping[str, Any]) -> int:
    from governed_bi.registry.settings import effective_settings

    settings = state.get("settings_resolved") or {}
    if settings.get("agent_recursion_limit") is not None:
        return int(settings["agent_recursion_limit"])
    return int(effective_settings()["agent_recursion_limit"])


def _question_message(state: dict, history: list[Any]) -> HumanMessage | None:
    """CLI and eval seed empty ``messages``, so this is the only copy of the question."""
    question = str(state.get("question") or "").strip()
    if not question:
        return None
    asked = any(
        str(getattr(m, "type", "")) == "human" and str(getattr(m, "content", "")).strip() == question for m in history
    )
    if asked:
        return None
    # Names vocabulary the question assumes. Empty on production paths.
    evidence = str(state.get("evidence") or "").strip()
    if evidence:
        return HumanMessage(content=f"Question: {question}\nEvidence: {evidence}")
    return HumanMessage(content=f"Question: {question}")


def _context_middleware(state: dict, model: Any = None) -> list[AgentMiddleware[Any, Any, Any]]:
    """See ADR 0010. Not a system message: ``prompt_set_hash`` digests the prompt registry only."""
    block = str((state.get("delivery") or {}).get("context_block") or "")
    if not block:
        return []
    cache_point = _cache_point(model)

    # ``async def``: a sync body registers only the sync hook, and ``astream`` then
    # raises ``NotImplementedError``.
    @wrap_model_call
    async def deliver_context(request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> ModelResponse:
        return await handler(request.override(messages=_with_block(request.messages, block, cache_point)))

    return [deliver_context]


def _cache_point(model: Any) -> dict[str, Any] | None:
    """A gateway may ignore it, so ``usage.model_calls`` makes the repeated share computable."""
    factory = getattr(model, "create_cache_point", None)
    if not callable(factory):
        return None
    try:
        point = factory()
    except Exception:  # noqa: BLE001 — a provider that cannot make one simply gets none
        return None
    return point if isinstance(point, dict) else None


def _with_block(messages: list[Any], block: str, cache_point: dict[str, Any] | None = None) -> list[Any]:
    """Placed after the question, the block reads as the newest user text."""
    content: Any = [block, cache_point] if cache_point else block
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if str(getattr(out[i], "type", "")) == "human":
            out.insert(i, HumanMessage(content=content))
            return out
    out.append(HumanMessage(content=content))
    return out


class _CapEndsTheTurn(ToolCallLimitMiddleware):
    """The cap, and the one reminder a prose ending gets. One node: each spends a recursion step.

    ``thread_limit`` survives resume; ``run_limit`` resets. Native ``end`` writes no ledger row,
    so the hook jumps.
    """

    def __init__(self, cap: int, epoch: str = "") -> None:
        super().__init__(tool_name="run_query", thread_limit=int(cap), exit_behavior="continue")
        self.epoch = epoch

    @hook_config(can_jump_to=["end", "model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """The decorator is repeated: an override without it loses ``can_jump_to``."""
        update = super().after_model(state, runtime)
        blocked = [m for m in (update or {}).get("messages") or () if isinstance(m, ToolMessage)]
        if not update or not blocked:
            return update or _remind_once(state, self.epoch)
        stranded = _stranded_replies(
            state,
            {m.tool_call_id for m in blocked},
            f"Not executed: the turn ended at the {self.tool_name} attempt cap.",
        )
        final = AIMessage(f"Stopped: {self.tool_name} reached its attempt limit of {self.thread_limit}.")
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


#: Sent once when the model ends in prose. Stripped from ``messages`` so it is not a question.
ANSWER_REMINDER = (
    "You ended without calling a tool. Finish by calling answer with the answer and its "
    "assumptions, or call ask_user if you need the person to choose. Prose alone is not an answer."
)


def _remind_once(state: Any, epoch: str) -> dict[str, Any] | None:
    """Back to the model once, when it ended in prose with nothing that ends a turn."""
    state = state or {}
    messages = list(state.get("messages") or ())
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or last.tool_calls or not epoch:
        return None
    if state.get("clarification_requested") or state.get("answer_accepted_epoch") == epoch:
        return None
    # A refused or capped ledger already decides the outcome; the prose there is not the answer.
    attempts = list((state.get("attempts_by_call") or {}).values())
    if execution_from_attempts(attempts)["terminal"] in ("refused", "capped"):
        return None
    if any(_is_reminder(m) for m in messages):
        return None
    return {"messages": [HumanMessage(ANSWER_REMINDER, id=f"{_REMINDER_ID}{epoch}")], "jump_to": "model"}


_REMINDER_ID = "answer-reminder-"


def _is_reminder(message: Any) -> bool:
    return str(getattr(message, "id", "") or "").startswith(_REMINDER_ID)


class _ClarificationEndsTheTurn(AgentMiddleware):
    """The tool does not jump to the outer end, which would skip ``finish``."""

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not (state or {}).get("clarification_requested"):
            return None
        return {"jump_to": "end"}


class _AnswerEndsTheTurn(AgentMiddleware):
    """An accepted answer ends the loop. A downgraded layer claim stays for one correction."""

    def __init__(self, epoch: str) -> None:
        super().__init__()
        self.epoch = epoch

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        accepted = (state or {}).get("answer_accepted_epoch")
        if not self.epoch or accepted != self.epoch:
            return None
        return {"jump_to": "end"}


class _EndsTheTurn(AgentMiddleware):
    """Both endings share one node, so the loop does not spend a step on each."""

    def __init__(self, epoch: str) -> None:
        super().__init__()
        self._clarification = _ClarificationEndsTheTurn()
        self._answer = _AnswerEndsTheTurn(epoch)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return (
            self._clarification.before_model(state, runtime)
            or self._answer.before_model(state, runtime)
        )


def _unanswered_tool_calls(state: Any, answered: Container[str]) -> list[Any]:
    """A name filter misses a second call to the capped tool when ``jump_to`` skips the tool node."""
    for message in reversed(list((state or {}).get("messages") or ())):
        if isinstance(message, AIMessage):
            return [tc for tc in message.tool_calls or () if tc.get("id") not in answered]
    return []


def _stranded_replies(state: Any, answered: Container[str], content: str) -> list[ToolMessage]:
    return [
        ToolMessage(content=content, tool_call_id=call["id"], name=call.get("name"), status="error")
        for call in _unanswered_tool_calls(state, answered)
    ]


#: ``{why}`` is an exception class name: driver text echoes the statement.
_ENDED_BEFORE_THE_TOOL_RAN = (
    "Not executed: the turn ended before this tool ran ({why}). Nothing ran and there is no "
    "result; call it again if the current question still needs it."
)


def _sealed(fresh: list[Any], failure: Mapping[str, Any] | None, *, paused: bool) -> list[Any]:
    """A paused turn is left alone, or resume breaks.

    Scoped to ``fresh``. Earlier history is not this write's to undo.
    """
    if paused:
        return fresh
    answered = {str(getattr(m, "tool_call_id", "")) for m in fresh if isinstance(m, ToolMessage)}
    why = str((failure or {}).get("error_type") or "the loop stopped")
    replies = _stranded_replies({"messages": fresh}, answered, _ENDED_BEFORE_THE_TOOL_RAN.format(why=why))
    return [*fresh, *replies] if replies else fresh


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


def _guess_count(assumptions: list[Any]) -> int:
    return sum(
        1 for item in assumptions if isinstance(item, Mapping) and item.get("basis") == "guess"
    )


def _as_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _last_executed_sql(attempts: Any) -> str | None:
    """See ADR 0008. ``sample`` rows are excluded.

    Never the model's argument: canonicalise and the row limit rewrite it.
    """
    last: str | None = None
    for attempt in answering_attempts(list(attempts or ())):
        if not isinstance(attempt, Mapping):
            continue
        sql = attempt.get("executed_sql")
        if sql:
            last = str(sql)
    return last
