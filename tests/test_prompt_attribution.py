"""Does the selected prompt variant reach the model, and the record reach disk?

A prompt id that stops halfway is worse than none, because it looks like coverage:
the artifact names a variant, the model was sent something else, and nothing
disagrees. So each hop is pinned separately —

    Settings.prompt_variants
      -> build_serve_rails            (the text the agent core / picker is handed)
      -> serve_config_hash            (two prompt sets are two configurations)
      -> Answer.provenance            (the stamped map + hash on a scored turn)
      -> the portable run record      (the durable copy of the same)
      -> agent_solver meta -> row     (per-row attribution in an eval run)
      -> manifest.json                (what the ledger and the resume guard read)

— and the two ends are checked against each other rather than each against a
constant spelled twice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from governed_bi import prompts
from governed_bi.config import DataSourceConfig, Environment, Settings
from governed_bi.corpus import Corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.provenance import serve_config_hash
from governed_bi.retrieval import SchemaPick

V2_PICK = prompts.get("schema_pick", "v2").text
V2_AGENT = prompts.get("agent_core", "v2").text


# --------------------------------------------------------------------------- #
# Settings -> the text the serve stack hands the model
# --------------------------------------------------------------------------- #


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


def _two_schema_corpus() -> Corpus:
    return Corpus(
        assets=[
            TableAsset(
                id="tbl_schema_a_orders",
                schema="schema_a",
                physical_name="orders",
                columns=[_col("order_id"), _col("amount")],
            ),
            TableAsset(
                id="tbl_schema_b_orders",
                schema="schema_b",
                physical_name="orders",
                columns=[_col("order_id"), _col("amount")],
            ),
        ]
    ).for_analyst()


def _lake_settings(**over) -> Settings:
    base = replace(
        Settings.for_env(Environment.dev),
        datasource=DataSourceConfig(kind="postgres", dsn="host=x"),  # span all schemas
        schema_route_llm_pick=True,
    )
    return replace(base, **over) if over else base


def _run_assemble(settings, monkeypatch, seen):
    """Drive one turn far enough to route + build the agent core, capturing the
    system prompts each stage was handed. The agent core itself never runs — the
    model is a dummy — which is fine: the prompts are chosen before that."""
    from governed_bi.analyst.agent import answer_question_agent
    from governed_bi.retrieval import RetrievalResult

    def _spy_pick(corpus, question, candidates, *, chat, system_prompt=None, **kw):
        seen["pick"] = system_prompt
        return SchemaPick("schema_a")

    def _fake_retrieve(corpus, question, *, embedder=None, **_kw):
        return RetrievalResult(
            question=question,
            table_ids=["tbl_schema_a_orders"],
            metric_ids=[],
            term_ids=[],
            few_shot_ids=[],
            scores={},
        )

    def _spy_build_core(*args, system_prompt="", **kw):
        seen["agent_core"] = system_prompt
        raise RuntimeError("stop after the prompt is chosen")

    monkeypatch.setattr("governed_bi.analyst.agent.pick_schema", _spy_pick)
    monkeypatch.setattr("governed_bi.analyst.agent.retrieve", _fake_retrieve)
    monkeypatch.setattr("governed_bi.analyst.agent.build_agent_core", _spy_build_core)

    conn = SqliteConnector(":memory:")
    try:
        with pytest.raises(Exception):
            answer_question_agent(
                "total order amount",
                Identity(user="dev", all_access=True),
                corpus=_two_schema_corpus(),
                gateway=Gateway(conn),
                settings=settings,
                session_id="s",
                model=object(),  # truthy: makes the router chat get built
            )
    finally:
        conn.close()


def test_the_default_stack_sends_v1_to_both_stages(monkeypatch):
    """The non-negotiable default: byte-identical to the pre-registry behaviour."""
    seen: dict = {}
    _run_assemble(_lake_settings(), monkeypatch, seen)
    assert seen["pick"] == prompts.get("schema_pick", "v1").text
    assert seen["agent_core"].startswith(prompts.get("agent_core", "v1").text)


def test_a_selected_variant_reaches_the_picker_and_only_the_picker(monkeypatch):
    seen: dict = {}
    _run_assemble(
        _lake_settings(prompt_variants={"schema_pick": "v2"}), monkeypatch, seen
    )
    assert seen["pick"] == V2_PICK
    # The other stage must not move: a variant that leaks across stages makes an
    # arm-to-arm delta attributable to neither.
    assert seen["agent_core"].startswith(prompts.get("agent_core", "v1").text)


def test_a_selected_variant_reaches_the_agent_core(monkeypatch):
    seen: dict = {}
    _run_assemble(
        _lake_settings(prompt_variants={"agent_core": "v2"}), monkeypatch, seen
    )
    assert seen["agent_core"].startswith(V2_AGENT)
    assert seen["pick"] == prompts.get("schema_pick", "v1").text


def test_the_governed_context_still_appends_after_the_variant(monkeypatch):
    """The variant replaces the instruction block, not the assembled context — an
    agent core with no context would refuse everything."""
    seen: dict = {}
    _run_assemble(
        _lake_settings(prompt_variants={"agent_core": "v3"}), monkeypatch, seen
    )
    assert seen["agent_core"].startswith(prompts.get("agent_core", "v3").text)
    assert "## Current time" in seen["agent_core"]


def test_an_unknown_variant_in_settings_fails_the_graph_build():
    """Fail closed at build, not per turn: a stack that quietly served v1 while
    Settings said v9 would stamp v9 on every row it produced."""
    from governed_bi.analyst.agent import build_serve_rails

    conn = SqliteConnector(":memory:")
    try:
        with pytest.raises(KeyError):
            build_serve_rails(
                corpus=_two_schema_corpus(),
                gateway=Gateway(conn),
                settings=_lake_settings(prompt_variants={"agent_core": "v9"}),
                identity=Identity(user="dev", all_access=True),
                model=None,
            )
    finally:
        conn.close()


def test_the_narrator_takes_a_variant_and_defaults_to_v1():
    from governed_bi.analyst.narrate import LlmAnswerNarrator
    from governed_bi.analyst.answer import ResultTable

    class _Chat:
        def __init__(self):
            self.system = None

        def complete(self, system, user):
            self.system = system
            return "seven"

    result = ResultTable(columns=["n"], rows=[(7,)], row_count=1)

    default = _Chat()
    LlmAnswerNarrator(default).narrate("q", "SELECT 1", result)
    assert default.system == prompts.get("narrator", "v1").text

    injected = _Chat()
    LlmAnswerNarrator(injected, system_prompt="CUSTOM").narrate("q", "SELECT 1", result)
    assert injected.system == "CUSTOM"


# --------------------------------------------------------------------------- #
# The build side: curator phases and the SME rules block
# --------------------------------------------------------------------------- #


def test_the_sme_rules_block_is_injectable_and_defaults_to_v1(tmp_path):
    from governed_bi.curator.sme import build_sme_brief

    default = build_sme_brief(tmp_path, [])
    assert prompts.get("sme_rules", "v1").text.strip() in default

    injected = build_sme_brief(tmp_path, [], system_rules="RULES-SENTINEL")
    assert "RULES-SENTINEL" in injected
    assert prompts.get("sme_rules", "v1").text.strip() not in injected


def test_the_phase_a_prompt_is_injectable(tmp_path, monkeypatch):
    """A curated corpus built under one prompt and stamped under another would make
    the curated arms' numbers unattributable, so this hop is threaded too — even
    though only ``v1`` exists today."""
    from governed_bi.curator import deep_agent, pipeline

    seen: dict = {}

    def _spy(model, **kw):
        seen["prompt"] = kw.get("system_prompt")
        raise RuntimeError("captured")

    monkeypatch.setattr(deep_agent, "build_curator_agent", _spy)
    monkeypatch.setattr(pipeline, "profile_database", lambda connector, schema=None: [])

    class _Connector:
        def list_schemas(self):
            return ["s"]

    with pytest.raises(RuntimeError, match="captured"):
        pipeline.build_curated_corpus(
            _Connector(),
            None,
            "s",
            [],
            tmp_path / "curated",
            model=object(),
            run_agent=True,
            system_prompt="PHASE-A-SENTINEL",
        )
    assert seen["prompt"] == "PHASE-A-SENTINEL"


def test_the_phase_a_default_is_still_v1(tmp_path, monkeypatch):
    from governed_bi.curator import deep_agent, pipeline

    seen: dict = {}

    def _spy(model, **kw):
        seen["prompt"] = kw.get("system_prompt")
        raise RuntimeError("captured")

    monkeypatch.setattr(deep_agent, "build_curator_agent", _spy)
    monkeypatch.setattr(pipeline, "profile_database", lambda connector, schema=None: [])

    class _Connector:
        def list_schemas(self):
            return ["s"]

    with pytest.raises(RuntimeError, match="captured"):
        pipeline.build_curated_corpus(
            _Connector(), None, "s", [], tmp_path / "curated", model=object(), run_agent=True
        )
    assert seen["prompt"] == prompts.get("curator_phase_a", "v1").text


def test_the_phase_b_prompt_is_injectable(tmp_path, monkeypatch):
    """Phase B via the real vendored fixture: the spy delegates, so the build still
    completes and the assertion is about what the agent was actually handed."""
    from langchain_core.messages import AIMessage

    from governed_bi.curator import deep_agent, pipeline
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        StaticResponder,
        write_clarifications,
    )
    from governed_bi.eval.dataset import EvalItem

    bird_db = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
    if not bird_db.exists():
        pytest.skip("vendored beer_factory.sqlite not present")

    from test_curator_agent_behavior import ScriptedToolModel, _tc

    conn = SqliteConnector(bird_db)
    try:
        gateway = Gateway(conn)
        train = [
            EvalItem(question="How many customers?", sql="SELECT COUNT(*) FROM customers")
        ]
        curated = pipeline.build_curated_corpus(
            conn,
            gateway,
            "beer_factory",
            train,
            tmp_path / "curated",
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

        real = deep_agent.build_curator_agent
        seen: dict = {}

        def _spy(model, **kw):
            seen["prompt"] = kw.get("system_prompt")
            return real(model, **kw)

        monkeypatch.setattr(deep_agent, "build_curator_agent", _spy)

        pipeline.build_curated_corpus_with_sme(
            conn,
            gateway,
            "beer_factory",
            train,
            tmp_path / "curated_sme",
            responder=StaticResponder(default="Customers who bought root beer."),
            curated_root=curated,
            model=ScriptedToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            _tc(
                                "annotate_table",
                                {
                                    "table": "customers",
                                    "description": "Customers who bought root beer.",
                                    "certified": True,
                                    "answered_by": "sme",
                                    "confidence": 0.9,
                                },
                                "b1",
                            )
                        ],
                    ),
                    AIMessage(content="ingested"),
                ]
            ),
            run_agent_repass=True,
            seed_ledger_if_empty=False,
            system_prompt="PHASE-B-SENTINEL",
        )
        assert seen["prompt"] == "PHASE-B-SENTINEL"
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# serve_config_hash
# --------------------------------------------------------------------------- #


def test_two_prompt_sets_are_two_serve_configurations():
    base = Settings.for_env(Environment.dev)
    v2 = replace(base, prompt_variants={"schema_pick": "v2"})
    assert serve_config_hash(v2) != serve_config_hash(base)
    # ...and spelling the defaults out explicitly is the same configuration.
    spelled = replace(base, prompt_variants=dict(prompts.DEFAULTS))
    assert serve_config_hash(spelled) == serve_config_hash(base)


def test_editing_a_prompt_in_place_changes_the_serve_config_hash(monkeypatch):
    """The digest has to move on a text edit, or the run before and the run after a
    prompt tweak claim to be the same configuration."""
    base = Settings.for_env(Environment.dev)
    before = serve_config_hash(base)
    edited = dict(prompts.REGISTRY["narrator"])
    edited["v1"] = prompts.PromptVariant(
        stage="narrator",
        variant="v1",
        text=prompts.get("narrator", "v1").text + " Also mention the weather.",
        rationale="edited in place",
    )
    monkeypatch.setitem(prompts.REGISTRY, "narrator", edited)
    assert serve_config_hash(base) != before


# --------------------------------------------------------------------------- #
# The stamped record
# --------------------------------------------------------------------------- #


@pytest.fixture
def log_settings(tmp_path):
    return replace(
        Settings.for_env(Environment.dev),
        run_log_kind="jsonl",
        run_log_path=str(tmp_path / "runs.jsonl"),
        prompt_variants={"agent_core": "v2"},
    )


def test_finalize_stamps_the_map_and_the_hash_on_the_answer(log_settings):
    from governed_bi.analyst.answer import refusal
    from governed_bi.analyst.run_log import (
        METADATA_PROVENANCE_KEYS,
        FinalizeCtx,
        finalize_and_log,
        load_run_record,
    )

    assert "prompt_variants" in METADATA_PROVENANCE_KEYS
    assert "prompt_set_hash" in METADATA_PROVENANCE_KEYS

    ctx = FinalizeCtx(settings=log_settings, run_id="r1", thread_id="t1", n_human=1)
    stamped = finalize_and_log(refusal(escalation="nope", provenance={}), ctx=ctx)

    # The MAP, resolved in full — a reader of one row must not need the defaults
    # table to know what the other five stages sent.
    assert stamped.provenance["prompt_variants"] == prompts.resolve(
        {"agent_core": "v2"}
    )
    assert stamped.provenance["prompt_set_hash"] == prompts.prompt_set_hash(
        {"agent_core": "v2"}
    )
    assert stamped.provenance["prompt_variants"]["schema_pick"] == "v1"

    record = load_run_record("t1:1", log_settings)
    assert record["prompt_variants"] == stamped.provenance["prompt_variants"]
    assert record["prompt_set_hash"] == stamped.provenance["prompt_set_hash"]


def test_emit_run_record_stamps_the_same_two_keys(log_settings):
    """Curator / SME / eval producers go through this path, not finalize_and_log; a
    prompt stamp on only one of them is a gap exactly where a corpus was built."""
    from governed_bi.analyst.run_log import emit_run_record
    from governed_bi.provenance import Producer

    rec = emit_run_record(
        settings=log_settings,
        producer=Producer.curator,
        run_id="r2",
        thread_id="t2",
        outcome="finalize",
    )
    assert rec["prompt_variants"] == prompts.resolve({"agent_core": "v2"})
    assert rec["prompt_set_hash"] == prompts.prompt_set_hash({"agent_core": "v2"})


# --------------------------------------------------------------------------- #
# The eval relay: provenance -> solver meta -> row
# --------------------------------------------------------------------------- #


def _solver_over(provenance: dict, monkeypatch):
    from governed_bi.eval.arms import agent_solver

    answer = SimpleNamespace(
        sql="SELECT 1",
        provenance=provenance,
        tier=SimpleNamespace(value="governed"),
        semantic_assurance=SimpleNamespace(value="grounded"),
        safety_clearance=True,
    )
    graph = SimpleNamespace(invoke=lambda state, config=None: {"answer": answer})
    monkeypatch.setattr(
        "governed_bi.analyst.agent.build_serve_rails", lambda **kw: graph
    )
    return agent_solver(
        corpus=None,
        gateway=None,
        settings=Settings.for_env(Environment.dev),
        identity=None,
        model=None,
    )


def test_the_solver_relays_the_prompt_stamp(monkeypatch):
    resolved = prompts.resolve({"agent_core": "v2"})
    digest = prompts.prompt_set_hash({"agent_core": "v2"})
    solver = _solver_over(
        {"prompt_variants": resolved, "prompt_set_hash": digest}, monkeypatch
    )
    _sql, meta = solver.solve_with_meta("q")
    assert meta["prompt_variants"] == resolved
    assert meta["prompt_set_hash"] == digest


def test_an_unstamped_turn_relays_none_not_the_defaults(monkeypatch):
    """"Nothing recorded which prompt ran" and "v1 ran" are different facts, and
    only the second may be printed as v1."""
    solver = _solver_over({"refused_by": None}, monkeypatch)
    _sql, meta = solver.solve_with_meta("q")
    assert meta["prompt_variants"] is None
    assert meta["prompt_set_hash"] is None


def test_the_pinned_driver_row_carries_the_prompt_stamp():
    from governed_bi.eval.dataset import EvalItem
    from governed_bi.eval.hash_grade import GoldHash
    from governed_bi.eval.run_experiment import _run_arm_generations
    from governed_bi.gateway.connectors.base import QueryResult

    digest = prompts.prompt_set_hash({"agent_core": "v2"})
    resolved = prompts.resolve({"agent_core": "v2"})

    class _Gateway:
        def execute(self, sql, identity):
            return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)

    class _Solver:
        def solve_with_meta(self, question):
            return "SELECT 1", {
                "prompt_variants": resolved,
                "prompt_set_hash": digest,
            }

        def solve(self, question):
            return self.solve_with_meta(question)[0]

    rows, _summary, _extra = _run_arm_generations(
        arm="curated",
        solver=_Solver(),
        items=[EvalItem(question="q", sql="SELECT 1", question_id="q0")],
        gold_hashes={"q0": GoldHash("q0", hash_lenient="x", hash_strict=None, nrows=1)},
        gateway=_Gateway(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_columns=frozenset(),
        dialect="postgres",
    )
    assert rows[0]["prompt_variants"] == resolved
    assert rows[0]["prompt_set_hash"] == digest


def test_the_pooled_driver_row_carries_the_prompt_stamp(tmp_path):
    from governed_bi.eval.dataset import EvalItem
    from governed_bi.eval.hash_grade import GoldHash
    from governed_bi.eval.run_datalake import _run_pool_arm
    from governed_bi.gateway.connectors.base import QueryResult

    digest = prompts.prompt_set_hash({"schema_pick": "v2"})
    resolved = prompts.resolve({"schema_pick": "v2"})

    class _Gateway:
        def execute(self, sql, identity):
            return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)

    class _Solver:
        def solve_with_meta(self, question):
            return "SELECT 1", {
                "prompt_variants": resolved,
                "prompt_set_hash": digest,
                "routed_schemas": ["db_a"],
                "shortlisted_schemas": ["db_a"],
                "schema_pick": "db_a",
            }

        def solve(self, question):
            return self.solve_with_meta(question)[0]

    rows, _summary = _run_pool_arm(
        arm="curated",
        solver=_Solver(),
        pairs=[(EvalItem(question="q", sql="SELECT 1", question_id="q0"), "db_a")],
        gold_hashes={"q0": GoldHash("q0", hash_lenient="x", hash_strict=None, nrows=1)},
        gateway=_Gateway(),
        identity=Identity(user="eval", all_access=True),
        bird_dir=None,
        suspect_by_db={},
        arm_corpus=None,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=tmp_path / "generations.curated.jsonl",
        split="test",
        resume=False,
    )
    assert rows[0]["prompt_variants"] == resolved
    assert rows[0]["prompt_set_hash"] == digest


# --------------------------------------------------------------------------- #
# manifest.json: the resume guard and the run ledger
# --------------------------------------------------------------------------- #


def _manifest(**over):
    from governed_bi.eval.run_datalake import _build_manifest

    kwargs = dict(
        bird_dir=Path("../BIRD-Data-Obfuscation"),
        split="test",
        model_name="gpt-5.6-luna",
        prompt_variants=prompts.resolve(None),
        route_top_k=10,
        route_llm_pick=True,
        schema_pick_max_columns=12,
        use_embedder=True,
        skip_agent=False,
        serve_workers=1,
    )
    kwargs.update(over)
    return _build_manifest(**kwargs)


def test_every_resume_knob_is_actually_in_the_manifest():
    """A knob the guard checks but the manifest never records can never fire, and
    its absence looks exactly like agreement."""
    from governed_bi.eval.run_datalake import _RESUME_KNOBS

    missing = set(_RESUME_KNOBS) - set(_manifest())
    assert not missing, missing


def test_the_manifest_carries_what_the_run_ledger_reads():
    from governed_bi.eval.index import COMPARABILITY_KEYS, record_for_run

    keys = {k for k, _label in COMPARABILITY_KEYS}
    assert "prompt_set_hash" in keys
    assert not keys - set(_manifest())
    assert record_for_run  # imported for the round-trip below


def test_the_ledger_reads_the_prompt_stamp_out_of_a_real_manifest(tmp_path):
    """End of the chain: the map and the hash the driver writes are the ones the
    ledger's comparability rule compares."""
    import json

    from governed_bi.eval.index import comparable, record_for_run

    def _write(name, manifest):
        run = tmp_path / name
        run.mkdir()
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (run / "summary.json").write_text(
            json.dumps(
                {
                    "split": "test",
                    "arms": {"curated": {"n": 1, "ex_lenient": 0.5, "crash_rate": 0.0}},
                }
            ),
            encoding="utf-8",
        )
        return record_for_run(run)

    v1 = _write("v1run", _manifest())
    v2 = _write("v2run", _manifest(prompt_variants=prompts.resolve({"agent_core": "v2"})))

    assert v1["prompt_variants"]["agent_core"] == "v1"
    assert v2["prompt_variants"]["agent_core"] == "v2"
    ok, diffs = comparable(v1, v2)
    assert not ok
    assert any("prompt set" in d for d in diffs)

    # Two runs on the same prompt set stay comparable.
    same = _write("v1again", _manifest())
    assert comparable(v1, same)[0], comparable(v1, same)[1]


