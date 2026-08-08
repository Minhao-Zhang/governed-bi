"""``narrate`` — the turn's last step: say the answer in a sentence.

**A node and not a boundary patch.** ``answer.answer_text`` is what the answer card reads, and
it used to be written only in ``routes._shape`` at the REST boundary — so ``POST /chat`` had an
answer and the streamed path, which is the one the UI uses, rendered the SQL and the ledger
with no answer on it. This sits after ``agent_core``, where every answering path funnels.

**It usually calls no model.** The agent narrates for free, so the normal path is to *adopt*
that text; the model runs only on the remainder — a loop that ended on a tool call, or on
reasoning blocks with no text at all — which is the case where there is nothing to fall back to.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.messages import last_ai_text
from governed_bi.serve.runtime import configurable

__all__ = ["narrate_node"]

#: How much of the result set the narrator is shown. It needs the answer, not the dataset, and
#: a 200,001-row cap upstream means "the rows" is not a bounded thing to paste into a prompt.
_ROWS_SHOWN = 20


async def narrate_node(state: dict, config: RunnableConfig) -> dict:
    """Write ``answer_text``. Adopts the agent's prose; generates only when there is none.

    Returns ``{}`` rather than ``{"answer_text": None}`` on the paths with nothing to say: the
    channel keeps its reset value and ``stamp`` reads null either way, but the *update* says
    which of the two happened.
    """
    # A terminal turn already has its wording, and it is not the model's: `refuse` and `decline`
    # write `answer["text"]`, which is system copy, and narrating over it would put a generated
    # sentence where a governance decision belongs.
    if state.get("path_kind") in ("refuse", "decline", "crashed"):
        return {}

    adopted = last_ai_text(state)
    if adopted:
        return {"answer_text": adopted.strip()}

    result = state.get("result_table")
    if not isinstance(result, Mapping):
        # No prose and no rows: inventing a sentence here would be the interface asserting an
        # answer the turn did not produce.
        return {}

    text, spent = await _generate(state, config, result)
    update: dict = {"answer_text": text}
    if spent is not None:
        update["usage"] = [spent]
    return update


async def _generate(
    state: Mapping[str, Any], config: RunnableConfig, result: Mapping[str, Any]
) -> tuple[str | None, dict | None]:
    """One utility-model call over (question, statement, rows). ``(None, None)`` if it cannot run.

    Returns its cost beside its text: a model call the ledger does not know about is a turn
    priced below what it spent, and a cost appearing on some turns and not others is the kind
    that averages into invisibility.

    **A failure here does not fail the turn.** The answer, the SQL and the ledger are already
    computed; the answer card falls back to the system copy and the stage event reports the
    error, so the absence degrades visibly rather than hiding.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.register.prompts import prompt_text
    from governed_bi.serve.usage import usage_row

    model = configurable(config).get("utility_model")
    if model is None:
        return None, None

    try:
        reply = await model.ainvoke(
            [
                SystemMessage(prompt_text("narrate")),
                HumanMessage(_brief(state, result)),
            ],
            # Named after the registered prompt, like the scope gate and the rewriters.
            config={"run_name": "narrate"},
        )
        text = str(getattr(reply, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — see the docstring: a lost sentence is not a lost turn
        return None, None
    spent = usage_row(
        stage="narrate", model=model, messages=reply, turn_index=state.get("turn_index", 1)
    )
    return (text or None), spent


def _brief(state: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """The narrator's whole input: the question, the statement, and a bounded slice of rows.

    Deliberately not the delivered context, the retrieved assets or the transcript: this stage
    reads a table out loud, and handing it the material the agent reasoned over invites it to
    re-decide a conclusion that has already been through governance.
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
    return (
        f"Question: {state.get('question') or ''}\n\n"
        f"Statement:\n{state.get('generated_sql') or '(none)'}\n\n"
        f"Result:\n{json.dumps(payload, default=str)}"
    )
