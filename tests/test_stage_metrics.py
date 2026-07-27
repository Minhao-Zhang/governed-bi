"""Per-stage timings, tool-call counts and guardrail-layer counters.

These exist because a three-arm run had to be thrown away for want of exactly
this: the harness could say a turn failed but not *where*, a ``NameError`` in a
tool helper read as an unremarkable ``model_error``, and the two most common tool
calls (``search_corpus`` / ``inspect_schema``) left no durable trace at all. So
every assertion below is either "a failure is attributable to a stage" or "an
unmeasured thing does not render as a confident zero".
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst import SqlCache
from governed_bi.analyst.agent import answer_question_agent
from governed_bi.analyst.answer import refusal
from governed_bi.analyst.governance import GovEventStream, StageRecorder
from governed_bi.analyst.run_log import (
    FinalizeCtx,
    _INSTRUMENTATION_KEYS,
    build_metadata_record,
    finalize_and_log,
    load_run_record,
    strip_stage_events_for_log,
)
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, GuardrailLayer, Identity, SqliteConnector, check
from governed_bi.graph import MissingJoinPath
from governed_bi.llm import HashingEmbedder
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.stages import Stage

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


# --------------------------------------------------------------------------- #
# StageRecorder unit contract
# --------------------------------------------------------------------------- #


def test_stage_records_status_timing_and_caller_detail():
    rec = StageRecorder()
    with rec.stage(Stage.route, intent="lookup") as detail:
        detail["n_bound_terms"] = 2

    (event,) = rec.provenance()["stage_events"]
    assert event["stage"] == "route"
    assert event["status"] == "ok"
    assert event["ms"] >= 0
    assert event["detail"] == {"intent": "lookup", "n_bound_terms": 2}


def test_a_raising_stage_is_recorded_as_error_and_re_raised():
    """Instrumentation that swallows is worse than none: the exception has to reach
    the caller, and the record has to say the stage died rather than never ran."""
    rec = StageRecorder()
    with pytest.raises(RuntimeError, match="boom"):
        with rec.stage(Stage.agent_core):
            raise RuntimeError("boom")

    (event,) = rec.provenance()["stage_events"]
    assert event["status"] == "error"
    assert event["detail"]["error_type"] == "RuntimeError"
    assert event["ms"] >= 0


def test_a_skipped_stage_has_no_duration_rather_than_zero():
    rec = StageRecorder()
    rec.skipped(Stage.schema_pick, spans_schemas=False)

    (event,) = rec.provenance()["stage_events"]
    assert event["status"] == "skipped"
    assert event["ms"] is None  # never ran != ran in 0 ms


def test_reset_drops_the_previous_turns_records():
    rec = StageRecorder()
    with rec.stage(Stage.route):
        pass
    rec.count_tool_call("run_query")
    rec.guardrail_layer(GuardrailLayer.syntax, True)
    rec.reset()

    assert rec.provenance() == {
        "stage_events": [],
        "n_tool_calls": {},
        "by_guardrail_layer": {},
    }


def test_two_recorders_never_share_state():
    """The eval harness serves several graphs at once, so a module-global
    accumulator would interleave two turns into one unreadable record."""
    a, b = StageRecorder(), StageRecorder()
    with a.stage(Stage.route):
        with b.stage(Stage.retrieve):
            b.count_tool_call("search_corpus")

    assert [e["stage"] for e in a.provenance()["stage_events"]] == ["route"]
    assert [e["stage"] for e in b.provenance()["stage_events"]] == ["retrieve"]
    assert a.provenance()["n_tool_calls"] == {}
    assert b.provenance()["n_tool_calls"] == {"search_corpus": 1}


def test_tool_calls_are_counted_by_name():
    rec = StageRecorder()
    for name in ("search_corpus", "search_corpus", "run_query"):
        rec.count_tool_call(name)
    assert rec.provenance()["n_tool_calls"] == {"search_corpus": 2, "run_query": 1}


def test_a_layer_that_ran_and_blocked_nothing_is_zero_not_absent():
    """Absent means "never ran" (L4 is skipped without a retrieval scope); 0 means
    "ran, blocked nothing". Collapsing them reports a confident zero for a layer
    nobody executed."""
    rec = StageRecorder()
    rec.guardrail_layer(GuardrailLayer.syntax, True)
    rec.guardrail_layer(GuardrailLayer.term_semantics, False)
    rec.guardrail_layer(GuardrailLayer.term_semantics, False)

    counters = rec.provenance()["by_guardrail_layer"]
    assert counters == {"syntax": 0, "term_semantics": 2}
    assert "cost_estimate" not in counters


def test_the_emitter_resets_the_recorder_with_the_turn():
    """One owner, one lifecycle: a recorder that survives the turn boundary bills
    the next turn for the last one's latency."""
    rec = StageRecorder()
    stream = GovEventStream(None, stages=rec)
    with rec.stage(Stage.route):
        pass
    stream.reset()
    assert rec.provenance()["stage_events"] == []


