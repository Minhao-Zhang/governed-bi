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

**The disclosure, and this docstring used to get it wrong.** It said "unchanged rather than
improved". It was not unchanged: ``main``'s ``raised`` row carried **seven** fields
(``kind``, ``turn_id``, ``thread_id``, ``note``, ``report_id``, ``reported_at``, ``open``) and this
one carried **thirty-one**, including ``gold_sql`` -- the held-out benchmark's reference answer, on a
route that authenticates nothing. Conformance rule V12 exists to keep a held-out question out of the
corpus; serving the answer over HTTP was the same contamination channel with the gate bypassed, and
it arrived on the branch that added V12's enforcement.

What ships now is a **narrowed projection built from an allowlist**
(:data:`PUBLIC_OBSERVATION_FIELDS`, :data:`PUBLIC_PATCH_FIELDS`,
:data:`PUBLIC_TRANSITION_FIELDS`). An allowlist because the alternative is what produced the defect:
the projection enumerated the dataclass, so a field added to ``Observation`` reached the wire by the
next deploy. Withheld from an unauthenticated caller: the gold statement and its two fingerprints, a
patch's ``was``/``becomes``/``rationale``/``base_corpus_content_hash``, and a transition's
``detail``.

**What is still disclosed, and this part genuinely is unchanged.** Nothing here authenticates;
reaching the port is sufficient (``docs/enterprise-fork.md``). So the GET hands any caller every open
question with the SQL the engine generated, and the POST lets any caller file against any turn. That
was accepted on the grounds that ``/audit/turns`` already discloses every thread's SQL to the same
caller, and those grounds have not changed. Under a real ``AccessPolicy`` both verbs still owe the
withholding the tools apply.
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


#: Observation fields an **unauthenticated** caller may read, and nothing else.
#:
#: An allowlist because the alternative produced the defect: this projection enumerated the
#: dataclass, so a field added to :class:`Observation` reached an unauthenticated route by the next
#: deploy. ``gold_sql`` arrived that way. Adding a name here is a disclosure decision somebody has
#: to make on purpose; leaving one out is the safe default.
#:
#: ``question``, ``generated_sql``, ``licensed`` and ``missing_tables`` are here deliberately: they
#: are what makes a queue row reviewable at all, and ``/audit/turns/{id}/trace`` already discloses a
#: turn's SQL to the same caller. That position predates this surface and is unchanged.
PUBLIC_OBSERVATION_FIELDS: frozenset[str] = frozenset(
    {
        "observation_id",
        "filed_at",
        "source",
        "kind",
        "category",
        "state",
        "open",
        "note",
        "decline_reason",
        "duplicate_of",
        "blocked_note",
        "turn_id",
        "thread_id",
        "question",
        "outcome",
        "refused_by",
        "generated_sql",
        "licensed",
        "schemas",
        "missing_tables",
        "quality_flags",
        "arm",
        "question_id",
        "db_id",
        "corpus_content_hash",
        "question_is_held_out",
    }
)

#: Patch fields an unauthenticated caller may read. **The fact that a patch exists is not the
#: secret; its content is.** `was`, `becomes` and `rationale` are the steward's working draft, and
#: `base_corpus_content_hash` is one of the two values the landing check compares -- publishing it
#: tells a caller which tree the change was authored against, which is provenance about work in
#: progress rather than about a turn that happened.
PUBLIC_PATCH_FIELDS: frozenset[str] = frozenset(
    {
        "patch_id",
        "created_at",
        "author",
        "intent",
        "state",
        "namespace",
        "asset_type",
        "asset_id",
        "field_path",
        "ladder",
        "withdrawn_reason",
        "observations",
        "derived_state",
    }
)

#: Transition fields an unauthenticated caller may read. ``detail`` is whatever the steward typed --
#: a decline reason in prose, a withdraw note -- so the *shape* of the append-only trail is public
#: and the sentences are not.
PUBLIC_TRANSITION_FIELDS: frozenset[str] = frozenset(
    {"at", "entity", "entity_id", "from_state", "to_state", "moved_by"}
)


def _narrowed(row: dict[str, Any], allowed: frozenset[str], *, for_steward: bool) -> dict[str, Any]:
    """``row`` with only ``allowed`` keys, unless the caller is the steward.

    One function for all three shapes, because "publish only what is on the list" is one rule and a
    second copy of it is how one shape comes to leak while the others do not.
    """
    if for_steward:
        return row
    return {name: value for name, value in row.items() if name in allowed}


