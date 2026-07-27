"""Tests for the oracle ladder, the execution probe, and the note-scope contract."""

from __future__ import annotations

import pytest

from governed_bi.analyst.note_inject import LicensedScope, scope_matches
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import (
    Column,
    JoinAsset,
    LogicalType,
    MetricAsset,
    NoteAsset,
    TableAsset,
)
from governed_bi.eval.oracle import (
    GoldIndex,
    OracleRung,
    gold_tables_for,
    restrict_corpus,
)


# --------------------------------------------------------------------------- #
# The note-scope contract the oracle corpus got wrong
# --------------------------------------------------------------------------- #


def _note(scope):
    return NoteAsset(
        id="note_1", kind="business_rule", scope=scope, summary="a rule"
    )


def _licensed():
    return LicensedScope(
        table_ids=frozenset({"tbl_address_congress"}),
        column_ids=frozenset(),
        metric_ids=frozenset(),
        join_ids=frozenset(),
        schemas=frozenset({"address"}),
        db_name="bird",
    )


def test_a_bare_schema_name_in_note_scope_matches_nothing():
    """The bug that voided the oracle experiment.

    9,154 gold notes were written with ``scope: ['address']``. Scope matching wants
    a ``schema:`` prefix, a ``db:`` prefix, or an asset id. Every note silently
    failed to match, none reached a prompt, and the resulting null was published as
    proof that enriching the semantic layer is worthless.

    This asserts the contract so the next corpus builder finds out from a red test
    rather than from three months of conclusions.
    """
    assert scope_matches(_note(["address"]), _licensed()) is False


def test_a_schema_prefixed_scope_matches():
    assert scope_matches(_note(["schema:address"]), _licensed()) is True


def test_a_bare_asset_id_scope_matches():
    assert scope_matches(_note(["tbl_address_congress"]), _licensed()) is True


def test_an_empty_scope_is_global_and_matches():
    assert scope_matches(_note([]), _licensed()) is True


def test_a_note_defaults_to_proposed_not_certified():
    """The oracle builder set ``audit.provenance.status`` and assumed it published.

    ``publication_status`` is the field that governs precedence, and it defaults to
    ``proposed``. Setting the audit field instead changes nothing a reader of the
    corpus can see, because ``for_analyst`` strips audit entirely.
    """
    note = _note(["schema:address"])
    assert note.publication_status.value == "proposed"


# --------------------------------------------------------------------------- #
# oracle
# --------------------------------------------------------------------------- #


def _table(tid, schema, physical):
    return TableAsset(
        id=tid,
        schema=schema,
        physical_name=physical,
        grain="row",
        columns=[
            Column(
                physical_name="x",
                physical_type="TEXT",
                logical_type=LogicalType.string,
                nullable=False,
                is_unique=False,
            )
        ],
    )


def _corpus():
    return Corpus(
        assets=[
            _table("t_a", "s1", "alpha"),
            _table("t_b", "s1", "beta"),
            _table("t_c", "s2", "gamma"),
            JoinAsset(id="j_ab", left_table="t_a", right_table="t_b", on="alpha.x = beta.x"),
            JoinAsset(id="j_ac", left_table="t_a", right_table="t_c", on="alpha.x = gamma.x"),
            MetricAsset(id="m_a", name="m", base_table="t_a", expression="count(*)"),
            MetricAsset(id="m_c", name="m2", base_table="t_c", expression="count(*)"),
        ]
    )


def test_restrict_to_a_schema_drops_other_schemas_and_their_dependents():
    narrowed = restrict_corpus(_corpus(), schema="s1")
    assert {t.id for t in narrowed.tables()} == {"t_a", "t_b"}
    ids = {a.id for a in narrowed.assets}
    assert "j_ab" in ids  # both endpoints survive
    assert "j_ac" not in ids  # endpoint in the dropped schema
    assert "m_a" in ids and "m_c" not in ids


def test_restrict_to_gold_tables_keeps_only_those():
    narrowed = restrict_corpus(_corpus(), schema="s1", tables=frozenset({"alpha"}))
    assert {t.physical_name for t in narrowed.tables()} == {"alpha"}
    # A join needs both endpoints; with one table there is no edge to offer.
    assert not [a for a in narrowed.assets if isinstance(a, JoinAsset)]


