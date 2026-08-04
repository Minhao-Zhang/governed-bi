"""Route / resolve / connect nodes — thin wrappers over ``retrieve.*``.

F2: ``route_node`` runs ADR 0005 §2.5 two-pass retrieval when a
``UnifiedIndex`` is on ``config["configurable"]["index"]``. Without an index,
F1-compatible behaviour remains (schema selection from facet / injector hits;
filter-or-empty ``retrieved``).

**All three nodes declare ``config``, and that is load-bearing** (ADR 0005 §2.8.2).
``wrap.py`` forwards ``RunnableConfig`` only to nodes whose signature asks for it, so
until 2026-08-03 ``resolve_node`` and ``connect_node`` had no way to reach the corpus
and read their inputs off ``state`` instead -- five fields that nothing anywhere ever
wrote. ``connect`` therefore ran on an empty edge set on every turn ever served and
declined ``missing_join_path`` whenever a turn licensed more than one table, while
``resolve`` ran on an empty reference map so **no closure row in §2.8 had ever fired**.
Single-table turns answered, which is what made it invisible.
"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig

from governed_bi.register.assets import AssetType
from governed_bi.retrieve.budget import apply_budgets
from governed_bi.retrieve.connect import connect
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.resolve import resolve
from governed_bi.retrieve.route import route as route_scores
from governed_bi.retrieve.structure import CorpusStructure, complete_joins
from governed_bi.serve.nodes.pass_two import pass_two_retrieve
from governed_bi.serve.runtime import (
    FUSE_WEIGHTS,
    corpus_structure,
    facet_hits,
)
from governed_bi.serve.runtime import (
    configurable as runtime_config,
)
from governed_bi.serve.state import TERMINAL_PATH_KINDS

__all__ = [
    "empty_retrieved",
    "route_node",
    "resolve_node",
    "connect_node",
]

_DEFAULT_TOP_N = 3
_DEFAULT_MAX_STEINER = 5
_DEFAULT_MAX_CROSSINGS = 2


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
        "lexical_coverage": 0.0,
    }


def route_node(state: dict, config: RunnableConfig) -> dict:
    """Pass-one evidence → top-N schemas → pass-two re-search (or F1 fallback).

    **The terminal guard is not boilerplate here; it is the one place it was missing.**
    ``route`` is the fan-in of five facet nodes, so it runs whenever *any* of them ran —
    including when one crashed and ``wrap.py`` marked the turn ``crashed``. Every other node
    downstream guards, and this one did not, so a facet crash proceeded through routing,
    retrieval, assembly and a full billed model call before ``stamp`` recorded the crash that
    had already happened. It also wrote ``"path_kind": None`` unconditionally, which erased
    that mark outright — the reason ``settle_path_kind`` now treats ``None`` as a no-op.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    hits = _route_hit_triples(state, structure)
    ranking = sorted(
        route_scores(hits),
        key=lambda pair: (-float(pair[1]), str(pair[0])),
    )
    top_n = int(state.get("route_top_n", _DEFAULT_TOP_N))
    eligible = [(schema, score) for schema, score in ranking if float(score) > 0]
    schemas = [schema for schema, _ in eligible[:top_n]]

    if not schemas:
        # ``schema_ranking`` is **not** returned as a top-level key. It was, and
        # ``ServeState`` declares no such channel, so LangGraph dropped it — while ``stamp``
        # read the field it publishes out of ``retrieved``, where ``empty_retrieved`` had
        # already put it. One write reached the record and the other went nowhere.
        return {
            "schemas": [],
            "path_kind": "decline",
            "terminal_reason": "no_schema_matched",
            "retrieved": empty_retrieved(ranking),
        }

    cfg = runtime_config(config)
    index = cfg.get("index")
    if index is not None:
        query_vector = cfg.get("query_vector")
        retrieved = pass_two_retrieve(
            state=state,
            index=index,
            schemas=schemas,
            ranking=ranking,
            query_vector=query_vector,
        )
    else:
        # No index: F1-compatible — filter pass-one hits (empty when only injector).
        retrieved = _retrieved_for_schemas(state, schemas, ranking, structure)

    # No ``path_kind`` key at all. Routing succeeding is not a path kind, and the node has
    # nothing to say about one — saying ``None`` was how a crash got erased.
    out: dict[str, Any] = {"schemas": schemas, "retrieved": retrieved}
    licensed = list((retrieved.get("by_type") or {}).get("table") or ())
    if licensed:
        out["licensed"] = sorted(str(x) for x in licensed)
    return out


