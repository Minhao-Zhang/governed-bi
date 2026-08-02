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
    from governed_bi.eval.harness import _validate_corpora

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

    from governed_bi.eval.harness import _collect_curator_errors

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


def test_adversary_signal_blocks_dangling_ref_before_write(bird_connector, tmp_path: Path):
    """C5: injecting a dangling metric must raise and leave no corpus YAML."""
    from governed_bi.corpus.schemas import (
        Audit,
        MetricAsset,
        Provenance,
        ProvenanceSource,
        ProvenanceStatus,
    )
    from governed_bi.curator.adversary import StructuralGateError
    from governed_bi.curator.pipeline import _run_adversary_signal

    tables = profile_database(bird_connector, schema="beer_factory")
    bag = AssetBag.from_tables("beer_factory", tables)
    bag.metrics["metric_beer_factory_ghost"] = MetricAsset(
        id="metric_beer_factory_ghost",
        name="ghost",
        base_table="tbl_beer_factory_does_not_exist",
        expression="count(*)",
        audit=Audit(
            provenance=Provenance(
                source=ProvenanceSource.curator, status=ProvenanceStatus.proposed
            )
        ),
    )
    out = tmp_path / "curated"
    out.mkdir()
    with pytest.raises(StructuralGateError):
        _run_adversary_signal(bag, connector=bird_connector, out_root=out)
    assert (out / "adversary_findings.jsonl").exists()
    assert list(out.rglob("*.yaml")) == []


def test_sme_sanitizes_sql_in_answers(tmp_path: Path):
    from dataclasses import replace

    from governed_bi.config import Environment, Settings
    from governed_bi.curator.sme import SimulatedSme, _sanitize_sme_answer

    assert "SELECT" not in _sanitize_sme_answer(
        "Looks reliable.\nSELECT * FROM decoy"
    ).upper()
    chat = StaticChatClient(responses="Mean student id.\n```sql\nSELECT 1\n```")
    # Isolate audit writes (AUDIT T6) — answer() emits a producer:sme run record.
    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    sme = SimulatedSme(chat, "brief", settings=settings)
    ans = sme.answer("What is student_id?")
    assert "SELECT" not in ans.upper()
    assert "student" in ans.lower() or "Unsure" in ans


def test_sme_sanitizer_keeps_the_prose_when_the_answer_leads_with_sql():
    """The regression that cost 11 of 381 measured answers.

    The sanitiser used to keep only the lines *before* the first keyword-bearing
    line, so an answer that opened with the query it had just probed with sanitised
    to the empty string and fell through to the canned "Unsure" fallback. That is
    the shape a model produces when it is told to check the data first, and it hit
    the decoy-confirmation questions hardest — precisely the ones the curator needed
    answered. Recorded clarifications are stored post-sanitisation, so raw model
    output cannot be replayed and this unit test is the only offline proof.
    """
    from governed_bi.curator.sme import _sanitize_sme_answer

    leading = _sanitize_sme_answer(
        "SELECT COUNT(*) FROM card_collections\nWHERE id IS NOT NULL;\n"
        "card_collections is a redundant duplicate of sets — do not use it."
    )
    assert "card_collections is a redundant duplicate" in leading
    assert "SELECT" not in leading.upper() and "WHERE" not in leading.upper()

    # Prose on both sides of an unfenced multi-line query survives; the wrapped
    # continuation lines (FROM / JOIN / ORDER BY) go with the keyword line.
    sandwiched = _sanitize_sme_answer(
        "I checked the data:\n"
        "select a.x, b.y\n"
        "  from a\n"
        "  join b on a.id = b.id\n"
        "order by a.x\n"
        "Every row is null, so the column is useless for analysis."
    )
    assert sandwiched.splitlines() == [
        "I checked the data:",
        "Every row is null, so the column is useless for analysis.",
    ]

    # Only when nothing at all survives may the canned string appear.
    assert _sanitize_sme_answer("```sql\nselect 1\n```").startswith("Unsure")



def test_seed_bundle_dedupes():
    sql = 'SELECT SUM(x) FROM t JOIN u ON t.id = u.id'
    bundle = seed_from_train_sql([sql, sql], dialect="postgres")
    assert len(bundle.joins) == 1
    assert bundle.metrics  # SUM(x)


