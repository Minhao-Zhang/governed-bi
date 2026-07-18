"""L3 (column allowlist) failures must never be graded-delivered (audit S2).

L3 also gates ``governance.excluded`` + ``suspect`` columns — a confidentiality
control — so re-executing an L3-blocked query under ``grade_semantic_failures``
would return hidden-column rows marked "unverified". L3 is now hard for the FINAL
disposition (still repairable mid-loop). L4/L5 remain graded-and-delivered.
"""

from __future__ import annotations

from dataclasses import replace

from governed_bi.analyst.governance import _finish_unsuccessful
from governed_bi.config import Environment, Settings
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult

_IDENTITY = Identity(user="u", all_access=True)


class _CountingGateway:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, sql: str, identity: Identity) -> QueryResult:  # noqa: ARG002
        self.calls += 1
        return QueryResult(columns=["x"], rows=[(1,)], row_count=1, truncated=False)


def _finish(gateway, failed_layer: str):
    settings = replace(Settings.for_env(Environment.dev), grade_semantic_failures=True)
    return _finish_unsuccessful(
        settings=settings,
        gateway=gateway,
        identity=_IDENTITY,
        last_refusal={
            "refused_by": "guardrail",
            "failed_layer": failed_layer,
            "sql": "SELECT secret FROM t",
            "escalation": "escalate",
        },
        attempts=3,
        base_provenance={},
        question="q",
    )


def test_l3_column_failure_is_hard_refused_never_executed():
    gw = _CountingGateway()
    answer = _finish(gw, "ast_column_allowlist")
    assert gw.calls == 0, "L3-blocked SQL must not be re-executed (confidentiality)"
    assert answer.result is None  # a refusal, not a delivered result grid


def test_l4_scope_failure_still_graded_delivers():
    gw = _CountingGateway()
    answer = _finish(gw, "term_semantics")
    assert gw.calls == 1, "L4 scope failures still deliver-and-grade (pipeline-design §6)"
    assert answer.result is not None
