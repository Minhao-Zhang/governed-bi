"""The metric register must match what the drivers actually emit.

``governed_bi.eval.metrics`` declares every field the three run artifacts carry.
A declaration that drifts from the code is worse than none, so these tests are
the contract: the summary check calls the real summariser and compares key sets,
and the manifest checks build real manifests in both modes and run them through
the validator.

Why this file exists at all: the generation row is consumed by ``.get()`` in
eight modules, where a renamed key degrades to ``None`` rather than raising, and
the manifest is read by name by the ledger's comparability and resume gates,
where an *absent* key is indistinguishable from "both runs agree".
"""

from __future__ import annotations

import pytest

from governed_bi.eval import metrics
from governed_bi.eval.index import COMPARABILITY_KEYS, RESUME_DRIFT_KEYS
from governed_bi.eval.run_datalake import _summarise_rows


def _manifest(mode: str, **over):
    base = dict(
        mode=mode,
        bird_dir="/data/bird",
        split="test",
        model_name="gpt-5.6-luna",
        prompt_variants={},
        skip_agent=False,
        created_at_utc="20260728T000000Z",
        route_top_k=3,
        route_llm_pick=False,
        schema_pick_max_columns=12,
        use_embedder=True,
    )
    base.update(over)
    return metrics.build_manifest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


def test_both_modes_produce_a_manifest_that_passes_the_validator():
    metrics.validate_manifest(_manifest("datalake"))
    metrics.validate_manifest(
        _manifest(
            "single",
            route_top_k=None,
            route_llm_pick=None,
            schema_pick_max_columns=None,
            use_embedder=None,
            db_ids=["beer_factory"],
        )
    )


@pytest.mark.parametrize("key,_label", COMPARABILITY_KEYS + RESUME_DRIFT_KEYS)
def test_every_ledger_gate_key_is_present_in_both_modes(key, _label):
    """The regression this file was written for.

    ``comparable()`` skips a knob that is ``None`` on both sides. So a key the
    single-schema manifest never wrote was not "unknown, be careful" — it was
    "these agree". Six of the eight comparability keys were missing there, and two
    of them were load-bearing: ``split``, and ``corpus_content_hash``, whose own
    comment in ``index.py`` calls it out as the one thing the check did not cover
    because the corpus *is* the treatment.
    """
    single = _manifest(
        "single",
        route_top_k=None,
        route_llm_pick=None,
        schema_pick_max_columns=None,
        use_embedder=None,
    )
    assert key in single, f"{key} absent from the single-schema manifest"
    assert key in _manifest("datalake"), f"{key} absent from the pooled manifest"


def test_two_single_schema_runs_over_different_corpora_are_not_comparable():
    """The end-to-end regression, through the real driver builder and the real gate.

    Before the register, this pair returned ``comparable() == True``: six of the
    eight keys were absent from this driver's manifest, ``comparable()`` skips a key
    that is ``None`` on both sides, and the two that remained (``model``,
    ``prompt_set_hash``) were identical. So a run over corpus A on the test split
    and a run over corpus B on the train split were reported as the same
    configuration — by the driver whose numbers were quoted.
    """
    from governed_bi.eval.index import comparable
    from governed_bi.eval.run_experiment import build_manifest

    def single(**over):
        return build_manifest(
            db_id="beer_factory",
            bird_dir="/d",
            pg_dsn="host=h port=5435",
            max_agent_steps=8,
            skip_agent=False,
            model_name="gpt-5.6-luna",
            resolved_prompts={},
            **over,
        )

    a, b = single(), single(split="train")
    a["corpus_content_hash"] = "sha256:corpusA"
    b["corpus_content_hash"] = "sha256:corpusB"

    ok, diffs = comparable(a, b)
    assert not ok
    assert any("split" in d for d in diffs), diffs
    assert any("corpus content" in d for d in diffs), diffs


