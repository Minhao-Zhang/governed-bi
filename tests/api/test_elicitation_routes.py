"""POST /elicitation/generate, GET /elicitation/candidates — the Setup Wizard's HTTP surface
(DetentAI v1, ported), plus the category-aware composition + D join-path auto-follow-up wired
into the pre-existing ``POST /clarifications/{id}/answer`` (no new answer route -- every
category-tagged candidate is answered through the same route Phase 1a/1c already built).

The route runs **both** generators: ``curator/elicitation.py``'s keyword heuristic and
``curator/gaps.py``'s structural detectors, with the latter's near-duplicate output gating the
former's records. So this fixture carries a real decoy pair (``country_code`` /
``country_code_alt``, disagreeing row-wise) and a connector that answers the row-wise comparison,
because a fixture with no contested column cannot tell a wired dependency gate from an unwired
one — which is exactly how the gate stayed untested through two prior phases.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The fixtures live next door only because this file outgrew its length cap; both this file and
# test_the_setup_wizard_gap_model_gates_the_wire.py build the same corpus, connector and client.
from elicitation_fixtures import (  # noqa: E402
    _DB_VALUES,
    _by_scope,
    _client,
    _join_followups,
    _ScriptedConnector,
    _session_with_schema,
    _session_without_corpus_root,
)

pytestmark = needs("D")


# ── POST /elicitation/generate ──────────────────────────────────────────────────────────────


def test_generate_requires_a_corpus_root(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    response = client.post("/elicitation/generate")
    assert response.status_code == 409


def test_generate_produces_real_candidates_across_categories(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    response = client.post("/elicitation/generate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["n_generated"] == len(body["generated"])
    categories = {row["category"] for row in body["generated"]}
    # ``D`` is the structural near-duplicate cluster question (the doc's D row seen from the
    # column side); the keyword generator still never proposes a standalone D.
    assert categories == {"A", "C", "E", "B", "D"}, categories
    assert all(row["source"] == "elicitation_wizard" for row in body["generated"])
    assert all(row["status"] == "open" for row in body["generated"])

    from governed_bi.curator.clarifications import load_clarifications

    on_disk = load_clarifications(tmp_path)
    assert len(on_disk) == len(body["generated"])


def test_generate_reports_both_generators_and_their_coverage(monkeypatch, tmp_path: Path) -> None:
    """The structural detectors are **additive**: the keyword path's own candidates all survive.

    Pinned as a per-category count rather than as a set, because "the keyword generator still
    runs" is the regression this route's wiring could plausibly break — the reason it is wired at
    all is that the keyword path returns nothing on the German corpus, not that it returns
    nothing useful on an English one.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    body = client.post("/elicitation/generate").json()
    rows = body["generated"]

    # Keyword: three ambiguous terms (revenue/total/amount), one fiscal-year rule, one sentinel
    # exclusion, and one value map per categorical column including the decoy. Each term gets an
    # engineering question; only ``amount`` matches two columns, so only it also gets the
    # business one (the gap model's ``A″``: a single-choice picker is a forced answer).
    keyword_scopes = {r["scope"] for r in rows if r["scope"].split(":")[1] in
                      {"term", "termcolumn", "rule", "exclusion", "valuemap"}}
    assert len([s for s in keyword_scopes if ":termcolumn:" in s]) == 3, keyword_scopes
    assert [s for s in keyword_scopes if s.startswith("elicitation:term:")] == [
        "elicitation:term:amount"
    ], keyword_scopes
    assert "elicitation:rule:fiscal_year_start" in keyword_scopes
    assert "elicitation:exclusion:orders.review_status" in keyword_scopes
    assert len([s for s in keyword_scopes if ":valuemap:" in s]) == 2, keyword_scopes

    # Structural: one T1 cluster on the decoy pair, and the per-table description questions.
    structural = {r["scope"] for r in rows} - keyword_scopes
    assert "elicitation:duplicate:orders.country_code|country_code_alt" in structural
    assert {s for s in structural if s.startswith("elicitation:describe")} == {
        "elicitation:describetable:shop.orders", "elicitation:describecolumns:shop.orders",
        "elicitation:describetable:shop.payments", "elicitation:describecolumns:shop.payments",
    }

    by_detector = {c["detector"]: c for c in body["coverage"]}
    assert set(by_detector) == {
        "near_duplicate_disagreement", "join_path", "semantic_coverage", "low_confidence_asset",
    }
    assert by_detector["near_duplicate_disagreement"]["found"] == 1
    # The honest-report contract: a detector that found nothing still says what it looked at.
    assert by_detector["join_path"]["found"] == 0
    assert by_detector["join_path"]["considered"] == 1  # orders × payments
    assert by_detector["low_confidence_asset"]["found"] == 0
    assert "0 instances on any freshly-seeded corpus" in by_detector["low_confidence_asset"]["note"]


