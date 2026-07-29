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

import re
from pathlib import Path

import pytest

from governed_bi.eval import metrics
from governed_bi.eval.index import COMPARABILITY_KEYS, RESUME_DRIFT_KEYS
from governed_bi.eval.run_datalake import _summarise_rows

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(metrics.__file__).resolve().parent


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
        llm_temperature=0.0,
        question_pool_hash="pool0000",
        arms=("baseline", "curated"),
        oracles=(),
        replicate_of=None,
        db_ids=None,
        limit=None,
        limit_dbs=None,
        question_scope_hash="abc123",
    )
    base.update(over)
    return metrics.build_manifest(**base)  # type: ignore[arg-type]


def _single_manifest(**over):
    """The single-schema driver's own builder, with its required arguments."""
    from governed_bi.eval.run_experiment import build_manifest

    base = dict(
        db_id="beer_factory",
        bird_dir="/d",
        pg_dsn="host=h port=5435",
        max_agent_steps=8,
        skip_agent=False,
        model_name="gpt-5.6-luna",
        resolved_prompts={},
        limit=None,
        llm_temperature=None,
        question_pool_hash="pool0000",
    )
    base.update(over)
    return build_manifest(**base)  # type: ignore[arg-type]


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
    eight keys of the time were absent from this driver's manifest, ``comparable()``
    skips a key that is ``None`` on both sides, and the two that remained (``model``,
    ``prompt_set_hash``) were identical. So a run over corpus A on the test split
    and a run over corpus B on the train split were reported as the same
    configuration — by the driver whose numbers were quoted.

    ``split`` is set by hand here rather than passed: this driver scores test only,
    which is why it no longer takes a ``split`` parameter. The gate still has to fire
    on a directory whose manifest says otherwise.
    """
    from governed_bi.eval.index import comparable

    a, b = _single_manifest(), _single_manifest()
    b["split"] = "train"
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

    def single():
        m = _single_manifest()
        m["corpus_content_hash"] = "sha256:same"
        return m

    ok, diffs = comparable(single(), single())
    assert ok, diffs


def test_the_single_schema_driver_records_the_temperature_it_actually_used():
    """Presence is all ``validate_manifest`` can check, and a silent default satisfies
    it. ``llm_temperature`` defaulted to ``None`` and this driver never passed it, so
    every single-schema manifest recorded "provider default" for runs whose configured
    temperature really was forwarded to the model
    (``llm.langchain_client.from_config``) — and every gate was satisfied, because the
    key was there."""
    assert _single_manifest(llm_temperature=0.7)["llm_temperature"] == 0.7
    # And ``None`` still means "never set, so the provider default applied" — now
    # because a caller said so rather than because nobody did.
    assert _single_manifest(llm_temperature=None)["llm_temperature"] is None


@pytest.mark.parametrize(
    "knob",
    [
        "llm_temperature",
        "question_pool_hash",
        "arms",
        "oracles",
        "limit",
        "db_ids",
        "question_scope_hash",
    ],
)
def test_no_manifest_knob_or_scope_field_may_be_defaulted(knob):
    """A defaulted parameter records a value the run never used and passes every gate.
    Parametrized over the register's own fields, so the next one added cannot quietly
    acquire a default."""
    base = dict(
        mode="datalake",
        bird_dir="/data/bird",
        split="test",
        model_name="m",
        prompt_variants={},
        skip_agent=False,
        created_at_utc="20260728T000000Z",
        route_top_k=3,
        route_llm_pick=False,
        schema_pick_max_columns=12,
        use_embedder=True,
        llm_temperature=0.0,
        question_pool_hash="pool0000",
        arms=(),
        oracles=(),
        replicate_of=None,
        db_ids=None,
        limit=None,
        limit_dbs=None,
        question_scope_hash=None,
    )
    base.pop(knob)
    with pytest.raises(TypeError, match=knob):
        metrics.build_manifest(**base)  # type: ignore[arg-type]


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


# A manifest key set anywhere other than inside ``build_manifest``. Both drivers and
# ``stamp_corpus_hashes`` add keys after the fact, and those are exactly the four that
# hid from the register: no builder returns them, so a test that only inspects a built
# manifest cannot see them.
_MANIFEST_MUTATION_PATTERNS = (
    r'manifest\["(\w+)"\]\s*=',
    r'\{\*\*(?:manifest|prior),\s*"(\w+)"',
)


def _manifest_keys_added_outside_the_builder() -> set[str]:
    found: set[str] = set()
    for name in ("metrics.py", "run_datalake.py", "run_experiment.py"):
        src = (EVAL_DIR / name).read_text(encoding="utf-8")
        for pattern in _MANIFEST_MUTATION_PATTERNS:
            found |= set(re.findall(pattern, src))
    return found


def test_the_post_build_manifest_scan_still_finds_the_known_mutations():
    """A canary for the scan below. If the drivers change how they stamp a manifest and
    these patterns stop matching, the emitted-but-undeclared check silently becomes a
    check of the builder only — which is the state that let four fields hide."""
    found = _manifest_keys_added_outside_the_builder()
    for name in (
        "corpus_content_hash_observed",
        "corpus_content_hash_by_arm",
        "completed_at_utc",
        "resumes",
    ):
        assert name in found, (
            f"the scan no longer sees {name} being written; _MANIFEST_MUTATION_PATTERNS "
            "is stale and this file's manifest check has gone blind"
        )


def test_the_manifest_emits_exactly_the_declared_field_set(tmp_path):
    """The check the manifest side did not have, and whose absence made this module's
    own opening claim false.

    ``corpus_content_hash_observed`` and ``corpus_content_hash_by_arm`` are written by
    ``stamp_corpus_hashes`` twelve lines below the register that failed to declare them;
    ``db_id`` by ``run_experiment.build_manifest``; ``completed_at_utc`` by the pooled
    driver at write time. All four reached ``manifest.json`` undeclared, because the
    summary had this test and the manifest did not.
    """
    from governed_bi.eval.run_datalake import _build_manifest

    pooled = _build_manifest(
        bird_dir=tmp_path,
        split="test",
        model_name="gpt-5.6-luna",
        prompt_variants={},
        route_top_k=3,
        route_llm_pick=False,
        schema_pick_max_columns=12,
        use_embedder=True,
        skip_agent=False,
        serve_workers=1,
        question_pool_hash="pool0000",
    )
    single = _single_manifest()

    root = tmp_path / "corpus_baseline" / "beer_factory" / "tables"
    root.mkdir(parents=True)
    (root / "t.yaml").write_text("asset_type: table\nid: tbl_x\n", encoding="utf-8")
    for m in (pooled, single):
        metrics.stamp_corpus_hashes(m, {"baseline": tmp_path / "corpus_baseline"})

    emitted = set(pooled) | set(single) | _manifest_keys_added_outside_the_builder()
    declared = {m.name for m in metrics.MANIFEST_DECLARED}

    assert not emitted - declared, (
        f"manifest.json carries undeclared fields: {sorted(emitted - declared)} — "
        "add them to governed_bi.eval.metrics"
    )
    # The other direction, for the fields every manifest must carry. The stamped and
    # mode-specific groups are deliberately excluded: requiring them would reject the
    # pre-run write that exists so a crashed run still leaves its knobs on disk.
    required = {m.name for m in metrics.MANIFEST_FIELDS}
    assert not required - (set(pooled) & set(single)), (
        "declared-and-required fields missing from a built manifest: "
        f"{sorted(required - (set(pooled) & set(single)))}"
    )


# --------------------------------------------------------------------------- #
# The comparability gate is derived from the register, not spelled beside it
# --------------------------------------------------------------------------- #


def test_every_declared_knob_is_either_gated_or_explicitly_excused():
    """The defect: ``llm_temperature`` was declared a knob — a field documented as
    changing what a scored row means — and was simply absent from
    ``COMPARABILITY_KEYS``, so two runs decoded at different temperatures compared as
    the same experiment. Nothing failed; the key was just not in the tuple."""
    from governed_bi.eval.index import COMPARABILITY_EXCLUSIONS

    gated = {k for k, _ in COMPARABILITY_KEYS}
    declared = {m.name for m in metrics.MANIFEST_KNOBS}
    assert gated | set(COMPARABILITY_EXCLUSIONS) == declared, (
        "every declared knob must be gated or carry a written reason not to be; "
        f"ungated and unexcused: {sorted(declared - gated - set(COMPARABILITY_EXCLUSIONS))}"
    )
    # A stale exclusion is its own hazard: it reads as a considered decision about a
    # knob that no longer exists.
    assert not set(COMPARABILITY_EXCLUSIONS) - declared
    assert "llm_temperature" in gated
    assert all(reason.strip() for reason in COMPARABILITY_EXCLUSIONS.values())


def test_two_runs_at_different_temperatures_are_not_comparable():
    from governed_bi.eval.index import comparable

    a = _single_manifest(llm_temperature=0.0)
    b = _single_manifest(llm_temperature=0.7)
    for m, h in ((a, "sha256:same"), (b, "sha256:same")):
        m["corpus_content_hash"] = h
    ok, diffs = comparable(a, b)
    assert not ok
    assert any("temperature" in d for d in diffs), diffs


def test_the_question_pool_hash_joined_the_comparability_gate():
    """Derivation is the claim; this is the check that it happened.

    ``COMPARABILITY_KEYS`` is built from ``MANIFEST_KNOBS`` minus
    ``COMPARABILITY_EXCLUSIONS``, so a new knob is *supposed* to join the gate with no
    second edit. Asserted rather than assumed, because the value of that design is
    entirely in the case nobody re-reads: a key that silently failed to join would leave
    two runs over different question pools comparing as one experiment, which is the
    defect this key exists for.
    """
    from governed_bi.eval.index import COMPARABILITY_EXCLUSIONS

    assert ("question_pool_hash", "question pool") in COMPARABILITY_KEYS
    # And in the resume guard, which is derived from the same tuple: a dataset
    # regenerated halfway through a directory is the same corruption, one level down.
    assert ("question_pool_hash", "question pool") in RESUME_DRIFT_KEYS
    assert "question_pool_hash" not in COMPARABILITY_EXCLUSIONS


def test_two_runs_over_different_question_pools_are_not_comparable():
    """End to end, through the real driver builder and the real gate.

    The sibling dataset repo filters questions whose gold SQL contradicts their
    ``evidence``: schemas stay, the split moves. Every knob in *this* repo is unchanged
    across that, so before this key the two runs matched on all of them.
    """
    from governed_bi.eval.index import comparable

    a = _single_manifest(question_pool_hash="pool_before_the_filter")
    b = _single_manifest(question_pool_hash="pool_after_the_filter")
    for m in (a, b):
        m["corpus_content_hash"] = "sha256:same"

    ok, diffs = comparable(a, b)
    assert not ok
    assert any("question pool" in d for d in diffs), diffs
    # The other direction: the same pool must still compare, or the gate is useless.
    assert comparable(a, _single_manifest(question_pool_hash="pool_before_the_filter") | {
        "corpus_content_hash": "sha256:same"
    })[0]


def test_the_pool_hash_moves_with_the_graded_set_and_with_its_gold():
    """Both halves of the requirement: it must move when the graded pool moves, and
    stay put otherwise.

    The gold digest is in the payload for the same reason ``prompt_set_hash`` hashes
    prompt TEXT rather than variant ids: an upstream correction that re-points a
    ``question_id`` at different gold changes what every EX in the run means, while
    leaving the id set — and therefore ``question_scope_hash`` — untouched.
    """
    pool = [
        ("beer_factory", "q1", "SELECT 1"),
        ("beer_factory", "q2", "SELECT 2"),
    ]
    base = metrics.question_pool_hash(pool)

    # Order is not information: the drivers iterate dbs in argv order.
    assert metrics.question_pool_hash(reversed(pool)) == base
    # A question dropped by the upstream filter.
    assert metrics.question_pool_hash(pool[:1]) != base
    # Same ids, corrected gold.
    assert metrics.question_pool_hash(
        [("beer_factory", "q1", "SELECT 1"), ("beer_factory", "q2", "SELECT 2 AS n")]
    ) != base
    # Same ids and gold on a different schema is a different pool.
    assert metrics.question_pool_hash(
        [("restaurant", q, sql) for _db, q, sql in pool]
    ) != base
    # A schema the filter emptied reads as empty rather than as a digest of nothing,
    # which the new dataset makes a real possibility.
    assert metrics.question_pool_hash([]) == "empty"


def test_a_record_with_no_manifest_schema_version_is_refused_not_passed():
    """``comparable()`` skips a knob that is ``None`` on both sides, which is sound only
    where the manifest guarantees every knob is present. A record written before that
    guarantee has no such promise, so its omissions would read as agreement — the exact
    failure the guarantee exists to end. Refused rather than silently passed."""
    from governed_bi.eval.index import comparable

    modern = _single_manifest()
    modern["corpus_content_hash"] = "sha256:same"
    legacy = dict(modern)
    del legacy["manifest_schema_version"]

    ok, diffs = comparable(modern, legacy)
    assert not ok
    assert any("manifest_schema_version" in d for d in diffs), diffs
    # Both sides missing is worse, not better.
    older = dict(legacy)
    assert not comparable(legacy, older)[0]
    # ...and a pair that both carry it compares on the knobs, as before.
    assert comparable(modern, dict(modern))[0]


def test_the_version_stamp_is_set_by_the_builder_so_no_call_site_changed():
    for m in (_manifest("datalake"), _single_manifest()):
        assert m["manifest_schema_version"] == metrics.MANIFEST_SCHEMA_VERSION


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


def _doc() -> str:
    return (REPO_ROOT / "docs" / "eval-metrics.md").read_text(encoding="utf-8")


def test_the_generated_doc_lists_every_declared_field():
    """``docs/eval-metrics.md`` is generated from the register. A stale generated doc
    is worse than no doc, so the field names have to still be in it — the generator
    is `scripts/gen_eval_metrics_doc.py`."""
    doc = _doc()
    declared = (
        [m.name for m in metrics.MANIFEST_DECLARED]
        + list(metrics.SUMMARY_FIELDS)
        + list(metrics.ROW_FIELDS)
        + list(metrics.STAGE_EVENT_FIELDS)
        + list(metrics.SPLIT_GAP_RATES)
        + list(metrics.SPLIT_GAP_FIELDS)
    )
    absent = sorted({name for name in declared if f"`{name}`" not in doc})
    assert not absent, (
        f"docs/eval-metrics.md does not mention {absent} — "
        "re-run scripts/gen_eval_metrics_doc.py"
    )


def test_the_counts_printed_in_the_doc_match_the_register():
    """Names in backticks were the only thing checked, and a count is not a name.

    The generator summed rates + counts + means + blocks and omitted
    ``SUMMARY_CONDITIONALS`` entirely, so the page advertised 80 summary fields against
    86 declared — and every one of the six missing fields still appeared in the counts
    table, so the name-grep above passed. Each count now comes from ``len()`` of the
    register tuple; these assertions are what makes that checkable rather than claimed.
    """
    doc = _doc()
    for expected in (
        f"| `manifest.json` | {len(metrics.MANIFEST_DECLARED)} "
        f"({len(metrics.MANIFEST_FIELDS)} in every run) |",
        f"| `generations.<arm>.jsonl` | {len(metrics.ROW_FIELDS)} per (question, arm) |",
        f"| `summary.json` | {len(metrics.SUMMARY_FIELDS)} |",
        f"| `stage_events.jsonl` | {len(metrics.STAGE_EVENT_FIELDS)} "
        "per (question, arm, stage) |",
        f"| `split_gap.json` | {len(metrics.SPLIT_GAP_FIELDS)} |",
    ):
        assert expected in doc, (
            f"docs/eval-metrics.md does not print {expected!r} — the count it prints "
            "disagrees with the register; re-run scripts/gen_eval_metrics_doc.py"
        )


def test_the_single_schema_summary_emits_nothing_undeclared():
    """The direction the doc's "in both directions" claim overstated.

    ``run_experiment`` hand-builds its ``summary.json`` from ``ArmSummary`` plus three
    nested blocks, and nothing checked it against the register at all — only the pooled
    driver's ``_summarise_rows`` was. The reverse direction genuinely does not apply
    here: this driver reports a documented SUBSET of the pooled fields, so
    declared-but-absent is expected and only emitted-but-undeclared is a defect.
    """
    from dataclasses import fields

    from governed_bi.eval.run_experiment import ArmSummary

    # ``run_experiment`` writes each arm as ``asdict(ArmSummary)`` and then attaches
    # these three blocks under the same ``arms.<arm>`` path the pooled driver uses.
    emitted = {f.name for f in fields(ArmSummary)} | {"cost", "errors", "treatment"}
    undeclared = sorted(emitted - set(metrics.SUMMARY_FIELDS))
    assert not undeclared, (
        f"the single-schema arm summary emits undeclared fields: {undeclared} — "
        "add them to governed_bi.eval.metrics, or the register does not describe the "
        "driver whose numbers were historically quoted"
    )


# --------------------------------------------------------------------------- #
# The other two artifacts
# --------------------------------------------------------------------------- #


def test_the_stage_event_register_matches_what_the_driver_writes():
    from governed_bi.eval.run_datalake import _stage_event_rows

    (row,) = _stage_event_rows(
        {"stage_events": [{"stage": "route", "status": "ok", "ms": 12, "detail": None}]},
        question_id="q1",
        arm="curated",
        db_id="beer_factory",
    )
    assert set(row) == set(metrics.STAGE_EVENT_FIELDS)


def test_the_split_gap_rates_are_the_ones_split_gap_actually_gaps():
    """``split_gap.json``'s seven rates were undeclared, and the doc did not list the
    file among a run's artifacts at all. Declared here so a rate renamed in
    ``SUMMARY_RATES`` cannot leave ``split_gap`` reading a key nobody writes and
    reporting ``None`` gaps that read as "not measured on one split"."""
    from governed_bi.eval import split_gap

    assert metrics.SPLIT_GAP_RATES == split_gap.GAPPED_RATES
    rates = {m.name for m in metrics.SUMMARY_RATES}
    assert not set(metrics.SPLIT_GAP_RATES) - rates, (
        "a gapped rate that is not a declared summary rate cannot be read off either "
        f"split's summary: {sorted(set(metrics.SPLIT_GAP_RATES) - rates)}"
    )


def test_the_split_gap_file_fields_are_the_ones_it_writes(tmp_path):
    from governed_bi.eval.split_gap import split_gap, write_split_gap

    report = split_gap(
        {"arms": {"curated": {"n": 4, "ex_lenient": 0.5}}},
        {"arms": {"curated": {"n": 4, "ex_lenient": 0.25}}},
    )
    assert not set(report) - set(metrics.SPLIT_GAP_FIELDS)
    # The error branch too: it replaces the rest of the block rather than joining it.
    broken = write_split_gap(tmp_path, tmp_path / "nope", tmp_path / "nope")
    assert not set(broken) - set(metrics.SPLIT_GAP_FIELDS)


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


# --------------------------------------------------------------------------- #
# Conditional diagnostics
# --------------------------------------------------------------------------- #


def _c(qid, **over):
    """A delivered, correct row with every conditional input stamped."""
    r = {
        "arm": "curated", "question_id": qid, "db_id": "beer", "correct": True,
        "correct_strict": True, "outcome": "answered",
        "generated_sql": "SELECT 1 FROM t", "gold_nrows": 1, "pred_nrows": 1,
        "nrows_match": True, "attempts": 1, "latency_sec": 1.0, "tier": "governed",
        "safety_clearance": True, "semantic_assurance": "unflagged",
        "n_notes_injected": 1, "n_caveats_injected": 2, "decoy_touch": False,
        "by_guardrail_layer": {"syntax": 0, "column_allowlist": 0},
    }
    r.update(over)
    return r


def test_the_stamp_is_calibrated_against_correctness():
    """The point of the whole block. ``by_semantic_assurance`` reported how many
    turns were ``unflagged`` and never whether they were more often right — which is
    the stamp's entire claim, and which analyst.md calls an uncalibrated heuristic to
    be tuned in eval."""
    rows = [
        _c("q1"),
        _c("q2"),
        _c("q3", semantic_assurance="heuristic", tier="lineage", correct=False),
        _c("q4", semantic_assurance="heuristic", tier="lineage"),
        _c("q5", semantic_assurance="unverified", tier="fenced_raw", correct=False),
    ]
    s = _summarise_rows("curated", rows)

    assert s["ex_by_semantic_assurance"]["unflagged"] == {"n": 2, "ex_lenient": 1.0}
    assert s["ex_by_semantic_assurance"]["heuristic"] == {"n": 2, "ex_lenient": 0.5}
    assert s["ex_by_semantic_assurance"]["unverified"] == {"n": 1, "ex_lenient": 0.0}
    assert s["ex_by_tier"]["governed"]["ex_lenient"] == 1.0
    assert s["ex_by_tier"]["fenced_raw"]["ex_lenient"] == 0.0


def test_an_unstamped_row_is_not_a_stamp_level():
    """``_bucket`` groups on ``str(r.get(key))``, which renders a missing stamp as a
    ``"None"`` bucket sitting beside the real levels. An instrumentation gap must not
    read as a governance outcome, so these blocks exclude it instead."""
    rows = [_c("q1"), _c("q2", semantic_assurance=None, tier=None)]
    s = _summarise_rows("curated", rows)
    assert "None" not in s["ex_by_semantic_assurance"]
    assert "None" not in s["ex_by_tier"]
    assert s["ex_by_semantic_assurance"]["unflagged"]["n"] == 1


def test_a_missing_conditional_input_is_counted_out_not_filed_as_false():
    """The trap the twin strata document: ``not r.get(...)`` puts an ABSENT key in
    the FALSE stratum, which silently turns one side of the split into the pooled
    figure. Here a row with no recorded note count must land in neither side."""
    rows = [
        _c("q1", n_notes_injected=1),
        _c("q2", n_notes_injected=0, correct=False),
        _c("q3", n_notes_injected=None),
    ]
    block = _summarise_rows("curated", rows)["ex_by_note_injected"]
    assert block["n_with"] == 1
    assert block["n_without"] == 1
    assert block["n_unstamped"] == 1
    assert block["with"] == 1.0
    assert block["without"] == 0.0


def test_an_empty_stratum_reports_none_rather_than_zero():
    """Routine, not exceptional: the baseline arm injects no caveats at all, so its
    caveat-present stratum is empty. ``0.0`` there would claim it was measured."""
    rows = [_c("q1", n_caveats_injected=0), _c("q2", n_caveats_injected=0)]
    block = _summarise_rows("curated", rows)["decoy_touch_by_caveat"]
    assert block["with"] is None
    assert block["n_with"] == 0
    assert block["without"] == 0.0


def test_the_caveat_split_is_conditioned_on_delivery():
    """Same denominator as ``decoy_touch_rate``: a refusal touched no column, and
    counting it dilutes both sides."""
    rows = [
        _c("q1", decoy_touch=True),
        _c("q2", generated_sql=None, outcome="refused", refused_by="refuse_gate",
           correct=False),
    ]
    block = _summarise_rows("curated", rows)["decoy_touch_by_caveat"]
    assert block["n_with"] + block["n_without"] + block["n_unstamped"] == 1


def test_repair_recovery_is_split_on_the_attempt_count():
    rows = [
        _c("q1", attempts=1),
        _c("q2", attempts=1, correct=False),
        _c("q3", attempts=3),
        _c("q4", attempts=None),
    ]
    block = _summarise_rows("curated", rows)["ex_by_repair"]
    assert block["without"] == 0.5      # first-attempt rows
    assert block["with"] == 1.0         # took a repair
    assert block["n_unstamped"] == 1


def test_the_guardrail_ceiling_counts_blocks_not_evaluations():
    """``by_guardrail_layer`` creates a key at 0 when a layer is merely EVALUATED and
    increments only on failure (``governance.py``: ``+ (0 if passed else 1)``), so a
    clean turn carries all five layers at zero. A truthiness test on the dict would
    report every governed turn as blocked."""
    rows = [
        _c("q1"),  # evaluated, nothing blocked
        _c("q2", by_guardrail_layer={"column_allowlist": 2}, correct=False),
        _c("q3", by_guardrail_layer={"term_semantics": 1}),  # blocked but still right
    ]
    block = _summarise_rows("curated", rows)["guardrail_cost_ceiling"]
    assert block["n_blocked"] == 2, "a clean turn's zero-valued layers are not blocks"
    assert block["n_blocked_and_wrong"] == 1
    assert block["blocked_then_wrong_rate"] == 0.5
    assert block["by_layer"] == {"column_allowlist": 1}


def test_the_guardrail_ceiling_is_none_when_nothing_blocked():
    block = _summarise_rows("curated", [_c("q1")])["guardrail_cost_ceiling"]
    assert block["n_blocked"] == 0
    assert block["blocked_then_wrong_rate"] is None


def test_every_conditional_names_its_denominator():
    for m in metrics.SUMMARY_CONDITIONALS:
        assert m.denominator, f"{m.name} declares no denominator"


def test_the_ceiling_is_documented_as_a_ceiling():
    """It counts turns where a layer blocked and the turn still ended wrong. Some of
    those were wrong for reasons the block had nothing to do with, and blocked SQL
    cannot be graded — grading it means executing un-guardrailed SQL. If the register
    ever describes this as measured loss, that is the bug."""
    entry = next(
        m for m in metrics.SUMMARY_CONDITIONALS if m.name == "guardrail_cost_ceiling"
    )
    assert "CEILING" in entry.meaning
    assert "cannot be graded" in entry.meaning
