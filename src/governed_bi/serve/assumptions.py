"""The assumptions an answer rests on, and the server's checks of them (ADR 0018).

The checks are pure and make no model call: the ``answer`` tool runs them in its body, so a
rejected claim goes back to the agent as a reply it can correct.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterable, Mapping, Sequence
from typing import Any, Literal, NamedTuple, TypedDict, cast

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.scope import Scope

from governed_bi.corpus.schema import Binding, TermAsset
from governed_bi.govern.identifiers import column_key, fold, normalise_column_key
from governed_bi.govern.policy import DEFAULT_DIALECT
from governed_bi.govern.scopes import iter_scopes
from governed_bi.serve.ledger import answering_attempts, attempt_field

__all__ = [
    "Basis",
    "STATED_BASES",
    "Assumption",
    "AssumptionClaim",
    "CompetingTerm",
    "AmbiguousGuess",
    "ambiguous_guesses",
    "check_layer_claims",
    "delivered_statement",
    "found_in_query",
    "merged",
]

Basis = Literal["layer", "convention", "guess", "found_in_query"]

#: What the model may write. ``found_in_query`` is the server's alone.
STATED_BASES: tuple[str, ...] = ("layer", "convention", "guess")


class AssumptionClaim(BaseModel):
    """One entry as the model writes it in the ``answer`` tool call."""

    statement: str = Field(description="One sentence in the person's words.")
    about: str | None = Field(default=None, description="The phrase of the question it concerns.")
    basis: Literal["layer", "convention", "guess"]
    asset_id: str | None = Field(default=None, description="Required when basis is layer.")


class Assumption(TypedDict):
    """One entry as stored on the turn record. A dict, because checkpoints round-trip JSON."""

    statement: str
    about: str | None
    basis: Basis
    asset_id: str | None


class CompetingTerm(TypedDict):
    """One retrieved term that binds the phrase differently from another."""

    id: str
    name: str
    binding: str


class AmbiguousGuess(TypedDict):
    """One guess, with every term that matches its phrase."""

    about: str
    terms: list[CompetingTerm]


def ambiguous_guesses(
    assumptions: Sequence[Mapping[str, Any]],
    terms: Sequence[TermAsset],
) -> list[AmbiguousGuess]:
    """Guesses whose phrase matches two terms that bind differently.

    ``about`` is compared case-folded, with surrounding whitespace stripped.
    Only basis ``guess`` is checked, and an empty phrase never matches.
    """
    found: list[AmbiguousGuess] = []
    for item in assumptions:
        if item.get("basis") != "guess":
            continue
        phrase = _phrase_key(item.get("about"))
        if not phrase:
            continue
        matched = [term for term in terms if phrase in _term_phrases(term)]
        if len({term.binding for term in matched}) < 2:
            continue
        about = item.get("about")
        found.append({
            "about": about.strip() if isinstance(about, str) else phrase,
            "terms": [_competing(term) for term in matched],
        })
    return found


def _phrase_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


def _term_phrases(term: TermAsset) -> set[str]:
    phrases = {_phrase_key(term.name)}
    phrases.update(_phrase_key(synonym) for synonym in term.synonyms)
    phrases.discard("")
    return phrases


def _competing(term: TermAsset) -> CompetingTerm:
    return {"id": term.id, "name": term.name, "binding": _binding_text(term.binding)}


def _binding_text(binding: Binding | None) -> str:
    if binding is None:
        return "no binding"
    return f"{binding.target_type.value} {binding.target_id}"


def check_layer_claims(
    claims: Iterable[AssumptionClaim],
    *,
    known_assets: Container[str],
    retrieved_assets: Container[str],
) -> list[Assumption]:
    """A ``layer`` claim whose asset is unknown or was not retrieved this turn becomes ``guess``."""
    checked: list[Assumption] = []
    for claim in claims:
        basis: Basis = claim.basis
        if basis == "layer" and (
            not claim.asset_id
            or claim.asset_id not in known_assets
            or claim.asset_id not in retrieved_assets
        ):
            # Keep the claimed id: the record should show what was named, not a blank guess.
            basis = "guess"
        checked.append(
            {
                "statement": claim.statement,
                "about": claim.about,
                "basis": basis,
                "asset_id": claim.asset_id,
            }
        )
    return checked


def delivered_statement(attempts: Sequence[Mapping[str, Any]]) -> str | None:
    """The checked text of the statement whose rows became ``result_table``, before the row limit."""
    text: str | None = None
    ran = False
    for attempt in answering_attempts(attempts):
        if attempt_field(attempt, "executed_sql") is None:
            continue
        ran = True
        text = _text_before_limit(attempt)
    return text if ran else None


def found_in_query(
    statement: str | None,
    *,
    question: str,
    suspect_columns: Container[str] = frozenset(),
    dialect: str | None = None,
) -> list[Assumption]:
    """Choices in the delivered statement, before the row limit was added. ``None`` gives ``[]``."""
    if statement is None or not statement.strip():
        return []
    try:
        tree = sqlglot.parse_one(statement, dialect=dialect or DEFAULT_DIALECT)
    except SqlglotError:
        return []
    if tree is None:
        return []
    order = {id(node): index for index, node in enumerate(tree.walk())}
    bound = _sources(tree)
    # Category order is fixed. A finding higher in the text does not jump an earlier category.
    found = [item for _, item in _filter_findings(tree, question, order)]
    found.extend(item for _, item in _join_findings(tree, bound, order))
    distinct = tree.find(exp.Distinct)
    if distinct is not None:
        found.append(_finding("The statement uses DISTINCT, so duplicate rows are collapsed."))
    if tree.find(exp.Limit) is not None:
        found.append(_finding("The statement has its own LIMIT."))
    found.extend(_suspect_findings(tree, bound, _suspect_keys(suspect_columns), order))
    return found


def merged(stated: Sequence[Assumption], found: Sequence[Assumption]) -> list[Assumption]:
    """The model's list, then each server finding."""
    return [*stated, *found]


