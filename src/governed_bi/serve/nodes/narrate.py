"""``narrate`` — the turn's last step: say the answer in a sentence.

**The narration was never missing. It was never delivered.** Measured on a live turn asking
"how many restaurants are there in total": the agent's closing message reads *"There are
**9,590 restaurants** in total."*, `result_table` holds `[[9590]]`, the governance ledger shows
one passing attempt — and the interface rendered the SQL, the ledger and the provenance drawer
with **no answer anywhere on it**. The answer card reads `answer.answer_text`, and nothing in
the graph ever wrote that field.

It was written in one place: `routes._shape`, at the REST boundary, from `last_ai_text`. So
`POST /chat` had an answer and the streamed path did not — and the streamed path is the one the
UI uses. A boundary patch that fixes one of two transports is how a defect hides behind a route
that passes.

**So this is a node, not another boundary patch**, and it sits after `agent_core` where every
answering path funnels. Whatever a transport does with the state, the state now has the answer
in it.

**It usually calls no model.** The agent narrates for free — that is what the measurement above
shows — so the normal path is to *adopt* that text. Generating a second, better-worded sentence
would pay a call per turn, at the very end where the user is already waiting, to replace prose
that was correct. The model runs only on the remainder: a loop that ended on a tool call, or on
reasoning blocks with no text at all. That case is exactly the one the interface could not
survive, because it is the case where there is nothing to fall back to.
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


def narrate_node(state: dict, config: RunnableConfig) -> dict:
    """Write ``answer_text``. Adopts the agent's prose; generates only when there is none.

    Returns ``{}`` rather than ``{"answer_text": None}`` on the paths with nothing to say, so a
    turn that legitimately has no narration is distinguishable from one where this node ran and
    produced nothing — the channel keeps its reset value and ``stamp`` reads null either way,
    but the *update* says which happened.
    """
    # A terminal turn already has its wording, and it is not the model's. `refuse` and `decline`
    # write `answer["text"]`, which is system copy this repository owns, and narrating over it
    # would put a generated sentence where a governance decision belongs.
    if state.get("path_kind") in ("refuse", "decline", "crashed"):
        return {}

    adopted = last_ai_text(state)
    if adopted:
        return {"answer_text": adopted.strip()}

    result = state.get("result_table")
    if not isinstance(result, Mapping):
        # No prose and no rows. There is nothing to narrate from, and inventing a sentence here
        # would be the interface asserting an answer the turn did not produce.
        return {}

    return {"answer_text": _generate(state, config, result)}


def _generate(state: Mapping[str, Any], config: RunnableConfig, result: Mapping[str, Any]) -> str | None:
    """One utility-model call over (question, statement, rows). ``None`` if it cannot run.

    **The utility model, and a failure here does not fail the turn.** The answer, the SQL and
    the ledger are all already computed and correct; losing the sentence costs the reader a
    convenience, and raising would cost them a turn they paid for. The absence is visible —
    the answer card falls back to the system copy and the stage event reports the error — which
    is the difference between degrading and hiding.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.register.prompts import prompt_text

    model = configurable(config).get("utility_model")
    if model is None:
        return None

    try:
        reply = model.invoke(
            [
                SystemMessage(prompt_text("narrate")),
                HumanMessage(_brief(state, result)),
            ]
        )
        text = str(getattr(reply, "text", "") or "").strip()
    except Exception:  # noqa: BLE001 — see the docstring: a lost sentence is not a lost turn
        return None
    return text or None


def _brief(state: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """The narrator's whole input: the question, the statement, and a bounded slice of rows.

    Deliberately not the delivered context, the retrieved assets or the transcript. This stage
    is reading a table out loud, not re-deciding anything, and handing it the material the agent
    reasoned over would invite it to reason again — at the end of the turn, with a fast model,
    against a conclusion that has already been through governance.
    """
    rows = result.get("rows") or []
    shown = list(rows)[:_ROWS_SHOWN]
    payload: dict[str, Any] = {
        "columns": result.get("columns"),
        "rows": shown,
        "row_count": result.get("row_count"),
    }
    # Said in words, because "20 of 9,590" as two numbers in a JSON blob is the kind of thing a
    # model narrates as if it were the answer.
    if len(rows) > len(shown):
        payload["note"] = f"showing the first {len(shown)} of {len(rows)} returned rows"
    return (
        f"Question: {state.get('question') or ''}\n\n"
        f"Statement:\n{state.get('generated_sql') or '(none)'}\n\n"
        f"Result:\n{json.dumps(payload, default=str)}"
    )
