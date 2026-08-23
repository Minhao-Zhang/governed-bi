"""Route / resolve / connect nodes — thin wrappers over ``retrieve.*``.

F2: ``route_node`` runs ADR 0005 §2.5 two-pass retrieval when a ``UnifiedIndex`` is on
``config["configurable"]["index"]``. Without an index, F1-compatible behaviour remains
(schema selection from facet / injector hits; filter-or-empty ``retrieved``).

**All three nodes must declare ``config``** (ADR 0005 §2.8.2): ``wrap.py`` forwards
``RunnableConfig`` only to nodes whose signature asks for it, so a node that drops the
parameter cannot reach the corpus and falls back to ``state`` keys nothing writes — an
empty edge set and an empty reference map, silently.
"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig

from governed_bi.register.assets import AssetType
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.connect import components, connect
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.resolve import resolve
from governed_bi.retrieve.route import route as route_scores
from governed_bi.retrieve.structure import CorpusStructure, complete_joins
from governed_bi.serve.nodes.pass_two import pass_two_retrieve
from governed_bi.serve.runtime import (
    ChannelScale,
    channel_scale,
    corpus_structure,
    facet_hits,
    facet_weights,
    int_knob,
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

# No local defaults for `route_top_n`, `max_steiner_points` or `max_crossings`: `int_knob`
# reads state, then `knobs_resolved`, then the register, which is the one place the value is
# declared. A `state.get(name, <constant>)` here is a knob nothing can set. ADR 0008 D7.


def empty_retrieved(
    schema_ranking: list[tuple[Any, float]] | None = None,
) -> dict[str, Any]:
    """Empty ``RetrievalResult``-shaped dict (ADR 0005 §3.2)."""
    return {
        "by_type": {},
        "selected": {},
        "attributions": {},
        "pulled_in": {},
        "schema_ranking": list(schema_ranking or ()),
        # `None`, not 0.0. An empty retrieval measured no coverage; it did not measure none.
        "lexical_coverage": None,
    }


def route_node(state: dict, config: RunnableConfig) -> dict:
    """Pass-one evidence → top-N schemas → pass-two re-search (or F1 fallback).

    The terminal guard is load-bearing: ``route`` is the fan-in of five facet nodes, so it runs
    whenever *any* of them ran, including when one crashed and ``wrap.py`` marked the turn
    ``crashed``. Without it a facet crash reaches a full billed model call. This node must also
    write no ``path_kind`` key at all — writing ``None`` erased that mark, which is why
    ``settle_path_kind`` treats ``None`` as a no-op.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    hits = _route_hit_triples(state, structure)
    ranking = sorted(
        # `facet_weight_schema` / `facet_weight_other`, comparability knobs, both 1.0 as shipped.
        route_scores(hits, weights=facet_weights(state)),
        key=lambda pair: (-float(pair[1]), str(pair[0])),
    )
    top_n = int_knob(state, "route_top_n")
    eligible = [(schema, score) for schema, score in ranking if float(score) > 0]
    schemas = [schema for schema, _ in eligible[:top_n]]

    # **Eval only** (`eval/replay.py`), and applied *after* the ranking is computed so the
    # ranking still reaches `retrieved` — pass two scores against it, and a pinned run that
    # skipped it would not be the same system. Nothing on a served turn writes this key.
    #
    # Restricted to schemas the structure actually knows: an artifact from another corpus
    # would otherwise pin a name that licenses nothing, and the arm would read as a routing
    # collapse caused by the replay rather than by anything under test.
    pinned = state.get("pinned_schemas")
    if pinned:
        known = set(structure.table_schemas.values())
        chosen = [str(s) for s in pinned if str(s) in known]
        if chosen:
            schemas = chosen

    if not schemas:
        # ``schema_ranking`` is **not** a top-level key: ``ServeState`` declares no such
        # channel, so LangGraph drops it silently. ``stamp`` reads it out of ``retrieved``.
        return {
            "schemas": [],
            "path_kind": "decline",
            "terminal_reason": "no_schema_matched",
            "retrieved": empty_retrieved(ranking),
        }

    cfg = runtime_config(config)
    index = cfg.get("index")
    if index is not None:
        # **State first, config second.** A query vector is per-turn, but `graph_app.make_graph`
        # binds the run constants once at load time with no question, so the config key is
        # absent on the streamed path — the only real one. `accept` writes it to state instead.
        # Config alone here leaves pass two with no semantic channel on every served turn.
        query_vector = state.get("query_vector") or cfg.get("query_vector")
        retrieved = pass_two_retrieve(
            state=state,
            index=index,
            schemas=schemas,
            ranking=ranking,
            query_vector=query_vector,
            # Threaded so pass two can embed each facet's *rewritten* query. Without it the
            # lexical channel searches the rewrite while the semantic channel scores the raw
            # question's vector, and the two are then blended.
            embedder=cfg.get("embedder"),
        )
    else:
        # No index: F1-compatible — filter pass-one hits (empty when only injector).
        retrieved = _retrieved_for_schemas(state, schemas, ranking, structure)

    # No ``path_kind`` key: routing succeeding is not a path kind, and ``None`` erases a crash.
    out: dict[str, Any] = {"schemas": schemas, "retrieved": retrieved}
    # **The seed is the POST-budget table set.** ``by_type`` is assembled out of the hits
    # ``apply_budgets(...)`` kept, so a table the retrieval cap dropped is never licensed and
    # Layer 6 refuses the statement ``r_table_not_licensed`` — a retrieval-budget outcome
    # recorded as a governance verdict. ``resolve`` and ``connect`` only widen this set and
    # neither restores a budget-cut table. ``govern/bounds.py::ToolBounds.licensed`` carries the
    # measurement; ADR 0006 §8 holds the open decision.
    licensed = list((retrieved.get("by_type") or {}).get("table") or ())
    if licensed:
        out["licensed"] = sorted(str(x) for x in licensed)
    return out


