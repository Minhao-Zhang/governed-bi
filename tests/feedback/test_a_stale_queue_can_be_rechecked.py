"""A queue whose rows were measured against another corpus, and the two ways out of it.

**Measured against the live store, 2026-08-24.** 71 of its 73 observations are `open` and **52 of
them no longer reproduce**. 71 carry ``corpus_content_hash = 86ed1dbfef8b325e...`` and
``../BIRD-corpus`` hashes to ``6e5c7b4be83d5682...``. So 73% of a steward's queue was noise and
nothing in the tree said so: the importer had both hashes and printed neither, and the re-check
could only be handed one observation or one patch -- 72 seconds for all 71 in one process with
``--embed`` against 32 minutes one at a time.

**What is driven here and what is not.** ``routing_recall`` needs a live catalog and an embedder, so
the retrieval half is stubbed and stays covered in ``tests/eval/``. Everything this module adds is
around it and is driven for real: which observations get selected, what the flags do, which rows
move, which rows could not, and what the importer says about the hash. The one thing no test here
can claim is that a `gone` verdict is *true* -- that needs the corpus and the embedder, and a test
that pretended otherwise would be the "false finding that reads like a real one" this tool's own
docstring is about.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from governed_bi.corpus.hash import corpus_content_hash
from governed_bi.eval.feedback_import import import_failures
from governed_bi.feedback.events import (
    DeclineReason,
    Kind,
    Observation,
    ObservationState,
    Source,
)
from governed_bi.feedback.lifecycle import TransitionRefused
from governed_bi.feedback.store import FeedbackStore, mint_observation_id, utc_now

GOLD = "SELECT district FROM address.zip_congress JOIN address.congress USING (district)"
WANTED = ("address.zip_congress", "address.congress")


def _observation(question_id: str, **over: object) -> Observation:
    base: dict[str, object] = dict(
        observation_id=mint_observation_id(),
        filed_at=utc_now(),
        source=Source.eval,
        kind=Kind.wrong_answer,
        state=ObservationState.open,
        question=f"question {question_id}",
        external_key=question_id,
        arm="v4",
        question_id=question_id,
        db_id="address",
        gold_sql=GOLD,
        missing_tables=("address.zip_congress",),
    )
    base.update(over)
    return Observation(**base)  # type: ignore[arg-type]


def _store_with(tmp_path: Path, *observations: Observation) -> FeedbackStore:
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    for observation in observations:
        store.file(observation)
    return store


def _stub_retrieval(monkeypatch: pytest.MonkeyPatch, *, licensed_for: dict[str, list[str]]) -> None:
    """Replace the routing half. ``licensed_for`` maps ``question_id`` to what came back licensed.

    Stubbed at ``reproduce_observation.routing_recall`` rather than deeper, because that is the
    seam the tool owns: everything below it is one compiled graph against a live catalog and is
    measured in ``tests/eval/``.
    """
    import reproduce_observation as ro

    monkeypatch.setattr(ro, "_session", lambda *a, **k: object())
    monkeypatch.setattr(
        ro,
        "routing_recall",
        lambda questions, session: [
            {
                "question_id": q["question_id"],
                "licensed": licensed_for.get(str(q["question_id"]), []),
            }
            for q in questions
        ],
    )


def _run(tmp_path: Path, store: FeedbackStore, *argv: str) -> int:
    import reproduce_observation as ro

    return ro.main(["--db", str(store.path), "--corpus-dir", str(tmp_path), *argv])


# ── (a) the way in ────────────────────────────────────────────────────────────


def test_a_steward_with_no_patch_can_recheck_the_whole_open_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect, stated as a command. ``_recheck`` has always taken a *list* and argued for it in
    its own docstring -- one ``routing_recall`` call compiles the graph once -- and the CLI could
    only be handed one observation or the ones one patch answers. A steward holding 71 untriaged
    rows and no patch had no way in, and the per-row loop that was the only alternative was measured
    at 32 minutes where the batch takes 72 seconds."""
    store = _store_with(tmp_path, _observation("q1"), _observation("q2"))
    _stub_retrieval(monkeypatch, licensed_for={"q1": list(WANTED), "q2": []})

    assert _run(tmp_path, store, "--state", "open") == 1, "q2 still reproduces, so 1"
    out = capsys.readouterr().out
    assert "1 still reproduce, 1 do not" in out, out


