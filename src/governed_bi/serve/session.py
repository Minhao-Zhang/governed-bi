"""Run constants for a served turn, built once (ADR 0005 §2.8.2.2).

Run-constant vs per-turn: index, structure, corpus, connector, policy, model, knobs,
and hashes live on :class:`Session`. Entry points: :meth:`Session.configurable` and
:meth:`Session.turn`. Ids are minted here, never accepted from a caller.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from governed_bi.register.prompts import prompt_set_hash
from governed_bi.register.prompts import select as selected_variants

from ..corpus.analyst import for_analyst
from ..corpus.hash import corpus_content_hash
from ..corpus.schema import Asset, AssetType, MetricAsset, TableAsset, TermAsset
from ..corpus.store import load as load_corpus
from ..corpus.store import write as write_asset
from ..model.embedder import embedding_knobs
from ..model.provider import reasoning_effort_of
from ..ports import Embedder
from ..register.knobs import defaults as knob_defaults
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


def _runtime_overrides() -> dict[str, Any]:
    """The operator's live switches. A function so the read happens per call rather than at import,
    and so `Session.turn` names the same thing `_resolved_knobs` does.
    """
    from .runtime_overrides import overrides

    return dict(overrides())


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
    #: ``{prompt name -> variant}`` this run selected; empty means every prompt at its default.
    #: Reaches the nodes through :meth:`configurable` and **must** be the same mapping
    #: :attr:`prompt_set_hash` was computed from, or the run records a treatment it did not send.
    prompt_variants: Mapping[str, str] = field(default_factory=dict)
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
        if self.prompt_variants:
            conf["prompt_variants"] = dict(self.prompt_variants)
        if question and self.embedder is not None:
            conf["query_vector"] = self.embedder.embed([question])[0]
        if self.corpus_root is not None:
            # UtkuAI, ported: the write target for `serve/nodes/mine_corpus.py`. Conditional
            # like `agent_model`/`embedder` above -- a session with no curated corpus to write
            # back to must not hand a node a key whose presence it would otherwise take as
            # permission to write.
            conf["corpus_root"] = self.corpus_root
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
            # The operator's switches are layered on **per turn**, not just at session
            # construction, because `_resolved_knobs` runs once and this mapping is a copy of what
            # it produced. Without this a switch flipped after boot writes its file, reports
            # success, and changes nothing a node reads -- the same defect as a control with no
            # server behind it, built in reverse. Layering it here also keeps the record honest:
            # this is the mapping the turn actually ran under, which is what
            # `measure/gates.py::_knobs_resolved_gate` reads to catch a mid-run flip as drift.
            "knobs_resolved": {**self.knobs_resolved, **_runtime_overrides()},
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


def _is_excluded(asset: Any) -> bool:
    return bool(getattr(getattr(asset, "governance", None), "excluded", False))


#: The references a type **cannot lose**. ``build_structure`` records a *fatal* problem when one
#: of these fails to resolve, so an asset whose required reference is excluded has to leave with
#: it — otherwise honouring a governance flag produces a corpus that refuses to serve.
#:
#: Read against ``retrieve/structure.py``, not invented here: these are exactly the endpoints
#: whose ``Problem`` takes the default ``fatal=True``. ``few_shot.sql`` is absent on purpose —
#: :func:`~governed_bi.retrieve.structure._link_few_shot` passes ``fatal=False``, so a few-shot
#: citing an excluded table degrades rather than stops (see :func:`_visible`).
_REQUIRED_TABLE_REFS: Mapping[AssetType, tuple[str, ...]] = {
    AssetType.column: ("parent_table",),
    AssetType.join: ("left_table", "right_table"),
    AssetType.metric: ("base_table",),
}


def _excluded_closure(assets: Sequence[Asset]) -> frozenset[str]:
    """Ids that must leave the served set: the ones a person marked, plus their dependents.

    Endpoints are resolved with :func:`~governed_bi.retrieve.structure.bind_endpoint` rather
    than compared as strings, because ``left_table``/``base_table``/``parent_table`` may be any
    of four spellings — asset id, ``table_id``, bare ``physical_name``, or the engine's
    ``{schema}.{physical_name}``. A string test would miss the three that are not the id and let
    the fatal problem back in.

    The lookup is built over **every** table including the excluded ones, so an endpoint binds
    to its real target and is then tested for exclusion. Binding against the survivors instead
    would make an ambiguous bare name resolve to whichever table happened to remain.

    ``scope`` reproduces structure.py's three call sites in one expression: only ``column``
    declares a ``schema`` field, so ``join`` and ``metric`` get ``None`` — which is what
    ``_link_join`` and ``_link_metric`` pass.

    A fixpoint rather than one pass. Today no required reference points at a join or a metric,
    so a single round would do; a bounded loop cannot be wrong when one is added.

    ``asset_type`` is read inline rather than through a local helper: ``structure.py`` already
    owns ``_type_of``, and ``tools/check_one_implementation.py`` is right to refuse a second one.
    """
    lookup = table_lookup({a.id: a for a in assets if a.asset_type is AssetType.table})
    out = {asset.id for asset in assets if _is_excluded(asset)}
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
                # `bound is None` means the corpus was already broken or ambiguous there.
                # Leave it: `build_structure` reports it exactly as it does today, and
                # inventing an exclusion would hide a curation defect behind a policy flag.
                if bound is not None and bound in out:
                    out.add(asset.id)
                    break
        if len(out) == before:
            break
    return frozenset(out)


def _without_excluded_refs(asset: Asset, excluded: frozenset[str]) -> Asset:
    """``asset`` with its **optional** references to excluded ids removed.

    The collections and the one nullable binding. Dropping a member costs recall; dropping the
    whole asset would withhold text that stands on its own — a term still glosses business
    vocabulary once its binding is gone, which ``_link_term`` treats as a state rather than a
    defect ("an unbound term is a state, not a defect").
    """
    # ``isinstance`` rather than ``asset_type`` here: ``Asset`` is a union of the eight
    # dataclasses, and narrowing is what lets ``replace`` type-check per field.
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


def _visible(assets: Sequence[Asset]) -> list[Asset]:
    """``assets`` minus everything ``governance.excluded`` reaches (D6).

    ``Governance.excluded`` is documented as removing an asset "from everything the analyst
    sees, in every environment", but only :func:`for_analyst` honoured it. The index was built
    from the full set, so an excluded column still scored in both channels, still spent one of
    the 30 column slots, and still rendered once the reference closure pulled it in from its
    parent table — three ways for an asset nobody may query to crowd out one they need.
    Filtering here makes the index, ``assets_by_id`` and the structure agree; ``for_analyst``
    still receives the **whole** list, because it needs the excluded columns' keys to make
    ``check()`` refuse SQL that names one.

    **Dropping an asset is not enough on its own, and the first version of this shipped that
    way.** Removing an asset leaves every reference to it dangling, and a dangling *required*
    reference is a ``fatal`` problem — so ``serve/__main__.py`` refuses to serve and
    ``/routes`` reports ``servable: false``. Measured on the two shapes the corpus actually
    has: excluding one column left its parent table still declaring the id in ``columns``
    (``TableAsset.columns`` holds derived ids), and excluding one table left every join on it
    unbindable. Withholding a decoy — the whole point of the flag — made the corpus unloadable.

    So exclusion propagates two ways, split on what structure.py treats as required:

    * a **required** reference to an excluded asset excludes the referrer too
      (:data:`_REQUIRED_TABLE_REFS`, via :func:`_excluded_closure`);
    * an **optional** one is pruned in place (:func:`_without_excluded_refs`).

    One thing is deliberately *not* handled: a ``few_shot`` whose ``sql`` names an excluded
    table still ships, because that reference is non-fatal and pruning it would mean parsing
    SQL here. It teaches the model a query over a withheld table, which is a curation question
    rather than a loading one — worth deciding, not worth guessing at inside this function.
    """
    if not any(_is_excluded(asset) for asset in assets):
        return list(assets)  # the path every existing corpus takes: nothing is copied
    excluded = _excluded_closure(assets)
    return [
        _without_excluded_refs(asset, excluded) for asset in assets if asset.id not in excluded
    ]


def _provider_of(model: Any) -> str:
    """Which gateway served the model — ``"openai"``, ``"bedrock"``, or ``"custom:<digest>"``.

    A digest of the base URL's host rather than the host: it separates two gateways in the
    config hash, which is the whole job, without writing an internal endpoint into every audit
    row.

    Bedrock is asked separately because it has no base URL at all. Falling through to
    ``"openai"`` for it would be the defect this function is used to close, one gateway over:
    a wrong provider on a comparability field reads as a measurement, where a null reads as an
    absence. Absent both, the vendor's own endpoint is the library default.
    """
    base = getattr(model, "openai_api_base", None) or getattr(model, "base_url", None)
    if not base:
        # `_llm_type` is LangChain's own label; `ChatBedrockConverse` reports
        # "chat-bedrock-converse" and carries no URL to digest.
        label = str(getattr(model, "_llm_type", "") or "")
        return "bedrock" if "bedrock" in label.lower() else "openai"
    host = urlsplit(str(base)).netloc or str(base)
    return "custom:" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:8]


def _model_name(model: Any) -> str:
    """The id to record for ``model``. One derivation, used by every model knob.

    Three call sites wrote this expression out; ``chat_model`` and ``llm_utility_model``
    disagreeing about what "the model" means is not a thing anyone should be able to
    introduce by editing one of them.
    """
    return (
        model_id(model) or getattr(model, "_llm_type", None) or type(model).__name__
    )


def _resolved_knobs(policy: Any) -> dict[str, Any]:
    """Every declared knob, resolved. **No key is ever omitted.**

    Omission was the defect. This dropped every ``UNSET`` knob and re-added exactly three from
    the policy, so ``sqlglot_version``, ``negative_tau`` and ``cost_budget`` were *absent* —
    not null — from all 8,106 rows of the six arms in ``runs/eval/``. That is worse than it
    sounds: ``measure/gates.py::_knobs_resolved_gate`` compares rows with ``row.get(key)``, so
    a key missing from every row compares equal to itself and the drift gate passes on a
    configuration it never saw.

    Three resolutions, in order:

    * **``UNSET`` becomes ``None``.** ``UNSET`` is not JSON and "this run had no calibrated
      value" is a measurement worth writing down. Readers are unaffected: ``int_knob`` and
      friends fall through a ``None`` to ``knob_default``, which is still ``UNSET`` and still
      raises rather than substituting a number.
    * **The policy, then the resolvers.** ``sqlglot_version``'s own note says it is UNSET
      "so it cannot be silently absent" and it was silently absent on every row;
      ``govern/functions.py`` has implemented exactly that resolver all along and nothing
      called it. Canonical function names are release-dependent and the ADR 0006 allowlist is
      keyed on them, so without it no artifact says which vocabulary the governance layer was
      enforcing. ``negative_tau`` stays ``None`` and that is the true value: the gate ships
      disabled and ``serve/nodes/negative.py`` writes ``"tau": None`` on every turn.
    * **The environment last**, because that is the order the readers use — see
      :func:`~governed_bi.register.knobs.env_override`.

    ``access_grant`` is resolved **from the policy and never from the register** (ADR 0012 §7).
    It is not in the tuple below because it is not a knob the policy happens to carry: it is a
    value type whose *digest* is the comparability fact, and the register's default is ``None``
    precisely so that a run whose policy was never threaded records an absence rather than the
    open grant's digest. Resolving it from :func:`~governed_bi.register.knobs.knob_default`
    would publish "open" for a fork shipping a restriction — the ``agent_recursion_limit``
    defect in the security register, which is what §3.10 of open-work.md is a whole section
    about.
    """
    from ..govern.functions import sqlglot_version
    from ..register.knobs import UNSET, env_override

    knobs = {k: (None if v is UNSET else v) for k, v in knob_defaults().items()}
    for name in ("guard_rules_enabled", "permitted_functions", "cost_budget"):
        value = getattr(policy, name, UNSET)
        if value is not UNSET and value is not None:
            # `frozenset` and `Mapping` both need a serializable form: the record is written
            # to JSON and read by a gate, and a set is not JSON.
            knobs[name] = sorted(value) if isinstance(value, (set, frozenset)) else value
    grant = getattr(policy, "access_grant", None)
    digest = getattr(grant, "digest", None)
    if callable(digest):
        knobs["access_grant"] = digest()
    knobs["sqlglot_version"] = sqlglot_version()
    # **The operator's runtime switches are deliberately NOT applied here.** They are layered by the
    # two readers that mint a claim -- `Session.turn` and `api/routes.py::capabilities_for` -- and
    # this function is what produces the base they layer over. Applying them in both places is the
    # bug that shipped first: a session built while a switch was on baked `True` into this mapping,
    # so layering `{}` over it after the operator cleared the switch still resolved `True`. The
    # switch turned on and would not turn off, which is worse than one that does neither, because
    # the operator cannot tell which state the engine is in. Found by clicking it off.
    for name in knobs:
        override = env_override(name)
        if override is not None:
            knobs[name] = override
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
    prompt_variants: Mapping[str, str] | None = None,
) -> Session:
    """Session over an in-memory asset set. The other constructors funnel here."""
    # `_visible` for the three views the analyst can reach; the full list for `for_analyst`,
    # which turns the excluded columns into `check()` refusals rather than silent absences.
    visible = _visible(assets)
    structure, structure_problems = build_structure(visible)
    entries = _index_entries(visible, structure)
    index = build_index(entries, embedder=embedder, vector_cache=vector_cache)
    knobs = _resolved_knobs(policy)
    # The variant of every prompt this run selected -- exactly the "resolved variant per
    # stage" the knob is declared to hold. `register/prompts.select()` computed it all along
    # and no caller wrote it down, so `prompt_set` was null on v2, v3, v4 and v5, the four
    # arms whose entire treatment is a prompt variant. They were still *distinguishable*,
    # because `prompt_set_hash` is on the row and does differ; they were not *nameable* --
    # nothing in an artifact said which variant produced which digest.
    #
    # Here rather than in the driver, and from the same `prompt_variants` argument
    # `prompt_set_hash` is computed from two lines below: a second knob-resolution site is
    # the defect this repository keeps paying for, and resolving it in the harness would
    # leave the served path null. `select` raises on an undeclared prompt name, which is the
    # same refusal `prompt_set_hash` already makes on the same input.
    knobs["prompt_set"] = selected_variants(prompt_variants)
    if embedder is not None:
        # One resolution of the embedder's comparability identity. It was duplicated here
        # (audit §10), and two copies is how one drifts from `knob_names()`.
        knobs.update(embedding_knobs(embedder))
    resolved_utility = utility_model or agent_model
    if agent_model is not None:
        # The agent model itself, under the name the register declares. `llm_model` used to be
        # written here beside it and is gone: `KNOB_REGISTER` never declared it, so it sat
        # outside `comparability_keys()` -- and on run1, run2, v3-pinned and v3-fold it was the
        # ONLY field carrying the model, which meant the one value that could have told those
        # arms apart was outside the comparability set. One spelling, and it is the declared
        # one.
        knobs["chat_model"] = _model_name(agent_model)
        # Whichever of the three spellings this client wears. `getattr(model,
        # "reasoning_effort")` was here and is only OpenAI's, so the proxy arms recorded null
        # while running at `high` -- see `model/provider.py::reasoning_effort_of`.
        effort = reasoning_effort_of(agent_model)
        if effort:
            knobs["llm_reasoning_effort"] = str(effort)
        # The gateway, not the model. Read off the client's base URL because that is the one
        # place a proxy differs from the vendor while `model_id` returns the same string for
        # both -- see the knob's own note for what that cost.
        knobs["llm_provider"] = _provider_of(agent_model)
        for knob, attr, cast in (
            ("llm_max_retries", "max_retries", int),
            ("llm_timeout_s", "request_timeout", float),
        ):
            value = getattr(agent_model, attr, None)
            if value is not None:
                knobs[knob] = cast(value)
    if resolved_utility is not None:
        # Written even when it falls back to the agent model, per the knob's note: "shared one
        # model" and "split them" are two treatments.
        knobs["llm_utility_model"] = _model_name(resolved_utility)
        # Same argument as `llm_provider`, one surface over, and it had no writer at all: six
        # proxy-served arms published the register default "openai" on this field while
        # `llm_provider` on the same row said "custom:007df842".
        knobs["llm_utility_provider"] = _provider_of(resolved_utility)
        timeout = getattr(resolved_utility, "request_timeout", None)
        if timeout is not None:
            knobs["llm_utility_timeout_s"] = float(timeout)
    return Session(
        index=index,
        structure=structure,
        assets_by_id={a.id: a for a in visible},
        corpus=for_analyst(list(assets)),
        connector=connector,
        policy=policy,
        corpus_content_hash=corpus_content_hash_,
        prompt_set_hash=prompt_set_hash(prompt_variants),
        knobs_resolved=knobs,
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
