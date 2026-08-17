"""GET /trust-loop/metrics (utku-ai-trust-loop-plan.md, task C) -- does the loop turn, and where
does it stop. Same fixture shapes as ``test_raised_route.py`` (session builders) and
``test_audit_surface.py`` (turn-log fixture + record helper), since this route shares both
routers' dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

_DB_ID = "beer"


def _session(tmp_path: Path, *, corpus_root: Path | None) -> Any:
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id=_DB_ID, run_id="r",
        corpus_root=corpus_root,
    )


def _turn_log(monkeypatch, tmp_path: Path) -> Any:
    """Redirect the turn log to ``tmp_path``. Call before any log write in a test -- see
    ``test_raised_route.py``'s own ordering note for why."""
    from governed_bi.api import trace_store

    monkeypatch.setattr(trace_store, "TURN_LOG_DIR", tmp_path / "serve")
    return trace_store


def _client(trace_store: Any, session: Any):
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    return TestClient(routes.make_app(session, None, trace_store))


def _record(turn_id: str, *, outcome: str, db_id: str = _DB_ID, **extra: Any) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "run_id": "r-1",
        "thread_id": "th-1",
        "question_id": "q-1",
        "db_id": db_id,
        "outcome": outcome,
        "terminal_reason": None,
        "schemas": [],
        "licensed": [],
        "generated_sql": None,
        "execution": {"terminal": outcome, "attempts": []},
        **extra,
    }


def _log_turn(trace_store: Any, turn_id: str, *, outcome: str, db_id: str = _DB_ID, **extra: Any) -> None:
    trace_store.append_turn(
        _record(turn_id, outcome=outcome, db_id=db_id, **extra),
        question="does not matter for this route",
        answer_text="does not matter for this route",
    )


# ── the empty case: 0/0/0/0 must read as "measured, and zero" ────────────────────────────────


def test_no_corpus_root_leaves_ledger_dependent_sections_unmeasured(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    client = _client(trace_store, _session(tmp_path, corpus_root=None))

    body = client.get("/trust-loop/metrics").json()
    assert body["entrances"] is None
    assert body["approved_rules"] is None
    assert body["retrieved"] is None
    assert body["funnel"][1:] == [None, None, None]
    # Refusals still come from the turn log, which needs no corpus_root at all.
    assert body["refusals"]["total"] == 0


def test_a_corpus_root_with_nothing_in_it_reports_real_zeros_not_none(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    body = client.get("/trust-loop/metrics").json()
    assert body["entrances"] == {"refusal_clarifications": 0, "reports": 0, "total": 0}
    assert body["approved_rules"] == {
        "by_source": {}, "reader_initiated_total": 0, "reader_initiated_ids": [],
    }
    assert body["retrieved"]["n_retrieved"] == 0
    assert body["funnel"] == [0, 0, 0, 0]


# ── counter 1: refusals, by reason, scoped to this session's db_id ───────────────────────────


def test_refusals_are_grouped_by_terminal_reason(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", outcome="refused", terminal_reason="no_schema_matched")
    _log_turn(trace_store, "t2", outcome="refused", terminal_reason="no_schema_matched")
    _log_turn(trace_store, "t3", outcome="refused", terminal_reason="guard")
    _log_turn(trace_store, "t4", outcome="answered")
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    refusals = client.get("/trust-loop/metrics").json()["refusals"]
    assert refusals["total"] == 3
    assert refusals["by_reason"] == {"no_schema_matched": 2, "guard": 1}


def test_capped_and_crashed_outcomes_are_not_counted_as_refusals(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", outcome="capped", terminal_reason="attempt_cap")
    _log_turn(trace_store, "t2", outcome="crashed", terminal_reason="model_error")
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    refusals = client.get("/trust-loop/metrics").json()["refusals"]
    assert refusals["total"] == 0


def test_refusals_from_a_different_db_id_are_excluded(monkeypatch, tmp_path: Path) -> None:
    """The turn log is process-wide and this repo's own copy holds turns from five different
    corpora in one file -- a session's loop is scoped to its own ``db_id``."""
    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", outcome="refused", terminal_reason="no_schema_matched", db_id=_DB_ID)
    _log_turn(trace_store, "t2", outcome="refused", terminal_reason="no_schema_matched", db_id="a_different_corpus")
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    refusals = client.get("/trust-loop/metrics").json()["refusals"]
    assert refusals["total"] == 1


# ── counter 2: reader entrances, both channels, no double count ──────────────────────────────


def test_entrances_combine_refusal_clarifications_and_reports_without_double_counting(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )
    from governed_bi.curator.feedback import FeedbackRecord, write_feedback

    trace_store = _turn_log(monkeypatch, tmp_path)
    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="refusal-1", scope="refusal:1", question="What does popular mean?",
                status=ClarificationRecordStatus.answered, answer="Highest downloads.",
                source="refusal", basis="data_definition",
            ),
            # Not this reader's own entrance -- must not be counted.
            ClarificationRecord(
                id="live-1", scope="live:1", question="what does active mean?",
                status=ClarificationRecordStatus.answered, answer="90 days",
                source="live_chat",
            ),
        ],
    )
    write_feedback(
        tmp_path,
        [FeedbackRecord(id="f1", turn_id="t1", question="q?", answer_text="a.")],
    )
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    entrances = client.get("/trust-loop/metrics").json()["entrances"]
    assert entrances == {"refusal_clarifications": 1, "reports": 1, "total": 2}


# ── counter 3: approved rules, by source, and the unstamped-row honesty check ────────────────


def test_approved_rules_are_bucketed_by_audit_extra_source(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.curator.clarification import draft_from_clarification
    from governed_bi.curator.feedback import _report_draft

    trace_store = _turn_log(monkeypatch, tmp_path)

    refusal_draft = draft_from_clarification("What does popular mean?", "Highest downloads.", schema=_DB_ID)
    submit_draft(tmp_path, refusal_draft, namespace=_DB_ID, extra={"source": "refusal"})
    approve_draft(tmp_path, refusal_draft.id)

    feedback_draft = _report_draft("How many apps?", "8,512.", schema=_DB_ID)
    submit_draft(tmp_path, feedback_draft, namespace=_DB_ID, extra={"source": "feedback"})
    approve_draft(tmp_path, feedback_draft.id)

    # Certified, but from an entrance this loop does not count (an admin's own offline review).
    curator_draft = draft_from_clarification("What is a row?", "One app listing.", schema=_DB_ID)
    submit_draft(tmp_path, curator_draft, namespace=_DB_ID, extra={"source": "curator"})
    approve_draft(tmp_path, curator_draft.id)

    # Still `proposed` -- an approved-rules count must not include it.
    unapproved_draft = draft_from_clarification("What is price?", "playstore.Price.", schema=_DB_ID)
    submit_draft(tmp_path, unapproved_draft, namespace=_DB_ID, extra={"source": "refusal"})

    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))
    approved = client.get("/trust-loop/metrics").json()["approved_rules"]

    assert approved["by_source"] == {"curator": 1, "feedback": 1, "refusal": 1}
    assert approved["reader_initiated_total"] == 2
    assert set(approved["reader_initiated_ids"]) == {refusal_draft.id, feedback_draft.id}