def test_selecting_by_state_reads_past_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``store.queue`` defaults to 50 rows and reports ``truncated``. The live queue is 71 open, so a
    selector that took the first page would have silently re-checked 50 of them and reported a total
    as if it were the total -- the same defect as the one it was written to fix."""
    store = _store_with(tmp_path, *[_observation(f"q{i}") for i in range(60)])
    _stub_retrieval(monkeypatch, licensed_for={f"q{i}": list(WANTED) for i in range(60)})

    assert _run(tmp_path, store, "--state", "open") == 0
    assert "0 still reproduce, 60 do not" in capsys.readouterr().out


def test_a_state_that_is_not_a_state_is_an_argument_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """2 and not 1. A typo in a flag is the caller's mistake, and 1 in this tool means the failure
    still reproduces."""
    store = _store_with(tmp_path, _observation("q1"))
    assert _run(tmp_path, store, "--state", "opne") == 2
    assert "opne" in capsys.readouterr().err


# ── (a) declining, which is a write ───────────────────────────────────────────


def test_rechecking_writes_nothing_unless_the_steward_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving a row is a write. The read-only run is the default because a batch that silently
    closed 52 rows is a batch nobody can undo -- ``declined`` is terminal."""
    store = _store_with(tmp_path, _observation("q1"))
    _stub_retrieval(monkeypatch, licensed_for={"q1": list(WANTED)})

    assert _run(tmp_path, store, "--state", "open") == 0
    assert store.queue().rows[0].state is ObservationState.open


def test_declining_closes_a_stale_row_with_cannot_reproduce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``DeclineReason.cannot_reproduce`` has been declared since the vocabulary was written and had
    **no producer anywhere** -- one copy string on the client and nothing that could set it. This is
    its first one.

    Two moves and not one: ``TRANSITIONS`` declares no ``open -> declined`` edge, so the row goes
    through ``triaged`` first. That is not a workaround -- the re-check *is* the look that
    ``triaged`` records.
    """
    store = _store_with(tmp_path, _observation("q1"))
    _stub_retrieval(monkeypatch, licensed_for={"q1": list(WANTED)})

    assert _run(tmp_path, store, "--state", "open", "--decline") == 0
    row = store.queue(states=[ObservationState.declined]).rows[0]
    assert row.state is ObservationState.declined
    assert row.decline_reason is DeclineReason.cannot_reproduce
    states = [h["to_state"] for h in store.history(row.observation_id)]
    assert states == ["open", "triaged", "declined"], states


def test_a_row_that_still_reproduces_is_left_where_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that would hurt. ``--decline`` acts on the `gone` verdict only; a row that still
    misses a gold table is the queue's actual work."""
    store = _store_with(tmp_path, _observation("still"), _observation("gone"))
    _stub_retrieval(monkeypatch, licensed_for={"still": [], "gone": list(WANTED)})

    assert _run(tmp_path, store, "--state", "open", "--decline") == 1
    by_state = {o.question_id: o.state for o in store.queue().rows}
    assert by_state == {
        "still": ObservationState.open,
        "gone": ObservationState.declined,
    }, by_state


def test_a_not_applicable_row_is_never_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`n/a` is "coverage cannot answer this", not "this is fixed". Declining it as
    ``cannot_reproduce`` would close a row on the strength of a check that never ran."""
    store = _store_with(tmp_path, _observation("nogold", gold_sql=None, missing_tables=()))
    _stub_retrieval(monkeypatch, licensed_for={})

    assert _run(tmp_path, store, "--state", "open", "--decline") == 0
    assert store.queue().rows[0].state is ObservationState.open


def test_a_row_it_could_not_move_is_reported_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A row already ``declined`` has no outgoing edge, so re-checking it and asking to decline it
    is a move ``TRANSITIONS`` refuses. One refusal must not lose the other 51, and a batch that
    reported 52 closed while closing 51 is worse than one that reported both numbers."""
    store = _store_with(tmp_path, _observation("gone"), _observation("closed"))
    closed = next(o for o in store.queue().rows if o.question_id == "closed")
    store.move(closed.observation_id, to=ObservationState.triaged)
    store.move(
        closed.observation_id,
        to=ObservationState.declined,
        decline_reason=DeclineReason.out_of_scope,
    )
    _stub_retrieval(monkeypatch, licensed_for={"gone": list(WANTED), "closed": list(WANTED)})

    assert _run(tmp_path, store, "--state", "open,declined", "--decline") == 0
    out = capsys.readouterr().out
    assert "declined 1" in out, out
    assert "could not move 1" in out, out
    reasons = {o.question_id: o.decline_reason for o in store.queue().rows}
    assert reasons["closed"] is DeclineReason.out_of_scope, "the first reason is not overwritten"
    assert reasons["gone"] is DeclineReason.cannot_reproduce


