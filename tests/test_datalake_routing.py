"""Data-lake schema routing (D15): the ``schema_route_llm_pick`` wiring.

The pooled data-lake serve path (``eval.run_datalake``) turns on a single-schema
LLM pick so every question is scoped to one schema before retrieval. These tests
pin the ``assemble`` branch added for that: when ``schema_route_llm_pick`` is on
(and a model is present) the router calls ``pick_schema`` and collapses
retrieval to the chosen schema; when it is off (the default), the multi-schema
shortlist + curated-join expansion path is unchanged.

Both run deterministically without a live model — the routing decision happens in
``assemble`` before the agent core, so a dummy/None model never has to answer.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.config import DataSourceConfig, Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.retrieval import RetrievalResult, SchemaPick

#: Repo root, so corpus paths do not depend on pytest's working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_A_ORDERS = "tbl_schema_a_orders"
SCHEMA_B_ORDERS = "tbl_schema_b_orders"


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _two_schema_corpus() -> Corpus:
    a = TableAsset(
        id=SCHEMA_A_ORDERS,
        schema="schema_a",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    b = TableAsset(
        id=SCHEMA_B_ORDERS,
        schema="schema_b",
        physical_name="orders",
        columns=[_col("order_id"), _col("amount")],
    )
    return Corpus(assets=[a, b]).for_analyst()


def _pg_settings(**over) -> Settings:
    base = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),  # schema=None: span all
    )
    return replace(base, **over) if over else base


def test_llm_pick_calls_pick_schema_and_collapses_retrieval(monkeypatch):
    """With ``schema_route_llm_pick`` on and a model present, ``assemble`` picks one
    schema via ``pick_schema`` and retrieval only ever sees that schema."""
    corpus = _two_schema_corpus()
    settings = _pg_settings(schema_route_llm_pick=True, schema_route_top_k=8)

    seen: dict = {}

    def _spy_pick(corpus_arg, question, candidates, *, chat, **kw):
        seen["candidates"] = sorted(candidates)
        return SchemaPick("schema_a")

    def _fake_retrieve(corpus_arg, question, *, embedder=None, **_kwargs):
        seen["retrieval_schemas"] = sorted(
            {t.schema for t in corpus_arg.assets if isinstance(t, TableAsset)}
        )
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.pick_schema", _spy_pick)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        # A truthy model makes ``build_serve_rails`` construct the router chat (the
        # guard is ``model is not None``); ``pick_schema`` is spied so the object
        # is never actually called. The agent core past ``assemble`` will fail on
        # this dummy model — irrelevant, the routing decision already happened.
        try:
            answer_question_agent(
                "total order amount",
                Identity(user="dev", all_access=True),
                corpus=corpus,
                gateway=Gateway(conn),
                settings=settings,
                session_id="s",
                model=object(),
            )
        except Exception:
            pass
    finally:
        conn.close()

    assert seen.get("candidates") == ["schema_a", "schema_b"]  # shortlist offered both
    assert seen.get("retrieval_schemas") == ["schema_a"]  # collapsed to the pick


def test_default_path_does_not_pick_and_refuses_missing_edge(monkeypatch):
    """Default (``schema_route_llm_pick`` off): no ``pick_schema`` call; a
    two-schema question with no curated join still refuses on missing edge."""
    corpus = _two_schema_corpus()
    settings = _pg_settings()  # llm_pick defaults to False
    assert settings.schema_route_llm_pick is False

    called = {"select": False}

    def _spy_pick(*a, **k):
        called["select"] = True
        return SchemaPick("schema_a")

    def _fake_retrieve(corpus_arg, question, *, embedder=None, **_kwargs):
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS, SCHEMA_B_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.pick_schema", _spy_pick)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        ans = answer_question_agent(
            "compare orders across schemas",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
            model=None,  # never reached: assemble refuses on the missing edge first
        )
    finally:
        conn.close()

    assert called["select"] is False  # single-schema pick is off by default
    assert ans.provenance["refused_by"] == "missing_edge"


def _one_schema_corpus() -> Corpus:
    return Corpus(
        assets=[
            TableAsset(
                id=SCHEMA_A_ORDERS,
                schema="schema_a",
                physical_name="orders",
                columns=[_col("order_id"), _col("amount")],
            )
        ]
    ).for_analyst()


def test_single_schema_turn_asserts_the_bypass_on_provenance(monkeypatch):
    """A one-schema corpus must SAY it bypassed routing, not leave the fields blank.

    This is the regression the eval could not see. ``assemble``'s single-schema
    branch used to record nothing on provenance, so every row read
    ``routed_schemas=[]`` -> ``routed_hit=False`` -> ``routing_recall=0.0`` for a pool
    with nothing to route, and the driver's "was routing bypassed?" guard
    (``isinstance(total_schemas, int)``) could never fire, charging every wrong
    answer to ``schema_pick`` — a stage that did not run.

    The test drives the REAL rails graph. The previous test of this behaviour drove a
    scripted solver and injected ``total_schemas=1`` by hand, so it passed against a
    value production never emitted.
    """
    corpus = _one_schema_corpus()
    settings = _pg_settings(schema_route_llm_pick=True)  # on, and still must not run

    called = {"pick": False}

    def _spy_pick(*a, **k):
        called["pick"] = True
        return SchemaPick("schema_a")

    def _fake_retrieve(corpus_arg, question, *, embedder=None, **_kwargs):
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.pick_schema", _spy_pick)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        ans = answer_question_agent(
            "total order amount",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
            model=None,  # agent_core will refuse; assemble has already stamped
        )
    finally:
        conn.close()

    prov = ans.provenance or {}
    # Positive evidence, from the code that knows.
    assert prov.get("routing_bypassed") is True
    assert prov.get("total_schemas") == 1
    # The turn is pinned to its one schema, so `routed_hit` is true for the right
    # reason instead of false for a missing field.
    assert prov.get("routed_schemas") == ["schema_a"]
    # ...and the picker/shortlist stay unmeasured rather than unanimously "correct":
    # stamping them would enrol these rows in `schema_pick_accuracy` and
    # `gold_schema_rank` as successes of components that never ran.
    assert prov.get("schema_pick") is None
    assert prov.get("shortlisted_schemas") is None
    assert called["pick"] is False


def test_multi_schema_turn_does_not_claim_a_bypass(monkeypatch):
    """The complement: when the router DOES run, nothing marks the turn bypassed.

    Without this, a bug that stamped the flag unconditionally would suppress every
    genuine routing miss in the pooled run and read as perfect routing.
    """
    corpus = _two_schema_corpus()
    settings = _pg_settings(schema_route_llm_pick=True)

    def _spy_pick(*a, **k):
        return SchemaPick("schema_a")

    def _fake_retrieve(corpus_arg, question, *, embedder=None, **_kwargs):
        return RetrievalResult(
            question=question,
            table_ids=[SCHEMA_A_ORDERS],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    monkeypatch.setattr("governed_bi.analyst.agent.pick_schema", _spy_pick)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)

    conn = SqliteConnector(":memory:")
    try:
        # The dummy model makes ``agent_core`` fail, which degrades to a refusal
        # (``refused_by="model_error"``) rather than raising — so provenance is always
        # there to assert against. Asserted unconditionally on purpose: an
        # ``if ans is not None`` guard here would let this test pass vacuously the day
        # the turn starts raising instead.
        ans = answer_question_agent(
            "total order amount",
            Identity(user="dev", all_access=True),
            corpus=corpus,
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
            model=object(),
        )
    finally:
        conn.close()

    prov = ans.provenance or {}
    assert prov.get("routing_bypassed") is None
    assert prov.get("total_schemas") == 2
    # The router really did run, so its provenance is present — otherwise this test
    # would also pass on a turn that never reached ``assemble``.
    assert prov.get("shortlisted_schemas") is not None


# --------------------------------------------------------------------------- #
# The router is not a gate, and the summary now says so.
#
# The agent core is built with the POOLED corpus — `agent_core_node` passes `corpus`,
# not the routed `retrieval_corpus` — so its `search_corpus` tool retrieves across every
# schema whatever the router selected. Demonstrated directly: with the router selecting
# only `address`, retrieval over the pooled corpus returns tables from `airline`.
#
# The consequence is a measurement one. `docs/measurement.md` presents
# `EX = routing_recall x cond_ex_given_routing` and says a delta moving one term localises
# where an arm helped. That holds only if the router bounds what the answer can use. It
# does not, so the escape has to be counted.
# --------------------------------------------------------------------------- #


def test_an_answer_using_a_schema_the_router_excluded_is_an_escape():
    from governed_bi.eval.run_datalake import _routing_escaped

    assert _routing_escaped({"airline"}, ["address"], bypassed=False) is True
    assert _routing_escaped({"address"}, ["address"], bypassed=False) is False
    # One table outside is enough, even alongside compliant ones.
    assert _routing_escaped({"address", "airline"}, ["address"], bypassed=False) is True
    # Multiple routed schemas: inside any of them is compliance.
    assert (
        _routing_escaped({"airline"}, ["address", "airline"], bypassed=False) is False
    )


def test_nothing_to_judge_is_not_scored_as_compliance():
    """A turn that used no tables did not stay inside the routed set — it did not go
    anywhere. Returning False would let a run that produced no SQL report perfect routing
    discipline."""
    from governed_bi.eval.run_datalake import _routing_escaped

    assert _routing_escaped(None, ["address"], bypassed=False) is None
    assert _routing_escaped(set(), ["address"], bypassed=False) is None
    # A bypassed router (single-schema corpus, or an oracle rung handed its schema) had no
    # decision to escape from.
    assert _routing_escaped({"airline"}, ["address"], bypassed=True) is None
    assert _routing_escaped({"airline"}, [], bypassed=False) is None


def test_the_escape_is_judged_on_the_answer_not_on_the_seed_license():
    """The defect this replaced. `licensed_tables` is the assemble-time seed license,
    computed from the *routed* corpus and never amended — so it cannot contain an
    out-of-routed schema no matter what the agent went on to do.

    A reviewer demonstrated the failure end to end: a turn reached past the router via
    `search_corpus`, licensed `tbl_airline_airports` via `inspect_schema`, the guardrail
    passed it — and `licensed_tables` was pure `address`, so the metric scored the escape
    as compliant. Across all 104 built corpora there are zero cross-schema JoinAssets, so
    the old signal could only ever return False or None.

    This pins the distinction: the same turn, judged on the two signals, disagrees.
    """
    from governed_bi.eval.run_datalake import _routing_escaped

    routed = ["address"]
    seed_license_schemas = {"address"}          # what licensed_tables would have shown
    schemas_the_answer_used = {"airline"}       # what the SQL actually referenced

    assert _routing_escaped(seed_license_schemas, routed, bypassed=False) is False
    assert _routing_escaped(schemas_the_answer_used, routed, bypassed=False) is True


def test_asset_ids_resolve_to_schemas_through_the_corpus():
    """Ids look like `tbl_<schema>_<name>`, but schema names contain underscores
    (`beer_factory`), so splitting the string guesses wrong. The corpus is the only
    reliable resolver."""
    from governed_bi.corpus import load_corpus
    from governed_bi.eval.run_datalake import _schema_of_assets

    # The committed corpus, not `runs/datalake/*` build output. Globbing gitignored
    # artifacts under a RELATIVE path meant this test always skipped in CI and
    # anywhere it was not run from the repo root (AUDIT T3) — and `beer_factory` is
    # exactly the underscore-containing schema name the test exists to pin.
    corpus = load_corpus(REPO_ROOT / "corpus", schema=None)
    tables = [a for a in corpus.assets if a.asset_type == "table"]
    assert tables, "corpus holds no tables"
    assert any("_" in a.schema for a in tables), (
        "the point of this test is a schema name containing an underscore"
    )

    sample = tables[:3]
    resolved, unresolved = _schema_of_assets(corpus, [a.id for a in sample])
    assert resolved == {a.schema for a in sample}
    assert unresolved == []

    # Unknown ids are returned as unresolved, never parsed heuristically.
    assert _schema_of_assets(corpus, ["tbl_not_a_real_thing"]) == (set(), ["tbl_not_a_real_thing"])
    assert _schema_of_assets(corpus, None) == (set(), [])
    assert _schema_of_assets(None, [a.id for a in sample]) == (
        set(),
        [a.id for a in sample],
    )


def test_the_summary_reports_the_escape_rate_and_its_denominator():
    from governed_bi.eval.run_datalake import _summarise_rows

    def row(qid, db, routed, used_schemas, correct):
        """`used_schemas` is what the delivered SQL referenced — `None` for a turn that
        produced none."""
        return {
            "question_id": qid, "db_id": db, "arm": "curated", "split": "test",
            "routed_schemas": routed, "routed_hit": db in routed,
            "routing_escaped": None
            if not used_schemas
            else bool(set(used_schemas) - set(routed)),
            "correct": correct, "generated_sql": "SELECT 1",
        }

    rows = [
        # Router picked address, the answer used address: obeyed.
        row("q1", "address", ["address"], ["address"], True),
        # Router picked address, the answer used airline anyway: escaped, and won.
        row("q2", "airline", ["address"], ["airline"], True),
        # Same escape, wrong answer.
        row("q3", "airline", ["address"], ["airline"], False),
        # Produced no SQL, so used no tables: no verdict either way.
        row("q4", "address", ["address"], None, False),
    ]
    s = _summarise_rows("curated", rows)

    assert s["n_routing_escape_observed"] == 3, "the refusal is not in the denominator"
    assert s["n_routing_escaped"] == 2
    assert abs(s["routing_escape_rate"] - 2 / 3) < 1e-9
    assert s["n_correct_via_routing_escape"] == 1, (
        "correct answers that used an excluded schema are wins the router did not enable"
    )


def test_the_escape_fields_are_not_measured_when_nothing_could_escape():
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {
            "question_id": "q1", "db_id": "d", "arm": "curated", "split": "test",
            "routed_schemas": ["d"], "routed_hit": True, "routing_escaped": None,
            "correct": True, "generated_sql": "SELECT 1",
        }
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_routing_escape_observed"] == 0
    assert s["routing_escape_rate"] is None, (
        "an empty denominator must read as unmeasured, not as a perfect rate"
    )


# --------------------------------------------------------------------------- #
# The row producer, not just the predicate.
#
# Deleting the `routing_escaped` entry from the row dict left the whole suite green, and so
# did reverting its argument from `tables_used` back to the seed `licensed_tables` that
# cannot observe an escape at all. The helper had tests; the wiring did not. This drives
# `_run_pool_arm` so the row's own field is asserted.
# --------------------------------------------------------------------------- #


class _FakeTable:
    asset_type = "table"

    def __init__(self, aid, schema):
        self.id = aid
        self.schema = schema


class _FakeCorpus:
    """Just enough to resolve asset ids to schemas."""

    def __init__(self, mapping):
        self._by_id = {aid: _FakeTable(aid, schema) for aid, schema in mapping.items()}

    def by_id(self, aid):
        return self._by_id.get(aid)


class _EscapingSolver:
    """Answers every question with SQL over a table in `used_schema`."""

    def __init__(self, routed, used_asset, total_schemas=3):
        self._routed = routed
        self._used = used_asset
        self._total = total_schemas

    def solve_with_meta(self, question):
        return "SELECT 1", {
            "routed_schemas": self._routed,
            "total_schemas": self._total,
            "tables_used": [self._used],
            "licensed_tables": ["address.zips"],  # seed license: always inside the route
        }

    def solve(self, question):
        return self.solve_with_meta(question)[0]


def test_the_row_records_the_escape_from_the_tables_the_answer_used(tmp_path):
    from governed_bi.eval.dataset import EvalItem
    from governed_bi.eval.hash_grade import GoldHash
    from governed_bi.eval.run_datalake import _read_rows, _run_pool_arm
    from governed_bi.gateway import Identity
    from governed_bi.gateway.connectors.base import QueryResult

    class _Gw:
        def execute(self, sql, identity):
            return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)

    corpus = _FakeCorpus({"tbl_airline_airports": "airline", "tbl_address_zips": "address"})
    items = [EvalItem(question="q0", sql="SELECT 1", question_id="q0")]
    gold = {"q0": GoldHash("q0", hash_lenient="x", hash_strict="x")}

    # Router picked `address`; the answer used a table in `airline`.
    out = tmp_path / "escaped.jsonl"
    _run_pool_arm(
        arm="curated",
        solver=_EscapingSolver(["address"], "tbl_airline_airports"),
        pairs=[(items[0], "address")],
        gold_hashes=gold,
        gateway=_Gw(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_by_db={"address": frozenset()},
        arm_corpus=corpus,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=out,
    )
    row = _read_rows(out)[0]
    assert row["routing_escaped"] is True, (
        "the answer used a schema the router excluded and the row did not record it"
    )
    assert row["tables_used"] == ["tbl_airline_airports"]

    # And the compliant case, so the field is not simply always True.
    out2 = tmp_path / "obeyed.jsonl"
    _run_pool_arm(
        arm="curated",
        solver=_EscapingSolver(["address"], "tbl_address_zips"),
        pairs=[(items[0], "address")],
        gold_hashes=gold,
        gateway=_Gw(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_by_db={"address": frozenset()},
        arm_corpus=corpus,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=out2,
    )
    assert _read_rows(out2)[0]["routing_escaped"] is False


def test_the_row_leaves_the_escape_unmeasured_on_a_single_schema_corpus(tmp_path):
    """`total_schemas <= 1` is a bypassed router. The row builder must use the same widened
    predicate its own `routing_bypassed` field uses — passing the narrow one let a row carry
    `routing_bypassed=True` beside a non-null escape verdict."""
    from governed_bi.eval.dataset import EvalItem
    from governed_bi.eval.hash_grade import GoldHash
    from governed_bi.eval.run_datalake import _read_rows, _run_pool_arm
    from governed_bi.gateway import Identity
    from governed_bi.gateway.connectors.base import QueryResult

    class _Gw:
        def execute(self, sql, identity):
            return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)

    corpus = _FakeCorpus({"tbl_airline_airports": "airline"})
    item = EvalItem(question="q0", sql="SELECT 1", question_id="q0")
    out = tmp_path / "single.jsonl"
    _run_pool_arm(
        arm="curated",
        solver=_EscapingSolver(["address"], "tbl_airline_airports", total_schemas=1),
        pairs=[(item, "address")],
        gold_hashes={"q0": GoldHash("q0", hash_lenient="x", hash_strict="x")},
        gateway=_Gw(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_by_db={"address": frozenset()},
        arm_corpus=corpus,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=out,
    )
    row = _read_rows(out)[0]
    assert row["routing_bypassed"] is True
    assert row["routing_escaped"] is None, (
        "a bypassed router has no decision to escape, so the verdict must be unmeasured"
    )


def test_a_few_shot_asset_id_does_not_resolve_to_a_schema():
    """`_schema_of_assets` guards on `asset_type == "table"`. `FewShotAsset` also carries a
    `schema`, so without the guard a few-shot id would contribute a schema to the
    used-tables set and could manufacture an escape out of a retrieved example."""
    from governed_bi.corpus import load_corpus
    from governed_bi.eval.run_datalake import _schema_of_assets

    # The committed corpus carries a few-shot asset, so this no longer depends on
    # gitignored `runs/datalake/*` build output that never exists in CI (AUDIT T3).
    corpus = load_corpus(REPO_ROOT / "corpus", schema=None)
    few = next(a for a in corpus.assets if a.asset_type == "few_shot")
    assert getattr(few, "schema", None), "fixture assumes few-shots carry a schema"
    assert _schema_of_assets(corpus, [few.id]) == (set(), [few.id]), (
        "a non-table asset contributed a schema to the used-tables set"
    )


def test_the_driver_supplies_the_arm_corpus_rather_than_disabling_the_metric():
    """`arm_corpus` is a required parameter, which stops it being *forgotten* — but a
    caller can still pass `None`, and doing so at the driver call site silently reported
    "nothing escaped" for a whole run. Nothing in the suite noticed, because no test drives
    `run_datalake()` itself.

    A source check, which is weaker than driving the harness — that needs Postgres, a model
    and an hour — but it pins the one line that decides whether the metric runs at all.
    """
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    assert "arm_corpus=corpora[served_corpus_arm]" in src, (
        "the driver no longer passes the served arm's corpus, so routing_escaped is None "
        "for every row and the summary reports a null rate rather than a measurement"
    )
    assert "arm_corpus=None" not in src


# --------------------------------------------------------------------------- #
# The `[routing]` TOML table is not decoration (A7)
#
# `governed_bi.toml` ships `[routing]` with `top_k` / `llm_pick` /
# `pick_max_columns` and a comment promising "CLI flags still override for a
# one-off run". They did worse than override: `--route-top-k` defaulted to 10 and
# `--no-llm-pick` was a `store_true`, and both were passed UNCONDITIONALLY into the
# `replace(settings, ...)` that builds the serve settings. `[routing] top_k = 3` had
# literally no effect on this driver — it was permanently 10, while being recorded
# in the manifest, guarded on resume and used as a comparability key.
# --------------------------------------------------------------------------- #


def _settings_from_toml(tmp_path, body: str):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(body, encoding="utf-8")
    # `apply_local=False`: the repo's git-ignored governed_bi.local.toml also carries
    # a [routing] table, and picking it up here would make the test's answer depend on
    # an untracked file on the machine running it.
    return load_settings(cfg, apply_local=False)


def test_the_toml_routing_table_decides_when_no_flag_is_passed(tmp_path):
    from governed_bi.eval.run_datalake import _resolve_routing

    settings = _settings_from_toml(
        tmp_path, "[routing]\ntop_k = 3\nllm_pick = false\npick_max_columns = 5\n"
    )
    resolved = _resolve_routing(
        settings, route_top_k=None, route_llm_pick=None, schema_pick_max_columns=None
    )
    assert (resolved.top_k, resolved.llm_pick, resolved.pick_max_columns) == (3, False, 5)


def test_a_flag_still_overrides_the_toml(tmp_path):
    from governed_bi.eval.run_datalake import _resolve_routing

    settings = _settings_from_toml(
        tmp_path, "[routing]\ntop_k = 3\nllm_pick = false\npick_max_columns = 5\n"
    )
    resolved = _resolve_routing(
        settings, route_top_k=7, route_llm_pick=True, schema_pick_max_columns=0
    )
    assert (resolved.top_k, resolved.llm_pick, resolved.pick_max_columns) == (7, True, 0)


def test_an_absent_routing_table_leaves_the_dataclass_defaults(tmp_path):
    from governed_bi.config import Settings
    from governed_bi.eval.run_datalake import _resolve_routing

    settings = _settings_from_toml(tmp_path, "[runtime]\nenvironment = \"dev\"\n")
    resolved = _resolve_routing(
        settings, route_top_k=None, route_llm_pick=None, schema_pick_max_columns=None
    )
    assert resolved.top_k == Settings.schema_route_top_k
    assert resolved.llm_pick == Settings.schema_route_llm_pick
    assert resolved.pick_max_columns == Settings.schema_pick_max_columns


def test_the_manifest_records_the_resolved_value_not_the_flag(tmp_path):
    """The knob is a resume guard and a ledger comparability key, so a manifest that
    records something the serve path did not read is worse than one that records
    nothing: `comparable()` would clear two runs that routed differently."""
    from governed_bi.eval.run_datalake import _build_manifest, _resolve_routing

    settings = _settings_from_toml(tmp_path, "[routing]\ntop_k = 3\nllm_pick = false\n")
    resolved = _resolve_routing(
        settings, route_top_k=None, route_llm_pick=None, schema_pick_max_columns=None
    )
    manifest = _build_manifest(
        bird_dir=tmp_path,
        split="test",
        model_name="m",
        llm_reasoning_effort="low",
        embedding_model="e",
        embedding_dimensions=None,
        prompt_variants={},
        route_top_k=resolved.top_k,
        route_llm_pick=resolved.llm_pick,
        schema_pick_max_columns=resolved.pick_max_columns,
        use_embedder=True,
        question_pool_hash="h",
        question_subset=None,
        always_note_global_max=3,
        always_note_char_max=400,
        pin_triggers_enabled=False,
        pin_require_certified=True,
        pin_max=2,
        grade_semantic_failures=True,
        serve_workers=1,
    )
    assert manifest["route_top_k"] == 3
    assert manifest["route_llm_pick"] is False


def test_the_driver_records_the_resolved_knobs_rather_than_its_arguments():
    """The one line the unit tests above cannot reach without Postgres. Reading the
    raw parameters here is exactly what made `[routing]` dead: they are the CLI's
    values, and after the sentinel they can be `None`."""
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    for line in (
        "route_top_k=settings.schema_route_top_k,",
        "route_llm_pick=settings.schema_route_llm_pick,",
        "schema_pick_max_columns=settings.schema_pick_max_columns,",
    ):
        assert line in src, f"the manifest no longer records the resolved knob: {line}"
    assert "schema_route_top_k=routing.top_k" in src
    assert "schema_route_llm_pick=routing.llm_pick" in src


def _knob_calls(monkeypatch, tmp_path, extra_argv):
    """Drive the real argv parser, recording the kwargs `run_datalake` is called with."""
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []

    def _stub(**kwargs):
        calls.append(kwargs)
        return {"arms": {}, "treatment_divergence": {}, "comparisons": [],
                "deltas": {}, "quotable": True}

    monkeypatch.setattr(rd, "run_datalake", _stub)
    rd.main([
        "--bird-dir", str(tmp_path / "bird"),
        "--out", str(tmp_path / "runs"),
        "--oracle-only",
        "--dbs", "beer_factory",
        *extra_argv,
    ])
    return calls[0]


def test_an_unpassed_flag_arrives_as_none_so_the_toml_can_win(monkeypatch, tmp_path):
    call = _knob_calls(monkeypatch, tmp_path, [])
    assert call["route_top_k"] is None
    assert call["route_llm_pick"] is None
    assert call["schema_pick_max_columns"] is None


def test_the_flags_still_reach_the_driver_when_they_are_passed(monkeypatch, tmp_path):
    call = _knob_calls(monkeypatch, tmp_path, ["--route-top-k", "3", "--no-llm-pick"])
    assert call["route_top_k"] == 3
    assert call["route_llm_pick"] is False

    # And the affirmative half of the pair, which is the only way to ask for the LLM
    # pick when the TOML turns it off.
    call = _knob_calls(monkeypatch, tmp_path, ["--llm-pick"])
    assert call["route_llm_pick"] is True


def test_no_embedder_is_the_same_shape_of_flag_but_not_the_same_defect(monkeypatch, tmp_path):
    """`--no-embedder` is also a `store_true`, and it is checked here because it looks
    identical. It is not: there is no `Settings` field for the embedder channel, so
    there is no configured value for the flag to overwrite and nothing a sentinel
    could fall back to. The manifest records `use_embedder=bool(embedder)` — whether
    the channel was actually built — rather than what was asked for.
    """
    from governed_bi.config import Settings

    embedder_fields = [f for f in Settings.__dataclass_fields__ if "embedder" in f]
    assert not embedder_fields, (
        f"Settings grew {embedder_fields}, so `--no-embedder` now DOES overwrite a "
        "configured value on every invocation — give it the same `None` sentinel the "
        "three [routing] flags have, and delete this test"
    )

    assert _knob_calls(monkeypatch, tmp_path, [])["use_embedder"] is True
    assert _knob_calls(monkeypatch, tmp_path, ["--no-embedder"])["use_embedder"] is False
