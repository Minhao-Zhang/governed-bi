"""Run constants for a served turn, built once. See ADR 0005 §2.8.2.2.

Ids are minted here, never accepted from a caller."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_bi.registry.prompts import prompt_set_hash
from governed_bi.registry.prompts import select as selected_variants

from ..corpus.analyst import for_analyst
from ..corpus.hash import corpus_content_hash
from ..corpus.schema import Asset, AssetType, MetricAsset, TableAsset, TermAsset
from ..corpus.store import load as load_corpus
from ..corpus.store import write as write_asset
from ..model.embedder import embedding_settings
from ..model.provider import reasoning_effort_of
from ..ports import Embedder
from ..retrieve.index import UnifiedIndex, build_index
from ..retrieve.structure import (
    CorpusStructure,
    bind_endpoint,
    build_structure,
    table_lookup,
)
from .runtime import model_id
from .state import PER_TURN_RESET

if TYPE_CHECKING:
    # Importing this module must not load ``lancedb`` (~1.1 s).
    from ..retrieve.vector_cache import VectorCache

__all__ = ["Session", "from_corpus_dir", "from_live_schema", "visible_assets"]

#: ``store.write`` needs a namespace these types do not declare; the seeder supplies the left endpoint's.
_NEEDS_NAMESPACE = frozenset({"join", "metric", "term"})


def _digest(*parts: object) -> str:
    """Short stable digest so ids survive a resume."""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Session:
    index: UnifiedIndex
    structure: CorpusStructure
    assets_by_id: Mapping[str, Asset]
    corpus: Any
    connector: Any
    policy: Any
    corpus_content_hash: str
    prompt_set_hash: str
    settings_resolved: Mapping[str, Any]
    db_id: str
    run_id: str
    agent_model: Any | None = None
    #: Guard and channel rewrites. Falls back to :attr:`agent_model`. See ADR 0011.
    utility_model: Any | None = None
    #: Variants this run selected. Must be the mapping :attr:`prompt_set_hash` was computed from.
    prompt_variants: Mapping[str, str] = field(default_factory=dict)
    embedder: Embedder | None = None
    problems: tuple[Any, ...] = ()
    corpus_root: Path | None = None
    _turns: list[str] = field(default_factory=list, repr=False, compare=False)

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        """Run constants. ``thread_id`` stays with the caller: it is per conversation."""
        conf: dict[str, Any] = {
            "policy": self.policy,
            "index": self.index,
            "structure": self.structure,
            "assets_by_id": dict(self.assets_by_id),
            "corpus": self.corpus,
            "connector": self.connector,
        }
        if self.agent_model is not None:
            conf["agent_model"] = self.agent_model
        utility = self.utility_model or self.agent_model
        if utility is not None:
            conf["utility_model"] = utility
        if self.embedder is not None:
            conf["embedder"] = self.embedder
        if self.prompt_variants:
            conf["prompt_variants"] = dict(self.prompt_variants)
        if question and self.embedder is not None:
            conf["query_vector"] = self.embedder.embed([question])[0]
        return {"configurable": conf}

    def turn(
        self,
        question: str,
        *,
        turn_index: int = 1,
        thread_id: str | None = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        """Mint ids and clear :data:`PER_TURN_RESET` so a prior turn's channels do not carry."""
        if not question or not question.strip():
            raise ValueError("a turn needs a question; an empty one has no answer to record")
        # ``thread_id`` is part of ``turn_id``, so two conversations do not collide.
        thread = thread_id or self.run_id
        turn_id = _digest(self.run_id, thread, turn_index, question)
        self._turns.append(turn_id)
        return {
            **PER_TURN_RESET,
            "question": question,
            "turn_index": turn_index,
            "thread_id": thread,
            "run_id": self.run_id,
            "turn_id": turn_id,
            "question_id": _digest(question),
            "attempt_id": _digest(turn_id, 0),
            "db_id": self.db_id,
            "corpus_content_hash": self.corpus_content_hash,
            "prompt_set_hash": self.prompt_set_hash,
            "settings_resolved": dict(self.settings_resolved),
            "n_re_served": 0,
            "evidence": str(evidence or ""),
            "messages": [],
            "usage": [],
        }

    @property
    def fatal_problems(self) -> tuple[Any, ...]:
        """Problems that must stop a serve, decided by ``Problem.fatal`` (ADR 0008)."""
        return tuple(p for p in self.problems if getattr(p, "fatal", True))

    @property
    def degradations(self) -> tuple[Any, ...]:
        return tuple(p for p in self.problems if not getattr(p, "fatal", True))


