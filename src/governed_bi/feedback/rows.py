"""How an ``Observation`` and a ``Patch`` are spelled in SQLite: the DDL and both mappers.

``store.py`` owns the connection, the transactions and the statements. This module owns the
*spelling* — and the two were one file until it crossed the 1,000-line cap.

**Why the seam is here rather than between reads and writes.** The obvious cut lifts the eight read
methods out. It also separates :func:`observation_row` from :func:`observation_from`, and those are
the pair that must change together: add a column and the DDL, the writer's mapper and the reader's
mapper all move. ``tests/feedback/test_the_store_keeps_the_promises_in_its_docstrings.py`` exists
because "three mappers each dropping one field survived the suite", and names the four places a
field lives — the dataclass, the DDL, and the two mappers. A read/write cut runs a module boundary
through the middle of that. Three of the four are now adjacent; the fourth is
:mod:`governed_bi.feedback.events`.

It would also not have been acyclic. A writer reads a row before updating it -- ``move`` needs
:func:`observation_from` inside its own transaction -- so ``store`` would have imported ``queries``
and ``queries`` would have grown the mappers regardless. This module imports the shapes it
translates and the standard library, and nothing else of ours;
``tests/feedback/test_the_storage_spelling_is_its_own_module.py`` is what keeps that true.

Nothing here touches a connection. Every function is a pure translation, which is why the round-trip
test can drive them without a database.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields as dataclass_fields
from typing import Any, Mapping, TypeVar

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
from governed_bi.register.assets import AssetType

#: Bumped when the DDL changes in a way an existing file cannot be read with.
SCHEMA_VERSION = 1


SCHEMA = """
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
JSON_TUPLES: Mapping[str, str] = {
    "licensed": "licensed_json",
    "schemas": "schemas_json",
    "missing_tables": "missing_tables_json",
    "quality_flags": "quality_flags_json",
}


# ── row mapping ───────────────────────────────────────────────────────────────


def observation_row(obs: Observation) -> dict[str, Any]:
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
    for attr, column in JSON_TUPLES.items():
        row[column] = json.dumps(list(getattr(obs, attr)))
    return row


def observation_from(row: sqlite3.Row) -> Observation:
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


def patch_row(patch: Patch) -> dict[str, Any]:
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


def patch_from(row: sqlite3.Row) -> Patch:
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


def replace_row(row: _Row, **changes: Any) -> _Row:
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