def test_a_move_the_store_refuses_mid_route_is_named_and_does_not_stop_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other refusal path, and the one that matters at 52 rows.

    An ``open`` row takes two moves, so the decline can fail *between* them -- ``store.move`` guards
    the write on the state the check was made against and raises ``TransitionRefused`` when another
    steward got there first. Without the catch, row 3 raising loses rows 4 through 52, and the
    operator is left with a batch that reported nothing and half-moved an unknown number of rows.

    Driven by making the second hop raise, because the declared table has no edge that fails on its
    own -- the case is real but only reachable under a concurrent writer.
    """
    store = _store_with(tmp_path, _observation("boom"), _observation("fine"))
    real_move = store.move
    calls: list[str] = []

    def flaky(observation_id: str, **kw: object):  # type: ignore[no-untyped-def]
        calls.append(str(kw.get("to")))
        if kw.get("to") is ObservationState.declined and len(calls) <= 2:
            raise TransitionRefused("somebody else moved it")
        return real_move(observation_id, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "move", flaky)
    _stub_retrieval(monkeypatch, licensed_for={"boom": list(WANTED), "fine": list(WANTED)})
    import reproduce_observation as ro

    monkeypatch.setattr(ro, "FeedbackStore", lambda _: store)

    assert _run(tmp_path, store, "--state", "open", "--decline") == 0
    out = capsys.readouterr().out
    assert "declined 1 as cannot_reproduce; could not move 1" in out, out
    assert "somebody else moved it" in out, "the reason, not just the count"
    by_state = {o.question_id: o.state for o in store.queue().rows}
    assert by_state["fine"] is ObservationState.declined, "the refusal did not stop the batch"
    assert by_state["boom"] is ObservationState.triaged, (
        "the first hop landed and the second did not, which is what `could not move` reports"
    )


# ── (b) the importer says which corpus the rows are about ─────────────────────


def _artifact(tmp_path: Path, corpus_hash: str) -> Path:
    path = tmp_path / "arm.jsonl"
    path.write_text(
        json.dumps(
            {
                "arm": "v_test",
                "question_id": "q1",
                "db_id": "beer_factory",
                "outcome": "answered",
                "correct": False,
                "crashed": False,
                "generated_sql": "SELECT count(*) FROM beer_factory.brauerei",
                "gold_sql": "SELECT count(*) FROM beer_factory.wurzelbier",
                "licensed": ["beer_factory.brauerei"],
                "schemas": ["beer_factory"],
                "quality_flags": [],
                "corpus_content_hash": corpus_hash,
                "prompt_set_hash": "prompt-a",
                "knobs_resolved": {"git_sha": "abc123"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _dataset(tmp_path: Path) -> Path:
    path = tmp_path / "test_final.jsonl"
    path.write_text(
        json.dumps(
            {
                "question_id": "q1",
                "db_id": "beer_factory",
                "question": "how many brauerei?",
                "sql_rename": "SELECT 1",
                "difficulty": "simple",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus" / "beer_factory"
    root.mkdir(parents=True)
    (root / "table.yaml").write_text("name: brauerei\n", encoding="utf-8")
    return tmp_path / "corpus"


def test_the_importer_names_the_corpus_the_rows_were_not_measured_on(tmp_path: Path) -> None:
    """The mechanism, and it is not subtle: 71 of 73 rows carry one hash and the loaded corpus has
    another. The importer held both and printed neither, so a queue measured against a corpus the
    engine no longer loads read exactly like a queue measured against the one it does."""
    corpus = _corpus(tmp_path)
    report = import_failures(
        _artifact(tmp_path, "86ed1dbf" + "0" * 56),
        dataset=_dataset(tmp_path),
        store=FeedbackStore(tmp_path / "feedback.sqlite"),
        corpus_dir=corpus,
    )
    rendered = report.render()
    assert report.rows_on_another_corpus == 1, rendered
    assert "86ed1dbf" in rendered, rendered
    assert corpus_content_hash(corpus)[:8] in rendered, rendered
    assert "reproduce_observation" in rendered, "and it points at the check that can settle it"


def test_a_mismatch_does_not_drop_the_row(tmp_path: Path) -> None:
    """An observation is a record of something that **did happen**. Refusing to file it would erase
    a true fact about a real run, and it would keep the rows away from the only tool that can tell
    which of them are still true."""
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    report = import_failures(
        _artifact(tmp_path, "86ed1dbf" + "0" * 56),
        dataset=_dataset(tmp_path),
        store=store,
        dry_run=False,
        corpus_dir=_corpus(tmp_path),
    )
    assert report.inserted == 1
    assert report.rows_on_another_corpus == 1
    assert store.queue().total == 1


def test_a_matching_corpus_reports_zero_rather_than_staying_silent(tmp_path: Path) -> None:
    """The other half of the same claim. A report that only spoke up on a mismatch would leave the
    reader unable to tell "checked and agreed" from "never checked"."""
    corpus = _corpus(tmp_path)
    report = import_failures(
        _artifact(tmp_path, corpus_content_hash(corpus)),
        dataset=_dataset(tmp_path),
        store=FeedbackStore(tmp_path / "feedback.sqlite"),
        corpus_dir=corpus,
    )
    assert report.rows_on_another_corpus == 0
    assert report.loaded_corpus_hash == corpus_content_hash(corpus)


def test_a_row_that_records_no_corpus_is_counted_apart_from_a_mismatch(tmp_path: Path) -> None:
    """Found by running the real thing: 71 of the 73 rows in the live store carry
    ``86ed1dbfef8b325e...`` and **2 carry nothing at all**. Counting a hash-less row as "measured
    elsewhere" reports 73 mismatches where there are 71, which is the sentinel defect
    ``corpus/hash.py`` refuses to have -- a missing value that compares unequal and reads as a
    finding."""
    corpus = _corpus(tmp_path)
    artifact = tmp_path / "mixed.jsonl"
    lines = [
        _artifact(tmp_path, "86ed1dbf" + "0" * 56).read_text(encoding="utf-8").strip(),
        _artifact(tmp_path, "").read_text(encoding="utf-8").strip().replace('"q1"', '"q2"'),
    ]
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dataset = tmp_path / "both.jsonl"
    dataset.write_text(
        _dataset(tmp_path).read_text(encoding="utf-8").strip()
        + "\n"
        + json.dumps(
            {
                "question_id": "q2",
                "db_id": "beer_factory",
                "question": "and again?",
                "sql_rename": "SELECT 1",
                "difficulty": "simple",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = import_failures(
        artifact,
        dataset=dataset,
        store=FeedbackStore(tmp_path / "feedback.sqlite"),
        corpus_dir=corpus,
    )
    assert report.inserted == 2
    assert report.rows_on_another_corpus == 1, "the hash-less row is not a mismatch"
    assert report.rows_with_no_corpus_hash == 1
    assert "unknown is not elsewhere" in report.render()


def test_no_corpus_to_compare_against_is_said_and_not_guessed(tmp_path: Path) -> None:
    """``corpus_dir=None`` is "nobody told me", which is not the same as "they agree". A report that
    printed 0 for it would answer the comparability question with a number it did not measure."""
    report = import_failures(
        _artifact(tmp_path, "86ed1dbf" + "0" * 56),
        dataset=_dataset(tmp_path),
        store=FeedbackStore(tmp_path / "feedback.sqlite"),
        corpus_dir=None,
    )
    assert report.loaded_corpus_hash is None
    assert report.rows_on_another_corpus is None
    assert "not compared" in report.render()