def test_an_unstamped_certified_asset_is_honest_about_not_knowing_its_source(
    monkeypatch, tmp_path: Path
) -> None:
    """Deliberately does not reuse ``/corpus/assumptions``'s ``"live_chat"`` fallback -- see
    ``_approved_rule_counts``'s own docstring for why mislabelling an unknown row would inflate
    a bucket this route's whole job is to report honestly."""
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.curator.clarification import draft_from_clarification

    trace_store = _turn_log(monkeypatch, tmp_path)

    draft = draft_from_clarification("What does popular mean?", "Highest downloads.", schema=_DB_ID)
    submit_draft(tmp_path, draft, namespace=_DB_ID)  # no `extra` at all -- predates task C-0
    approve_draft(tmp_path, draft.id)

    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))
    approved = client.get("/trust-loop/metrics").json()["approved_rules"]

    assert approved["by_source"] == {"unstamped": 1}
    assert approved["reader_initiated_total"] == 0
    assert "live_chat" not in approved["by_source"]


# ── counter 4: retrieved -- honest about what facet_hits can and cannot claim ─────────────────


def test_a_certified_reader_initiated_rule_hit_as_a_facet_term_candidate_is_retrieved(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.curator.clarification import draft_from_clarification

    trace_store = _turn_log(monkeypatch, tmp_path)

    draft = draft_from_clarification("What does popular mean?", "Highest downloads.", schema=_DB_ID)
    submit_draft(tmp_path, draft, namespace=_DB_ID, extra={"source": "refusal"})
    approve_draft(tmp_path, draft.id)

    _log_turn(
        trace_store, "t-later", outcome="answered",
        facet_hits={"facet_term": {"hits": [{"asset_id": draft.id, "score": 0.9}]}},
    )
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    body = client.get("/trust-loop/metrics").json()
    assert body["retrieved"]["n_retrieved"] == 1
    assert body["retrieved"]["retrieved_rule_ids"] == [draft.id]
    # No clarification-ledger row was written here, only the asset -- entrances stays 0.
    assert body["funnel"] == [0, 0, 1, 1]


def test_an_approved_rule_never_seen_again_is_a_real_zero(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.drafts import approve_draft, submit_draft
    from governed_bi.curator.clarification import draft_from_clarification

    trace_store = _turn_log(monkeypatch, tmp_path)

    draft = draft_from_clarification("What does popular mean?", "Highest downloads.", schema=_DB_ID)
    submit_draft(tmp_path, draft, namespace=_DB_ID, extra={"source": "refusal"})
    approve_draft(tmp_path, draft.id)
    # A turn logged, but its facet hits never mention this rule.
    _log_turn(trace_store, "t-later", outcome="answered", facet_hits={})

    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))
    retrieved = client.get("/trust-loop/metrics").json()["retrieved"]
    assert retrieved["n_retrieved"] == 0
    assert retrieved["retrieved_rule_ids"] == []


def test_the_scan_bound_is_reported_and_flagged_when_reached(monkeypatch, tmp_path: Path) -> None:
    trace_store = _turn_log(monkeypatch, tmp_path)
    _log_turn(trace_store, "t1", outcome="refused", terminal_reason="no_schema_matched")
    _log_turn(trace_store, "t2", outcome="refused", terminal_reason="no_schema_matched")
    client = _client(trace_store, _session(tmp_path, corpus_root=tmp_path))

    body = client.get("/trust-loop/metrics?turn_scan_limit=1").json()
    assert body["refusals"]["scan_bound"] == 1
    assert body["refusals"]["turns_scanned"] == 1
    assert body["refusals"]["possibly_truncated"] is True