class _Bound(NamedTuple):
    handles: dict[int, dict[str, tuple[str | None, str] | None]]
    bases: dict[int, list[tuple[str | None, str]]]
    column_scope: dict[int, Scope]


_COMPARISONS = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Between,
)


def _finding(statement: str) -> Assumption:
    return {
        "statement": statement,
        "about": None,
        "basis": "found_in_query",
        "asset_id": None,
    }


def _text_before_limit(attempt: Any) -> str | None:
    """``checked_sql`` absent means an old checkpoint: the only stored text is ``executed_sql``."""
    if isinstance(attempt, Mapping):
        text = attempt["checked_sql"] if "checked_sql" in attempt else attempt.get("executed_sql")
    elif hasattr(attempt, "checked_sql"):
        text = attempt.checked_sql
    else:
        text = getattr(attempt, "executed_sql", None)
    return text if isinstance(text, str) else None


def _suspect_keys(columns: Container[str]) -> set[str]:
    keys: set[str] = set()
    # Container promises membership, not iteration. Callers pass a set or a list.
    for raw in cast(Iterable[str], columns):
        try:
            keys.add(normalise_column_key(str(raw), None))
        except ValueError:
            continue
    return keys


def _sources(tree: exp.Expr) -> _Bound:
    handles: dict[int, dict[str, tuple[str | None, str] | None]] = {}
    bases: dict[int, list[tuple[str | None, str]]] = {}
    column_scope: dict[int, Scope] = {}
    try:
        views = iter_scopes(tree)
    except (OptimizeError, SqlglotError):
        return _Bound(handles, bases, column_scope)
    for view in views:
        local: dict[str, tuple[str | None, str] | None] = {}
        own: list[tuple[str | None, str]] = []
        try:
            selected = list(view.scope.selected_sources.items())
        except OptimizeError:
            selected = []
        for alias, (_node, source) in selected:
            handle = fold(str(alias)) if alias else ""
            if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
                schema = str(source.db) if source.db else None
                name = str(source.name)
                local[handle or fold(name)] = (schema, name)
                own.append((schema, name))
            elif handle:
                local[handle] = None
        handles[id(view.scope)] = local
        bases[id(view.scope)] = own
        for column in view.columns():
            column_scope[id(column)] = view.scope
    return _Bound(handles, bases, column_scope)


