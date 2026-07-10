"""Server step (optional): phrase an executed result into natural language.

After the guardrails pass and the query executes, the flow renders a *compact*
textual answer (``flow._render``) - fine for a single number, but only a
"N row(s) over [cols]" shape for a table. When a model client is available, this
optional seam replaces that with a grounded natural-language sentence or two,
phrased from the actual rows - so the chat surfaces plain English *and* the audit
table, not just a shape.

Grounded by construction: the narrator is shown only the question, the SQL, and
the (already bounded) result grid, and is told to state only what the rows show
and never to invent a number. It is a **presentation layer over an
already-governed result** - it never changes the SQL, the guardrail verdict, or
the reliability stamp, so it cannot turn a refusal into an answer or move an
answer's tier. The client is injected behind the :class:`ChatClient` protocol
(tests use a scripted ``StaticChatClient``; production a LangChain client), and
when no narrator is supplied the flow falls back to the compact render.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..llm import ChatClient
    from .answer import ResultTable

# How many result rows to show the model. The result is already capped upstream
# (gateway + ResultTable preview); this bounds prompt size for a wide result.
_MAX_PROMPT_ROWS = 30

_NARRATOR_SYSTEM = """\
You turn the result of a database query into a short, plain-English answer for a \
business user.

Rules:
- Answer the user's question directly, using ONLY the values in the result rows. \
Never invent, estimate, or round beyond what is shown.
- Be concise: one or two sentences. Do not restate the SQL or mention tables, \
columns, or "the query".
- If the result is a single value, state it plainly.
- If it is a list/ranking, summarise the top rows and note how many there are in \
total; do not read out every row (the full table is shown alongside your answer).
- If the result has no rows, say that nothing matched.
"""


@runtime_checkable
class AnswerNarrator(Protocol):
    """Turns (question, SQL, executed result) into a natural-language answer."""

    def narrate(self, question: str, sql: str, result: "ResultTable") -> str:
        ...


def _render_result_for_prompt(result: "ResultTable") -> str:
    """A compact text rendering of the result grid for the model prompt."""
    if not result.rows:
        return "(no rows)"
    header = " | ".join(result.columns)
    lines = [header, "-" * len(header)]
    for row in result.rows[:_MAX_PROMPT_ROWS]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    if result.row_count > _MAX_PROMPT_ROWS:
        lines.append(f"... ({result.row_count} rows total)")
    return "\n".join(lines)


def _fallback_text(result: "ResultTable") -> str:
    """A deterministic answer if the model returns nothing (never leave it blank)."""
    if result.row_count == 0:
        return "No rows matched."
    if result.row_count == 1 and len(result.columns) == 1:
        return f"{result.columns[0]} = {result.rows[0][0]}"
    return f"{result.row_count} row(s) over [{', '.join(result.columns)}]"


class LlmAnswerNarrator:
    """Model-backed narrator: phrases the result grid into grounded English.

    Construct with any :class:`~governed_bi.llm.ChatClient` (a scripted
    ``StaticChatClient`` in tests, a ``LangChainChatClient`` in production). Falls
    back to a deterministic shape summary if the model returns an empty response,
    so the answer text is never blank. Implements :class:`AnswerNarrator`.
    """

    def __init__(self, chat: "ChatClient") -> None:
        self.chat = chat

    def narrate(self, question: str, sql: str, result: "ResultTable") -> str:
        user = (
            f"Question: {question}\n\n"
            f"SQL that ran:\n{sql}\n\n"
            f"Result:\n{_render_result_for_prompt(result)}"
        )
        text = self.chat.complete(_NARRATOR_SYSTEM, user).strip()
        return text or _fallback_text(result)
