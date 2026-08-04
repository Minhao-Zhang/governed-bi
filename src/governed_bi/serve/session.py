"""The run constants a served turn needs, built once. ADR 0005 §2.8.2.2.

**The seam is run-constant versus per-turn**, and that is exactly what the five deleted
"optional wiring hooks" got wrong. The index, the structure, the corpus, the connector, the
policy, the model, the resolved knobs and the two hashes are constant for every turn of a
run. Putting any of them in per-turn state creates a place where two turns of one run can
**disagree about what they served**, and every retired number in this project came from a
field of exactly that kind.

So one frozen object holds them, and two methods are the only ways in:

* :meth:`Session.configurable` — the mapping the graph's nodes read.
* :meth:`Session.turn` — a turn dict carrying every required record field.

Nothing else assembles either. Before this existed, a caller hand-built eight ``configurable``
keys and a fifteen-key turn, and the only two places that did it correctly were
``eval/harness.py`` and a test fixture. A node handed a half-wired config does not fail
loudly: it **degrades**. No ``index`` means no retrieval, no ``agent_model`` means the stub
answer, and both look like a turn that worked.

**Two sources, one path.** A curated corpus on disk and a live schema both end up as
:func:`~governed_bi.corpus.store.load` over a directory, because
:func:`~governed_bi.corpus.hash.corpus_content_hash` digests a *tree* and raises when there
is none — deliberately, since "no corpus" must be reported out of band rather than as a
digest that would compare equal to another absence. Seeding therefore **writes what it
seeded** and loads it back. That is not a workaround: it makes the two sources genuinely
uniform, and it makes a seeded corpus inspectable and editable, which is the whole point of a
semantic layer.

**Ids are minted here, never accepted from a caller.** See :meth:`Session.turn`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governed_bi.register.prompts import prompt_set_hash

from ..corpus.analyst import for_analyst
from ..corpus.hash import corpus_content_hash
from ..corpus.schema import Asset
from ..corpus.store import load as load_corpus
from ..corpus.store import write as write_asset
from ..ports import Embedder
from ..register.knobs import defaults as knob_defaults
from ..retrieve.index import UnifiedIndex, build_index
from ..retrieve.structure import CorpusStructure, build_structure
from .runtime import model_id
from .state import PER_TURN_RESET

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
    embedder: Embedder | None = None
    problems: tuple[Any, ...] = ()
    corpus_root: Path | None = None
    _turns: list[str] = field(default_factory=list, repr=False, compare=False)

    # ── the two ways in ───────────────────────────────────────────────────────

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        """The ``configurable`` mapping the nodes read, as ``{"configurable": {...}}``.

        Called with no arguments this is **pure run constants**, and two calls compare equal —
        which is the property that makes "two turns of one run cannot disagree" checkable
        rather than merely intended.

        ``question`` is the one concession, and it is forced rather than chosen:
        ``route_retrieve`` reads the question's embedding from
        ``configurable["query_vector"]``, not from state, so a per-turn vector has to travel
        on the config. Passing a question adds exactly that key and nothing else. Without an
        embedder it adds nothing at all, and retrieval stays lexical — which is a supported
        configuration, not a degraded one, because ``DeterministicEmbedder`` exists so the
        model-free path never has to pay for tokens.
        """
        conf: dict[str, Any] = {
            # **No `thread_id`.** It was here and that was the same category error this whole
            # seam exists to prevent: a thread is *per conversation*, not a run constant, so
            # defaulting it to `run_id` silently collapsed every conversation of a run into
            # one — and because LangGraph checkpoints on the **config's** `thread_id`, a
            # caller passing one in the turn state was ignored while believing it had been
            # honoured. The caller supplies it; there is no default to fall back to wrongly.
            "policy": self.policy,
            "index": self.index,
            "structure": self.structure,
            "assets_by_id": dict(self.assets_by_id),
            "corpus": self.corpus,
            "connector": self.connector,
        }
        if self.agent_model is not None:
            conf["agent_model"] = self.agent_model
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
    ) -> dict[str, Any]:
        """A turn dict with every field ``register.record.required_keys`` needs from a caller.

        **Every id is minted here and a caller cannot supply one.** ``run_id``, ``turn_id``,
        ``question_id``, ``attempt_id``, ``corpus_content_hash``, ``prompt_set_hash`` and
        ``knobs_resolved`` are the run's own claims about itself, and the quotability gates
        read them: a client that can set ``corpus_content_hash`` can make two different
        corpora report as one, which is a *forged* comparison rather than a wrong one. This is
        the same rule as ADR 0006's "no tool writes to ``licensed``", and it is why the HTTP
        surface accepts a message rather than a state update.

        Ids are digests of the run and the turn rather than random, so a resumed turn keeps
        its identity. ``question_id`` digests the question text, so the same question asked
        twice in one run is recognisably the same question — which is what a re-serve is.

        **It also clears every per-turn channel, and that is not tidiness.** Under a
        checkpointer a channel outlives its turn, and nothing here cleared one — so a turn
        that crashed left ``path_kind="crashed"`` in the thread, ``_after_guard`` sent the
        **next** turn straight to ``stamp``, and that conversation could never be served
        again. The quieter half is the same shape: a turn refused at ``guard`` never reaches
        ``negative_gate``, so it inherited the *previous* turn's ``negative`` verdict and
        stamped it into its own record as if the gate had run.

        Which fields to clear lives in :data:`~governed_bi.serve.state.PER_TURN_RESET`, beside
        the channel declarations it has to track, and
        ``tests/serve/test_turn_contract.py`` fails until every declared channel is classified
        as per-turn, accumulating, turn identity or a test hook. A new channel is therefore
        cleared by being declared, rather than by someone remembering this method exists.
        """
        if not question or not question.strip():
            raise ValueError("a turn needs a question; an empty one has no answer to record")
        # **The thread is part of the turn's identity.** It was not, so two conversations
        # asking the same question in one run minted the *same* ``turn_id`` — and the audit
        # log keys on it, so ``get_turn`` returned the first and the second turn was
        # unreachable in the trace view. Observed on 2026-08-04: "how many air carriers are
        # listed?" asked at 04:39 and again at 05:52 on different threads, one id.
        #
        # It still digests rather than randomises, which is what "a resumed turn keeps its
        # identity" requires: same thread, same index, same question → same id.
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
            "messages": [],
            "usage": [],
            # Checkpointed, and read back by ``resume.resume_clarification`` to decide whether
            # the caller answering an ``ask_user`` question is the caller who was asked. Absent
            # unless a caller supplies one, and absence **fails closed** —
            # ``resume_authorised`` refuses two ``None``s deliberately, because
            # ``None == None`` is the comparison that let v1 quote
            # ``corpus_content_hash == "unknown"``. Nothing in this repository was passing one,
            # so every clarification on the server path was unanswerable.
            **({"identity": identity} if identity is not None else {}),
        }

    # ── what the caller must look at before serving ───────────────────────────

    @property
    def fatal_problems(self) -> tuple[Any, ...]:
        """Problems that must stop a serve rather than be printed beside one.

        Every problem is reported; these are the ones that mean the corpus is not what it
        claims. An asset that failed to load is missing from retrieval entirely, and an
        unresolvable join endpoint is a licensing question — ADR 0005 §2.8.2 requires both to
        surface where the corpus is built rather than three layers away as a decline on a turn
        that looked ordinary.

        **``Problem.fatal`` decides, not "there is a problem".** ADR 0008 D9: this returned
        *every* problem, so ``python -m governed_bi.serve`` exited 3 on a corpus that
        ``make_graph()`` served without checking anything — the CLI and the server disagreed
        about what is servable, and the CLI was the stricter of two readers of the same list.
        A few-shot that cannot be used and a dimension nobody can place are degradations:
        recorded, counted, and not a reason to refuse a 13 981-asset corpus. A dangling
        structural reference still is.

        Anything that predates the flag is fatal by default, so a problem site nobody has
        classified stops the serve rather than becoming a warning nobody reads.
        """
        return tuple(p for p in self.problems if getattr(p, "fatal", True))

    @property
    def degradations(self) -> tuple[Any, ...]:
        """Problems that are recorded and counted but do not stop a serve.

        Separate from :attr:`fatal_problems` rather than inferred by a caller, so "this
        corpus is smaller than the lake" is a number a run can publish next to its score
        instead of something a reader has to reconstruct from a printed list.
        """
        return tuple(p for p in self.problems if not getattr(p, "fatal", True))


# ── construction ──────────────────────────────────────────────────────────────


def _index_entries(assets: Sequence[Asset], structure: CorpusStructure) -> list[Any]:
    """Assets as index entries, tagged from **the same resolution** the edges used.

    `CorpusStructure.schema_tags` already holds the answer, and taking it from there is the
    point rather than a shortcut. A ``JoinAsset``'s tag is its ``left_table``'s schema (ADR
    0005 §2.2), and ``left_table`` is a physical name needing the reconciliation
    ``build_structure`` has already done — with the three outcomes §2.8.2 specifies, including
    "ambiguous, so refuse". A second lookup here could disagree with the first and **nothing
    would raise**: the join would vote for one schema in routing while connecting two tables
    in another.

    An earlier draft of this function re-derived the tags through ``schema_tag_for``. That is
    exactly the second resolution ``tests/retrieve/test_structure_contract.py`` was written to
    forbid, and it was two lines from being committed.
    """
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


def _resolved_knobs(policy: Any) -> dict[str, Any]:
    """The knob defaults, with the six ``UNSET`` ones resolved from the policy or omitted.

    Six knobs ship ``UNSET`` on purpose — ``register/knobs.py`` says so, and ``Unset.__bool__``
    raises for the same reason ``Measured``'s does: an uncalibrated knob must not be usable as
    if it had a value. Two consequences the first draft of this function got wrong:

    * **``UNSET`` must not appear in a field named ``knobs_resolved``.** A knob that is unset
      is, by definition, not resolved. Carrying the sentinel there is the absence-as-a-value
      defect wearing the opposite sign, and it also fails loudly at the checkpointer, which is
      how the mistake surfaced: ``TypeError: Type is not msgpack serializable: Unset``.
    * **Some of them are resolved by the policy, not by the register.** ``guard_rules_enabled``
      and ``permitted_functions`` are `GovernancePolicy` fields, so a session holding a policy
      *does* know them. Omitting those would under-report what the run was configured with, on
      two ``Role.comparability`` knobs.

    Anything still unresolved is **omitted, not nulled**. That loses no information: the
    register is the authority on which knobs exist, so ``knob_names() - knobs_resolved.keys()``
    is exactly the uncalibrated set, derivable rather than guessed.
    """
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
    embedder: Embedder | None = None,
    vector_cache: MutableMapping[str, Any] | None = None,
    problems: Sequence[Any] = (),
    run_id: str | None = None,
    corpus_root: Path | None = None,
) -> Session:
    """A session over an in-memory asset set. The other two constructors funnel here.

    ``vector_cache`` is passed straight through to ``build_index``, which owns the key format
    (``model|dimensions|text``) and the write-back. It exists on this signature so a server can
    survive a restart without re-embedding the corpus — 8035 summaries in the gold layer — and it
    stays ``None`` for callers that build one index and exit, where a cache is pure overhead.
    """
    structure, structure_problems = build_structure(assets)
    entries = _index_entries(assets, structure)
    index = build_index(entries, embedder=embedder, vector_cache=vector_cache)
    knobs = _resolved_knobs(policy)
    if embedder is not None:
        knobs["embedding_model"] = embedder.model
        knobs["embedding_dimensions"] = embedder.dimensions
    if agent_model is not None:
        # `runtime.model_id` rather than a second local walk over the same attributes: this
        # knob and every `usage[].model` row are compared, and they disagreed — the usage row
        # asked `_llm_type` first and recorded "openai-chat".
        knobs["llm_model"] = (
            model_id(agent_model) or getattr(agent_model, "_llm_type", None)
            or type(agent_model).__name__
        )
        # Recorded **only when the model carries one**, never defaulted. `knobs.py` declares
        # this a comparability knob because two v1 ladders differed only in it, unrecorded,
        # and it moved a baseline arm +2.5pp against a 2.3pp threshold. Writing a default here
        # would recreate that: two runs at different efforts would agree on every hashed field.
        effort = getattr(agent_model, "reasoning_effort", None)
        if effort:
            knobs["llm_reasoning_effort"] = str(effort)
    return Session(
        index=index,
        structure=structure,
        assets_by_id={a.id: a for a in assets},
        corpus=for_analyst(list(assets)),
        connector=connector,
        policy=policy,
        corpus_content_hash=corpus_content_hash_,
        # **Over the whole registry, not over one prompt.** This was `_digest(SYSTEM_PROMPT)`,
        # which was true while there was one prompt and would have become a false comparability
        # key the moment there were two: `register/prompts.py` records why, and `knobs.py`
        # records the ladder that was already invalidated once by an unrecorded treatment.
        prompt_set_hash=prompt_set_hash(),
        knobs_resolved=knobs,
        db_id=db_id,
        run_id=run_id or uuid.uuid4().hex[:16],
        agent_model=agent_model,
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
    digest — and it is also what makes a seeded corpus a thing you can read and edit rather
    than a value that existed for one process. ``corpus_content_hash`` needs a tree, and
    reporting a seeded corpus's identity as "no digest" would be an absence that compares
    equal to every other absence.
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


#: The dataclass's own field names, for the one place that rebuilds a session with one field
#: replaced. Derived rather than listed: a hand-written copy of this drifts the first time a
#: field is added, and the failure would be a session missing a run constant.
_FIELDS = tuple(f for f in Session.__dataclass_fields__ if not f.startswith("_"))