def resolve_node(state: dict, config: RunnableConfig) -> dict:
    """Reference closure over hit ids; additions land in ``pulled_in`` / ``licensed``.

    The closure rows are §2.8's, **minus** its last one: join completion needs both
    endpoints, which a disjunctive fixpoint cannot express, and it runs after
    ``connect`` (§2.8.1). Everything here is ``join -> its two tables``, never the
    reverse.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved = _copy_retrieved(state.get("retrieved"))
    hit_ids = _hit_ids(retrieved)
    closure = resolve(hit_ids, references=structure.references)
    added = closure - hit_ids

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in added:
        pulled_in.setdefault(str(asset_id), "resolve")
    retrieved["pulled_in"] = pulled_in

    asset_types = structure.asset_types
    licensed = set(state.get("licensed") or ())
    licensed.update(_table_ids_from_retrieved(retrieved, asset_types))
    for asset_id in added:
        if _is_table(asset_id, asset_types, retrieved):
            licensed.add(asset_id)

    return {
        "retrieved": retrieved,
        "licensed": sorted(str(x) for x in licensed),
    }


def connect_node(state: dict, config: RunnableConfig) -> dict:
    """Bounded Steiner join over licensed tables; decline when disconnected / over caps.

    Then **join completion** (§2.8.1): every join whose both endpoints are in the final
    licensed set is pulled in. It runs here rather than in ``resolve`` because a Steiner
    point's whole purpose is to sit on a join path, so the pairs that most need their
    ``on`` clause in the prompt are the ones this node has just created.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    structure = corpus_structure(config)
    retrieved = _copy_retrieved(state.get("retrieved"))
    terminals = set(state.get("licensed") or ())
    if not terminals:
        terminals = _table_ids_from_retrieved(retrieved, structure.asset_types)

    edges = structure.join_edges
    max_points = int(state.get("max_steiner_points", _DEFAULT_MAX_STEINER))
    result = connect(terminals, edges=edges, max_points=max_points)

    if result.declined:
        reason = _connect_decline_reason(terminals, edges, max_points)
        return {
            "path_kind": "decline",
            "terminal_reason": reason,
            "retrieved": retrieved,
            "crossings": [],
            "licensed": sorted(str(x) for x in terminals),
        }

    licensed = frozenset(terminals | set(result.added))

    pulled_in = dict(retrieved.get("pulled_in") or {})
    for asset_id in result.added:
        pulled_in[str(asset_id)] = "connect"
    # §2.8's last row, over the final set. Joins are `pulled_in` and never enter
    # `licensed`: that field is govern's table allowlist (bounds.py), and a join id in
    # it would be a table key naming no table.
    for join_asset_id in complete_joins(licensed, structure):
        pulled_in.setdefault(str(join_asset_id), "connect")
    retrieved["pulled_in"] = pulled_in

    table_schemas = structure.table_schemas
    selected_schemas = set(state.get("schemas") or ())
    crossings = _crossings(result.added, table_schemas, selected_schemas)

    max_crossings = int(state.get("max_crossings", _DEFAULT_MAX_CROSSINGS))
    if len(crossings) > max_crossings:
        return {
            "path_kind": "decline",
            "terminal_reason": "over_connect_bounds",
            "retrieved": retrieved,
            "crossings": crossings,
            "licensed": sorted(str(x) for x in terminals),
        }

    return {
        "retrieved": retrieved,
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
    triples: list[tuple[Any, Any, float]] = []
    for facet_name, facet_result in (state.get("facets") or {}).items():
        hits = facet_hits(facet_result)
        for hit in hits:
            schema = _hit_schema(hit, schema_tags)
            score = _hit_score(hit)
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


def _hit_score(hit: Any) -> float | None:
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
    return float(fuse(scores, FUSE_WEIGHTS))


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
            score = _hit_score(hit)
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
        "lexical_coverage": float(state.get("lexical_coverage") or 0.0),
    }


def _copy_retrieved(raw: Any) -> dict[str, Any]:
    if not raw:
        return empty_retrieved()
    return {
        "by_type": {k: list(v) for k, v in dict(raw.get("by_type") or {}).items()},
        "selected": dict(raw.get("selected") or {}),
        "attributions": {
            k: list(v) for k, v in dict(raw.get("attributions") or {}).items()
        },
        "pulled_in": dict(raw.get("pulled_in") or {}),
        "schema_ranking": list(raw.get("schema_ranking") or ()),
        "lexical_coverage": float(raw.get("lexical_coverage") or 0.0),
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
