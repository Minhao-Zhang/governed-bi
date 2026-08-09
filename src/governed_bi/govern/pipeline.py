"""Transformation pipeline (ADR 0006 §3). Fixed order — G4::

    1. normalise    NFKC, after the control-character check
    2. canonicalise rewrite identifiers to corpus spelling (ambiguous folds refuse)
    3. check()      the layer stack
    4. limit        min(existing, max_rows + 1) at the statement root
    5. execute      caller's job via ports.Connector
    6. ledger       sha256 of step 4's string

Canonicalisation precedes checking. Step 4 is the only post-check rewrite
(structural LIMIT); the ledger hashes its output. Parse failure at step 4 refuses.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Mapping

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..corpus.analyst import AnalystCorpus
from .check import GovernanceUsageError, check
from .guard import has_control_characters
from .identifiers import fold, fold_map
from .layers import CheckVerdict, refuse
from .policy import DEFAULT_DIALECT, GovernancePolicy

__all__ = [
    "Prepared",
    "normalise",
    "canonicalise",
    "apply_row_limit",
    "prepare",
    "spellings_for",
]


@dataclass(frozen=True, slots=True)
class Prepared:
    """Outcome of steps 1–4. ``sql`` is ``None`` when nothing may run."""

    verdict: CheckVerdict
    #: The exact string to hand to ``Connector.execute``, limit included.
    sql: str | None
    raw: str
    canonical: str | None


def normalise(text: str) -> str:
    """NFKC. **Only** after :func:`~governed_bi.govern.guard.has_control_characters`.

    NFKC *rewrites* text, so any check placed after it is a check on a string nobody
    sent. (Not for the reason ADR 0006 §3 gives: NFKC strips no character the encoding
    rule rejects.)
    """
    return unicodedata.normalize("NFKC", text)


def spellings_for(
    corpus: AnalystCorpus, licensed: frozenset[str]
) -> tuple[Mapping[str, str], frozenset[str], Mapping[str, Mapping[str, str]]]:
    """``(folded -> declared, ambiguous folds, per-table folded -> declared)``.

    Scoped to ``licensed``, because a corpus-wide map makes ``name``, ``id`` and ``city``
    ambiguous and would refuse nearly every query. **That scoping is right in kind and was not
    narrow enough**: a turn licenses ~26 tables across ~8 schemas, so two of them declaring
    ``Name`` and ``name`` made every reference to either refuse. Measured on the 2026-08-09 v3
    arm, that hit 119 of 1 351 turns, 112 of which ended ``capped`` at EX 0.025, and it burned
    24% of the run's input tokens on statements the model was never told how to fix.

    The third return value is the fix: the same map **per table**, so a *qualified* reference
    resolves against its own table and never consults the flat one. ``T1."Name"`` was never
    ambiguous; only the flat namespace made it look that way.
    """
    names: list[str] = []
    by_table: dict[str, Mapping[str, str]] = {}
    for table_id in sorted(licensed):
        table = corpus.get(table_id)
        if table is None:
            continue
        for attr in ("physical_name", "schema"):
            value = getattr(table, attr, None)
            if isinstance(value, str) and value:
                names.append(value)
        own: list[str] = []
        for column_id in getattr(table, "columns", ()) or ():
            column = corpus.get(str(column_id))
            physical = getattr(column, "physical_name", None)
            if isinstance(physical, str) and physical:
                names.append(physical)
                own.append(physical)
        # Keyed by both the bare and the schema-qualified table name, because a reference may
        # carry either. A table whose *own* columns collide keeps no entry: within one table
        # the collision is real and the flat map's refusal is the right answer.
        own_spellings, own_ambiguous = fold_map(own)
        if own_ambiguous:
            continue
        physical_name = getattr(table, "physical_name", None)
        schema = getattr(table, "schema", None)
        if isinstance(physical_name, str) and physical_name:
            bare = fold(physical_name)
            # Two licensed schemas both declaring ``country`` must not have one of them own the
            # bare key by sort order -- the same collision this function refuses, one scope up.
            # ``None`` poisons it; the schema-qualified key still resolves.
            by_table[bare] = None if bare in by_table else own_spellings
            if isinstance(schema, str) and schema:
                by_table[f"{fold(schema)}.{bare}"] = own_spellings
    return (*fold_map(names), {k: v for k, v in by_table.items() if v is not None})


def _sources(tree: exp.Expression) -> dict[str, str]:
    """``{handle (folded) -> by_table key}``, **only for handles that name exactly one table**.

    A handle used for two different tables anywhere in the statement is dropped, not guessed.
    That is what makes this safe to run beside ``binding.py``, which resolves per scope with
    ``traverse_scope``: a handle unambiguous over the whole tree resolves the same way in every
    scope, so the two resolvers cannot disagree. A handle that is not tree-unambiguous falls
    through to the flat pass and refuses exactly as it did before this resolver existed.

    An aliased table is registered **under its alias only**, the rule
    ``binding.py::_classify_sources`` states — Postgres hides the table name behind an alias.

    Three statements motivated both rules; the first draft rewrote each one **wrongly** while
    reaching a passing verdict, which is the precise failure ``r_ambiguous_fold`` exists to
    prevent:

    * ``... FROM s.people AS T1 WHERE id IN (SELECT T1.name FROM s.places AS T1)`` — the inner
      ``T1`` is ``s.places``; the draft spelled it from ``s.people``.
    * the same shape across a ``UNION``.
    * ``FROM s.customers AS c JOIN s.orders AS customers`` — ``customers`` is an *alias* of
      ``s.orders``, and the draft resolved it to the table of that name. ``bind()`` accepts the
      statement, so nothing downstream would have caught it.

    A CTE name is excluded: it is a name the statement defines, not one the corpus declares.
    """
    defined = {fold(str(c.alias_or_name)) for c in tree.find_all(exp.CTE) if c.alias_or_name}
    out: dict[str, str] = {}
    conflicted: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = fold(str(table.name or ""))
        if not name or name in defined:
            continue
        key = f"{fold(str(table.db))}.{name}" if table.db else name
        handle = fold(str(table.alias or "")) or name
        if handle in defined:
            continue
        if out.get(handle, key) != key:
            conflicted.add(handle)
        out.setdefault(handle, key)
    for handle in conflicted:
        out.pop(handle, None)
    return out


def canonicalise(
    sql: str,
    *,
    spellings: Mapping[str, str],
    ambiguous: frozenset[str] = frozenset(),
    dialect: str = DEFAULT_DIALECT,
    by_table: Mapping[str, Mapping[str, str]] | None = None,
) -> str | CheckVerdict:
    """Rewrite every declared identifier to the corpus's spelling, **and quote it**.

    Unknown identifiers — aliases, CTE names — pass through untouched: the binding rule
    resolves them, and rewriting one would be inventing a name.

    **Quoting is unconditional for a known identifier, including one already spelled
    correctly** (ADR 0008 D2). Rewriting alone emits the name unquoted, and Postgres
    folds an unquoted identifier to lower case, so the engine looks for ``address.cbsa``
    and reports no such relation. 81 of 738 tables and 610 of 6,909 columns in the
    obfuscated lake are mixed-case, and each failed at the engine *after* a passing
    verdict, because ``check()`` compares folded keys. "Quote only when necessary" is
    rejected: *necessary* is a predicate over the engine's folding rules, which is the
    thing that was got wrong. Always-quoting also gives one spelling per identifier, so
    ``generated_sql`` is comparable across runs.

    Returns the rewritten SQL, or a refusing :class:`CheckVerdict` when an identifier's
    folded form is ambiguous in the corpus.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError as err:
        return refuse("r_unparseable", f"{type(err).__name__}: {err}")
    if tree is None:
        return refuse("r_empty_statement", "no statement to canonicalise")

    # Qualified columns first. ``T1."Name"`` names exactly one table, so the flat map's
    # ambiguity — which exists only because ~26 licensed tables share one namespace — cannot
    # apply to it. Resolving these here is what stops a genuine collision two tables away from
    # refusing a reference that was never in doubt.
    settled: set[int] = set()
    if by_table is None:
        by_table = {}
    if by_table:
        sources = _sources(tree)
        for column in tree.find_all(exp.Column):
            identifier = column.this
            if not isinstance(identifier, exp.Identifier):
                continue
            own = by_table.get(sources.get(fold(column.table or ""), ""))
            if own is None:
                continue
            declared = own.get(fold(identifier.name))
            if declared is None:
                # Not this table's column. Left to the flat pass and then to BINDING, which is
                # the layer that decides what a reference resolves to.
                continue
            identifier.set("this", declared)
            identifier.set("quoted", True)
            settled.add(id(identifier))

    for identifier in tree.find_all(exp.Identifier):
        if id(identifier) in settled:
            continue
        key = fold(identifier.name)
        if key in ambiguous:
            return refuse(
                "r_ambiguous_fold",
                f"{identifier.name} folds onto more than one declared identifier and carries "
                "no qualifier that names which, so the engine would pick one and the column "
                "layer would approve another",
            )
        declared = spellings.get(key)
        if declared is None:
            # An alias, a CTE, an output label. Left unquoted: quoting a name we cannot
            # vouch for adds nothing, and the binding rule is what resolves it.
            continue
        if declared != identifier.name:
            identifier.set("this", declared)
        identifier.set("quoted", True)
    return tree.sql(dialect=dialect)


