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


def spellings_for(
    corpus: AnalystCorpus, licensed: frozenset[str]
) -> tuple[Mapping[str, str], frozenset[str]]:
    """``(folded → declared spelling, ambiguous folds)`` for this turn's licensed tables.

    **Scoped to ``licensed``, and the scope is the whole design.** Built over the entire
    corpus instead, 30 folded names carry more than one declared spelling — ``name``,
    ``id``, ``city``, ``code``, ``type``, ``title`` — so every one of them would land in
    the ambiguous set and ``r_ambiguous_fold`` would refuse almost every query ever
    written. Restricted to the tables a turn actually licensed, the collisions are 2 in
    57 schemas (``address.District``/``district``, ``card_games.multiverseId``/
    ``multiverseid``, measured 2026-08-04), and refusing *there* is right: the engine
    folds the reference to one of them, and under obfuscation the one it picks can be the
    decoy.

    Per turn rather than per run, which is safe because the input is per turn: this is a
    pure function of ``(corpus, licensed)`` and ``licensed`` is already this turn's. It is
    not the kind of per-turn derivation ``retrieve/structure.py`` argues against — that
    one is a projection of the *corpus*, where two turns disagreeing would mean two turns
    ran against different shapes.

    Reads the table's own ``columns`` rather than scanning every column asset, which is
    an exact index because ``parent_table`` and ``columns`` both hold ids (ADR 0008 D4).
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

    Unknown identifiers — model-invented aliases, CTE names — pass through untouched.
    They are resolved by the binding rule, not by spelling, and rewriting them would
    be inventing a name.

    **Quoting is unconditional for a known identifier, including one already spelled
    correctly.** ADR 0008 D2. Rewriting alone is not enough and this was the shipped
    defect: ``identifier.set("this", "CBSA")`` emits ``address.CBSA`` *unquoted*, and
    Postgres folds an unquoted identifier to lower case, so it looks for
    ``address.cbsa`` and reports that the relation does not exist. 81 of 738 tables and
    610 of 6,909 columns in the obfuscated lake are mixed-case, and each of them failed
    at the engine **after** a passing verdict — because ``check()`` compares folded keys
    and the fold made the wrong spelling match.

    "Quote only when necessary" is not the rule, deliberately: *necessary* is a
    predicate over the engine's folding and quoting rules, and getting it wrong is
    exactly the above. Always-quoting is also what makes the ledger's ``generated_sql``
    comparable across runs — one spelling per identifier, forever.

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
            # Not a name the corpus declares: an alias, a CTE, an output label. Left
            # alone *and left unquoted*, because quoting a name we cannot vouch for adds
            # nothing and the binding rule is what resolves it.
            continue
        if declared != identifier.name:
            identifier.set("this", declared)
        identifier.set("quoted", True)
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
    spellings: Mapping[str, str] | None,
    ambiguous_folds: frozenset[str] = frozenset(),
    default_schema: str | None = None,
    dialect: str = DEFAULT_DIALECT,
    policy: GovernancePolicy | None = None,
) -> Prepared:
    """Steps 1–4. The only function that may produce a string for execution.

    ``spellings`` has **no default**, and that is the fix for the defect this signature
    caused. ADR 0008 D7: *an optional control argument is a control that will be
    un-wired.* It shipped as ``spellings: ... = None``, the only production caller never
    passed it, ``fold_map`` — which produces it — had no caller in ``src/`` at all, and
    two green tests exercised canonicalisation in isolation with hand-written dicts. A
    control with a producer nobody calls and a consumer nobody feeds.

    Pass ``spellings={}`` to state that this call declares none. ``None`` raises, on the
    same reasoning ``check()`` gives for ``licensed=None``: absence is not permission,
    and it must not be spellable by omission.
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
