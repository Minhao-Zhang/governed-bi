"""The Setup Wizard's severity / audience / dependency gating, on the wire
(detent-ai-setup-wizard-gap-model.md), plus the A-pair end-to-end scenario that exercises it.

Split out of ``test_elicitation_routes.py`` by the 1000-line cap (ADR 0005 §6), which was
forcing the timing rather than the seam: that file's tests are the route surface itself
(generate, candidates, composition, the D join-path follow-up, folding an answer into the
corpus); this file's concern is the gap-model's blocking semantics -- a value question waits
for its cluster question, answering a blocked candidate stamps the unmet prerequisite it was
waiting on, and the business/engineering A pair unblocks and quotes across HTTP the same way.
The two are related but no test in either section reads a helper or a route the other file
does not also need on its own terms.

Shares its fixtures with ``test_elicitation_routes.py`` via
``elicitation_fixtures.py`` -- see that module's docstring for why the fixtures moved there
instead of into either test file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elicitation_fixtures import (  # noqa: E402
    _by_scope,
    _client,
    _join_followups,
    _session_with_schema,
)

pytestmark = needs("D")


# ── severity / audience / dependency gating on the wire (detent-ai-setup-wizard-gap-model.md) ──


def _blocked_pair() -> list[Any]:
    """A prerequisite and the candidate that must wait for it, **hand-written**.

    Kept seeded for the two cases the generated path cannot produce on demand: a ``C`` record
    (which names no column, so no cluster can ever gate it) standing in for "any blocked
    candidate", and the dangling/unwarranted-answer states below. The generated equivalent — a
    real near-duplicate cluster question gating a real value-mapping question — is exercised
    against the detectors' own output in the two tests after these.
    """
    from governed_bi.curator.clarifications import ClarificationRecord

    return [
        ClarificationRecord(
            id="q_blocker", scope="s_blocker", question="Which of the two is authoritative?",
            source="elicitation_wizard", category="D", severity="T1", audience="data",
        ),
        ClarificationRecord(
            id="q_dependent", scope="s_dependent", question="What month does your year start?",
            source="elicitation_wizard", category="C", severity="T2", audience="business",
            blocked_by=("q_blocker",),
        ),
    ]


def test_candidates_expose_severity_audience_and_the_dependency_fields(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    assert all(row["severity"] and row["audience"] for row in generated), generated

    rows = client.get("/elicitation/candidates").json()
    assert {row["audience"] for row in rows} == {"business", "data"}
    # T1 from the structural cluster, T2 from the keyword categories, T4 from the description
    # sweep. Every tier the two generators can emit on this schema reaches the wire.
    assert {row["severity"] for row in rows} == {"T1", "T2", "T4"}
    assert {row["severity"] for row in rows if row["blocked"]} == {"T2"}


def test_a_generated_value_question_on_a_contested_column_waits_for_its_cluster_question(
    monkeypatch, tmp_path: Path
) -> None:
    """The doc's hard constraint, on real detector output rather than a seeded record.

    ``country_code`` and ``country_code_alt`` disagree on 37 of 200 rows, so one of them is a
    decoy and a value checklist cannot show which. Both value-mapping questions therefore wait on
    the cluster question that decides it — and the cluster question itself waits on nothing, or
    the wizard would deadlock.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    rows = {r["scope"]: r for r in client.get("/elicitation/candidates").json()}

    cluster = rows["elicitation:duplicate:orders.country_code|country_code_alt"]
    assert (cluster["severity"], cluster["audience"]) == ("T1", "data")
    assert cluster["blocked"] is False and cluster["blocked_by"] == []
    assert "37 of 200 rows" in cluster["question"]

    for column in ("country_code", "country_code_alt"):
        value_map = rows[f"elicitation:valuemap:orders.{column}"]
        assert value_map["blocked"] is True, value_map
        assert value_map["blocked_by"] == [cluster["id"]]
    # A question about an uncontested column is untouched by the gate.
    assert rows["elicitation:exclusion:orders.review_status"]["blocked"] is False


