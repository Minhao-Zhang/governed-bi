"""Shared serve runtime knobs (config + candidate depth + fuse weights).

One home so facet / pass-two / route / assemble do not each redefine the same
helpers (ADR 0005 §6 one-implementation gate).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from governed_bi.register.knobs import Unset, knob_default
from governed_bi.retrieve.fuse import fuse
from governed_bi.retrieve.structure import CorpusStructure, build_structure

__all__ = [
    "DEFAULT_CONTEXT_BUDGET",
    "FUSE_WEIGHTS",
    "assets_by_id",
    "bool_knob",
    "candidate_depth",
    "combine_channels",
    "configurable",
    "corpus_structure",
    "facet_hits",
    "facet_weights",
    "float_knob",
    "int_knob",
    "model_id",
    "trust",
    "trusted",
]

DEFAULT_CONTEXT_BUDGET = 80_000

#: Channel weights for :func:`~governed_bi.retrieve.fuse.fuse`, **read from the register**.
#:
#: This was the literal ``{"lexical": 0.5, "semantic": 0.5}`` while ``w_lexical`` and
#: ``w_semantic`` sat in ``register/knobs.py`` as declared ``Role.comparability`` knobs that no
#: code read. So both entered ``config_hash_keys()`` and ``knobs_resolved`` — a run could
#: publish ``w_lexical: 0.9``, move its config hash, and behave identically. That is the
#: inverse of the defect ``knobs.py`` opens by describing, and it is worse than an undeclared
#: constant: an undeclared constant merely hides: a declared-but-unread one actively lies.
FUSE_WEIGHTS: Mapping[str, float] = {
    "lexical": float(knob_default("w_lexical")),
    "semantic": float(knob_default("w_semantic")),
}

def combine_channels(lexical: float | None, semantic: float | None) -> float | None:
    """The **one** channel combiner, shared by pass one and pass two.

    Inputs must already be on a shared scale — see
    :func:`~governed_bi.retrieve.fuse.scale_within_channel`, which each caller applies over its
    own facet's scored population. This function only weights and renormalises.

    ``None`` where a channel did not score the asset, and ``None`` returned when neither did.
    :func:`~governed_bi.retrieve.fuse.fuse` renormalises by *active* weight, so a single-channel
    asset keeps its own value rather than being halved — a facet declaring one channel must not
    score structurally below one declaring two.

    **There were two combiners and they competed in the same sort.** Pass one used
    ``max(lexical or 0.0, semantic or 0.0)``; pass two used ``fuse(scores, FUSE_WEIGHTS)``. Both
    scores reach ``apply_budgets``' single global ordering, because pass two carries untagged
    pass-one hits forward verbatim — so one asset could hold 0.9 down one path and 0.7 down the
    other, and assets that happened to be untagged were advantaged at the 8-table boundary by
    arithmetic rather than by relevance. Neither number was wrong on its own; having both was.

    Choosing ``fuse`` over ``max`` for the shared rule keeps ``w_lexical`` and ``w_semantic``
    live and tunable, which is the whole reason they are declared. On measurement the two are
    within noise on the corpus this ships with (schema recall@3 0.9649 blended against 0.9620
    for max-of-scaled, 342 questions); ``max`` was marginally more robust on the densified
    corpus, and that corpus is not the one the routing measurement says to use.
    """
    scores: dict[str, float] = {}
    if lexical is not None:
        scores["lexical"] = float(lexical)
    if semantic is not None:
        scores["semantic"] = float(semantic)
    if not scores:
        return None
    return float(fuse(scores, FUSE_WEIGHTS))


#: ``id(asset container) -> (that container, its projection)``. Insertion-ordered and
#: capped, so a driver that builds a fresh corpus per question cannot grow it without
#: bound. Deliberately **not** a weak cache: ``dict`` does not support weak references,
#: which is why the container is held and identity-checked on read.
_STRUCTURE_CACHE: dict[int | None, tuple[Any, "CorpusStructure"]] = {}
_STRUCTURE_CACHE_MAX = 8


#: Run constants no request may name. Empty in-process by default; registered once by
#: ``api/graph_app.make_graph`` at server start. See :func:`trust`.
_TRUSTED: dict[str, Any] = {}


def trust(constants: Mapping[str, Any] | None = None) -> None:
    """Declare the run constants a request must not be able to override.

    **This closes a hole that let a client replace the governance policy.**
    ``make_graph`` binds the session's constants with ``with_config``, and LangGraph merges
    caller config **over** bound defaults — correct for ``thread_id``, which is exactly why
    the binding is used, and catastrophic for the six keys beside it. A request to
    ``/threads/{id}/runs`` carrying ``config.configurable.policy`` replaced the
    ``GovernancePolicy`` for that run; carrying ``assets_by_id`` replaced the corpus every
    tool licenses against. Reproduced: ``policy=CLIENT_POLICY assets={'pwned': 1}``.

    This is the same rule as ADR 0007 §2's "provenance fields are ignored, not merged" and
    ADR 0006's "no tool writes to ``licensed``", one layer out: the run's own claims about
    itself are not negotiable by the party being served. ``accept`` already applies it to the
    fifteen state fields; ``configurable`` is where the other seven live.

    Registered once per process because the session **is** the run constants — a second set
    would mean two requests of one run disagreeing about what they served.

    Call with nothing to clear (tests, and a process that serves more than one session).
    """
    _TRUSTED.clear()
    _TRUSTED.update(constants or {})


def trusted() -> Mapping[str, Any]:
    """What :func:`trust` registered, for a caller that needs to check."""
    return dict(_TRUSTED)


def configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """``config["configurable"]``, with any :func:`trust`-ed constants forced **over** it.

    Every node reads its wiring here and nowhere else, which is what makes one merge enough.
    Two nodes used to subscript ``config["configurable"]`` directly — ``guard`` for the
    policy of all things — and each was a way around this.
    """
    if not config:
        return dict(_TRUSTED)
    raw = config.get("configurable") if isinstance(config, Mapping) else None
    if not isinstance(raw, Mapping):
        return dict(_TRUSTED)
    return {**raw, **_TRUSTED} if _TRUSTED else raw


def model_id(model: Any) -> str | None:
    """The provider's model id, or ``None`` when the object does not carry one.

    One implementation for two readers — ``knobs_resolved["llm_model"]`` and every
    ``usage`` row — because those two are compared. The usage row read ``_llm_type`` first
    and therefore recorded ``"openai-chat"`` for every OpenAI model ever served, while the
    knob beside it held ``gpt-5.6-luna``: one turn reporting two different models, on a
    ``Role.comparability`` field. ``_llm_type`` is a LangChain *class* label, not a model.
    """
    for attr in ("model_name", "model", "deployment_name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def int_knob(state: Mapping[str, Any], name: str) -> int:
    """This turn's value for an integer knob: ``state``, then ``knobs_resolved``, then the
    register — and nowhere else.

    **One reader, because the alternative shipped.** ``route_top_n``,
    ``max_steiner_points`` and ``max_crossings`` each read ``state`` *only*, with a
    module-level constant beside them supplying the default. No production entry point
    writes those state keys — only ``eval/harness.py`` and test fixtures do — so all
    three were ``Role.comparability`` knobs that nothing in production could set. The
    record still published ``route_top_n: 3`` and routing genuinely used 3, but only
    because the local constant happened to equal the register's default. Move either one
    and the record reports a value routing did not use. ADR 0008 D7.

    So the default comes from the knob register and there is no second copy of it. A
    knob that ships ``UNSET`` raises rather than becoming a threshold nobody chose, and
    a knob carrying a non-integer raises rather than being silently replaced by the
    default — substituting a value here is the same comparability lie in a smaller
    costume.
    """
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"knob {name!r} is {raw!r}, which is not an integer. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def float_knob(state: Mapping[str, Any], name: str) -> float:
    """:func:`int_knob` for a knob whose value is a weight rather than a count.

    Same precedence and the same two refusals — ``UNSET`` raises rather than becoming a
    number nobody chose, and an unparseable value raises rather than being silently replaced
    by the default. Separate function rather than a ``cast=`` parameter because ``int(0.9)``
    is ``0``: a weight read through the integer reader would be silently floored, which is
    exactly the class of quiet substitution the register exists to prevent.
    """
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    try:
        return float(raw)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"knob {name!r} is {raw!r}, which is not a number. Falling back to the "
            "register default would make the record report a value this turn did not use."
        ) from err


def bool_knob(state: Mapping[str, Any], name: str) -> bool:
    """:func:`int_knob` for an on/off knob. Same precedence, same two refusals.

    ``bool(raw)`` is not used to coerce: a knob resolved to the string ``"false"`` would
    otherwise read as on, which is the same quiet substitution the register exists to
    prevent, just spelled as a truthiness bug instead of a silent default.
    """
    raw = state.get(name)
    if raw is None:
        knobs = state.get("knobs_resolved") or {}
        if isinstance(knobs, Mapping):
            raw = knobs.get(name)
    if raw is None:
        raw = knob_default(name)
    if isinstance(raw, Unset):
        raise ValueError(
            f"knob {name!r} ships UNSET, so there is no value to run with. A guessed "
            "one here would be a fabricated measurement."
        )
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"knob {name!r} is {raw!r}, which is not a bool. Falling back to the register "
        "default would make the record report a value this turn did not use."
    )


def facet_weights(state: Mapping[str, Any]) -> Mapping[str, float]:
    """Per-facet vote multipliers for :func:`~governed_bi.retrieve.route.route`.

    ``facet_weight_schema`` applies to ``facet_schema`` and ``facet_weight_other`` to every
    other facet, which is the split the two knobs describe. Both ship 1.0, so this is
    behaviour-preserving — the point is that moving either one now moves the result, which was
    not true while ``route`` took no weights at all.
    """
    from governed_bi.register.stages import FACET_STAGES, Stage

    other = float_knob(state, "facet_weight_other")
    return {
        stage.value: (
            float_knob(state, "facet_weight_schema") if stage is Stage.facet_schema else other
        )
        for stage in FACET_STAGES
    }


def candidate_depth(state: Mapping[str, Any]) -> int:
    """Pass-one / pass-two candidate pool size. One knob, read through :func:`int_knob`."""
    return int_knob(state, "candidate_depth")


def facet_hits(facet_result: Any) -> list[Any]:
    """Hits list from a FacetResult dict or object."""
    if facet_result is None:
        return []
    if isinstance(facet_result, Mapping):
        return list(facet_result.get("hits") or ())
    return list(getattr(facet_result, "hits", None) or ())


def corpus_structure(config: Mapping[str, Any] | None) -> CorpusStructure:
    """This turn's corpus structure projection (ADR 0005 §2.8.2).

    ``configurable["structure"]`` is the **declared** wiring: the projection is built
    beside the index, once, from the same asset set, and passed in. That is where its
    ``problems`` have a reader -- an unresolvable join endpoint is a curation defect and
    §2.8.2 says it must surface where the corpus is built, not as a decline three
    layers away.

    When it is absent this **derives it from the assets already on ``configurable``**
    rather than returning an empty projection, and the distinction matters: an empty
    projection is not a degradation, it is the defect §2.8.2 was written about --
    ``connect`` on an empty edge set declines ``missing_join_path`` for every turn
    licensing two tables, and single-table turns answer, so nothing looks broken. The
    derivation is a pure function of the asset set, so two turns given the same assets
    cannot disagree; what the fallback genuinely loses is the ``problems`` list, which
    has no reader at serve time. Nothing in ``src/`` builds the index either, so this
    path is the one the in-repo callers take today.

    The fallback is memoised on the identity of the asset container the caller supplied,
    so ``route``, ``resolve`` and ``connect`` share one object instead of building three.
    That is the *other* half of §2.2's "computed at build, not query time": three
    projections per turn of a pooled corpus is three rounds of few-shot SQL parsing, and
    a driver whose per-turn cost depends on how many nodes read the corpus is a driver
    whose latency numbers mean something different from the ones before it.
    """
    cfg = configurable(config)
    ready = cfg.get("structure")
    if isinstance(ready, CorpusStructure):
        return ready

    source = cfg.get("assets_by_id")
    if source is None:
        source = cfg.get("corpus")
    key = id(source) if source is not None else None
    cached = _STRUCTURE_CACHE.get(key)
    if cached is not None and cached[0] is source:
        return cached[1]

    structure, _problems = build_structure(assets_by_id(cfg).values())
    if key is not None:
        if len(_STRUCTURE_CACHE) >= _STRUCTURE_CACHE_MAX:
            _STRUCTURE_CACHE.pop(next(iter(_STRUCTURE_CACHE)))
        # The source object is held alongside the value, so a recycled ``id()`` cannot
        # return another corpus's projection: the identity check above rejects it.
        _STRUCTURE_CACHE[key] = (source, structure)
    return structure


def assets_by_id(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve ``assets_by_id`` or build it from ``corpus`` (list / dict / AnalystCorpus).

    One implementation, here rather than in ``nodes/assemble.py``, because the
    structure projection needs the same asset set the render does. Two resolvers would
    be two answers to "which assets does this turn have", and they would disagree
    exactly where one of the four accepted shapes was handled in only one of them.
    """
    direct = cfg.get("assets_by_id")
    if isinstance(direct, Mapping) and direct:
        return {str(k): v for k, v in direct.items()}

    corpus = cfg.get("corpus")
    if corpus is None:
        return {}

    by_id = getattr(corpus, "by_id", None)
    if isinstance(by_id, Mapping):
        return {str(k): v for k, v in by_id.items()}

    if isinstance(corpus, Mapping):
        # id → asset
        values = list(corpus.values())
        if values and _looks_like_asset(values[0]):
            return {str(k): v for k, v in corpus.items()}
        # type → sequence of assets
        out: dict[str, Any] = {}
        for value in values:
            _ingest_assets(out, value)
        return out

    if isinstance(corpus, Sequence) and not isinstance(corpus, (str, bytes)):
        out = {}
        _ingest_assets(out, corpus)
        return out

    return {}


def _ingest_assets(out: dict[str, Any], value: Any) -> None:
    if isinstance(value, Mapping) and _looks_like_asset(value):
        aid = value.get("id")
        if aid is not None:
            out[str(aid)] = value
        return
    if hasattr(value, "id") and hasattr(value, "asset_type"):
        out[str(value.id)] = value
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _ingest_assets(out, item)


def _looks_like_asset(obj: Any) -> bool:
    if isinstance(obj, Mapping):
        return "id" in obj and ("asset_type" in obj or "summary" in obj)
    return hasattr(obj, "id") and (
        hasattr(obj, "asset_type") or hasattr(obj, "summary")
    )
