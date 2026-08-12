"""Tool bounds and the licensed set (ADR 0006 §8; ADR 0012 §6).

A tool that grants privilege must have a bound the model cannot widen.
:class:`ToolBounds` is frozen with no widening method; ``licensed`` is closed at
``connect`` (ADR 0005). Out-of-scope and non-existent share
:data:`OUT_OF_SCOPE_MESSAGE`. :func:`resume_authorised` is the auth primitive
(a ``thread_id`` is not a capability).

ADR 0012 adds ``grant`` and ``withheld``. The layer stack is the bound on ``run_query``; the
other three tools have no statement, so authorization has to be asked here or
``inspect_schema`` and ``sample_rows`` would read a table the principal is denied. Default
:data:`~governed_bi.govern.access.OPEN_RESOLVED` and ``withheld=None``, so nothing changes
until a caller constructs bounds with a real grant — and a caller that constructs one
**must** supply the disclosure set with it, which :meth:`ToolBounds.__post_init__` enforces.

The division of labour between the two is the point. ``grant`` answers at *table* granularity
about a *folded key*; ``withheld`` answers at *asset* granularity about an *asset id*, and only
a caller holding the corpus can compute it. A tool that names an asset asks
:meth:`ToolBounds.discloses`; the grant predicates remain as the fail-closed backstop for the
table the tool was called on.
"""


from __future__ import annotations

import hmac
from dataclasses import dataclass, field

from .access import OPEN_RESOLVED, ResolvedGrant
from .identifiers import normalise_column_key, normalise_table_key

__all__ = ["OUT_OF_SCOPE_MESSAGE", "ToolBounds", "resume_authorised"]

#: The single reply for "you may not" **and** for "there is no such thing".
OUT_OF_SCOPE_MESSAGE = (
    "That identifier is not available in this conversation. Work from the assets in "
    "the context you were given."
)


def _folded_table(key: str) -> str:
    """``key`` in the shape ``resolve_grant`` folded the grant's own keys into.

    The grant is folded at resolution and the tool key is not, so ``Sales.Customers`` and
    ``sales.customers`` compared unequal here while ``check()`` — which folds both sides — called
    them one table. That cost a false refusal on :meth:`ToolBounds.may_inspect_schema` and a
    **fail-open** on :meth:`ToolBounds.may_sample`, which is the half nobody had written down.

    An unfoldable key falls back to itself rather than raising: this is a bound, not a parser,
    and a key with the wrong number of parts simply matches nothing in a listed grant.
    """
    try:
        return normalise_table_key(key, None)
    except ValueError:
        return key


