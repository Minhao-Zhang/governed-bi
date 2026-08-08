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
) -> tuple[Mapping[str, str], frozenset[str]]:
    """``(folded -> declared spelling, ambiguous folds)`` for this turn's licensed tables.

    Scoped to ``licensed`` (corpus-wide collisions would refuse almost everything).
    Ambiguous folds refuse at canonicalisation. Pure function of ``(corpus, licensed)``.
    """
    names: list[str] = []
    for table_id in sorted(licensed):
        table = corpus.get(table_id)
        if table is None:
            continue
        for attr in ("physical_name", "schema"):
            value = getattr(table, attr, None)
            if isinstance(value, str) and value:
                names.append(value)
        for column_id in getattr(table, "columns", ()) or ():
            column = corpus.get(str(column_id))
            physical = getattr(column, "physical_name", None)
            if isinstance(physical, str) and physical:
                names.append(physical)
    return fold_map(names)


def canonicalise(
    sql: str,
    *,
    spellings: Mapping[str, str],
    ambiguous: frozenset[str] = frozenset(),
    dialect: str = DEFAULT_DIALECT,
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

    for identifier in tree.find_all(exp.Identifier):
        key = fold(identifier.name)
        if key in ambiguous:
            return refuse(
                "r_ambiguous_fold",
                f"{identifier.name} folds onto more than one declared identifier, so the "
                "engine would pick one and the column layer would approve another",
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
        normalised, spellings=spellings or {}, ambiguous=ambiguous_folds, dialect=dialect
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