def test_final_stamps_the_records_onto_the_answer():
    rec = StageRecorder()
    stream = GovEventStream(None, stages=rec)
    rec.count_tool_call("run_query")
    stamped = stream.final(refusal(escalation="nope"))

    assert stamped.provenance["n_tool_calls"] == {"run_query": 1}
    assert stamped.provenance["by_guardrail_layer"] == {}
    assert stamped.provenance["stage_events"] == []


# --------------------------------------------------------------------------- #
# guardrails.check() observation — must never touch a verdict
# --------------------------------------------------------------------------- #

_ALLOWED = {"s.t.a"}


def _observed(sql: str, **kw):
    seen: list[tuple[str, bool]] = []
    verdict = check(
        sql,
        allowed_columns=set(_ALLOWED),
        hard_block_suspect=True,
        default_schema="s",
        on_layer=lambda layer, passed: seen.append((layer.value, passed)),
        **kw,
    )
    return verdict, seen


def test_check_reports_each_layer_that_ran_and_stops_at_the_failure():
    verdict, seen = _observed("DROP TABLE t")
    assert not verdict.passed
    assert verdict.failed_layer is GuardrailLayer.policy_blacklist
    # Nothing after the deciding layer ran, so nothing after it reports.
    assert seen == [("syntax", True), ("policy_blacklist", False)]


def test_a_skipped_layer_reports_nothing_at_all():
    """L4 is skipped when the caller passes no retrieval scope. It must not appear
    as a layer that ran and passed."""
    _verdict, seen = _observed("SELECT a FROM t")
    assert [layer for layer, _ in seen] == [
        "syntax",
        "policy_blacklist",
        "ast_column_allowlist",
        "cost_estimate",
    ]
    _verdict, scoped = _observed("SELECT a FROM t", allowed_tables=frozenset({"s.t"}))
    assert "term_semantics" in [layer for layer, _ in scoped]


def test_an_observer_that_raises_cannot_change_the_verdict():
    """A governance regression here would be far worse than a missing metric, so
    the observer gets no veto over the safety path."""

    def boom(_layer, _passed):
        raise RuntimeError("metrics sink down")

    common = dict(allowed_columns=set(_ALLOWED), hard_block_suspect=True, default_schema="s")
    for sql in ("SELECT a FROM t", "DROP TABLE t", "SELECT b FROM t"):
        plain = check(sql, **common)
        observed = check(sql, on_layer=boom, **common)
        assert (observed.passed, observed.failed_layer, observed.reason) == (
            plain.passed,
            plain.failed_layer,
            plain.reason,
        )


# --------------------------------------------------------------------------- #
# Durable persistence (H11 Tier A: counts and timings, no content)
# --------------------------------------------------------------------------- #


def test_strip_stage_events_keeps_numbers_and_drops_every_string():
    events = [
        {
            "stage": "guardrail",
            "status": "ok",
            "ms": 1.25,
            "detail": {"passed": False, "rows": 3, "failed_layer": "term_semantics"},
        }
    ]
    assert strip_stage_events_for_log(events) == [
        {
            "stage": "guardrail",
            "status": "ok",
            "ms": 1.25,
            "detail": {"passed": False, "rows": 3},
        }
    ]