def test_restricting_to_nothing_yields_an_empty_corpus_not_the_full_one():
    """Falling back to the full corpus would make the rung measure nothing."""
    narrowed = restrict_corpus(_corpus(), schema="s1", tables=frozenset({"nope"}))
    assert narrowed.tables() == []


def test_gold_tables_are_extracted_from_the_statement():
    assert gold_tables_for("SELECT a.x FROM alpha AS a JOIN beta AS b ON a.x = b.x") == {
        "alpha",
        "beta",
    }


def test_gold_tables_of_unparseable_sql_is_empty_not_an_error():
    assert gold_tables_for("SELCT nope FROM") == frozenset()


def test_gold_index_tolerates_duplicate_questions_with_the_same_answer():
    """Five BIRD questions appear in both splits with identical text and gold."""
    index = GoldIndex.build(
        [
            {"question": "How many?", "sql_rename": "SELECT 1", "question_id": "a"},
            {"question": "How many?", "sql_rename": "SELECT 1", "question_id": "b"},
        ]
    )
    assert index.get("How many?")["sql_rename"] == "SELECT 1"


def test_gold_index_refuses_duplicate_questions_with_different_answers():
    """Silently picking one would hand a question another's answer."""
    with pytest.raises(ValueError, match="different gold SQL"):
        GoldIndex.build(
            [
                {"question": "How many?", "sql_rename": "SELECT 1", "question_id": "a"},
                {"question": "How many?", "sql_rename": "SELECT 2", "question_id": "b"},
            ]
        )


def test_oracle_rungs_are_not_members_of_the_fair_arm_ladder():
    """Keeping them out of ``Arm`` is what stops one being quoted as performance."""
    from governed_bi.eval.arms import Arm

    assert {a.value for a in Arm}.isdisjoint({r.value for r in OracleRung})


# --------------------------------------------------------------------------- #
# oracle_tables_padded and the few-shot leak
# --------------------------------------------------------------------------- #


def _fs(fid, schema, sql):
    from governed_bi.corpus.schemas import FewShotAsset

    return FewShotAsset(id=fid, schema=schema, question="q?", sql=sql)


def test_padding_restores_the_table_count_so_only_identity_differs():
    from governed_bi.eval.oracle import pad_tables

    corpus = _corpus()
    padded = pad_tables(
        corpus, schema="s1", gold=frozenset({"alpha"}), target=2, seed_key="q1"
    )
    assert "alpha" in padded
    assert len(padded) == 2
    assert padded - {"alpha"} <= {"beta"}


def test_padding_is_deterministic_across_runs_but_varies_by_question():
    """No random source: a rung must reproduce exactly on a resume."""
    from governed_bi.eval.oracle import pad_tables

    corpus = Corpus(
        assets=[_table(f"t{i}", "s1", f"tbl{i}") for i in range(12)]
    )
    gold = frozenset({"tbl0"})
    a1 = pad_tables(corpus, schema="s1", gold=gold, target=5, seed_key="q1")
    a2 = pad_tables(corpus, schema="s1", gold=gold, target=5, seed_key="q1")
    b = pad_tables(corpus, schema="s1", gold=gold, target=5, seed_key="q2")
    assert a1 == a2  # same question, same padding, every time
    assert a1 != b  # different questions get different distractors


def test_padding_never_drops_a_gold_table_even_when_target_is_small():
    from governed_bi.eval.oracle import pad_tables

    gold = frozenset({"alpha", "beta"})
    padded = pad_tables(_corpus(), schema="s1", gold=gold, target=1, seed_key="q")
    assert gold <= padded


def test_a_few_shot_citing_a_dropped_table_is_not_rendered():
    """It shows the model gold SQL the turn is then blocked from imitating.

    Measured at 73.7% of exemplars under oracle_tables before this filter, which
    depresses the rung for a reason unrelated to the stage it isolates.
    """
    corpus = Corpus(
        assets=[
            _table("t_a", "s1", "alpha"),
            _table("t_b", "s1", "beta"),
            _fs("fs_ok", "s1", "SELECT x FROM alpha"),
            _fs("fs_leak", "s1", "SELECT x FROM beta"),
        ]
    )
    narrowed = restrict_corpus(corpus, schema="s1", tables=frozenset({"alpha"}))
    ids = {a.id for a in narrowed.assets}
    assert "fs_ok" in ids
    assert "fs_leak" not in ids