def _lookup(column: exp.Column, bound: _Bound) -> tuple[str | None, str] | None:
    if not isinstance(column.this, exp.Identifier):
        return None
    if column.db and column.table:
        return str(column.db), str(column.table)
    scope: Scope | None = bound.column_scope.get(id(column))
    qualifier = fold(column.table) if column.table else ""
    if qualifier:
        while scope is not None:
            local = bound.handles.get(id(scope), {})
            if qualifier in local:
                return local[qualifier]
            scope = scope.parent
        return None
    while scope is not None:
        own = bound.bases.get(id(scope), [])
        if len(own) == 1:
            return own[0]
        if len(own) > 1:
            return None
        scope = scope.parent
    return None


def _relation(schema: str | None, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def _expr_relation(node: exp.Expr) -> str:
    if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
        return _relation(str(node.db) if node.db else None, str(node.name))
    alias = node.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Identifier):
        return str(alias.this.name)
    return "a subquery"


def _unwrap(node: exp.Expr) -> exp.Expr:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _literal(node: exp.Expr) -> tuple[str, bool] | None:
    node = _unwrap(node)
    if isinstance(node, exp.Neg):
        inner = _unwrap(node.this)
        if isinstance(inner, exp.Literal) and not inner.is_string and _numeric(str(inner.this)):
            return f"-{inner.this}", False
        return None
    if not isinstance(node, exp.Literal):
        return None
    text = str(node.this)
    if node.is_string:
        return text, True
    if _numeric(text):
        return text, False
    return None


def _numeric(text: str) -> bool:
    try:
        number = float(text)
    except ValueError:
        return False
    return number == number and number not in (float("inf"), float("-inf"))


def _has_column(node: exp.Expr) -> bool:
    return isinstance(_unwrap(node), exp.Column) or node.find(exp.Column) is not None


def _column_phrase(side: exp.Expr) -> str:
    side = _unwrap(side)
    column = side if isinstance(side, exp.Column) else side.find(exp.Column)
    if not isinstance(column, exp.Column) or not isinstance(column.this, exp.Identifier):
        return "a column"
    return f"{column.table}.{column.name}" if column.table else column.name


def _in_filter(node: exp.Expr) -> bool:
    current: exp.Expr | None = node
    while current is not None:
        parent = current.parent
        if isinstance(parent, (exp.Where, exp.Having)):
            return True
        if isinstance(parent, exp.Join) and parent.args.get("on") is current:
            return True
        current = parent
    return False


def _owned_by(node: exp.Expr, owner: exp.Expr) -> bool:
    current: exp.Expr | None = node
    while current is not None and current is not owner:
        if isinstance(current, (exp.Select, exp.Union, exp.Subquery)):
            return False
        current = current.parent
    return current is owner


def _compared(predicate: exp.Expr) -> list[tuple[exp.Expr, exp.Expr]]:
    if isinstance(predicate, exp.In):
        if predicate.args.get("query") is not None:
            return []
        return [(expr, predicate.this) for expr in predicate.expressions]
    if isinstance(predicate, exp.Between):
        low = predicate.args.get("low")
        high = predicate.args.get("high")
        return [(side, predicate.this) for side in (low, high) if side is not None]
    left = predicate.this
    right = predicate.expression
    if left is None or right is None:
        return []
    return [(right, left), (left, right)]


def _mentioned(text: str, question: str, *, is_string: bool) -> bool:
    if is_string:
        return text.lower() in question.lower()
    # A number counts only as a whole number: the question "top 10" does not mention 1.
    return re.search(rf"(?<![\d.]){re.escape(text)}(?![\d.]*\d)", question) is not None


