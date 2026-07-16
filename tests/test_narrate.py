"""Tests for the natural-language narrator (analyst.narrate).

The narrator is a presentation layer over an already-governed result: it phrases
the executed rows into English, is grounded only in what it is shown, and never
changes the SQL or the stamp. These tests pin the prompt content, the protocol
conformance, and the deterministic fallbacks.
"""

from __future__ import annotations

from governed_bi.llm import StaticChatClient
from governed_bi.analyst.answer import ResultTable
from governed_bi.analyst.narrate import AnswerNarrator, LlmAnswerNarrator


def test_narrator_returns_model_phrasing():
    chat = StaticChatClient("There are 554 customers.")
    result = ResultTable(columns=["customer_count"], rows=[(554,)], row_count=1)
    text = LlmAnswerNarrator(chat).narrate(
        "How many customers are there?",
        "SELECT COUNT(*) AS customer_count FROM customers",
        result,
    )
    assert text == "There are 554 customers."


def test_narrator_prompt_is_grounded_in_question_sql_and_rows():
    chat = StaticChatClient("ok")
    result = ResultTable(columns=["customer_count"], rows=[(554,)], row_count=1)
    LlmAnswerNarrator(chat).narrate(
        "How many customers are there?",
        "SELECT COUNT(*) AS customer_count FROM customers",
        result,
    )
    _system, user = chat.calls[-1]
    assert "How many customers are there?" in user
    assert "COUNT(*)" in user
    assert "554" in user  # the actual row value is shown to the model


def test_narrator_satisfies_protocol():
    assert isinstance(LlmAnswerNarrator(StaticChatClient("x")), AnswerNarrator)


def test_empty_model_response_falls_back_to_shape():
    narrator = LlmAnswerNarrator(StaticChatClient("   "))  # whitespace -> empty
    result = ResultTable(columns=["a", "b"], rows=[(1, 2), (3, 4)], row_count=2)
    assert narrator.narrate("q", "SELECT a, b FROM t", result) == "2 row(s) over [a, b]"


def test_empty_model_response_scalar_fallback():
    narrator = LlmAnswerNarrator(StaticChatClient(""))
    result = ResultTable(columns=["n"], rows=[(0,)], row_count=1)
    assert narrator.narrate("q", "SELECT ...", result) == "n = 0"


def test_no_rows_fallback():
    narrator = LlmAnswerNarrator(StaticChatClient(""))
    result = ResultTable(columns=["x"], rows=[], row_count=0)
    assert narrator.narrate("q", "SELECT ...", result) == "No rows matched."


def test_wide_result_caps_rows_shown_to_model():
    chat = StaticChatClient("ok")
    rows = [(i,) for i in range(100)]
    result = ResultTable(columns=["n"], rows=rows, row_count=100, truncated=True)
    LlmAnswerNarrator(chat).narrate("q", "SELECT n FROM t", result)
    _system, user = chat.calls[-1]
    assert "100 rows total" in user  # the model is told the full count, not shown all rows