def _two_table_bag() -> AssetBag:
    """Two tables sharing every column name, so a misattributed qualifier shows up."""
    from governed_bi.corpus.schemas import Column, LogicalType, TableAsset

    def _col(name: str) -> Column:
        return Column(
            physical_name=name,
            physical_type="INTEGER",
            logical_type=LogicalType.integer,
            nullable=True,
            is_unique=False,
        )

    return AssetBag.from_tables(
        "demo",
        [
            TableAsset(
                id=f"tbl_demo_{name}",
                schema="demo",
                physical_name=name,
                columns=[_col("a"), _col("b"), _col("decoy")],
            )
            for name in ("tbl_x", "tbl_y")
        ],
    )


def _suspect_names(bag: AssetBag) -> set[str]:
    from governed_bi.corpus.schemas import ReliabilityStatus

    return {
        f"{t.physical_name}.{c.physical_name}"
        for t in bag.tables.values()
        for c in t.columns
        if c.reliability.status is ReliabilityStatus.suspect
    }


# --------------------------------------------------------------------------- #
# Reliability is AI-authored (B6). `_mark_columns_absent_from_gold` used to stamp
# every column train gold never referenced as suspect, and five tests here pinned
# its alias resolution. It is deleted: "BIRD never queried this column" is not
# evidence the column is unreliable, and where the gold SQL was defective the mask
# banned columns the generator needed. What replaces those tests is the boundary
# the ladder now depends on — the mechanical rungs author no reliability at all,
# and the two authored paths (the agent's tool, the SME's answer) do.
# --------------------------------------------------------------------------- #


def test_the_mechanical_build_authors_no_suspect_columns():
    """The `seeded` rung's attribution. Seeding derives joins and metrics from train
    gold SQL and nothing else; if it ever marks a column suspect again, the ladder
    stops separating "metadata the machine derived" from "reliability a model judged"
    and the curated arm's decoy numbers become unattributable."""
    import inspect

    from governed_bi.curator import pipeline

    bag = _two_table_bag()
    stats = pipeline._apply_seed(
        bag, seed_from_train_sql(["SELECT a FROM tbl_x"], dialect="postgres")
    )
    assert stats["joins_fail"] == 0
    assert _suspect_names(bag) == set()

    mechanical = inspect.getsource(pipeline.build_curated_corpus).split(
        "if run_agent and model is not None:"
    )[0]
    assert "seed_from_train_sql" in mechanical
    for banned in ("mark_column_suspect", "suspect=True", "absent_from_gold("):
        assert banned not in mechanical, (
            f"the non-agent build path writes reliability again ({banned}); B6 moved "
            "that judgement to the curator agent and the SME fold"
        )


def test_the_curator_agent_can_mark_suspect_but_cannot_exclude():
    """The governance boundary, enforced by absence. Suspect argues against a column
    and the analyst still sees it; `governance.excluded` removes it and is a human
    decision. The agent has a tool for the first and none for the second."""
    import io
    import tokenize
    from pathlib import Path as _Path

    import governed_bi.curator as curator_pkg
    from governed_bi.curator.deep_agent import curator_tools

    bag = _two_table_bag()
    names = {t.__name__ for t in curator_tools(None, "demo", bag=bag)}
    assert "annotate_column" in names
    assert not {n for n in names if "exclu" in n.lower()}, names

    # Code only: comments and docstrings under curator/ discuss the boundary at
    # length, and the point is that no statement crosses it.
    for path in sorted(_Path(curator_pkg.__file__).parent.glob("*.py")):
        code = [
            tok.string
            for tok in tokenize.generate_tokens(
                io.StringIO(path.read_text(encoding="utf-8")).readline
            )
            if tok.type not in (tokenize.COMMENT, tokenize.STRING)
        ]
        assert "excluded" not in "".join(code), (
            f"{path.name} now touches governance.excluded; that field is human-only "
            "and is enforced by there being no way for the agent to reach it"
        )


