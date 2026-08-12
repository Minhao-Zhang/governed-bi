"""``--resume`` refuses an artifact that was measured under a different treatment.

The artifact filename carries ``--model``, ``--effort``, ``--top-n``, ``--embed``, the provider,
``--prompt-variant`` and (since 2026-08-11) ``--replay-routing``, so a renamed tag already
aborts. It carries **no** corpus and no dataset, and an explicit ``--out`` bypasses the tag
entirely. Pull ``../BIRD-corpus``, resume, and one artifact holds two corpora: every gate passes
and the driver prints ``ALL GATES PASS -- these numbers are quotable as a single arm``.

The guard reads three things back off the rows the resume intends to keep — the two treatment
hashes and every comparability knob — because the hashes alone do not move with ``--top-n``,
``--embed``, ``--reflect`` or the model id, and those are the knobs an ``--out`` run can change
with no tag to stop it.

These tests drive :func:`resume_identity_problem` rather than ``main``: it is the only part of
the resume decision that needs no corpus, no dataset, no database and no model.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.eval.provenance import (
    append_refusal,
    flag_conflict,
    resume_identity_problem,
    truncation_notice,
)

#: The two knobs the fixtures move. Real comparability knob names, so a rename in the register
#: breaks these tests rather than leaving them asserting against a string nothing declares.
KNOBS = ("route_top_n", "reflect_enabled")


@pytest.fixture(autouse=True)
def _knob_names_are_real() -> None:
    from governed_bi.register.knobs import comparability_keys

    missing = sorted(set(KNOBS) - comparability_keys())
    assert not missing, f"{missing} are no longer comparability knobs; fix the fixtures"


def _rows(
    n: int = 3,
    *,
    corpus: Any = "corpus-a",
    prompt: Any = "prompt-p",
    knobs: dict | None = None,
    outcome: str = "answered",
    licensed: tuple[str, ...] = ("s.t",),
    routing_pinned: bool = False,
    first_qid: int = 1,
) -> list[dict]:
    return [
        {
            "question_id": f"q{i}",
            "corpus_content_hash": corpus,
            "prompt_set_hash": prompt,
            "knobs_resolved": dict(knobs if knobs is not None else {"route_top_n": 3}),
            "outcome": outcome,
            "licensed": list(licensed),
            "routing_pinned": routing_pinned,
        }
        for i in range(first_qid, first_qid + n)
    ]


def check(rows, **over):
    kwargs: dict[str, Any] = {
        "corpus_content_hash": "corpus-a",
        "prompt_set_hash": "prompt-p",
        "knobs_resolved": {"route_top_n": 3},
        "comparability": frozenset({"route_top_n"}),
        "question_ids": {f"q{i}" for i in range(1, 9)},
        "replay_routing": False,
    }
    kwargs.update(over)
    return resume_identity_problem(rows, **kwargs)


def test_an_artifact_from_the_same_treatment_resumes() -> None:
    refusal, warnings = check(_rows(5))
    assert refusal == ""
    assert warnings == []


def test_a_second_corpus_in_the_artifact_refuses_the_resume() -> None:
    """The failure this guard exists for: ``git pull`` in the corpus, then resume."""
    rows = _rows(9) + _rows(4, corpus="corpus-b", first_qid=10)
    refusal, _ = check(rows, question_ids={f"q{i}" for i in range(1, 14)})
    assert refusal != ""
    assert "corpus_content_hash" in refusal
    # The row count matters: it tells the reader how much of the artifact is the other corpus.
    assert "corpus-b" in refusal and "(4 rows)" in refusal


def test_a_second_prompt_set_in_the_artifact_refuses_the_resume() -> None:
    rows = _rows(6) + _rows(4, prompt="prompt-q", first_qid=7)
    refusal, _ = check(rows, question_ids={f"q{i}" for i in range(1, 11)})
    assert "prompt_set_hash" in refusal
    assert "prompt-q" in refusal


def test_a_row_predating_the_field_warns_but_does_not_refuse() -> None:
    """``None`` on an *answered* row means "written before this field existed".

    Refusing on it would strand every artifact produced before the treatment hashes landed,
    which is a different and much less likely failure than the one being guarded.
    """
    refusal, warnings = check(_rows(4, corpus=None, prompt=None))
    assert refusal == ""
    assert len(warnings) == 2
    assert all("cannot prove" in w for w in warnings)


def test_a_clarification_that_never_routed_is_counted_and_not_warned_about() -> None:
    """open-work 3.6a: a turn that abstains before routing carries no treatment identity.

    Every ``corpus_content_hash: None`` row in the 2026-08-09 artifacts is one of these — 6 of
    6 in v3-fold, 4 of 4 in v4, 5 of 5 in v5 — so the old guard's "cannot prove they are the
    same treatment" fired on **every legitimate resume**, which is the shape that teaches a
    reader to ignore a warning. Reported as a count instead, in words that are not a caution.
    """
    rows = _rows(5) + _rows(
        2, corpus=None, prompt=None, outcome="clarification", licensed=(), first_qid=6
    )
    refusal, warnings = check(rows)
    assert refusal == ""
    assert warnings and all("abstained before routing" in w for w in warnings)
    assert not any("cannot prove" in w for w in warnings)
    assert any("2 resumed row(s)" in w for w in warnings)


def test_a_clarification_that_did_license_tables_still_warns() -> None:
    """The narrow exemption stays narrow: only a turn that licensed *nothing* is the declared
    path. A clarification with tables in hand reached routing, so a missing hash there is the
    unexplained case the warning is for."""
    rows = _rows(3, corpus=None, outcome="clarification", licensed=("s.t", "s.u"))
    _, warnings = check(rows)
    assert any("cannot prove" in w for w in warnings)


def test_a_comparability_knob_that_moved_refuses_even_though_both_hashes_agree() -> None:
    """The ``--out`` hole. ``--top-n`` moves no hash and is only in the filename tag, which an
    explicit ``--out`` bypasses — so two shortlist budgets could land in one artifact with the
    corpus and prompt guards both satisfied."""
    refusal, _ = check(
        _rows(4, knobs={"route_top_n": 8}), knobs_resolved={"route_top_n": 3}
    )
    assert "route_top_n" in refusal
    assert "8" in refusal and "3" in refusal


def test_a_knob_recorded_on_one_side_only_warns_rather_than_refusing() -> None:
    """All seven ``proxy_*`` artifacts in ``runs/eval/`` are missing six comparability knobs.
    Refusing on that would make every artifact on disk unresumable, which is not what a
    treatment guard is for."""
    refusal, warnings = check(
        _rows(3, knobs={}),
        knobs_resolved={"route_top_n": 3},
        comparability=frozenset({"route_top_n"}),
    )
    assert refusal == ""
    assert any("route_top_n" in w and "cannot be shown to share it" in w for w in warnings)


def test_a_knob_absent_from_both_sides_is_silent() -> None:
    """Both declined to say. A warning here would fire four times on every real resume."""
    refusal, warnings = check(
        _rows(3, knobs={"route_top_n": 3}),
        comparability=frozenset({"route_top_n", "sqlglot_version"}),
    )
    assert refusal == ""
    assert warnings == []


def test_an_artifact_naming_questions_this_run_does_not_cover_refuses() -> None:
    """A changed ``--dataset`` or a narrowed scope; the driver names both rather than guessing."""
    rows = _rows(2) + [{**_rows(1)[0], "question_id": "gone"}]
    refusal, _ = check(rows)
    assert "gone" in refusal
    assert "--dataset" in refusal and "--limit" in refusal


def test_a_run_covering_more_questions_than_the_artifact_still_resumes() -> None:
    """The ordinary case: the artifact is a prefix of this run, which is what resume is for."""
    refusal, warnings = check(_rows(2))
    assert refusal == ""
    assert warnings == []


def test_a_second_run_without_resume_refuses_rather_than_appending(tmp_path) -> None:
    """The other half of the artifact hole, and the one that produced a *printed number*.

    The driver opened its output in ``"a"`` mode unconditionally, so re-running without
    ``--resume`` duplicated every question and ``_report`` printed EX over the doubled
    population. ``Population.of`` does raise on the repeated ids — afterwards, which is too
    late for a number already on the screen.
    """
    path = tmp_path / "arm.jsonl"
    path.write_text('{"question_id": "q1"}\n{"question_id": "q2"}\n', encoding="utf-8")

    refusal = append_refusal(path, resume=False, truncate=False)
    assert refusal and "2 row(s)" in refusal and "--resume" in refusal

    assert append_refusal(path, resume=True, truncate=False) is None, "a resume is the point"
    assert append_refusal(path, resume=False, truncate=True) is None, "explicitly asked for"
    assert append_refusal(tmp_path / "new.jsonl", resume=False, truncate=False) is None

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert append_refusal(empty, resume=False, truncate=False) is None, (
        "a zero-byte file is a run that wrote nothing, not a population"
    )


# ── the destructive branch, which used to be unreachable from a test ──────────


def test_only_truncate_destroys_an_artifact_and_it_says_how_much(tmp_path) -> None:
    """``--force-fresh`` must not delete a paid run, and ``--truncate`` must say what it takes.

    ``--force-fresh`` was documented as relaxing the *sibling-artifact* abort -- a path where
    the output file does not exist -- and then quietly acquired ``out_path.write_text("")`` in
    ``main``, where no test could reach it. An arm on this dataset is hours of paid model calls.

    The regression this pins: a driver in which ``--force-fresh`` truncates makes the first
    assertion fail, because the notice would be non-empty for it.
    """
    path = tmp_path / "arm.jsonl"
    path.write_text('{"question_id": "q1"}\n{"question_id": "q2"}\n', encoding="utf-8")

    assert truncation_notice(path, resume=False, truncate=False) is None, (
        "nothing is destroyed unless the caller asked for it in its own flag"
    )
    assert truncation_notice(path, resume=True, truncate=True) is None, (
        "a resume keeps what was measured; the driver refuses the flag pair anyway"
    )

    notice = truncation_notice(path, resume=False, truncate=True)
    assert notice and "2 measured row(s)" in notice and str(path) in notice

    assert truncation_notice(tmp_path / "absent.jsonl", resume=False, truncate=True) is None
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert truncation_notice(empty, resume=False, truncate=True) is None, (
        "a zero-byte file is nothing to discard, so there is nothing to warn about"
    )


def test_keeping_the_artifact_and_discarding_it_are_refused_together() -> None:
    """Opposite instructions about one file, refused rather than resolved either way.

    Driven as the pure decision rather than through ``main``. Driving ``main`` was tried and
    is actively unsafe: with the guard mutated away the driver runs on into credential
    resolution, model construction and a database connection, so the mutation that proves the
    guard works instead hangs the test -- and on a machine that *has* credentials it would get
    further than that. The branch has to be reachable without starting the driver, which is the
    same argument ``append_refusal`` is built on.
    """
    conflict = flag_conflict(resume=True, truncate=True)
    assert conflict and "--truncate" in conflict and "--resume" in conflict

    assert flag_conflict(resume=True, truncate=False) is None
    assert flag_conflict(resume=False, truncate=True) is None
    assert flag_conflict(resume=False, truncate=False) is None


def test_an_unpinned_run_refuses_an_artifact_whose_routing_was_replayed() -> None:
    """``--replay-routing`` is a treatment and had neither a tag segment nor a readable row.

    Only the reliable direction is asserted, and the docstring on the guard says why the other
    one is not: a pinned run whose kept rows all abstained before routing carries no ``True``
    either, so the absence of one proves nothing.
    """
    refusal, _ = check(_rows(4, routing_pinned=True), replay_routing=False)
    assert "--replay-routing" in refusal
    assert "4 row(s)" in refusal

    ok, _ = check(_rows(4, routing_pinned=True), replay_routing=True)
    assert ok == ""
