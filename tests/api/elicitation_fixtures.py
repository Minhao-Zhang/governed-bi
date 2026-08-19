"""Shared fixtures for the elicitation wizard's HTTP surface tests.

Split out of ``test_elicitation_routes.py`` by the 1000-line cap (ADR 0005 §6), which was
forcing the timing rather than the seam: that file's tests and
``test_the_setup_wizard_gap_model_gates_the_wire.py``'s both build the same corpus, the same
scripted connector and the same test client, so the fixtures belong to neither file alone.
Mirrors ``tests/serve/turn_contract_fixtures.py``'s precedent for the same situation.

The route under test runs **both** generators: ``curator/elicitation.py``'s keyword heuristic
and ``curator/gaps.py``'s structural detectors, with the latter's near-duplicate output gating
the former's records. So :func:`_schema_assets` carries a real decoy pair (``country_code`` /
``country_code_alt``, disagreeing row-wise) and :class:`_ScriptedConnector` answers the row-wise
comparison, because a fixture with no contested column cannot tell a wired dependency gate from
an unwired one — which is exactly how the gate stayed untested through two prior phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


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


#: Moved here from the D join-path section of test_elicitation_routes.py by the 1000-line
#: cap split: the A-pair end-to-end test in the file that moved to
#: test_the_setup_wizard_gap_model_gates_the_wire.py also filters on this shape, so both
#: resulting test modules need it and neither owns it alone.
def _join_followups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["scope"].startswith("elicitation:join:")]


def _client(monkeypatch, session: Any) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes

    # `routes.app` reached a process-global session that no longer exists: upstream
    # removed `_session` at the 2026-08-11 restructure in favour of this constructor.
    return TestClient(routes.make_app(session, None))
