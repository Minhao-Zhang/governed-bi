"""Two facts every scored row was missing: WHICH channel routed it, and how wide
the schema it was routed into is.

**Routing channel (A3).** ``retrieval.schema_router`` has recorded
``schema_route_channel`` / ``schema_route_degraded`` for a year (AUDIT R8), and no
artifact has ever carried them: the serve path stamps them onto answer provenance,
``eval.arms``'s solver relay copies provenance into ``meta`` through an explicit
allow-list that does not name them, and the row builder therefore had nothing to
copy. A real ``generations.curated.jsonl`` row from the 20260731 ladder has 73
fields and not one is channel- or degradation-related. The measured cost of the
gap, from ``runs/ablation/e1-shortlist-curated.json`` (2026-07-31 curated corpus, 57
schemas, all 1351 test questions): shortlist recall@10 — the default ``route_top_k`` —
is 0.953 on ``text-embedding-3-large`` and 0.906 on BM25 alone, so a silently dead
embedding endpoint costs 4.7pp and every downstream number reads as a curation failure
instead.

That figure USED to be quoted here, and in five places in ``src/`` including an
operator-facing WARNING, as "recall@3 0.70 vs BM25 0.35 — degradation halves routing
recall". It came from a probe on a retired 2030-question pool and the artifact above
falsifies it by 2.4x: at ``recall@3`` the real gap is 0.852 vs 0.844, and at
``recall@1`` BM25 is *ahead* (0.736 vs 0.694). It is the same defect as the price table
that was wrong by 9x — a number describing the world, written as a literal in a log
string with no path, no date and no test — and the test below used to pin the wrong
value, which is how it survived a rewrite of the code around it.

**Schema width (A4).** Pooled, EX falls monotonically from 70.7% (widest gold table
under 15 columns) to 44.3% (40+), but a within-schema control gives a sign test at
p=0.23. The observational split cannot settle it and an intervention will; either
way the covariate has to be ON the row, so the analysis stops needing a live
catalog query against a database the archived run no longer has.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.hash_grade import GoldHash
from governed_bi.eval.run_datalake import (
    _read_rows,
    _routing_channel,
    _run_pool_arm,
    _SchemaWidth,
)
from governed_bi.eval.statistics import routing_channel_counts, schema_width_census
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult
from governed_bi.stages import Stage

REPO_ROOT = Path(__file__).resolve().parents[1]


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _table(schema: str, name: str, n_columns: int) -> TableAsset:
    return TableAsset(
        id=f"tbl_{schema}_{name}",
        schema=schema,
        physical_name=name,
        columns=[_col(f"c{i}") for i in range(n_columns)],
    )


def _lake() -> Corpus:
    """Two schemas, each with a narrow and a wide table, and a name collision.

    ``orders`` exists in both schemas at different widths — the case a
    ``{table_name: n_columns}`` index would answer wrongly, and pooled BIRD is full
    of it.
    """
    return Corpus(
        assets=[
            _table("beer_factory", "orders", 3),
            _table("beer_factory", "customers", 41),
            _table("airline", "orders", 12),
        ]
    )


class _Gw:
    def execute(self, sql, identity):
        return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)


class _ScriptedSolver:
    """Returns fixed SQL plus whatever ``meta`` the test wants on the row."""

    def __init__(self, meta: dict):
        self._meta = meta

    def solve_with_meta(self, question):
        return "SELECT 1", dict(self._meta)

    def solve(self, question):
        return self.solve_with_meta(question)[0]


def _serve_one(tmp_path, *, meta, db="beer_factory", gold_sql="SELECT 1", corpus=None):
    """Drive the real row builder for one question and hand back its row."""
    item = EvalItem(question="q0", sql=gold_sql, question_id="q0")
    out = tmp_path / f"gen.{db}.jsonl"
    rows, summary = _run_pool_arm(
        arm="curated",
        solver=_ScriptedSolver(meta),
        pairs=[(item, db)],
        gold_hashes={"q0": GoldHash("q0", hash_lenient="x", hash_strict="x")},
        gateway=_Gw(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_by_db={db: frozenset()},
        arm_corpus=_lake() if corpus is None else corpus,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=out,
    )
    (row,) = _read_rows(out)
    assert rows[0] == row or rows[0]["question_id"] == row["question_id"]
    return row, summary


# --------------------------------------------------------------------------- #
# A3 — the channel reaches the row
# --------------------------------------------------------------------------- #


def _shortlist_event(channel, degraded):
    return {
        "stage": Stage.shortlist.value,
        "status": "ok",
        "ms": 3.0,
        "detail": {
            "schema_route_channel": channel,
            "schema_route_degraded": degraded,
            "n_candidates": 2,
        },
    }


def test_the_row_records_the_channel_the_shortlist_stage_reported(tmp_path):
    row, _summary = _serve_one(
        tmp_path,
        meta={
            "routed_schemas": ["beer_factory"],
            "total_schemas": 4,
            "stage_events": [_shortlist_event("bm25_fallback", True)],
        },
    )
    assert row["schema_route_channel"] == "bm25_fallback"
    assert row["schema_route_degraded"] is True


def test_a_healthy_embedding_route_is_recorded_as_not_degraded(tmp_path):
    row, _summary = _serve_one(
        tmp_path,
        meta={
            "routed_schemas": ["beer_factory"],
            "total_schemas": 4,
            "stage_events": [_shortlist_event("embedding", False)],
        },
    )
    assert row["schema_route_channel"] == "embedding"
    assert row["schema_route_degraded"] is False


def test_a_turn_that_never_routed_leaves_both_fields_unmeasured(tmp_path):
    """The three-state discipline `routed_hit` / `routing_escaped` / `pick_hit` keep.

    A bypassed router (single-schema pool, oracle-pinned corpus) recorded no channel.
    `False` there would assert that the strong channel ran and did not degrade, which
    is the reading that makes a dead endpoint invisible.
    """
    row, summary = _serve_one(
        tmp_path,
        meta={"routed_schemas": ["beer_factory"], "total_schemas": 1},
    )
    assert row["routing_bypassed"] is True
    assert row["schema_route_channel"] is None
    assert row["schema_route_degraded"] is None
    assert summary["n_routing_channel_observed"] == 0
    assert summary["routing_degraded_rate"] is None, (
        "an arm that measured no channel must not report a 0.0 degradation rate"
    )


def test_the_relay_is_preferred_over_the_stage_record_when_it_carries_the_keys():
    """`eval.arms`'s meta allow-list does not carry these two today. If it ever does,
    the direct keys win and the two sources cannot disagree on a row."""
    channel, degraded = _routing_channel(
        {
            "schema_route_channel": "embedding",
            "schema_route_degraded": False,
            "stage_events": [_shortlist_event("bm25_fallback", True)],
        }
    )
    assert (channel, degraded) == ("embedding", False)


def test_an_unrecognised_channel_is_kept_on_the_row_and_reported(capsys):
    """Dropping it would let the per-channel counts stop summing to the observed
    total with nothing said."""
    channel, degraded = _routing_channel(
        {"stage_events": [_shortlist_event("rrf_fusion", False)]}
    )
    assert channel == "rrf_fusion"
    assert degraded is False
    assert "unrecognised schema_route_channel" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# A3 — the arm summary counts, over their own denominator
# --------------------------------------------------------------------------- #


def test_the_summary_counts_degradation_over_the_rows_that_measured_it():
    rows = [
        {"schema_route_channel": "embedding", "schema_route_degraded": False},
        {"schema_route_channel": "bm25_fallback", "schema_route_degraded": True},
        {"schema_route_channel": "bm25_fallback", "schema_route_degraded": True},
        {"schema_route_channel": "none", "schema_route_degraded": True},
        # Never routed: dropped from every denominator, exactly as a `None`
        # `routing_escaped` is.
        {"schema_route_channel": None, "schema_route_degraded": None},
    ]
    counts = routing_channel_counts(rows)
    assert counts["n_routing_channel_observed"] == 4
    assert counts["n_routing_degraded_observed"] == 4
    assert counts["n_routing_degraded"] == 3
    assert counts["routing_degraded_rate"] == pytest.approx(0.75)
    assert counts["n_routing_channel_embedding"] == 1
    assert counts["n_routing_channel_bm25_fallback"] == 2
    assert counts["n_routing_channel_none"] == 1
    # The per-channel counts partition the observed rows.
    assert (
        counts["n_routing_channel_embedding"]
        + counts["n_routing_channel_bm25_fallback"]
        + counts["n_routing_channel_none"]
        == counts["n_routing_channel_observed"]
    )


def test_an_arm_with_no_routing_at_all_reports_null_not_zero():
    counts = routing_channel_counts([{"correct": True}, {"correct": False}])
    assert counts["n_routing_channel_observed"] == 0
    assert counts["n_routing_degraded"] == 0
    assert counts["routing_degraded_rate"] is None


def test_a_degraded_arm_is_announced_to_the_operator(tmp_path, capsys):
    """The warning, and the magnitude it quotes.

    ``eval.index.quotable`` now refuses the run above
    ``ROUTING_DEGRADED_QUOTABLE_FRACTION``, so this line is no longer the only place the
    fact appears — but it is still the one an operator sees while the run is alive, and
    the number in it has to be one the repo can defend. ``0.953 -> 0.906`` is
    ``runs/ablation/e1-shortlist-curated.json`` at the default ``route_top_k``; the
    ``0.70 -> 0.35`` this assertion used to pin was falsified by that same artifact.
    """
    _row, summary = _serve_one(
        tmp_path,
        meta={
            "routed_schemas": ["beer_factory"],
            "total_schemas": 4,
            "stage_events": [_shortlist_event("bm25_fallback", True)],
        },
    )
    assert summary["n_routing_degraded"] == 1
    printed = capsys.readouterr().out
    assert "schema_route_degraded" in printed
    assert "0.953 -> 0.906" in printed
    assert "0.70 -> 0.35" not in printed, (
        "the retired 2030-question probe figure, falsified 2.4x by "
        "runs/ablation/e1-shortlist-curated.json"
    )


# --------------------------------------------------------------------------- #
# A3 — the router itself: the fallback branch can now fire on a dead endpoint
# --------------------------------------------------------------------------- #


class _DeadEmbedder:
    """An embedding endpoint that is up enough to be configured and down enough to
    fail every call — a revoked key, a 500, a DNS hole."""

    def embed(self, texts):
        raise RuntimeError("503 Service Unavailable")

    def embed_one(self, text):
        raise RuntimeError("503 Service Unavailable")


def test_a_dead_embedder_degrades_to_bm25_instead_of_crashing_the_question():
    """The `bm25_fallback` branch exists for a dead embedding endpoint (AUDIT R8) and
    could not fire on it: `embed_one` raised, the exception left the router, and the
    driver scored the question as CRASHED. So the branch never once fired on the
    failure mode it was written for, and no run ever recorded `degraded=True`."""
    from governed_bi.retrieval.schema_router import shortlist_schemas

    channel: dict = {}
    out = shortlist_schemas(
        _lake(),
        "how many orders did each customer place",
        top_k=2,
        embedder=_DeadEmbedder(),
        channel_out=channel,
    )
    assert out, "the router returned nothing rather than falling back"
    assert channel["schema_route_channel"] == "bm25_fallback"
    assert channel["schema_route_degraded"] is True


def test_a_dead_embedder_degrades_even_with_precomputed_schema_vectors():
    """The serve path precomputes schema vectors at graph-build time, so the only
    live call per question is `embed_one`. Guarding just the batch embed would leave
    the hot path unguarded."""
    from governed_bi.retrieval.schema_router import shortlist_schemas

    channel: dict = {}
    shortlist_schemas(
        _lake(),
        "orders",
        top_k=2,
        embedder=_DeadEmbedder(),
        schema_vectors={"beer_factory": [1.0, 0.0], "airline": [0.0, 1.0]},
        channel_out=channel,
    )
    assert channel["schema_route_degraded"] is True


def test_a_working_embedder_reports_the_embedding_channel_undegraded():
    from governed_bi.retrieval.schema_router import shortlist_schemas

    class _Embedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def embed_one(self, text):
            return [1.0, 0.0]

    channel: dict = {}
    shortlist_schemas(
        _lake(), "orders", top_k=1, embedder=_Embedder(), channel_out=channel
    )
    assert channel["schema_route_channel"] == "embedding"
    assert channel["schema_route_degraded"] is False


class _DiesMidRunEmbedder:
    """Alive when the graph is built, dead by the time questions arrive.

    The realistic shape of AUDIT R8's failure: the schema vectors are precomputed
    once at graph-build time and only `embed_one` runs per question, so an endpoint
    that dies during a multi-hour run fails on the hot path alone. It is also the
    only shape that MUST degrade rather than raise — a run half-served cannot be
    restarted for free, while an endpoint that is dead from the start should (and
    does, see the test below) take the graph build down before anything is spent.
    """

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_one(self, text):
        raise RuntimeError("503 Service Unavailable")


def test_an_endpoint_dead_before_the_graph_is_built_still_fails_loudly():
    """The boundary of the degrade, stated as a test so it is a decision and not an
    oversight. `ServeRuntime.build` precomputes the schema vectors, and that call is
    deliberately NOT guarded: an endpoint that never worked is a misconfiguration,
    and failing there costs nothing and writes no rows, while degrading would serve a
    whole run on the weak channel and re-embed every schema document per question.
    """
    from governed_bi.retrieval.schema_router import embed_schema_documents

    with pytest.raises(RuntimeError, match="503"):
        embed_schema_documents(_lake(), _DeadEmbedder())


def test_the_serve_path_publishes_the_channel_where_the_relay_can_reach_it(monkeypatch):
    """End to end over the real graph: a multi-schema corpus, an embedder that dies
    mid-run, and no hand-written provenance anywhere. This is the test that would have
    caught the original gap — the two fields were on `answer.provenance` and the eval
    could not see them, because `eval.arms` relays a fixed list of keys and
    `stage_events` is the only general-purpose carrier on it.
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from governed_bi.analyst.agent import answer_question_agent
    from governed_bi.config import DataSourceConfig, Environment, Settings
    from governed_bi.gateway import Gateway, SqliteConnector
    from governed_bi.llm.fake import FakeToolModel
    from governed_bi.retrieval import RetrievalResult

    monkeypatch.setattr(
        "governed_bi.analyst.agent.retrieve",
        lambda corpus, question, **kw: RetrievalResult(
            question=question,
            table_ids=["tbl_beer_factory_orders"],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        ),
    )
    settings = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),
    )
    conn = SqliteConnector(":memory:")
    try:
        answer = answer_question_agent(
            "how many orders",
            Identity(user="dev", all_access=True),
            corpus=_lake().for_analyst(),
            gateway=Gateway(conn),
            settings=settings,
            session_id="s",
            model=FakeToolModel(responses=[AIMessage(content="done")]),
            embedder=_DiesMidRunEmbedder(),
        )
    finally:
        conn.close()

    prov = answer.provenance
    assert prov["schema_route_channel"] == "bm25_fallback"
    assert prov["schema_route_degraded"] is True
    # ...and the same two values on the carrier the eval relay actually copies.
    channel, degraded = _routing_channel({"stage_events": prov["stage_events"]})
    assert (channel, degraded) == ("bm25_fallback", True)
    # The same record also lands in stage_events.jsonl, where the shortlist stage
    # had no timing at all before this — it was declared in `governed_bi.stages`
    # and emitted by nobody.
    from governed_bi.eval.run_datalake import _stage_event_rows

    shortlist_rows = [
        r
        for r in _stage_event_rows(prov, question_id="q0", arm="curated", db_id="x")
        if r["stage"] == Stage.shortlist.value
    ]
    assert len(shortlist_rows) == 1
    assert shortlist_rows[0]["detail"]["schema_route_degraded"] is True
    assert isinstance(shortlist_rows[0]["ms"], float)