def test_an_sme_who_disowns_a_column_marks_it_suspect_not_just_described():
    """The signal the SME brief exists to produce. `reliability.status` is the field
    serve reads to steer generation off a column, so an answer that says "I have never
    heard of this column" has to land there and not only in prose."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
    )

    answer = (
        "I do not recognise `decoy` — it is not part of the documented schema I know, "
        "and I would not rely on it for analysis. Use `a` instead."
    )
    rec = ClarificationRecord(
        id="q001",
        scope="table:tbl_x.decoy",
        question="What does decoy hold?",
        status=ClarificationRecordStatus.answered,
        answer=answer,
        answered_by="sme",
    )
    bag = _two_table_bag()
    assert bag.apply_answered_clarifications([rec]) == 1
    assert _suspect_names(bag) == set(), "precondition: the fold alone only describes"

    stats = bag.mark_unrecognised_columns([rec])
    assert stats == {"marked": 1, "no_column_in_scope": 0, "unknown_column": 0}
    assert _suspect_names(bag) == {"tbl_x.decoy"}
    col = next(c for c in bag.tables["tbl_x"].columns if c.physical_name == "decoy")
    assert col.reliability.note.startswith("DO NOT USE — ")
    assert col.description == answer  # the fold's prose is not overwritten


def test_an_ordinary_sme_answer_leaves_reliability_alone():
    """The false-positive guard. Most answers are definitions, and a definition that
    happens to discuss reliability in the positive must not hard-block a column."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
    )

    rec = ClarificationRecord(
        id="q002",
        scope="table:tbl_x.a",
        question="What does a hold?",
        status=ClarificationRecordStatus.answered,
        answer="`a` is the account balance in cents. It is populated and reliable.",
        answered_by="sme",
    )
    bag = _two_table_bag()
    stats = bag.mark_unrecognised_columns([rec])
    assert stats["marked"] == 0
    assert _suspect_names(bag) == set()


def test_a_disowned_column_with_no_column_in_scope_is_counted_not_dropped():
    """The known gap, reported rather than hidden. `parse_scope` yields a column only
    for `table:Name.col`; a `pair:`- or `table:`-scoped question about one column has
    nowhere to put the mark, so the answer stays a note and the count says so."""
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
    )

    recs = [
        ClarificationRecord(
            id="q003",
            scope=scope,
            question="Is decoy real?",
            status=ClarificationRecordStatus.answered,
            answer="I have never heard of that column and would not rely on it.",
            answered_by="sme",
        )
        for scope in ("pair:t14", "table:tbl_x")
    ]
    bag = _two_table_bag()
    stats = bag.mark_unrecognised_columns(recs)
    assert stats["no_column_in_scope"] == 2
    assert stats["marked"] == 0
    assert _suspect_names(bag) == set()
    # The caveat still reaches the corpus for the scope that has no asset at all.
    assert bag.record_caveats(recs) == 1

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


def test_sme_brief_is_addressed_to_the_physical_schema(tmp_path: Path):
    """The SME must name the columns the agent can actually select.

    The description CSVs are BIRD's untouched originals, so on the 55 of 69
    obfuscated schemas that carry a real rename an un-translated brief is
    semantically true and completely unusable: it says ``PurchaseDate`` while the
    agent is choosing between ``kaufdatum``, ``bewertungsdatum`` and
    ``transaktionsdatum``, so nothing the SME knows can land on a column. Three
    separate defects produced that, and each is asserted here:

    * no ``rename_map`` was ever passed;
    * the address came from ``column_name`` (a label — "customer id") rather than
      ``original_column_name`` (the identifier the map is keyed by);
    * 83 of the 597 CSVs open with a BOM, which corrupts the *first* header name
      and so blanked ``original_column_name`` for every row of those files.
    """
    desc = tmp_path / "database_description"
    desc.mkdir()
    (desc / "customers.csv").write_text(
        "﻿original_column_name,column_name,column_description,data_format,"
        "value_description\n"
        "CustomerID,customer id,the unique id for the customer,integer,\n"
        "PurchaseDate,purchase date,the date of purchase,date,yyyy-mm-dd\n"
        "Freight,freight,shipping cost on the order,real,\n",
        encoding="utf-8",
    )
    rename_map = {
        "customers": "kunden",
        "CustomerID": "kunde_id",
        "PurchaseDate": "kaufdatum",
    }
    brief = build_sme_brief(desc, [], rename_map=rename_map)

    assert "### kunden" in brief
    assert "- kunde_id: the unique id for the customer" in brief
    assert "- kaufdatum: the date of purchase" in brief
    # The BIRD spellings and the friendly labels are both wrong addresses.
    for absent in ("CustomerID", "PurchaseDate", "customer id", "purchase date"):
        assert absent not in brief
    # A described column with no map entry is not in the obfuscated schema (BIRD
    # ships full-dataset docs for subset databases); describing it would invent a
    # column the agent cannot select.
    assert "Freight" not in brief and "shipping cost" not in brief

    # No map means "already physical" — an identity-rename schema must not be
    # emptied out by the same code path.
    plain = build_sme_brief(desc, [], rename_map={})
    assert "- CustomerID: the unique id for the customer" in plain
    assert "- Freight: shipping cost on the order" in plain