def test_a_resume_after_a_prompt_change_is_fatal(tmp_path):
    """Escalated from a warning after review showed the warning was not enough.

    A warning assumes someone downstream can still tell the two halves apart.
    Nobody can: ``_merge_resume_manifest`` keeps the ORIGINAL manifest's top-level
    knobs and files the resume's under ``resumes``, and ``eval/index.py`` reads
    only the top level. So a directory half-scored on v1 and half on v2 reported
    itself as a clean v1 run, and ``comparable()`` matched it against one.
    """
    import json

    import pytest

    from governed_bi.eval.run_datalake import _check_resume_manifest

    out = tmp_path / "run"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")

    with pytest.raises(RuntimeError, match="prompt set"):
        _check_resume_manifest(
            out, _manifest(prompt_variants=prompts.resolve({"agent_core": "v2"}))
        )


def test_a_resume_on_the_same_prompt_set_is_silent(tmp_path, capsys):
    from governed_bi.eval.run_datalake import _check_resume_manifest

    import json

    out = tmp_path / "run"
    out.mkdir()
    prior = _manifest()
    (out / "manifest.json").write_text(json.dumps(prior), encoding="utf-8")
    _check_resume_manifest(out, _manifest())
    assert "changed knobs" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# The CLIs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module", ["governed_bi.eval.run_datalake", "governed_bi.eval.run_experiment"]
)
def test_an_unknown_variant_on_the_cli_exits_before_any_work(module, monkeypatch):
    """``--prompt sqlgen=v9`` must be a usage error. Reaching the run body first
    would spend a build (and, worse, could produce rows)."""
    import importlib

    mod = importlib.import_module(module)
    ran = {"called": False}
    entry = "run_datalake" if module.endswith("run_datalake") else "run_experiment"
    monkeypatch.setattr(
        mod, entry, lambda **kw: ran.__setitem__("called", True) or {}
    )

    argv = ["--prompt", "sqlgen=v9"]
    if entry == "run_experiment":
        argv = ["--db", "beer_factory", *argv]
    with pytest.raises(SystemExit):
        mod.main(argv)
    assert ran["called"] is False


