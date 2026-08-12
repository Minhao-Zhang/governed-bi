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
from .scopes import iter_scopes

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

    Two early exits remain — one ``continue`` for a licensed id the corpus does not hold, and the
    ``physical_name`` guard below, which skips the write rather than the iteration. Neither can
    poison a bare key, because neither has a name
    to poison it with. Both fail closed elsewhere — an absent table contributes no allowed column
    keys, so every reference to it refuses at COLUMNS, and a table with no physical name cannot
    be written in a statement at all. Deriving a name from the id instead would put a second
    source of truth for a table's name in the file whose job is to agree with the corpus.
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
        # carry either.
        own_spellings, own_ambiguous = fold_map(own)
        physical_name = getattr(table, "physical_name", None)
        schema = getattr(table, "schema", None)
        if isinstance(physical_name, str) and physical_name:
            bare = fold(physical_name)
            # Two licensed schemas both declaring ``country`` must not have one of them own the
            # bare key by sort order -- the same collision this function refuses, one scope up.
            # ``None`` poisons it; the schema-qualified key still resolves.
            #
            # **The poison write runs before the own-collision guard, and that order is the whole
            # content of the fix** (open-work.md 3.2a, second defect). It used to be a ``continue``
            # above this block, so a self-colliding table neither registered nor poisoned its bare
            # key and another schema's table of the same name took sole ownership: ``FROM country``
            # bound to the self-colliding table while ``country.code`` was spelled from the other
            # one, behind a passing verdict, where ``r_ambiguous_fold`` was the required answer.
            by_table[bare] = None if own_ambiguous or bare in by_table else own_spellings
            # The schema-qualified key is still withheld from a self-colliding table: within one
            # table the collision is real and the flat map's refusal is the right answer.
            if isinstance(schema, str) and schema and not own_ambiguous:
                by_table[f"{fold(schema)}.{bare}"] = own_spellings
    return (*fold_map(names), {k: v for k, v in by_table.items() if v is not None})


def _handles_in_scope(view) -> dict[str, str | None]:
    """One scope's ``{handle (folded) -> by_table key}``. ``None`` is a **derived** source.

    ``binding.py::_classify_sources``, restated over the same ``scope.sources`` mapping and with
    the same two rules, because the whole justification for this resolver is that it and
    ``bind()`` must not disagree about what a handle names:

    * an **aliased** table registers under its alias only — Postgres hides the table name behind
      an alias, so resolving ``sales.customers.id`` against ``FROM sales.customers AS cc`` would
      approve a reference the engine rejects;
    * anything that is not an ``exp.Table`` is a derived source — a subquery, a CTE, a ``VALUES``
      list — and maps to ``None``. The statement defines that name, so the corpus declares no
      spelling for what it exposes, and a reference through it must fall to the flat pass.

    **The ``None`` is defensive and the adversarial suite cannot falsify it — stated because an
    unmarked untested branch is the thing this file keeps being audited for.** Deleting it (so a
    derived handle is simply absent) changes nothing the 115 cases can see: ``_column_sources``
    then walks to the ancestor scope, which only differs when a derived alias in an *inner* scope
    shadows a base handle in an *outer* one, and reaching that needs the derived source to expose
    a column whose folded name is ambiguous in the corpus. Every statement of that shape refuses
    at the flat pass anyway, on the projection alias inside the subquery — which is an identifier
    canonicalisation cannot settle. So the branch is what makes this resolver agree with
    ``bind()`` *by construction* rather than by the flat pass happening to catch the difference,
    and that is the whole reason to keep it; it is not a claim that a test would notice its loss.
    Re-verified 2026-08-12 by deleting it: 210/210 govern tests pass and the suite reports 0
    failures over its 115 cases either way.
    """
    local: dict[str, str | None] = {}
    for alias, source in view.scope.sources.items():
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            name = fold(str(source.name))
            if not name:
                continue
            handle = fold(str(alias)) if alias else name
            local[handle] = f"{fold(str(source.db))}.{name}" if source.db else name
        elif alias:
            local[fold(str(alias))] = None
    return local


