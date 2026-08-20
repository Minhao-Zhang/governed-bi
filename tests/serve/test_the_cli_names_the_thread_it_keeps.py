"""The one-turn CLI's thread: nameable, and otherwise not left behind.

``serve/__main__`` compiles a **durable** graph so a turn paused on ``ask_user`` outlives the
process. That was true and unreachable: the thread id was ``session.run_id``, a fresh
``uuid4().hex[:16]`` per process, and there was no flag with which a later invocation could ask
for it. Measured 2026-08-20: ``runs/harness-checkpoints.sqlite`` held two such orphan threads in
4.6 MB — about 1.8 MB stranded per question ever asked.

Two halves, tested separately because they are separable: ``--thread-id`` makes the durability
reachable, and its absence now means the checkpoints are deleted rather than kept for nobody.

The graph body is stubbed and the **saver is real**. A real turn would need a corpus, a Postgres
connection and a model, none of which is what could be broken here; the saver is exactly what
could, and it is the async-only ``AsyncSqliteSaver`` whose sync ``delete_thread`` hangs rather
than raising, so a stubbed saver would prove nothing about the eviction path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from governed_bi.serve import __main__ as cli
from governed_bi.serve.graph import compile_durable


class _Creds:
    """``_credentials()``'s surface, with nothing read from the environment or ``.env``."""

    PG_DSN_NAMES = ("GOVERNED_BI_PG_DSN",)

    def secret(self, *names: str) -> str | None:
        return "postgresql://stub/stub" if set(names) & set(self.PG_DSN_NAMES) else None


class _Session:
    """The session surface ``main`` touches, recording the thread it is handed."""

    def __init__(self) -> None:
        self.run_id = "0123456789abcdef"
        self.assets_by_id: dict[str, Any] = {}
        self.fatal_problems: tuple[Any, ...] = ()
        self.degradations: tuple[Any, ...] = ()
        #: What `main` passed to `turn(thread_id=...)`, which must be the id it also put in the
        #: config: `Session.turn` folds it into `turn_id` and records it as the turn's identity.
        self.turn_threads: list[str | None] = []

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        return {"configurable": {"question": question}}

    def turn(self, question: str, *, thread_id: str | None = None, **_: Any) -> dict[str, Any]:
        self.turn_threads.append(thread_id)
        return {"question": question, "thread_id": thread_id or self.run_id}


class _Graph:
    """A real durable app with the topology replaced by one checkpoint write.

    Records what was already on the thread when the turn started, which is how "a second
    invocation reached the same thread" is observable without a model.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.found: list[str | None] = []

    def __getattr__(self, name: str) -> Any:
        # `checkpointer`, `run_coro` and `close` all go through here, so `_forget_thread` and the
        # `finally` block in `main` exercise the real ones.
        return getattr(self._inner, name)

    def invoke(self, turn: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        cfg = {"configurable": {"thread_id": config["configurable"]["thread_id"], "checkpoint_ns": ""}}
        before = self._inner.run_coro(self._inner.checkpointer.aget_tuple(cfg))
        self.found.append(None if before is None else before.checkpoint["channel_values"]["question"])
        self._inner.run_coro(self._inner.checkpointer.aput(
            cfg,
            {
                "v": 4,
                "id": f"cp-{turn['question']}",
                "ts": "2026-08-20T00:00:00+00:00",
                "channel_values": {"question": turn["question"]},
                "channel_versions": {"question": "1"},
                "versions_seen": {},
            },
            {"source": "update", "step": 1, "parents": {}},
            {},
        ))
        # An empty record, so `main` returns 1 on `missing_required`. These tests are about the
        # checkpoint, not the record; a fabricated complete record would be the more brittle lie.
        return {"answer": {"record": {}}}


class _Harness:
    """The patches, plus the sessions and graphs each ``main`` call built."""

    def __init__(self, db: Path, monkeypatch: Any) -> None:
        self.db = db
        self.sessions: list[_Session] = []
        self.graphs: list[_Graph] = []

        monkeypatch.setattr(cli, "_credentials", lambda: _Creds())
        monkeypatch.setattr("governed_bi.datasource.postgres.PostgresConnector", lambda dsn: object())
        monkeypatch.setattr("governed_bi.serve.session.from_corpus_dir", self._session)
        monkeypatch.setattr("governed_bi.serve.graph.compile_durable", self._graph)

    def _session(self, *_args: Any, **_kwargs: Any) -> _Session:
        self.sessions.append(_Session())
        return self.sessions[-1]

    def _graph(self, **_kwargs: Any) -> _Graph:
        # One file for every invocation in a test: that shared file *is* the durability under
        # test, and it is a `tmp_path` one so no test touches `HARNESS_DB`.
        self.graphs.append(_Graph(compile_durable(path=self.db)))
        return self.graphs[-1]

    def ask(self, question: str, *, thread_id: str | None = None) -> int:
        argv = ["--corpus-dir", "unused", "-q", question, "--no-model", "--json"]
        if thread_id is not None:
            argv += ["--thread-id", thread_id]
        return cli.main(argv)

    def rows(self) -> int:
        """Checkpoints on disk, read through a plain connection so the file must be closed."""
        conn = sqlite3.connect(str(self.db))
        try:
            return int(conn.execute("select count(*) from checkpoints").fetchone()[0])
        finally:
            conn.close()


def test_two_invocations_naming_one_thread_reach_the_same_thread(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """What ``--thread-id`` is for. Without it the second call could not name the first's thread."""
    harness = _Harness(tmp_path / "named.sqlite", monkeypatch)

    assert harness.ask("first?", thread_id="t-cli") == 1
    assert harness.ask("second?", thread_id="t-cli") == 1

    assert harness.graphs[0].found == [None], "nothing should have been on a fresh thread"
    assert harness.graphs[1].found == ["first?"], (
        "the second invocation did not see the first one's checkpoint, so the durable saver is "
        "not reachable by name and `--thread-id` buys nothing"
    )
    # Both runs must have told the *turn* the same thing they told the config; otherwise the
    # record names a thread the checkpoint is not under.
    assert [s.turn_threads for s in harness.sessions] == [["t-cli"], ["t-cli"]]
    # Kept, because the caller named it. This is the leak's counterpart: eviction must be
    # conditional, not unconditional.
    assert harness.rows() >= 1


def test_a_run_that_names_no_thread_leaves_nothing_behind(tmp_path: Path, monkeypatch: Any) -> None:
    """The leak. An unnamed thread is unreachable by construction, so it is deleted."""
    harness = _Harness(tmp_path / "unnamed.sqlite", monkeypatch)

    assert harness.ask("how many customers?") == 1

    # The default is still the run id — the id nobody can ask for, which is *why* it goes.
    assert harness.sessions[0].turn_threads == ["0123456789abcdef"]
    assert harness.rows() == 0, (
        "the CLI left checkpoints on a thread no later invocation can name; measured at ~1.8 MB "
        "per question in runs/harness-checkpoints.sqlite"
    )

    # The control, in the same file: a named run writes, so the assertion above is about
    # eviction and not about a stub that quietly writes nothing.
    assert harness.ask("how many customers?", thread_id="t-kept") == 1
    assert harness.rows() == 1
