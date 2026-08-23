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
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, TypeVar

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
from governed_bi.feedback.lifecycle import (
    Actor,
    TransitionRefused,
    patch_transition_for,
    transition_for,
)
from governed_bi.feedback.validate import NOTE_MAX_CHARS, faults_with
from governed_bi.paths import assert_not_a_warehouse
from governed_bi.register.assets import AssetType

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
SCHEMA_VERSION = 1


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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  observation_id      TEXT PRIMARY KEY,
  filed_at            TEXT NOT NULL,
  source              TEXT NOT NULL,
  kind                TEXT NOT NULL,
  category            TEXT,
  note                TEXT NOT NULL DEFAULT '',
  state               TEXT NOT NULL,
  decline_reason      TEXT,
  duplicate_of        TEXT REFERENCES observation(observation_id),
  blocked_note        TEXT NOT NULL DEFAULT '',
  triaged_at          TEXT,
  turn_id             TEXT,
  thread_id           TEXT,
  question            TEXT NOT NULL DEFAULT '',
  outcome             TEXT,
  refused_by          TEXT,
  generated_sql       TEXT,
  licensed_json       TEXT NOT NULL DEFAULT '[]',
  schemas_json        TEXT NOT NULL DEFAULT '[]',
  missing_tables_json TEXT NOT NULL DEFAULT '[]',
  gold_sql            TEXT,
  gold_fingerprint    TEXT,
  pred_fingerprint    TEXT,
  quality_flags_json  TEXT NOT NULL DEFAULT '[]',
  corpus_content_hash TEXT,
  prompt_set_hash     TEXT,
  git_sha             TEXT,
  arm                 TEXT,
  question_id         TEXT,
  db_id               TEXT,
  external_key        TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_obs_state    ON observation(state, filed_at);
CREATE INDEX IF NOT EXISTS ix_obs_turn     ON observation(turn_id);
CREATE INDEX IF NOT EXISTS ix_obs_category ON observation(category, state);
CREATE INDEX IF NOT EXISTS ix_obs_cluster  ON observation(db_id, category);

