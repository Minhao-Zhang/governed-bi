"""M4 offline gates: R1/R4/R10 proxies + trigger PIN (R7/R8)."""

from __future__ import annotations

from pathlib import Path

from governed_bi.config import Settings, load_settings
from governed_bi.corpus import Corpus, load_corpus
from governed_bi.corpus.schemas import NoteAsset, ProvenanceStatus, TableAsset
from governed_bi.eval.note_gates import run_offline_note_gates
from governed_bi.retrieval.triggers import fire_triggers

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


def test_offline_note_gates_green_on_beer_factory():
    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    settings = load_settings(apply_local=False)
    results = run_offline_note_gates(corpus, settings=settings)
    assert results
    assert all(r.passed for r in results), [r for r in results if not r.passed]


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