def test_an_unparseable_few_shot_is_kept_rather_than_silently_dropped():
    """Dropping it would shrink the prompt for a parser gap, not the intervention."""
    corpus = Corpus(
        assets=[_table("t_a", "s1", "alpha"), _fs("fs_bad", "s1", "SELCT nope FROM")]
    )
    narrowed = restrict_corpus(corpus, schema="s1", tables=frozenset({"alpha"}))
    assert "fs_bad" in {a.id for a in narrowed.assets}


def test_schema_only_narrowing_keeps_every_few_shot():
    """The filter is table-scoped; oracle_schema must be unaffected by it."""
    corpus = Corpus(
        assets=[
            _table("t_a", "s1", "alpha"),
            _table("t_b", "s1", "beta"),
            _fs("fs_b", "s1", "SELECT x FROM beta"),
        ]
    )
    narrowed = restrict_corpus(corpus, schema="s1")
    assert "fs_b" in {a.id for a in narrowed.assets}


def test_the_padded_rung_is_a_known_cli_value_and_not_an_arm():
    from governed_bi.eval.arms import Arm

    assert OracleRung.tables_padded.value == "oracle_tables_padded"
    assert OracleRung.tables_padded.value not in {a.value for a in Arm}


def test_padding_is_degenerate_when_gold_already_fills_the_budget():
    """The control is vacuous at BOTH ends, onto different neighbours.

    Padded to the whole schema it is oracle_schema; not padded at all -- which
    happens whenever gold needs as many tables as the budget allows -- it is
    oracle_tables. Either way the row says nothing about table identity.
    """
    from governed_bi.eval.oracle import pad_tables

    corpus = Corpus(assets=[_table(f"t{i}", "s1", f"tbl{i}") for i in range(20)])
    gold = frozenset(f"tbl{i}" for i in range(9))  # 9 gold, budget 8
    padded = pad_tables(corpus, schema="s1", gold=gold, target=8, seed_key="q")
    assert padded == gold  # nothing could be added
    # The solver must flag this; the check is `len(tables) >= schema_tables or
    # tables == gold_only`, and it is the second clause that catches this end.
    assert padded == gold and len(padded) < 20


def test_gold_and_offered_table_sets_are_recorded_separately():
    """`oracle_gold_tables` must stay gold, or the licensing-recall check the
    docs prescribe is silently wrong on the padded arm -- the one arm where the
    offered set is deliberately not the gold set."""
    import inspect

    from governed_bi.eval import oracle

    src = inspect.getsource(oracle.oracle_solver)
    assert '"oracle_gold_tables": sorted(gold_only)' in src
    assert '"oracle_offered_tables": sorted(tables)' in src


# --------------------------------------------------------------------------- #
# The rungs' own routing provenance. Untested until an adversarial review found
# that oracle.py RE-DERIVED `routing_bypassed` from the absence of
# `routed_schemas` — so the moment `assemble` started stamping that field on the
# single-schema path (which is exactly what every rung creates), the flag flipped
# to False and the rung rejoined the routing denominator, scoring a trivially
# perfect recall off a schema it had been handed.
# --------------------------------------------------------------------------- #


def _oracle_meta(prov: dict, *, rung=OracleRung.schema):
    """Run a rung's meta construction over a canned serve provenance."""
    from types import SimpleNamespace

    import governed_bi.eval.oracle as oracle_mod
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
    from governed_bi.eval.oracle import GoldIndex, oracle_solver

    col = Column(
        physical_name="x",
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )
    corpus = Corpus(
        assets=[TableAsset(id="tbl_restaurant_t", schema="restaurant", physical_name="t", columns=[col])]
    ).for_analyst()
    gold = GoldIndex.build(
        [{"question": "q?", "question_id": "1", "db_id": "restaurant", "sql": "SELECT x FROM t"}]
    )
    answer = SimpleNamespace(
        sql="SELECT x FROM t",
        provenance=prov,
        tier=SimpleNamespace(value="governed"),
        semantic_assurance=SimpleNamespace(value="grounded"),
        safety_clearance=True,
    )
    graph = SimpleNamespace(invoke=lambda state, config=None: {"answer": answer})
    from governed_bi.config import Environment, Settings

    original = oracle_mod.build_serve_rails if hasattr(oracle_mod, "build_serve_rails") else None
    del original
    import governed_bi.analyst.agent as agent_mod

    saved = agent_mod.build_serve_rails
    agent_mod.build_serve_rails = lambda **kw: graph
    try:
        solver = oracle_solver(
            rung,
            corpus,
            gateway=None,
            settings=Settings.for_env(Environment.dev),
            identity=None,
            model=object(),
            gold=gold,
        )
        return solver.solve_with_meta("q?")[1]
    finally:
        agent_mod.build_serve_rails = saved