def test_generate_reports_a_ledger_row_for_every_governed_read(monkeypatch, tmp_path: Path) -> None:
    """Both generators issue governed statements, so the route owes the caller a verdict for each.
    The deleted ``Connector.sample_values`` path wrote none, which is what made
    ``guardrail_errors == 0`` hold vacuously for it."""
    connector = _ScriptedConnector()
    client = _client(monkeypatch, _session_with_schema(tmp_path, connector=connector))
    body = client.post("/elicitation/generate").json()

    # Every column of the fixture gets a value read now that a value-driven detector exists,
    # plus two name-alike column pairs, plus one row/distinct count for each of the two columns
    # whose *name* carries an ambiguous business term — the grain evidence category A's two
    # halves are built on. A capped distinct-value list can never say how many rows a column
    # has, so it is a second statement rather than a wider first one.
    comparisons = [s for s in connector.statements if "IS DISTINCT FROM" in s]
    value_reads = [s for s in connector.statements if "SELECT DISTINCT" in s]
    cardinalities = [
        s for s in connector.statements if "COUNT(*)" in s and "IS DISTINCT FROM" not in s
    ]
    assert len(comparisons) == 2, connector.statements
    assert len(value_reads) == 8, connector.statements
    assert len(cardinalities) == 2, connector.statements
    assert len(body["ledger"]) == 12, body["ledger"]
    assert all(row["path"] == "sample" and row["passed"] for row in body["ledger"])
    assert all(row["executed_sql"] for row in body["ledger"])
    assert any('"country_code"' in row["executed_sql"] for row in body["ledger"])
    # Row-wise, not a value-set read: the pair that made this necessary holds the identical value
    # set on both sides.
    assert any("IS DISTINCT FROM" in row["executed_sql"] for row in body["ledger"])


def test_b_and_e_offer_the_values_the_database_really_returned(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]

    b_rec = next(r for r in generated if r["category"] == "B")
    assert b_rec["target_column"] == "country_code"
    assert [c["id"] for c in b_rec["choices"]] == sorted(_DB_VALUES["country_code"])

    e_rec = next(r for r in generated if r["category"] == "E")
    assert e_rec["target_column"] == "review_status"
    assert "'pending'" in e_rec["question"], e_rec["question"]


