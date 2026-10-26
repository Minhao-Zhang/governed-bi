"""Reading more rows with OFFSET must not replace the statement the answer delivers.

In both baseline runs, every answer whose delivered SQL was a page (``LIMIT 20 OFFSET 20``) was wrong.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.corpus.analyst import analyst_corpus_from_keys
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.paging import is_page_of
from governed_bi.serve.scripted_model import ScriptedChatModel, answer_call
from governed_bi.serve.wrap import wrap_node

TABLE = "beer_factory.customers"
FULL = f"SELECT id FROM {TABLE}"
PAGE = f"SELECT id FROM {TABLE} ORDER BY id LIMIT 20 OFFSET 20"


def _query(sql: str, call_id: str) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": "run_query", "args": {"sql": sql}, "id": call_id}])


class _TwentyFiveRows:
    dialect = "postgres"

    def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        if "OFFSET" in sql.upper():
            return (["id"], [(i,) for i in range(21, 26)], False)
        return (["id"], [(i,) for i in range(1, 26)], False)


def _turn(*calls: AIMessage) -> dict[str, Any]:
    state: dict[str, Any] = {
        "turn_index": 1,
        "turn_id": "t-page",
        "messages": [],
        "usage": [],
        "retrieved_tables": [TABLE],
    }
    config = {
        "configurable": {
            "thread_id": "t-page",
            "policy": GovernancePolicy(),
            "agent_model": ScriptedChatModel(responses=[*calls, answer_call("25 customers")]),
            "connector": _TwentyFiveRows(),
            "corpus": analyst_corpus_from_keys(allowed=[f"{TABLE}.id"]),
        }
    }
    return asyncio.run(wrap_node("agent_core", agent_core_node)(state, config))


def test_the_answer_delivers_the_full_statement_not_the_page() -> None:
    out = _turn(_query(FULL, "q1"), _query(PAGE, "q2"))

    assert "OFFSET" not in out["generated_sql"].upper(), out["generated_sql"]
    assert out["result_table"]["row_count"] == 25
    attempts = out["execution"]["attempts"]
    assert [bool(a.get("reads_page")) for a in attempts] == [False, True]


def test_the_agent_is_told_the_preview_is_not_the_whole_answer() -> None:
    out = _turn(_query(FULL, "q1"))

    replies = [m.content for m in out["messages"] if getattr(m, "type", "") == "tool"]
    assert any("do not re-run it with LIMIT or OFFSET" in str(r) for r in replies), replies


def test_a_top_one_after_a_list_is_still_an_answer() -> None:
    assert not is_page_of(f"SELECT id FROM {TABLE} ORDER BY id DESC LIMIT 1", [FULL])
    assert not is_page_of(f"SELECT id FROM {TABLE} LIMIT 20 OFFSET 0", [FULL])
    assert not is_page_of(f"SELECT id FROM {TABLE} WHERE id > 3 LIMIT 20 OFFSET 20", [FULL])
    assert is_page_of(PAGE, [FULL])