def test_two_single_schema_runs_of_the_same_configuration_stay_comparable():
    """The other direction: the fix must not make everything incomparable. Routing
    knobs are ``None`` on both sides because neither run routed, and that is a
    genuine agreement, not a missing key."""
    from governed_bi.eval.index import comparable
    from governed_bi.eval.run_experiment import build_manifest

    def single():
        m = build_manifest(
            db_id="beer_factory",
            bird_dir="/d",
            pg_dsn="host=h port=5435",
            max_agent_steps=8,
            skip_agent=False,
            model_name="gpt-5.6-luna",
            resolved_prompts={},
        )
        m["corpus_content_hash"] = "sha256:same"
        return m

    ok, diffs = comparable(single(), single())
    assert ok, diffs


def test_the_validator_rejects_a_manifest_missing_a_gate_key():
    m = _manifest("datalake")
    del m["corpus_content_hash"]
    with pytest.raises(ValueError, match="corpus_content_hash"):
        metrics.validate_manifest(m)


def test_a_bypassed_router_records_none_explicitly_rather_than_a_default():
    """Recording ``route_top_k=3`` for a run that pinned one schema would claim the
    router ran with that shortlist. It did not run at all. ``None`` plus
    ``routing_bypassed=True`` says which of the two situations produced the None."""
    single = _manifest(
        "single",
        route_top_k=None,
        route_llm_pick=None,
        schema_pick_max_columns=None,
        use_embedder=None,
    )
    assert single["routing_bypassed"] is True
    assert single["route_top_k"] is None
    assert _manifest("datalake")["routing_bypassed"] is False


def test_skip_agent_records_no_model_in_either_mode():
    """A smoke run that called no model must not report a model name, or it compares
    as the same configuration as a real run."""
    for mode in ("single", "datalake"):
        m = _manifest(
            mode,
            skip_agent=True,
            route_top_k=None if mode == "single" else 3,
            route_llm_pick=None if mode == "single" else False,
            schema_pick_max_columns=None if mode == "single" else 12,
            use_embedder=None if mode == "single" else True,
        )
        assert m["model"] is None, mode


def test_corpus_hash_is_declared_before_the_build_and_stamped_after(tmp_path):
    """Declared ``None`` up front because the manifest is written before any corpus
    exists — the gold pre-flight has to run before a model is paid for. Declared
    rather than added later, because a gate key absent from the manifest can never
    fire."""
    m = _manifest("datalake")
    assert m["corpus_content_hash"] is None
    assert "corpus_content_hash" in m

    baseline = tmp_path / "corpus_baseline"
    curated = tmp_path / "corpus_curated"
    for root in (baseline, curated):
        (root / "beer_factory" / "tables").mkdir(parents=True)
        (root / "beer_factory" / "tables" / "t.yaml").write_text(
            f"asset_type: table\nid: tbl_{root.name}\n", encoding="utf-8"
        )

    observed = metrics.stamp_corpus_hashes(
        m, {"baseline": baseline, "curated": curated}
    )
    assert m["corpus_content_hash"] == observed
    assert set(m["corpus_content_hash_by_arm"]) == {"baseline", "curated"}
    # Per-arm digests differ, so a reader sees WHICH arm's corpus moved.
    assert (
        m["corpus_content_hash_by_arm"]["baseline"]
        != m["corpus_content_hash_by_arm"]["curated"]
    )


def test_a_resume_keeps_the_declared_hash_so_the_caller_can_compare(tmp_path):
    root = tmp_path / "corpus_baseline" / "beer_factory" / "tables"
    root.mkdir(parents=True)
    (root / "t.yaml").write_text("asset_type: table\nid: tbl_x\n", encoding="utf-8")

    m = _manifest("datalake")
    m["corpus_content_hash"] = "sha256:declared-by-the-first-invocation"
    observed = metrics.stamp_corpus_hashes(m, {"baseline": tmp_path / "corpus_baseline"})
    assert m["corpus_content_hash"] == "sha256:declared-by-the-first-invocation"
    assert m["corpus_content_hash_observed"] == observed