def test_answering_the_generated_cluster_question_unblocks_the_value_questions(
    monkeypatch, tmp_path: Path
) -> None:
    """End to end on real data: generate, answer the T1, watch the T2s become answerable.

    Previously demonstrable only with a hand-seeded prerequisite, because no detector emitted one.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    rows = {r["scope"]: r for r in client.get("/elicitation/candidates").json()}
    cluster = rows["elicitation:duplicate:orders.country_code|country_code_alt"]

    answer = client.post(
        f"/clarifications/{cluster['id']}/answer",
        json={"choice_id": "orders.country_code"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["unmet_prerequisites_at_answer"] == []
    # The picked choice survives composition and reaches the corpus. Found live: the D branch
    # returned freeform only, so a column-picker answer composed "" and folded nothing.
    assert answer.json()["answer"] == "orders.country_code is authoritative."
    assert answer.json()["converted_to_corpus"] is True

    after = {r["scope"]: r for r in client.get("/elicitation/candidates").json()}
    assert after["elicitation:valuemap:orders.country_code"]["blocked"] is False
    assert after["elicitation:valuemap:orders.country_code_alt"]["blocked"] is False
    # The dependency is still recorded — unblocked means "answerable now", not "never gated".
    assert after["elicitation:valuemap:orders.country_code"]["blocked_by"] == [cluster["id"]]


def test_answering_a_blocked_value_question_first_records_the_missing_warrant(
    monkeypatch, tmp_path: Path
) -> None:
    """Not refused, and the reason is a pilot constraint: a DBA with no business counterpart must
    be able to answer the engineering half standalone. What the answer must not do is claim a
    warrant it does not have — so the still-open cluster question is stamped on it, now from real
    detector output.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    rows = {r["scope"]: r for r in client.get("/elicitation/candidates").json()}
    value_map = rows["elicitation:valuemap:orders.country_code_alt"]
    cluster = rows["elicitation:duplicate:orders.country_code|country_code_alt"]

    body = client.post(
        f"/clarifications/{value_map['id']}/answer", json={"choice_ids": ["US", "CA"]}
    ).json()
    assert body["status"] == "answered"
    assert body["unmet_prerequisites_at_answer"] == [cluster["id"]]