def test_a_rung_relays_the_serve_paths_bypass_flag_verbatim():
    meta = _oracle_meta(
        {
            "routed_schemas": ["restaurant"],
            "total_schemas": 1,
            "routing_bypassed": True,
        }
    )
    assert meta["routing_bypassed"] is True, (
        "the rung was handed its schema; scoring that as a routing success is the "
        "rung grading its own gift"
    )
    assert meta["oracle_rung"] == OracleRung.schema.value


def test_a_rung_does_not_claim_a_schema_pick_it_never_made():
    """Stamping `schema_pick` enrolled every oracle row in `schema_pick_accuracy` as
    a unanimous success of a picker that never ran."""
    meta = _oracle_meta(
        {"routed_schemas": ["restaurant"], "total_schemas": 1, "routing_bypassed": True}
    )
    assert meta.get("schema_pick") is None


def test_oracle_sql_needs_no_serve_path_and_still_reports_its_bypass():
    from governed_bi.eval.oracle import GoldIndex, OracleRung, oracle_solver
    from governed_bi.config import Environment, Settings
    from governed_bi.corpus import Corpus

    gold = GoldIndex.build(
        [{"question": "q?", "question_id": "1", "db_id": "restaurant", "sql": "SELECT 1"}]
    )
    solver = oracle_solver(
        OracleRung.sql,
        Corpus(assets=[]),
        gateway=None,
        settings=Settings.for_env(Environment.dev),
        identity=None,
        model=None,
        gold=gold,
    )
    sql, meta = solver.solve_with_meta("q?")
    assert sql == "SELECT 1"
    assert meta["routing_bypassed"] is True
    assert meta["oracle_applied"] is True


def test_the_oracle_graph_cache_is_bounded():
    """`oracle_tables` needs one graph per distinct gold table set — roughly one per
    question. Unbounded, a full benchmark holds thousands of compiled graphs, each
    closing over a corpus, a join graph, an allowlist and a retrieval index cache,
    for the life of the run."""
    import inspect

    from governed_bi.eval import oracle as oracle_mod

    src = inspect.getsource(oracle_mod.oracle_solver)
    assert "_GRAPH_CACHE_MAX" in src
    assert "popitem(last=False)" in src, "eviction must be LRU, oldest first"


def test_an_evicted_graph_is_rebuilt_with_a_fresh_session_id():
    """Session ids used to be keyed off cache SIZE, so an evicted-then-rebuilt graph
    reused an id another graph already held — two graphs' turns colliding on it."""
    import governed_bi.analyst.agent as agent_mod
    from types import SimpleNamespace

    from governed_bi.config import Environment, Settings
    from governed_bi.corpus import Corpus
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
    from governed_bi.eval.oracle import GoldIndex, OracleRung, oracle_solver

    seen_ids: list[str] = []
    saved = agent_mod.build_serve_rails

    def _fake(**kw):
        seen_ids.append(kw.get("session_id"))
        return SimpleNamespace(invoke=lambda *a, **k: {"answer": None})

    agent_mod.build_serve_rails = _fake
    try:
        col = Column(
            physical_name="x",
            physical_type="INTEGER",
            logical_type=LogicalType.integer,
            nullable=True,
            is_unique=False,
        )
        assets = [
            TableAsset(id=f"tbl_s_t{i}", schema="s", physical_name=f"t{i}", columns=[col])
            for i in range(40)
        ]
        corpus = Corpus(assets=assets).for_analyst()
        items = [
            {
                "question": f"q{i}?",
                "question_id": str(i),
                "db_id": "s",
                "sql": f"SELECT x FROM t{i}",
            }
            for i in range(40)
        ]
        solver = oracle_solver(
            OracleRung.tables,
            corpus,
            gateway=None,
            settings=Settings.for_env(Environment.dev),
            identity=None,
            model=object(),
            gold=GoldIndex.build(items),
        )
        for it in items:
            solver.solve_with_meta(it["question"])
    finally:
        agent_mod.build_serve_rails = saved

    assert len(seen_ids) == len(set(seen_ids)), (
        f"session ids collided across rebuilds: {len(seen_ids)} builds, "
        f"{len(set(seen_ids))} distinct ids"
    )


