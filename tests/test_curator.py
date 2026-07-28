"""Tests for the curator authoring scaffold: proposer, adversary, loop.

Deterministic and offline. Fast unit cases build small ``TableAsset`` inputs
inline; two cases exercise the committed artefacts (the authored
``corpus/beer_factory`` tree and, when present, the vendored SQLite DB).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.corpus.schemas import (
    Audit,
    Column,
    ColumnRole,
    LogicalType,
    Provenance,
    ProvenanceSource,
    ProvenanceStatus,
    TableAsset,
)
from governed_bi.corpus.validate import Finding
from governed_bi.curator import (
    CurationResult,
    HeuristicProposer,
    Proposer,
    curate,
    profile_database,
    review,
)

EXAMPLE_CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "beer_factory"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


def _facts_column(name: str, logical: LogicalType, *, is_unique: bool, references=None) -> Column:
    """A Facts-only column (Inference tier empty), as the profiler emits."""
    return Column(
        physical_name=name,
        physical_type=logical.value.upper(),
        logical_type=logical,
        nullable=True,
        is_unique=is_unique,
        references=references,
    )


def _orders_table() -> TableAsset:
    """A Facts-only table: one unique *ID, one numeric non-key, one string."""
    return TableAsset(
        id="tbl_demo_orders",
        schema="demo",
        physical_name="orders",
        columns=[
            _facts_column("OrderID", LogicalType.integer, is_unique=True),
            _facts_column("amount", LogicalType.decimal, is_unique=False),
            _facts_column("note", LogicalType.string, is_unique=False),
        ],
    )


# --------------------------------------------------------------------------- #
# Proposer
# --------------------------------------------------------------------------- #


def test_heuristic_proposer_satisfies_protocol():
    assert isinstance(HeuristicProposer(), Proposer)


def test_heuristic_proposer_fills_roles_and_provenance():
    [table] = HeuristicProposer().propose([_orders_table()])

    by_name = {c.physical_name: c for c in table.columns}
    assert by_name["OrderID"].role is ColumnRole.primary_key  # unique *ID
    assert by_name["amount"].role is ColumnRole.measure  # numeric non-key
    assert by_name["note"].role is ColumnRole.dimension  # string

    for col in table.columns:
        assert col.description is None  # prose is the LLM proposer's job
        assert col.confidence == 0.5
        assert col.audit is not None
        assert col.audit.provenance.source is ProvenanceSource.curator
        assert col.audit.provenance.status is ProvenanceStatus.proposed

    # The table itself is stamped so it is a promotable proposed unit.
    assert table.audit is not None
    assert table.audit.provenance.status is ProvenanceStatus.proposed


def test_heuristic_proposer_marks_foreign_key_when_references_set():
    table = TableAsset(
        id="tbl_demo_lines",
        schema="demo",
        physical_name="lines",
        columns=[
            _facts_column(
                "CustomerID",
                LogicalType.integer,
                is_unique=False,
                references="col_demo_customers_CustomerID",
            )
        ],
    )
    [proposed] = HeuristicProposer().propose([table])
    assert proposed.columns[0].role is ColumnRole.foreign_key


def test_heuristic_proposer_sole_unique_non_id_is_primary_key():
    """A unique column that is the table's only unique one is a key even without
    an *ID name."""
    table = TableAsset(
        id="tbl_demo_codes",
        schema="demo",
        physical_name="codes",
        columns=[
            _facts_column("slug", LogicalType.string, is_unique=True),
            _facts_column("label", LogicalType.string, is_unique=False),
        ],
    )
    [proposed] = HeuristicProposer().propose([table])
    by_name = {c.physical_name: c for c in proposed.columns}
    assert by_name["slug"].role is ColumnRole.primary_key
    assert by_name["label"].role is ColumnRole.dimension


def test_heuristic_proposer_does_not_mutate_input():
    table = _orders_table()
    HeuristicProposer().propose([table])
    assert table.audit is None
    assert all(c.role is None and c.confidence is None for c in table.columns)


# --------------------------------------------------------------------------- #
# AssetBag.read_corpus
# --------------------------------------------------------------------------- #


def test_read_corpus_unknown_table_returns_error_not_raises():
    """Regression: the fix-pass agent calls ``read_corpus(table="restaurant")``
    using the *schema* name (the bag is keyed by physical table names), which
    used to raise ``KeyError`` before the unknown-table guard ran — crashing the
    whole curated_sme fix-pass. It must return a recoverable error string instead."""
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("restaurant", [_orders_table()])
    # ``restaurant`` is the schema, not a physical table (which is ``orders``).
    out = bag.read_corpus(table="restaurant")
    assert out.startswith("error: unknown table='restaurant'")
    assert "orders" in out  # lists known tables so the agent can self-correct
    # The valid table still renders.
    assert "[table] orders" in bag.read_corpus(table="orders")


def test_validate_flags_join_on_column_not_in_either_table():
    """Regression: join endpoint ids were checked, but the ``on`` SQL was not, so
    a typo'd/hallucinated column passed CI green and only mis-joined at serve time.
    validate_corpus now parses ``on`` and flags a column in neither joined table."""
    from governed_bi.corpus.schemas import JoinAsset
    from governed_bi.corpus.validate import validate_corpus

    customers = TableAsset(
        id="tbl_demo_customers",
        schema="demo",
        physical_name="customers",
        columns=[_facts_column("CustomerID", LogicalType.integer, is_unique=True)],
    )
    orders = TableAsset(
        id="tbl_demo_orders2",
        schema="demo",
        physical_name="orders",
        columns=[_facts_column("CustomerID", LogicalType.integer, is_unique=False)],
    )
    good = JoinAsset(
        id="join_demo_ok",
        left_table="tbl_demo_orders2",
        right_table="tbl_demo_customers",
        on="orders.CustomerID = customers.CustomerID",
    )
    assert validate_corpus([customers, orders, good]) == []  # resolves -> green

    bad = JoinAsset(
        id="join_demo_bad",
        left_table="tbl_demo_orders2",
        right_table="tbl_demo_customers",
        on="orders.CustomerID = customers.Ghost",  # Ghost is in neither table
    )
    findings = validate_corpus([customers, orders, bad])
    assert [f.code for f in findings] == ["join-on-unresolved"]
    assert "Ghost" in findings[0].message


def test_upsert_term_updates_in_place_when_name_is_existing_id():
    """Regression for the 6->12 doubling: a fix-pass that echoes a finding's
    asset_id as the term ``name`` must update that term in place, not mint a
    slugged duplicate (term_demo_x -> term_demo_term_demo_x)."""
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("demo", [_orders_table()])
    bag.upsert_term("revenue", binding_asset_type="column", binding_asset_id="orders.amount")
    assert set(bag.terms) == {"term_demo_revenue"}

    # Agent echoes the asset id as `name` while correcting the binding.
    msg = bag.upsert_term(
        "term_demo_revenue", binding_asset_type="column", binding_asset_id="orders.note"
    )
    assert msg == "ok: wrote term_demo_revenue"
    assert set(bag.terms) == {"term_demo_revenue"}  # no duplicate minted
    assert bag.terms["term_demo_revenue"].name == "revenue"  # display name preserved
    assert bag.terms["term_demo_revenue"].binding.asset_id == "col_demo_orders_note"


def test_upsert_term_coerces_and_rejects_column_binding():
    """Regression: the curator agent does not know the ``col_<table>_<column>``
    id derivation, so left to free text it wrote ``term.binding.asset_id`` as
    ``tbl_x.col`` / ``physical.col`` — a dangling reference that the (retired)
    stochastic fix-pass could not repair and doubled instead. ``upsert_term`` now
    coerces a resolvable ``table.column`` spelling to the canonical id and rejects
    an unresolvable one outright (never persisting a dangling binding)."""
    from governed_bi.corpus.validate import validate_corpus
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("demo", [_orders_table()])

    # physical 'table.column' is coerced to the loader-derived id.
    msg = bag.upsert_term(
        "amount", binding_asset_type="column", binding_asset_id="orders.amount"
    )
    assert msg.startswith("ok:")
    assert bag.terms["term_demo_amount"].binding.asset_id == "col_demo_orders_amount"

    # the '<table_id>.col' shape the agent produced in prod is coerced too.
    bag.upsert_term(
        "amt2", binding_asset_type="column", binding_asset_id="tbl_demo_orders.amount"
    )
    assert bag.terms["term_demo_amt2"].binding.asset_id == "col_demo_orders_amount"

    # an unresolvable binding is refused and NOT persisted.
    bad = bag.upsert_term(
        "ghost", binding_asset_type="column", binding_asset_id="orders.nope"
    )
    assert bad.startswith("error:")
    assert "term_demo_ghost" not in bag.terms

    assert validate_corpus(bag.all_assets()) == []


def test_repair_references_resolves_malformed_across_asset_types():
    """Deterministic reference repair: legacy malformed refs across every
    coercible type (term binding, metric.base_table, note.scope) are rewritten to
    canonical ids in place, so the fix-pass never hands a machine-fixable dangling
    ref to a stochastic agent."""
    from governed_bi.corpus.schemas import (
        MetricAsset,
        NoteAsset,
        TermAsset,
        TermBinding,
    )
    from governed_bi.corpus.validate import validate_corpus
    from governed_bi.curator.asset_bag import AssetBag

    bag = AssetBag.from_tables("demo", [_orders_table()])
    # term binding written as tbl_x.col (the old fix-pass shape)
    bag.terms["term_demo_total"] = TermAsset(
        id="term_demo_total",
        name="total",
        binding=TermBinding(asset_type="column", asset_id="tbl_demo_orders.amount"),
    )
    # metric.base_table written as the physical name instead of the table id
    bag.metrics["metric_demo_n"] = MetricAsset(
        id="metric_demo_n", name="n", base_table="orders", expression="count(*)"
    )
    # note.scope entry written as a physical column spelling
    bag.notes["note_demo_1"] = NoteAsset(
        id="note_demo_1",
        kind="context",
        summary="amount is net",
        scope=["orders.amount"],
    )
    assert len(validate_corpus(bag.all_assets())) == 3  # all three dangle

    assert bag.repair_references() == 3
    assert bag.terms["term_demo_total"].binding.asset_id == "col_demo_orders_amount"
    assert bag.metrics["metric_demo_n"].base_table == "tbl_demo_orders"
    assert bag.notes["note_demo_1"].scope == ["col_demo_orders_amount"]
    assert validate_corpus(bag.all_assets()) == []

    # Back-compat alias still works.
    assert bag.repair_term_bindings() == 0  # already canonical


# --------------------------------------------------------------------------- #
# Adversary
# --------------------------------------------------------------------------- #


def test_review_green_on_example_corpus():
    corpus = load_corpus(EXAMPLE_CORPUS.parent, schema=EXAMPLE_CORPUS.name)
    findings = review(corpus.assets)
    assert findings == [], "\n".join(str(f) for f in findings)


def test_review_flags_foreign_key_without_references():
    table = TableAsset(
        id="tbl_demo_bad",
        schema="demo",
        physical_name="bad",
        columns=[
            Column(
                physical_name="RefID",
                physical_type="INTEGER",
                logical_type=LogicalType.integer,
                nullable=True,
                is_unique=False,
                role=ColumnRole.foreign_key,  # claims FK but names no target
            )
        ],
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            )
        ),
    )
    findings = review([table])
    assert any(f.code == "fk-missing-ref" for f in findings)


def test_review_flags_missing_provenance():
    table = TableAsset(id="tbl_demo_noprov", schema="demo", physical_name="noprov")  # audit is None
    findings = review([table])
    assert any(f.code == "missing-provenance" for f in findings)


def test_soft_findings_do_not_gate_hard_findings_do():
    from governed_bi.curator.adversary import (
        StructuralGateError,
        gate_hard_findings,
        hard_findings,
    )

    soft_only = [
        Finding("missing-provenance", "tbl_x", "no audit"),
        Finding("fk-missing-ref", "tbl_x", "fk without ref"),
    ]
    assert hard_findings(soft_only) == []
    gate_hard_findings(soft_only)  # must not raise

    dangling = Finding(
        "dangling-ref",
        "metric_demo_ghost",
        "metric.base_table -> 'tbl_nope' does not resolve",
    )
    assert hard_findings([*soft_only, dangling]) == [dangling]
    with pytest.raises(StructuralGateError) as err:
        gate_hard_findings([*soft_only, dangling])
    assert err.value.findings == [dangling]


def test_adversary_signal_fails_closed_on_dangling_ref(tmp_path: Path):
    """C5: a bag with a dangling reference must not be writable — gate raises
    after recording findings, before any caller can ``bag.write``."""
    from governed_bi.corpus.schemas import MetricAsset
    from governed_bi.curator.adversary import StructuralGateError
    from governed_bi.curator.asset_bag import AssetBag
    from governed_bi.curator.pipeline import _run_adversary_signal

    bag = AssetBag.from_tables("demo", [_orders_table()])
    bag.metrics["metric_demo_ghost"] = MetricAsset(
        id="metric_demo_ghost",
        name="ghost",
        base_table="tbl_demo_does_not_exist",
        expression="count(*)",
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            )
        ),
    )
    out = tmp_path / "curated"
    out.mkdir()
    with pytest.raises(StructuralGateError) as err:
        _run_adversary_signal(bag, connector=None, out_root=out)
    assert any(f.code == "dangling-ref" for f in err.value.findings)
    assert (out / "adversary_findings.jsonl").exists()
    # Fail closed: nothing under the schema tree was written by the gate path.
    assert not (out / "demo").exists()
    bag.write(out)  # would succeed if called — the pipeline must not call it
    # Prove the bag still carries the dangling asset (gate did not mutate it away).
    from governed_bi.corpus.validate import validate_corpus

    assert any(f.code == "dangling-ref" for f in validate_corpus(bag.all_assets()))


def test_write_tools_do_not_expose_certified_params():
    """C6: model-facing tool schemas must not accept certified / answered_by."""
    import inspect

    pytest.importorskip("deepagents")
    from governed_bi.curator.asset_bag import AssetBag
    from governed_bi.curator.deep_agent import curator_tools

    bag = AssetBag.from_tables("demo", [_orders_table()])
    tools = curator_tools(connector=None, schema="demo", bag=bag)  # type: ignore[arg-type]
    write_names = {
        "upsert_join",
        "upsert_metric",
        "upsert_term",
        "upsert_few_shot",
        "annotate_table",
        "annotate_column",
    }
    for tool in tools:
        if tool.__name__ not in write_names:
            continue
        params = inspect.signature(tool).parameters
        assert "certified" not in params, tool.__name__
        assert "answered_by" not in params, tool.__name__


def test_tool_writes_never_stamp_human_certified():
    """C6: even if bag.upsert still accepts certified=True internally, the
    model-facing tools force certified=False."""
    pytest.importorskip("deepagents")
    from governed_bi.curator.asset_bag import AssetBag
    from governed_bi.curator.deep_agent import curator_tools

    bag = AssetBag.from_tables("demo", [_orders_table()])
    tools = {t.__name__: t for t in curator_tools(connector=None, schema="demo", bag=bag)}  # type: ignore[arg-type]
    msg = tools["annotate_table"]("orders", description="Orders header", confidence=0.9)
    assert msg.startswith("ok:")
    table = bag.tables["orders"]
    assert table.audit is not None
    assert table.audit.provenance.source is ProvenanceSource.curator
    assert table.audit.provenance.status is ProvenanceStatus.proposed

    # Internal non-agent path may still certify (deterministic SME fold).
    bag.annotate_table(
        "orders", description="Orders header (SME)", confidence=0.9, certified=True, answered_by="sme"
    )
    assert bag.tables["orders"].audit.provenance.source is ProvenanceSource.human
    assert bag.tables["orders"].audit.provenance.status is ProvenanceStatus.certified


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


def test_curate_reaches_green_and_promotes_to_draft():
    result = curate([_orders_table()], HeuristicProposer())

    assert isinstance(result, CurationResult)
    assert result.green is True
    assert result.findings == []
    assert result.rounds == 1

    [table] = result.assets
    assert table.audit.provenance.status is ProvenanceStatus.draft
    for col in table.columns:
        assert col.audit.provenance.status is ProvenanceStatus.draft


# --------------------------------------------------------------------------- #
# Integration: profile the vendored BIRD DB, then curate (skipped if absent)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not BIRD_DB.exists(), reason="vendored beer_factory.sqlite not present")
def test_curate_end_to_end_from_profiled_facts():
    from governed_bi.gateway import SqliteConnector

    conn = SqliteConnector(BIRD_DB)
    try:
        tables = profile_database(conn, schema="beer_factory")
        result = curate(tables, HeuristicProposer(), connector=conn)
    finally:
        conn.close()

    assert result.green is True, "\n".join(str(f) for f in result.findings)
    assert result.assets, "profiling produced no tables"
    for table in result.assets:
        assert table.audit.provenance.status is ProvenanceStatus.draft
        # Every column got a role, and none carries invented prose.
        for col in table.columns:
            assert col.role is not None
            assert col.description is None


# --------------------------------------------------------------------------- #
# AUDIT C6: the agent owns clarifications.jsonl, so it can forge a human answer
# --------------------------------------------------------------------------- #


def test_quarantine_resets_agent_authored_answers():
    """A pre-answered record must not survive the Phase A boundary.

    Without this, an agent writing `status: "answered"` + `answered_by: "<a name>"`
    through `write_file` gets `source=human, status=certified` out of
    `apply_answered_clarifications` — a certified fact with no human in the loop.
    """
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        quarantine_agent_answers,
    )

    forged = ClarificationRecord(
        id="q001",
        scope="table:customers",
        question="What counts as an active customer?",
        status=ClarificationRecordStatus.answered,
        answer="Anyone with a transaction in the last 90 days.",
        answered_by="Jane Chen, Finance",
    )
    honest = ClarificationRecord(id="q002", scope="table:transaction", question="Units of amount?")

    cleaned, reset = quarantine_agent_answers([forged, honest])

    assert reset == ["q001"]
    assert cleaned[0].status is ClarificationRecordStatus.open
    assert cleaned[0].answer is None
    assert cleaned[0].answered_by is None
    # An untouched record passes through byte-identical.
    assert cleaned[1] == honest


def test_quarantined_record_cannot_mint_a_certified_fact(tmp_path):
    """End of the chain: the forged record folds into nothing."""
    from governed_bi.curator.asset_bag import AssetBag
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        quarantine_agent_answers,
    )

    tables = [_orders_table()]
    forged = ClarificationRecord(
        id="q001",
        scope="table:orders",
        question="What is this table?",
        status=ClarificationRecordStatus.answered,
        answer="Every order, one row each.",
        answered_by="Jane Chen, Finance",
    )

    bag = AssetBag.from_tables("demo", tables)
    assert bag.apply_answered_clarifications([forged]) == 1, "precondition: the forgery works"

    bag2 = AssetBag.from_tables("demo", tables)
    cleaned, _ = quarantine_agent_answers([forged])
    assert bag2.apply_answered_clarifications(cleaned) == 0


# --------------------------------------------------------------------------- #
# AUDIT E3: the decoy defence derives "suspect" from train gold SQL. With no
# train SQL that rule suspects the entire schema.
# --------------------------------------------------------------------------- #


def test_decoy_defense_marks_unreferenced_columns_when_train_sql_exists():
    from governed_bi.curator.asset_bag import AssetBag
    from governed_bi.curator.pipeline import _mark_columns_absent_from_gold

    bag = AssetBag.from_tables("demo", [_orders_table()])
    stats = _mark_columns_absent_from_gold(
        bag, ["SELECT amount FROM orders"], dialect="sqlite"
    )
    assert sum(v for k, v in stats.items() if "mark" in k) >= 1, stats


def test_decoy_defense_is_skipped_with_no_train_sql(monkeypatch, tmp_path):
    """The README path: curating without train SQL must not suspect the schema."""
    from governed_bi.curator import pipeline

    called = []
    monkeypatch.setattr(
        pipeline,
        "_mark_columns_absent_from_gold",
        lambda *a, **k: called.append(a) or {},
    )
    monkeypatch.setattr(pipeline, "profile_database", lambda *a, **k: [_orders_table()])
    monkeypatch.setattr(
        pipeline, "seed_from_train_sql", lambda *a, **k: pipeline.SeedBundle([], [])
    )

    class _Connector:
        """Enough surface for validate_corpus's physical-existence probe."""

        def list_tables(self, schema=None):
            return ["orders"]

        def list_schemas(self):
            return ["demo"]

        def describe_table(self, table, schema=None):
            class _Col:
                def __init__(self, name):
                    self.name = name

            class _Info:
                columns = [_Col("OrderID"), _Col("amount"), _Col("note")]

            return _Info()

    pipeline.build_curated_corpus(
        _Connector(),
        object(),  # gateway (unused without a model)
        "demo",
        [],  # train_items: the README path
        tmp_path,
        model=None,
        dialect="sqlite",
    )
    assert called == [], "the decoy heuristic ran with nothing to derive suspicion from"
