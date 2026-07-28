"""Regression coverage for silent-failure audit findings F1–F7.

These pin durable contracts: build completeness markers, relocated SME ledgers,
limit/scope resume gates, rollback-safe promote, atomic JSONL rewrite, fatal
git_sha drift on paid resume, and full twin-stamp coverage before strata.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from governed_bi.corpus.schemas import TableAsset
from governed_bi.curator.clarifications import (
    ClarificationRecord,
    ClarificationRecordStatus,
    StaticResponder,
    resolve_clarifications_path,
    write_clarifications,
)
from governed_bi.eval.run_datalake import (
    _BUILD_COMPLETE_MARKER,
    _check_resume_manifest,
    _compare_arms,
    _corpus_complete,
    _discard_incomplete_corpus,
    _mark_build_complete,
    _summarise_rows,
)
from governed_bi.eval.run_experiment import _write_jsonl


def _table(schema: str = "beer") -> TableAsset:
    return TableAsset(
        id=f"{schema}.t",
        schema=schema,
        physical_name="t",
        columns=[],
    )


# --------------------------------------------------------------------------- #
# F1 — completeness marker
# --------------------------------------------------------------------------- #


def test_yaml_alone_is_not_a_complete_corpus(tmp_path: Path):
    root = tmp_path / "corpus_baseline"
    tables = root / "beer" / "tables"
    tables.mkdir(parents=True)
    (tables / "t.yaml").write_text("physical_name: t\n", encoding="utf-8")
    assert not _corpus_complete(root, "beer")
    assert _discard_incomplete_corpus(root, "beer")
    assert not (root / "beer").exists()


def test_marker_plus_yaml_is_complete(tmp_path: Path):
    root = tmp_path / "corpus_baseline"
    tables = root / "beer" / "tables"
    tables.mkdir(parents=True)
    (tables / "t.yaml").write_text("physical_name: t\n", encoding="utf-8")
    _mark_build_complete(root, "beer")
    assert (root / "beer" / "_build" / _BUILD_COMPLETE_MARKER).is_file()
    assert _corpus_complete(root, "beer")
    assert not _discard_incomplete_corpus(root, "beer")


# --------------------------------------------------------------------------- #
# F2 — relocated clarifications ledger
# --------------------------------------------------------------------------- #


def test_resolve_clarifications_prefers_live_then_relocated(tmp_path: Path):
    curated = tmp_path / "corpus_curated"
    curated.mkdir()
    live = curated / "clarifications.jsonl"
    live.write_text("{}\n", encoding="utf-8")
    assert resolve_clarifications_path(curated, "beer") == live

    live.unlink()
    relocated = curated / "beer" / "_build" / "clarifications.jsonl"
    relocated.parent.mkdir(parents=True)
    relocated.write_text("{}\n", encoding="utf-8")
    assert resolve_clarifications_path(curated, "beer") == relocated


def test_sme_reads_relocated_ledger_on_cross_resume(tmp_path: Path, monkeypatch):
    """Curated finished and relocated; SME resume must fold the relocated ledger."""
    from governed_bi.curator import pipeline as pl

    curated = tmp_path / "corpus_curated"
    schema = "beer"
    relocated = curated / schema / "_build"
    relocated.mkdir(parents=True)
    rec = ClarificationRecord(
        id="c1",
        scope=f"{schema}.t",
        question="what is t?",
        status=ClarificationRecordStatus.open,
        raised_by=["agent"],
    )
    write_clarifications(relocated / "clarifications.jsonl", [rec])
    (relocated / "run_manifest.json").write_text(
        json.dumps(
            {
                "phase": "A",
                "schema": schema,
                "ledger_source": "agent",
                "clarification_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (curated / schema / "tables").mkdir(parents=True)

    class _FakeCorpus:
        assets = [_table(schema)]

    monkeypatch.setattr(
        "governed_bi.corpus.loader.load_corpus", lambda *a, **k: _FakeCorpus()
    )
    monkeypatch.setattr(pl, "_corpora_differ", lambda *a, **k: True)
    monkeypatch.setattr(pl, "_validate_fix_pass", lambda *a, **k: ([], {}, None))
    monkeypatch.setattr(pl, "_run_adversary_signal", lambda *a, **k: None)
    monkeypatch.setattr(
        pl,
        "fill_clarifications_with_responder",
        lambda records, responder: [
            r.model_copy(
                update={
                    "status": ClarificationRecordStatus.answered,
                    "answer": responder.answer(r.question),
                    "answered_by": "static",
                }
            )
            for r in records
        ],
    )

    out = tmp_path / "corpus_curated_sme"
    out.mkdir()
    pl.build_curated_corpus_with_sme(
        SimpleNamespace(),
        SimpleNamespace(),
        schema,
        [],
        out,
        responder=StaticResponder(default="answered"),
        curated_root=curated,
        model=None,
        run_agent_repass=False,
        seed_ledger_if_empty=False,
    )
    sme_ledger = out / "clarifications.jsonl"
    assert sme_ledger.exists()
    lines = [
        json.loads(line)
        for line in sme_ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines[0]["status"] == "answered"
    assert lines[0]["answer"] == "answered"


def test_paid_sme_fails_closed_when_recorded_ledger_is_missing(tmp_path: Path, monkeypatch):
    from governed_bi.curator import pipeline as pl

    curated = tmp_path / "corpus_curated"
    schema = "beer"
    build = curated / schema / "_build"
    build.mkdir(parents=True)
    (build / "run_manifest.json").write_text(
        json.dumps(
            {
                "phase": "A",
                "schema": schema,
                "ledger_source": "agent",
                "clarification_count": 2,
            }
        ),
        encoding="utf-8",
    )
    (curated / schema / "tables").mkdir(parents=True)

    class _FakeCorpus:
        assets = [_table(schema)]

    monkeypatch.setattr(
        "governed_bi.corpus.loader.load_corpus", lambda *a, **k: _FakeCorpus()
    )

    with pytest.raises(RuntimeError, match="missing relocated ledger"):
        pl.build_curated_corpus_with_sme(
            SimpleNamespace(),
            SimpleNamespace(),
            schema,
            [],
            tmp_path / "sme",
            responder=StaticResponder(default="x"),
            curated_root=curated,
            model=None,
            run_agent_repass=False,
            seed_ledger_if_empty=False,
        )


def test_skip_agent_does_not_seed_over_a_resolved_relocated_ledger(
    tmp_path: Path, monkeypatch
):
    """If the relocated ledger exists (even all-answered), scaffolding must not invent."""
    from governed_bi.curator import pipeline as pl

    curated = tmp_path / "corpus_curated"
    schema = "beer"
    relocated = curated / schema / "_build"
    relocated.mkdir(parents=True)
    answered = ClarificationRecord(
        id="c1",
        scope=f"{schema}.t",
        question="what is t?",
        status=ClarificationRecordStatus.answered,
        raised_by=["agent"],
        answer="already folded",
        answered_by="sme",
    )
    write_clarifications(relocated / "clarifications.jsonl", [answered])
    (relocated / "run_manifest.json").write_text(
        json.dumps(
            {
                "phase": "A",
                "schema": schema,
                "ledger_source": "agent",
                "clarification_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (curated / schema / "tables").mkdir(parents=True)

    class _FakeCorpus:
        assets = [_table(schema)]

    seeded = {"called": False}

    def _boom_seed(*a, **k):
        seeded["called"] = True
        raise AssertionError("seed_gap must not run over a resolved relocated ledger")

    monkeypatch.setattr(
        "governed_bi.corpus.loader.load_corpus", lambda *a, **k: _FakeCorpus()
    )
    monkeypatch.setattr(pl, "seed_gap_clarifications", _boom_seed)
    monkeypatch.setattr(pl, "_corpora_differ", lambda *a, **k: True)
    monkeypatch.setattr(pl, "_validate_fix_pass", lambda *a, **k: ([], {}, None))
    monkeypatch.setattr(pl, "_run_adversary_signal", lambda *a, **k: None)

    out = tmp_path / "sme"
    out.mkdir()
    pl.build_curated_corpus_with_sme(
        SimpleNamespace(),
        SimpleNamespace(),
        schema,
        [],
        out,
        responder=StaticResponder(default="nope"),
        curated_root=curated,
        model=None,
        run_agent_repass=False,
        seed_ledger_if_empty=True,
    )
    assert not seeded["called"]


# --------------------------------------------------------------------------- #
# F3 — limit / limit_dbs / scope hash
# --------------------------------------------------------------------------- #


def test_resume_refuses_limit_widen_and_narrow(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "split": "test",
                "limit": 5,
                "limit_dbs": 2,
                "question_scope_hash": "abc123",
            }
        ),
        encoding="utf-8",
    )
    base = {
        "split": "test",
        "limit": 5,
        "limit_dbs": 2,
        "question_scope_hash": "abc123",
    }
    _check_resume_manifest(tmp_path, base)

    with pytest.raises(RuntimeError, match="Scope is not a resume knob"):
        _check_resume_manifest(tmp_path, {**base, "limit": None})  # widen
    with pytest.raises(RuntimeError, match="Scope is not a resume knob"):
        _check_resume_manifest(tmp_path, {**base, "limit": 2})  # narrow
    with pytest.raises(RuntimeError, match="Scope is not a resume knob"):
        _check_resume_manifest(tmp_path, {**base, "limit_dbs": 10})
    with pytest.raises(RuntimeError, match="Scope is not a resume knob"):
        _check_resume_manifest(
            tmp_path, {**base, "question_scope_hash": "different"}
        )


def test_manifest_records_limit_caps_and_scope_hash():
    from governed_bi.eval.run_datalake import _build_manifest, _question_scope_hash
    from governed_bi.prompts import resolve as resolve_prompts

    pairs = [
        (SimpleNamespace(question_id="q1", question="q1"), "beer"),
        (SimpleNamespace(question_id="q2", question="q2"), "beer"),
    ]
    h = _question_scope_hash(pairs)
    m = _build_manifest(
        bird_dir=Path("."),
        split="test",
        model_name="m",
        prompt_variants=resolve_prompts(None),
        route_top_k=10,
        route_llm_pick=True,
        schema_pick_max_columns=12,
        use_embedder=True,
        skip_agent=False,
        serve_workers=1,
        limit=2,
        limit_dbs=1,
        question_scope_hash=h,
    )
    assert m["limit"] == 2
    assert m["limit_dbs"] == 1
    assert m["question_scope_hash"] == h


# --------------------------------------------------------------------------- #
# F5 — atomic JSONL rewrite
# --------------------------------------------------------------------------- #


def test_write_jsonl_is_atomic_temp_replace(tmp_path: Path, monkeypatch):
    import os

    path = tmp_path / "generations.curated.jsonl"
    path.write_text('{"question_id":"keep"}\n', encoding="utf-8")

    seen: dict[str, object] = {}
    real_replace = os.replace
    real_fsync = os.fsync

    def tracking_replace(src, dst):
        seen["replaced"] = (Path(src).name, Path(dst).name)
        assert Path(src).exists(), "temp must exist before replace"
        assert path.read_text(encoding="utf-8").startswith("{"), (
            "destination must still hold the prior file until replace"
        )
        return real_replace(src, dst)

    def tracking_fsync(fd):
        seen["fsync"] = True
        return real_fsync(fd)

    monkeypatch.setattr(os, "replace", tracking_replace)
    monkeypatch.setattr(os, "fsync", tracking_fsync)

    _write_jsonl(path, [{"question_id": "new", "correct": True}])
    assert seen.get("fsync") is True
    assert seen.get("replaced") is not None
    assert '"new"' in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp*"))


# --------------------------------------------------------------------------- #
# F6 — git_sha drift
# --------------------------------------------------------------------------- #


def test_paid_resume_refuses_git_sha_drift_by_default(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "git_sha": "aaaa", "skip_agent": False}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="git_sha"):
        _check_resume_manifest(
            tmp_path,
            {"split": "test", "git_sha": "bbbb", "skip_agent": False},
        )


def test_smoke_resume_warns_on_git_sha_drift(tmp_path: Path, capsys):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "git_sha": "aaaa", "skip_agent": True}),
        encoding="utf-8",
    )
    _check_resume_manifest(
        tmp_path,
        {"split": "test", "git_sha": "bbbb", "skip_agent": True},
    )
    assert "git_sha" in capsys.readouterr().out


def test_allow_git_sha_drift_opts_paid_resume_in(tmp_path: Path, capsys):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "git_sha": "aaaa", "skip_agent": False}),
        encoding="utf-8",
    )
    _check_resume_manifest(
        tmp_path,
        {"split": "test", "git_sha": "bbbb", "skip_agent": False},
        allow_git_sha_drift=True,
    )
    out = capsys.readouterr().out
    assert "git_sha" in out
    assert "allow-git-sha-drift" in out


# --------------------------------------------------------------------------- #
# F7 — twin stamp coverage
# --------------------------------------------------------------------------- #


def _row(
    qid: str,
    *,
    correct: bool,
    twin: bool | None,
    frozen: bool = False,
    order_sensitive: bool = False,
):
    r = {
        "question_id": qid,
        "db_id": "beer",
        "correct": correct,
        "correct_strict": correct,
        "generated_sql": "SELECT 1",
        "refused_by": None,
        "error": None,
    }
    if twin is not None:
        r["gold_twin_in_train"] = twin
    if frozen:
        r["gold_frozen"] = True
    if order_sensitive:
        r["gold_order_sensitive"] = True
    return r


def test_partial_twin_stamps_do_not_emit_strata_or_no_twin_comparison():
    mixed = [
        _row("a", correct=True, twin=False),
        _row("b", correct=False, twin=False),
        _row("c", correct=True, twin=None),  # unstamped
    ]
    s = _summarise_rows("curated", mixed)
    assert s["n_twin_unstamped"] == 1
    assert s["ex_no_twin"] is None
    assert s["ex_twin"] is None

    lo = [_row(f"q{i}", correct=False, twin=False) for i in range(4)] + [
        _row("partial", correct=False, twin=None)
    ]
    hi = [_row(f"q{i}", correct=True, twin=False) for i in range(4)] + [
        _row("partial", correct=True, twin=False)
    ]
    comparisons, _ = _compare_arms({"baseline": lo, "curated": hi})
    assert comparisons[0]["no_twin"] is None


def test_full_twin_stamp_coverage_still_emits_strata():
    rows = [
        _row("t1", correct=True, twin=True),
        _row("n1", correct=False, twin=False),
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_twin_unstamped"] == 0
    assert s["ex_no_twin"] == pytest.approx(0.0)
    assert s["ex_twin"] == pytest.approx(1.0)


def test_unstamped_frozen_or_order_sensitive_blocks_strata_and_no_twin():
    """Stamp completeness is over all scored rows, not the gradeable subset.

    An unstamped frozen/order-sensitive row used to leave summary twin EX intact
    (gradeable-only gate) while comparisons[].no_twin refused (all-rows gate), or
    the reverse after a later edit. One shared population closes both.
    """
    from governed_bi.eval.run_datalake import _twin_stamps_complete

    gradeable_stamped = [
        _row("ok", correct=True, twin=False),
        _row("twin", correct=False, twin=True),
    ]

    for kind, unstamped in (
        ("frozen", _row("bad", correct=False, twin=None, frozen=True)),
        ("order_sensitive", _row("bad", correct=False, twin=None, order_sensitive=True)),
    ):
        rows = [*gradeable_stamped, unstamped]
        assert not _twin_stamps_complete(rows), kind
        s = _summarise_rows("curated", rows)
        assert s["n_twin_unstamped"] == 1, kind
        assert s["n_gradeable"] == 2, kind
        assert s["ex_no_twin"] is None, kind
        assert s["ex_twin"] is None, kind

        lo = [
            _row("ok", correct=False, twin=False),
            _row("twin", correct=False, twin=True),
            {**unstamped, "correct": False},
        ]
        hi = [
            _row("ok", correct=True, twin=False),
            _row("twin", correct=True, twin=True),
            {**unstamped, "correct": True},
        ]
        comparisons, _ = _compare_arms({"baseline": lo, "curated": hi})
        assert comparisons[0]["no_twin"] is None, kind

    # Control: same shape with the ungradeable row stamped still emits strata.
    stamped_frozen = [
        *gradeable_stamped,
        _row("frozen", correct=False, twin=False, frozen=True),
    ]
    assert _twin_stamps_complete(stamped_frozen)
    s = _summarise_rows("curated", stamped_frozen)
    assert s["n_twin_unstamped"] == 0
    assert s["ex_no_twin"] == pytest.approx(1.0)
    assert s["ex_twin"] == pytest.approx(0.0)