def test_sme_brief_resolves_misfiled_description_filenames(tmp_path: Path):
    """A description CSV filed under a name that is not the table must still be
    headed with the table.

    Three of the 569 CSVs are misfiled: app_store's ``googleplaystore.csv`` /
    ``googleplaystore_user_reviews.csv`` are tables ``playstore`` / ``user_reviews``,
    and student_loan's ``filed_for_bankruptcy.csv`` is the misspelt table
    ``filed_for_bankrupcy``. Taking the stem at face value headed *both* of
    app_store's tables with a name that exists nowhere in the schema.
    """
    desc = tmp_path / "database_description"
    desc.mkdir()
    header = (
        "original_column_name,column_name,column_description,data_format,"
        "value_description\n"
    )
    (desc / "googleplaystore.csv").write_text(header + "App,,name,text,\n", "utf-8")
    (desc / "googleplaystore_user_reviews.csv").write_text(
        header + "Sentiment,,tone,text,\n", encoding="utf-8"
    )
    (desc / "filed_for_bankruptcy.csv").write_text(header + "name,,who,text,\n", "utf-8")

    brief = build_sme_brief(
        desc,
        [],
        rename_map={
            "playstore": "playstore",
            "user_reviews": "user_reviews",
            "filed_for_bankrupcy": "shen_qing_po_chan",
            "App": "App",
            "Sentiment": "Sentiment",
            "name": "ming_cheng",
        },
    )
    assert "### playstore" in brief
    assert "### user_reviews" in brief  # longest suffix wins over a "Reviews" column
    assert "### shen_qing_po_chan" in brief  # closest key, past the typo
    assert "googleplaystore" not in brief and "filed_for_bankruptcy" not in brief


def test_sme_brief_leakage_guard_ignores_the_english_word_select(tmp_path: Path):
    """``SELECT`` matched case-insensitively also matched prose.

    european_football_2's ``Player_Attributes.csv`` says "implies that the player
    will select the attack actions he will join in". Once the dev-tree descriptions
    became reachable, that schema failed the leakage assert and was dropped from the
    pool *after* its baseline, seeded and curated corpora had been built and paid
    for. All 30,492 gold statements spell the keyword ``SELECT``, so the guard is
    case-sensitive and loses nothing.
    """
    desc = tmp_path / "database_description"
    desc.mkdir()
    (desc / "player.csv").write_text(
        "original_column_name,column_name,column_description,data_format,"
        "value_description\n"
        "defensive_work_rate,,the rate,text,medium: implies that the player will "
        "select the defensive actions he will join in\n",
        encoding="utf-8",
    )
    brief = build_sme_brief(
        desc,
        [],
        rename_map={"player": "spieler", "defensive_work_rate": "abwehrarbeitsrate"},
    )
    assert "will select the defensive actions" in brief
    assert_brief_no_leakage(brief, gold_sqls=[], test_questions=[])
    with pytest.raises(AssertionError, match="SELECT"):
        assert_brief_no_leakage("bad SELECT 1", gold_sqls=[], test_questions=[])


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


