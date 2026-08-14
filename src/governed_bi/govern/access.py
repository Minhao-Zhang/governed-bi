"""The access seam: principals, grants, and the two adapters (ADR 0012).

``ports`` declares the vocabulary — :class:`~governed_bi.ports.Principal`,
:class:`~governed_bi.ports.Grant`, :class:`~governed_bi.ports.AccessPolicy` — because a
Protocol cannot name a type from a later layer. Everything that *decides* anything lives
here, beside the identifier rules it has to agree with:

* :func:`resolve_grant` folds an integrator's keys the way ``check()`` folds a statement's
  references, using the same ``identifiers`` functions. ``Sales.Orders`` and ``sales.orders``
  are one table, and the integrator never learns that they were not.
* :class:`ResolvedGrant` is what the layer stack asks. Three total predicates, each with a
  fail-closed reading, so no layer re-derives an authorization decision.
* :class:`StaticRoleAccessPolicy` owns the composition algebra: **grants union, denials
  union, and a conflicting row predicate raises**. One statement of it, here, rather than one
  per fork.

**What this module does not do.** It does not enforce a row predicate. See
:class:`~governed_bi.ports.PredicateEnforcement`: the two shipped answers are "refuse the
statement" and "the operator says the database does it", and there is no third.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ..ports import (
    OPEN_GRANT,
    AccessPolicy,
    Grant,
    PredicateEnforcement,
    Principal,
    Reach,
    RowPredicate,
)
from .identifiers import normalise_column_key, normalise_table_key

__all__ = [
    "LOCAL_PRINCIPAL",
    "ResolvedGrant",
    "OPEN_RESOLVED",
    "resolve_grant",
    "OpenAccessPolicy",
    "StaticRoleAccessPolicy",
    "SUITE_VERSION",
]

#: The one principal this repository has. ``api/auth.py`` authenticates unconditionally and
#: returns ``identity: "governed-bi-local"`` for every caller; this is that identity as a value,
#: so a fork replacing the auth handler has one name to grep for.
#:
#: It **is** imported by ``api/`` — ``api/auth.py::authenticated_principal`` returns this object
#: and the handler returns ``authenticated_principal().id`` rather than its own copy of the
#: literal. This comment said the opposite for as long as it took ADR 0012 §8.1 to land the
#: wire, which is the same class of defect it was written to warn about: a note describing the
#: tree it was written against, kept after the tree changed.
LOCAL_PRINCIPAL: Principal = Principal(id="governed-bi-local", roles=frozenset({"local"}))

#: The version string the reference adapter's file must declare.
SUITE_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ResolvedGrant:
    """A :class:`~governed_bi.ports.Grant` with every key folded. What the layers ask.

    Three predicates, all total, all fail-closed on a key they have never seen:
    an unknown table is authorized only under :attr:`~governed_bi.ports.Reach.every_table`,
    an unknown column is not denied, and an unknown table carries no predicate. "Not denied"
    is the safe reading for columns because the COLUMNS layer already requires positive
    membership of the corpus's allowed set — denial narrows an allowlist, it does not
    replace one.
    """

    reach: Reach
    tables: frozenset[str]
    denied_columns: frozenset[str]
    #: Tables whose declared predicate this engine cannot apply, so it refuses instead.
    refusing_tables: frozenset[str]
    #: Tables whose predicate the operator asserts the database enforces. Recorded, never
    #: applied, never verified.
    delegated_tables: frozenset[str]
    digest: str

    @property
    def is_open(self) -> bool:
        return (
            self.reach is Reach.every_table
            and not self.denied_columns
            and not self.refusing_tables
            and not self.delegated_tables
        )

    def authorizes_table(self, table_key: str) -> bool:
        """Whether this principal may read ``table_key`` (already a folded table key)."""
        if self.reach is Reach.every_table:
            return True
        return table_key in self.tables

    def denies_column(self, column_key: str) -> bool:
        """Whether this principal is denied ``column_key`` (already a folded column key)."""
        return column_key in self.denied_columns

    def refuses_for_row_predicate(self, table_key: str) -> bool:
        """Whether ``table_key`` carries a predicate this engine will not let past.

        ``delegated_tables`` deliberately answers ``False``: the operator said the database
        applies it. That claim is theirs, and ADR 0012 §5 says so in the same words.
        """
        return table_key in self.refusing_tables


@lru_cache(maxsize=64)
def resolve_grant(grant: Grant, default_schema: str | None) -> ResolvedGrant:
    """Fold a grant's keys into the shape a bound reference resolves to.

    Cached because ``check()`` runs per statement and a grant is closed for the turn;
    :class:`~governed_bi.ports.Grant` is frozen and hashable so that the cache key is the
    grant's content rather than its identity.

    A malformed key **raises** — it is a caller error, and ``check()`` calls this outside its
    own ``except`` for the same reason it normalises ``licensed`` there: a blocked verdict
    would report a broken policy file as an unsafe query.
    """
    tables = frozenset(normalise_table_key(key, default_schema) for key in grant.tables)
    denied = frozenset(
        normalise_column_key(key, default_schema) for key in grant.denied_columns
    )
    refusing: set[str] = set()
    delegated: set[str] = set()
    for predicate in grant.row_predicates:
        key = normalise_table_key(predicate.table, default_schema)
        if predicate.enforcement is PredicateEnforcement.refuse:
            refusing.add(key)
        else:
            delegated.add(key)
    return ResolvedGrant(
        reach=grant.reach,
        tables=tables,
        denied_columns=denied,
        refusing_tables=frozenset(refusing),
        delegated_tables=frozenset(delegated),
        digest=grant.digest(),
    )


#: The resolved form of :data:`~governed_bi.ports.OPEN_GRANT`. Every predicate on it is
#: constant — ``authorizes_table`` is ``True`` for every string, ``denies_column`` and
#: ``refuses_for_row_predicate`` are ``False`` for every string — which is the whole of the
#: claim that the default adapter changes no verdict.
OPEN_RESOLVED: ResolvedGrant = resolve_grant(OPEN_GRANT, None)


class OpenAccessPolicy:
    """Authorize everything. The default, and behaviour-identical to having no seam.

    Not a stub: it is the honest description of a deployment where the connection role is
    the whole authorization story, which is what ``langgraph dev`` on a laptop is. A fork
    replaces it; a reader of the ledger can tell which one ran from
    :attr:`ResolvedGrant.digest`.
    """

    def grant_for(self, principal: Principal) -> Grant:
        return OPEN_GRANT


class StaticRoleAccessPolicy:
    """Roles → grants, from a committed TOML file. The reference adapter.

    Two adapters justify the seam, and this is the second. It is also the smallest thing
    that is not a toy: a file of roles is how an enterprise fork's first week looks, before
    the grants come from whatever the company already has.

    File shape::

        version = "1"

        [role.analyst]
        tables = ["sales.orders", "sales.customers"]
        denied_columns = ["sales.customers.email"]

        [[role.analyst.row_predicate]]
        table = "sales.orders"
        expression = "region_id = current_setting('app.region')::int"
        enforcement = "database_role"

        [role.auditor]
        reach = "every_table"

    **Composition, stated once.** A principal's grant is the union of its roles': tables
    union, denials union, ``every_table`` wins over ``listed``. Grants are additive and
    denials are absolute, so a role that grants a table cannot un-deny a column another role
    denied. Two roles declaring *different* predicates for one table **raises** — picking
    one would be choosing an authorization by sort order.

    **A principal with no known role gets** ``Grant()`` **, which authorizes nothing.** An
    unknown role is not an error and not a wildcard; it is simply no grant.
    """

    def __init__(self, roles: Mapping[str, Grant]) -> None:
        self._roles = dict(roles)

    @property
    def roles(self) -> Mapping[str, Grant]:
        return dict(self._roles)

    @classmethod
    def from_toml(cls, path: str | Path) -> "StaticRoleAccessPolicy":
        """Load and validate. Every shape error is a load failure, never a query-time one."""
        source = Path(path)
        return cls(_parse_roles(tomllib.loads(source.read_text(encoding="utf-8")), source.name))

    def grant_for(self, principal: Principal) -> Grant:
        known = [self._roles[role] for role in sorted(principal.roles) if role in self._roles]
        if not known:
            return Grant()
        reach = (
            Reach.every_table
            if any(g.reach is Reach.every_table for g in known)
            else Reach.listed
        )
        tables: frozenset[str] = frozenset()
        if reach is Reach.listed:
            for grant in known:
                tables |= grant.tables
        denied: frozenset[str] = frozenset()
        for grant in known:
            denied |= grant.denied_columns
        predicates: dict[str, RowPredicate] = {}
        for grant in known:
            for predicate in grant.row_predicates:
                key = predicate.table.strip().lower()
                existing = predicates.get(key)
                if existing is not None and existing != predicate:
                    raise ValueError(
                        f"principal {principal.id!r} holds two roles declaring different row "
                        f"predicates for {predicate.table!r}. Combining them by picking one, or "
                        "by OR-ing two expressions this engine never parses, would be inventing "
                        "an authorization. Declare a table's predicate in at most one role."
                    )
                predicates[key] = predicate
        return Grant(
            reach=reach,
            tables=tables,
            denied_columns=denied,
            row_predicates=tuple(predicates[k] for k in sorted(predicates)),
        )


def _require_keys(raw: Any, *, parts: tuple[int, ...], where: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        raise ValueError(f"{where} must be a list of strings, got {type(raw).__name__}")
    out: list[str] = []
    for key in raw:
        n = len([p for p in key.split(".") if p])
        if n not in parts:
            raise ValueError(
                f"{where}: {key!r} has {n} part(s); this key takes "
                f"{' or '.join(str(p) for p in parts)}. Refused at load rather than at the "
                "first query that touches it — a policy file is read once and enforced "
                "thousands of times."
            )
        out.append(key)
    return frozenset(out)


def _parse_predicates(raw: Any, where: str) -> tuple[RowPredicate, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}.row_predicate must be an array of tables")
    out: list[RowPredicate] = []
    for i, body in enumerate(raw, 1):
        at = f"{where}.row_predicate #{i}"
        if not isinstance(body, Mapping):
            raise ValueError(f"{at} is not a table")
        table, expression = body.get("table"), body.get("expression")
        if not isinstance(table, str) or not isinstance(expression, str):
            raise ValueError(f"{at} needs a string `table` and a string `expression`")
        enforcement = body.get("enforcement", PredicateEnforcement.refuse.value)
        try:
            resolved = PredicateEnforcement(enforcement)
        except ValueError:
            raise ValueError(
                f"{at}: enforcement {enforcement!r} is not one of "
                f"{[e.value for e in PredicateEnforcement]}. There is no `inject` — ADR 0012 "
                "rejects rewriting a checked statement, and a vocabulary that cannot spell "
                "the dangerous option is how it stays rejected."
            ) from None
        out.append(RowPredicate(table=table, expression=expression, enforcement=resolved))
    return tuple(out)


def _parse_roles(data: Mapping[str, Any], filename: str) -> dict[str, Grant]:
    version = data.get("version")
    if version != SUITE_VERSION:
        raise ValueError(
            f"{filename}: version is {version!r}, expected {SUITE_VERSION!r}. An access policy "
            "read under the wrong schema is an authorization nobody has checked."
        )
    roles = data.get("role")
    if not isinstance(roles, Mapping) or not roles:
        raise ValueError(
            f"{filename}: no [role.*] tables. A policy file that grants nothing is a "
            "configuration mistake, not a lockdown; write `[role.x] reach = \"every_table\"` "
            "if that is what you mean."
        )
    out: dict[str, Grant] = {}
    for name, body in roles.items():
        where = f"{filename} [role.{name}]"
        if not isinstance(body, Mapping):
            raise ValueError(f"{where} is not a table")
        reach = Reach(body.get("reach", Reach.listed.value))
        tables = _require_keys(body.get("tables"), parts=(1, 2), where=f"{where}.tables")
        if reach is Reach.every_table and tables:
            raise ValueError(
                f"{where} sets reach = \"every_table\" and also lists tables; one of the two "
                "is a mistake and this file will not guess which."
            )
        out[str(name)] = Grant(
            reach=reach,
            tables=tables,
            denied_columns=_require_keys(
                body.get("denied_columns"), parts=(2, 3), where=f"{where}.denied_columns"
            ),
            row_predicates=_parse_predicates(body.get("row_predicate"), where),
        )
    return out


def _assert_the_default_adapter_is_inert() -> None:
    """Import-time guard: the shipped default authorizes everything and denies nothing.

    Asserted as an effect on the resolved value rather than against ``OPEN_GRANT``'s fields,
    because the failure being caught is a *predicate* that stopped being constant — which is
    the whole of ADR 0012's behaviour-identity claim and would read as an ordinary boolean
    in the dataclass above.
    """
    resolved = OPEN_RESOLVED
    probes = ("sales.orders", "", "a.b.c", "public.anything")
    if not all(resolved.authorizes_table(p) for p in probes):  # pragma: no cover
        raise AssertionError("the open grant refused a table; it is no longer behaviour-identical")
    if any(resolved.denies_column(p) for p in probes):  # pragma: no cover
        raise AssertionError("the open grant denied a column")
    if any(resolved.refuses_for_row_predicate(p) for p in probes):  # pragma: no cover
        raise AssertionError("the open grant carries a row predicate")
    if not isinstance(OpenAccessPolicy(), AccessPolicy):  # pragma: no cover
        raise AssertionError("OpenAccessPolicy no longer satisfies ports.AccessPolicy")
    if not isinstance(StaticRoleAccessPolicy({}), AccessPolicy):  # pragma: no cover
        raise AssertionError("StaticRoleAccessPolicy no longer satisfies ports.AccessPolicy")


_assert_the_default_adapter_is_inert()
