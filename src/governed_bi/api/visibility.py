"""What the browse surface may project (ADR 0012 §8.5).

`serve/` narrows two things by the grant — the rendered context block and the tool bounds — and
until 2026-08-12 `api/` narrowed nothing. Every browse route read `session.assets_by_id`
directly, so a deployment that set `GOVERNED_BI_ACCESS_POLICY` to deny
`sales.customers.email` withheld that column from the model and served it, with its
`sample_values`, from `GET /corpus/rows?type=column` and `GET /schema/{table_id}`. Two answers
to "what may this principal see", which is the thing ADR 0012 §8.4 is a section about.

**One function decides, and it is the same one.**
:func:`~governed_bi.serve.context.withheld_by_grant` computes the asset ids; this module hands
the routes a session whose corpus projections are already missing them. A route that forgets to
ask cannot exist, because there is nothing left to ask: the map it reads is the narrowed one.

**No cache, and that is a measurement rather than a shrug.** ``withheld_by_grant`` returns
``frozenset()`` on its first line under an open grant, which is what this repository ships and
what every artifact in ``runs/`` was measured under, so the default path pays one attribute
read and one boolean. A deployment that configured a policy pays one pass over the corpus per
corpus-reading request; that is a real cost and it belongs to the deployment that asked for it,
not to a cache whose invalidation nobody would own.

**The one thing this does not narrow, and why.** ``/audit/corpus``'s ``problems`` are the
curator's own defect strings, and one of them can name a table this principal may not read.
They are carried verbatim: ``servable`` is ``not fatal_problems``, so filtering them would let
an unservable corpus read as servable — trading a health signal the operator needs for a
narrow disclosure to the one principal this repository has. It is named in ADR 0012 §8.5 and
asserted as an exemption in ``tests/api/test_the_browse_surface_respects_the_grant.py`` rather
than left to be discovered.

That trade held more comfortably when this note claimed the same strings were already on the
server's stdout at startup. They are not: only ``serve/__main__.py``, the one-question CLI,
prints them. Neither ``graph_app.session_from_environment`` nor ``routes.app_from_environment``
prints a problem, so under ``langgraph dev`` this route is the only place they appear. A fork
that authenticates people has to decide the trade again on that footing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

__all__ = ["visible", "withheld_for"]


def withheld_for(session: Any) -> frozenset[str]:
    """The asset ids this session's grant does not disclose. **Empty under the open grant.**

    Read off ``session.policy.access_grant`` — the same value ``serve/delivery.py`` reads and
    the same one ``check()`` enforces — and folded with ``default_schema=None``, which is what
    the serve path gives ``prepare()``. Folding against a different one here would withhold a
    different set from the one the layer stack refuses.
    """
    from governed_bi.govern.access import resolve_grant
    from governed_bi.serve.context import withheld_by_grant

    grant = getattr(getattr(session, "policy", None), "access_grant", None)
    if grant is None:
        return frozenset()
    resolved = resolve_grant(grant, None)
    if resolved.is_open:
        return frozenset()
    return withheld_by_grant(dict(session.assets_by_id), resolved)


def visible(session: Any) -> Any:
    """``session`` with its corpus projections narrowed to what the grant discloses.

    Returns the session **itself** when nothing is withheld, so the open-grant path is
    byte-identical and not merely equal — no proxy, no copy, no new object identity for a test
    to trip over.
    """
    withheld = withheld_for(session)
    if not withheld:
        return session
    return _VisibleSession(session, withheld)


class _VisibleSession:
    """A read-through view of a session with two attributes replaced.

    ``assets_by_id`` and ``structure`` are the only two corpus projections the HTTP surface
    reads, and both are narrowed here rather than at each of the nine call sites. Narrowing
    ``structure`` is not optional: ``/graph``'s edges come from ``structure.join_edges``, so a
    filter on the asset map alone would drop a withheld table's **node** and keep an edge whose
    ``target`` is its id — the table's existence, spelled out in a different field.

    Everything else forwards. In particular ``fatal_problems`` and ``degradations`` do; see the
    module docstring.
    """

    __slots__ = ("_session", "_withheld", "assets_by_id", "structure")

    def __init__(self, session: Any, withheld: frozenset[str]) -> None:
        self._session = session
        self._withheld = withheld
        self.assets_by_id: Mapping[str, Any] = {
            aid: _narrow_asset(asset, withheld)
            for aid, asset in session.assets_by_id.items()
            if aid not in withheld
        }
        self.structure = _narrow_structure(session.structure, withheld)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


#: Fields that hold **other assets' ids**, by the type that declares them. Dropping an asset
#: from the map is not enough while a surviving asset still lists its id: ``browse.row_for``
#: projects every dataclass field, so ``/corpus/rows?type=table`` handed back
#: ``columns: ["sales.customers.email", ...]`` for a table whose ``email`` column the grant
#: denies. The list is closed and short on purpose — a new id-bearing field is a new hole, and
#: ``test_the_browse_surface_respects_the_grant``'s sweep is what finds it.
_ID_TUPLE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "table": ("columns",),
    "metric": ("dimensions",),
}
_ID_SCALAR_FIELDS: Mapping[str, tuple[str, ...]] = {
    "column": ("references",),
}


def _narrow_asset(asset: Any, withheld: frozenset[str]) -> Any:
    """``asset`` with every reference to a withheld asset removed. Usually ``asset`` itself."""
    kind = str(getattr(getattr(asset, "asset_type", None), "value", "") or "")
    changes: dict[str, Any] = {}
    for name in _ID_TUPLE_FIELDS.get(kind, ()):
        current = tuple(getattr(asset, name, ()) or ())
        kept = tuple(x for x in current if str(x) not in withheld)
        if kept != current:
            changes[name] = kept
    for name in _ID_SCALAR_FIELDS.get(kind, ()):
        current = getattr(asset, name, None)
        if current is not None and str(current) in withheld:
            changes[name] = None
    if not changes:
        return asset
    return replace(asset, **changes)


def _narrow_structure(structure: Any, withheld: frozenset[str]) -> Any:
    """Every mapping in ``CorpusStructure``, minus the withheld ids on either end.

    ``dataclasses.replace`` rather than ``build_structure`` over the surviving assets: the
    latter is a pure function and would give the same answer, at the cost of re-running the
    whole linker — and of inventing new ``Problem`` rows for endpoints that resolve to a table
    the *grant* removed, which is not a curation defect and must not be reported as one.
    """

    def keep(asset_id: Any) -> bool:
        return str(asset_id) not in withheld

    return replace(
        structure,
        join_edges=frozenset(
            edge for edge in structure.join_edges if keep(edge[0]) and keep(edge[1])
        ),
        references={
            source: frozenset(t for t in targets if keep(t))
            for source, targets in structure.references.items()
            if keep(source)
        },
        asset_types={k: v for k, v in structure.asset_types.items() if keep(k)},
        table_schemas={k: v for k, v in structure.table_schemas.items() if keep(k)},
        schema_tags={k: v for k, v in structure.schema_tags.items() if keep(k)},
        joins_by_edge={
            edge: tuple(j for j in joins if keep(j))
            for edge, joins in structure.joins_by_edge.items()
            if keep(edge[0]) and keep(edge[1])
        },
    )