def _existing_limit(tree: exp.Query) -> int | None:
    """The root's own limit as an integer, or ``None`` if there is not one to read.

    ``None`` means *replace*: no limit, a non-numeric limit (a bound parameter), or an
    unrecognised shape. "Leave it alone" on any of those is how ``LIMIT 100000000``
    survived.
    """
    node = tree.args.get("limit")
    if node is None:
        return None
    for candidate in (node.args.get("expression"), node.args.get("count")):
        if isinstance(candidate, exp.Literal) and not candidate.is_string:
            try:
                return int(candidate.this)
            except (TypeError, ValueError):
                return None
    return None


def apply_row_limit(sql: str, *, max_rows: int, dialect: str = DEFAULT_DIALECT) -> str | CheckVerdict:
    """Force ``min(existing, max_rows + 1)`` at the statement root."""
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except SqlglotError as err:
        return refuse(
            "r_unparseable",
            f"cannot inject a row limit into a statement that does not parse: "
            f"{type(err).__name__}: {err}",
        )
    if tree is None:
        return refuse("r_empty_statement", "no statement to limit")
    if not isinstance(tree, exp.Query):
        # Only a query has a root LIMIT to set, and anything reaching step 4 cleared
        # check(), so this is unreachable. It refuses rather than returning the
        # statement unlimited.
        return refuse(
            "r_not_a_read", f"{type(tree).__name__} has no statement-level row limit"
        )
    ceiling = max_rows + 1
    existing = _existing_limit(tree)
    limit = ceiling if existing is None else min(existing, ceiling)
    return tree.limit(limit).sql(dialect=dialect)


