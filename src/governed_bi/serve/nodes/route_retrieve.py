"""Route, resolve, and connect (ADR 0005 §2.5).

All three nodes take ``config``: ``wrap.py`` forwards it only when the signature asks (ADR 0005 §2.8.2)."""

from __future__ import annotations

from typing import AbstractSet, Any, Mapping

from langchain_core.runnables import RunnableConfig

from governed_bi.registry.assets import AssetType
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.connect import components, connect
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.resolve import resolve
from governed_bi.retrieve.route import route as route_scores
from governed_bi.retrieve.structure import CorpusStructure, complete_joins
from governed_bi.serve.nodes.pass_two import pass_two_retrieve
from governed_bi.serve.runtime import (
    ChannelScale,
    channel_hits,
    channel_scale,
    channel_weights,
    corpus_structure,
    int_setting,
)
from governed_bi.serve.runtime import (
    configurable as runtime_config,
)
from governed_bi.serve.runtime import lexical_coverage as _lexical_coverage
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = [
    "empty_retrieved",
    "route_node",
    "resolve_node",
    "connect_node",
]

# No local defaults. A constant here is a setting nothing can set. See ADR 0008.


def empty_retrieved(
    schema_ranking: list[tuple[Any, float]] | None = None,
) -> dict[str, Any]:
    """Empty retrieval dict (ADR 0005 §3.2)."""
    return {
        "by_type": {},
        "selected": {},
        "attributions": {},
        "pulled_in": {},
        "schema_ranking": list(schema_ranking or ()),
        # ``None``, not 0.0: nothing was measured.
        "lexical_coverage": None,
    }


def route_node(state: dict, config: RunnableConfig) -> dict:
    """A miss writes ``decline``. Success omits ``path_kind``, so it cannot clear a prior terminal."""
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    hits = _route_hit_triples(state, structure)
    ranking = sorted(
        route_scores(hits, weights=channel_weights(state)),
        key=lambda pair: (-float(pair[1]), str(pair[0])),
    )
    top_n = int_setting(state, "route_top_n")
    eligible = [(schema, score) for schema, score in ranking if float(score) > 0]
    schemas = [schema for schema, _ in eligible[:top_n]]

    # Eval pin, applied after the ranking so ``retrieved`` still carries it.
    pinned = state.get("pinned_schemas")
    if pinned:
        known = set(structure.table_schemas.values())
        chosen = [str(s) for s in pinned if str(s) in known]
        if chosen:
            schemas = chosen

    if not schemas:
        # ``schema_ranking`` lives on ``retrieved``. There is no such state channel.
        return {
            "schemas": [],
            "path_kind": "decline",
            "terminal_reason": "no_schema_matched",
            "retrieved": empty_retrieved(ranking),
        }

    cfg = runtime_config(config)
    index = cfg.get("index")
    if index is not None:
        # State before config: the streamed graph binds config with no question.
        query_vector = state.get("query_vector") or cfg.get("query_vector")
        retrieved = pass_two_retrieve(
            state=state,
            index=index,
            schemas=schemas,
            ranking=ranking,
            query_vector=query_vector,
            # Pass two embeds each rewritten query, not the raw question's vector.
            embedder=cfg.get("embedder"),
        )
    else:
        retrieved = _retrieved_for_schemas(state, schemas, ranking, structure)

    out: dict[str, Any] = {"schemas": schemas, "retrieved": retrieved}
    # Post-budget tables. A dropped table is refused later as not retrieved (ADR 0006 §8).
    retrieved_tables = list((retrieved.get("by_type") or {}).get("table") or ())
    if retrieved_tables:
        out["retrieved_tables"] = sorted(str(x) for x in retrieved_tables)
    return out