def resolve_node(state: dict, config: RunnableConfig) -> dict:
    """Reference closure over hit ids; additions land in ``pulled_in`` / ``licensed``.

    §2.8's closure rows **minus** its last one: join completion needs both endpoints, which a
    disjunctive fixpoint cannot express, so it runs in ``connect`` (§2.8.1). Everything here is
    ``join -> its two tables``, never the reverse.

    **Writes a delta.** ``pulled_in`` is the only key of ``retrieved`` this node decides, and
    :func:`~governed_bi.serve.state.merge_delta` carries the rest. The previous version rebuilt
    the whole result from the six keys it knew about, which silently deleted the two
    ``pass_two`` had added beside them.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved: Mapping[str, Any] = state.get("retrieved") or {}
    hit_ids = _hit_ids(retrieved)
    closure = resolve(hit_ids, references=structure.references)
    added = closure - hit_ids

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in added:
        pulled_in.setdefault(str(asset_id), "resolve")

    asset_types = structure.asset_types
    licensed = set(state.get("licensed") or ())
    licensed.update(_table_ids_from_retrieved(retrieved, asset_types))
    for asset_id in added:
        if _is_table(asset_id, asset_types, retrieved):
            licensed.add(asset_id)

    return {
        "retrieved": {"pulled_in": pulled_in},
        "licensed": sorted(str(x) for x in licensed),
    }


def connect_node(state: dict, config: RunnableConfig) -> dict:
    """Bounded Steiner join over licensed tables; decline when disconnected / over caps.

    **``route_top_n`` is a shortlist, not a conjunction.** Pass two licenses tables from every
    shortlisted schema, so on a pooled lake the terminal set spans schemas that share no join
    edge and a ``missing_join_path`` decline says nothing about the question. Measured
    2026-08-04: three questions that answered at ``route_top_n = 1`` all declined at the
    register default of 3.

    So terminals are partitioned into :func:`~governed_bi.retrieve.connect.components` first —
    by component and not by schema, so two schemas with a declared cross-schema join stay
    together (the case ADR 0005 §2.8.2 charges ``crossings`` for). A losing component's assets
    are dropped from ``retrieved`` as well as from ``licensed``, so the prompt cannot show the
    analyst a table the turn may not query.

    **Join completion** (§2.8.1) runs here rather than in ``resolve``: a Steiner point exists
    to sit on a join path, so the pairs that most need their ``on`` clause in the prompt are
    the ones this node has just created.

    **Writes a delta**, like ``resolve``: only ``pulled_in``, plus the four collections
    :func:`_restrict_to_component` narrows when a component is dropped.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved: Mapping[str, Any] = state.get("retrieved") or {}
    terminals = set(state.get("licensed") or ())
    if not terminals:
        terminals = _table_ids_from_retrieved(retrieved, structure.asset_types)

    edges = structure.join_edges
    max_points = int_knob(state, "max_steiner_points")

    # **Connect each component; license every one that connects.** Do not pick: a pick caps
    # reachability at ``recall@1``, and no ranking rule — routing rank, pass-two score, any
    # other — escapes a ceiling set by the shortlist it ranks. The 1 351-question arm that
    # showed it is retired (register/citations.py); the bound is arithmetic and stands anyway.
    #
    # Licensing all of them is sound: ``licensed`` is govern's table allowlist and ``check()``
    # refuses any table a statement names but ``licensed`` does not. What ``connect``
    # guarantees is a *retrieval* property — that the prompt carries a join path for the
    # tables it offers — and that holds per component. The turn declines only when **no**
    # component connects, which is what ``missing_join_path`` means.
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

    # ``terminals`` guards the decline: zero terminals means retrieval licensed no table, so
    # there is nothing to join and ``connect(set())`` returns "not declined". Without the guard
    # every such turn declines ``over_connect_bounds`` — including the conformance suite's
    # answered path, which licenses no table and is supposed to reach the agent.
    if terminals and not connected:
        reason = _connect_decline_reason(terminals, edges, max_points)
        # No ``retrieved`` key: this node changed nothing about it, and the previous version
        # wrote back a rebuilt copy of what was already in the channel.
        return {
            "path_kind": "decline",
            "terminal_reason": reason,
            "crossings": [],
            "licensed": sorted(str(x) for x in terminals),
        }

    delta: dict[str, Any] = {}
    if unconnectable:
        # Dropped from *both* licensing and context, so the prompt never shows a table the
        # turn could not write a join for.
        dropped = frozenset().union(*unconnectable)
        delta = _restrict_to_component(
            retrieved, frozenset(connected), structure, dropped=dropped
        )
        # The rest of this node reads the narrowed view, not the channel's.
        retrieved = {**retrieved, **delta}

    terminals = set(connected)
    licensed = frozenset(connected | added)

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in added:
        pulled_in[str(asset_id)] = "connect"
    # §2.8's last row, over the final set. Joins are `pulled_in` and never enter
    # `licensed`: that field is govern's table allowlist (bounds.py), and a join id in
    # it would be a table key naming no table.
    for join_asset_id in complete_joins(licensed, structure):
        pulled_in.setdefault(str(join_asset_id), "connect")
    delta["pulled_in"] = pulled_in

    table_schemas = structure.table_schemas
    selected_schemas = set(state.get("schemas") or ())
    crossings = _crossings(added, table_schemas, selected_schemas)

    max_crossings = int_knob(state, "max_crossings")
    if len(crossings) > max_crossings:
        return {
            "path_kind": "decline",
            "terminal_reason": "over_connect_bounds",
            "retrieved": delta,
            "crossings": crossings,
            "licensed": sorted(str(x) for x in terminals),
        }

    return {
        "retrieved": delta,
        "licensed": sorted(str(x) for x in licensed),
        "crossings": crossings,
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _route_hit_triples(
    state: Mapping[str, Any], structure: CorpusStructure
) -> list[tuple[Any, Any, float]]:
    """Pass-one evidence for ``route``. Prefer facet hits; injector is escape hatch."""
    triples = _triples_from_facets(state, structure)
    if triples:
        return triples

    precomputed = state.get("facet_route_hits")
    if precomputed is not None:
        return [
            (facet, schema, float(score))
            for facet, schema, score in precomputed
        ]
    return []


def _triples_from_facets(
    state: Mapping[str, Any], structure: CorpusStructure
) -> list[tuple[Any, Any, float]]:
    schema_tags = structure.schema_tags
    scale = channel_scale(state)
    triples: list[tuple[Any, Any, float]] = []
    for facet_name, facet_result in (state.get("facets") or {}).items():
        hits = facet_hits(facet_result)
        for hit in hits:
            schema = _hit_schema(hit, schema_tags)
            score = _hit_score(hit, scale)
            if schema is None or score is None:
                continue
            triples.append((facet_name, schema, float(score)))
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
    # ``consulted=scores`` here and only here: this is the fallback for a hit payload carrying
    # components but no ``score``, and such a payload does not record which channels ran, so
    # the components present are the whole of what is known. Stated rather than defaulted —
    # for the two real scoring paths the same assumption is what ``fuse``'s signature prevents.
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
        "facet": getattr(hit, "facet", None),
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
    """F1 fallback: RetrievalResult from facet hits in the selected schemas."""
    schema_set = {str(s) for s in schemas}
    schema_tags = structure.schema_tags
    scale = channel_scale(state)
    ranked: list[tuple[str, str, float]] = []
    attributions: dict[str, list[dict[str, Any]]] = {}
    selected: dict[str, dict[str, Any]] = {}
    best_score: dict[str, float] = {}

    for facet_name, facet_result in (state.get("facets") or {}).items():
        for hit in facet_hits(facet_result):
            schema = _hit_schema(hit, schema_tags)
            if schema is None or str(schema) not in schema_set:
                continue
            asset_id = _hit_asset_id(hit)
            asset_type = _hit_asset_type(hit)
            score = _hit_score(hit, scale)
            if asset_id is None or asset_type is None or score is None:
                continue
            payload = _hit_as_dict(hit)
            payload["facet"] = facet_name
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
        prev = by_id.get(asset_id)
        if prev is None or score > prev[2]:
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
        # F1 no-index path: no BM25 to ask, so no coverage to measure. `None`, honouring the
        # register's `Absence.not_measured` — zero would read as "looked and found none".
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
    terminals: set[Any],
    edges: set[tuple[Any, Any]],
    max_points: int,
) -> str:
    """Distinguish disconnected terminals from an over-budget Steiner tree."""
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
    """The four narrowed collections, as a ``retrieved`` delta.

    Drops assets belonging to schemas no kept table belongs to.

    ``dropped`` names the tables whose component could not be connected. Their schemas are
    excluded **only when no kept table shares the schema** — two components inside one schema
    are possible, and dropping the whole schema would delete the half that works.

    Licensing and context must agree: narrowing ``licensed`` alone leaves the losing schema's
    tables rendered in the prompt but unqueryable, so the model is shown a table and then
    refused for using it.

    **Untagged assets are kept.** An unbound term has no schema to be outside of (ADR 0005
    makes untagged a value, not a defect).
    """
    keep_schemas = {structure.table_schemas.get(str(t), "") for t in kept}
    keep_schemas.discard("")
    if dropped:
        # Tables named explicitly, so a schema surviving in another component keeps its assets.
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
