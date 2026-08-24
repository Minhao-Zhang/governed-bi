"""The return path's store: observations, patches, and every transition between their states.

One SQLite file, stdlib ``sqlite3``, **synchronous**. Each of those is a decision.

*A file this repository owns and migrates*, rather than the accumulating checkpoint channel that
held reader notes before it. ADR 0014 rejected a hand-rolled SQLite table for the *turn record* and
was right about it -- ``ACCUMULATING`` already existed, so a channel on a durable checkpointer
answered that requirement exactly. No native primitive answers this one. The requirement is a
**mutable row, queried across threads on fields the checkpoint is not indexed on**, and 0014 itself
rejected the LangGraph Store for the audit index because ``BaseStore.search()`` has no sort
parameter. A turn happens once; an observation is edited four times.

*Synchronous, and not ``aiosqlite``.* Every loop-binding hazard ``serve/checkpointer.py``
documents -- a saver's lock poisoned across ``asyncio.run`` calls, a non-daemon worker blocking
interpreter exit, ``SqliteSaver`` raising on every async method -- exists because the store shares
the graph's loop. This one is written and read from sync handlers and from ``tools/``, and never
touches it. That is also what lets ``api/raised_write.py``'s ~250 lines of loop-hopping go.

*Greppable*, which is the property 0014 lists as the thing it gave up:
``sqlite3 runs/feedback.sqlite "select ..."`` answers a question about the queue in one line.

**The transition table is append-only and is the audit trail.** A state change writes the new state
onto the row *and* a row into ``transition``, in one SQLite transaction, and every ``transition``
row carries the actor that moved it -- the invariant :mod:`.lifecycle` exists to make
unrepresentable is enforced here at the point of writing.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from governed_bi.feedback.events import (
    Category,
    DeclineReason,
    Drafted,
    Observation,
    ObservationState,
    Patch,
    PatchState,
    Unmoved,
)
from governed_bi.feedback.lifecycle import (
    Actor,
    TransitionRefused,
    patch_transition_for,
    transition_for,
)
from governed_bi.feedback.rows import (
    SCHEMA,
    SCHEMA_VERSION,
    observation_from,
    observation_row,
    patch_from,
    patch_row,
    replace_row,
)
from governed_bi.feedback.validate import NOTE_MAX_CHARS, faults_with
from governed_bi.paths import assert_not_a_warehouse

__all__ = [
    "SCHEMA_VERSION",
    "FeedbackStore",
    "Page",
    "Rejected",
    "mint_observation_id",
    "mint_patch_id",
    "utc_now",
]

#: Bumped when a migration is added. There is one version and no migration yet; the column exists
#: so the first one has somewhere to read from, which is cheaper than adding it later to a file
#: that already has rows.


class Rejected(ValueError):
    """The store refused a write. Carries every fault, not the first one.

    Raised rather than returned because a caller that ignores a return value writes a row that
    breaks the vocabulary, and there is no useful partial success: an observation is one row.
    """

    def __init__(self, what: str, faults: Sequence[str]) -> None:
        self.faults = tuple(faults)
        super().__init__(f"{what} was refused:\n  - " + "\n  - ".join(faults))


@dataclass(frozen=True, slots=True)
class Page:
    """One page of rows plus what the caller cannot see from the rows alone.

    ``truncated`` is load-bearing (ADR 0009): a caller that cannot tell a full page from the end
    of the queue will stop at the first page and believe it saw everything.
    """

    rows: tuple[Any, ...]
    total: int
    truncated: bool


def utc_now() -> str:
    """ISO-8601 UTC to the second. Seconds because nothing here is ordered finer than a person."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mint_observation_id() -> str:
    """``obs-{yyyymmddThhmmssZ}-{8hex}``. Sortable by eye, unique without coordination."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"obs-{stamp}-{secrets.token_hex(4)}"


def mint_patch_id() -> str:
    """``pat-{yyyymmddThhmmssZ}-{6hex}``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pat-{stamp}-{secrets.token_hex(3)}"





