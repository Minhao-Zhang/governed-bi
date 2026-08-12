"""Tool delivery tracker + delivery_hash (ADR 0005 §3.6)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

__all__ = [
    "DeliveryTracker",
    "delivery_hash_for",
    "grant_for_turn",
    "payload_digest",
    "tool_bounds_from_state",
]

from governed_bi.govern.access import OPEN_RESOLVED, ResolvedGrant, resolve_grant
from governed_bi.govern.bounds import ToolBounds


def payload_digest(payload: str) -> str:
    """``sha256(payload)[:16]`` for ``tool_delivered`` values."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def delivery_hash_for(
    context_hash: str | None,
    tool_delivered: Mapping[str, str],
) -> str | None:
    """``sha256(context_hash + sorted tool_delivered)``; None when no context_hash."""
    if not context_hash:
        return None
    items = sorted((str(k), str(v)) for k, v in tool_delivered.items())
    blob = context_hash + json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def grant_for_turn(cfg: Mapping[str, Any]) -> ResolvedGrant:
    """This turn's folded :class:`~governed_bi.govern.access.ResolvedGrant`, off the policy.

    ``default_schema=None`` because that is what the serve path gives ``prepare()`` — see
    ``serve/fetch.py``, which passes no ``default_schema`` at either call site. Folding the
    grant against a different one here would authorize a different set from the one the layer
    stack enforces, which is ADR 0006 B5 in a new place.

    :data:`~governed_bi.govern.access.OPEN_RESOLVED` when no policy is threaded. That is not a
    fail-open: a serve path with no ``GovernancePolicy`` has no ``licensed`` bound either, and
    ``check()`` refuses on the licence long before authorization is asked. It keeps the many
    in-process callers that build a bare config behaving exactly as they did.
    """
    policy = cfg.get("policy")
    grant = getattr(policy, "access_grant", None)
    if grant is None:
        return OPEN_RESOLVED
    return resolve_grant(grant, None)


def tool_bounds_from_state(state: Mapping[str, Any], cfg: Mapping[str, Any]) -> ToolBounds:
    """Frozen bounds from ``licensed`` + ``retrieved`` (hits ∪ pulled_in), narrowed by the grant.

    ``cfg`` is **required and has no default**, on ADR 0008 D7's rule that an optional control
    argument is a control that will be un-wired — the ``spellings`` defect, which shipped
    optional and whose only production caller omitted it until 610 mixed-case columns had
    failed after a passing verdict. A caller with no configuration passes ``{}`` and says so.

    Two things the grant does here, and they are the halves of ADR 0012 §6 and §8.4:

    * ``grant`` reaches :class:`~governed_bi.govern.bounds.ToolBounds`, so ``inspect_schema``
      and ``sample_rows`` are bounded. Those two build no statement, so without this they are
      the way around the layer stack rather than a path through it.
    * ``readable_assets`` is **narrowed**, which is what gates ``read_body``. §6 declined to
      gate it inside ``ToolBounds`` because gating the tool while the renderer still put the
      asset in the prompt would be a bound that only looks enforced; narrowing the bound at
      the one place that also narrows the renderer is the version that is enforced. The set is
      :func:`~governed_bi.serve.context.withheld_by_grant`'s, so the prompt and the tool
      cannot disagree.

    The same set is also handed over **whole**, as ``ToolBounds.withheld``, and not only
    subtracted. Subtraction answers ``read_body``, whose bound is ``hits ∪ pulled_in``. It
    cannot answer ``inspect_schema``, which enumerates a table's columns whether or not
    retrieval found them — so a denied column that was never retrieved is not in
    ``readable_assets`` for the subtraction to reach, and the tool handed the model its id,
    physical name, type and nullability while the rendered block correctly omitted it.

    ``licensed`` is **not** narrowed, deliberately. ADR 0012's rejected alternatives open with
    it: filtering the licence by the grant makes ``r_table_not_authorized`` unreachable, so
    every permission refusal would be reported as a retrieval miss and the histogram would say
    the opposite of what happened.
    """
    licensed = frozenset(str(x) for x in (state.get("licensed") or ()))
    retrieved = state.get("retrieved") or {}
    readable: set[str] = set()
    if isinstance(retrieved, Mapping):
        readable.update(str(k) for k in (retrieved.get("selected") or {}))
        readable.update(str(k) for k in (retrieved.get("pulled_in") or {}))
        for group in (retrieved.get("attributions") or {}).values():
            for hit in group or ():
                if isinstance(hit, Mapping) and hit.get("asset_id") is not None:
                    readable.add(str(hit["asset_id"]))
                else:
                    aid = getattr(hit, "asset_id", None)
                    if aid is not None:
                        readable.add(str(aid))
    grant = grant_for_turn(cfg)
    withheld: frozenset[str] | None = None
    if not grant.is_open:
        from governed_bi.serve.context import withheld_by_grant
        from governed_bi.serve.runtime import assets_by_id

        withheld = withheld_by_grant(assets_by_id(cfg), grant)
        readable -= withheld
    return ToolBounds(
        licensed=licensed,
        readable_assets=frozenset(readable),
        grant=grant,
        withheld=withheld,
    )


class DeliveryTracker:
    """Mutable ``tool_delivered`` map for the duration of ``agent_core``."""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self.tool_delivered: dict[str, str] = dict(initial or {})

    def record(self, call_id: str, payload: str) -> None:
        if call_id:
            self.tool_delivered[str(call_id)] = payload_digest(payload)

    def merge_into(
        self,
        delivery: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """The ``delivery`` **delta** this turn's tools add: ``tool_delivered`` and its hash.

        Two keys, not four, and the two are exactly what ``agent_core`` decides.
        :func:`~governed_bi.serve.state.merge_delta` carries ``context_block``,
        ``context_hash`` and ``evicted`` because they are already in the channel.

        **This function used to rebuild the record.** It returned a fresh four-key dict, so
        ``assemble``'s ``evicted`` — the only record that the char budget dropped a licensed
        table before the model ever saw it — was destroyed here, mid-turn, on every turn that
        had one. It is the reason ``table_coverage`` reads as an EX ceiling and is really a
        licensing figure: a table can be routed, licensed, counted as covered, and then
        evicted for space with nothing anywhere saying so. Measured once it survived: 1.4% of
        turns, bodies only. Rare -- but "rare" was not knowable while this deleted it.

        The first fix carried that one key by name. Writing a delta is the same fix without a
        list to keep: the identical loss was live on ``retrieved`` at the same time, one
        channel over, and a named carry here could never have covered it.

        ``delivery`` is still read, because ``delivery_hash`` digests the *whole* delivery —
        the context hash the block was rendered to, plus every tool payload — so the hash
        cannot be computed from the delta alone.
        """
        base = dict(delivery or {})
        existing = dict(base.get("tool_delivered") or {})
        existing.update(self.tool_delivered)
        return {
            "tool_delivered": existing,
            "delivery_hash": delivery_hash_for(base.get("context_hash"), existing),
        }