def test_b_and_e_are_not_proposed_when_there_is_no_connector_to_read_through(
    monkeypatch, tmp_path: Path
) -> None:
    """``ColumnAsset.sample_values`` is populated on the gated columns in this fixture and is
    no longer consulted -- the field is empty on every live-seeded corpus, which is the bug.

    The near-duplicate detector goes silent for the same reason and the corpus-shape detectors do
    not: with no database there is no row-level evidence, and a near-duplicate *name* alone is
    ``created_at``/``updated_at``. What must not happen is the pair being reported anyway.

    **The ledger assertion inverted at the 2026-08-14 upstream merge, and this fork was wrong.**
    It read ``len(body["ledger"]) == 12`` with every row ``passed is False``, on the reasoning
    that "every refusal is still a governance decision the audit trail is owed". Upstream's
    ``test_a_wiring_failure_is_not_a_verdict`` refutes the premise: a missing connector is not a
    refusal at all, it is this deployment being unconfigured, and a ``r_not_a_read`` row files
    that against the *statement*. Concretely those twelve rows made an unconfigured scan
    indistinguishable in the ledger from a scan of a clean schema — twelve governed reads that
    were never sent. Zero rows is the honest count, and the empty ``duplicate`` scope below is
    what says the detector went silent rather than passing.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path, connector=None))
    body = client.post("/elicitation/generate").json()

    assert {row["category"] for row in body["generated"]} == {"A", "C"}
    assert not [r for r in body["generated"] if ":duplicate:" in r["scope"]]
    assert [r for r in body["generated"] if ":describetable:" in r["scope"]]
    assert body["ledger"] == [], (
        "a scan with no database issued no governed statement, so a ledger row here would "
        "record a read that never happened"
    )


def test_generate_is_idempotent_on_a_second_call(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = client.post("/elicitation/generate").json()
    assert first["n_generated"] > 0

    second = client.post("/elicitation/generate").json()
    assert second["n_generated"] == 0
    assert second["generated"] == []


# ── GET /elicitation/candidates ─────────────────────────────────────────────────────────────


def test_candidates_returns_empty_list_with_no_corpus_root(monkeypatch) -> None:
    client = _client(monkeypatch, _session_without_corpus_root())
    assert client.get("/elicitation/candidates").json() == []


def test_candidates_filters_to_elicitation_wizard_source_only(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import ClarificationRecord, write_clarifications

    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(id="q_curator", scope="s1", question="q1?", source="curator"),
            ClarificationRecord(
                id="q_wizard", scope="s2", question="q2?", source="elicitation_wizard", category="A",
            ),
            # A reader's own refusal-originated clarification (task A) is not a wizard
            # candidate either -- it was never proposed by a scan.
            ClarificationRecord(id="q_refusal", scope="s3", question="q3?", source="refusal"),
        ],
    )
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    rows = client.get("/elicitation/candidates").json()
    assert [r["id"] for r in rows] == ["q_wizard"]


def test_candidates_includes_both_open_and_answered(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.curator.clarifications import (
        ClarificationRecord,
        ClarificationRecordStatus,
        write_clarifications,
    )

    write_clarifications(
        tmp_path,
        [
            ClarificationRecord(
                id="q_open", scope="s1", question="q1?", source="elicitation_wizard", category="C",
            ),
            ClarificationRecord(
                id="q_answered", scope="s2", question="q2?", source="elicitation_wizard", category="C",
                status=ClarificationRecordStatus.answered, answer="Fiscal year starts in month 4.",
            ),
        ],
    )
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    rows = {r["id"]: r for r in client.get("/elicitation/candidates").json()}
    assert set(rows) == {"q_open", "q_answered"}
    assert rows["q_open"]["status"] == "open"
    assert rows["q_answered"]["status"] == "answered"


# ── category-aware composition, wired into POST /clarifications/{id}/answer ────────────────


def test_answering_an_a_candidate_by_choice_composes_a_real_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    picked = next(c["id"] for c in a_amount["choices"] if c["id"] == "orders.total_amount")

    response = client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": picked})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer"] == "'amount' maps to orders.total_amount."
    assert body["answer_text"] == "'amount' maps to orders.total_amount."


def test_answering_an_a_candidate_with_freeform_composes_a_real_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")

    response = client.post(f"/clarifications/{a_amount['id']}/answer", json={"answer": "orders.grand_total"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "'amount' maps to orders.grand_total."


def test_answering_a_c_candidate_composes_the_fiscal_year_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    c_rec = next(r for r in generated if r["category"] == "C")

    response = client.post(f"/clarifications/{c_rec['id']}/answer", json={"answer": "4"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Fiscal year starts in month 4."


def test_answering_an_e_candidate_composes_the_exclusion_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    e_rec = next(r for r in generated if r["category"] == "E")

    response = client.post(f"/clarifications/{e_rec['id']}/answer", json={"choice_id": "exclude"})
    assert response.status_code == 200, response.text
    # The question is in business words ("review status"); the fact names the physical column,
    # because its reader is the retrieval layer and not the person who answered.
    assert response.json()["answer"].startswith("orders.review_status — Leave out the rows")


def test_answering_a_b_candidate_composes_the_checklist_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    b_rec = next(r for r in generated if r["category"] == "B")
    picked = [c["id"] for c in b_rec["choices"]][:2]

    response = client.post(f"/clarifications/{b_rec['id']}/answer", json={"choice_ids": picked})
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(v in body["answer"] for v in picked)
    assert body["answer"].startswith("In orders.country_code, these values count as one group:")


# ── D join-path auto-follow-up ──────────────────────────────────────────────────────────────
#
# Selected by scope, not by ``category == "D"``: the structural near-duplicate cluster question is
# also a D (``curator/gaps.py``: "D, not a sixth letter" — a disagreeing identity-ish pair within
# one table is the doc's D row seen from the column side), so the category alone no longer
# identifies the auto-follow-up.


def test_d_followup_appears_when_the_picked_table_differs_from_target_table(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    assert a_amount["target_table"] == "orders"

    # Pick the column on the *other* table -- payments, not the expected orders.
    other_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("payments."))
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": other_table_choice})

    candidates = client.get("/elicitation/candidates").json()
    d_rows = _join_followups(candidates)
    assert d_rows, "expected a D follow-up after picking a column on a different table"
    assert d_rows[0]["category"] == "D"
    assert d_rows[0]["target_table"] == "payments"
    assert "orders" in d_rows[0]["question"] and "payments" in d_rows[0]["question"]

    all_clarifications = client.get("/clarifications").json()
    assert _join_followups(all_clarifications)


def test_no_d_followup_when_the_picked_table_matches_target_table(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    same_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("orders."))

    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": same_table_choice})

    candidates = client.get("/elicitation/candidates").json()
    assert not _join_followups(candidates)


def test_answering_the_d_followup_with_freeform_is_accepted(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    other_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("payments."))
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": other_table_choice})

    d_row = _join_followups(client.get("/elicitation/candidates").json())[0]
    response = client.post(f"/clarifications/{d_row['id']}/answer", json={"answer": "orders.id = payments.order_id"})
    assert response.status_code == 200, response.text
    # Prefixed with the object it is about: the freeform alone folds into the corpus as a
    # sentence with no subject.
    assert response.json()["answer"] == (
        "payments.revenue_amount: orders.id = payments.order_id."
    )


# ── end-to-end fold into the corpus (A/E/B, via the shared fold_ledger_answer_into_corpus) ──


def test_a_answer_folds_the_composed_sentence_into_the_corpus(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    picked = next(c["id"] for c in a_amount["choices"] if c["id"] == "orders.total_amount")

    body = client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": picked}).json()
    assert body["converted_to_corpus"] is True

    assets, problems = load(tmp_path, schemas=["shop"])
    assert not problems
    (draft,) = assets
    assert draft.asset_type.value == "term"
    assert "'amount' maps to orders.total_amount." in draft.summary


def test_e_answer_folds_the_composed_sentence_into_the_corpus(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    e_rec = next(r for r in generated if r["category"] == "E")

    body = client.post(f"/clarifications/{e_rec['id']}/answer", json={"choice_id": "exclude"}).json()
    assert body["converted_to_corpus"] is True

    assets, _ = load(tmp_path, schemas=["shop"])
    (draft,) = assets
    assert body["answer"] in draft.summary, draft.summary


def test_b_answer_folds_the_composed_sentence_into_the_corpus(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    b_rec = next(r for r in generated if r["category"] == "B")
    picked = [c["id"] for c in b_rec["choices"]][:2]

    body = client.post(f"/clarifications/{b_rec['id']}/answer", json={"choice_ids": picked}).json()
    assert body["converted_to_corpus"] is True

    assets, _ = load(tmp_path, schemas=["shop"])
    (draft,) = assets
    assert all(v in draft.summary for v in picked)


def test_generate_a_second_time_after_answering_does_not_duplicate(monkeypatch, tmp_path: Path) -> None:
    """Answering a candidate does not remove it from ``existing`` -- a second ``generate`` call
    must still see its scope as covered and propose nothing new for it."""
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = _by_scope(generated, "elicitation:termcolumn:amount")
    picked = next(c["id"] for c in a_amount["choices"] if c["id"] == "orders.total_amount")
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": picked})

    second = client.post("/elicitation/generate").json()
    assert second["n_generated"] == 0


def test_a_question_whose_answer_is_already_in_the_corpus_is_not_re_proposed(
    monkeypatch, tmp_path: Path
) -> None:
    """Scope idempotency cannot cover this, which is why the corpus check exists. The ledger is
    one file at the corpus root; the folded answer is an asset beneath it. Clear the ledger --
    a rebuilt corpus root, a hand-edited file, a second deployment pointed at the same corpus --
    and every scope the wizard knew about is gone while every fact it produced is still there.
    """
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = client.post("/elicitation/generate").json()["generated"]
    answered = next(r for r in first if r["category"] == "B")
    body = client.post(
        f"/clarifications/{answered['id']}/answer", json={"choice_ids": ["US", "CA"]}
    ).json()
    assert body["converted_to_corpus"] is True

    (tmp_path / "clarifications.jsonl").unlink()
    regenerated = client.post("/elicitation/generate").json()["generated"]

    scopes = {r["scope"] for r in regenerated}
    assert answered["scope"] not in scopes, "the corpus already answers this one"
    assert scopes, "every other question is proposed again, because nothing answered them"