# --------------------------------------------------------------------------- #
# A4 — per-row schema width
# --------------------------------------------------------------------------- #


def test_the_widest_gold_table_is_measured_not_the_first_one(tmp_path):
    row, _summary = _serve_one(
        tmp_path,
        meta={"routed_schemas": ["beer_factory"], "total_schemas": 4},
        gold_sql=(
            "SELECT c.c0 FROM orders o JOIN customers c ON o.c0 = c.c0"
        ),
    )
    assert row["gold_table_max_columns"] == 41  # customers, not orders (3)
    assert row["n_schema_tables"] == 2


def test_width_is_read_from_the_questions_own_schema_not_a_same_named_table(tmp_path):
    """`orders` is 3 columns in beer_factory and 12 in airline. A name-keyed index
    would answer this question with whichever schema loaded last."""
    row, _summary = _serve_one(
        tmp_path,
        db="airline",
        meta={"routed_schemas": ["airline"], "total_schemas": 4},
        gold_sql="SELECT c0 FROM orders",
    )
    assert row["gold_table_max_columns"] == 12
    assert row["n_schema_tables"] == 1


@pytest.mark.parametrize(
    "gold_sql",
    [
        pytest.param("SELECT * FROM (VALUES (1))", id="frozen-constant-names-no-table"),
        pytest.param("this is not sql at all ((", id="unparseable"),
        pytest.param("SELECT 1 FROM not_in_the_corpus", id="table-absent-from-corpus"),
        pytest.param(None, id="no-gold-at-all"),
    ],
)
def test_unmeasurable_width_is_none_never_zero(tmp_path, gold_sql):
    """`0` would land the question in the NARROWEST stratum of the analysis this
    field exists to feed — the same absent-vs-zero collapse that cost this harness a
    set of numbers already."""
    row, _summary = _serve_one(
        tmp_path,
        meta={"routed_schemas": ["beer_factory"], "total_schemas": 4},
        gold_sql=gold_sql,
    )
    assert row["gold_table_max_columns"] is None


