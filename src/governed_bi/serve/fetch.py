"""What each governed tool actually does — the bodies, with no LangGraph in them.

**The seam is measured, not asserted.** These four functions mention ``runtime``, ``Command``
and ``_reply`` exactly **zero** times: each is a function of ``(identifiers, bounds, corpus,
connector, policy)`` returning a payload, and nothing about how that payload reaches the model.
``serve/tools.py`` is the adapter that makes them tools; this is the work.

Splitting them out is what took ``tools.py`` under ADR 0005 §6's 400-line tier, but the reason to
put the cut *here* is the boundary rather than the arithmetic: a reader asking "what does
``sample_rows`` show the model, and what does governance stop it showing" now reads one file with
no tool-call plumbing in it, and a reader asking "how does a tool answer" reads the other with no
SQL in it.

Every function returns a **tuple**, and each second element is a different fact the adapter has
to record: ``read_body`` returns whether the payload counts as a *delivery* (an out-of-scope
refusal is received by the model and is not one, and ``delivery_hash`` audits what was shown);
``run_query`` and ``sample_rows`` return the ledger row for the attempt. A single string would
have made all of those invisible.

**The two executing tools take the same route.** ``sample_rows`` used to hand-build a string and
call ``connector.sample_values``, which calls ``execute`` — the method ``ports.Connector``
reserves for ``govern.pipeline``. So it reached the database through none of the layers and wrote
no ledger row, which made ``guardrail_errors == 0`` hold *vacuously* for that path, and made one
policy refuse a suspect column in ``run_query`` while returning its values through
``sample_rows``. Both tools now build a statement, pass it through :func:`prepare`, and ledger the
verdict; the only difference is who writes the statement.

**One function here is not a tool.** :func:`compare_column_pair` has no entry in
``serve/tools.py`` and no language model ever calls it; its caller is ``curator/gaps.py``'s
near-duplicate detector, which needs a *row-wise* comparison
(``COUNT(*) WHERE a IS DISTINCT FROM b``) that no value-set read can express — two columns can
hold the same 554 distinct customer ids and still disagree on 6 305 of 6 312 rows. It lives
here anyway, and deliberately: the paragraph above is the reason. "A governed read, built as a
tree, run through :func:`prepare`, ledgered" has one home, and a second copy of that body
written next to a detector is how the deleted ``Connector.sample_values`` came to exist. The
signature difference is the honest marker of the difference in caller — it returns a typed
:class:`PairAgreement` rather than JSON, because JSON is what a *model* needs to read.

__all__ = ["read_body", "inspect_schema", "sample_rows", "run_query", "read_body_cap",
           "distinct_values_statement", "SAMPLE_ROWS_MAX_VALUES",
           "column_pair_agreement_statement", "compare_column_pair", "PairAgreement",
           "column_cardinality_statement", "count_distinct_values", "ColumnCardinality"]
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlglot import expressions as exp

from governed_bi.corpus.analyst import AnalystCorpus
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from governed_bi.govern.check import GovernanceUsageError
from governed_bi.govern.layers import refuse
from governed_bi.govern.ledger import (
    AttemptRecord,
    attempt_record,
)
from governed_bi.govern.pipeline import prepare, spellings_for
from governed_bi.govern.policy import GovernancePolicy

_DEFAULT_READ_BODY_MAX_CHARS = 80_000

#: Ceiling on the number of distinct values ``sample_rows`` returns.
#:
#: The old bound was ``max(1, int(limit))`` — clamped from below only, so the row bound was a
#: model-supplied argument with no ceiling, which is the shape :class:`ToolBounds` exists to
#: prevent (ADR 0006 §8: a tool that grants privilege must have a bound the model cannot widen).
#:
#: A constant rather than a knob because nothing can set a knob on this surface: ``cost_budget``
#: ships UNSET and no env var, config key or ``int_knob`` entry can write it, so a knob here
#: would be a declaration with no writer — the class of defect this repository has the most of.
SAMPLE_ROWS_MAX_VALUES = 20


def read_body_cap(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    for source in (state, state.get("knobs_resolved") or {}, cfg):
        if not isinstance(source, Mapping):
            continue
        raw = source.get("read_body_max_tokens")
        if raw is not None:
            return max(256, int(raw) * 4)
    return _DEFAULT_READ_BODY_MAX_CHARS


def asset_attr(asset: Any, name: str) -> Any:
    if isinstance(asset, Mapping):
        return asset.get(name)
    return getattr(asset, name, None)


def read_body(
    asset_ids: Sequence[str],
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    max_chars: int,
) -> tuple[str, bool]:
    """``(payload, delivered)``.

    The flag is the reason this is a tuple rather than a string: an out-of-scope refusal is a
    payload the model receives but **not** a delivery, and ``delivery_hash`` is an audit of
    what the corpus handed over. The tracker call used to be skipped by an early ``return``,
    which encoded the same distinction where a caller could not see it.
    """
    parts: list[str] = []
    used = 0
    for raw_id in asset_ids:
        aid = str(raw_id)
        if not bounds.may_read_body(aid):
            return OUT_OF_SCOPE_MESSAGE, False
        asset = assets.get(aid)
        if asset is None:
            return OUT_OF_SCOPE_MESSAGE, False
        body = asset_attr(asset, "body")
        text = "" if body is None else str(body)
        chunk = f"### {aid}\n{text}"
        if used + len(chunk) > max_chars:
            remain = max_chars - used
            if remain <= 0:
                break
            chunk = chunk[:remain] + "\n…[truncated]"
            parts.append(chunk)
            break
        parts.append(chunk)
        used += len(chunk)
    payload = "\n\n".join(parts) if parts else "(empty)"
    return payload, True


def inspect_schema(
    table_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
) -> tuple[str, bool]:
    tid = str(table_id)
    if not bounds.may_inspect_schema(tid):
        return OUT_OF_SCOPE_MESSAGE, False
    table = assets.get(tid)
    if table is None or not asset_is_table(table):
        return OUT_OF_SCOPE_MESSAGE, False
    columns: list[dict[str, Any]] = []
    for col_id in asset_attr(table, "columns") or ():
        col = assets.get(str(col_id))
        if col is None:
            columns.append({"id": str(col_id)})
            continue
        columns.append(
            {
                "id": str(asset_attr(col, "id") or col_id),
                "physical_name": asset_attr(col, "physical_name"),
                "physical_type": asset_attr(col, "physical_type"),
                "nullable": asset_attr(col, "nullable"),
            }
        )
    payload = json.dumps(
        {
            "table_id": tid,
            "physical_name": asset_attr(table, "physical_name"),
            "schema": asset_attr(table, "schema"),
            "columns": columns,
        },
        sort_keys=True,
        default=str,
    )
    return payload, True


def asset_is_table(asset: Any) -> bool:
    if isinstance(asset, TableAsset):
        return True
    at = asset_attr(asset, "asset_type")
    return str(getattr(at, "value", at) or "") == "table"


def distinct_values_statement(
    *, schema: str, table: str, column: str, limit: int, dialect: str
) -> str:
    """``SELECT DISTINCT c FROM t WHERE c IS NOT NULL ORDER BY c LIMIT n``, as a tree.

    **Built as a syntax tree and rendered, never interpolated.** The string this replaces was
    ``f'SELECT DISTINCT "{column}" FROM "{schema}"."{table}"…'``, and Postgres has no
    quote-doubling, so a ``physical_name`` containing ``"`` closed the quote and the rest of
    the value became SQL. That is not a hypothetical input: ``corpus/identity.slug`` exists
    precisely because ``physical_name`` holds the engine's identifier *verbatim* — "any
    character, any case, any script" — and ``corpus/validate.py`` validates only its slug, so
    the corpus deliberately does not constrain the content of this field.

    Rendering from ``exp.Identifier`` nodes puts the escaping in sqlglot's generator, which
    doubles an embedded quote for whichever dialect is asked for. The identifier reaches the
    engine as a *name*, which is the only thing it can be.
    """
    col = exp.column(column, table=table, quoted=True)
    query = (
        exp.select(col)
        .distinct()
        .from_(exp.table_(table, db=schema or None, quoted=True))
        .where(exp.Not(this=col.is_(exp.null())))
        .order_by(col)
        .limit(limit)
    )
    return query.sql(dialect=dialect)


def column_pair_agreement_statement(
    *, schema: str, table: str, left: str, right: str, dialect: str
) -> str:
    """``SELECT count(*), count(differing), count(distinct l), count(distinct r) FROM t``.

    **Row-wise, and that is the whole point.** :func:`distinct_values_statement` answers "what
    values does this column hold"; no answer to that question can decide whether two columns
    hold the same thing *on the same row*. The measured case:
    ``transaktion.kunde_id`` and ``transaktion.transaktions_kunde_id`` have the identical set of
    554 distinct customer ids and disagree on 6 305 of 6 312 rows, so a value-set comparison
    calls them the same column and a row-wise one calls them a poisoned join key.

    ``IS DISTINCT FROM`` rather than ``<>``: ``NULL <> 5`` is NULL, not true, so a plain
    inequality silently under-counts every row where one side is missing — and two NULLs
    *agreeing* is the semantics wanted, since a pair that is NULL everywhere is redundant rather
    than contradictory.

    The two ``count(distinct …)`` columns are in the same statement rather than a second one
    because they are a **precision** signal for the same finding, not a separate question: two
    columns that are two copies of one fact draw on comparable value vocabularies, and a pair
    holding 554 and 2 distinct values is two different facts that happen to share a name stem
    (``curator/gaps.py`` measures this and it removes 12 of 17 false positives at no cost to
    recall). Splitting them would double the governed round trips for one finding.

    Built from ``exp`` nodes and rendered, never interpolated — :func:`distinct_values_statement`
    gives the reason, and it is the same field: ``physical_name`` holds the engine's identifier
    verbatim, so its content is deliberately unconstrained.

    Both columns must belong to ``table``. A row-wise predicate over two relations needs a join
    key, and *which* key is the question the join detector asks; :func:`compare_column_pair`
    enforces that rather than this function, because "these ids name one table" is a fact about
    the corpus and this function only knows names.
    """
    src = exp.table_(table, db=schema or None, quoted=True)
    left_col = exp.column(left, table=table, quoted=True)
    right_col = exp.column(right, table=table, quoted=True)
    differing = exp.Case(
        ifs=[
            exp.If(
                this=exp.NullSafeNEQ(this=left_col.copy(), expression=right_col.copy()),
                true=exp.Literal.number(1),
            )
        ]
    )
    return (
        exp.select(
            exp.alias_(exp.Count(this=exp.Star()), "n_rows"),
            exp.alias_(exp.Count(this=differing), "n_differing"),
            exp.alias_(
                exp.Count(this=exp.Distinct(expressions=[left_col.copy()])), "n_distinct_left"
            ),
            exp.alias_(
                exp.Count(this=exp.Distinct(expressions=[right_col.copy()])), "n_distinct_right"
            ),
        )
        .from_(src)
        .sql(dialect=dialect)
    )


@dataclass(frozen=True, slots=True)
class PairAgreement:
    """What one governed comparison measured. Four counts, no interpretation.

    A typed record rather than the JSON ``sample_rows`` returns, because the difference in
    caller is real: ``sample_rows``'s other caller is a language model reading a tool result,
    and this function's only caller is Python deciding a severity tier. Serialising and
    re-parsing to look alike would be a cost paid for a resemblance.

    Whether these numbers mean "poisoned duplicate" (T1), "redundant copy" (T4) or "two
    different facts" lives in ``curator/gaps.py``. This module measures; it does not classify.
    """

    n_rows: int
    n_differing: int
    n_distinct_left: int
    n_distinct_right: int


def compare_column_pair(
    left_id: str,
    right_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
    corpus: Any,
    policy: GovernancePolicy,
) -> tuple[PairAgreement | None, AttemptRecord | None]:
    """``(measurement, ledger row)`` for one within-table column pair.

    Same governed route as :func:`sample_rows`, layer for layer: statement as a tree,
    :func:`prepare`, one ``path="sample"`` ``attempt_record``, and ``connector.execute`` only on
    the string ``prepare`` returned. The ``None``/row combinations carry the same meanings they
    do there, for the same reasons:

    * ``(None, None)`` — no statement was ever built, so there is no governance decision to
      record: an out-of-scope or unknown column id, a pair spanning two tables, or an
      identifier the corpus cannot spell. The licensing surface that refused it is already in
      ``bounds.licensed``.
    * ``(None, refusing row)`` — a layer refused. An ``excluded`` or ``suspect`` column (under
      ``hard_block_suspect``) refuses at COLUMNS, and the caller's correct response is to skip
      the pair, never to measure it some other way. The row is still owed: a refused attempt is
      a governance decision.
    * ``(None, passing row)`` — the statement cleared every layer and was sent, and the driver
      failed. The commonest cause is a genuinely type-incompatible pair
      (``bigint IS DISTINCT FROM text`` raises ``operator does not exist``), which is why the
      caller's type-compatibility gate is a correctness requirement and not only a filter.
    """
    left = assets.get(str(left_id))
    right = assets.get(str(right_id))
    if not (bounds.may_sample(str(left_id)) and bounds.may_sample(str(right_id))):
        return None, None
    if left is None or right is None or not (_is_column(left) and _is_column(right)):
        return None, None
    table_id = str(asset_attr(left, "parent_table") or "")
    if not table_id or table_id != str(asset_attr(right, "parent_table") or ""):
        return None, None

    if connector is None:
        return None, attempt_record(
            refuse("r_not_a_read", "no connector configured"), "sample", executed_sql=None
        )
    if not isinstance(corpus, AnalystCorpus):
        # G1, verbatim from ``sample_rows``: a missing corpus is a wiring failure, and refusing
        # on it would record a governance verdict for it.
        raise GovernanceUsageError(
            "compare_column_pair has no AnalystCorpus: corpus is "
            f"{type(corpus).__name__}. Column authorization is derived from AnalystCorpus as a "
            "type (ADR 0006 §8), never from a parallel set."
        )

    parent = assets.get(table_id)
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    schema = str(asset_attr(left, "schema") or "")
    left_name = str(asset_attr(left, "physical_name") or "")
    right_name = str(asset_attr(right, "physical_name") or "")
    if not (table_name and left_name and right_name):
        # No identifier to name a relation or a column with, so no statement — the same
        # "out of scope rather than a refusal" call ``sample_rows`` makes here.
        return None, None

    dialect = getattr(connector, "dialect", None) or "sqlite"
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        column_pair_agreement_statement(
            schema=schema, table=table_name, left=left_name, right=right_name, dialect=dialect
        ),
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=dialect,
        policy=policy,
    )
    attempt = attempt_record(verdict=prepared.verdict, path="sample", executed_sql=prepared.sql)
    if prepared.sql is None:
        return None, attempt
    try:
        _columns, rows, _truncated = connector.execute(prepared.sql)
    except Exception:  # noqa: BLE001 — the row is the point, not the traceback
        return None, attempt
    row = list(rows)[0] if rows else None
    if row is None or len(row) < 4:
        return None, attempt
    return (
        PairAgreement(
            n_rows=int(row[0] or 0),
            n_differing=int(row[1] or 0),
            n_distinct_left=int(row[2] or 0),
            n_distinct_right=int(row[3] or 0),
        ),
        attempt,
    )


def column_cardinality_statement(*, schema: str, table: str, column: str, dialect: str) -> str:
    """``SELECT count(*), count(distinct c) FROM t`` — does this column identify a row?

    **The one fact the corpus cannot answer and the join detector needs.**
    ``Session.from_live_schema`` leaves ``is_unique``, ``role`` and ``references`` unset on every
    column and ``nullable`` true on all of them, and ``pg_rename_decoy`` declares *zero* table
    constraints (``information_schema.table_constraints``, measured), so there is no primary key
    to read. The alternative the join detector used instead was the *name* — a key is
    conventionally called after what it identifies — and on real ``restaurant`` that convention
    holds for nothing at all: 5 tables, 0 declared joins, 0 questions emitted.

    Distinct from :func:`distinct_values_statement`, which returns the first
    :data:`SAMPLE_ROWS_MAX_VALUES` *values*: a capped list says a column has at least twenty
    distinct values and can never say whether it has exactly as many as there are rows. The
    counts are the question; the values are a different one, asked elsewhere and for a different
    reader.

    Built from ``exp`` nodes and rendered, never interpolated, for
    :func:`distinct_values_statement`'s reason and on the same field.
    """
    col = exp.column(column, table=table, quoted=True)
    return (
        exp.select(
            exp.alias_(exp.Count(this=exp.Star()), "n_rows"),
            exp.alias_(exp.Count(this=exp.Distinct(expressions=[col])), "n_distinct"),
        )
        .from_(exp.table_(table, db=schema or None, quoted=True))
        .sql(dialect=dialect)
    )


@dataclass(frozen=True, slots=True)
class ColumnCardinality:
    """What one governed cardinality read measured. Two counts, no interpretation.

    Whether ``n_distinct == n_rows`` means "primary key", "a unique text field nothing joins on"
    or "a three-row table where every column is trivially unique" lives in ``curator/gaps.py``.
    """

    n_rows: int
    n_distinct: int


def count_distinct_values(
    column_id: str,
    *,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
    corpus: Any,
    policy: GovernancePolicy,
) -> tuple[ColumnCardinality | None, AttemptRecord | None]:
    """``(measurement, ledger row)`` for one column's row and distinct counts.

    Same governed route as :func:`compare_column_pair`, layer for layer, and the
    ``None``/row combinations carry exactly the meanings its docstring gives them.
    """
    column = assets.get(str(column_id))
    if not bounds.may_sample(str(column_id)):
        return None, None
    if column is None or not _is_column(column):
        return None, None
    if connector is None:
        return None, attempt_record(
            refuse("r_not_a_read", "no connector configured"), "sample", executed_sql=None
        )
    if not isinstance(corpus, AnalystCorpus):
        # G1, verbatim from ``compare_column_pair``: a missing corpus is a wiring failure, and
        # refusing on it would record a governance verdict for it.
        raise GovernanceUsageError(
            "count_distinct_values has no AnalystCorpus: corpus is "
            f"{type(corpus).__name__}. Column authorization is derived from AnalystCorpus as a "
            "type (ADR 0006 §8), never from a parallel set."
        )

    parent = assets.get(str(asset_attr(column, "parent_table") or ""))
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    column_name = str(asset_attr(column, "physical_name") or "")
    if not (table_name and column_name):
        return None, None

    dialect = getattr(connector, "dialect", None) or "sqlite"
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        column_cardinality_statement(
            schema=str(asset_attr(column, "schema") or ""),
            table=table_name,
            column=column_name,
            dialect=dialect,
        ),
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=dialect,
        policy=policy,
    )
    attempt = attempt_record(verdict=prepared.verdict, path="sample", executed_sql=prepared.sql)
    if prepared.sql is None:
        return None, attempt
    try:
        _columns, rows, _truncated = connector.execute(prepared.sql)
    except Exception:  # noqa: BLE001 — the row is the point, not the traceback
        return None, attempt
    row = list(rows)[0] if rows else None
    if row is None or len(row) < 2:
        return None, attempt
    return ColumnCardinality(n_rows=int(row[0] or 0), n_distinct=int(row[1] or 0)), attempt


def _is_column(asset: Any) -> bool:
    at = asset_attr(asset, "asset_type")
    return isinstance(asset, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"


def sample_rows(
    column_id: str,
    *,
    limit: int,
    bounds: ToolBounds,
    assets: Mapping[str, Any],
    connector: Any,
    corpus: Any,
    policy: GovernancePolicy,
) -> tuple[str, bool, AttemptRecord | None]:
    """``(payload, delivered, attempt_row)``. A governed executor path, like ``run_query``.

    The ledger row is ``path="sample"`` — the second of ``EXECUTOR_PATHS`` to acquire a
    writer. It is ``None`` only when no statement was ever built: an out-of-scope column id
    produces no SQL, so there is no governance decision to record, and the licensing surface
    that refused it is already in ``licensed`` / ``readable_assets``.

    Layer coverage is the point. ``check()`` refuses a suspect column at COLUMNS when
    ``hard_block_suspect`` is on — which is what closes the bypass where one policy refused a
    suspect column in ``run_query`` and handed over its values here. ADR 0006 §7 said the
    exclusion/suspect filter was applied "in the tool"; it was not applied anywhere.
    """
    cid = str(column_id)
    if not bounds.may_sample(cid):
        return OUT_OF_SCOPE_MESSAGE, False, None
    col = assets.get(cid)
    if col is None:
        return OUT_OF_SCOPE_MESSAGE, False, None
    at = asset_attr(col, "asset_type")
    is_col = isinstance(col, ColumnAsset) or str(getattr(at, "value", at) or "") == "column"
    if not is_col:
        return OUT_OF_SCOPE_MESSAGE, False, None
    if connector is None:
        return (
            "sample_rows error: no connector configured",
            False,
            attempt_record(
                refuse("r_not_a_read", "no connector configured"), "sample", executed_sql=None
            ),
        )
    if not isinstance(corpus, AnalystCorpus):
        # G1, and the same reasoning ``run_query`` gives: a missing corpus is a wiring
        # failure, and refusing on it would record a governance verdict for it.
        raise GovernanceUsageError(
            "sample_rows has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. The sample path now runs through check(), which "
            "derives column authorization from AnalystCorpus as a type (ADR 0006 §8)."
        )

    table = str(asset_attr(col, "parent_table") or "")
    physical = str(asset_attr(col, "physical_name") or "")
    schema = str(asset_attr(col, "schema") or "")
    # **The engine's spelling, not the corpus key** (ADR 0008 D1: a key is not a name).
    # `parent_table.split(".")[-1]` yields the *slug* — `Air_Carriers_66c534` for the table
    # whose physical name is `Air Carriers` — which is not a relation in any engine. And the
    # schema was read into a local and then dropped, so the connector fell back to `public`.
    # Both halves had to be wrong for the failure to be invisible: an unqualified slug and a
    # default schema produce 42P01, which surfaced as a tool error nothing counted.
    parent = assets.get(table)
    table_name = str(asset_attr(parent, "physical_name") or "") if parent is not None else ""
    if not table_name:
        table_name = table.split(".")[-1] if table else ""
    if not physical or not table_name:
        # No identifier to name a relation with. Out of scope rather than a refusal: there is
        # no statement, so there is nothing for a layer to have decided.
        return OUT_OF_SCOPE_MESSAGE, False, None

    dialect = getattr(connector, "dialect", None) or "sqlite"
    statement = distinct_values_statement(
        schema=schema,
        table=table_name,
        column=physical,
        limit=max(1, min(int(limit), SAMPLE_ROWS_MAX_VALUES)),
        dialect=dialect,
    )
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        statement,
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=dialect,
        policy=policy,
    )
    attempt = attempt_record(verdict=prepared.verdict, path="sample", executed_sql=prepared.sql)
    if prepared.sql is None:
        detail = prepared.verdict.get("detail") or prepared.verdict.get("reason_code")
        return f"sample_rows refused: {detail}", False, attempt

    try:
        _columns, rows, _truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # Returned with the attempt, for the reason ``run_query`` gives: it cleared every
        # layer and was sent, so the ledger owes it a row.
        return f"sample_rows error: {type(exc).__name__}: {exc}", False, attempt
    payload = json.dumps(
        {
            "column_id": cid,
            "schema": schema,
            "table": table,
            "values": [row[0] for row in rows],
        },
        sort_keys=True,
        default=str,
    )
    return payload, True, attempt


def run_query(
    sql: str,
    *,
    bounds: ToolBounds,
    corpus: Any,
    connector: Any,
    policy: GovernancePolicy,
) -> tuple[str, Any]:
    """``(payload, attempt_row)``. Exactly one ledger row per admitted call.

    The cap is not decided here any more — :class:`~governed_bi.serve.agent_state.AttemptBook`
    owns it, because counting attempts requires knowing what a *previous node execution*
    committed and this function only ever saw one closure's list.
    """
    if connector is None:
        return (
            "run_query error: no connector configured",
            attempt_record(
                refuse("r_not_a_read", "no connector configured"), "agent", executed_sql=None
            ),
        )

    if not isinstance(corpus, AnalystCorpus):
        # G1: absence refuses. An empty corpus here fails closed, so nothing leaks — but
        # it records "the corpus was never wired up" as ``r_column_not_allowed`` with
        # ``guardrail_errors: 0``, indistinguishable from "the model asked for a column it
        # may not see". ``check()`` raises this for the same input; ``serve/`` must not
        # catch it and substitute a default.
        raise GovernanceUsageError(
            "run_query has no AnalystCorpus: configurable['corpus'] is "
            f"{type(corpus).__name__}. Every tool reads through AnalystCorpus as a type, "
            "not a convention (ADR 0006 §8), and a turn served without one cannot tell a "
            "governance refusal from its own wiring failure."
        )

    # ADR 0008 D2/D7. Without these two the model's spelling reaches the engine
    # unchanged: `check()` compares folded keys, so `FROM address.cbsa` matches the
    # licensed `address.CBSA` and passes every layer, and Postgres then folds the
    # unquoted name and reports that the relation does not exist. 81 tables and 610
    # columns in the obfuscated lake failed that way, each *after* a passing verdict.
    # Scoped to `bounds.licensed`, because a corpus-wide map makes `name`, `id` and
    # `city` ambiguous and would refuse nearly every query.
    spellings, ambiguous = spellings_for(corpus, bounds.licensed)
    prepared = prepare(
        sql,
        licensed=bounds.licensed,
        corpus=corpus,
        spellings=spellings,
        ambiguous_folds=ambiguous,
        dialect=getattr(connector, "dialect", None) or "sqlite",
        policy=policy,
    )
    attempt = attempt_record(
        verdict=prepared.verdict, path="agent", executed_sql=prepared.sql
    )
    if prepared.sql is None:
        detail = prepared.verdict.get("detail") or prepared.verdict.get("reason_code")
        return f"run_query refused: {detail}", attempt

    try:
        columns, rows, truncated = connector.execute(prepared.sql)
    except Exception as exc:  # noqa: BLE001 — the row is the point, not the traceback
        # **The attempt is returned even though the execution failed.** It passed every
        # governance layer and was sent to the database, so it is a governed statement and the
        # ledger owes it a row. The old shared-box code kept the row by accident — it appended
        # before executing and the box outlived the exception — and returning only the error
        # string here would have made a driver failure look like a turn that never attempted
        # anything, which is the empty-ledger-holds-vacuously shape.
        return f"run_query error: {type(exc).__name__}: {exc}", attempt
    preview = [list(r) for r in list(rows)[:20]]
    payload = json.dumps(
        {
            "columns": list(columns),
            "rows": preview,
            "truncated": bool(truncated),
            "row_count": len(rows),
        },
        default=str,
    )
    return payload, attempt
