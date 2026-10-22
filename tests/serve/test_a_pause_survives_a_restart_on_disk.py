"""A real ``ask_user`` interrupt on the SQLite saver, answered by a graph that did not write it.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.graph import compile_durable
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel

#: Long enough that a slow Windows file open is not a failure, short enough that a genuine block
#: is reported in the same minute. The failure mode being bounded is a *block*, which no timeout
#: value can make pass, so this only decides how long a broken run takes to say so.
DEADLINE_S = 60.0


def _asking_model() -> ScriptedChatModel:
    """A model that asks once, then answers in prose."""
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": "which year?", "basis": "data_definition"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok: 2020"),
        ]
    )


def _config(thread: str, model: Any) -> dict[str, Any]:
    """The caller's config, rebuilt per graph the way a fresh process would have to."""
    return {
        "configurable": {
            "thread_id": thread,
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }


def _turn(thread: str) -> dict[str, Any]:
    return {
        "question": "revenue?",
        "thread_id": thread,
        "turn_index": 1,
        "turn_id": f"turn-{thread}",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "settings_resolved": {},
        "n_re_served": 0,
        "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
    }


def _bounded(work: Any, what: str) -> dict[str, Any]:
    """Run ``work()`` on a daemon thread and fail rather than block."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = work()
        except BaseException as exc:  # noqa: BLE001 -- the box is the report
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True, name=f"bounded-{what}")
    thread.start()
    thread.join(DEADLINE_S)
    if thread.is_alive():
        pytest.fail(
            f"{what} did not finish in {DEADLINE_S:.0f}s. A blocked resume is the failure this "
            "bound exists for: `aiosqlite` binds its connection to the loop that opened it, and a "
            "cross-loop use blocks instead of raising."
        )
    return box


def _pause_one(db: Path, thread: str) -> None:
    """Write a real, paused ``ask_user`` interrupt to ``db``, then close everything that wrote it."""
    def work() -> Any:
        with compile_durable(path=db) as graph:
            paused = graph.invoke(_turn(thread), _config(thread, _asking_model()))
            assert paused.get("__interrupt__"), (
                "precondition: the scripted model's `ask_user` must pause the turn, or this test "
                "would assert a resume of nothing"
            )
            return True

    box = _bounded(work, "the paused turn")
    if "error" in box:
        raise box["error"]


def test_a_pause_is_resumed_by_a_graph_that_did_not_write_it(tmp_path: Path) -> None:
    """The join: interrupt through one saver, answer through another, over one file."""
    db = tmp_path / "conversations.sqlite"
    thread = "t-restart"
    _pause_one(db, thread)
    assert db.exists() and db.stat().st_size > 0, "the pause has to be on disk to be resumable"

    def work() -> Any:
        # A second `compile_durable` on the same path: new loop, new connection, new saver, new
        # compiled graph. Nothing but the file survives from the call above.
        with compile_durable(path=db) as graph:
            return resume_clarification(
                graph,
                config=_config(thread, _asking_model()),
                answer="2020",
            )

    box = _bounded(work, "the resume on a fresh saver")
    if "error" in box:
        raise box["error"]
    done = box["value"]

    clarifications = done.get("clarifications") or []
    assert any(c.get("answer") == "2020" for c in clarifications), (
        f"the answer a person typed did not reach the resumed turn: {clarifications!r}"
    )
    # `no_sql` is allowed and `crashed` is not: the scripted model answers in prose on its second
    # response, so no governed statement runs. What this test is about is that the resume happened
    # at all.
    outcome = (done.get("answer") or {}).get("outcome")
    assert outcome in {"answered", "clarification", "no_sql"}, (
        f"the resumed turn did not complete: outcome={outcome!r}, "
        f"path_kind={done.get('path_kind')!r}, failure={done.get('failure')!r}"
    )


def test_a_resume_reaches_only_the_thread_it_names(tmp_path: Path) -> None:
    """The thread id is the only handle, so an answer sent to another thread must not land here."""
    db = tmp_path / "conversations.sqlite"
    thread = "t-restart-named"
    _pause_one(db, thread)

    def work() -> Any:
        with compile_durable(path=db) as graph:
            try:
                resume_clarification(
                    graph, config=_config("t-restart-stray", _asking_model()), answer="1999"
                )
            except Exception:  # noqa: BLE001 -- a thread with no pause may refuse; either is fine
                pass
            return resume_clarification(
                graph, config=_config(thread, _asking_model()), answer="2020"
            )

    box = _bounded(work, "the resume of the named thread after a stray one")
    if "error" in box:
        raise box["error"]
    answers = [c.get("answer") for c in (box["value"].get("clarifications") or [])]
    assert answers == ["2020"], f"the paused thread recorded {answers!r}, expected only its own answer"


def test_the_paused_thread_is_the_only_one_on_disk(tmp_path: Path) -> None:
    """The pause is stored under the thread the caller named, and nothing else is stored."""
    db = tmp_path / "conversations.sqlite"
    thread = "t-restart-solo"
    _pause_one(db, thread)

    # `closing`, not a bare `with`: `sqlite3.Connection.__exit__` commits the transaction and
    # leaves the connection open, which is the `ResourceWarning: unclosed database` this suite
    # spent an audit driving from 20 occurrences down to one.
    with closing(sqlite3.connect(db)) as conn:
        ids = sorted({row[0] for row in conn.execute("SELECT thread_id FROM checkpoints")})
    assert ids == [thread], f"expected exactly the named thread on disk, found {ids!r}"
