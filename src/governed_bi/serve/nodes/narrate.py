"""Say the answer in a sentence, after ``agent_core``. See ADR 0002, amendment 3.

Adopts the answer tool's text, then the agent's prose, and generates only when both are
missing. A capped turn always generates: its last message is the budget's exit line, and
adopting it would present that line as the answer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp

from governed_bi.serve.ledger import ledger_ended_without_answer
from governed_bi.serve.messages import last_ai_text
from governed_bi.serve.runtime import configurable, prompt_variants

__all__ = ["narrate_node"]

#: How much of the result set the narrator is shown. It needs the answer, not the dataset, and
#: a 200,001-row cap upstream means "the rows" is not a bounded thing to paste into a prompt.
_ROWS_SHOWN = 20

#: Prepended on a capped turn. The payload cannot say the turn ran out of budget, or that the
#: statement shown is the last one that succeeded, usually a probe. Without it the narrator
#: reads an intermediate result set out loud as the answer.
_CAP_PREAMBLE = (
    "IMPORTANT — this turn ended at its statement-attempt budget, not at a conclusion. The "
    "statement and result below are the LAST ONE THAT SUCCEEDED, which on a capped turn is "
    "usually an intermediate step rather than an answer to the question. Report how far the "
    "turn got and state plainly that it is incomplete. Do not present these rows as the "
    "answer, and do not extrapolate one.\n\n"
)


def _ended_at_the_cap(state: Mapping[str, Any]) -> bool:
    """Cap only. ``refused`` is the same to reflect, and not to the narrator."""
    execution = state.get("execution")
    if not isinstance(execution, Mapping):
        return False
    return execution.get("terminal") == "capped"


async def narrate_node(state: dict, config: RunnableConfig) -> dict:
    """Write ``answer_text``. Adopt the tool's text, then the agent's prose.

    Returns ``{}`` when there is nothing to say. Never raises into the wrapper: a crash would
    mark an answered turn crashed. A capped turn generates, so the rows get a sentence.
    """
    # A terminal turn already has its wording, and it is not the model's: `refuse` and `decline`
    # write `answer["text"]`, which is system copy, and narrating over it would put a generated
    # sentence where a governance decision belongs.
    if state.get("path_kind") in ("refuse", "decline", "crashed"):
        return {}
    capped = _ended_at_the_cap(state)
    if ledger_ended_without_answer(state) and not capped:
        return {}

    try:
        if not capped:
            tool_text = state.get("answer_text")
            if isinstance(tool_text, str) and tool_text.strip():
                return {"answer_text": tool_text.strip()}
            adopted = last_ai_text(state)
            if adopted:
                return {"answer_text": adopted.strip()}

        result = state.get("result_table")
        if not isinstance(result, Mapping):
            # No prose and no rows: inventing a sentence here would be the interface asserting an
            # answer the turn did not produce.
            return {}

        text, spent = await _generate(state, config, result, capped=capped)
        update: dict = {"answer_text": text}
        if spent is not None:
            update["usage"] = [spent]
        return update
    except GraphBubbleUp:
        # ``GraphInterrupt`` is a ``GraphBubbleUp``. Catching it below would return ``{}``
        # and drop the pause. The wrapper re-raises it so the checkpointer can resume.
        raise
    except Exception:  # noqa: BLE001 — observer/narrator must not fail the turn
        return {}


async def _generate(
    state: Mapping[str, Any],
    config: RunnableConfig,
    result: Mapping[str, Any],
    *,
    capped: bool = False,
) -> tuple[str | None, dict | None]:
    """``(None, None)`` if it cannot run. The cost is returned beside the text."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.registry.prompts import prompt_text
    from governed_bi.serve.usage import usage_row

    model = configurable(config).get("utility_model")
    if model is None:
        return None, None

    try:
        reply = await model.ainvoke(
            [
                SystemMessage(prompt_text("narrate", prompt_variants(config))),
                HumanMessage(_brief(state, result, capped=capped)),
            ],
            # Named after the registered prompt, like the scope check and the rewriters.
            config={"run_name": "narrate"},
        )
        text = str(getattr(reply, "text", "") or "").strip()
    except GraphBubbleUp:  # see :func:`narrate_node` — a pause is not a failed sentence
        raise
    except Exception:  # noqa: BLE001 — see the docstring: a lost sentence is not a lost turn
        return None, None
    spent = usage_row(
        stage="narrate", model=model, messages=reply, turn_index=state.get("turn_index", 1)
    )
    return (text or None), spent


def _brief(state: Mapping[str, Any], result: Mapping[str, Any], *, capped: bool = False) -> str:
    """The question, the statement, and a bounded slice of rows. Not the delivered context.

    The transcript and the retrieved assets would invite the model to re-decide a conclusion
    that has already been through governance.
    """
    rows = result.get("rows") or []
    shown = list(rows)[:_ROWS_SHOWN]
    payload: dict[str, Any] = {
        "columns": result.get("columns"),
        "rows": shown,
        "row_count": result.get("row_count"),
    }
    # Said in words: "20 of 9,590" as two bare numbers in a JSON blob is the kind of thing a
    # model narrates as if it were the answer.
    if len(rows) > len(shown):
        payload["note"] = f"showing the first {len(shown)} of {len(rows)} returned rows"
    # First, not appended: on a capped turn this reframes everything below it, and a caveat
    # after the rows is one the narrator has already written past.
    preamble = _CAP_PREAMBLE if capped else ""
    return (
        f"{preamble}"
        f"Question: {state.get('question') or ''}\n\n"
        f"Statement:\n{state.get('generated_sql') or '(none)'}\n\n"
        f"Result:\n{json.dumps(payload, default=str)}"
    )