def test_strip_stage_events_keeps_not_measured_as_none():
    assert strip_stage_events_for_log(None) is None
    assert strip_stage_events_for_log([]) == []


def test_a_content_carrying_detail_never_reaches_the_metadata_record(tmp_path):
    """``detail`` is free-form at the source, so the durable projection cannot
    trust it by key name — a later ``detail["query"]`` would put the user's words
    into a metadata-only log."""
    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
        log_full_content=False,
    )
    ans = refusal(
        escalation="nope",
        provenance={
            "stage_events": [
                {
                    "stage": "search_corpus",
                    "status": "ok",
                    "ms": 4.0,
                    "detail": {"query": "what is the secret revenue", "n_hits": 2},
                }
            ]
        },
    )
    ctx = FinalizeCtx(settings=settings, run_id="r", thread_id="t", n_human=1)
    stamped = finalize_and_log(ans, ctx=ctx)
    rec = build_metadata_record(stamped, ctx=ctx, provenance=stamped.provenance)

    assert "secret revenue" not in str(rec)
    assert rec["stage_events"] == [
        {"stage": "search_corpus", "status": "ok", "ms": 4.0, "detail": {"n_hits": 2}}
    ]


def test_unmeasured_instrumentation_is_null_not_zero(tmp_path):
    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    stamped = finalize_and_log(
        refusal(escalation="nope"),
        ctx=FinalizeCtx(settings=settings, run_id="r", thread_id="t", n_human=1),
    )
    for key in _INSTRUMENTATION_KEYS:
        assert key in stamped.provenance, key
        assert stamped.provenance[key] is None, key


# --------------------------------------------------------------------------- #
# End to end over a scripted trajectory
# --------------------------------------------------------------------------- #


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def settings(tmp_path):
    return replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def _repair_trajectory():
    return [
        ai_tool_turn("search_corpus", {"query": "total revenue"}, "c0"),
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        # attempt 1: an unlicensed table → an L4 term_semantics block (repairable)
        ai_tool_turn("run_query", {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'}, "c2"),
        # attempt 2: the licensed table → passes
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c3",
        ),
        AIMessage(content="done"),
    ]


@pytest.fixture
def served(corpus, bird_gateway, settings, identity):
    return answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="stage-metrics",
        model=FakeToolModel(responses=_repair_trajectory()),
    )


def test_exploration_tool_calls_are_counted_even_though_the_ledger_omits_them(served):
    """Blind spot #1: ``search_corpus``/``inspect_schema`` dominate a turn's tool
    calls and get no ledger entry, so before this counter no artifact could answer
    "how many searches did this turn take". The ledger must stay narrow — widening
    the audit record would widen what claims to be governed."""
    prov = served.provenance
    assert prov["n_tool_calls"] == {
        "search_corpus": 1,
        "inspect_schema": 1,
        "run_query": 2,
    }
    actions = [e.get("action") for e in prov.get("governance_ledger") or []]
    assert actions == ["run_query", "run_query"]


def test_the_turn_reports_a_timing_for_every_stage_it_ran(served):
    events = served.provenance["stage_events"]
    by_stage = {e["stage"] for e in events}
    assert {
        "route",
        "cache",
        "schema_pick",
        "retrieve",
        "assemble",
        "guardrail",
        "execute",
        "agent_core",
        "narrate",
    } <= by_stage
    assert all(e["status"] in ("ok", "skipped", "error") for e in events)
    assert all(e["ms"] is None or e["ms"] >= 0 for e in events)
    # Records land in COMPLETION order, so a nested stage precedes its parent —
    # which is also why the ms values are a tree and must not be summed.
    order = [e["stage"] for e in events]
    assert order.index("retrieve") < order.index("assemble")
    assert order.index("assemble") < order.index("agent_core")
    # A single-schema corpus never routes: recorded as skipped with no duration,
    # not omitted (which would read as a build that cannot measure the pick).
    pick = next(e for e in events if e["stage"] == "schema_pick")
    assert (pick["status"], pick["ms"]) == ("skipped", None)