CREATE TABLE IF NOT EXISTS patch (
  patch_id                     TEXT PRIMARY KEY,
  created_at                   TEXT NOT NULL,
  author                       TEXT NOT NULL,
  intent                       TEXT NOT NULL,
  state                        TEXT NOT NULL,
  namespace                    TEXT NOT NULL,
  rationale                    TEXT NOT NULL DEFAULT '',
  asset_type                   TEXT,
  asset_id                     TEXT,
  field_path                   TEXT,
  was                          TEXT,
  becomes                      TEXT,
  asset_yaml                   TEXT,
  base_corpus_content_hash     TEXT NOT NULL DEFAULT '',
  expected_corpus_content_hash TEXT,
  ladder_json                  TEXT NOT NULL DEFAULT '{}',
  withdrawn_reason             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_patch_state ON patch(state, created_at);

CREATE TABLE IF NOT EXISTS observation_patch (
  observation_id TEXT NOT NULL REFERENCES observation(observation_id),
  patch_id       TEXT NOT NULL REFERENCES patch(patch_id),
  PRIMARY KEY (observation_id, patch_id)
);

CREATE TABLE IF NOT EXISTS transition (
  rowid_     INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  entity     TEXT NOT NULL,
  entity_id  TEXT NOT NULL,
  from_state TEXT,
  to_state   TEXT NOT NULL,
  moved_by   TEXT NOT NULL,
  detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_transition_entity ON transition(entity, entity_id, rowid_);
"""

#: Observation fields kept as JSON text rather than as columns, and the SQL column each lands in.
#: Tuples of strings, all of them: SQLite has no array type and a junction table for "the tables
#: this turn was allowed to read" would be a join nobody queries across.
_JSON_TUPLES: Mapping[str, str] = {
    "licensed": "licensed_json",
    "schemas": "schemas_json",
    "missing_tables": "missing_tables_json",
    "quality_flags": "quality_flags_json",
}


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
            conn.executescript(_SCHEMA)
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

        row = _observation_row(obs)
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

            proposed = _replace(
                current,
                state=to,
                decline_reason=decline_reason if to is ObservationState.declined else None,
                duplicate_of=duplicate_of if duplicate_of is not None else current.duplicate_of,
                blocked_note=blocked_note or current.blocked_note,
                triaged_at=current.triaged_at or utc_now(),
            )
            faults = faults_with(proposed)
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

    def draft(self, patch: Patch, *, observations: Sequence[str]) -> str:
        """Insert a patch and attach it to the observations it answers.

        ``observations`` may be empty and that is not an error: a patch drafted from a corpus audit
        rather than from a failure answers nobody, and refusing it would push that work outside the
        store where nothing records it.
        """
        faults = faults_with(patch)
        if faults:
            raise Rejected(f"patch {patch.patch_id or '(no id)'}", faults)

        row = _patch_row(patch)
        with self._tx() as conn:
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            conn.execute(f"INSERT INTO patch ({cols}) VALUES ({marks})", tuple(row.values()))
            for observation_id in observations:
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
                detail=f"intent={patch.intent.value}, observations={len(observations)}",
            )
        return patch.patch_id

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

            proposed = _replace(
                current,
                state=to,
                withdrawn_reason=withdrawn_reason or current.withdrawn_reason,
                expected_corpus_content_hash=(
                    expected_corpus_content_hash
                    if expected_corpus_content_hash is not None
                    else current.expected_corpus_content_hash
                ),
            )
            faults = faults_with(proposed)
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
        return _observation_from(row) if row is not None else None

    def get_patch(self, patch_id: str) -> Patch | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM patch WHERE patch_id = ?", (patch_id,)).fetchone()
        return _patch_from(row) if row is not None else None

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
            rows=tuple(_observation_from(r) for r in rows),
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
        return tuple(_observation_from(r) for r in rows)

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
            rows=tuple(_patch_from(r) for r in rows),
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
        return tuple(_patch_from(r) for r in rows)

    def observations_of(self, patch_id: str) -> tuple[Observation, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT o.* FROM observation o JOIN observation_patch op "
                "ON op.observation_id = o.observation_id WHERE op.patch_id = ? "
                "ORDER BY o.filed_at, o.rowid",
                (patch_id,),
            ).fetchall()
        return tuple(_observation_from(r) for r in rows)

    def history(self, entity_id: str) -> tuple[dict[str, Any], ...]:
        """Every transition on one entity, oldest first. The audit trail, unfiltered."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT at, entity, entity_id, from_state, to_state, moved_by, detail "
                "FROM transition WHERE entity_id = ? ORDER BY rowid_",
                (entity_id,),
            ).fetchall()
        return tuple(dict(r) for r in rows)

    def counts_by(self, column: str) -> dict[str, int]:
        """``{value: n}`` over one column. For the import report and the queue's own header.

        The column name is checked against the table's real columns rather than interpolated,
        because this is the one read that takes an identifier from a caller.
        """
        with self._conn() as conn:
            known = {r["name"] for r in conn.execute("PRAGMA table_info(observation)")}
            if column not in known:
                raise KeyError(f"observation has no column {column!r}; it has {sorted(known)}")
            rows = conn.execute(
                f"SELECT {column} AS k, count(*) AS n FROM observation GROUP BY {column}"
            ).fetchall()
        return {("" if r["k"] is None else str(r["k"])): int(r["n"]) for r in rows}


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


def _observation_row(obs: Observation) -> dict[str, Any]:
    row: dict[str, Any] = {
        "observation_id": obs.observation_id,
        "filed_at": obs.filed_at,
        "source": obs.source.value,
        "kind": obs.kind.value,
        "category": obs.category.value if obs.category else None,
        "note": obs.note,
        "state": obs.state.value,
        "decline_reason": obs.decline_reason.value if obs.decline_reason else None,
        "duplicate_of": obs.duplicate_of,
        "blocked_note": obs.blocked_note,
        "triaged_at": obs.triaged_at,
        "turn_id": obs.turn_id,
        "thread_id": obs.thread_id,
        "question": obs.question,
        "outcome": obs.outcome,
        "refused_by": obs.refused_by,
        "generated_sql": obs.generated_sql,
        "gold_sql": obs.gold_sql,
        "gold_fingerprint": obs.gold_fingerprint,
        "pred_fingerprint": obs.pred_fingerprint,
        "corpus_content_hash": obs.corpus_content_hash,
        "prompt_set_hash": obs.prompt_set_hash,
        "git_sha": obs.git_sha,
        "arm": obs.arm,
        "question_id": obs.question_id,
        "db_id": obs.db_id,
        "external_key": obs.external_key,
    }
    for attr, column in _JSON_TUPLES.items():
        row[column] = json.dumps(list(getattr(obs, attr)))
    return row


def _observation_from(row: sqlite3.Row) -> Observation:
    return Observation(
        observation_id=row["observation_id"],
        filed_at=row["filed_at"],
        source=Source(row["source"]),
        kind=Kind(row["kind"]),
        state=ObservationState(row["state"]),
        category=Category(row["category"]) if row["category"] else None,
        note=row["note"],
        turn_id=row["turn_id"],
        thread_id=row["thread_id"],
        question=row["question"],
        outcome=row["outcome"],
        refused_by=row["refused_by"],
        generated_sql=row["generated_sql"],
        licensed=tuple(json.loads(row["licensed_json"])),
        schemas=tuple(json.loads(row["schemas_json"])),
        missing_tables=tuple(json.loads(row["missing_tables_json"])),
        gold_sql=row["gold_sql"],
        gold_fingerprint=row["gold_fingerprint"],
        pred_fingerprint=row["pred_fingerprint"],
        quality_flags=tuple(json.loads(row["quality_flags_json"])),
        corpus_content_hash=row["corpus_content_hash"],
        prompt_set_hash=row["prompt_set_hash"],
        git_sha=row["git_sha"],
        arm=row["arm"],
        question_id=row["question_id"],
        db_id=row["db_id"],
        external_key=row["external_key"],
        decline_reason=DeclineReason(row["decline_reason"]) if row["decline_reason"] else None,
        duplicate_of=row["duplicate_of"],
        blocked_note=row["blocked_note"],
        triaged_at=row["triaged_at"],
    )


def _patch_row(patch: Patch) -> dict[str, Any]:
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
        "asset_yaml": patch.asset_yaml,
        "base_corpus_content_hash": patch.base_corpus_content_hash,
        "expected_corpus_content_hash": patch.expected_corpus_content_hash,
        "ladder_json": json.dumps(dict(patch.ladder), sort_keys=True),
        "withdrawn_reason": patch.withdrawn_reason,
    }


