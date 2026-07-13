"""Offline tests for three-arm experiment pieces (baseline, seed, bag, SME, pipeline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.curator.asset_bag import AssetBag
from governed_bi.curator.pipeline import build_curated_corpus, build_curated_corpus_with_sme
from governed_bi.curator.profile import profile_database
from governed_bi.curator.seed import extract_joins_from_sql, seed_from_train_sql
from governed_bi.curator.sme import assert_brief_no_leakage, build_sme_brief
from governed_bi.curator.clarify_loop import StaticResponder
from governed_bi.eval.baseline_solver import no_layer_solver
from governed_bi.eval.dataset import EvalItem
from governed_bi.gateway import Gateway, SqliteConnector
from governed_bi.llm import StaticChatClient

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


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
    out = tmp_path / "a2"
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


def test_no_layer_solver_returns_sql(bird_connector):
    gateway = Gateway(bird_connector)
    chat = StaticChatClient(responses='SELECT COUNT(*) FROM "customers"')
    solver = no_layer_solver(
        bird_connector, gateway, chat, schema="beer_factory", dialect="sqlite"
    )
    sql = solver.solve("How many customers?")
    assert sql is not None
    assert "SELECT" in sql.upper()


def test_no_layer_solver_refuses(bird_connector):
    gateway = Gateway(bird_connector)
    chat = StaticChatClient(responses="CANNOT_ANSWER")
    solver = no_layer_solver(
        bird_connector, gateway, chat, schema="beer_factory", dialect="sqlite"
    )
    assert solver.solve("anything") is None


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
        tmp_path / "corpus_a2",
        model=None,
        dialect="sqlite",
        run_agent=False,
    )
    assert (root / "beer_factory" / "tables").exists()
    joins_dir = root / "beer_factory" / "joins"
    assert joins_dir.exists() and any(joins_dir.iterdir())


def test_build_curated_corpus_with_sme_folds_human(bird_connector, tmp_path: Path):
    gateway = Gateway(bird_connector)
    train = [
        EvalItem(
            question="How many customers?",
            sql="SELECT COUNT(*) FROM customers",
            question_id="t1",
        )
    ]
    a2 = build_curated_corpus(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a2",
        run_agent=False,
        dialect="sqlite",
    )
    responder = StaticResponder(default="Customers who bought root beer.")
    a3 = build_curated_corpus_with_sme(
        bird_connector,
        gateway,
        "beer_factory",
        train,
        tmp_path / "corpus_a3",
        responder=responder,
        a2_root=a2,
        model=None,
    )
    # At least one table/column should carry human provenance after resolve.
    from governed_bi.corpus import load_corpus
    from governed_bi.corpus.schemas import ProvenanceSource

    corpus = load_corpus(a3, schema="beer_factory")
    human = False
    for asset in corpus.tables():
        if asset.audit and asset.audit.provenance.source is ProvenanceSource.human:
            human = True
        for col in asset.columns:
            if col.audit and col.audit.provenance.source is ProvenanceSource.human:
                human = True
    assert human