def test_the_blocking_guardrail_layer_is_countable(served):
    """Blind spot #8: which layer blocks most often was previously unanswerable."""
    counters = served.provenance["by_guardrail_layer"]
    assert counters["term_semantics"] == 1  # attempt 1's L4 block
    assert counters["syntax"] == 0  # ran twice, blocked nothing
    assert counters["cost_estimate"] == 0


def test_the_guardrail_stage_carries_the_deciding_layer(served):
    guardrails = [e for e in served.provenance["stage_events"] if e["stage"] == "guardrail"]
    assert len(guardrails) == 2
    assert guardrails[0]["detail"] == {
        "action": "run_query",
        "passed": False,
        "failed_layer": "term_semantics",
    }
    assert guardrails[1]["detail"]["passed"] is True


def test_no_cache_configured_leaves_cache_hit_unmeasured(served):
    """A ``False`` here would report a miss on a lookup the turn never made."""
    assert served.provenance["cache_hit"] is None


def test_a_configured_cache_records_a_miss_as_a_measured_false(
    corpus, bird_gateway, settings, identity
):
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="stage-metrics-cache",
        model=FakeToolModel(responses=_repair_trajectory()),
        cache=SqlCache(HashingEmbedder()),
    )
    assert ans.provenance["cache_hit"] is False


def test_the_durable_run_log_keeps_the_counts_and_timings(served, settings):
    """Blind spot #2: a deployment not running the eval harness had no durable
    record of its own routing / retrieval / cache / tool-call behaviour."""
    rec = load_run_record(served.provenance["turn_id"], settings)
    assert rec is not None
    assert rec["n_tool_calls"] == {"search_corpus": 1, "inspect_schema": 1, "run_query": 2}
    assert rec["by_guardrail_layer"]["term_semantics"] == 1
    assert rec["cache_hit"] is None  # no cache configured for this turn
    assert rec["attempts"] == 2
    assert {e["stage"] for e in rec["stage_events"]} >= {"route", "agent_core", "execute"}


def test_a_stage_that_ends_the_turn_still_reports_its_own_timing(
    corpus, bird_gateway, settings, identity, monkeypatch
):
    """``assemble`` mints the missing-edge refusal itself, so its record did not
    exist yet when the answer was stamped. Losing it would drop ``assemble`` from
    exactly the turns that stopped in ``assemble`` — a mean over the records would
    then silently describe only the turns that got further.

    The refusal is forced here (the D15 logic itself is covered in
    test_missing_edge.py); what is under test is the instrumentation seam.
    """
    monkeypatch.setattr(
        "governed_bi.analyst.agent.detect_missing_join_path",
        lambda *_a, **_k: MissingJoinPath(
            table_ids=frozenset({TXN}), schemas=frozenset({"beer_factory"}), reason="forced"
        ),
    )
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="stage-metrics-edge",
        model=None,
    )

    assert ans.provenance["refused_by"] == "missing_edge"
    stages_seen = [e["stage"] for e in ans.provenance["stage_events"]]
    assert "assemble" in stages_seen
    assert "retrieve" in stages_seen


class _ExplodingModel(FakeToolModel):
    """A model call that raises — the shape of the ``NameError`` that sat in the
    serve path surfacing only as ``refused_by="model_error"``."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        raise RuntimeError("model down")


def test_a_crash_inside_agent_core_is_attributed_to_agent_core(
    corpus, bird_gateway, settings, identity
):
    """The rails fail closed by degrading a crash to a refusal, which is correct —
    and is exactly why the crash needs a stage of its own: without it the turn is
    indistinguishable from a governed refusal."""
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="stage-metrics-crash",
        model=_ExplodingModel(responses=[AIMessage(content="unused")]),
    )

    assert ans.provenance["refused_by"] == "model_error"
    core = [e for e in ans.provenance["stage_events"] if e["stage"] == "agent_core"]
    assert core and core[-1]["status"] == "error"
    assert core[-1]["detail"]["error_type"] == "RuntimeError"
    # The stages before it still report ok, so the failure localises.
    assert {e["status"] for e in ans.provenance["stage_events"] if e["stage"] == "retrieve"} == {
        "ok"
    }