def make_feedback_router(
    pending: Any, turn_log: Any, store: FeedbackStore, *, for_steward: bool = False
) -> APIRouter:
    """Routes over the interrupt reader, the turn log, and the feedback store.

    ``pending`` exposes ``pending(limit=, offset=)`` and ``PENDING_FIELDS``; ``turn_log`` exposes
    ``get_turn``. The store is passed rather than constructed here so a test drives a ``tmp_path``
    one and the composition root owns the path — the same reason ``accept_node`` takes its session.

    ``for_steward`` widens the projection to every field, and it is **the same decision that mounts
    the admin router** — read once in ``api/routes.py``, which is the module allowed to read the
    environment, and threaded in. Two independent reads of one switch is how they come to disagree,
    and a disagreement here means `GET /patches` 404s while `GET /observations/{id}` serves the same
    patch content, which is what shipped.
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

        **``source`` is ``reader`` unless the steward switch is on.** It was ``operator``
        unconditionally, on a route that authenticates nothing, and ``operator`` is a *capability* --
        "can read the corpus and name an asset". The consequence was not cosmetic:
        ``validate.py`` waves through :data:`~governed_bi.feedback.events.OPERATOR_ONLY_CATEGORIES`
        for any ``operator``, so ``column_excluded``, ``column_suspect`` and ``reusable_fact`` -- the
        three that name a column -- were filable by anybody who could reach the port, and the gate
        could not fire on any row in the store. ``for_steward`` is the switch that already decides
        this (it mounts the steward's verbs, and ``make_admin_router`` says what it means), so the
        source follows it rather than a second control invented here.
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
            source=Source.operator if for_steward else Source.reader,
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
        return {
            "ok": True,
            "observation": _wire_observation_detail(
                store, observation_id, for_steward=for_steward
            ),
        }

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
        return {
            "ok": True,
            "observation": _wire_observation_detail(
                store, observation_id, for_steward=for_steward
            ),
        }

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
                        "observations": [
                            _wire_observation(o, for_steward=for_steward)
                            for o in c.observations
                        ],
                    }
                    for c in grouped
                ],
                "meta": _queue_meta(page, limit, offset, grouped=len(grouped)),
            }
        return {
            "rows": [_wire_observation(o, for_steward=for_steward) for o in page.rows],
            "meta": _queue_meta(page, limit, offset),
        }

    @router.get("/observations/{observation_id}")
    def get_observation(observation_id: str) -> dict[str, Any]:
        """One observation, its patches, and its transition trail.

        **No landing state.** ``derived_state`` is on every patch row and is ``null`` on all of
        them, for the reason ``_wire_observation_detail`` gives: answering "did this land" needs the
        loaded corpus and a request handler does not have it. Landing is CLI-only --
        ``tools/check_landed.py`` is the one reader of ``lifecycle.derived_state``. This docstring
        promised the derived state for as long as the code returned ``None``.
        """
        row = _wire_observation_detail(store, observation_id, for_steward=for_steward)
        if row is None:
            raise HTTPException(status_code=404, detail="observation not found")
        return row

    return router


# ── projections ───────────────────────────────────────────────────────────────


def _wire_observation(obs: Observation, *, for_steward: bool = False) -> dict[str, Any]:
    """One observation on the wire. ``open`` is **computed**, never read from a column.

    Narrowed to :data:`PUBLIC_OBSERVATION_FIELDS` unless the caller is the steward. The three
    fields that narrowing removes -- ``gold_sql``, ``gold_fingerprint``, ``pred_fingerprint`` --
    are the held-out reference answer, which conformance rule V12 exists to keep out of the corpus
    and which this route served in full to anybody who could reach the port.
    """
    return _narrowed(
        {
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
        },
        PUBLIC_OBSERVATION_FIELDS,
        for_steward=for_steward,
    )


def _wire_observation_detail(
    store: FeedbackStore, observation_id: str, *, for_steward: bool = False
) -> dict[str, Any] | None:
    obs = store.get(observation_id)
    if obs is None:
        return None
    return {
        **_wire_observation(obs, for_steward=for_steward),
        # `derived_state` is null on every row here. It is derived and stored nowhere, and this
        # route has no session, so it cannot read the corpus; answering "did this land" from an
        # empty corpus view would say `superseded` about everything. The key is present rather than
        # absent so a client is not left telling "no answer" from "no such key".
        #
        # **Nothing composes the corpus half.** This comment used to say the review surface did;
        # it does not -- `ui/components/review/handoff-panel.tsx` reads this field straight off the
        # payload and renders a badge only when it is truthy, so the badge is permanently absent.
        # Landing state is CLI-only, and `tools/check_landed.py` is its one reader.
        "patches": [
            {**_wire_patch(store, patch, for_steward=for_steward), "derived_state": None}
            for patch in store.patches_of(observation_id)
        ],
        "history": [
            _narrowed(dict(row), PUBLIC_TRANSITION_FIELDS, for_steward=for_steward)
            for row in store.history(observation_id)
        ],
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
        # `for_steward=True` unconditionally: this router only exists when the switch is on, so
        # there is no narrower caller to serve. Reading the switch again here would be the second
        # read the one-decision rule exists to prevent.
        return {
            "ok": True,
            "observation": _wire_observation_detail(store, observation_id, for_steward=True),
        }

    @router.post("/patches", status_code=201)
    def draft_patch(body: dict[str, Any]) -> dict[str, Any]:
        """Draft a patch against one asset field.

        The handler builds the ``Patch`` and hands it to the store; every rule about what a patch
        may say lives in ``feedback/validate.py``, so a field this endpoint forgot to police is
        still refused. It does **not** write to the corpus and cannot: the only write is a human's
        ``git commit``, and this row is what produces the diff for it.

        **The response carries what the draft did to the observations it answers.** Drafting is the
        producer of ``addressed`` (``store.draft``), and the move is per observation because the
        edge is: a row still ``open`` has no ``-> addressed`` edge and stays where it is. Both lists
        go out, so a client learns the half that did not happen from the answer rather than by
        re-fetching each row and comparing.
        """
        try:
            patch = _patch_from_body(body or {})
        except _BadRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            observations = _string_list((body or {}).get("observations"), "observations")
        except _BadRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for observation_id in observations:
            if store.get(observation_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"no observation {observation_id!r}"
                )
        try:
            drafted = store.draft(patch, observations=observations)
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {
            "ok": True,
            "patch": _wire_patch(store, store.get_patch(drafted.patch_id), for_steward=True),
            "addressed": list(drafted.addressed),
            "not_addressed": [
                {
                    "observation_id": unmoved.observation_id,
                    "state": unmoved.state.value,
                    "why": unmoved.why,
                }
                for unmoved in drafted.not_addressed
            ],
        }

    @router.post("/patches/{patch_id}/withdraw")
    def withdraw(patch_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Abandon a patch, with a reason. Legal from ``draft`` and from ``exported`` both.

        From ``exported`` too, because a bundle that went out and was rejected in review is the
        common case, and leaving it `exported` forever is the unclosable row this design exists to
        remove. What the corpus does with a bundle already applied is not this store's business --
        ``tools/check_landed.py`` reads that from the corpus.

        **The response carries what the withdrawal did to the observations the patch answered**, the
        same way ``POST /patches`` carries what drafting did. Withdrawing the last live patch returns
        a row from ``addressed`` to ``triaged``: it is the mirror of drafting, and the store does the
        move so nothing that reaches it can withdraw a patch and leave the queue claiming the row was
        answered. Both lists go out, so a client learns the half that did not move -- a row with a
        second draft still open, a row already declined -- from the answer rather than by re-fetching
        each one.
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
            moved = store.move_patch(
                patch_id, to=PatchState.withdrawn, withdrawn_reason=reason, detail=reason
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="patch not found") from exc
        except TransitionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Rejected as exc:
            raise HTTPException(status_code=422, detail=list(exc.faults)) from exc
        return {
            "ok": True,
            "patch": _wire_patch(store, moved.patch, for_steward=True),
            "reopened": list(moved.reopened),
            "not_reopened": [
                {
                    "observation_id": unmoved.observation_id,
                    "state": unmoved.state.value,
                    "why": unmoved.why,
                }
                for unmoved in moved.not_reopened
            ],
        }

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
            "patches": [_wire_patch(store, p, for_steward=True) for p in page.rows],
            "meta": _queue_meta(page, limit, offset),
        }

    return router


class _BadRequest(ValueError):
    """A body this surface can reject without asking the store."""


def _string_list(value: Any, field: str) -> list[str]:
    """``value`` as a list of ids, or :class:`_BadRequest` naming what arrived instead.

    This was ``[str(o) for o in (value or [])]``, which fails two ways and neither of them says what
    the caller did. A number is not iterable, so the comprehension raised ``TypeError`` out of the
    route and the caller got a **500** -- a claim about the engine, which sends the operator to the
    wrong half. A *string* is iterable, so ``"obs-nope"`` became ``o``, ``b``, ``s``, ... and the
    route answered ``404 no observation 'o'``: a true status code carrying a message with no
    relationship to the request, which costs more than the crash because nothing about it looks
    wrong.
    """
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise _BadRequest(
            f"{field} must be a list of observation ids, and this request sent "
            f"{type(value).__name__}. A single id goes in a list of one."
        )
    return [str(item) for item in value]


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


def _wire_patch(
    store: FeedbackStore, patch: Patch | None, *, for_steward: bool = False
) -> dict[str, Any]:
    """A patch on the wire. ``ladder`` goes out as the store holds it, because the review surface
    renders whatever tiers ran rather than a fixed three.

    Narrowed to :data:`PUBLIC_PATCH_FIELDS` unless the caller is the steward: the fact that a patch
    exists is not the secret, its text is. `GET /patches` 404s with the switch off and this
    projection served `was`/`becomes`/`rationale` through the observation route regardless.
    """
    if patch is None:  # pragma: no cover - callers pass a row they just wrote
        return {}
    return _narrowed(
        {
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
        },
        PUBLIC_PATCH_FIELDS,
        for_steward=for_steward,
    )
