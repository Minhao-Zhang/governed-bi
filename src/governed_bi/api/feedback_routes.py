"""The return path's HTTP surface: file an observation, read the queue, triage one (ADR 0015).

Replaces ``api/clarification_routes.py``, which wrote reader notes onto the ``ServeState.raised``
checkpoint channel through ``api/raised_write.py``. Both are deleted. What that removes is worth
listing, because all three were consequences of the substrate rather than of the feature:

* **~250 lines of loop-hopping.** ``raised_write.py`` existed to write graph state safely from a
  sync handler — ``run_coroutine_threadsafe`` onto the server's main loop, a runtime probe for
  in-flight runs, a fail-closed degradation when it could not tell. A row in a table needs none of
  it.
* **A 40-round-trip read.** The pending queue projected ``values.raised`` off *every* thread in the
  store, uncached, twice per request. It is one indexed query now, and the walk that remains is the
  interrupt half, which is a handful of threads at any volume.
* **The 409 on a paused thread.** Filing used to refuse there, because ``as_node="raise_note"``
  would consume the live ``ask_user`` interrupt. Nothing writes graph state any more, so **the
  reader whose turn is paused — the one most likely to want to complain — can file.**

**What it does not remove: the disclosure, and it is unchanged rather than improved.** Nothing on
this surface authenticates; reaching the port is sufficient (``docs/enterprise-fork.md``). So the
GET hands any caller every open question, and those questions can name assets, and the POST lets
any caller file against any turn. That was accepted before on the grounds that ``/audit/turns``
already discloses every thread's SQL to the same caller, and the grounds have not changed. Two
things are *arithmetically* better: a note is one row once instead of a row re-serialised into every
later checkpoint of its thread, and the store is sweepable where the channel was not. Neither is a
control. Under a real ``AccessPolicy`` both verbs still owe the withholding the tools apply.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from governed_bi.feedback.cluster import clusters
from governed_bi.feedback.events import (
    Category,
    DeclineReason,
    Kind,
    Observation,
    ObservationState,
    Patch,
    PatchIntent,
    PatchState,
    Source,
)
from governed_bi.feedback.lifecycle import TransitionRefused, is_open
from governed_bi.feedback.store import (
    FeedbackStore,
    Rejected,
    mint_observation_id,
    mint_patch_id,
    utc_now,
)
from governed_bi.feedback.validate import NOTE_MAX_CHARS
from governed_bi.register.assets import AssetType

__all__ = ["make_admin_router", "make_feedback_router", "PENDING_SOURCE_INTERRUPT"]

#: ``source`` on a pending row that came from a live ``ask_user`` interrupt rather than from the
#: store. Kept from the deleted module: the client switches on it to decide which card to draw.
PENDING_SOURCE_INTERRUPT = "interrupt"

#: Cap on the optional after-the-fact ``expected`` line. Short on purpose — it is one claim
#: ("about 400, not 4102"), and a field that invites a paragraph gets a paragraph nobody reads.
EXPECTED_MAX_CHARS = 200


def make_feedback_router(pending: Any, turn_log: Any, store: FeedbackStore) -> APIRouter:
    """Routes over the interrupt reader, the turn log, and the feedback store.

    ``pending`` exposes ``pending(limit=, offset=)`` and ``PENDING_FIELDS``; ``turn_log`` exposes
    ``get_turn``. The store is passed rather than constructed here so a test drives a ``tmp_path``
    one and the composition root owns the path — the same reason ``accept_node`` takes its session.
    """
    router = APIRouter()

    # ── the reader's two verbs, both enabled ──────────────────────────────────

    @router.post("/turns/{turn_id}/raised", status_code=201)
    def file_on_turn(turn_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """File an observation about a finished turn.

        The path and the ``kind`` values are unchanged, so a client written against the deleted
        route keeps working. ``category`` and ``expected`` are new and optional: the first tap
        files something valid, and a refinement is never a gate.

        Validated before the turn is read, so a rejected body costs one comparison. The store
        validates again — it is the thing that must not accept a bad row — and this layer's job is
        only to turn a refusal into a status code a client can act on.
        """
        kind = _kind_or_422(body)
        category = _category_or_422(body)
        note = str((body or {}).get("note") or "").strip()
        expected = str((body or {}).get("expected") or "").strip()
        if len(note) > NOTE_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"note must be at most {NOTE_MAX_CHARS} characters, not {len(note)}",
            )
        if len(expected) > EXPECTED_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"expected must be at most {EXPECTED_MAX_CHARS} characters, not "
                    f"{len(expected)}"
                ),
            )

        entry = turn_log.get_turn(str(turn_id))
        if entry is None:
            raise HTTPException(status_code=404, detail="turn not found")
        record = entry.get("record") or {}
        thread_id = str(record.get("thread_id") or "")
        if not thread_id:
            raise HTTPException(status_code=404, detail="turn has no thread_id")
        question = str(entry.get("question") or "")
        if not question:
            # The validator refuses a question-less observation, and turning that into a 422 here
            # says which side is at fault: the turn was recorded without the thing the failure is
            # about, so filing against it would make a row nobody can review.
            raise HTTPException(
                status_code=422,
                detail=(
                    "the turn's record carries no question, so an observation about it could not "
                    "be reviewed"
                ),
            )

        note_text = "\n".join(part for part in (expected and f"expected: {expected}", note) if part)
        observation = Observation(
            observation_id=mint_observation_id(),
            filed_at=utc_now(),
            source=Source.operator,
            kind=kind,
            state=ObservationState.open,
            category=category,
            note=note_text.strip(),
            turn_id=str(turn_id),
            thread_id=thread_id,
            question=question,
            outcome=entry.get("outcome") or record.get("outcome"),
            refused_by=record.get("refused_by"),
            generated_sql=record.get("generated_sql"),
            licensed=tuple(str(t) for t in (record.get("licensed") or ())),
            schemas=tuple(str(s) for s in (record.get("schemas") or ())),
            corpus_content_hash=record.get("corpus_content_hash"),
            prompt_set_hash=record.get("prompt_set_hash"),
        )
        try:
            observation_id = store.file(observation)
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {"ok": True, "observation": _wire_observation_detail(store, observation_id)}

    @router.patch("/observations/{observation_id}")
    def amend(observation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Add or replace the note on an observation nobody has triaged yet.

        The inversion that matters: the note is asked for **after** the filing succeeded, so it is
        a bonus rather than a gate. A form whose free-text field blocks submission is a form that
        collects empty free text.

        409 once somebody has looked, because a reviewer reading a row while its text changes
        underneath them is worse than a reader having to file a second observation.
        """
        current = store.get(observation_id)
        if current is None:
            raise HTTPException(status_code=404, detail="observation not found")
        if current.state is not ObservationState.open:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"observation is {current.state.value}; a note can only be amended while "
                    "nobody has triaged it"
                ),
            )
        note = str((body or {}).get("note") or "").strip()
        if len(note) > NOTE_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"note must be at most {NOTE_MAX_CHARS} characters, not {len(note)}",
            )
        store.amend_note(observation_id, note)
        return {"ok": True, "observation": _wire_observation_detail(store, observation_id)}

    # ── reads ─────────────────────────────────────────────────────────────────

    @router.get("/clarifications/pending")
    def pending_clarifications(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Open questions and untriaged observations, oldest first.

        Two populations from two stores, unioned here rather than by either — a thread reader reads
        threads and the feedback store reads itself, and ``api/`` is where a surface that wants both
        composes them. That seam is also what deleted the 40-round-trip walk: the note half is one
        indexed query.

        Answering a paused question from here is still refused (ADR 0006 B9). The link the UI grows
        instead routes an operator's answer into the semantic layer as an observation, which is the
        provenance gate this whole design is.
        """
        page = pending.pending(limit=limit, offset=offset)
        interrupt_rows = [dict(row) for row in page.rows]
        stored = store.queue(states=[ObservationState.open], limit=limit, offset=0)
        note_rows = [_as_pending_row(obs) for obs in stored.rows]
        rows = sorted(interrupt_rows + note_rows, key=lambda r: str(r.get("asked_at") or ""))
        return {
            "rows": rows[:limit],
            "meta": {
                "n": len(rows[:limit]),
                "truncated": bool(page.truncated) or stored.truncated or len(rows) > limit,
                "threads_scanned": int(page.threads_scanned),
                "limit": limit,
                "offset": offset,
                "columns": list(pending.PENDING_FIELDS),
            },
        }

    @router.get("/observations")
    def list_observations(
        state: str | None = Query(None),
        category: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        group: str | None = Query(None, description="'cluster' to group structurally"),
    ) -> dict[str, Any]:
        """The queue. **Oldest first**, because the row that has waited longest is the one to act on.

        ``group=cluster`` returns clusters instead of rows. The grouping is structural — category
        and schema, nothing more — and the caption the client must render says so: nothing here read
        the questions and decided they mean the same thing.
        """
        states = _states_or_422(state)
        page = store.queue(
            states=states, category=_category_value_or_422(category), limit=limit, offset=offset
        )
        if group == "cluster":
            grouped = clusters(page.rows)
            return {
                "clusters": [
                    {
                        "key": c.key,
                        "category": c.category.value if c.category else None,
                        "schema": c.db_id,
                        "n": c.n,
                        "n_distinct_questions": c.n_distinct_questions,
                        "shared_missing_tables": list(c.missing_tables),
                        "oldest_filed_at": c.oldest_filed_at,
                        "observations": [_wire_observation(o) for o in c.observations],
                    }
                    for c in grouped
                ],
                "meta": _queue_meta(page, limit, offset, grouped=len(grouped)),
            }
        return {
            "rows": [_wire_observation(o) for o in page.rows],
            "meta": _queue_meta(page, limit, offset),
        }

    @router.get("/observations/{observation_id}")
    def get_observation(observation_id: str) -> dict[str, Any]:
        """One observation, its patches, and each patch's **derived** landing state."""
        row = _wire_observation_detail(store, observation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="observation not found")
        return row

    return router


# ── projections ───────────────────────────────────────────────────────────────


def _wire_observation(obs: Observation) -> dict[str, Any]:
    """One observation on the wire. ``open`` is **computed**, never read from a column."""
    return {
        "observation_id": obs.observation_id,
        "filed_at": obs.filed_at,
        "source": obs.source.value,
        "kind": obs.kind.value,
        "category": obs.category.value if obs.category else None,
        "state": obs.state.value,
        "open": is_open(obs.state),
        "note": obs.note,
        "decline_reason": obs.decline_reason.value if obs.decline_reason else None,
        "duplicate_of": obs.duplicate_of,
        "blocked_note": obs.blocked_note,
        "turn_id": obs.turn_id,
        "thread_id": obs.thread_id,
        "question": obs.question,
        "outcome": obs.outcome,
        "refused_by": obs.refused_by,
        "generated_sql": obs.generated_sql,
        "licensed": list(obs.licensed),
        "schemas": list(obs.schemas),
        "missing_tables": list(obs.missing_tables),
        "gold_sql": obs.gold_sql,
        "gold_fingerprint": obs.gold_fingerprint,
        "pred_fingerprint": obs.pred_fingerprint,
        "quality_flags": list(obs.quality_flags),
        "arm": obs.arm,
        "question_id": obs.question_id,
        "db_id": obs.db_id,
        "corpus_content_hash": obs.corpus_content_hash,
        # Named on the wire because the review surface must label it: an imported question comes
        # from the held-out split, and a person who writes corpus prose from it contaminates the
        # benchmark. Conformance rule V12 is the gate; this field is what tells the reader.
        "question_is_held_out": obs.source is Source.eval,
    }


def _wire_observation_detail(store: FeedbackStore, observation_id: str) -> dict[str, Any] | None:
    obs = store.get(observation_id)
    if obs is None:
        return None
    return {
        **_wire_observation(obs),
        # `derived_state` is null on every row here. It is derived and stored nowhere, and this
        # route has no session, so it cannot read the corpus; answering "did this land" from an
        # empty corpus view would say `superseded` about everything. The review surface composes
        # the corpus half, and `tools/check_landed.py` is the one that reads it.
        "patches": [
            {**_wire_patch(store, patch), "derived_state": None}
            for patch in store.patches_of(observation_id)
        ],
        "history": list(store.history(observation_id)),
    }


def _as_pending_row(obs: Observation) -> dict[str, Any]:
    """An observation as a pending-queue row, in the shape the interrupt half uses.

    Every declared column is present and null where it does not apply, never absent: a client
    forced to tell "no value" from "no such key" ends up guessing which kind of row it holds.
    """
    return {
        "asked_at": obs.filed_at,
        "question": obs.question,
        "why": obs.note or (obs.category.value if obs.category else ""),
        "clarification_id": None,
        "turn_id": obs.turn_id,
        "thread_id": obs.thread_id,
        "source": obs.kind.value,
        "basis": None,
        "observation_id": obs.observation_id,
    }


def _queue_meta(page: Any, limit: int, offset: int, **extra: Any) -> dict[str, Any]:
    return {
        "n": len(page.rows),
        "total": page.total,
        "truncated": page.truncated,
        "limit": limit,
        "offset": offset,
        **extra,
    }


# ── request parsing ───────────────────────────────────────────────────────────


def _kind_or_422(body: dict[str, Any] | None) -> Kind:
    raw = str((body or {}).get("kind") or "")
    try:
        return Kind(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(k.value for k in Kind)}",
        ) from exc