def _index_entries(assets: Sequence[Asset], structure: CorpusStructure) -> list[Any]:
    """Tag entries from this ``CorpusStructure``, the same resolution as the edges."""
    from ..retrieve.index import IndexEntry

    return [
        IndexEntry(
            id=asset.id,
            summary=asset.summary,
            asset_type=asset.asset_type,
            schema_tag=structure.schema_tags.get(asset.id),
        )
        for asset in assets
    ]


def _is_excluded(asset: Any) -> bool:
    return bool(getattr(getattr(asset, "governance", None), "excluded", False))


#: References ``build_structure`` treats as fatal. An excluded one takes the referrer with it.
#: ``few_shot.sql`` is non-fatal, so that asset degrades. See :func:`visible_assets`.
_REQUIRED_TABLE_REFS: Mapping[AssetType, tuple[str, ...]] = {
    AssetType.column: ("parent_table",),
    AssetType.join: ("left_table", "right_table"),
    AssetType.metric: ("base_table",),
}


def _excluded_closure(assets: Sequence[Asset]) -> frozenset[str]:
    """Marked ids plus assets whose required reference binds into that set.

    :func:`bind_endpoint`, not string equality: a reference has four spellings."""
    # Includes excluded tables, so a bare name cannot rebind to a survivor.
    lookup = table_lookup({a.id: a for a in assets if a.asset_type is AssetType.table})
    out = {asset.id for asset in assets if _is_excluded(asset)}
    # Fixpoint: a required reference may point at a join or a metric, not only a table.
    for _ in range(len(assets) + 1):
        before = len(out)
        for asset in assets:
            if asset.id in out:
                continue
            for field_name in _REQUIRED_TABLE_REFS.get(asset.asset_type, ()):
                bound, _why = bind_endpoint(
                    getattr(asset, field_name, None),
                    lookup,
                    scope=getattr(asset, "schema", None),
                )
                # Leave an unresolved reference: inventing an exclusion would hide a curation defect.
                if bound is not None and bound in out:
                    out.add(asset.id)
                    break
        if len(out) == before:
            break
    return frozenset(out)


def _without_excluded_refs(asset: Asset, excluded: frozenset[str]) -> Asset:
    """Drop optional references into the excluded set. An unbound term is a state, not a defect."""
    # ``isinstance`` so ``replace`` can narrow. ``asset_type`` does not.
    if isinstance(asset, TableAsset):
        columns = tuple(c for c in asset.columns if c not in excluded)
        return replace(asset, columns=columns) if len(columns) != len(asset.columns) else asset
    if isinstance(asset, MetricAsset):
        dims = tuple(d for d in asset.dimensions if d not in excluded)
        return replace(asset, dimensions=dims) if len(dims) != len(asset.dimensions) else asset
    if isinstance(asset, TermAsset):
        if asset.binding is not None and asset.binding.target_id in excluded:
            return replace(asset, binding=None)
    return asset


def visible_assets(assets: Sequence[Asset]) -> list[Asset]:
    """Drop what ``governance.excluded`` reaches. Index, ``assets_by_id``, and structure share this set.

    :func:`for_analyst` still receives the full list so ``check()`` can refuse an excluded column."""
    if not any(_is_excluded(asset) for asset in assets):
        return list(assets)  # the usual path: nothing is copied
    excluded = _excluded_closure(assets)
    return [
        _without_excluded_refs(asset, excluded) for asset in assets if asset.id not in excluded
    ]


def _model_name(model: Any) -> str:
    """One recorded id for both model settings."""
    return (
        model_id(model) or getattr(model, "_llm_type", None) or type(model).__name__
    )


def _resolved_settings(policy: Any) -> dict[str, Any]:
    """Every declared setting. ``UNSET`` is written as ``None``, and no key is omitted."""
    from ..govern.functions import sqlglot_version
    from ..registry.settings import UNSET, effective_settings

    settings = {k: v for k, v in effective_settings().items() if k != "config_hash"}
    for name in ("guard_rules_enabled", "permitted_functions"):
        value = getattr(policy, name, UNSET)
        if value is not UNSET and value is not None:
            settings[name] = sorted(value) if isinstance(value, (set, frozenset)) else value
    settings["sqlglot_version"] = sqlglot_version()
    return settings


