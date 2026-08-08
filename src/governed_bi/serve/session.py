"""Run constants for a served turn, built once (ADR 0005 §2.8.2.2).

Run-constant vs per-turn: index, structure, corpus, connector, policy, model, knobs,
and hashes live on :class:`Session`. Entry points: :meth:`Session.configurable` and
:meth:`Session.turn`. Ids are minted here, never accepted from a caller.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from governed_bi.register.prompts import prompt_set_hash

from ..corpus.analyst import for_analyst
from ..corpus.hash import corpus_content_hash
from ..corpus.schema import Asset
from ..corpus.store import load as load_corpus
from ..corpus.store import write as write_asset
from ..model.embedder import embedding_knobs
from ..ports import Embedder
from ..register.knobs import defaults as knob_defaults
from ..retrieve.index import UnifiedIndex, build_index
from ..retrieve.structure import CorpusStructure, build_structure
from .runtime import model_id
from .state import PER_TURN_RESET

if TYPE_CHECKING:
    # Type-only, so importing a session does not pull in ``lancedb`` (~1.1 s) for the
    # callers that build no vectors at all — which is every test that builds a corpus.
    from ..retrieve.vector_cache import VectorCache

__all__ = ["Session", "from_corpus_dir", "from_live_schema"]

#: Asset types whose file needs an explicit namespace on write, because they declare no
#: ``schema`` field of their own. ``store.write`` raises without one; the namespace is a fact
#: held by another asset (a join's is its left endpoint's), so the seeder knows it and the
#: writer cannot derive it.
_NEEDS_NAMESPACE = frozenset({"join", "metric", "term"})


def _digest(*parts: object) -> str:
    """A short stable digest. Used for ids that must be reproducible across a resume."""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Session:
    """Everything constant for one run, plus the two ways to use it."""

    index: UnifiedIndex
    structure: CorpusStructure
    assets_by_id: Mapping[str, Asset]
    corpus: Any
    connector: Any
    policy: Any
    corpus_content_hash: str
    prompt_set_hash: str
    knobs_resolved: Mapping[str, Any]
    db_id: str
    run_id: str
    agent_model: Any | None = None
    #: Model for guard + facet rewriters. Falls back to :attr:`agent_model`.
    utility_model: Any | None = None
    embedder: Embedder | None = None
    problems: tuple[Any, ...] = ()
    corpus_root: Path | None = None
    _turns: list[str] = field(default_factory=list, repr=False, compare=False)

    # ── the two ways in ───────────────────────────────────────────────────────

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        """Run constants as ``{"configurable": {...}}``. Optional ``question`` adds ``query_vector``."""
        conf: dict[str, Any] = {
            # No thread_id — that is per conversation, supplied by the caller.
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
        if question and self.embedder is not None:
            conf["query_vector"] = self.embedder.embed([question])[0]
        return {"configurable": conf}

    def turn(
        self,
        question: str,
        *,
        turn_index: int = 1,
        thread_id: str | None = None,
        identity: Any = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        """Turn dict with required record fields. Mints ids; clears :data:`PER_TURN_RESET` channels."""
        if not question or not question.strip():
            raise ValueError("a turn needs a question; an empty one has no answer to record")
        # Thread is part of turn identity so two conversations don't collide on turn_id.
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
            "knobs_resolved": dict(self.knobs_resolved),
            "n_re_served": 0,
            "evidence": str(evidence or ""),
            "messages": [],
            "usage": [],
            # Absent identity fails closed on resume (resume_authorised refuses two Nones).
            **({"identity": identity} if identity is not None else {}),
        }

    # ── what the caller must look at before serving ───────────────────────────

    @property
    def fatal_problems(self) -> tuple[Any, ...]:
        """Problems that must stop a serve. Decided by ``Problem.fatal`` (ADR 0008 D9)."""
        return tuple(p for p in self.problems if getattr(p, "fatal", True))

    @property
    def degradations(self) -> tuple[Any, ...]:
        """Problems recorded but not blocking a serve."""
        return tuple(p for p in self.problems if not getattr(p, "fatal", True))


# ── construction ──────────────────────────────────────────────────────────────


def _index_entries(assets: Sequence[Asset], structure: CorpusStructure) -> list[Any]:
    """Assets as index entries, tagged from the same ``CorpusStructure`` resolution as the edges."""
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


def _provider_of(model: Any) -> str:
    """Which gateway served the model — ``"openai"``, or ``"custom:<digest>"``.

    A digest of the base URL's host rather than the host: it separates two gateways in the
    config hash, which is the whole job, without writing an internal endpoint into every audit
    row. Absent base URL means the vendor's own, which is the library default.
    """
    base = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None)
    if not base:
        return "openai"
    host = urlsplit(str(base)).netloc or str(base)
    return "custom:" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:8]


def _resolved_knobs(policy: Any) -> dict[str, Any]:
    """Knob defaults with UNSET omitted; policy-resolved knobs included when set."""
    from ..register.knobs import UNSET

    knobs = {k: v for k, v in knob_defaults().items() if v is not UNSET}
    for name in ("guard_rules_enabled", "permitted_functions", "cost_budget"):
        value = getattr(policy, name, UNSET)
        if value is not UNSET and value is not None:
            # `frozenset` and `Mapping` both need a serializable form: the record is written
            # to JSON and read by a gate, and a set is not JSON.
            knobs[name] = sorted(value) if isinstance(value, (set, frozenset)) else value
    return knobs


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
) -> Session:
    """Session over an in-memory asset set. The other constructors funnel here."""
    structure, structure_problems = build_structure(assets)
    entries = _index_entries(assets, structure)
    index = build_index(entries, embedder=embedder, vector_cache=vector_cache)
    knobs = _resolved_knobs(policy)
    if embedder is not None:
        # One resolution of the embedder's comparability identity. It was duplicated here
        # (audit §10), and two copies is how one drifts from `knob_names()`.
        knobs.update(embedding_knobs(embedder))
    if agent_model is not None:
        knobs["llm_model"] = (
            model_id(agent_model) or getattr(agent_model, "_llm_type", None)
            or type(agent_model).__name__
        )
        effort = getattr(agent_model, "reasoning_effort", None)
        if effort:
            knobs["llm_reasoning_effort"] = str(effort)
        # The gateway, not the model. Read off the client's base URL because that is the one
        # place a proxy differs from the vendor while `model_id` returns the same string for
        # both -- see the knob's own note for what that cost.
        knobs["llm_provider"] = _provider_of(agent_model)
        resolved_utility = utility_model or agent_model
        knobs["llm_utility_model"] = (
            model_id(resolved_utility) or getattr(resolved_utility, "_llm_type", None)
            or type(resolved_utility).__name__
        )
        for knob, attr, cast, source in (
            ("llm_max_retries", "max_retries", int, agent_model),
            ("llm_timeout_s", "request_timeout", float, agent_model),
            ("llm_utility_timeout_s", "request_timeout", float, resolved_utility),
        ):
            value = getattr(source, attr, None)
            if value is not None:
                knobs[knob] = cast(value)
    return Session(
        index=index,
        structure=structure,
        assets_by_id={a.id: a for a in assets},
        corpus=for_analyst(list(assets)),
        connector=connector,
        policy=policy,
        corpus_content_hash=corpus_content_hash_,
        prompt_set_hash=prompt_set_hash(),
        knobs_resolved=knobs,
        db_id=db_id,
        run_id=run_id or uuid.uuid4().hex[:16],
        agent_model=agent_model,
        utility_model=utility_model,
        embedder=embedder,
        problems=(*problems, *structure_problems),
        corpus_root=corpus_root,
    )


def from_corpus_dir(root: Path | str, *, schemas: Sequence[str] | None = None, **kwargs: Any) -> Session:
    """A session over a curated corpus on disk.

    ``schemas`` is the manifest, and passing one matters for more than scope: it restricts the
    content hash to the subtrees actually served, so a leftover subtree from another attempt
    enters neither the load nor the digest.
    """
    root = Path(root)
    assets, problems = load_corpus(root, schemas=schemas)
    digest = corpus_content_hash(root, schemas=schemas)
    db_id = kwargs.pop("db_id", None) or (schemas[0] if schemas else root.name)
    return from_assets(
        assets, db_id=db_id, corpus_content_hash_=digest, problems=problems, corpus_root=root, **kwargs
    )


def from_live_schema(schema: str, *, connector: Any, corpus_root: Path | str, **kwargs: Any) -> Session:
    """Seed a corpus from a live schema, **write it**, and load it back.

    The write is what makes this uniform with :func:`from_corpus_dir` — one load path, one
    digest. ``corpus_content_hash`` needs a tree, and reporting a seeded corpus's identity as
    "no digest" would be an absence that compares equal to every other absence.
    """
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


#: Dataclass field names for rebuilding a session with one field replaced.
_FIELDS = tuple(f for f in Session.__dataclass_fields__ if not f.startswith("_"))