def test_an_unbuilt_schema_reports_no_table_count(tmp_path):
    row, _summary = _serve_one(
        tmp_path,
        db="never_built",
        meta={"routed_schemas": ["never_built"], "total_schemas": 4},
        gold_sql="SELECT 1 FROM orders",
    )
    assert row["n_schema_tables"] is None
    assert row["gold_table_max_columns"] is None


def test_the_width_index_is_a_property_of_the_catalog_not_of_the_arm():
    """Two arms' corpora differ in descriptions, notes and few-shots, never in the
    catalog's column lists. The covariate has to be identical across arms or a
    within-schema control is comparing different strata per arm.
    """
    lake = _lake()
    curated = Corpus(assets=[*lake.assets])
    for asset in curated.assets:
        if isinstance(asset, TableAsset):
            asset.description = "a curated description"

    assert _SchemaWidth.of(lake).columns == _SchemaWidth.of(curated).columns
    assert _SchemaWidth.of(lake).tables == _SchemaWidth.of(curated).tables


def test_the_width_index_survives_a_corpus_object_with_no_assets():
    """Several eval call sites pass a minimal stand-in corpus whose only job is
    resolving asset ids. Width is then unmeasured, not a crash."""
    class _IdOnlyCorpus:
        def by_id(self, aid):
            return None

    width = _SchemaWidth.of(_IdOnlyCorpus())
    assert width.tables == {}
    assert width.gold_max_columns("beer_factory", "SELECT 1 FROM orders", dialect="postgres") is None