def prepare(
    sql: str,
    *,
    licensed: frozenset[str] | None,
    corpus: AnalystCorpus | None,
    spellings: Mapping[str, str] | None,
    ambiguous_folds: frozenset[str] = frozenset(),
    spellings_by_table: Mapping[str, Mapping[str, str]] | None = None,
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> Prepared:
    """Steps 1–4. The only function that may produce a string for execution.

    ``spellings`` has **no default** (ADR 0008 D7: an optional control argument is a
    control that will be un-wired — this one shipped optional and no production caller
    passed it). Pass ``spellings={}`` to state that this call declares none; ``None``
    raises, on ``check()``'s reasoning for ``licensed=None`` — absence is not
    permission and must not be spellable by omission.
    """
    if spellings is None:
        raise GovernanceUsageError(
            "prepare() requires `spellings`. Build it with `spellings_for(corpus, "
            "licensed)`, or pass `spellings={}` to state that this call declares none. "
            "It has no default because it had one: the only production caller omitted "
            "it, so every mixed-case identifier in the corpus reached the engine folded "
            "to lower case and failed after a passing verdict (ADR 0008 P1)."
        )
    policy = policy or GovernancePolicy()

    if has_control_characters(sql):
        return Prepared(
            verdict=refuse(
                "r_control_characters",
                "the statement contains control, bidi or zero-width characters, which "
                "NFKC would fold into ordinary text",
            ),
            sql=None,
            raw=sql,
            canonical=None,
        )

    normalised = normalise(sql)
    canonical = canonicalise(
        normalised,
        spellings=spellings or {},
        ambiguous=ambiguous_folds,
        dialect=dialect,
        by_table=spellings_by_table,
    )
    if isinstance(canonical, dict):
        return Prepared(verdict=canonical, sql=None, raw=sql, canonical=None)

    verdict = check(
        canonical,
        licensed=licensed,
        corpus=corpus,
        default_schema=default_schema,
        dialect=dialect,
        policy=policy,
    )
    if not verdict["passed"]:
        return Prepared(verdict=verdict, sql=None, raw=sql, canonical=canonical)

    limited = apply_row_limit(canonical, max_rows=policy.max_rows, dialect=dialect)
    if isinstance(limited, dict):
        return Prepared(verdict=limited, sql=None, raw=sql, canonical=canonical)
    return Prepared(verdict=verdict, sql=limited, raw=sql, canonical=canonical)
