"""M4 offline gates: R1/R4/R10 proxies + trigger PIN (R7/R8)."""

from __future__ import annotations

from pathlib import Path

from governed_bi.config import Settings, load_settings
from governed_bi.corpus import Corpus, load_corpus
from governed_bi.corpus.schemas import NoteAsset, ProvenanceStatus, TableAsset
from governed_bi.eval.note_gates import (
    GateResult,
    run_offline_note_gates,
    summarise_gates,
)
from governed_bi.retrieval.triggers import fire_triggers

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


def test_offline_note_gates_green_on_beer_factory():
    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    settings = load_settings(apply_local=False)
    results = run_offline_note_gates(corpus, settings=settings)
    assert results
    # Asserting `all(r.passed)` was the wrong expectation: a skipped gate carries
    # passed=True on purpose, so that assertion is also satisfied by a run in which
    # every gate bailed out on a setup gap. clean_pass demands that each gate
    # actually ran.
    summary = summarise_gates(results)
    assert summary.clean_pass, summary.detail
    assert summary.n_passed == len(results)


def test_all_skipped_gate_run_does_not_read_as_a_pass():
    """An empty corpus skips every gate; that must not report as green.

    This is the failure mode `GateResult.skipped` was introduced for: with nothing
    to compare, each gate returns passed=True, so the old `all(r.passed)` check
    below still holds while nothing whatsoever was measured.
    """
    results = run_offline_note_gates(Corpus(assets=[]), settings=Settings.for_env("dev"))
    assert all(r.passed for r in results)  # the trap the summary has to catch

    summary = summarise_gates(results)
    assert summary.n_skipped == len(results)
    assert summary.n_passed == 0
    assert summary.verdict == "inconclusive"
    assert not summary.clean_pass
    assert "skipped" in summary.detail


def test_summarise_gates_counts_skips_apart_from_passes():
    mixed = [
        GateResult("ran", True, "measured"),
        GateResult("bailed", True, "no questions", skipped=True),
    ]
    summary = summarise_gates(mixed)
    assert (summary.n_passed, summary.n_skipped, summary.n_failed) == (1, 1, 0)
    # A real pass alongside a skip is a pass on what ran, but not a clean one: the
    # skipped gate's invariant is still unverified.
    assert summary.verdict == "pass"
    assert not summary.clean_pass
    assert "1 passed, 0 failed, 1 skipped" in summary.detail

    failing = summarise_gates([*mixed, GateResult("broke", False, "regressed")])
    assert failing.verdict == "fail"
    assert failing.n_failed == 1
    assert not failing.clean_pass


def test_adv_wrong_note_pin_does_not_evict_true_schema():
    """R7/R10: a certified wrong-schema PIN must not evict the true schema.

    Multi-schema (the bundled gate skips single-schema beer_factory). top_k=1 puts
    the true schema at the shortlist boundary, so the additive-PIN fix is exercised:
    this FAILS on the pre-fix `merged[:max(top_k, len(pinned))]` eviction.
    """
    from dataclasses import replace

    from governed_bi.eval.note_gates import gate_adv_wrong_note

    true_tbl = TableAsset(
        id="tbl_sales_orders",
        schema="sales",
        physical_name="orders",
        description="revenue sales orders purchase amount total money paid",
    )
    wrong_tbl = TableAsset(
        id="tbl_weather_daily",
        schema="weather",
        physical_name="daily",
        description="weather climate temperature rainfall humidity forecast",
    )
    corpus = Corpus(assets=[true_tbl, wrong_tbl])
    settings = replace(
        Settings.for_env("dev"),
        pin_triggers_enabled=True,
        pin_require_certified=True,
        pin_max=3,
    )
    res = gate_adv_wrong_note(
        corpus,
        "revenue",
        true_schema="sales",
        wrong_schema="weather",
        settings=settings,
        top_k=1,
    )
    assert res.passed, res.detail


def test_fire_triggers_keyword_only_respects_certified_gate():
    table = TableAsset(id="tbl_s_orders", schema="s", physical_name="orders")
    draft = NoteAsset(
        id="note_draft_pin",
        kind="routing",
        summary="draft pin",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        publication_status=ProvenanceStatus.draft,
        scope=["schema:s"],
    )
    certified = NoteAsset(
        id="note_cert_pin",
        kind="routing",
        summary="cert pin",
        triggers=[{"kind": "keyword", "value": "revenue"}],
        publication_status=ProvenanceStatus.certified,
        scope=["schema:s"],
    )
    corpus = Corpus(assets=[table, draft, certified])
    settings = Settings.for_env("dev")
    from dataclasses import replace

    settings = replace(
        settings, pin_triggers_enabled=True, pin_require_certified=True, pin_max=3
    )
    hits = fire_triggers(corpus, "total revenue please", settings=settings)
    assert hits == ["note_cert_pin"]

    settings_dev = replace(settings, pin_require_certified=False)
    hits2 = fire_triggers(corpus, "total revenue please", settings=settings_dev)
    assert "note_draft_pin" in hits2
