"""The run_query attempt-cap path must never graded-deliver unvalidated SQL.

Audit Vuln 2 (broken access control): when the ``run_query`` attempt cap is hit,
the middleware records a ``{"verdict": "cap", "sql": ...}`` ledger entry BEFORE
``check()`` runs — it carries no ``layer``. The agent then selects that entry as
the SQL to grade-deliver, yielding ``failed_layer=None``, which the old denylist
gate treated as non-hard and re-executed. A capped attempt cleared NO guardrail
layer, so its SQL (which may touch an excluded column or a dangerous function)
must be hard-refused, never executed.

Graded delivery is now an allowlist: only a curated SEMANTIC failure (L4/L5) —
which proves L1/L2/L3 were cleared — is ever re-executed, and even then the SQL is
re-checked immediately before execution (mirroring ``_try_cache_hit``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from governed_bi.analyst.agent import answer_question_agent
from governed_bi.analyst.answer import ReliabilityTier
from governed_bi.analyst.governance import _finish_unsuccessful
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist
from governed_bi.gateway.connectors.base import QueryResult
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"
# A curator-flagged `excluded` column on beer_factory.transaction (confidentiality
# control): touching it fails L3 and, if ever executed, leaks card numbers.
EXCLUDED_SQL = 'SELECT "CreditCardNumber" FROM "transaction"'

_IDENTITY = Identity(user="u", all_access=True)


class _CountingGateway:
    """Records how many times SQL is handed to the DB (see test_graded_delivery_l3_hard)."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, sql: str, identity: Identity) -> QueryResult:  # noqa: ARG002
        self.calls += 1
        return QueryResult(columns=["x"], rows=[(1,)], row_count=1, truncated=False)


def _settings():
    return replace(Settings.for_env(Environment.dev), grade_semantic_failures=True)


# --- Unit level: the governance finalizer ------------------------------------


def test_cap_entry_is_refused_never_executed():
    """A cap-shaped refusal (no ``failed_layer``, as agent.py builds from a ``cap``
    ledger entry) is hard-refused and never re-executed — even with grading on."""
    gw = _CountingGateway()
    answer = _finish_unsuccessful(
        settings=_settings(),
        gateway=gw,
        identity=_IDENTITY,
        last_refusal={
            "refused_by": "guardrail",
            "failed_layer": None,  # a cap entry never ran check(): no layer
            "sql": EXCLUDED_SQL,
            "escalation": "escalate",
        },
        attempts=3,
        base_provenance={},
        question="q",
    )
    assert gw.calls == 0, "unvalidated cap SQL must never be executed (Vuln 2)"
    assert answer.result is None  # a refusal, not a delivered result grid
    assert answer.tier is ReliabilityTier.refused


def test_recheck_refuses_mislabeled_semantic_entry():
    """Even if the ledger LABELS a failure semantic (``term_semantics``), the
    pre-execute re-check re-runs check() and refuses when the SQL actually trips a
    safety/confidentiality layer (here L3, an excluded column) — never trusting the
    label, mirroring _try_cache_hit."""
    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    gw = _CountingGateway()
    answer = _finish_unsuccessful(
        settings=_settings(),
        gateway=gw,
        identity=_IDENTITY,
        last_refusal={
            "refused_by": "guardrail",
            "failed_layer": "term_semantics",  # claims a benign scope failure
            "sql": EXCLUDED_SQL,  # but touches an excluded (L3) column
            "escalation": "escalate",
        },
        attempts=3,
        base_provenance={},
        question="q",
        allowlist=column_allowlist(corpus),
        dialect="sqlite",
        default_schema="beer_factory",
    )
    assert gw.calls == 0, "re-check must catch the L3 violation and refuse"
    assert answer.result is None
    assert answer.provenance.get("graded_delivery_recheck_failed") == "ast_column_allowlist"


def test_recheck_allows_clean_l4_scope_failure():
    """The re-check must NOT block a legitimate L4 (scope-only) graded delivery:
    a query over allowlisted columns still delivers-and-grades (pipeline-design §6)."""
    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    gw = _CountingGateway()
    answer = _finish_unsuccessful(
        settings=_settings(),
        gateway=gw,
        identity=_IDENTITY,
        last_refusal={
            "refused_by": "guardrail",
            "failed_layer": "term_semantics",
            "sql": 'SELECT SUM("PurchasePrice") FROM "transaction"',  # allowlisted col
            "escalation": "escalate",
        },
        attempts=3,
        base_provenance={},
        question="q",
        allowlist=column_allowlist(corpus),
        dialect="sqlite",
        default_schema="beer_factory",
    )
    assert gw.calls == 1, "a clean L4 scope failure still delivers-and-grades"
    assert answer.result is not None


# --- Integration: the full agent cap flow ------------------------------------


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def test_cap_bypass_end_to_end_is_refused(corpus, bird_gateway):
    """Reproduce the confirmed chain: three run_query attempts blocked at L3, the
    fourth hit the attempt cap (unvalidated), and the agent would grade-deliver it.
    With the fix the turn REFUSES and the unvalidated card-number SQL never reaches
    the gateway."""
    identity = Identity(user="dev", all_access=True)
    settings = replace(Settings.for_env(Environment.dev), grade_semantic_failures=True)
    turns = [
        ai_tool_turn("run_query", {"sql": EXCLUDED_SQL}, "c1"),  # L3 block (non-hard)
        ai_tool_turn("run_query", {"sql": EXCLUDED_SQL}, "c2"),  # L3 block
        ai_tool_turn("run_query", {"sql": EXCLUDED_SQL}, "c3"),  # L3 block (3rd → cap next)
        ai_tool_turn("run_query", {"sql": EXCLUDED_SQL}, "c4"),  # CAP: no check() runs
        AIMessage(content="giving up"),
    ]
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="cap-bypass-test",
        model=FakeToolModel(responses=turns),
    )

    assert ans.tier is ReliabilityTier.refused, "capped/unvalidated SQL must be refused"
    assert ans.result is None
    executed = [e.sql for e in bird_gateway.audit_log]
    assert not any("CreditCardNumber" in sql for sql in executed), (
        "unvalidated cap SQL touching an excluded column must never be executed"
    )
    # Belt-and-suspenders: nothing at all was executed (every attempt was blocked
    # before execute, and graded delivery was refused).
    assert executed == []
    ledger = ans.provenance.get("governance_ledger") or []
    assert any(e.get("verdict") == "cap" for e in ledger), "the cap path was actually exercised"