def from_assets(
    assets: Sequence[Asset],
    *,
    connector: Any,
    policy: Any,
    db_id: str,
    corpus_content_hash_: str,
    agent_model: Any | None = None,
    utility_model: Any | None = None,
    embedder: Embedder | None = None,
    vector_cache: VectorCache | None = None,
    problems: Sequence[Any] = (),
    run_id: str | None = None,
    corpus_root: Path | None = None,
    prompt_variants: Mapping[str, str] | None = None,
) -> Session:
    """Session over an in-memory asset set. The other constructors call this."""
    visible = visible_assets(assets)
    structure, structure_problems = build_structure(visible)
    entries = _index_entries(visible, structure)
    index = build_index(entries, embedder=embedder, vector_cache=vector_cache)
    settings = _resolved_settings(policy)
    # The map the hash uses. A second resolution would leave one of the two unset.
    settings["prompt_set"] = selected_variants(prompt_variants)
    if embedder is not None:
        # One resolution of the embedder identity, so it cannot drift from `setting_names()`.
        settings.update(embedding_settings(embedder))
    resolved_utility = utility_model or agent_model
    if agent_model is not None:
        # `chat_model` is the declared name. A second spelling would fall outside `setting_names()`.
        settings["chat_model"] = _model_name(agent_model)
        effort = reasoning_effort_of(agent_model)
        settings["llm_reasoning_effort"] = str(effort) if effort else None
        for setting, attr, cast in (
            ("llm_max_retries", "max_retries", int),
            ("llm_timeout_s", "request_timeout", float),
        ):
            value = getattr(agent_model, attr, None)
            if value is not None:
                settings[setting] = cast(value)
    if resolved_utility is not None:
        # Written even when it falls back to the agent model: shared and split are different settings (ADR 0011).
        settings["llm_utility_model"] = _model_name(resolved_utility)
        utility_effort = reasoning_effort_of(resolved_utility)
        settings["llm_utility_reasoning_effort"] = str(utility_effort) if utility_effort else None
        timeout = getattr(resolved_utility, "request_timeout", None)
        if timeout is not None:
            settings["llm_utility_timeout_s"] = float(timeout)
    from ..registry.settings import config_hash

    settings["config_hash"] = config_hash(settings)
    return Session(
        index=index,
        structure=structure,
        assets_by_id={a.id: a for a in visible},
        corpus=for_analyst(list(assets)),
        connector=connector,
        policy=policy,
        corpus_content_hash=corpus_content_hash_,
        prompt_set_hash=prompt_set_hash(prompt_variants),
        settings_resolved=settings,
        db_id=db_id,
        run_id=run_id or uuid.uuid4().hex[:16],
        agent_model=agent_model,
        utility_model=utility_model,
        prompt_variants=dict(prompt_variants or {}),
        embedder=embedder,
        problems=(*problems, *structure_problems),
        corpus_root=corpus_root,
    )


def from_corpus_dir(root: Path | str, *, schemas: Sequence[str] | None = None, **kwargs: Any) -> Session:
    """A corpus on disk. ``schemas`` limits both the load and the content hash."""
    root = Path(root)
    assets, problems = load_corpus(root, schemas=schemas)
    digest = corpus_content_hash(root, schemas=schemas)
    db_id = kwargs.pop("db_id", None) or (schemas[0] if schemas else root.name)
    return from_assets(
        assets, db_id=db_id, corpus_content_hash_=digest, problems=problems, corpus_root=root, **kwargs
    )


def from_live_schema(schema: str, *, connector: Any, corpus_root: Path | str, **kwargs: Any) -> Session:
    """Seed, write, and load through :func:`from_corpus_dir`, so the digest has a tree."""
    from ..corpus.seed import seed

    root = Path(corpus_root)
    assets, problems = seed(connector.introspect(schema), schema)
    for asset in assets:
        namespace = schema if asset.asset_type.value in _NEEDS_NAMESPACE else None
        write_asset(root, asset, namespace=namespace)
    session = from_corpus_dir(root, schemas=[schema], connector=connector, db_id=schema, **kwargs)
    if problems:
        return Session(**{**{f: getattr(session, f) for f in _FIELDS}, "problems": (*problems, *session.problems)})
    return session


#: Public fields, so a rebuilt session does not copy private state.
_FIELDS = tuple(f for f in Session.__dataclass_fields__ if not f.startswith("_"))
