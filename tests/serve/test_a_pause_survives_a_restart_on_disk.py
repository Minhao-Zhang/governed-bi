"""A real ``ask_user`` pause, resumed by a graph that did not write it.

**The gap this closes, stated as it was found.** Every human-in-the-loop test compiles through
``compile_graph``, whose saver is ``InMemorySaver``, and
``test_the_durable_saver_survives_a_process.py`` reaches the durable saver through
``update_state`` because that file is about persistence rather than the serve topology. So the two
halves had each been tested and their *join* had not: nothing drove a real interrupt onto
``AsyncSqliteSaver`` and then answered it from a graph that had not written it. That join is what
``README.md`` claims on the front page and what ``hitl_survives_process_restart`` reports, and until
2026-08-20 its whole evidence was one hand-run observation (2026-08-19, recorded in
``docs/analysis/adopting-the-downstream-fork-2026-08-19.md``).

**What "restart" means here, and what it does not.** Each test closes the first graph and calls
:func:`compile_durable` again on the same file. That is a new ``asyncio`` loop, a new
``aiosqlite`` connection, a new ``AsyncSqliteSaver`` and a new compiled graph -- every object the
pause could have been hiding in, gone -- so the only thing carrying it across is the file. It is
**not** a process boundary: the interpreter, the imported modules and the module-level state
survive, and a bug that lived in one of those would pass here. Spawning ``langgraph dev``, killing
it and waiting for a fresh one to bind is the test that would cover that, and it needs a model key
and a port, which is why the hand procedure stays written down rather than automated. What is
covered is the seam that had no coverage at all: a checkpoint written by one saver and resumed
through another.

**Every test bounds its own hang.** ``aiosqlite`` binds its connection to the loop that opened it
and a cross-loop reuse does not raise, it blocks (see the sibling file's header). A blocked resume
here would otherwise hang the session rather than fail a test, so each body runs on a daemon thread
with a join deadline and the assertions are made on what that thread recorded. `tmp_path` databases
throughout: nothing touches ``HARNESS_DB`` or the served store.
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
from governed_bi.serve.resume import ResumeRejected, resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel

#: Long enough that a slow Windows file open is not a failure, short enough that a genuine block
#: is reported in the same minute. The failure mode being bounded is a *block*, which no timeout
#: value can make pass, so this only decides how long a broken run takes to say so.
DEADLINE_S = 60.0

TOKEN = "identity-restart"


def _asking_model() -> ScriptedChatModel:
    """A model that asks once, then answers in prose.

    Prose on the second turn is deliberate: the subject is the resume, and calling ``run_query``
    would drag a connector and a corpus into a test about a checkpoint. It costs the resumed turn
    its ``answered`` terminal -- see the outcome assertion, which allows ``no_sql`` for exactly
    this reason and refuses ``crashed``.
    """
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
    """The caller's config, rebuilt per graph the way a fresh process would have to.

    ``agent_model`` and ``policy`` are *not* checkpointed and must not be: a saver that persisted a
    live model object would be persisting a credential. So a resume after a restart always supplies
    them again, and that this test does so twice is the realistic shape rather than a shortcut.
    """
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
        "knobs_resolved": {},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "identity": {"token": TOKEN},
        "clarifications": [],
    }


def _bounded(work: Any, what: str) -> dict[str, Any]:
    """Run ``work()`` on a daemon thread and fail rather than block.

    Daemon so that a thread still blocked at interpreter exit cannot stop the process -- the trap
    the sibling file names, one layer out: a non-daemon thread here would turn a regression into a
    hung CI job with no output.
    """
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
    """The join: interrupt through one saver, answer through another, over one file.

    Both halves are asserted, because either alone is satisfiable by a broken engine. The answer
    has to arrive **and** the clarification has to carry the text a person typed -- a resume that
    completed the turn while dropping the answer would leave the model to guess, which is the
    failure `ask_user` exists to prevent.
    """
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
                identity={"token": TOKEN},
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


def test_the_identity_gate_reads_the_token_off_disk(tmp_path: Path) -> None:
    """ADR 0006 B9 across the restart, which is the half a fresh process cannot fake.

    ``resume_clarification`` loads the checkpointed ``identity`` for the thread and compares it to
    the caller's. In one process that comparison could pass on a value still in memory; here the
    only copy is the one the saver wrote, so this asserts the gate is reading the file. A restart
    that lost ``identity`` would fail *open* -- every caller authorised, because there is nothing
    to disagree with -- which is why the wrong token is tested before the right one.
    """
    db = tmp_path / "conversations.sqlite"
    thread = "t-restart-identity"
    _pause_one(db, thread)

    def work() -> Any:
        with compile_durable(path=db) as graph:
            with pytest.raises(ResumeRejected):
                resume_clarification(
                    graph,
                    config=_config(thread, _asking_model()),
                    identity={"token": "wrong-token"},
                    answer="2020",
                )
            return resume_clarification(
                graph,
                config=_config(thread, _asking_model()),
                identity={"token": TOKEN},
                answer="2020",
            )

    box = _bounded(work, "the identity gate on a fresh saver")
    if "error" in box:
        raise box["error"]
    done = box["value"]
    assert any(
        c.get("answer") == "2020" for c in (done.get("clarifications") or [])
    ), "the authorised resume after a rejected one must still deliver the answer"


def test_the_paused_thread_is_the_only_one_on_disk(tmp_path: Path) -> None:
    """The pause is stored under the thread the caller named, and nothing else is stored.

    Cheap, and it is the assertion that would have caught the defect this suite already carries a
    fix for one layer up: the CLI wrote every turn under a fresh per-process id, so the file filled
    with threads nothing could ask for. Reading the ids back is how a test sees that at all.
    """
    db = tmp_path / "conversations.sqlite"
    thread = "t-restart-solo"
    _pause_one(db, thread)

    # `closing`, not a bare `with`: `sqlite3.Connection.__exit__` commits the transaction and
    # leaves the connection open, which is the `ResourceWarning: unclosed database` this suite
    # spent an audit driving from 20 occurrences down to one.
    with closing(sqlite3.connect(db)) as conn:
        ids = sorted({row[0] for row in conn.execute("SELECT thread_id FROM checkpoints")})
    assert ids == [thread], f"expected exactly the named thread on disk, found {ids!r}"