def test_deep_agent_invoke_receives_usage_callback(bird_connector, tmp_path: Path, monkeypatch):
    """The curator deep agent must run with the usage callback in its config, or
    its (majority) LLM volume is unpriceable. Regression guard.

    This guarded a Langfuse handler AND the usage handler, which shared one
    producer. Langfuse went on 2026-08-02; the usage handler is the load-bearing
    half and stays — ``analyst.run_log.usage_callback_entries`` reads curator/SME
    token totals straight off it, and curator spend is already the largest
    unpriced line in a run.

    Patched at ``governed_bi.obs.usage_callbacks`` (formerly ``tracing_callbacks``)
    — the single producer every call site draws from, since
    ``obs.tracing_invoke_config`` routes through it. This used to patch the
    pipeline module's own re-exported name, which is not a seam anyone promised to
    keep: M4 N12a (526f21a) replaced the module-level import with a function-local
    ``tracing_invoke_config`` call, and the guard started erroring on a missing
    attribute instead of checking anything. Patching the producer holds however
    the pipeline reaches it, and pins N12a's own contribution too — the
    :class:`~governed_bi.obs.RunContext` metadata, which is what correlates the
    curator's trace with ``runs/index.jsonl`` and the durable run log.
    """
    from governed_bi import obs as obs_mod
    from governed_bi.curator import deep_agent as da_mod
    from governed_bi.curator import pipeline as pipe_mod

    class _RecordingAgent:
        def __init__(self):
            self.configs: list = []

        def stream(self, payload, config=None, stream_mode=None):
            self.configs.append(config)
            yield "values", {"messages": []}

    rec = _RecordingAgent()
    monkeypatch.setattr(da_mod, "build_curator_agent", lambda *a, **k: rec)
    monkeypatch.setattr(
        obs_mod, "usage_callbacks", lambda **_kwargs: ["USAGE_SENTINEL"]
    )

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
    cfg = rec.configs[0]
    assert cfg.get("callbacks") == ["USAGE_SENTINEL"], (
        f"usage callback not threaded into agent.invoke config: {cfg}"
    )
    # N12a threads a RunContext alongside the callbacks. Without run_id in the
    # trace metadata the curator's spans land in LangSmith unjoinable to the run
    # that produced them, which is the same blind spot one level down.
    metadata = cfg.get("metadata") or {}
    assert metadata.get("run_id"), f"RunContext run_id not threaded into agent config: {cfg}"


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


def test_the_only_sme_rules_variant_permits_probing_and_answers_for_decoys():
    """One variant, and it has to carry both halves of what the SME is for.

    The graded database is `rename_decoy` and the decoys are undocumented: 1,486
    invented columns and 162 invented tables sit alongside the real ones in
    `pg_rename_decoy`, and none has a BIRD description or a rename-map entry, so
    none can reach the brief — verified separately against the SQLite schemas, where
    every one of the 2,893 real physical column names is described and none of the
    decoys is. That makes "absent from the brief" a sound signal, and the
    unknown-identifier rule is what turns it into an answer instead of silence.

    The other half is the SQL clause. The two deleted variants banned database
    queries outright while the runtime user message in the same call invited a
    read-only probe; the surviving one permits the probe tool and restricts the ban
    to the answer text, which is all `_sanitize_sme_answer` enforces.
    """
    from governed_bi import prompts

    assert prompts.variants("sme_rules") == ["v1"]
    assert prompts.resolve()["sme_rules"] == "v1"

    rules = prompts.text("sme_rules")
    assert "do not recognise it" in rules and "would not rely on it" in rules
    assert "run_probe_query" in rules
    # The contradiction itself, asserted absent: no blanket ban on queries may come
    # back, because the same call hands the SME a query tool.
    assert "Never write database queries" not in rules
    assert "ANSWER itself must be plain prose" in rules


def test_sme_brief_carries_the_selected_rules_variant(tmp_path: Path):
    """The brief embeds whichever rules text the caller resolved.

    Also the leakage guard, which the SQL clause walks straight into:
    `assert_brief_no_leakage` is case-**sensitive** on `SELECT` and the rules block
    is part of the brief, so a rules block that spelled the keyword to explain
    itself would fail every leakage invariant in the harness and drop the schema
    from the pool after its corpus had been paid for.
    """
    from governed_bi import prompts

    desc = tmp_path / "database_description"
    desc.mkdir()
    (desc / "t.csv").write_text(
        "original_column_name,column_name,column_description,data_format,"
        "value_description\nc,,a column,text,\n",
        encoding="utf-8",
    )
    rules = prompts.text("sme_rules")
    brief = build_sme_brief(
        desc, [], system_rules=rules, rename_map={"t": "t", "c": "c"}
    )
    assert "do not recognise it" in brief
    assert_brief_no_leakage(brief, gold_sqls=[], test_questions=[])