def resolve_node(state: dict, config: RunnableConfig) -> dict:
    """Join completion stays in ``connect``: it needs both endpoints (ADR 0005 §2.8)."""
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved: Mapping[str, Any] = state.get("retrieved") or {}
    hit_ids = _hit_ids(retrieved)
    closure = resolve(hit_ids, references=structure.references)
    added = closure - hit_ids

    # After the full closure. Truncating early would look like a governance refusal.
    max_added = int_setting(state, "max_resolve_additions")
    if len(added) > max_added:
        # Cleared, as ``connect`` clears them: a partial list would read as finished.
        return {
            "path_kind": "decline",
            "terminal_reason": "over_resolve_bounds",
            "crossings": [],
        }

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in added:
        pulled_in.setdefault(str(asset_id), "resolve")

    asset_types = structure.asset_types
    retrieved_tables = set(state.get("retrieved_tables") or ())
    retrieved_tables.update(_table_ids_from_retrieved(retrieved, asset_types))
    for asset_id in added:
        if _is_table(asset_id, asset_types, retrieved):
            retrieved_tables.add(asset_id)

    return {
        "retrieved": {"pulled_in": pulled_in},
        "retrieved_tables": sorted(str(x) for x in retrieved_tables),
    }


def connect_node(state: dict, config: RunnableConfig) -> dict:
    """``route_top_n`` is a shortlist, not a conjunction, so each component is kept or dropped (ADR 0008)."""
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved: Mapping[str, Any] = state.get("retrieved") or {}
    terminals = set(state.get("retrieved_tables") or ())
    if not terminals:
        terminals = _table_ids_from_retrieved(retrieved, structure.asset_types)

    edges = structure.join_edges
    max_points = int_setting(state, "max_steiner_points")

    # Every component that connects. Picking one caps reachability at the shortlist.
    groups = components(terminals, edges=edges)
    connected: set[str] = set()
    added: set[str] = set()
    unconnectable: list[frozenset[str]] = []
    for group in groups:
        result = connect(set(group), edges=edges, max_points=max_points)
        if result.declined:
            unconnectable.append(group)
            continue
        connected.update(str(t) for t in group)
        added.update(str(a) for a in result.added)

    # An empty terminal set is not a decline: the agent must still run.
    if terminals and not connected:
        reason = _connect_decline_reason(terminals, edges, max_points)
        # No ``retrieved`` key: this node changed nothing, so a rebuilt copy is a false write.
        return {
            "path_kind": "decline",
            "terminal_reason": reason,
            "crossings": [],
            "retrieved_tables": sorted(str(x) for x in terminals),
        }

    delta: dict[str, Any] = {}
    if unconnectable:
        # Dropped from context too, so the prompt cannot show an unjoinable table.
        dropped = frozenset().union(*unconnectable)
        delta = _restrict_to_component(
            retrieved, frozenset(connected), structure, dropped=dropped
        )
        retrieved = {**retrieved, **delta}

    terminals = set(connected)
    retrieved_tables = frozenset(connected | added)

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in added:
        pulled_in[str(asset_id)] = "connect"
    # Joins are ``pulled_in`` only. ``retrieved_tables`` is the table allowlist.
    for join_asset_id in complete_joins(retrieved_tables, structure):
        pulled_in.setdefault(str(join_asset_id), "connect")
    delta["pulled_in"] = pulled_in

    table_schemas = structure.table_schemas
    selected_schemas = set(state.get("schemas") or ())
    crossings = _crossings(added, table_schemas, selected_schemas)

    max_crossings = int_setting(state, "max_crossings")
    if len(crossings) > max_crossings:
        return {
            "path_kind": "decline",
            "terminal_reason": "over_connect_bounds",
            "retrieved": delta,
            "crossings": crossings,
            "retrieved_tables": sorted(str(x) for x in terminals),
        }

    return {
        "retrieved": delta,
        "retrieved_tables": sorted(str(x) for x in retrieved_tables),
        "crossings": crossings,
    }


def _route_hit_triples(
    state: Mapping[str, Any], structure: CorpusStructure
) -> list[tuple[Any, Any, float]]:
    triples = _triples_from_channels(state, structure)
    if triples:
        return triples

    precomputed = state.get("channel_route_hits")
    if precomputed is not None:
        return [
            (channel, schema, float(score))
            for channel, schema, score in precomputed
        ]
    return []


def _triples_from_channels(
    state: Mapping[str, Any], structure: CorpusStructure
) -> list[tuple[Any, Any, float]]:
    schema_tags = structure.schema_tags
    scale = channel_scale(state)
    triples: list[tuple[Any, Any, float]] = []
    for channel_name, channel_result in (state.get("channels") or {}).items():
        hits = channel_hits(channel_result)
        for hit in hits:
            schema = _hit_schema(hit, schema_tags)
            score = _hit_score(hit, scale)
            if schema is None or score is None:
                continue
            triples.append((channel_name, schema, float(score)))
    return triples


