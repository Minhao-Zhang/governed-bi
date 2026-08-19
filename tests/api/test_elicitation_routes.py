"""POST /elicitation/generate, GET /elicitation/candidates — the Setup Wizard's HTTP surface
(UtkuAI v1, ported), plus the category-aware composition + D join-path auto-follow-up wired
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
        # The decoy. Reads as a second spelling of the column beside it, holds a comparable
        # vocabulary, and disagrees on 37 of 200 rows (:data:`_PAIR_COUNTS`) — the shape whose
        # whole danger is that a value checklist cannot show it.
        _column("shop.orders", "country_code_alt", samples=("US", "CA", "MX", "FR", "DD")),
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


#: What the value-gated columns really hold in the fake database behind this session.
#: Categories B and E read these through ``serve/fetch.sample_rows`` rather than off
#: ``ColumnAsset.sample_values``, so a session with no connector proposes neither.
_DB_VALUES: dict[str, tuple[str, ...]] = {
    "country_code": ("US", "CA", "MX", "FR", "DE"),
    "country_code_alt": ("US", "CA", "MX", "FR", "DD"),
    "review_status": ("approved", "pending", "not_yet_rated"),
}

#: What a **row-wise** comparison of one within-table column pair counts, keyed by the two column
#: names its statement quotes: ``(n_rows, n_differing, n_distinct_left, n_distinct_right)``.
#:
#: Both entries matter and they are the detector's two outcomes. The decoy pair disagrees over
#: comparable vocabularies, which is T1. ``order_id``/``order_date`` reads alike enough to clear
#: the name gate (``orderid``/``orderdate`` share a five-character run) and is *not* a finding,
#: because 200 distinct ids against 3 distinct dates cannot be two copies of one fact — so it also
#: pins that the cardinality precision filter is reached through the route, not just in unit
#: tests. A pair with no entry returns no row, which the caller reads as a refusal and skips.
_PAIR_COUNTS: dict[frozenset[str], tuple[int, int, int, int]] = {
    frozenset({"country_code", "country_code_alt"}): (200, 37, 5, 5),
    frozenset({"order_id", "order_date"}): (200, 200, 200, 3),
}


class _ScriptedConnector:
    """The repo's governed-query test idiom (``tests/serve/test_agent_tools_hitl.py``'s
    ``Recorder``): a ``dialect`` and an ``execute`` returning ``(columns, rows, truncated)``.

    Two statement shapes now reach it, and it tells them apart the way they differ on the wire:
    the pair comparison is the only one carrying ``IS DISTINCT FROM``. Quoted names, not bare
    ones, so ``"country_code"`` does not also match ``"country_code_alt"``'s own statement.
    """

    dialect = "postgres"

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, **_kwargs: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        self.statements.append(sql)
        if "IS DISTINCT FROM" in sql:
            named = frozenset(n for n in (*_DB_VALUES, *_KEY_LIKE) if f'"{n}"' in sql)
            counts = _PAIR_COUNTS.get(named)
            return (
                (["n_rows", "n_differing", "n_distinct_left", "n_distinct_right"], [counts], False)
                if counts is not None
                else ([], [], False)
            )
        if "COUNT(*)" in sql:
            for name, counts in _CARDINALITIES.items():
                if f'"{name}"' in sql:
                    return (["n_rows", "n_distinct"], [counts], False)
            return ([], [], False)
        for name, values in _DB_VALUES.items():
            if f'"{name}"' in sql:
                return ([name], [(v,) for v in values], False)
        return ([], [], False)


#: Columns that appear in a comparison statement but never in a value read, so
#: :class:`_ScriptedConnector` can name the pair it is being asked about.
_KEY_LIKE: frozenset[str] = frozenset({"order_id", "order_date"})

#: ``(n_rows, n_distinct)`` for the columns category A asks a cardinality count about — the two
#: whose names carry an ambiguous business term. The two shapes are deliberately opposite:
#: ``total_amount`` repeats (48 values over 200 order lines) and ``revenue_amount`` is unique per
#: row, which is the grain distinction A-biz's choices are worded from.
_CARDINALITIES: dict[str, tuple[int, int]] = {
    "total_amount": (200, 48),
    "revenue_amount": (200, 200),
}


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


def _by_scope(rows: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    """One generated row by exact scope.

    Selecting category A by letter stopped identifying a question once A became a *pair*
    (``curator/elicitation_terms.py``) on top of the three shapes ``curator/gaps.py`` already
    borrows the letter for. ``elicitation:termcolumn:amount`` is the engineering half — the one
    that binds a term to a column, and therefore the one every test below is about.
    """
    return next(r for r in rows if r["scope"] == scope)


def _client(monkeypatch, session: Any) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    # `routes.app` reached a process-global session that no longer exists: upstream
    # removed `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None))


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


def _join_followups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["scope"].startswith("elicitation:join:")]


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


# ── severity / audience / dependency gating on the wire (utku-ai-setup-wizard-gap-model.md) ──


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

    ``utku-ai-setup-wizard-gap-model.md``'s A-biz example offers "after discounts" and "after
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