def test_oracle_sql_does_not_need_a_model_and_is_not_gated_behind_one():
    """It submits gold SQL to the grader — no model call, no retrieval, no agent loop.

    Gating it behind `lc_model is not None` made the one rung that costs nothing
    silently degrade to refuse-all under `--skip-agent`, reporting EX 0.000 for the
    grader ceiling. That is the number every other number is read against, and the
    runbook's first step is exactly this command.
    """
    from governed_bi.eval.run_datalake import plan_arm_serving

    # Asserted on the plan the driver applies, not on its source text: a reformat
    # broke the previous version of this and a rewrite defeated it.
    servable_offline = {
        rung: plan_arm_serving(
            rung=rung,
            source_arm="baseline",
            oracle_base="baseline",
            effective_workers=8,
            has_model=False,
        ).needs_factory
        for rung in OracleRung
    }
    assert servable_offline[OracleRung.sql] is True, (
        "oracle_sql must be reachable without a model — it is the grader ceiling"
    )


def test_the_other_rungs_still_require_a_model():
    """They serve through the real graph, so without a model they would be measuring
    a refusal, not a counterfactual."""
    from governed_bi.eval.oracle import OracleRung

    needs_model = [r for r in OracleRung if r is not OracleRung.sql]
    assert {r.value for r in needs_model} == {
        "oracle_schema",
        "oracle_tables",
        "oracle_tables_padded",
    }


def test_a_graph_serving_oracle_rung_relays_the_governance_stamp():
    """`oracle_schema` and friends serve through the real graph and hold a real `Answer`,
    and dropped `safety_clearance` / `graded_delivery` / `coverage_best_effort` on the way
    into the row. Every oracle row then recorded `None` for all three, so the summary's
    `n_*_observed` read 0 on an arm that delivered every row — making the governance rates
    unreadable on exactly the rungs whose purpose is to isolate where EX comes from.

    `oracle_sql` is the deliberate exception: it submits gold SQL with no model and no
    serve path, so there is no `Answer` and no guardrail decision to report.
    """
    meta = _oracle_meta(
        {"graded_delivery": True, "coverage_best_effort": True},
        rung=OracleRung.schema,
    )
    assert meta["safety_clearance"] is True
    assert meta["graded_delivery"] is True
    assert meta["coverage_best_effort"] is True


def test_the_governance_booleans_are_relayed_as_false_not_dropped():
    """Absent means "this was not a graded delivery" for these flags, so `False` is the
    right value — dropping them to `None` is what made the denominators read zero."""
    meta = _oracle_meta({}, rung=OracleRung.schema)
    assert meta["graded_delivery"] is False
    assert meta["coverage_best_effort"] is False
    assert meta["safety_clearance"] is True


def test_an_oracle_rung_never_stamps_a_schema_pick():
    """A rung is HANDED its schema; it does not pick one.

    ``oracle_sql`` stamped ``schema_pick = item["db_id"]`` — the answer key — so
    ``pick_hit`` was true on every row by construction and the run published
    ``schema_pick_accuracy: 1.0`` for a picker that never ran. It reached the ledger
    headline and printed in ``render_index``'s table beside the real arms. Confirmed
    on the whole-split ``--skip-agent`` ceiling: 2030 rows, ``n_pick_fallback: 0``.

    The graph-serving branch of the same function already states the rule in a
    comment; the ``sql`` branch did the opposite. ``routed_schemas`` carries the
    provenance and ``routing_bypassed`` keeps it out of the recall denominator, so
    nothing is lost by dropping it.
    """
    meta = _oracle_meta({}, rung=OracleRung.sql)
    assert "schema_pick" not in meta, meta
    assert meta["routed_schemas"] == ["restaurant"]
    assert meta["routing_bypassed"] is True

    # And no rung stamps one, so the rule holds for the ladder rather than one branch.
    for rung in OracleRung:
        assert "schema_pick" not in _oracle_meta({}, rung=rung), rung