def _hit_schema(hit: Any, schema_tags: Mapping[str, str]) -> str | None:
    if isinstance(hit, Mapping):
        tag = hit.get("schema_tag")
        asset_id = hit.get("asset_id")
    else:
        tag = getattr(hit, "schema_tag", None)
        asset_id = getattr(hit, "asset_id", None)
    if tag:
        return str(tag)
    if asset_id is not None and asset_id in schema_tags:
        return str(schema_tags[asset_id])
    return None


def _hit_score(hit: Any, scale: ChannelScale) -> float | None:
    if isinstance(hit, Mapping):
        if hit.get("score") is not None:
            return float(hit["score"])
        lexical = hit.get("lexical")
        semantic = hit.get("semantic")
    else:
        if getattr(hit, "score", None) is not None:
            return float(hit.score)
        lexical = getattr(hit, "lexical", None)
        semantic = getattr(hit, "semantic", None)

    scores: dict[str, float] = {}
    if lexical is not None:
        scores["lexical"] = float(lexical)
    if semantic is not None:
        scores["semantic"] = float(semantic)
    if not scores:
        return None
    # ``consulted`` is the components present: the payload does not record which channels ran.
    return float(fuse(scores, scale.weights, consulted=scores.keys()))


def _hit_asset_id(hit: Any) -> str | None:
    if isinstance(hit, Mapping):
        aid = hit.get("asset_id")
    else:
        aid = getattr(hit, "asset_id", None)
    return str(aid) if aid is not None else None


def _hit_asset_type(hit: Any) -> str | None:
    if isinstance(hit, Mapping):
        at = hit.get("asset_type")
    else:
        at = getattr(hit, "asset_type", None)
    return str(at) if at is not None else None


def _hit_as_dict(hit: Any) -> dict[str, Any]:
    if isinstance(hit, Mapping):
        return dict(hit)
    return {
        "channel": getattr(hit, "channel", None),
        "asset_id": getattr(hit, "asset_id", None),
        "asset_type": getattr(hit, "asset_type", None),
        "lexical": getattr(hit, "lexical", None),
        "semantic": getattr(hit, "semantic", None),
        "queries": list(getattr(hit, "queries", None) or ()),
        "score": getattr(hit, "score", None),
        "schema_tag": getattr(hit, "schema_tag", None),
    }


def _retrieved_for_schemas(
    state: Mapping[str, Any],
    schemas: list[Any],
    ranking: list[tuple[Any, float]],
    structure: CorpusStructure,
) -> dict[str, Any]:
    schema_set = {str(s) for s in schemas}
    schema_tags = structure.schema_tags
    scale = channel_scale(state)
    ranked: list[tuple[str, str, float]] = []
    attributions: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, dict[str, Any]] = {}
    best_score: dict[str, float] = {}

    for channel_name, channel_result in (state.get("channels") or {}).items():
        for hit in channel_hits(channel_result):
            schema = _hit_schema(hit, schema_tags)
            if schema is None or str(schema) not in schema_set:
                continue
            asset_id = _hit_asset_id(hit)
            asset_type = _hit_asset_type(hit)
            score = _hit_score(hit, scale)
            if asset_id is None or asset_type is None or score is None:
                continue
            payload = _hit_as_dict(hit)
            payload["channel"] = channel_name
            payload["score"] = score
            attributions.setdefault(asset_id, []).append(payload)
            prev = best_score.get(asset_id)
            if prev is None or score > prev:
                best_score[asset_id] = score
                selected[asset_id] = payload
            ranked.append((asset_id, asset_type, score))

    if not ranked:
        return empty_retrieved(ranking)

    by_id: dict[str, tuple[str, AssetType, float]] = {}
    for asset_id, asset_type, score in ranked:
        try:
            at = asset_type if isinstance(asset_type, AssetType) else AssetType(str(asset_type))
        except ValueError:
            continue
        kept = by_id.get(asset_id)
        if kept is None or score > kept[2]:
            by_id[asset_id] = (asset_id, at, score)

    budgeted = apply_budgets(list(by_id.values()), pulled_in=[])
    by_type: dict[str, list[str]] = {}
    kept_ids: set[str] = set()
    for asset_id, asset_type, _score in budgeted.hits:
        by_type.setdefault(
            str(asset_type.value if isinstance(asset_type, AssetType) else asset_type),
            [],
        ).append(asset_id)
        kept_ids.add(asset_id)

    return {
        "by_type": by_type,
        "selected": {k: v for k, v in selected.items() if k in kept_ids},
        "attributions": {k: v for k, v in attributions.items() if k in kept_ids},
        "pulled_in": {},
        "schema_ranking": list(ranking),
        # No index, so coverage is unmeasured. ``None`` is not zero.
        "lexical_coverage": _lexical_coverage(state, None),
    }


