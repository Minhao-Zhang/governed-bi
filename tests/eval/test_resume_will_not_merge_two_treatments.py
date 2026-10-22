"""Resume reads identity off the rows, not the filename. The filename has no corpus, and ``--out`` bypasses the tag."""

from __future__ import annotations

import json
from typing import Any

import pytest

from governed_bi.eval.provenance import (
    append_refusal,
    flag_conflict,
    resume_identity_problem,
    truncation_notice,
)

#: The two settings the fixtures move. Real setting that affects results names, so a rename in the register
#: breaks these tests rather than leaving them asserting against a string nothing declares.
SETTINGS = ("route_top_n", "abstention_policy_enabled")


@pytest.fixture(autouse=True)
def _setting_names_are_real() -> None:
    from governed_bi.registry.settings import setting_names

    missing = sorted(set(SETTINGS) - setting_names())
    assert not missing, f"{missing} are no longer comparability settings; fix the fixtures"


def _rows(
    n: int = 3,
    *,
    corpus: Any = "corpus-a",
    prompt: Any = "prompt-p",
    settings: dict | None = None,
    outcome: str = "answered",
    retrieved_tables: tuple[str, ...] = ("s.t",),
    routing_pinned: bool = False,
    first_qid: int = 1,
) -> list[dict]:
    return [
        {
            "question_id": f"q{i}",
            "corpus_content_hash": corpus,
            "prompt_set_hash": prompt,
            "settings_resolved": dict(settings if settings is not None else {"route_top_n": 3}),
            "outcome": outcome,
            "retrieved_tables": list(retrieved_tables),
            "routing_pinned": routing_pinned,
        }
        for i in range(first_qid, first_qid + n)
    ]


def check(rows, **over):
    kwargs: dict[str, Any] = {
        "corpus_content_hash": "corpus-a",
        "prompt_set_hash": "prompt-p",
        "settings_resolved": {"route_top_n": 3},
            "affects_results": frozenset({"route_top_n"}),
        "question_ids": {f"q{i}" for i in range(1, 9)},
        "replay_routing": False,
    }
    kwargs.update(over)
    return resume_identity_problem(rows, **kwargs)


def test_an_artifact_from_the_same_under_test_resumes() -> None:
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

    Refusing on it would strand every artifact produced before the config hash landed,
    which is a different and much less likely failure than the one being guarded.
    """
    refusal, warnings = check(_rows(4, corpus=None, prompt=None))
    assert refusal == ""
    assert len(warnings) == 2
    assert all("cannot prove" in w for w in warnings)


def test_a_clarification_that_never_routed_is_counted_and_not_warned_about() -> None:
    """A turn that abstains before routing has no setting identity. Warning on it fired on every legitimate resume."""
    rows = _rows(5) + _rows(
        2, corpus=None, prompt=None, outcome="clarification", retrieved_tables=(), first_qid=6
    )
    refusal, warnings = check(rows)
    assert refusal == ""
    assert warnings and all("abstained before routing" in w for w in warnings)
    assert not any("cannot prove" in w for w in warnings)
    assert any("2 resumed row(s)" in w for w in warnings)


def test_a_clarification_that_did_license_tables_still_warns() -> None:
    """The narrow exemption stays narrow: only a turn that retrieved_tables *nothing* is the declared
    path. A clarification with tables in hand reached routing, so a missing hash there is the
    unexplained case the warning is for."""
    rows = _rows(3, corpus=None, outcome="clarification", retrieved_tables=("s.t", "s.u"))
    _, warnings = check(rows)
    assert any("cannot prove" in w for w in warnings)


def test_a_affects_results_setting_that_moved_refuses_even_though_both_hashes_agree() -> None:
    """The ``--out`` hole. ``--top-n`` moves no hash and is only in the filename tag, which an
    explicit ``--out`` bypasses — so two shortlist budgets could land in one artifact with the
    corpus and prompt guards both satisfied."""
    refusal, _ = check(
        _rows(4, settings={"route_top_n": 8}), settings_resolved={"route_top_n": 3}
    )
    assert "route_top_n" in refusal
    assert "8" in refusal and "3" in refusal


def test_a_setting_recorded_on_one_side_only_warns_rather_than_refusing() -> None:
    """All seven ``proxy_*`` artifacts in ``runs/eval/`` are missing six settings that affect results.
    Refusing on that would make every artifact on disk unresumable, which is not what a
    setting under test guard is for."""
    refusal, warnings = check(
        _rows(3, settings={}),
        settings_resolved={"route_top_n": 3},
        affects_results=frozenset({"route_top_n"}),
    )
    assert refusal == ""
    assert any("route_top_n" in w and "cannot be shown to share it" in w for w in warnings)


def test_a_setting_absent_from_both_sides_is_silent() -> None:
    """Both declined to say. A warning here would fire four times on every real resume."""
    refusal, warnings = check(
        _rows(3, settings={"route_top_n": 3}),
        affects_results=frozenset({"route_top_n", "sqlglot_version"}),
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
    """Appending without resume doubled the population and printed a rate over it."""
    path = tmp_path / "config.jsonl"
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


# ── the destructive branch ──


def test_only_truncate_destroys_an_artifact_and_it_says_how_much(tmp_path) -> None:
    """Only ``--truncate`` destroys rows, and it says how many. ``--force-fresh`` must not."""
    path = tmp_path / "config.jsonl"
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
    """Resume and truncate are opposite instructions. The decision is reachable without starting the driver."""
    conflict = flag_conflict(resume=True, truncate=True)
    assert conflict and "--truncate" in conflict and "--resume" in conflict

    assert flag_conflict(resume=True, truncate=False) is None
    assert flag_conflict(resume=False, truncate=True) is None
    assert flag_conflict(resume=False, truncate=False) is None


def test_an_unpinned_run_refuses_an_artifact_whose_routing_was_replayed() -> None:
    """Only one direction is asserted: rows that abstained before routing carry no ``True``, so absence proves nothing.
    """
    refusal, _ = check(_rows(4, routing_pinned=True), replay_routing=False)
    assert "--replay-routing" in refusal
    assert "4 row(s)" in refusal

    ok, _ = check(_rows(4, routing_pinned=True), replay_routing=True)
    assert ok == ""


def test_a_setting_the_writer_reshapes_is_not_a_second_under_test() -> None:
    """JSON turns a tuple of tuples into a list of lists. Comparing ``repr`` reported a change that had not happened."""
    from governed_bi.registry.settings import defaults, setting_names

    assert "asset_budgets" in setting_names()
    shipped = defaults()["asset_budgets"]
    assert isinstance(shipped, tuple), "fixture assumes the register ships a tuple"

    as_written = json.loads(json.dumps(shipped))
    assert repr(as_written) != repr(shipped), "the round trip must actually reshape it"

    refusal, warnings = check(
        _rows(3, settings={"asset_budgets": as_written}),
        settings_resolved={"asset_budgets": shipped},
        affects_results=frozenset({"asset_budgets"}),
    )
    assert refusal == "", refusal
    assert warnings == []


def test_a_string_and_the_number_it_spells_are_still_two_under_tests() -> None:
    """``3`` and the string ``3`` still refuse. A looser compare would report that drift as agreement."""
    refusal, _ = check(
        _rows(3, settings={"route_top_n": "3"}),
        settings_resolved={"route_top_n": 3},
        affects_results=frozenset({"route_top_n"}),
    )
    assert "route_top_n" in refusal