# --------------------------------------------------------------------------- #
# Arm summary
# --------------------------------------------------------------------------- #


def _row(**over):
    row = {
        "arm": "curated",
        "question_id": "q1",
        "db_id": "beer_factory",
        "correct": True,
        "correct_strict": True,
        "outcome": "answered",
        "generated_sql": "SELECT 1 FROM t",
        "gold_nrows": 1,
        "pred_nrows": 1,
        "nrows_match": True,
        "difficulty": "simple",
        "attempts": 1,
        "latency_sec": 1.0,
        "tier": "governed",
        "safety_clearance": True,
        "semantic_assurance": "unflagged",
        "routed_hit": True,
        "routed_schemas": ["beer_factory"],
    }
    row.update(over)
    return row


def test_the_summary_emits_exactly_the_declared_field_set():
    """Both directions. An emitted-but-undeclared key means the register is stale;
    a declared-but-absent key means a consumer reading it by name gets ``None``."""
    summary = _summarise_rows("curated", [_row(), _row(question_id="q2", correct=False)])

    declared = set(metrics.SUMMARY_FIELDS)
    emitted = set(summary)

    assert not emitted - declared, (
        f"summary emits undeclared fields: {sorted(emitted - declared)} — "
        "add them to governed_bi.eval.metrics"
    )
    assert not declared - emitted, (
        f"declared fields the summary never emits: {sorted(declared - emitted)}"
    )


def test_every_declared_rate_names_its_denominator():
    """The recurring defect class here is a rate whose denominator silently absorbs
    another outcome's failures — over all rows, an arm that refuses 8 of 10 reports
    the best graded-delivery rate. Naming the population is what makes that
    reviewable."""
    for m in metrics.SUMMARY_RATES:
        assert m.denominator, f"{m.name} declares no denominator"


def test_quotability_free_pass_counters_are_declared():
    """``index.quotable()`` reads these three by name. They arrive in the summary
    through a ``**free_pass_counts(...)`` splat, which is exactly the shape a
    register misses if it is written by reading the literal dict."""
    for name in (
        "n_correct_with_empty_gold",
        "n_correct_and_pred_has_no_from",
        "n_correct_and_zero_table_overlap",
    ):
        assert name in metrics.SUMMARY_FIELDS


# --------------------------------------------------------------------------- #
# Generation row
# --------------------------------------------------------------------------- #


def test_the_generated_doc_lists_every_declared_field():
    """``docs/eval-metrics.md`` is generated from the register. A stale generated doc
    is worse than no doc, so the field names have to still be in it — the generator
    is `scripts/gen_eval_metrics_doc.py`."""
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "docs" / "eval-metrics.md").read_text(
        encoding="utf-8"
    )
    declared = (
        [m.name for m in metrics.MANIFEST_FIELDS]
        + list(metrics.SUMMARY_FIELDS)
        + list(metrics.ROW_FIELDS)
    )
    absent = sorted({name for name in declared if f"`{name}`" not in doc})
    assert not absent, (
        f"docs/eval-metrics.md does not mention {absent} — "
        "re-run scripts/gen_eval_metrics_doc.py"
    )


def test_the_row_register_has_no_duplicates_across_its_groups():
    assert len(metrics.ROW_FIELDS) == len(set(metrics.ROW_FIELDS))


def test_the_row_register_covers_what_the_summariser_reads():
    """Anything the summariser pulls off a row has to be a declared row field, or
    the register cannot be used to reason about what a row must contain."""
    import inspect
    import re

    src = inspect.getsource(_summarise_rows)
    read = set(re.findall(r'r\.get\("(\w+)"', src)) | set(
        re.findall(r'row\.get\("(\w+)"', src)
    )
    # Keys the summariser derives itself rather than reading off the row.
    derived = {"n", "arm"}
    undeclared = sorted(read - set(metrics.ROW_FIELDS) - derived)
    assert not undeclared, (
        f"the summariser reads row keys that are not declared: {undeclared}"
    )