def _patch_from(row: sqlite3.Row) -> Patch:
    return Patch(
        patch_id=row["patch_id"],
        created_at=row["created_at"],
        author=Source(row["author"]),
        intent=PatchIntent(row["intent"]),
        state=PatchState(row["state"]),
        namespace=row["namespace"],
        rationale=row["rationale"],
        asset_type=AssetType(row["asset_type"]) if row["asset_type"] else None,
        asset_id=row["asset_id"],
        field_path=row["field_path"],
        was=row["was"],
        becomes=row["becomes"],
        asset_yaml=row["asset_yaml"],
        base_corpus_content_hash=row["base_corpus_content_hash"],
        expected_corpus_content_hash=row["expected_corpus_content_hash"],
        ladder=json.loads(row["ladder_json"]),
        withdrawn_reason=row["withdrawn_reason"],
    )


_Row = TypeVar("_Row", Observation, Patch)


def _observation_or_none(conn: sqlite3.Connection, observation_id: str) -> Observation | None:
    """One observation, on a connection the caller already holds inside a transaction."""
    row = conn.execute(
        "SELECT * FROM observation WHERE observation_id = ?", (observation_id,)
    ).fetchone()
    return _observation_from(row) if row is not None else None


def _patch_or_none(conn: sqlite3.Connection, patch_id: str) -> Patch | None:
    """One patch, on a connection the caller already holds inside a transaction."""
    row = conn.execute("SELECT * FROM patch WHERE patch_id = ?", (patch_id,)).fetchone()
    return _patch_from(row) if row is not None else None


def _replace(row: _Row, **changes: Any) -> _Row:
    """``dataclasses.replace`` over a slotted frozen class, spelled out.

    Written by hand rather than imported so the field list is checked at call time: a typo in a
    keyword would otherwise be a silently ignored change on some Python versions. One function for
    both rows, because "the same row with one field moved" is one concept and two copies of it is
    how ``Patch`` acquires a field ``Observation``'s copy silently drops.
    """
    known = {f.name for f in dataclass_fields(row)}
    unknown = set(changes) - known
    if unknown:
        raise KeyError(f"{type(row).__name__} has no field(s) {sorted(unknown)}")
    current = {f.name: getattr(row, f.name) for f in dataclass_fields(row)}
    current.update(changes)
    return type(row)(**current)