class FeedbackStore:
    """Every read and write of the return path's own state.

    The constructor migrates. There is no ``close``: the connection is per-call, because a
    long-lived handle owned by a module is the thing that makes a sync store awkward to use from
    both a request handler and a script.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(assert_not_a_warehouse(str(path), source="feedback store path"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ── connection ────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction. A state change and its audit row land together or not at all."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._conn() as conn:
            # WAL outside the transaction: it is a database-level property, not a change.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path} is at schema version {row['version']} and this code knows "
                    f"{SCHEMA_VERSION}. A newer store read by older code is how a column nobody "
                    "here writes gets silently dropped; refusing instead."
                )

    # ── writes ────────────────────────────────────────────────────────────────

    def file(self, obs: Observation) -> str:
        """Insert one observation. Returns its id, or the existing id on a known ``external_key``.

        An importer re-reading the same artifact is **idempotent**, and that is what the unique
        ``external_key`` buys: the second read finds the row and returns its id rather than filing
        a second observation about the same failure. A *person* filing the same complaint twice is
        two complaints and carries no key, so the two cases do not have to share a rule.
        """
        faults = faults_with(obs)
        if faults:
            raise Rejected(f"observation {obs.observation_id or '(no id)'}", faults)
        if obs.state is not ObservationState.open:
            raise Rejected(
                f"observation {obs.observation_id}",
                [f"filed in state {obs.state.value}; the only opening edge is to open"],
            )

        row = observation_row(obs)
        with self._tx() as conn:
            if obs.external_key:
                seen = conn.execute(
                    "SELECT observation_id FROM observation WHERE external_key = ?",
                    (obs.external_key,),
                ).fetchone()
                if seen is not None:
                    return str(seen["observation_id"])
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            conn.execute(f"INSERT INTO observation ({cols}) VALUES ({marks})", tuple(row.values()))
            _record_transition(
                conn,
                entity="observation",
                entity_id=obs.observation_id,
                from_state=None,
                to_state=obs.state.value,
                moved_by=transition_for(None, obs.state).moved_by,
                detail=f"source={obs.source.value}",
            )
        return obs.observation_id

    def move(
        self,
        observation_id: str,
        *,
        to: ObservationState,
        moved_by: Actor | None = None,
        detail: str = "",
        decline_reason: DeclineReason | None = None,
        duplicate_of: str | None = None,
        blocked_note: str = "",
    ) -> Observation:
        """Move an observation to ``to``, writing the row and its audit line together.

        ``moved_by`` defaults to the actor the transition table declares, which is the answer in
        every case a caller has today. It is an argument at all so a future second identity can
        record *which* steward, without the table having to become a policy.

        The new row is validated before it is written, so the fields a state makes mandatory --
        ``decline_reason`` on a decline, ``blocked_note`` on a block -- are enforced by the same
        rules that enforce them at filing rather than by a second copy here.

        **The read, the check and the write are one transaction, and the write is guarded on the
        state the check was made against.** They were not, and two stewards moving one row at the
        same moment both won: reproduced at 29 of 40 attempts, leaving the row on an edge
        ``TRANSITIONS`` does not declare, with ``decline_reason`` nulled by the *other* writer's
        target, and two audit lines both claiming the same ``from_state`` -- so the append-only
        trail stopped chaining. ``BEGIN IMMEDIATE`` serialises it; the ``AND state = ?`` guard is
        the second lock, so a future refactor that moves the read back out fails loudly instead of
        corrupting a row quietly.
        """
        with self._tx() as conn:
            current = _observation_or_none(conn, observation_id)
            if current is None:
                raise KeyError(f"no observation {observation_id!r}")
            edge = transition_for(current.state, to)

            proposed = replace_row(
                current,
                state=to,
                decline_reason=decline_reason if to is ObservationState.declined else None,
                duplicate_of=duplicate_of if duplicate_of is not None else current.duplicate_of,
                blocked_note=blocked_note or current.blocked_note,
                triaged_at=current.triaged_at or utc_now(),
            )
            faults = list(faults_with(proposed))
            # `validate.py` catches the two `duplicate_of` cases it can see without a store -- no id
            # on a `duplicate`, and an id naming the row itself -- and cannot catch the third,
            # because knowing whether a row exists means asking. Left to the constraint, the answer
            # was `sqlite3.IntegrityError` raising out of the route as a **500**, which tells the
            # operator the engine broke. Asked here, it is one more fault in the same list the
            # caller already maps to 422.
            if (
                proposed.duplicate_of
                and proposed.duplicate_of != current.duplicate_of
                and _observation_or_none(conn, proposed.duplicate_of) is None
            ):
                faults.append(
                    f"duplicate_of names {proposed.duplicate_of!r}, which is not an observation in "
                    "this store"
                )
            faults.extend(_edge_faults(conn, observation_id, current.state, to))
            if faults:
                raise Rejected(f"observation {observation_id} -> {to.value}", faults)

            changed = conn.execute(
                "UPDATE observation SET state = ?, decline_reason = ?, duplicate_of = ?, "
                "blocked_note = ?, triaged_at = ? WHERE observation_id = ? AND state = ?",
                (
                    proposed.state.value,
                    proposed.decline_reason.value if proposed.decline_reason else None,
                    proposed.duplicate_of,
                    proposed.blocked_note,
                    proposed.triaged_at,
                    observation_id,
                    current.state.value,
                ),
            ).rowcount
            if changed != 1:
                raise TransitionRefused(
                    f"observation {observation_id} was {current.state.value} when this move was "
                    f"checked and is not any more, so {current.state.value} -> {to.value} is not "
                    "the move that would land. Read it again and decide against what it says now."
                )
            if to is ObservationState.duplicate and proposed.duplicate_of:
                # The second half of this edge's `requires`, and it names its own consequence:
                # "otherwise a landing counts one affected observation instead of two". The row
                # naming half was a refusal; this half is an action, so refusing would be the wrong
                # shape -- the steward has said these are the same complaint and the patch set is
                # what makes the landing count both.
                conn.executemany(
                    "INSERT OR IGNORE INTO observation_patch (patch_id, observation_id) "
                    "VALUES (?, ?)",
                    [
                        (row["patch_id"], observation_id)
                        for row in conn.execute(
                            "SELECT patch_id FROM observation_patch WHERE observation_id = ?",
                            (proposed.duplicate_of,),
                        ).fetchall()
                    ],
                )
            _record_transition(
                conn,
                entity="observation",
                entity_id=observation_id,
                from_state=current.state.value,
                to_state=to.value,
                moved_by=moved_by or edge.moved_by,
                detail=detail,
            )
        moved = self.get(observation_id)
        assert moved is not None  # noqa: S101 - just written in this transaction
        return moved

    def draft(self, patch: Patch, *, observations: Sequence[str]) -> Drafted:
        """Insert a patch, attach it to the observations it answers, and move the ones it addresses.

        ``observations`` may be empty and that is not an error: a patch drafted from a corpus audit
        rather than from a failure answers nobody, and refusing it would push that work outside the
        store where nothing records it.

        **This is the only producer of ``ObservationState.addressed``** (see the member's own note
        for what its absence cost).

        **The move is per observation, because the edge is.** ``-> addressed`` exists from
        ``triaged`` and ``blocked_on_a_person`` and nowhere else, so a row still ``open`` cannot
        take it. Refusing the whole draft over that would block the steward's actual sequence --
        read the queue, draft the change -- and skipping it silently is the defect family this
        module keeps removing. So it is neither: :class:`Drafted` names every row that moved and
        every row that did not, with its state and the reason.

        The moves run **after** the patch commits and each goes through :meth:`move`, so every
        guard that method documents applies. A move the table or a racing writer refuses lands in
        ``not_addressed`` rather than rolling back a patch that is correct.
        """
        faults = faults_with(patch)
        if faults:
            raise Rejected(f"patch {patch.patch_id or '(no id)'}", faults)

        # Deduplicated, order kept: the same id twice is one attachment, and moving it twice would
        # report the second copy as a refused move against a row this call just moved.
        wanted = list(dict.fromkeys(str(o) for o in observations))
        row = patch_row(patch)
        with self._tx() as conn:
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            conn.execute(f"INSERT INTO patch ({cols}) VALUES ({marks})", tuple(row.values()))
            for observation_id in wanted:
                conn.execute(
                    "INSERT OR IGNORE INTO observation_patch (observation_id, patch_id) "
                    "VALUES (?, ?)",
                    (observation_id, patch.patch_id),
                )
            _record_transition(
                conn,
                entity="patch",
                entity_id=patch.patch_id,
                from_state=None,
                to_state=patch.state.value,
                moved_by=Actor.steward,
                detail=f"intent={patch.intent.value}, observations={len(wanted)}",
            )

        addressed: list[str] = []
        not_addressed: list[Unmoved] = []
        for observation_id in wanted:
            current = self.get(observation_id)
            if current is None:  # pragma: no cover - the join insert above enforces this
                continue
            try:
                self.move(
                    observation_id,
                    to=ObservationState.addressed,
                    detail=f"addressed by {patch.patch_id}",
                )
            except (TransitionRefused, Rejected) as refusal:
                not_addressed.append(
                    Unmoved(
                        observation_id=observation_id,
                        state=current.state,
                        why=str(refusal),
                    )
                )
            else:
                addressed.append(observation_id)
        return Drafted(
            patch_id=patch.patch_id,
            addressed=tuple(addressed),
            not_addressed=tuple(not_addressed),
        )

    def amend_note(self, observation_id: str, note: str) -> None:
        """Replace the note on an untriaged observation. Refuses once somebody has looked.

        The one mutable field outside the lifecycle, and it earns that by being the field the
        design asks for **after** filing succeeds: a note that gates submission is a note nobody
        writes. No transition row, because nothing about the row's *state* changed — and the
        append-only audit trail is about who moved it, not about who typed into it.
        """
        note = note.strip()
        faults = [
            f"note must be at most {NOTE_MAX_CHARS} characters, not {len(note)}"
        ] if len(note) > NOTE_MAX_CHARS else []
        if faults:
            raise Rejected(f"observation {observation_id}", faults)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT state FROM observation WHERE observation_id = ?", (observation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no observation {observation_id!r}")
            if row["state"] != ObservationState.open.value:
                raise Rejected(
                    f"observation {observation_id}",
                    [
                        f"is {row['state']}; a note can only be amended while nobody has triaged "
                        "it, because a reviewer reading a row whose text changes underneath them "
                        "is worse than a second observation"
                    ],
                )
            conn.execute(
                "UPDATE observation SET note = ? WHERE observation_id = ?", (note, observation_id)
            )

    def move_patch(
        self,
        patch_id: str,
        *,
        to: PatchState,
        moved_by: Actor | None = None,
        detail: str = "",
        withdrawn_reason: str = "",
        expected_corpus_content_hash: str | None = None,
    ) -> Patch:
        """Move a patch, through the same table that moves an observation.

        ``exported`` is set by ``tools/export_bundle.py`` after the bundle is on disk and not
        before: a patch that says a bundle exists when the write failed is a patch the steward
        stops looking at. ``withdrawn`` needs a reason for the same rule the observation's decline
        follows -- a terminal state whose "why" lives only in somebody's memory is a row that gets
        re-drafted from scratch six weeks later.

        Read, check and write are one transaction guarded on the checked state, for the reason
        :meth:`move` gives at length: without it two concurrent moves both win. Here the damage is
        that ``exported`` and ``withdrawn`` are both reachable from ``draft`` while
        ``withdrawn -> exported`` is not declared -- so a lost update can un-withdraw a patch.
        """
        with self._tx() as conn:
            current = _patch_or_none(conn, patch_id)
            if current is None:
                raise KeyError(f"no patch {patch_id!r}")
            edge = patch_transition_for(current.state, to)

            proposed = replace_row(
                current,
                state=to,
                withdrawn_reason=withdrawn_reason or current.withdrawn_reason,
                expected_corpus_content_hash=(
                    expected_corpus_content_hash
                    if expected_corpus_content_hash is not None
                    else current.expected_corpus_content_hash
                ),
            )
            faults = list(faults_with(proposed))
            if to is PatchState.exported and not proposed.expected_corpus_content_hash:
                faults.append(
                    "expected_corpus_content_hash is unset, and this edge requires it -- a bundle "
                    "was written or the state did not move. Without it `derived_state` can never "
                    "answer better than `landed_matched`, which is also true of a corpus where "
                    "three other bundles landed."
                )
            if faults:
                raise Rejected(f"patch {patch_id} -> {to.value}", faults)

            changed = conn.execute(
                "UPDATE patch SET state = ?, withdrawn_reason = ?, "
                "expected_corpus_content_hash = ? WHERE patch_id = ? AND state = ?",
                (
                    proposed.state.value,
                    proposed.withdrawn_reason,
                    proposed.expected_corpus_content_hash,
                    patch_id,
                    current.state.value,
                ),
            ).rowcount
            if changed != 1:
                raise TransitionRefused(
                    f"patch {patch_id} was {current.state.value} when this move was checked and "
                    f"is not any more, so {current.state.value} -> {to.value} is not the move "
                    "that would land."
                )
            _record_transition(
                conn,
                entity="patch",
                entity_id=patch_id,
                from_state=current.state.value,
                to_state=to.value,
                moved_by=moved_by or edge.moved_by,
                detail=detail or withdrawn_reason,
            )
        moved = self.get_patch(patch_id)
        assert moved is not None  # noqa: S101 - just written in this transaction
        return moved

    def record_ladder(self, patch_id: str, tier: str, result: Mapping[str, Any]) -> None:
        """Merge one tier's result into a patch's ladder. Later runs of a tier replace earlier ones.

        Replace and not append, because a tier's answer is about the patch as it stands: keeping
        both would make "did T1 pass" a question with two answers and no rule for choosing.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT ladder_json FROM patch WHERE patch_id = ?", (patch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no patch {patch_id!r}")
            ladder = dict(json.loads(row["ladder_json"]))
            ladder[tier] = dict(result)
            conn.execute(
                "UPDATE patch SET ladder_json = ? WHERE patch_id = ?",
                (json.dumps(ladder, sort_keys=True), patch_id),
            )

    # ── reads ─────────────────────────────────────────────────────────────────

    # ── reads on a caller's connection ────────────────────────────────────────
    #
    # `move`/`move_patch` need to read INSIDE their own transaction, and `self.get` opens a fresh
    # connection -- which is exactly the window that let two writers both win. These two are the
    # same query against a connection the caller already holds.

    def get(self, observation_id: str) -> Observation | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM observation WHERE observation_id = ?", (observation_id,)
            ).fetchone()
        return observation_from(row) if row is not None else None

    def get_patch(self, patch_id: str) -> Patch | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM patch WHERE patch_id = ?", (patch_id,)).fetchone()
        return patch_from(row) if row is not None else None

    def queue(
        self,
        *,
        states: Sequence[ObservationState] | None = None,
        category: Category | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page:
        """Observations, **oldest first**, with the total behind the page.

        Oldest-first, unlike ``/audit``: a log is read newest-first because the newest event is
        the one you came for, and a queue is read oldest-first because the row that has waited
        longest is the one to act on. Sorting by cluster size instead would make the long tail
        permanently invisible.

        The tiebreak is ``rowid`` and not the id. ``filed_at`` is to the second and an id carries a
        random suffix, so two rows filed in the same second -- which is what an importer does 73
        times -- would order by that suffix, differently on every run. ``rowid`` is insertion
        order, which is what "oldest first" means when the clock cannot tell them apart.
        """
        where: list[str] = ["1 = 1"]
        params: list[object] = []
        if states:
            where.append(f"state IN ({', '.join('?' for _ in states)})")
            params.extend(s.value for s in states)
        if category is not None:
            where.append("category = ?")
            params.append(category.value)
        clause = " AND ".join(where)
        with self._conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT count(*) AS n FROM observation WHERE {clause}", tuple(params)
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"SELECT * FROM observation WHERE {clause} ORDER BY filed_at, rowid "
                "LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return Page(
            rows=tuple(observation_from(r) for r in rows),
            total=total,
            truncated=offset + len(rows) < total,
        )

    def observations_for_turn(self, turn_id: str) -> tuple[Observation, ...]:
        """Every observation filed about one turn. What ``ix_obs_turn`` exists for.

        The audit trace reads this beside ``clarifications``, and for the same reason that one is
        on the trace: an observation about a turn is evidence about the turn, so a trace without it
        cannot explain why somebody thought the statement above was wrong.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM observation WHERE turn_id = ? ORDER BY filed_at, rowid",
                (str(turn_id),),
            ).fetchall()
        return tuple(observation_from(r) for r in rows)

    def patches(
        self,
        *,
        states: Sequence[PatchState] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page:
        """Patches, newest first, with the total behind the page.

        Newest-first where the observation queue is oldest-first, and the asymmetry is the point:
        an observation queue is work waiting, so the row that has waited longest is next; a patch
        list is work done, so the one just authored is the one being looked for.
        """
        where: list[str] = ["1 = 1"]
        params: list[object] = []
        if states:
            where.append(f"state IN ({', '.join('?' for _ in states)})")
            params.extend(s.value for s in states)
        clause = " AND ".join(where)
        with self._conn() as conn:
            total = int(
                conn.execute(f"SELECT count(*) FROM patch WHERE {clause}", params).fetchone()[0]
            )
            rows = conn.execute(
                f"SELECT * FROM patch WHERE {clause} ORDER BY created_at DESC, rowid DESC "
                f"LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return Page(
            rows=tuple(patch_from(r) for r in rows),
            total=total,
            truncated=offset + len(rows) < total,
        )

    def patches_of(self, observation_id: str) -> tuple[Patch, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.* FROM patch p JOIN observation_patch op ON op.patch_id = p.patch_id "
                "WHERE op.observation_id = ? ORDER BY p.created_at, p.rowid",
                (observation_id,),
            ).fetchall()
        return tuple(patch_from(r) for r in rows)

    def observations_of(self, patch_id: str) -> tuple[Observation, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT o.* FROM observation o JOIN observation_patch op "
                "ON op.observation_id = o.observation_id WHERE op.patch_id = ? "
                "ORDER BY o.filed_at, o.rowid",
                (patch_id,),
            ).fetchall()
        return tuple(observation_from(r) for r in rows)

    def history(self, entity_id: str) -> tuple[dict[str, Any], ...]:
        """Every transition on one entity, oldest first. The audit trail, unfiltered."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT at, entity, entity_id, from_state, to_state, moved_by, detail "
                "FROM transition WHERE entity_id = ? ORDER BY rowid_",
                (entity_id,),
            ).fetchall()
        return tuple(dict(r) for r in rows)

    # There is no `counts_by`. Its docstring said it was "for the import report and the queue's own
    # header" and neither called it -- one grep, one definition, one quotation of its signature in
    # `docs/return-path.md`. Deleted rather than wired: the import report counts what it imported
    # and the queue header already has `Page.total`. It also held the only `f"SELECT {column}"` here.


