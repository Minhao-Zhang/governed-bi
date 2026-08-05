"""``narrate`` writes the sentence the answer card reads.

The defect these pin: a turn answered, ran one governed statement, returned `[[9590]]`, and the
interface showed SQL, a ledger and a provenance drawer with **no answer on it**. The card reads
`answer.answer_text`; nothing in the graph wrote it. It was written at the REST boundary only,
so `POST /chat` had an answer and the streamed path — the one the UI uses — did not.

Two behaviours worth a test each, because each is a decision rather than an implementation
detail: the node **adopts** the agent's prose instead of paying for a second opinion, and it
**generates** only when there is no prose to adopt.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from governed_bi.serve.nodes.narrate import narrate_node

RESULT = {"columns": ["restaurant_count"], "rows": [[9590]], "row_count": 1, "truncated": False}


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
        # failure — a fake narrower than the interface turning a code change into a
        # plausible-looking wrong verdict. It is recorded so a caller cannot pass one
        # silently, and ignored because nothing here reads it.
        self.configs.append(config)
        self.calls.append(messages)
        return AIMessage(self._text)


def _config(model: Any) -> dict[str, Any]:
    return {"configurable": {"utility_model": model}}


def test_the_agents_own_sentence_is_adopted_and_no_model_is_called() -> None:
    """The common path, and the reason this stage is nearly free.

    Measured on a live turn, the agent's closing message is *"There are **9,590 restaurants** in
    total."* — a correct narration the loop produced on its way to finishing. Generating a
    second one would pay a model call per turn, at the very end where the user is already
    waiting, to replace prose that was already right.
    """
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

    out = narrate_node(state, _config(model))

    assert out == {"answer_text": "There are **9,590 restaurants** in total."}
    assert model.calls == [], "the agent had already answered; a second call buys nothing"


def test_a_loop_that_ended_without_prose_gets_a_generated_sentence() -> None:
    """The case the interface could not survive.

    When the last AI frame carries only tool calls or reasoning, there is nothing to adopt — and
    that is precisely when the answer card had nothing to render. The narrator is handed the
    question, the statement and the rows, and nothing else.
    """
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

    out = narrate_node(state, _config(model))

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
        out = narrate_node(
            {"path_kind": path_kind, "question": "q", "result_table": RESULT, "messages": []},
            _config(model),
        )
        assert out == {}, f"{path_kind} must not be narrated"
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
    assert narrate_node(state, _config(_Dead())) == {"answer_text": None}
    # And with no utility model configured at all.
    assert narrate_node(state, {"configurable": {}}) == {"answer_text": None}


def test_nothing_is_invented_when_there_is_no_prose_and_no_rows() -> None:
    """No text to adopt and no table to read: there is nothing to narrate *from*, and a sentence
    written here would be the interface asserting an answer the turn did not produce."""
    model = _Recorder()
    out = narrate_node(
        {"path_kind": "answered", "question": "q", "messages": [HumanMessage("q")]},
        _config(model),
    )
    assert out == {}
    assert model.calls == []
