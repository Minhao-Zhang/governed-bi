"""The transformation pipeline (ADR 0006 §3). Fixed order, stated once — G4.

::

    1. normalise    NFKC, after the control-character check, never before
    2. canonicalise rewrite each identifier to the corpus's declared spelling
    3. check()      the layer stack
    4. limit        min(existing_limit, max_rows + 1) at the statement root
    5. execute      the caller's job; ports.Connector is the last hop
    6. ledger       sha256 of the exact string produced by step 4

**Canonicalisation precedes checking**, so the verdict is about the statement that
runs. ADR 0006's first draft left the order unstated and called canonicalisation
"cosmetic-but-recorded, never a control", which is false: an ambiguous fold — two
corpus columns differing only by case — left unrewritten is folded by Postgres to one
of them, possibly the **decoy**, so the column layer approves one binding and the
engine reads another. Ambiguous folds therefore refuse.

**The row limit is ``min(existing, max_rows + 1)``, and a parse failure at step 4
refuses.** v1 left the limit unchanged when a ``LIMIT`` already existed, so
``LIMIT 100000000`` defeated the cap — and it left it unchanged on parse failure, on
a path that also served executors where ``check()`` never ran. The ``+ 1`` is what
makes truncation *detectable* rather than inferable.

**Step 4 is the only transformation after the check**, and it is structural: a
``LIMIT`` at the statement root cannot change which tables, columns or functions the
statement touches, which is why it can be last and why the ledger hashes its output
rather than the checked string. Every other rewrite happens before the verdict.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Mapping

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from ..corpus.analyst import AnalystCorpus
from .check import check
from .guard import has_control_characters
from .identifiers import fold
from .layers import CheckVerdict, refuse
from .policy import DEFAULT_DIALECT, GovernancePolicy

__all__ = ["Prepared", "normalise", "canonicalise", "apply_row_limit", "prepare"]


@dataclass(frozen=True, slots=True)
class Prepared:
    """The outcome of steps 1–4. ``sql`` is ``None`` when nothing may run.

    Both the raw input and the canonical form are carried, because "what did the model
    write" and "what did we check" are different questions and v1 could answer neither
    from its record.
    """

    verdict: CheckVerdict
    #: The exact string to hand to ``Connector.execute``, limit included.
    sql: str | None
    raw: str
    canonical: str | None


def normalise(text: str) -> str:
    """NFKC. **Only** after :func:`~governed_bi.govern.guard.has_control_characters`.

    The order is kept from ADR 0006 §3, though not for the reason the ADR gives — NFKC
    does not strip any character the encoding rule rejects (see
    :mod:`~governed_bi.govern.guard`). It is still the correct order: NFKC *rewrites*
    text, so any check placed after it is a check on a string nobody sent.
    """
    return unicodedata.normalize("NFKC", text)


def canonicalise(
    sql: str,
    *,
    spellings: Mapping[str, str],
    ambiguous: frozenset[str] = frozenset(),
    dialect: str = DEFAULT_DIALECT,
) -> str | CheckVerdict:
    """Rewrite every identifier to the corpus's declared spelling.

    Unknown identifiers — model-invented aliases, CTE names — pass through untouched.
    They are resolved by the binding rule, not by spelling, and rewriting them would
    be inventing a name.

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
        if identifier.quoted:
            # A quoted identifier is already an exact spelling and the engine will not
            # fold it. Rewriting it would be overruling the author.
            continue
        key = fold(identifier.name)
        if key in ambiguous:
            return refuse(
                "r_ambiguous_fold",
                f"{identifier.name} folds onto more than one declared identifier, so the "
                "engine would pick one and the column layer would approve another",
            )
        declared = spellings.get(key)
        if declared is not None and declared != identifier.name:
            identifier.set("this", declared)
    return tree.sql(dialect=dialect)


def _existing_limit(tree: exp.Query) -> int | None:
    """The root's own limit as an integer, or ``None`` if there is not one to read.

    ``None`` means *replace*: no limit, a non-numeric limit (a bound parameter), or a
    shape this code does not understand. Every one of those is a case where "leave it
    alone" is how ``LIMIT 100000000`` survived.
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
        # Only a query has a root LIMIT to set. Anything else reaching step 4 has
        # already cleared check(), so this is unreachable rather than tolerated — and
        # it refuses rather than returning the statement unlimited, which is the v1
        # behaviour this whole function replaces.
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
    spellings: Mapping[str, str] | None = None,
    ambiguous_folds: frozenset[str] = frozenset(),
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> Prepared:
    """Steps 1–4. The only function that may produce a string for execution."""
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
