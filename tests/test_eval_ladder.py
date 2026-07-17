"""Offline tests for eval-ladder pieces (baseline, seed, bag, SME, pipeline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.curator.asset_bag import AssetBag
from governed_bi.curator.clarifications import (
    ClarificationRecord,
    StaticResponder,
    write_clarifications,
)
from governed_bi.curator.pipeline import (
    build_baseline_corpus,
    build_curated_corpus,
    build_curated_corpus_with_sme,
)
from governed_bi.curator.profile import profile_database
from governed_bi.curator.seed import extract_joins_from_sql, seed_from_train_sql
from governed_bi.curator.sme import assert_brief_no_leakage, build_sme_brief
from governed_bi.eval.dataset import EvalItem
from governed_bi.gateway import Gateway, SqliteConnector
from governed_bi.llm import StaticChatClient

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


def test_validate_corpora_gate_counts_findings_per_arm():
    """The CI-green gate must surface a per-arm finding count so a corpus with a
    reference-integrity defect can never be scored silently (the exact hole that
    let dangling term bindings ride into a scored arm)."""
    from types import SimpleNamespace

    from governed_bi.corpus.schemas import LogicalType, TableAsset, TermAsset, TermBinding
    from governed_bi.eval.run_experiment import _validate_corpora

    def _tbl() -> TableAsset:
        from governed_bi.corpus.schemas import Column

        return TableAsset(
            id="tbl_demo_orders",
            schema="demo",
            physical_name="orders",
            columns=[
                Column(
                    physical_name="amount",
                    physical_type="DECIMAL",
                    logical_type=LogicalType.decimal,
                    nullable=True,
                    is_unique=False,
                )
            ],
        )

    clean = SimpleNamespace(assets=[_tbl()])
    dangling = SimpleNamespace(
        assets=[
            _tbl(),
            TermAsset(
                id="term_demo_x",
                name="x",
                binding=TermBinding(asset_type="column", asset_id="col_does_not_exist"),
            ),
        ]
    )
    out = _validate_corpora({"baseline": clean, "curated": dangling})
    assert out["baseline"]["finding_count"] == 0
    assert out["curated"]["finding_count"] == 1
    assert "dangling-ref" in out["curated"]["findings"][0]


def test_collect_curator_errors_lifts_swallowed_failures(tmp_path):
    """A fix-pass crash is swallowed into the per-corpus manifest; the collector
    lifts its short form into the headline so it is not silently lost."""
    import json

    from governed_bi.eval.run_experiment import _collect_curator_errors

    clean_dir = tmp_path / "corpus_curated"
    crashed_dir = tmp_path / "corpus_curated_sme"
    clean_dir.mkdir()
    crashed_dir.mkdir()
    (clean_dir / "run_manifest.json").write_text(
        json.dumps({"error": None, "fix_pass_error": None}), encoding="utf-8"
    )
    (crashed_dir / "run_manifest.json").write_text(
        json.dumps({"error": None, "fix_pass_error": "KeyError: 'x'\n  File ...\n  ..."}),
        encoding="utf-8",
    )
    out = _collect_curator_errors({"curated": clean_dir, "curated_sme": crashed_dir})
    assert "curated" not in out  # no error -> not surfaced
    assert out["curated_sme"]["fix_pass_error"] == "KeyError: 'x'"  # first line only


@pytest.fixture
def bird_connector():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield conn
    conn.close()


def test_seed_extracts_join_from_sql_rename_style():
    """BIRD-style aliased JOINs must resolve to physical table names (not T1/T2)."""
    sql = (
        'SELECT "T1"."salary" FROM "cs_semester"."RA" AS "T1" '
        'INNER JOIN "cs_semester"."student" AS "T2" '
        'ON "T1"."student_id" = "T2"."student_id"'
    )
    joins = extract_joins_from_sql(sql, dialect="postgres")
    assert len(joins) == 1
    j = joins[0]
    assert j.left_table == "RA"
    assert j.right_table == "student"
    assert j.on == "RA.student_id = student.student_id"
    # Aliases must never leak into the candidate — AssetBag would reject them.
    assert "T1" not in j.left_table and "T2" not in j.right_table
    assert "T1" not in j.on and "T2" not in j.on


def test_seed_aliased_join_applies_to_asset_bag(bird_connector, tmp_path: Path):
    """End-to-end: resolved seed joins must successfully propose_join."""
    from governed_bi.curator.pipeline import _apply_seed

    sql = (
        'SELECT COUNT(*) FROM customers AS "T1" '
        'INNER JOIN "transaction" AS "T2" '
        'ON "T1"."CustomerID" = "T2"."CustomerID"'
    )
    tables = profile_database(bird_connector, schema="beer_factory")
    bag = AssetBag.from_tables("beer_factory", tables)
    seed = seed_from_train_sql([sql], dialect="sqlite")
    assert seed.joins
    assert seed.joins[0].left_table == "customers"
    assert seed.joins[0].right_table == "transaction"
    stats = _apply_seed(bag, seed)
    assert stats["joins_ok"] >= 1
    assert stats["joins_fail"] == 0


def test_adversary_signal_writes_findings(bird_connector, tmp_path: Path):
    from governed_bi.curator.pipeline import _run_adversary_signal

    tables = profile_database(bird_connector, schema="beer_factory")
    bag = AssetBag.from_tables("beer_factory", tables)
    out = tmp_path / "curated"
    out.mkdir()
    findings = _run_adversary_signal(bag, connector=bird_connector, out_root=out)
    assert (out / "adversary_findings.jsonl").exists()
    assert isinstance(findings, list)


def test_sme_sanitizes_sql_in_answers():
    from governed_bi.curator.sme import SimulatedSme, _sanitize_sme_answer

    assert "SELECT" not in _sanitize_sme_answer(
        "Looks reliable.\nSELECT * FROM decoy"
    ).upper()
    chat = StaticChatClient(responses="Mean student id.\n```sql\nSELECT 1\n```")
    sme = SimulatedSme(chat, "brief")
    ans = sme.answer("What is student_id?")
    assert "SELECT" not in ans.upper()
    assert "student" in ans.lower() or "Unsure" in ans



def test_seed_bundle_dedupes():
    sql = 'SELECT SUM(x) FROM t JOIN u ON t.id = u.id'
    bundle = seed_from_train_sql([sql, sql], dialect="postgres")
    assert len(bundle.joins) == 1
    assert bundle.metrics  # SUM(x)


def test_asset_bag_propose_join_and_suspect(bird_connector, tmp_path: Path):
    tables = profile_database(bird_connector, schema="beer_factory")
    bag = AssetBag.from_tables("beer_factory", tables)
    # Pick two real tables if present.
    names = list(bag.tables)
    assert len(names) >= 2
    left, right = names[0], names[1]
    msg = bag.propose_join(left, right, f"{left}.id = {right}.id")
    assert msg.startswith("ok:")
    col = bag.tables[left].columns[0].physical_name
    assert bag.mark_column_suspect(left, col).startswith("ok:")
    assert bag.suspect_count() >= 1
    written = bag.write(tmp_path)
    assert written
    assert (tmp_path / "beer_factory" / "joins").exists()


def test_build_baseline_corpus_is_deterministic_db_derivable(bird_connector, tmp_path: Path):
    """The baseline arm (D5): no curator LLM, no train-SQL seeding — just Facts
    (names/types/sample values) plus naming-convention FK candidates."""
    import json

    from governed_bi.corpus import load_corpus

    root = build_baseline_corpus(bird_connector, "beer_factory", tmp_path / "corpus_baseline")

    assert (root / "beer_factory" / "tables").exists()
    corpus = load_corpus(root, schema="beer_factory")
    tables = [a for a in corpus.assets if a.asset_type == "table"]
    assert tables
    for t in tables:
        assert t.description is None  # Inference tier untouched: no LLM ran
        for c in t.columns:
            assert c.description is None

    # transaction.CustomerID -> customers.CustomerID is derivable from column
    # naming alone (no train SQL involved).
    joins = [a for a in corpus.assets if a.asset_type == "join"]
    assert any("transaction" in j.on and "customers" in j.on for j in joins)

    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == "baseline"
    assert manifest["fk_candidates"]["fk_candidates_ok"] >= 1


def test_sme_brief_leakage_guard(tmp_path: Path):
    desc = tmp_path / "database_description"
    desc.mkdir()
    (desc / "student.csv").write_text(
        "original_column_name,column_name,column_description,data_format,value_description\n"
        "student_id,student_id,Unique student id,integer,\n",
        encoding="utf-8",
    )
    train = [
        EvalItem(
            question="What is the average RA salary?",
            sql='SELECT AVG("salary") FROM "cs_semester"."RA"',
            question_id="train_1",
            evidence="RA means research assistant",
        )
    ]
    brief = build_sme_brief(desc, train)
    assert "student_id" in brief
    assert "average RA salary" in brief
    assert_brief_no_leakage(
        brief,
        gold_sqls=[train[0].sql],
        test_questions=["Held-out test question that must not appear"],
    )
    with pytest.raises(AssertionError, match="SELECT"):
        assert_brief_no_leakage("bad SELECT 1", gold_sqls=[], test_questions=[])


def test_build_curated_corpus_seed_only(bird_connector, tmp_path: Path):
    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql='SELECT COUNT(*) FROM customers JOIN "transaction" ON customers.CustomerID = "transaction".CustomerID',
            question_id="t1",
            evidence="",
        )
    ]
    root = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        model=None,
        dialect="sqlite",
        run_agent=False,
    )
    assert (root / "beer_factory" / "tables").exists()
    joins_dir = root / "beer_factory" / "joins"
    assert joins_dir.exists() and any(joins_dir.iterdir())
    assert (root / "run_manifest.json").exists()
    # Agent-authored ledger is not pre-created; seed-only leaves it missing.
    assert not (root / "clarifications.jsonl").exists()


def test_build_curated_corpus_with_sme_folds_human(bird_connector, tmp_path: Path):
    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    curated = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        run_agent=False,
        dialect="sqlite",
    )
    # Plant an agent-style ledger (offline path does not invent questions).
    write_clarifications(
        curated / "clarifications.jsonl",
        [
            ClarificationRecord(
                id="q001",
                scope="table:customers",
                question="Who are the customers?",
                raised_by=["t1"],
            )
        ],
    )
    responder = StaticResponder(default="Customers who bought root beer.")
    curated_sme = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated_sme",
        responder=responder,
        curated_root=curated,
        model=None,
        seed_ledger_if_empty=False,
    )
    # At least one table/column should carry human provenance after resolve.
    from governed_bi.corpus import load_corpus
    from governed_bi.corpus.schemas import ProvenanceSource

    corpus = load_corpus(curated_sme, schema="beer_factory")
    human = False
    for asset in corpus.tables():
        if asset.audit and asset.audit.provenance.source is ProvenanceSource.human:
            human = True
        for col in asset.columns:
            if col.audit and col.audit.provenance.source is ProvenanceSource.human:
                human = True
    assert human

    import json

    manifest = json.loads((curated_sme / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fold_mode"] == "deterministic"
    assert manifest["ledger_source"] == "agent"
    assert manifest["agent_ran"] is False


def test_deep_agent_invoke_receives_tracing_callbacks(bird_connector, tmp_path: Path, monkeypatch):
    """The curator deep agent must run with Langfuse callbacks in its config, or
    its (majority) LLM volume is invisible to the dashboard. Regression guard."""
    from governed_bi.curator import deep_agent as da_mod
    from governed_bi.curator import pipeline as pipe_mod

    class _RecordingAgent:
        def __init__(self):
            self.configs: list = []

        def invoke(self, payload, config=None):
            self.configs.append(config)
            return {}

    rec = _RecordingAgent()
    monkeypatch.setattr(da_mod, "build_curator_agent", lambda *a, **k: rec)
    monkeypatch.setattr(pipe_mod, "tracing_callbacks", lambda: ["LF_SENTINEL"])

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(question="How many customers?", sql="SELECT COUNT(*) FROM customers", question_id="t1")
    ]
    pipe_mod.build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        run_agent=True,
        model=object(),
        dialect="sqlite",
    )

    assert rec.configs, "deep agent was never invoked"
    assert rec.configs[0].get("callbacks") == ["LF_SENTINEL"], (
        f"tracing callbacks not threaded into agent.invoke config: {rec.configs[0]}"
    )


def test_sme_clarifications_logged(bird_connector, tmp_path: Path):
    import json

    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    curated = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated",
        run_agent=False,
        dialect="sqlite",
    )
    write_clarifications(
        curated / "clarifications.jsonl",
        [
            ClarificationRecord(
                id="q001",
                scope="table:customers",
                question="Who are the customers?",
                raised_by=["t1"],
            )
        ],
    )
    responder = StaticResponder(default="Customers who bought root beer.")
    curated_sme = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_curated_sme",
        responder=responder,
        curated_root=curated,
        model=None,
    )

    log = curated_sme / "sme_clarifications.jsonl"
    assert log.exists(), "sme_clarifications.jsonl was not written"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows, "expected at least one logged clarification"

    expected_keys = {
        "schema", "table_id", "table", "column", "question",
        "answer", "answered_by", "asked_by", "status", "at",
    }
    for r in rows:
        assert expected_keys <= set(r), f"missing keys in {r}"
        assert r["table_id"], f"table_id should resolve for scope {r.get('scope')}"

    answered = [r for r in rows if r["status"] == "answered"]
    assert answered, "expected at least one answered clarification"
    assert all(r["question"] for r in answered), "every answered row must record the question"
    # The verbatim SME answer is captured (this is what makes leakage auditable).
    assert any("root beer" in (r["answer"] or "") for r in answered)
    assert all(r["answered_by"] for r in answered)
