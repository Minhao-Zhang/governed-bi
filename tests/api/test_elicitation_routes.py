"""POST /elicitation/generate, GET /elicitation/candidates — the Setup Wizard's HTTP surface
(UtkuAI v1, ported), plus the category-aware composition + D join-path auto-follow-up wired
into the pre-existing ``POST /clarifications/{id}/answer`` (no new answer route -- every
category-tagged candidate is answered through the same route Phase 1a/1c already built).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _column(table_id: str, name: str, *, logical_type: Any = None, samples: tuple[Any, ...] = ()) -> Any:
    from governed_bi.corpus.schema import ColumnAsset

    return ColumnAsset(
        id=f"{table_id}.{name}",
        schema="shop",
        parent_table=table_id,
        physical_name=name,
        summary=name,
        logical_type=logical_type,
        sample_values=tuple(samples),
    )


def _schema_assets() -> dict[str, Any]:
    from governed_bi.corpus.schema import LogicalType, TableAsset

    orders_columns = [
        _column("shop.orders", "order_id"),
        _column("shop.orders", "order_date", logical_type=LogicalType.date),
        _column("shop.orders", "total_amount", logical_type=LogicalType.decimal),
        _column("shop.orders", "country_code", samples=("US", "CA", "MX", "FR", "DE")),
        _column("shop.orders", "review_status", samples=("approved", "pending", "not_yet_rated")),
    ]
    orders = TableAsset(
        id="shop.orders", schema="shop", physical_name="orders", summary="orders",
        columns=tuple(c.id for c in orders_columns),
    )
    payments_columns = [
        _column("shop.payments", "payment_id"),
        _column("shop.payments", "revenue_amount", logical_type=LogicalType.decimal),
    ]
    payments = TableAsset(
        id="shop.payments", schema="shop", physical_name="payments", summary="payments",
        columns=tuple(c.id for c in payments_columns),
    )
    return {a.id: a for a in [orders, payments, *orders_columns, *payments_columns]}


#: What the two value-gated columns really hold in the fake database behind this session.
#: Categories B and E now read these through ``serve/fetch.sample_rows`` rather than off
#: ``ColumnAsset.sample_values``, so a session with no connector proposes neither.
_DB_VALUES: dict[str, tuple[str, ...]] = {
    "country_code": ("US", "CA", "MX", "FR", "DE"),
    "review_status": ("approved", "pending", "not_yet_rated"),
}


class _ScriptedConnector:
    """The repo's governed-query test idiom (``tests/serve/test_agent_tools_hitl.py``'s
    ``Recorder``): a ``dialect`` and an ``execute`` returning ``(columns, rows, truncated)``."""

    dialect = "postgres"

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        for name, values in _DB_VALUES.items():
            if f'"{name}"' in sql:
                return ([name], [(v,) for v in values], False)
        return ([], [], False)


#: "the caller did not say", distinct from ``connector=None`` ("this session has no connector"),
#: which is itself a case under test.
_UNSET = object()


def _session_with_schema(
    tmp_path: Path, *, agent_model: Any = None, connector: Any = _UNSET
) -> Any:
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    assets_by_id = _schema_assets()
    return Session(
        index=None, structure=structure, assets_by_id=assets_by_id,
        # A real ``AnalystCorpus``, because ``POST /elicitation/generate`` now issues governed
        # statements and ``check()`` derives column authorization from that type, not from a
        # parallel set (ADR 0006 §8).
        corpus=for_analyst(list(assets_by_id.values())),
        connector=_ScriptedConnector() if connector is _UNSET else connector,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="shop", run_id="r",
        corpus_root=tmp_path, agent_model=agent_model,
    )


def _session_without_corpus_root() -> Any:
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    return Session(
        index=None, structure=structure, assets_by_id=_schema_assets(), corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="shop", run_id="r",
        corpus_root=None,
    )


def _client(monkeypatch, session: Any) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    monkeypatch.setattr(routes, "_session", lambda: session)
    return TestClient(routes.app)


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
    assert categories == {"A", "C", "E", "B"}, categories
    assert all(row["source"] == "elicitation_wizard" for row in body["generated"])
    assert all(row["status"] == "open" for row in body["generated"])

    from governed_bi.curator.clarifications import load_clarifications

    on_disk = load_clarifications(tmp_path)
    assert len(on_disk) == len(body["generated"])


def test_generate_reports_a_ledger_row_for_every_governed_value_read(monkeypatch, tmp_path: Path) -> None:
    """B and E now issue governed statements to get their real values, so the route owes the
    caller the verdict for each one. The deleted ``Connector.sample_values`` path wrote none,
    which is what made ``guardrail_errors == 0`` hold vacuously for it."""
    connector = _ScriptedConnector()
    client = _client(monkeypatch, _session_with_schema(tmp_path, connector=connector))
    body = client.post("/elicitation/generate").json()

    # Two keyword-gated columns (country_code, review_status), so two statements.
    assert len(connector.statements) == 2, connector.statements
    assert len(body["ledger"]) == 2, body["ledger"]
    assert all(row["path"] == "sample" and row["passed"] for row in body["ledger"])
    assert all(row["executed_sql"] for row in body["ledger"])
    assert any('"country_code"' in row["executed_sql"] for row in body["ledger"])


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
    """``ColumnAsset.sample_values`` is populated on both gated columns in this fixture and is
    no longer consulted -- the field is empty on every live-seeded corpus, which is the bug."""
    client = _client(monkeypatch, _session_with_schema(tmp_path, connector=None))
    body = client.post("/elicitation/generate").json()

    assert {row["category"] for row in body["generated"]} == {"A", "C"}
    # The refusal is still a governance decision with a row, not a silent skip.
    assert len(body["ledger"]) == 2
    assert all(row["passed"] is False for row in body["ledger"])


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
    a_amount = next(
        r for r in generated if r["category"] == "A" and "amount" in r["scope"]
    )
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
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])

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
    assert "apply this exclusion by default" in response.json()["answer"]


def test_answering_a_b_candidate_composes_the_checklist_sentence(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    b_rec = next(r for r in generated if r["category"] == "B")
    picked = [c["id"] for c in b_rec["choices"]][:2]

    response = client.post(f"/clarifications/{b_rec['id']}/answer", json={"choice_ids": picked})
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(v in body["answer"] for v in picked)
    assert "grouping asked about" in body["answer"]


# ── D join-path auto-follow-up ──────────────────────────────────────────────────────────────


def test_d_followup_appears_when_the_picked_table_differs_from_target_table(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])
    assert a_amount["target_table"] == "orders"

    # Pick the column on the *other* table -- payments, not the expected orders.
    other_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("payments."))
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": other_table_choice})

    candidates = client.get("/elicitation/candidates").json()
    d_rows = [r for r in candidates if r["category"] == "D"]
    assert d_rows, "expected a D follow-up after picking a column on a different table"
    assert d_rows[0]["target_table"] == "payments"
    assert "orders" in d_rows[0]["question"] and "payments" in d_rows[0]["question"]

    all_clarifications = client.get("/clarifications").json()
    assert any(r["category"] == "D" for r in all_clarifications)


def test_no_d_followup_when_the_picked_table_matches_target_table(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])
    same_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("orders."))

    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": same_table_choice})

    candidates = client.get("/elicitation/candidates").json()
    assert not [r for r in candidates if r["category"] == "D"]


def test_answering_the_d_followup_with_freeform_is_accepted(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])
    other_table_choice = next(c["id"] for c in a_amount["choices"] if c["id"].startswith("payments."))
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": other_table_choice})

    d_row = next(r for r in client.get("/elicitation/candidates").json() if r["category"] == "D")
    response = client.post(f"/clarifications/{d_row['id']}/answer", json={"answer": "orders.id = payments.order_id"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "orders.id = payments.order_id"


# ── end-to-end fold into the corpus (A/E/B, via the shared fold_ledger_answer_into_corpus) ──


def test_a_answer_folds_the_composed_sentence_into_the_corpus(monkeypatch, tmp_path: Path) -> None:
    from governed_bi.corpus.store import load

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    generated = client.post("/elicitation/generate").json()["generated"]
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])
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
    assert "apply this exclusion by default" in draft.summary


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
    a_amount = next(r for r in generated if r["category"] == "A" and "amount" in r["scope"])
    picked = next(c["id"] for c in a_amount["choices"] if c["id"] == "orders.total_amount")
    client.post(f"/clarifications/{a_amount['id']}/answer", json={"choice_id": picked})

    second = client.post("/elicitation/generate").json()
    assert second["n_generated"] == 0


# ── severity / audience / dependency gating on the wire (utku-ai-setup-wizard-gap-model.md) ──


def _blocked_pair() -> list[Any]:
    """A prerequisite and the candidate that must wait for it. **Hand-written, not generated** —
    no shipped detector emits a ``blocked_by`` yet (the near-duplicate-cluster question and the
    A-biz/A-eng pair are a later phase), so the only way to exercise the gate is to seed one."""
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
    assert {row["severity"] for row in rows} == {"T2"}
    assert all(row["blocked"] is False and row["blocked_by"] == [] for row in rows)


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
