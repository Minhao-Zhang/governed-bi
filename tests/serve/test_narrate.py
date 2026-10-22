"""``narrate`` writes ``answer_text``. The card has nothing else to read as the answer.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from governed_bi.serve.nodes.narrate import narrate_node

RESULT = {"columns": ["restaurant_count"], "rows": [[9590]], "row_count": 1, "truncated": False}

#: The synthetic message ``_CapEndsTheTurn.after_model`` appends at ``cap=5``.
#:
#: **A duplicate of the f-string in ``agent_core.py``, not a read of it.** The middleware builds
_CAP_EXIT_LINE = "Stopped: run_query reached its attempt limit of 5."


class _Recorder:
    """A model that records what it was asked and answers a fixed sentence."""

    def __init__(self, text: str = "There are 9,590 restaurants.") -> None:
        self.calls: list[list[Any]] = []
        self.configs: list[Any] = []
        self._text = text

    def invoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        # ``config=`` is part of ``BaseChatModel.invoke``'s real signature, and this fake
        # omitted it. When the callers began naming their runs for the trace, the fake
        # raised ``TypeError`` and the caller's ``except`` reported it as a provider
        self.configs.append(config)
        self.calls.append(messages)
        return AIMessage(self._text)

    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        """The nodes await now, and a double that only offers ``invoke`` fails them open."""
        return self.invoke(messages, config, **kwargs)

def _config(model: Any) -> dict[str, Any]:
    return {"configurable": {"utility_model": model}}


def test_the_agents_own_sentence_is_adopted_and_no_model_is_called() -> None:
    """The common path, and the reason this stage is nearly free."""
    model = _Recorder()
    state = {
        "path_kind": "answered",
        "question": "how many restaurants are there in total",
        "result_table": RESULT,
        "messages": [
            HumanMessage("how many restaurants are there in total"),
            AIMessage("There are **9,590 restaurants** in total."),
        ],
    }

    out = asyncio.run(narrate_node(state, _config(model)))

    assert out == {"answer_text": "There are **9,590 restaurants** in total."}
    assert model.calls == [], "the agent had already answered; a second call buys nothing"


def test_a_loop_that_ended_without_prose_gets_a_generated_sentence() -> None:
    """The case the interface could not survive."""
    model = _Recorder()
    state = {
        "path_kind": "answered",
        "question": "how many restaurants are there in total",
        "generated_sql": 'SELECT COUNT("restaurant_id") FROM "restaurant"."allgemeine_informationen"',
        "result_table": RESULT,
        "messages": [
            HumanMessage("how many restaurants are there in total"),
            AIMessage("", tool_calls=[{"name": "run_query", "args": {}, "id": "t1"}]),
            ToolMessage("{}", tool_call_id="t1"),
        ],
    }

    out = asyncio.run(narrate_node(state, _config(model)))

    assert out["answer_text"] == "There are 9,590 restaurants."
    assert len(model.calls) == 1
    brief = model.calls[0][1].content
    assert "9590" in brief and "restaurant_id" in brief, "the narrator needs the rows and the SQL"

    # **The call is billed to this stage.** It is a rare path — the stage normally adopts the
    # agent's prose and calls nothing — and a cost that appears on some turns and not others is
    # exactly the kind that gets averaged into invisibility if it is never recorded at all.
    assert [row["stage"] for row in out["usage"]] == ["narrate"]
    assert out["usage"][0]["turn_index"] == state.get("turn_index", 1)


def test_a_refusal_keeps_the_systems_own_wording() -> None:
    """`refuse` and `decline` write `answer["text"]` themselves, and that copy is not the
    model's to paraphrase. A generated sentence over a governance decision would be the
    interface restating a refusal in words nobody reviewed."""
    model = _Recorder()
    for path_kind in ("refuse", "decline", "crashed"):
        out = asyncio.run(narrate_node(
            {"path_kind": path_kind, "question": "q", "result_table": RESULT, "messages": []},
            _config(model),
        ))
        assert out == {}, f"{path_kind} must not be narrated"
    assert model.calls == []


def _capped_state(result: Any = RESULT, last_ai: str = _CAP_EXIT_LINE) -> dict[str, Any]:
    """A turn whose statements ran and whose budget then ended it.

    ``last_ai`` defaults to the real synthetic message ``_CapEndsTheTurn`` appends, because that
    is what is sitting there to be adopted.
    """
    return {
        "path_kind": "answered",
        "question": "which chillers ran the most hours",
        "generated_sql": "SELECT DISTINCT assetdesc FROM work_orders WHERE assetdesc ILIKE '%CHILLER%'",
        "result_table": result,
        "messages": [HumanMessage("q"), AIMessage(last_ai)],
        "execution": {"terminal": "capped", "attempts": [], "guardrail_errors": 0},
    }


def test_an_all_refused_ledger_is_not_narrated() -> None:
    """``agent_core`` still writes ``path_kind: answered``; finish reclassifies from the ledger.

    A refused turn had every attempt rejected, so it executed nothing. Narrating it would put
    generated prose over a governance decision and invent a result to go with it.
    """
    model = _Recorder()
    out = asyncio.run(
        narrate_node(
            {
                "path_kind": "answered",
                "question": "q",
                "result_table": RESULT,
                "messages": [HumanMessage("q"), AIMessage("There are nine thousand restaurants.")],
                "execution": {"terminal": "refused", "attempts": [], "guardrail_errors": 0},
            },
            _config(model),
        )
    )
    assert out == {}, "a refused ledger must not be narrated"
    assert model.calls == []


def test_a_capped_turn_is_narrated_because_its_statements_ran() -> None:
    """The revision."""
    model = _Recorder("It found nine chiller assets before running out of attempts.")
    out = asyncio.run(narrate_node(_capped_state(), _config(model)))

    assert out.get("answer_text"), "a capped turn that executed statements has something to say"
    assert len(model.calls) == 1, "it must generate; see the adopt test below for why"


def test_a_capped_turn_generates_instead_of_adopting_the_cap_exit_line() -> None:
    """Why generation, not adoption, is the fix."""
    model = _Recorder("Nine chiller assets found; the run-hours question stayed open.")
    out = asyncio.run(narrate_node(_capped_state(), _config(model)))

    assert out["answer_text"] != _CAP_EXIT_LINE
    assert _CAP_EXIT_LINE not in (out.get("answer_text") or "")


def test_the_narrator_is_told_the_capped_turn_is_incomplete() -> None:
    """The honesty guard, and the reason narrating a cap is not the decoration it replaced."""
    model = _Recorder()
    asyncio.run(narrate_node(_capped_state(), _config(model)))

    brief = model.calls[0][-1].content
    assert "budget" in brief.lower(), brief[:200]
    assert "incomplete" in brief.lower(), brief[:200]
    assert brief.index("IMPORTANT") < brief.index("Result:"), (
        "the caveat must precede the rows; after them it is one the narrator has written past"
    )


def test_a_capped_turn_with_no_rows_is_still_not_narrated() -> None:
    """The cap exception is earned by having executed something, not by being capped.

    A turn that burned its budget on refused or probing statements and returned no table has
    nothing to report, and generating from the exit line alone would be inventing a result.
    """
    model = _Recorder()
    out = asyncio.run(narrate_node(_capped_state(result=None), _config(model)))

    assert out == {}
    assert model.calls == []


def test_a_dead_narrator_costs_the_sentence_and_not_the_turn() -> None:
    """The answer, the SQL and the ledger are computed and correct before this node runs.

    Raising here would trade a turn the user paid for against a convenience. The absence is
    visible instead — `answer_text` stays null and the card falls back to the system copy.
    """

    class _Dead:
        def invoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
            raise RuntimeError("provider down")

    state = {
        "path_kind": "answered",
        "question": "q",
        "result_table": RESULT,
        "messages": [HumanMessage("q")],
    }
    # No `usage` key on either: a call that raised and a call that never happened both cost
    # nothing this code can attest to, and writing a zero row would be the ledger inventing
    # a measurement — the same defect `Measured.unmeasured` exists to prevent one level in.
    assert asyncio.run(narrate_node(state, _config(_Dead()))) == {"answer_text": None}
    # And with no utility model configured at all.
    assert asyncio.run(narrate_node(state, {"configurable": {}})) == {"answer_text": None}


def test_nothing_is_invented_when_there_is_no_prose_and_no_rows() -> None:
    """No text to adopt and no table to read: there is nothing to narrate *from*, and a sentence
    written here would be the interface asserting an answer the turn did not produce."""
    model = _Recorder()
    out = asyncio.run(narrate_node(
        {"path_kind": "answered", "question": "q", "messages": [HumanMessage("q")]},
        _config(model),
    ))
    assert out == {}
    assert model.calls == []