def _filter_findings(
    tree: exp.Expr, question: str, order: dict[int, int]
) -> list[tuple[int, Assumption]]:
    found: list[tuple[int, Assumption]] = []
    for predicate in tree.find_all(*_COMPARISONS):
        if not _in_filter(predicate):
            continue
        for literal_node, other in _compared(predicate):
            parsed = _literal(literal_node)
            if parsed is None or not _has_column(other):
                continue
            text, is_string = parsed
            if _mentioned(text, question, is_string=is_string):
                continue
            shown = f"'{text}'" if is_string else text
            sentence = (
                f"The statement compares {_column_phrase(other)} to {shown}, "
                "which the question does not mention."
            )
            found.append((order.get(id(literal_node), 0), _finding(sentence)))
    found.sort(key=lambda item: item[0])
    return found


def _labels(expr: exp.Expr, owner: exp.Expr, bound: _Bound) -> list[str]:
    labels: list[str] = []
    for column in expr.find_all(exp.Column):
        if not _owned_by(column, owner):
            continue
        source = _lookup(column, bound)
        if source is None:
            continue
        label = _relation(*source)
        if label not in labels:
            labels.append(label)
    return labels


def _explicit_inner(join: exp.Join) -> bool:
    side = str(join.args.get("side") or "").upper()
    kind = str(join.args.get("kind") or "").upper()
    method = str(join.args.get("method") or "").upper()
    if side or method or kind == "CROSS":
        return False
    if kind == "INNER":
        return True
    # No ON and no USING is a comma join. A plain JOIN with either is inner.
    return join.args.get("on") is not None or bool(join.args.get("using"))


def _comma(join: exp.Join) -> bool:
    return (
        not join.args.get("side")
        and not join.args.get("kind")
        and not join.args.get("method")
        and join.args.get("on") is None
        and not join.args.get("using")
    )


def _join_sentence(left: str, right: str) -> str:
    return f"Unmatched rows between {left} and {right} are dropped."


def _join_findings(
    tree: exp.Expr, bound: _Bound, order: dict[int, int]
) -> list[tuple[int, Assumption]]:
    found: list[tuple[int, Assumption]] = []
    for select in tree.find_all(exp.Select):
        joins = select.args.get("joins") or ()
        # The arg is ``from_``: ``from`` is a keyword, and this sqlglot stores it under that name.
        from_clause = select.args.get("from_")
        left_node: exp.Expr | None = from_clause.this if from_clause is not None else None
        where = select.args.get("where")
        pairs: list[tuple[str, str]] = []
        if where is not None:
            for predicate in where.find_all(*_COMPARISONS):
                if not _owned_by(predicate, select) or isinstance(predicate, (exp.In, exp.Between)):
                    continue
                left_expr = predicate.this
                right_expr = predicate.expression
                if left_expr is None or right_expr is None:
                    continue
                for left_label in _labels(left_expr, select, bound):
                    for right_label in _labels(right_expr, select, bound):
                        if left_label != right_label:
                            pairs.append((left_label, right_label))
        for join in joins:
            if not isinstance(join, exp.Join):
                continue
            if _explicit_inner(join) and left_node is not None:
                left = _expr_relation(left_node)
                right = _expr_relation(join.this)
                found.append((order.get(id(join), 0), _finding(_join_sentence(left, right))))
            elif _comma(join):
                right = _expr_relation(join.this)
                partners: list[str] = []
                for one, other in pairs:
                    partner = other if one == right else one if other == right else ""
                    if partner and partner not in partners:
                        partners.append(partner)
                if partners:
                    left = partners[0] if len(partners) == 1 else " and ".join(partners)
                    found.append((order.get(id(join), 0), _finding(_join_sentence(left, right))))
            left_node = join.this
    found.sort(key=lambda item: item[0])
    return found


def _suspect_findings(
    tree: exp.Expr, bound: _Bound, keys: set[str], order: dict[int, int]
) -> list[Assumption]:
    if not keys:
        return []
    first: dict[str, int] = {}
    for column in tree.find_all(exp.Column):
        source = _lookup(column, bound)
        if source is None or not isinstance(column.this, exp.Identifier):
            continue
        try:
            key = column_key(source[0], source[1], column.name)
        except ValueError:
            continue
        if key in keys and key not in first:
            first[key] = order.get(id(column), 0)
    ordered = sorted(first, key=lambda key: first[key])
    return [
        _finding(f"The statement reads {key}, which the layer marks as suspect.")
        for key in ordered
    ]