# ── row mapping ───────────────────────────────────────────────────────────────


def _record_transition(
    conn: sqlite3.Connection,
    *,
    entity: str,
    entity_id: str,
    from_state: str | None,
    to_state: str,
    moved_by: Actor,
    detail: str,
) -> None:
    """Append one audit line. ``moved_by`` is an :class:`Actor`, so it cannot be empty."""
    conn.execute(
        "INSERT INTO transition (at, entity, entity_id, from_state, to_state, moved_by, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (utc_now(), entity, entity_id, from_state, to_state, moved_by.value, detail),
    )












def _edge_faults(
    conn: sqlite3.Connection,
    observation_id: str,
    frm: ObservationState,
    to: ObservationState,
) -> list[str]:
    """The ``requires`` clauses on the observation table that read the *patch* set.

    ``Transition.requires`` says it "is checked by the store rather than here". Measured, four of
    the twelve clauses were checked nowhere, and these two are the pair that needs a query:

    * ``triaged -> addressed`` requires at least one live patch. ``addressed`` is the terminal
      "this was answered" state and it was reachable with nothing behind it, so the queue could
      report work done that has no artifact and ``derived_state`` would answer "did this land"
      about a patch that does not exist.
    * ``addressed -> triaged`` requires every patch withdrawn. Reopening while one is live leaves
      the same row reading as open work and answered work at once.

    **``frm`` is a parameter because a clause belongs to an edge, not to a target state.** Taking
    only ``to``, this refused *every* move to ``triaged`` with a live patch -- two edges too many:
    ``open -> triaged`` is "I am looking at this" and ``blocked_on_a_person -> triaged`` is a block
    clearing, and neither is about withdrawing anything. Reachable as soon as ``draft`` could attach
    a patch to an ``open`` row: drafting from the queue and then clicking "I am looking at this" was
    answered with "reopening requires every patch withdrawn".

    ``(None, open)``'s "the turn exists and has finished" stays prose: this store has no turn log
    and injecting one to satisfy a docstring would put the audit surface inside the writer.
    """
    live = {PatchState.draft.value, PatchState.exported.value}
    states = {
        str(row["state"])
        for row in conn.execute(
            "SELECT p.state AS state FROM patch p "
            "JOIN observation_patch po ON po.patch_id = p.patch_id "
            "WHERE po.observation_id = ?",
            (observation_id,),
        ).fetchall()
    }
    if to is ObservationState.addressed and not (states & live):
        return [
            "addressed requires at least one patch in draft or exported, and this observation has "
            f"{len(states) or 'no'} patch(es), none of them live. `addressed` is the state that "
            "says somebody answered this; with no artifact it says nobody can check."
        ]
    if frm is ObservationState.addressed and to is ObservationState.triaged and (states & live):
        return [
            "reopening requires every patch withdrawn, and "
            f"{len(states & live)} is still draft or exported. Withdraw it first, so the row is "
            "not answered work and open work at the same time."
        ]
    return []


def _observation_or_none(conn: sqlite3.Connection, observation_id: str) -> Observation | None:
    """One observation, on a connection the caller already holds inside a transaction."""
    row = conn.execute(
        "SELECT * FROM observation WHERE observation_id = ?", (observation_id,)
    ).fetchone()
    return observation_from(row) if row is not None else None


def _patch_or_none(conn: sqlite3.Connection, patch_id: str) -> Patch | None:
    """One patch, on a connection the caller already holds inside a transaction."""
    row = conn.execute("SELECT * FROM patch WHERE patch_id = ?", (patch_id,)).fetchone()
    return patch_from(row) if row is not None else None