# --------------------------------------------------------------------------- #
# The stamp has to be tied to what was sent, not to the same literal twice.
#
# `test_finalize_stamps_the_map_and_the_hash_on_the_answer` above builds its
# FinalizeCtx from a Settings holding `{"agent_core": "v2"}` and then asserts the
# stamp equals `resolve({"agent_core": "v2"})` — the same literal on both sides. It
# would pass unchanged if delivery were fully decoupled from the stamp, which is the
# one failure it exists to catch: a row stamped v2 whose model was sent v1 is worse
# than an unstamped row, because it is quotable and wrong.
#
# These tie the two together by observing the prompt the agent core was actually
# handed and requiring the stamp to move with it.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variant", ["v1", "v2", "v3"])
def test_the_stamped_hash_identifies_the_prompt_the_model_was_sent(monkeypatch, variant):
    from governed_bi.analyst.answer import refusal
    from governed_bi.analyst.run_log import FinalizeCtx, finalize_and_log

    settings = _lake_settings(prompt_variants={"agent_core": variant})

    seen: dict = {}
    _run_assemble(settings, monkeypatch, seen)
    delivered = seen["agent_core"]

    stamped = finalize_and_log(
        refusal(escalation="nope", provenance={}),
        ctx=FinalizeCtx(settings=settings, run_id="r1", thread_id=f"t-{variant}", n_human=1),
    )
    stamped_map = stamped.provenance["prompt_variants"]

    # The stamp names a variant whose registry text is what the core actually got.
    assert delivered.startswith(prompts.get("agent_core", stamped_map["agent_core"]).text), (
        f"stamped agent_core={stamped_map['agent_core']!r} but the model was sent "
        "different text"
    )
    assert stamped.provenance["prompt_set_hash"] == prompts.prompt_set_hash(stamped_map)


def test_two_runs_sending_different_prompts_do_not_share_a_stamp(monkeypatch):
    """The property that makes the hash usable as a comparability key: it has to
    separate configurations that sent different text. A stamp computed from a
    constant, or from a map the delivery path ignores, would satisfy every
    single-variant assertion above and still collapse these two into one."""
    from governed_bi.analyst.answer import refusal
    from governed_bi.analyst.run_log import FinalizeCtx, finalize_and_log

    observed, stamps = {}, {}
    for variant in ("v1", "v2"):
        settings = _lake_settings(prompt_variants={"agent_core": variant})
        seen: dict = {}
        _run_assemble(settings, monkeypatch, seen)
        observed[variant] = seen["agent_core"]
        stamps[variant] = finalize_and_log(
            refusal(escalation="nope", provenance={}),
            ctx=FinalizeCtx(
                settings=settings, run_id="r", thread_id=f"t-{variant}", n_human=1
            ),
        ).provenance["prompt_set_hash"]

    assert observed["v1"] != observed["v2"], (
        "the fixture is not exercising anything — both variants sent the same text"
    )
    assert stamps["v1"] != stamps["v2"], (
        "two runs that sent different prompts share a prompt_set_hash, so the ledger "
        "would call them the same experiment"
    )