def _hit_ids(retrieved: Mapping[str, Any]) -> set[Any]:
    ids: set[Any] = set(retrieved.get("selected") or {})
    ids.update(retrieved.get("attributions") or {})
    for group in (retrieved.get("by_type") or {}).values():
        ids.update(group)
    return ids


def _table_ids_from_retrieved(
    retrieved: Mapping[str, Any],
    asset_types: Mapping[str, str],
) -> set[Any]:
    tables: set[Any] = set((retrieved.get("by_type") or {}).get("table") or ())
    for asset_id, hit in (retrieved.get("selected") or {}).items():
        if _hit_asset_type(hit) == "table" or asset_types.get(asset_id) == "table":
            tables.add(asset_id)
    return tables


def _is_table(
    asset_id: Any,
    asset_types: Mapping[str, str],
    retrieved: Mapping[str, Any],
) -> bool:
    if asset_types.get(str(asset_id)) == "table":
        return True
    if asset_id in ((retrieved.get("by_type") or {}).get("table") or ()):
        return True
    hit = (retrieved.get("selected") or {}).get(asset_id)
    return _hit_asset_type(hit) == "table" if hit is not None else False


def _connect_decline_reason(
    terminals: AbstractSet[Any],
    edges: AbstractSet[tuple[Any, Any]],
    max_points: int,
) -> str:
    """``missing_join_path`` when an unbounded tree still declines."""
    probe = connect(terminals, edges=edges, max_points=10**9)
    if probe.declined:
        return "missing_join_path"
    _ = max_points
    return "over_connect_bounds"


def _crossings(
    added: Any,
    table_schemas: Mapping[str, str],
    selected_schemas: set[str],
) -> list[dict[str, str]]:
    crossings: list[dict[str, str]] = []
    if not selected_schemas:
        return crossings
    primary = sorted(selected_schemas)[0]
    for table_id in sorted(added, key=str):
        into = table_schemas.get(str(table_id))
        if into is None or into in selected_schemas:
            continue
        crossings.append(
            {
                "from_schema": primary,
                "into_schema": str(into),
                "table_id": str(table_id),
                "reason": "steiner_point",
            }
        )
    return crossings


def _restrict_to_component(
    retrieved: Mapping[str, Any],
    kept: frozenset[str],
    structure: CorpusStructure,
    *,
    dropped: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Keep a schema while any kept table shares it. Untagged assets stay (ADR 0005)."""
    keep_schemas = {structure.table_schemas.get(str(t), "") for t in kept}
    keep_schemas.discard("")
    if dropped:
        # Named tables, so a schema that survives in another component keeps its assets.
        gone = {str(t) for t in dropped} - {str(t) for t in kept}
    else:
        gone = set()
    tags = structure.schema_tags

    def inside(asset_id: str) -> bool:
        if str(asset_id) in gone:
            return False
        tag = tags.get(str(asset_id))
        return tag is None or str(tag) in keep_schemas

    out: dict[str, Any] = {}
    out["selected"] = {k: v for k, v in (retrieved.get("selected") or {}).items() if inside(k)}
    out["attributions"] = {
        k: v for k, v in (retrieved.get("attributions") or {}).items() if inside(k)
    }
    out["pulled_in"] = {k: v for k, v in (retrieved.get("pulled_in") or {}).items() if inside(k)}
    out["by_type"] = {
        kind: [a for a in (ids or ()) if inside(a)]
        for kind, ids in (retrieved.get("by_type") or {}).items()
    }
    return out
