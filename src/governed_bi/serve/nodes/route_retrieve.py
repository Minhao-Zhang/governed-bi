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
from governed_bi.retrieve.connect import components, connect
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.resolve import resolve
from governed_bi.retrieve.route import route as route_scores
from governed_bi.retrieve.structure import CorpusStructure, complete_joins
from governed_bi.serve.nodes.pass_two import pass_two_retrieve
from governed_bi.serve.runtime import (
    FUSE_WEIGHTS,
    corpus_structure,
    facet_hits,
    facet_weights,
    int_knob,
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

# No local defaults for `route_top_n`, `max_steiner_points` or `max_crossings`. All three
# used to be `state.get(name, <constant here>)`, and no production entry point writes those
# state keys -- so they were comparability knobs nothing could set, and the record agreed
# with routing only because the constants happened to equal the register's defaults.
# `int_knob` reads state, then `knobs_resolved`, then the register, which is the one place
# the value is declared. ADR 0008 D7.


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


def _lexical_coverage(state: Mapping[str, Any], index: Any) -> float | None:
    """Share of the question's terms the corpus vocabulary has, or ``None``.

    **The field shipped hard-coded to ``0.0`` on every production turn** (audit §10), which is
    the maximum-weakness reading of a signal whose job is to flag exactly that — and the
    register declares it ``Absence.not_measured``, so zero was not even the honest placeholder.
    ``BM25.coverage`` is the measurement; this decides *which text* is measured and honours the
    ``lexical_coverage`` test hook when a caller set one.

    The **raw question**, not a facet rewrite. The point of the field is whether the user's own
    words are in the corpus vocabulary; a rewrite is the utility model restating them *into*
    that vocabulary, so measuring it would report the rewriter's success as the corpus's.
    """
    hooked = state.get("lexical_coverage")
    if isinstance(hooked, (int, float)) and not isinstance(hooked, bool):
        return float(hooked)
    lexical = getattr(index, "lexical", None)
    coverage = getattr(lexical, "coverage", None)
    if coverage is None:
        return None
    try:
        return coverage(str(state.get("question") or ""))
    except Exception:  # noqa: BLE001 — a degraded signal must not fail the turn
        return None


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
        # `facet_weight_schema` / `facet_weight_other`, both 1.0 as shipped. They were
        # declared comparability knobs with no reader until `route` took a `weights` argument.
        route_scores(hits, weights=facet_weights(state)),
        key=lambda pair: (-float(pair[1]), str(pair[0])),
    )
    top_n = int_knob(state, "route_top_n")
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
        # **State first, config second — and this line was the half that was never fixed.**
        # A query vector is per-turn, so `graph_app.make_graph` binds the run constants once
        # at load time with no question and the config key is simply absent on the streamed
        # path, which is the only real one. `accept` writes it to *state* instead, and
        # `facets._query_vector` was taught to read state first for exactly that reason
        # (see its docstring). Reading config alone here meant pass two — the pass whose
        # output becomes the analyst's context — had **no semantic channel at all** on every
        # served turn, while `eval/datalake.py` supplied the config key and therefore
        # measured a configuration the server does not run.
        query_vector = state.get("query_vector") or cfg.get("query_vector")
        retrieved = pass_two_retrieve(
            state=state,
            index=index,
            schemas=schemas,
            ranking=ranking,
            query_vector=query_vector,
            # Threaded so pass two can embed each facet's *rewritten* query. Without it the
            # lexical channel searched the rewrite and the semantic channel scored the raw
            # question's vector, and the two were then blended — in the pass whose output
            # becomes the analyst's context.
            embedder=cfg.get("embedder"),
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

    **``route_top_n`` is a shortlist, not a conjunction.** Routing selects the top N
    schemas of 57 and pass two licenses tables from every one of them, so on a pooled lake
    the terminal set spans schemas that share no join edge and ``connect`` declined
    ``missing_join_path`` — a decline that says nothing about the question, because the
    terminals were disconnected *by construction*. Measured 2026-08-04: three questions
    that answered at ``route_top_n = 1`` all declined at the register default of 3.

    So the terminals are partitioned into :func:`~governed_bi.retrieve.connect.components`
    first and **one component is kept**. Partitioning by component rather than by schema is
    deliberate: two schemas with a declared cross-schema join are one component and stay
    together, which is the case ADR 0005 §2.8.2 charges ``crossings`` for, while two
    unrelated schemas are two components and the loser is dropped. A decline then means
    what it says — the tables the turn kept cannot be joined.

    The drop is **not silent** and it is not only a licensing change: the losing component's
    assets are removed from ``retrieved`` as well, so the prompt cannot show the analyst a
    table the turn may not query. ``schemas`` keeps its declared meaning (route's selected
    top-N) and ``schema_ranking`` still holds every candidate, so what was shortlisted, what
    survived and what the turn could reach are three readable facts rather than one.

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
    max_points = int_knob(state, "max_steiner_points")

    # **Connect each component; license every one that connects.** Do not pick.
    #
    # Picking one was measured and it caps reachability at ``recall@1``. Over 1 351 BIRD
    # test questions the router shortlisted the gold schema 823 times (``recall@3`` 0.609)
    # and a single-component pick reached it only 0.442 of the time — every one of the 226
    # losses ranked 2nd or 3rd. Ranking by pass-two score instead of by routing rank was
    # worse (0.417), which is the useful part of the result: no *pick* rule can beat
    # ``recall@1``, because picking is the thing that throws the other candidates away.
    #
    # Licensing all of them is sound rather than lax. ``licensed`` is govern's table
    # allowlist, and a statement can only reach a table it names; ``check()`` refuses any
    # it does not. What ``connect`` guarantees is a *retrieval* property — that the prompt
    # carries a join path for the tables it offers — and that holds per component. So each
    # component is connected on its own, its Steiner points are added, and
    # ``complete_joins`` supplies every ON clause. The turn declines only when **no**
    # component connects, which is now what ``missing_join_path`` means.
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

    # ``terminals`` guards the decline, and its absence is not a connect failure. Zero
    # terminals means retrieval licensed no table at all — there is nothing to join, and
    # ``connect(set())`` has always returned "not declined" for exactly that reason. Without
    # the guard this declined every such turn as ``over_connect_bounds``, which is both the
    # wrong reason and the wrong outcome: the conformance suite's answered path licenses no
    # table and is supposed to reach the agent.
    if terminals and not connected:
        reason = _connect_decline_reason(terminals, edges, max_points)
        return {
            "path_kind": "decline",
            "terminal_reason": reason,
            "retrieved": retrieved,
            "crossings": [],
            "licensed": sorted(str(x) for x in terminals),
        }

    if unconnectable:
        # A component that cannot be joined internally is dropped from *both* licensing and
        # context, so the prompt never shows a table the turn could not write a join for.
        dropped = frozenset().union(*unconnectable)
        retrieved = _restrict_to_component(
            retrieved, frozenset(connected), structure, dropped=dropped
        )

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
    retrieved["pulled_in"] = pulled_in

    table_schemas = structure.table_schemas
    selected_schemas = set(state.get("schemas") or ())
    crossings = _crossings(added, table_schemas, selected_schemas)

    max_crossings = int_knob(state, "max_crossings")
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
    # **``consulted=scores`` here, and only here, because there is nothing better to pass.**
    # This branch is the fallback for a hit payload carrying components but no ``score`` —
    # every payload the fan-out and pass two write has one, and it is preferred above. A bare
    # component payload does not record which channels ran for the query that produced it, so
    # the components present are the whole of what is known. Stated explicitly rather than
    # defaulted, because for the two real scoring paths the same assumption is the defect
    # ``fuse``'s signature exists to prevent.
    return float(fuse(scores, FUSE_WEIGHTS, consulted=scores.keys()))


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
        # The F1 no-index path. There is no BM25 to ask, so there is no coverage to measure —
        # `None`, honouring the register's `Absence.not_measured`. This is the branch that made
        # the old hard-coded 0.0 look defensible: without an index the number was never
        # obtainable, and writing zero rather than nothing is how "we did not look" became "we
        # looked and found none" on every turn, indexed or not.
        "lexical_coverage": _lexical_coverage(state, None),
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
        # Copied through, `None` included: a copy that defaulted absence to 0.0 would
        # manufacture the measurement the original declined to make.
        "lexical_coverage": raw.get("lexical_coverage"),
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
    retrieved: dict[str, Any],
    kept: frozenset[str],
    structure: CorpusStructure,
    *,
    dropped: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Drop assets belonging to schemas no kept table belongs to.

    ``dropped`` names the tables whose component could not be connected. Their schemas are
    excluded **only when no kept table shares the schema** -- two components inside one
    schema are possible, and dropping the whole schema for one of them would delete the
    half that works.

    Licensing and context must agree. Narrowing ``licensed`` alone would leave the losing
    schema's tables and columns rendered in the prompt while being unqueryable, so the
    model would be shown a table and then refused for using it — which reads to the
    analyst as a governance fault rather than as a routing decision.

    **Untagged assets are kept.** An unbound term has no schema to be outside of (ADR 0005
    makes untagged a value, not a defect), and dropping it here would delete a pass-one hit
    with no record — the failure ``retrieve/structure.py`` was written about.
    """
    keep_schemas = {structure.table_schemas.get(str(t), "") for t in kept}
    keep_schemas.discard("")
    if dropped:
        # Tables named explicitly, so a schema that survives in another component keeps
        # its assets. Only the unreachable *tables* go.
        gone = {str(t) for t in dropped} - {str(t) for t in kept}
    else:
        gone = set()
    tags = structure.schema_tags

    def inside(asset_id: str) -> bool:
        if str(asset_id) in gone:
            return False
        tag = tags.get(str(asset_id))
        return tag is None or str(tag) in keep_schemas

    out = dict(retrieved)
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