def _category_or_422(body: dict[str, Any] | None) -> Category | None:
    raw = str((body or {}).get("category") or "")
    if not raw:
        return None
    try:
        return Category(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(c.value for c in Category)}",
        ) from exc


def _category_value_or_422(raw: str | None) -> Category | None:
    if not raw:
        return None
    try:
        return Category(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown category {raw!r}") from exc


def _states_or_422(raw: str | None) -> list[ObservationState] | None:
    """``open,triaged`` → the members, or 422 naming the vocabulary."""
    if not raw:
        return None
    out: list[ObservationState] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ObservationState(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown state {part!r}; declared: "
                    f"{sorted(s.value for s in ObservationState)}"
                ),
            ) from exc
    return out or None


def make_admin_router(store: FeedbackStore) -> APIRouter:
    """The steward's verbs. **Mounted only when ``GOVERNED_BI_FEEDBACK_ADMIN`` is set.**

    404 when unmounted rather than 403, because a 403 confirms the route exists. This is a
    deployment switch and not an identity: ``api/auth.py`` returns one principal, so with the
    switch on, whoever reaches the port is the steward. The honest control is that it is off.
    """
    router = APIRouter()

    @router.post("/observations/{observation_id}/triage")
    def triage(observation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Move an observation. The transition table decides what is legal, not this handler."""
        raw_to = str((body or {}).get("to") or "")
        try:
            to = ObservationState(raw_to)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"to must be one of {sorted(s.value for s in ObservationState)}",
            ) from exc

        reason = (body or {}).get("decline_reason")
        decline_reason: DeclineReason | None = None
        if reason:
            try:
                decline_reason = DeclineReason(str(reason))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"decline_reason must be one of {sorted(r.value for r in DeclineReason)}",
                ) from exc

        try:
            store.move(
                observation_id,
                to=to,
                detail=str((body or {}).get("detail") or ""),
                decline_reason=decline_reason,
                duplicate_of=(body or {}).get("duplicate_of"),
                blocked_note=str((body or {}).get("blocked_note") or ""),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="observation not found") from exc
        except TransitionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {"ok": True, "observation": _wire_observation_detail(store, observation_id)}

    @router.post("/patches", status_code=201)
    def draft_patch(body: dict[str, Any]) -> dict[str, Any]:
        """Draft a patch against one asset field.

        The handler builds the ``Patch`` and hands it to the store; every rule about what a patch
        may say lives in ``feedback/validate.py``, so a field this endpoint forgot to police is
        still refused. It does **not** write to the corpus and cannot: the only write is a human's
        ``git commit``, and this row is what produces the diff for it.
        """
        try:
            patch = _patch_from_body(body or {})
        except _BadRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        observations = [str(o) for o in ((body or {}).get("observations") or [])]
        for observation_id in observations:
            if store.get(observation_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"no observation {observation_id!r}"
                )
        try:
            patch_id = store.draft(patch, observations=observations)
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {"ok": True, "patch": _wire_patch(store, store.get_patch(patch_id))}

    @router.post("/patches/{patch_id}/withdraw")
    def withdraw(patch_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Abandon a patch, with a reason. Legal from ``draft`` and from ``exported`` both.

        From ``exported`` too, because a bundle that went out and was rejected in review is the
        common case, and leaving it `exported` forever is the unclosable row this design exists to
        remove. What the corpus does with a bundle already applied is not this store's business --
        ``tools/check_landed.py`` reads that from the corpus.
        """
        reason = str((body or {}).get("reason") or "").strip()
        if not reason:
            raise HTTPException(
                status_code=422,
                detail=(
                    "reason is required. A terminal state whose why lives only in somebody's "
                    "memory is a patch that gets re-drafted from scratch six weeks later."
                ),
            )
        try:
            store.move_patch(
                patch_id, to=PatchState.withdrawn, withdrawn_reason=reason, detail=reason
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="patch not found") from exc
        except TransitionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {"ok": True, "patch": _wire_patch(store, store.get_patch(patch_id))}

    @router.get("/patches")
    def list_patches(
        state: str | None = Query(default=None, description="comma-separated PatchState values"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Patches, newest first. No derived landing state on this route, deliberately.

        Landing is answered by reading the corpus, which this process does at load time and not
        per request. Putting a stale ``landed_*`` on a list row would make the list disagree with
        ``tools/check_landed.py`` -- two answers to one question, which is the defect the derived
        state was introduced to avoid.
        """
        states: list[PatchState] | None = None
        if state:
            states = []
            for part in (p.strip() for p in state.split(",") if p.strip()):
                try:
                    states.append(PatchState(part))
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"unknown state {part!r}; declared: "
                            f"{sorted(s.value for s in PatchState)}"
                        ),
                    ) from exc
        page = store.patches(states=states or None, limit=limit, offset=offset)
        return {
            "patches": [_wire_patch(store, p) for p in page.rows],
            "meta": _queue_meta(page, limit, offset),
        }

    return router


class _BadRequest(ValueError):
    """A body this surface can reject without asking the store."""


def _patch_from_body(body: dict[str, Any]) -> Patch:
    """Build a ``Patch`` from a request body, or raise ``_BadRequest`` with what is wrong.

    Enum members are looked up rather than trusted, and the error names the declared set: a client
    sending ``"edit"`` for ``"edit_asset"`` learns the vocabulary from the 422 instead of from a
    stack trace.
    """
    try:
        intent = PatchIntent(str(body.get("intent") or ""))
    except ValueError as exc:
        raise _BadRequest(
            f"intent must be one of {sorted(i.value for i in PatchIntent)}"
        ) from exc

    asset_type: AssetType | None = None
    raw_type = body.get("asset_type")
    if raw_type:
        try:
            asset_type = AssetType(str(raw_type))
        except ValueError as exc:
            raise _BadRequest(
                f"asset_type must be one of {sorted(a.value for a in AssetType)}"
            ) from exc

    namespace = str(body.get("namespace") or "").strip()
    if not namespace:
        raise _BadRequest("namespace is required")

    return Patch(
        patch_id=mint_patch_id(),
        created_at=utc_now(),
        author=Source.operator,
        intent=intent,
        state=PatchState.draft,
        namespace=namespace,
        rationale=str(body.get("rationale") or ""),
        asset_type=asset_type,
        asset_id=_optional(body.get("asset_id")),
        field_path=_optional(body.get("field_path")),
        was=_optional(body.get("was")),
        becomes=_optional(body.get("becomes")),
        asset_yaml=_optional(body.get("asset_yaml")),
        base_corpus_content_hash=str(body.get("base_corpus_content_hash") or ""),
    )


def _optional(value: Any) -> str | None:
    """``None`` and ``""`` are different answers on a ``Patch``: ``was=""`` says the field was
    empty, ``was=None`` says this patch does not edit a field. JSON gives one of them by omission,
    so an empty string is preserved rather than folded into ``None``."""
    return None if value is None else str(value)


def _wire_patch(store: FeedbackStore, patch: Patch | None) -> dict[str, Any]:
    """A patch on the wire. ``ladder`` goes out as the store holds it, because the review surface
    renders whatever tiers ran rather than a fixed three."""
    if patch is None:  # pragma: no cover - callers pass a row they just wrote
        return {}
    return {
        "patch_id": patch.patch_id,
        "created_at": patch.created_at,
        "author": patch.author.value,
        "intent": patch.intent.value,
        "state": patch.state.value,
        "namespace": patch.namespace,
        "rationale": patch.rationale,
        "asset_type": patch.asset_type.value if patch.asset_type else None,
        "asset_id": patch.asset_id,
        "field_path": patch.field_path,
        "was": patch.was,
        "becomes": patch.becomes,
        "base_corpus_content_hash": patch.base_corpus_content_hash,
        "expected_corpus_content_hash": patch.expected_corpus_content_hash,
        "ladder": dict(patch.ladder),
        "withdrawn_reason": patch.withdrawn_reason,
        "observations": [o.observation_id for o in store.observations_of(patch.patch_id)],
    }