def _column_sources(tree: exp.Expression) -> dict[int, str]:
    """``{id(Column node) -> by_table key}``, resolved **per scope**.

    A handle means whatever the scope the reference sits in says it means, and nothing else in
    the tree gets a vote. That is ``binding.py``'s rule — ``_lookup`` walks the reference's own
    scope and then its ancestors, for correlated references — and matching it is the point:
    ``r_ambiguous_fold`` exists to catch the two resolvers disagreeing, so a resolver that
    answers a *different question* from ``bind()`` is the defect rather than the guard.

    Three statements motivated the per-scope rule; a first draft resolved each one over the whole
    tree and rewrote it **wrongly** while reaching a passing verdict, which is precisely what
    ``r_ambiguous_fold`` exists to prevent:

    * ``... FROM s.people AS T1 WHERE id IN (SELECT T1.name FROM s.places AS T1)`` — the inner
      ``T1`` is ``s.places``; the draft spelled it from ``s.people``. Here the inner scope owns
      its own ``T1`` and the outer one owns another, so neither borrows.
    * the same shape across a ``UNION``.
    * ``FROM s.customers AS c JOIN s.orders AS customers`` — ``customers`` is an *alias* of
      ``s.orders``, and the draft resolved it to the table of that name.

    And a fourth, the one that made the whole-tree answer unsafe rather than merely coarse
    (open-work.md 3.2a, first defect)::

        SELECT p.name
        FROM (SELECT o.name, x.name FROM s.places AS o JOIN s.people AS x ON o.id = x.id) AS p
        WHERE EXISTS (SELECT 1 FROM s.people AS p WHERE p.id = 1)

    With ``s.places.name`` and ``s.people.Name`` both licensed that reached ``passed: True`` and
    emitted ``p."Name"`` — the derived source exposes both spellings, so it executes and reads a
    different column of a different table, and ``bind()`` marks ``p.name`` ``opaque`` so nothing
    downstream looks at it. The outer ``p`` is derived *in the scope the reference sits in*, so
    it resolves to nothing here and falls to the flat pass, which refuses.

    **The first fix for that was tree-wide and cost false refusals its own controls could not
    see.** It collected every derived handle anywhere in the tree and dropped it from the map
    globally, so a handle that is a derived alias in one scope lost per-table spelling in *every*
    scope::

        SELECT r."Name" FROM sales.regions AS r WHERE EXISTS (SELECT 1 FROM (SELECT 1 AS z) AS r)

    ``r."Name"`` names exactly one table in its own scope and was refused ``r_ambiguous_fold``
    because an unrelated subquery two scopes away reused the letter. Both spellings of that shape
    are benign cases in ``adversarial.toml`` now, so the false-refusal rate covers the only shape
    this resolver changes.

    Returns nothing for a non-query root (``iter_scopes`` yields no scopes there); every
    reference then falls to the flat pass, which is where a statement with no query scope belongs.
    """
    views = iter_scopes(tree)
    if not views:
        return {}
    per_scope = {id(view.scope): _handles_in_scope(view) for view in views}

    out: dict[int, str] = {}
    for view in views:
        for column in view.columns():
            handle = fold(str(column.table or ""))
            if not handle:
                continue
            # `binding.py::_lookup`'s walk: this scope, then its ancestors, because a correlated
            # reference resolves in a named ancestor scope.
            scope = view.scope
            while scope is not None:
                local = per_scope.get(id(scope))
                if local is not None and handle in local:
                    key = local[handle]
                    if key is not None:
                        out[id(column)] = key
                    break
                scope = scope.parent
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
        sources = _column_sources(tree)
        for column in tree.find_all(exp.Column):
            identifier = column.this
            if not isinstance(identifier, exp.Identifier):
                continue
            own = by_table.get(sources.get(id(column), ""))
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