def test_a_candidate_with_an_open_prerequisite_is_reported_as_blocked(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications

    write_clarifications(tmp_path, _blocked_pair())
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    rows = {row["id"]: row for row in client.get("/elicitation/candidates").json()}

    assert rows["q_blocker"]["blocked"] is False
    assert rows["q_dependent"]["blocked"] is True
    assert rows["q_dependent"]["blocked_by"] == ["q_blocker"]


def test_answering_the_prerequisite_unblocks_the_candidate_that_waited(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import write_clarifications

    write_clarifications(tmp_path, _blocked_pair())
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/clarifications/q_blocker/answer", json={"answer": "orders.id = payments.order_id"})

    rows = {row["id"]: row for row in client.get("/elicitation/candidates").json()}
    assert rows["q_dependent"]["blocked"] is False


def test_answering_a_blocked_candidate_stamps_the_unmet_prerequisite_on_it(monkeypatch, tmp_path: Path) -> None:
    """Deliberately **not** refused. The doc requires a DBA with no business counterpart to be
    able to answer the engineering half standalone; what the answer must not do is claim a
    warrant it does not have, so the still-open prerequisite is recorded on the record for a
    later phase to land ``draft`` + ``reliability: suspect`` on instead of ``certified``."""
    from governed_bi.curator.clarifications import write_clarifications

    write_clarifications(tmp_path, _blocked_pair())
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    response = client.post("/clarifications/q_dependent/answer", json={"answer": "4"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["unmet_prerequisites_at_answer"] == ["q_blocker"]


def test_answering_an_unblocked_candidate_stamps_an_empty_warrant(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    c_rec = next(row for row in generated if row["category"] == "C")

    body = client.post(f"/clarifications/{c_rec['id']}/answer", json={"answer": "4"}).json()
    assert body["unmet_prerequisites_at_answer"] == []


# ── the A pair, end to end over HTTP ─────────────────────────────────────────────────────────


def test_the_business_half_unblocks_the_engineering_half_and_is_quoted_into_it(
    monkeypatch, tmp_path: Path
) -> None:
    """The whole chain the split exists for, driven the way an admin drives it.

    A-eng is written at scan time and shown as waiting; answering A-biz clears the edge and
    restates A-eng's question with the definition it was waiting for. The record's id never
    changes — it is derived from its scope — so the ``blocked_by`` edge pointing at A-biz stays
    valid through the rewrite.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    biz = _by_scope(generated, "elicitation:term:amount")
    eng = _by_scope(generated, "elicitation:termcolumn:amount")
    assert eng["blocked_by"] == [biz["id"]]

    before = {r["id"]: r for r in client.get("/elicitation/candidates").json()}
    assert before[eng["id"]]["blocked"] is True
    assert before[biz["id"]]["blocked"] is False

    picked = next(c["id"] for c in biz["choices"] if c["id"] == "orders.total_amount")
    label = next(c["label"] for c in biz["choices"] if c["id"] == picked)
    answered = client.post(f"/clarifications/{biz['id']}/answer", json={"choice_id": picked})
    assert answered.status_code == 200, answered.text
    assert answered.json()["answer"] == f"In business terms, 'amount' means {label}."

    after = {r["id"]: r for r in client.get("/elicitation/candidates").json()}
    assert after[eng["id"]]["blocked"] is False
    assert after[eng["id"]]["question"].startswith("Business defines 'amount' as ")
    assert label in after[eng["id"]]["question"]
    assert after[eng["id"]]["blocked_by"] == [biz["id"]], "the edge survives the rewrite"

    # Answering A-biz binds no column, so it mints no join follow-up: only the engineering half
    # picks a column, and only a picked column can land on an unexpected table.
    assert not _join_followups(client.get("/elicitation/candidates").json())


def test_the_engineering_half_answered_with_its_prerequisite_lands_an_approvable_draft(
    monkeypatch, tmp_path: Path
) -> None:
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    biz = _by_scope(generated, "elicitation:term:amount")
    eng = _by_scope(generated, "elicitation:termcolumn:amount")

    client.post(f"/clarifications/{biz['id']}/answer", json={"choice_id": "orders.total_amount"})
    body = client.post(
        f"/clarifications/{eng['id']}/answer", json={"choice_id": "orders.total_amount"}
    ).json()
    assert body["unmet_prerequisites_at_answer"] == []

    assets, _ = load(tmp_path, schemas=["shop"])
    eng_draft = next(a for a in assets if "maps to orders.total_amount" in a.summary)
    assert eng_draft.audit.provenance.status.value == "proposed"
    assert "Unverified" not in eng_draft.summary


def test_the_engineering_half_answered_alone_lands_a_draft_nobody_can_certify(
    monkeypatch, tmp_path: Path
) -> None:
    """Power Kiosk's ordinary case: a DBA, no named business-domain expert. The answer is
    accepted — the API deliberately does not refuse a blocked record, because refusing it would
    make one of the two pilots unable to use the wizard at all — and it is recorded as weaker
    evidence than the same answer given with a business definition behind it."""
    import pytest

    from governed_bi.corpus.drafts import DraftNotPending, approve_draft
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    eng = _by_scope(generated, "elicitation:termcolumn:amount")

    body = client.post(
        f"/clarifications/{eng['id']}/answer", json={"choice_id": "orders.total_amount"}
    ).json()
    assert body["status"] == "answered", "the answer is taken, not refused"
    assert body["unmet_prerequisites_at_answer"] == [
        _by_scope(generated, "elicitation:term:amount")["id"]
    ]

    (draft,) = load(tmp_path, schemas=["shop"])[0]
    assert draft.audit.provenance.status.value == "draft"
    assert "Unverified" in draft.summary
    with pytest.raises(DraftNotPending):
        approve_draft(tmp_path, draft.id)


def test_the_two_halves_say_only_what_this_scan_measured(monkeypatch, tmp_path: Path) -> None:
    """The grounding rule, pinned as text an admin actually sees.

    ``detent-ai-setup-wizard-gap-model.md``'s A-biz example offers "after discounts" and "after
    refunds and card fees". Neither is derivable from a column name, a type, a value sample or a
    row count — they are facts about a company's commercial arrangements — so neither is
    generated. What is left is where the value is recorded and how it varies, both read off this
    database in this scan.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    biz = _by_scope(generated, "elicitation:term:amount")
    eng = _by_scope(generated, "elicitation:termcolumn:amount")
    labels = {c["id"]: c["label"] for c in biz["choices"]}

    # ``total_amount`` repeats across the fixture's 200 rows; ``revenue_amount`` does not. Both
    # numbers come from the governed ``count(*)/count(distinct)`` this route now issues.
    assert labels["orders.total_amount"] == (
        "the 'total amount' recorded in your orders data — 48 different values across 200 records"
    )
    assert labels["payments.revenue_amount"] == (
        "the 'revenue amount' recorded in your payments data — a separate value on every one of "
        "its 200 records"
    )
    # The engineering half carries the same counts against the identifier, plus the type.
    eng_labels = {c["id"]: c["label"] for c in eng["choices"]}
    assert eng_labels["orders.total_amount"] == "orders.total_amount — decimal; 200 rows, 48 distinct"