def _folded_column(key: str) -> str:
    """:func:`_folded_table` for a column key. Same fallback, same reason."""
    try:
        return normalise_column_key(key, None)
    except ValueError:
        return key


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """What this turn's tools may touch. Closed at ``connect``, never widened."""

    #: **Asset ids** this turn licensed. Not ``{schema}.{physical_name}``: ``connect`` writes
    #: ``ServeState.licensed`` from the retrieved assets, and ``serve/context.py::_tool_key``
    #: documents the divergence for the corpora where ``slug()`` fired. The two agree on 655 of
    #: the 656 gold tables, which is why this said "table keys" for as long as it did.
    licensed: frozenset[str] = frozenset()
    #: Asset ids in this turn's ``hits ∪ pulled_in``, already narrowed by :attr:`withheld`.
    #: ``read_body``'s bound.
    readable_assets: frozenset[str] = frozenset()
    #: This principal's authorization, already folded (ADR 0012 §6). Resolved by whoever
    #: builds the bounds, because that caller knows the datasource's ``default_schema`` and
    #: this value type must not learn identifier rules a second time.
    grant: ResolvedGrant = field(default=OPEN_RESOLVED)
    #: **The** answer to "what may this principal see": the asset ids
    #: :func:`~governed_bi.serve.context.withheld_by_grant` computed for this turn, which is the
    #: same set the renderer skipped. ``None`` means "nobody computed one", which is legal only
    #: under an open grant — see :meth:`__post_init__`.
    #:
    #: It is a set of *asset ids* and the grant is a set of *folded table keys*, and that is
    #: exactly why both exist. Only a caller holding the corpus can map one to the other
    #: (``table_qualifier(asset)``), so the grant predicates below are a fail-closed backstop
    #: and this set is the authoritative answer.
    withheld: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """A restrictive grant with no disclosure set is a **wiring failure**, not a default.

        ADR 0008 D7: an optional control argument is a control that will be un-wired. It was:
        ``inspect_schema`` asked :meth:`may_inspect_schema`, which is a *table*-level test, and
        then enumerated every column of the table — so a grant denying ``sales.customers.email``
        refused the column in the prompt and handed the model its id, physical name, type and
        nullability through the tool. The renderer was gated and the tool was not, which is the
        disagreement ADR 0012 §8.4 says the one-function design prevents.

        An **empty** ``withheld`` is a legitimate answer (a grant may deny a column no corpus
        declares), so emptiness cannot be the signal. ``None`` is, and it raises.
        """
        if not self.grant.is_open and self.withheld is None:
            raise ValueError(
                "ToolBounds was given a restrictive grant and no `withheld` set. The grant "
                "predicates on this value type answer at table granularity; the asset ids a "
                "principal may not see are computed from the corpus by "
                "serve.context.withheld_by_grant, and without them `inspect_schema` discloses "
                "every denied column of an authorized table. Pass the same set the renderer "
                "was narrowed with (frozenset() if it is genuinely empty)."
            )

    def discloses(self, asset_id: str) -> bool:
        """Whether this principal may be shown ``asset_id`` **at all**.

        One membership test against one set, and the same set the context block was rendered
        from. Every tool that names an asset asks this; nothing re-derives it, because two
        derivations of "what may this principal see" is how one comes to disclose what the other
        refuses.
        """
        return asset_id not in (self.withheld or frozenset())

    def may_read_body(self, asset_id: str) -> bool:
        """``read_body`` is corpus prose, not data, and is bounded by ``readable_assets``.

        Still **no grant test here**, and the reason has changed. ADR 0012 §6 left it ungated
        because gating the tool while the renderer put the asset in the prompt would be a
        bound that only looks enforced. §8.4's wire landed on 2026-08-12, and it closes both
        halves at once and in one place:
        :func:`~governed_bi.serve.delivery.tool_bounds_from_state` subtracts
        :func:`~governed_bi.serve.context.withheld_by_grant` from ``readable_assets`` with the
        same set it narrows the renderer with. A second test here would be a second answer to
        "what may this principal see", keyed on an asset id this value type cannot map to a
        table without learning the corpus.
        """
        return asset_id in self.readable_assets

    def may_inspect_schema(self, table_key: str) -> bool:
        """The **table** bound. It is not the whole of ``inspect_schema``'s bound.

        That tool returns a roster of column metadata, so a table-level answer is the wrong
        granularity for it on its own; ``serve/fetch.py`` asks :meth:`discloses` per column as
        well, with the set this object carries. ADR 0012 §6's table said "licensed **and**
        authorized" and that was the whole of it, which is the hole.
        """
        if table_key not in self.licensed or not self.discloses(table_key):
            return False
        return self.grant.authorizes_table(_folded_table(table_key))

    def may_sample(self, column_id: str) -> bool:
        """``sample_rows`` takes a **column id**, not a name (§7).

        A model-supplied column name would be interpolated into ``SELECT {column} FROM
        {table}`` — identifiers cannot be bound as parameters — giving an injection
        surface with no parse, function or column layer and no ledger entry. The bound
        is the column's *table*: a column id whose table this turn does not license is
        out of scope.

        The grant is asked here as well as inside ``check()`` because this tool returns
        **real values**: a denied column reaching the layer stack would be refused, but a
        denied column whose table is merely unauthorized would produce a statement at all
        only if this bound let it. Same answer twice, one turn earlier, no ledger row spent —
        and the keys are folded on both sides, or it is not the same answer. Unfolded, this
        line returned ``True`` for ``Sales.Customers.Email`` under a grant denying exactly that
        column: ``check()`` then refused it, so nothing leaked, but the ledger row was spent
        and the sentence above was false on any corpus that is not all lower case.
        """
        table = column_id.rsplit(".", 1)[0]
        if not table or table == column_id or table not in self.licensed:
            return False
        if not self.discloses(column_id) or not self.discloses(table):
            return False
        return self.grant.authorizes_table(_folded_table(table)) and not self.grant.denies_column(
            _folded_column(column_id)
        )


def resume_authorised(*, stored_identity: str | None, caller_identity: str | None) -> bool:
    """Constant-time identity check (``hmac.compare_digest``)."""
    if not stored_identity or not caller_identity:
        return False
    return hmac.compare_digest(stored_identity, caller_identity)
