"""The seam: serve provenance -> eval solver meta -> the row and stage_events file.

Every hop of this chain is already tested in isolation, and that is exactly the
problem. ``tests/test_stage_metrics.py`` reads ``answer.provenance`` directly,
``tests/test_eval_arms_meta.py`` feeds the relay a hand-written provenance dict, and
``tests/test_datalake_stage_attribution.py`` feeds the driver a hand-written meta
dict. So if the serve path renamed ``stage_events``, all three would still pass and
the artifact would silently go empty — which is the same failure that already cost a
run: ``ledger_len`` was computed, relayed by nobody, and reached no row.

This test spans the whole chain with one real turn and no stubbed provenance, so a
key-name change anywhere in it fails here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.eval.run_datalake import _stage_event_rows
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn
from governed_bi.stages import Stage

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"

#: Keys the serve path must publish and the relay must carry. Naming them once, here,
#: is what makes a rename a test failure instead of an empty column in an artifact.
CONTRACT_KEYS = ("stage_events", "n_tool_calls", "by_guardrail_layer")


@pytest.fixture
def served(tmp_path):
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    try:
        settings = replace(
            Settings.for_env(Environment.dev),
            run_log_kind="sqlite",
            run_log_path=str(tmp_path / "runs.sqlite"),
        )
        yield answer_question_agent(
            "total revenue",
            Identity(user="dev", all_access=True),
            corpus=load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst(),
            gateway=Gateway(conn),
            settings=settings,
            session_id="seam",
            model=FakeToolModel(
                responses=[
                    ai_tool_turn("search_corpus", {"query": "total revenue"}, "c0"),
                    ai_tool_turn(
                        "run_query",
                        {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
                        "c1",
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    finally:
        conn.close()


def test_the_serve_path_publishes_every_contract_key(served):
    missing = [k for k in CONTRACT_KEYS if k not in served.provenance]
    assert not missing, (
        f"serve provenance is missing {missing}; the eval relay reads these by name, "
        "so a rename here empties the artifact without failing any per-layer test"
    )


def test_stage_events_carry_a_real_stage_name_and_a_real_duration(served):
    events = served.provenance["stage_events"]
    assert events, "a completed turn recorded no stage timings at all"
    known = {s.value for s in Stage}
    for event in events:
        assert event["stage"] in known, f"{event['stage']!r} is not a Stage member"
        assert event["status"] in {"ok", "error", "skipped"}
        if event["status"] != "skipped":
            # A real measurement, not a placeholder: 0.0 for every stage would mean
            # the timer never ran, which is indistinguishable from a fast pipeline
            # unless something asserts otherwise.
            assert isinstance(event["ms"], (int, float))
    assert any(e["ms"] > 0 for e in events if e["status"] != "skipped")


def test_a_real_turns_provenance_flattens_into_stage_event_rows(served):
    # The driver reads solver *meta*, and arms.py relays provenance into meta under
    # the same names — so passing provenance here exercises the driver against the
    # real producer's shape rather than a fixture's.
    rows = _stage_event_rows(
        served.provenance, question_id="q1", arm="curated", db_id="beer_factory"
    )
    assert len(rows) == len(served.provenance["stage_events"])
    assert {r["question_id"] for r in rows} == {"q1"}
    assert {r["arm"] for r in rows} == {"curated"}
    assert {r["db_id"] for r in rows} == {"beer_factory"}
    # Nothing in the flattened record may be None where the producer had a value.
    assert all(r["stage"] is not None and r["status"] is not None for r in rows)


def test_the_relay_carries_the_real_keys_not_just_matching_ones(served, monkeypatch):
    """Run the actual eval relay over the actual serve answer.

    ``agent_solver`` is the only bridge from provenance to a row. Stubbing the graph
    (rather than the provenance) keeps the answer object real, so this fails if the
    relay and the serve path ever disagree about a name.
    """
    from governed_bi.eval.arms import agent_solver

    monkeypatch.setattr(
        "governed_bi.analyst.agent.build_serve_rails",
        lambda **kwargs: type(
            "G", (), {"invoke": staticmethod(lambda state, config=None: {"answer": served})}
        )(),
    )
    solver = agent_solver(
        corpus=None,
        gateway=None,
        settings=Settings.for_env(Environment.dev),
        identity=None,
        model=None,
    )
    _sql, meta = solver.solve_with_meta("total revenue")

    for key in CONTRACT_KEYS:
        assert meta.get(key) == served.provenance.get(key), (
            f"{key} did not survive the relay verbatim"
        )
    # ledger_len is the field that was previously computed and dropped before disk.
    assert meta["ledger_len"] == len(served.provenance.get("governance_ledger") or [])