def test_the_two_width_implementations_cannot_drift_apart():
    """`_SchemaWidth` (per-row) and `schema_width_census` (per-db) count the same corpus.

    They exist for different shapes -- one is a ``(schema, table) -> n_columns`` index
    feeding ``gold_table_max_columns`` on each row, the other is a per-schema rollup
    feeding ``by_db`` -- and each has its own tests. Neither of those catches the thing
    that actually goes wrong: the two agreeing today and disagreeing after someone
    changes what "a column" means in one of them. A run would then publish a per-row
    width and a per-schema width that contradict each other, and the wide-table
    analysis these fields exist for reads whichever it happened to pick up.

    Rolling the index up by schema must reproduce the census exactly.
    """
    from governed_bi.eval.run_datalake import _SchemaWidth


    # Two schemas that share a table NAME, so a name-only key would merge them, and
    # one table far wider than its neighbours so `max_table_columns` cannot be
    # accidentally equal to `n_columns` or to the table count.
    corpus = Corpus(
        assets=[
            _table("sales", "orders", 4),
            _table("sales", "wide_fact", 31),
            _table("sales", "regions", 2),
            _table("hr", "orders", 7),
        ]
    )
    index = _SchemaWidth.of(corpus)
    census = schema_width_census(corpus)

    rolled = {}
    for (schema, _tbl_name), n_cols in index.columns.items():
        acc = rolled.setdefault(schema, {"n_tables": 0, "n_columns": 0, "max_table_columns": 0})
        acc["n_tables"] += 1
        acc["n_columns"] += n_cols
        acc["max_table_columns"] = max(acc["max_table_columns"], n_cols)

    assert rolled == census, (
        "the per-row width index and the per-db width census disagree; one of them "
        f"changed what it counts. index rollup={rolled} census={census}"
    )
    assert index.tables == {s: v["n_tables"] for s, v in census.items()}
    assert census["sales"]["max_table_columns"] == 31
    assert census["hr"]["n_tables"] == 1, "a name-only key would have merged the two `orders`"
