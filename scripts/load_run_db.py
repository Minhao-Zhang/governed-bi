"""Load eval run directories into one queryable SQLite database.

Why this exists, and why it is a *loader* rather than a change to the harness.
The serve loop runs up to 20 concurrent workers appending to ``generations.<arm>.jsonl``
and ``stage_events.jsonl``. Append-to-JSONL is contention-free; twenty writers on one
SQLite file is ``database is locked`` two hours into a paid run. So JSONL stays the
*during-run* format and SQLite is the *export* format. That split is settled.

The JSONL is also the durability story, not a theoretical one: during a worktree
cleanup the ``generations.*.jsonl`` for two runs were lost and ``stage_events.jsonl``
alone reconstructed the crash diagnosis. This loader is built to survive exactly that
— :func:`load_run` will happily load a run with stage events and no generations, or
generations and no stage events, and record which was missing rather than refusing.

What you get::

    uv run python scripts/load_run_db.py --db runs/runs.db --discover runs
    uv run python scripts/load_run_db.py --db runs/runs.db --examples
    uv run python scripts/load_run_db.py --db runs/runs.db --sql "SELECT arm, COUNT(*) FROM turns GROUP BY arm"

Three tables — ``runs`` (one row per run directory), ``turns`` (one row per
arm x question), ``events`` (one row per stage event) — plus the *full source row*
as JSON in ``turns.row_json`` / ``events.detail_json`` / ``runs.manifest_json``, so a
field this loader does not promote to a column is still one ``json_extract`` away.
That is deliberate: the emitted field set is **per run**. ``eval/metrics.py``'s
``ROW_FIELDS`` declares 79 fields; the 20260731 opus ladder emitted 73 and the
20260802 oracle run emitted 78. Promoting a fixed column set and keeping the raw row
is the only shape that survives a run that predates a field or postdates one.

Three traps this loader handles explicitly, because each of them silently corrupts a
naive load:

1. **``stage_events.jsonl`` has no sequence number** (as of 20260802 — an explicit
   per-turn sequence is being added to newly-written rows). Rows for one turn are
   contiguous in file order, which was verified over the three largest runs
   (0 non-contiguous turn re-entries out of 4,163 / 5,404 / 4,053 turns), so an
   ordinal *can* be derived. This loader uses the explicit field when a row carries
   one and derives from file order when it does not, and stamps ``events.seq_derived``
   per row plus ``runs.seq_source`` per run so a query never has to guess which it got.
   ``events.file_row`` is the raw 0-based line number and is always trustworthy.
2. **``gold_sql`` is not in the generation rows.** It lives only in
   ``questions.jsonl``, which three of the eleven run directories on disk do not have.
   Gold is left-joined on ``question_id``; where the file is absent ``turns.gold_sql``
   is NULL and the run carries a note saying so.
3. **``stage`` is an open vocabulary.** ``governed_bi.stages.Stage`` is the declared
   set, but the 20260801 runs emit ``sql_normalisation``, which is not a member. The
   loader never validates ``stage`` against the enum; it imports the enum only to
   record, per run, which observed stages are *undeclared* (``runs.notes``). New tool
   stages are landing and a loader that rejected them would be the bug.

Never recursively scan a run directory blind: ``corpus_<arm>/`` holds ~11,768 small
YAML files per ladder run. :func:`discover_runs` prunes those, ``_staging``, and
dotdirs, and stops descending as soon as it finds a ``manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

#: Bumped whenever the table shape changes. Stored in ``PRAGMA user_version``; a
#: mismatch is refused rather than migrated, because these databases are rebuilt from
#: the JSONL in seconds and a half-migrated analysis DB is worse than a missing one.
SCHEMA_VERSION = 1

#: Field names a stage-event row may use for an explicit per-turn ordinal. None of the
#: runs on disk as of 20260802 carry any of them; a concurrent change is adding one.
#: First present wins, and the row is then stamped ``seq_derived = 0``.
SEQ_FIELDS: tuple[str, ...] = ("seq", "turn_seq", "event_seq")

#: Directory names never descended into while discovering runs. ``corpus_*`` is the
#: expensive one (~11,768 files per ladder run).
_PRUNE_PREFIXES: tuple[str, ...] = ("corpus_", "_staging", ".")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_dir                 TEXT PRIMARY KEY,   -- repo-relative POSIX path; joins runs/index.jsonl
    run_path                TEXT,               -- absolute path this was loaded from
    mode                    TEXT,
    model                   TEXT,
    split                   TEXT,
    git_sha                 TEXT,
    corpus_content_hash     TEXT,
    prompt_set_hash         TEXT,
    question_pool_hash      TEXT,
    created_at_utc          TEXT,
    completed_at_utc        TEXT,               -- NULL = the run never finished
    manifest_schema_version INTEGER,
    n_resumes               INTEGER,            -- len(manifest.resumes[])
    arms_expected           TEXT,               -- JSON array: manifest ∪ index
    arms_loaded             TEXT,               -- JSON array: generations.<arm>.jsonl actually read
    arms_missing            TEXT,               -- JSON array: expected but no generations file
    n_questions             INTEGER,
    n_turns                 INTEGER,
    n_events                INTEGER,
    quotable                INTEGER,            -- from runs/index.jsonl; NULL = run not indexed
    claim_ready             INTEGER,
    headline_json           TEXT,
    not_quotable_because    TEXT,               -- JSON array
    seq_source              TEXT,               -- 'field' | 'derived' | 'mixed' | NULL (no events)
    notes                   TEXT,               -- JSON array of degeneracy notes from this load
    loaded_at_utc           TEXT,
    manifest_json           TEXT,               -- full manifest, incl. nested resumes[]
    index_json              TEXT                -- full runs/index.jsonl row, if any
);

CREATE TABLE IF NOT EXISTS turns (
    run_dir             TEXT NOT NULL,
    arm                 TEXT NOT NULL,
    question_id         TEXT NOT NULL,
    db_id               TEXT,
    split               TEXT,
    run_id              TEXT,
    turn_id             TEXT,
    -- verdict
    correct             INTEGER,
    correct_strict      INTEGER,
    outcome             TEXT,
    failed_stage        TEXT,
    refused_by          TEXT,
    error               TEXT,
    error_type          TEXT,
    failed_layer        TEXT,
    tier                TEXT,
    semantic_assurance  TEXT,
    safety_clearance    INTEGER,
    graded_delivery     INTEGER,
    -- prediction
    generated_sql       TEXT,
    pred_nrows          INTEGER,
    pred_ncols          INTEGER,
    gold_nrows          INTEGER,
    nrows_match         INTEGER,
    -- gold, left-joined from questions.jsonl (NULL when that file is absent)
    question            TEXT,
    evidence            TEXT,
    gold_sql            TEXT,
    -- leakage / difficulty
    difficulty          TEXT,
    gold_twin_in_train  INTEGER,
    gold_frozen         INTEGER,
    gold_order_sensitive INTEGER,
    gold_schema_rank    INTEGER,
    -- routing
    routed_hit          INTEGER,
    pick_hit            INTEGER,
    schema_pick         TEXT,
    schema_pick_fallback TEXT,
    total_schemas       INTEGER,
    routing_bypassed    INTEGER,
    decoy_touch         INTEGER,
    -- context / effort
    context_chars       INTEGER,
    n_notes_injected    INTEGER,
    n_few_shots_injected INTEGER,
    attempts            INTEGER,
    n_tool_calls_total  INTEGER,
    ledger_len          INTEGER,
    -- cost
    latency_sec         REAL,
    cost_est_usd        REAL,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    total_tokens        INTEGER,
    -- oracle / provenance
    oracle_rung         TEXT,
    oracle_applied      INTEGER,
    prompt_set_hash     TEXT,
    -- everything else
    row_json            TEXT NOT NULL,
    PRIMARY KEY (run_dir, arm, question_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS events (
    run_dir     TEXT NOT NULL,
    file_row    INTEGER NOT NULL,   -- 0-based line number in stage_events.jsonl; total order
    arm         TEXT,
    question_id TEXT,
    db_id       TEXT,
    run_id      TEXT,
    turn_id     TEXT,               -- NULL on runs written before turn_id existed
    seq         INTEGER,            -- 0-based ordinal WITHIN the turn
    seq_derived INTEGER NOT NULL,   -- 1 = derived from file order, 0 = read from the row
    stage       TEXT,               -- open vocabulary; NOT validated against stages.Stage
    status      TEXT,
    ms          REAL,
    detail_json TEXT,
    PRIMARY KEY (run_dir, file_row)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_turns_arm_correct      ON turns(arm, correct);
CREATE INDEX IF NOT EXISTS idx_turns_arm_failed_stage ON turns(arm, failed_stage);
CREATE INDEX IF NOT EXISTS idx_turns_arm_outcome      ON turns(arm, outcome);
CREATE INDEX IF NOT EXISTS idx_turns_question         ON turns(question_id);
CREATE INDEX IF NOT EXISTS idx_events_stage_status    ON events(stage, status);
CREATE INDEX IF NOT EXISTS idx_events_turn            ON events(run_dir, arm, question_id);
CREATE INDEX IF NOT EXISTS idx_events_question        ON events(question_id);
"""

#: ``turns`` columns, in table order, excluding ``row_json`` which is appended last.
_TURN_COLUMNS: tuple[str, ...] = (
    "run_dir", "arm", "question_id", "db_id", "split", "run_id", "turn_id",
    "correct", "correct_strict", "outcome", "failed_stage", "refused_by", "error",
    "error_type", "failed_layer", "tier", "semantic_assurance", "safety_clearance",
    "graded_delivery",
    "generated_sql", "pred_nrows", "pred_ncols", "gold_nrows", "nrows_match",
    "question", "evidence", "gold_sql",
    "difficulty", "gold_twin_in_train", "gold_frozen", "gold_order_sensitive",
    "gold_schema_rank",
    "routed_hit", "pick_hit", "schema_pick", "schema_pick_fallback", "total_schemas",
    "routing_bypassed", "decoy_touch",
    "context_chars", "n_notes_injected", "n_few_shots_injected", "attempts",
    "n_tool_calls_total", "ledger_len",
    "latency_sec", "cost_est_usd", "input_tokens", "output_tokens", "total_tokens",
    "oracle_rung", "oracle_applied", "prompt_set_hash",
    "row_json",
)

#: Generation-row fields copied straight across, with a bool -> 0/1 coercion applied to
#: whichever of them arrive as booleans.
_PASSTHROUGH: tuple[str, ...] = (
    "db_id", "split", "run_id", "turn_id",
    "correct", "correct_strict", "outcome", "failed_stage", "refused_by", "error",
    "error_type", "failed_layer", "tier", "semantic_assurance", "safety_clearance",
    "graded_delivery",
    "generated_sql", "pred_nrows", "pred_ncols", "gold_nrows", "nrows_match",
    "difficulty", "gold_twin_in_train", "gold_frozen", "gold_order_sensitive",
    "gold_schema_rank",
    "routed_hit", "pick_hit", "schema_pick", "schema_pick_fallback", "total_schemas",
    "routing_bypassed", "decoy_touch",
    "context_chars", "n_notes_injected", "n_few_shots_injected", "attempts",
    "ledger_len",
    "latency_sec", "cost_est_usd",
    "oracle_rung", "oracle_applied", "prompt_set_hash",
)

_EVENT_COLUMNS: tuple[str, ...] = (
    "run_dir", "file_row", "arm", "question_id", "db_id", "run_id", "turn_id",
    "seq", "seq_derived", "stage", "status", "ms", "detail_json",
)

_RUN_COLUMNS: tuple[str, ...] = (
    "run_dir", "run_path", "mode", "model", "split", "git_sha",
    "corpus_content_hash", "prompt_set_hash", "question_pool_hash",
    "created_at_utc", "completed_at_utc", "manifest_schema_version", "n_resumes",
    "arms_expected", "arms_loaded", "arms_missing",
    "n_questions", "n_turns", "n_events",
    "quotable", "claim_ready", "headline_json", "not_quotable_because",
    "seq_source", "notes", "loaded_at_utc", "manifest_json", "index_json",
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _scalar(value: Any) -> Any:
    """SQLite-storable form of a JSON value.

    Booleans become 0/1 (SQLite has no bool, and ``correct = 1`` must work).
    Containers become JSON text rather than raising, so an unexpectedly nested value
    in a promoted column degrades to something queryable instead of killing the load.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _iter_jsonl(
    path: Path, *, bad_lines: list[int] | None = None
) -> Iterator[tuple[int, dict[str, Any]]]:
    """``(line_index, row)`` for each non-blank line that parses to an object.

    A malformed line is skipped rather than fatal: a run killed mid-append leaves a
    truncated final line, and losing 50,763 good events to the 50,764th is the
    opposite of what this loader is for. Pass *bad_lines* to collect the indices of
    the ones dropped, so the load can say so instead of quietly shrinking.
    """
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                if bad_lines is not None:
                    bad_lines.append(index)
                continue
            if isinstance(row, dict):
                yield index, row
            elif bad_lines is not None:
                bad_lines.append(index)


def repo_root_for(path: Path) -> Path | None:
    """Nearest ancestor of *path* that looks like the repository root."""
    for parent in [path, *path.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return None


def canonical_run_dir(path: Path) -> str:
    """The key a run is stored under: its repo-relative POSIX path.

    ``runs/index.jsonl`` records ``run_dir`` exactly this way
    (``runs/datalake/luna-max/20260801T-ladder``), so using the same form is what
    makes the index row joinable without a fuzzy match. Falls back to the absolute
    POSIX path when the run lives outside any repo — a temp dir in a test, say.
    """
    resolved = path.resolve()
    root = repo_root_for(resolved)
    if root is not None:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def find_index_file(run_path: Path) -> Path | None:
    """The ``index.jsonl`` governing *run_path*, if there is one.

    Walks up looking for a sibling ``index.jsonl``; in the layout on disk that is
    ``runs/index.jsonl``, one level above ``runs/datalake/``.
    """
    for parent in run_path.resolve().parents:
        candidate = parent / "index.jsonl"
        if candidate.is_file():
            return candidate
    return None


def load_index(index_path: Path | None) -> dict[str, dict[str, Any]]:
    """``run_dir -> index row``. Later rows win; the index is append-with-replace."""
    if index_path is None or not index_path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for _, row in _iter_jsonl(index_path):
        key = row.get("run_dir")
        if isinstance(key, str):
            rows[key.replace("\\", "/")] = row
    return rows


def declared_stages() -> frozenset[str]:
    """The ``Stage`` vocabulary declared in ``src/governed_bi/stages.py``.

    Read, never enforced. Its only job is to let a load report *undeclared* stages
    (``sql_normalisation`` on the 20260801 runs) so a new tool stage is visible rather
    than silently absorbed. Import failure is not fatal — the loader must work against
    a run directory copied somewhere without the package.
    """
    try:
        from governed_bi.stages import Stage
    except Exception:  # pragma: no cover - only when the package is not importable
        return frozenset()
    return frozenset(member.value for member in Stage)


def discover_runs(root: Path) -> list[Path]:
    """Every directory under *root* holding a ``manifest.json``.

    Handles both nesting shapes on disk — bare (``runs/datalake/<ts>/``) and
    bundle-with-nested-run-dir (``runs/datalake/<label>/<ts>/``) — by walking rather
    than globbing a fixed depth. Prunes ``corpus_*`` (~11,768 files each), ``_staging``
    and dotdirs, and stops descending once a manifest is found: run directories do not
    nest inside one another.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith(_PRUNE_PREFIXES)
        ]
        if "manifest.json" in filenames:
            found.append(Path(dirpath))
            dirnames[:] = []
    return sorted(found)


# --------------------------------------------------------------------------- #
# Connection / schema
# --------------------------------------------------------------------------- #


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the analysis database with the schema applied."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Autocommit: the load drives BEGIN/COMMIT itself so a run is all-or-nothing.
    conn.isolation_level = None
    existing = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('runs','turns','events')"
    ).fetchone()[0]
    if has_tables and existing != SCHEMA_VERSION:
        conn.close()
        raise SystemExit(
            f"{db_path} was written by loader schema v{existing}, this is v{SCHEMA_VERSION}. "
            "These databases are rebuilt from the JSONL in seconds — delete it and reload."
        )
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


# --------------------------------------------------------------------------- #
# Row shaping
# --------------------------------------------------------------------------- #


def _tokens(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    """``(input, output, total)`` tokens, preferring ``token_sum`` over ``usage``.

    Both carry the same three keys on the runs seen so far; ``token_sum`` is the
    post-hoc roll-up over ``token_usage[]`` and is the one to trust when they disagree.
    """
    for key in ("token_sum", "usage"):
        block = row.get(key)
        if isinstance(block, dict):
            return (
                block.get("input_tokens"),
                block.get("output_tokens"),
                block.get("total_tokens"),
            )
    return (None, None, None)


def turn_values(
    row: dict[str, Any],
    *,
    run_dir: str,
    arm: str,
    question: dict[str, Any] | None,
) -> tuple[Any, ...]:
    """One ``turns`` row, in ``_TURN_COLUMNS`` order.

    *question* is the ``questions.jsonl`` record for this ``question_id`` or ``None``.
    Gold comes from there and nowhere else — generation rows do not carry ``gold_sql``,
    which is trap #2 in the module docstring.
    """
    values: dict[str, Any] = {
        "run_dir": run_dir,
        "arm": arm,
        "question_id": str(row.get("question_id")),
    }
    for field in _PASSTHROUGH:
        values[field] = _scalar(row.get(field))

    # The row's own `arm` wins over the filename when both exist; they have agreed on
    # every run so far, and if they ever stop the row is the closer witness.
    if isinstance(row.get("arm"), str):
        values["arm"] = row["arm"]

    question = question or {}
    values["question"] = question.get("question")
    values["evidence"] = question.get("evidence")
    values["gold_sql"] = question.get("gold_sql")
    if not values.get("db_id"):
        values["db_id"] = question.get("db_id")

    tool_calls = row.get("n_tool_calls")
    values["n_tool_calls_total"] = (
        sum(v for v in tool_calls.values() if isinstance(v, (int, float)))
        if isinstance(tool_calls, dict)
        else None
    )

    values["input_tokens"], values["output_tokens"], values["total_tokens"] = _tokens(row)
    values["row_json"] = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return tuple(values.get(column) for column in _TURN_COLUMNS)


def read_questions(run_path: Path) -> dict[str, dict[str, Any]]:
    """``question_id -> question record`` from ``questions.jsonl``, or ``{}``."""
    path = run_path / "questions.jsonl"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in _iter_jsonl(path):
        qid = row.get("question_id")
        if qid is not None:
            out[str(qid)] = row
    return out


def iter_event_rows(
    path: Path, *, run_dir: str
) -> Iterator[tuple[tuple[Any, ...], bool, str | None]]:
    """``(values, seq_was_derived, stage)`` for each stage event, in file order.

    The per-turn ordinal. If the row carries one of :data:`SEQ_FIELDS`, that value is
    used verbatim. Otherwise it is derived by counting within a contiguous run of rows
    sharing ``(arm, question_id, turn_id)`` — legitimate because that contiguity was
    measured, not assumed (see the module docstring), and because the alternative
    (grouping non-contiguously) would silently interleave a resume's re-served turn
    with the original.

    ``turn_id`` is absent on runs written before it existed. There, a question is
    served exactly once per arm, so ``(arm, question_id, None)`` is still one turn.
    Where ``turn_id`` *is* present it is what separates a resume's second attempt at
    the same question from the first: the 20260801 luna-max ladder has 4,163 turns
    over 4,053 arm-question pairs.
    """
    previous_key: tuple[Any, Any, Any] | None = None
    counter = 0
    for file_row, row in _iter_jsonl(path):
        key = (row.get("arm"), row.get("question_id"), row.get("turn_id"))
        if key != previous_key:
            previous_key = key
            counter = 0

        explicit = next(
            (row[field] for field in SEQ_FIELDS if isinstance(row.get(field), int)),
            None,
        )
        derived = explicit is None
        seq = counter if derived else explicit
        counter += 1

        detail = row.get("detail")
        stage = row.get("stage")
        values = (
            run_dir,
            file_row,
            row.get("arm"),
            None if row.get("question_id") is None else str(row["question_id"]),
            row.get("db_id"),
            row.get("run_id"),
            row.get("turn_id"),
            seq,
            1 if derived else 0,
            stage,
            row.get("status"),
            row.get("ms"),
            None if detail is None else json.dumps(detail, ensure_ascii=False, sort_keys=True),
        )
        yield values, derived, stage if isinstance(stage, str) else None


# --------------------------------------------------------------------------- #
# The load
# --------------------------------------------------------------------------- #


class LoadReport:
    """What one run directory actually produced. Printed, and returned to tests."""

    def __init__(self, run_dir: str, run_path: Path) -> None:
        self.run_dir = run_dir
        self.run_path = run_path
        self.arms_expected: list[str] = []
        self.arms_loaded: list[str] = []
        self.arms_missing: list[str] = []
        self.n_turns = 0
        self.n_events = 0
        self.n_questions = 0
        self.seq_source: str | None = None
        self.notes: list[str] = []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LoadReport {self.run_dir} turns={self.n_turns} events={self.n_events}>"

    def summary_line(self) -> str:
        bits = [
            f"{self.run_dir}",
            f"arms={','.join(self.arms_loaded) or '-'}",
            f"turns={self.n_turns}",
            f"events={self.n_events}",
        ]
        if self.arms_missing:
            bits.append(f"MISSING_ARMS={','.join(self.arms_missing)}")
        if self.seq_source:
            bits.append(f"seq={self.seq_source}")
        return "  ".join(bits)


def load_run(
    conn: sqlite3.Connection,
    run_path: Path,
    *,
    index: dict[str, dict[str, Any]] | None = None,
    batch: int = 5000,
) -> LoadReport:
    """Load one run directory. Idempotent: re-loading replaces, never duplicates.

    Nothing here is allowed to fail the load because part of a run is absent. Every
    absence becomes a note on the ``runs`` row instead, because the failure mode that
    matters is a *silently partial* database, not a missing one — and the runs most
    worth reading are exactly the ones that broke.
    """
    run_path = Path(run_path).resolve()
    manifest_path = run_path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json in {run_path}")

    run_dir = canonical_run_dir(run_path)
    report = LoadReport(run_dir, run_path)

    manifest: dict[str, Any] = {}
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.notes.append(f"manifest.json is unparseable ({exc}); run loaded with no config")
    else:
        if isinstance(parsed, dict):
            manifest = parsed
        else:
            report.notes.append("manifest.json is not a JSON object; run loaded with no config")

    if index is None:
        index = load_index(find_index_file(run_path))
    index_row = index.get(run_dir)
    if index_row is None:
        report.notes.append("not present in runs/index.jsonl (no quotable / headline verdict)")

    # --- arms ------------------------------------------------------------- #
    # `arms` in the manifest is the *plan*. The oracle run has `arms: []` yet ships
    # generations.oracle_sql.jsonl; the two aborted provider-b ladders list three arms
    # and ship none. So the union is the expectation and the files are the truth.
    expected = [a for a in (manifest.get("arms") or []) if isinstance(a, str)]
    for arm in (index_row or {}).get("arms") or []:
        if isinstance(arm, str) and arm not in expected:
            expected.append(arm)
    on_disk = sorted(
        p.name[len("generations.") : -len(".jsonl")]
        for p in run_path.glob("generations.*.jsonl")
        if p.is_file()
    )
    for arm in on_disk:
        if arm not in expected:
            expected.append(arm)
            report.notes.append(f"arm {arm!r} has generations but is not declared in the manifest")
    report.arms_expected = expected
    report.arms_missing = [a for a in expected if a not in on_disk]
    if report.arms_missing:
        report.notes.append(
            f"no generations file for {', '.join(report.arms_missing)} — "
            "turns are absent for those arms; stage events (if any) are still loaded"
        )

    questions = read_questions(run_path)
    report.n_questions = len(questions)
    if not questions:
        report.notes.append("no questions.jsonl: gold_sql / question / evidence are NULL")

    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM turns  WHERE run_dir = ?", (run_dir,))
        conn.execute("DELETE FROM events WHERE run_dir = ?", (run_dir,))
        conn.execute("DELETE FROM runs   WHERE run_dir = ?", (run_dir,))

        insert_turn = (
            f"INSERT OR REPLACE INTO turns ({', '.join(_TURN_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_TURN_COLUMNS))})"
        )
        for arm in on_disk:
            path = run_path / f"generations.{arm}.jsonl"
            bad: list[int] = []
            loaded = 0
            for chunk in _chunks(
                (
                    turn_values(
                        row,
                        run_dir=run_dir,
                        arm=arm,
                        question=questions.get(str(row.get("question_id"))),
                    )
                    for _, row in _iter_jsonl(path, bad_lines=bad)
                    if row.get("question_id") is not None
                ),
                batch,
            ):
                conn.executemany(insert_turn, chunk)
                loaded += len(chunk)
            if bad:
                report.notes.append(
                    f"generations.{arm}.jsonl: {len(bad)} unparseable line(s) skipped"
                )
            report.arms_loaded.append(arm)
            report.n_turns += loaded

        # --- events -------------------------------------------------------- #
        events_path = run_path / "stage_events.jsonl"
        stages_seen: set[str] = set()
        any_derived = False
        any_explicit = False
        if not events_path.is_file():
            report.notes.append("no stage_events.jsonl")
        elif events_path.stat().st_size == 0:
            report.notes.append("stage_events.jsonl is empty (0 bytes): no per-stage timing")
        else:
            insert_event = (
                f"INSERT OR REPLACE INTO events ({', '.join(_EVENT_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_EVENT_COLUMNS))})"
            )
            pending: list[tuple[Any, ...]] = []
            for values, derived, stage in iter_event_rows(events_path, run_dir=run_dir):
                any_derived = any_derived or derived
                any_explicit = any_explicit or not derived
                if stage:
                    stages_seen.add(stage)
                pending.append(values)
                if len(pending) >= batch:
                    conn.executemany(insert_event, pending)
                    report.n_events += len(pending)
                    pending = []
            if pending:
                conn.executemany(insert_event, pending)
                report.n_events += len(pending)

        if any_explicit and any_derived:
            report.seq_source = "mixed"
        elif any_explicit:
            report.seq_source = "field"
        elif any_derived:
            report.seq_source = "derived"

        undeclared = sorted(stages_seen - declared_stages()) if stages_seen else []
        if undeclared:
            report.notes.append(
                f"stage values not declared in governed_bi.stages.Stage: {', '.join(undeclared)}"
            )
        if not manifest.get("completed_at_utc"):
            report.notes.append("no completed_at_utc: the run did not finish")

        n_questions = report.n_questions or _int_or_none((index_row or {}).get("n_questions"))
        if not n_questions:
            n_questions = conn.execute(
                "SELECT COUNT(DISTINCT question_id) FROM turns WHERE run_dir = ?", (run_dir,)
            ).fetchone()[0] or None
        if not n_questions:
            n_questions = conn.execute(
                "SELECT COUNT(DISTINCT question_id) FROM events WHERE run_dir = ?", (run_dir,)
            ).fetchone()[0] or None
        report.n_questions = n_questions or 0

        conn.execute(
            f"INSERT INTO runs ({', '.join(_RUN_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_RUN_COLUMNS))})",
            (
                run_dir,
                run_path.as_posix(),
                manifest.get("mode"),
                manifest.get("model") or (index_row or {}).get("model"),
                manifest.get("split") or (index_row or {}).get("split"),
                manifest.get("git_sha"),
                manifest.get("corpus_content_hash"),
                manifest.get("prompt_set_hash"),
                manifest.get("question_pool_hash"),
                manifest.get("created_at_utc"),
                manifest.get("completed_at_utc"),
                _int_or_none(manifest.get("manifest_schema_version")),
                len(manifest.get("resumes") or []),
                json.dumps(report.arms_expected),
                json.dumps(report.arms_loaded),
                json.dumps(report.arms_missing),
                n_questions,
                report.n_turns,
                report.n_events,
                _bool_or_none((index_row or {}).get("quotable")),
                _bool_or_none((index_row or {}).get("claim_ready")),
                _json_or_none((index_row or {}).get("headline")),
                _json_or_none((index_row or {}).get("not_quotable_because")),
                report.seq_source,
                json.dumps(report.notes, ensure_ascii=False),
                datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                json.dumps(manifest, ensure_ascii=False),
                None if index_row is None else json.dumps(index_row, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return report


def _chunks(rows: Iterable[tuple[Any, ...]], size: int) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool_or_none(value: Any) -> int | None:
    return None if value is None else (1 if value else 0)


def _json_or_none(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Example queries
# --------------------------------------------------------------------------- #

EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        "Which runs are in this database, and may they be quoted?",
        """
SELECT run_dir, model, n_questions, n_turns, n_events,
       arms_loaded, arms_missing, quotable, completed_at_utc
FROM runs
ORDER BY created_at_utc;
""",
    ),
    (
        "Per-arm EX with crashes excluded.\n"
        "  A crash is our bug, a refusal is the product working, and a wrong answer is\n"
        "  the model's. Pooling them is how a set of numbers had to be retired once\n"
        "  already, so the denominator is stated: graded turns that did not crash.\n"
        "  The filtering is done in the aggregate, not in a WHERE clause, so an arm\n"
        "  that crashed on every question still appears (ex NULL, crash_rate 1.0)\n"
        "  instead of vanishing from the table -- a missing row reads as 'not run'.",
        """
SELECT r.model, t.run_dir, t.arm,
       COUNT(*)                                                     AS n_turns,
       SUM(COALESCE(t.outcome,'') = 'crashed')                      AS n_crashed,
       SUM(COALESCE(t.outcome,'') <> 'crashed' AND t.correct IS NOT NULL) AS n_graded,
       SUM(COALESCE(t.outcome,'') <> 'crashed' AND t.correct = 1)   AS n_correct,
       ROUND(1.0 * SUM(COALESCE(t.outcome,'') <> 'crashed' AND t.correct = 1)
             / NULLIF(SUM(COALESCE(t.outcome,'') <> 'crashed' AND t.correct IS NOT NULL), 0),
             4)                                                     AS ex,
       ROUND(1.0 * SUM(COALESCE(t.outcome,'') = 'crashed') / COUNT(*), 4) AS crash_rate
FROM turns t JOIN runs r USING (run_dir)
GROUP BY t.run_dir, t.arm
ORDER BY r.model, t.run_dir, ex DESC;
""",
    ),
    (
        "Do failing questions call inspect_schema less than passing ones?\n"
        "  (Measured answer on the runs to hand: no, the reverse. Failing turns call\n"
        "  every tool MORE, on every arm. Reach for it as a symptom of a hard question,\n"
        "  not as a lever.)\n"
        "  Tool counts are not a promoted column: they live in the raw row under\n"
        "  n_tool_calls, which is an open vocabulary, so json_extract is the honest way\n"
        "  to read them and json_each below is how you find out what tools exist.",
        """
SELECT arm,
       CASE t.correct WHEN 1 THEN 'pass' ELSE 'fail' END AS verdict,
       COUNT(*)                                                              AS n,
       ROUND(AVG(COALESCE(json_extract(row_json,'$.n_tool_calls.inspect_schema'), 0)), 3) AS inspect_schema,
       ROUND(AVG(COALESCE(json_extract(row_json,'$.n_tool_calls.search_corpus'), 0)), 3)  AS search_corpus,
       ROUND(AVG(COALESCE(json_extract(row_json,'$.n_tool_calls.run_query'),     0)), 3)  AS run_query,
       ROUND(AVG(COALESCE(t.n_tool_calls_total, 0)), 3)                       AS all_tools
FROM turns t
WHERE t.outcome = 'answered' AND t.correct IS NOT NULL
GROUP BY arm, verdict
ORDER BY arm, verdict;
""",
    ),
    (
        "Which tool names exist at all (open vocabulary, read from the data)?",
        """
SELECT j.key AS tool, COUNT(*) AS turns_using, SUM(j.value) AS calls
FROM turns t, json_each(t.row_json, '$.n_tool_calls') j
WHERE j.key IS NOT NULL   -- a JSON null n_tool_calls yields one keyless row
GROUP BY j.key ORDER BY calls DESC;
""",
    ),
    (
        "Which stage dominates latency?\n"
        "  CAUTION: the rails contain each other. schema_pick / retrieve sit inside\n"
        "  assemble; guardrail / execute sit inside agent_core. Summing every stage\n"
        "  double-counts, so pick one level. This query reports both columns and the\n"
        "  percentage is taken against the rail total, not the grand total.",
        """
WITH rails(name) AS (VALUES ('route'),('assemble'),('agent_core'),('narrate'),('finalize'))
SELECT e.stage,
       (e.stage IN (SELECT name FROM rails))          AS is_rail,
       COUNT(*)                                        AS n,
       ROUND(SUM(e.ms)/1000.0, 1)                      AS total_sec,
       ROUND(AVG(e.ms), 1)                             AS mean_ms,
       ROUND(100.0 * SUM(e.ms) / (SELECT SUM(ms) FROM events
                                  WHERE run_dir = e.run_dir
                                    AND stage IN (SELECT name FROM rails)), 2) AS pct_of_rails
FROM events e
GROUP BY e.stage ORDER BY total_sec DESC;
""",
    ),
    (
        "Where do turns fail, per arm?",
        """
SELECT arm, outcome, COALESCE(failed_stage,'(none)') AS failed_stage,
       COALESCE(refused_by,'(none)') AS refused_by, COUNT(*) AS n
FROM turns
GROUP BY arm, outcome, failed_stage, refused_by
ORDER BY arm, n DESC;
""",
    ),
    (
        "Same question, different arms: where did curation flip the verdict?",
        """
SELECT a.question_id, a.db_id, a.correct AS baseline, b.correct AS curated, a.gold_sql
FROM turns a JOIN turns b
  ON a.run_dir = b.run_dir AND a.question_id = b.question_id
WHERE a.arm = 'baseline' AND b.arm = 'curated' AND a.correct <> b.correct
ORDER BY a.db_id, a.question_id;
""",
    ),
    (
        "Replay one turn's stage sequence.\n"
        "  seq_derived = 1 means the ordinal came from file order, not from the row.",
        """
WITH pick AS (
    SELECT run_dir, arm, question_id, turn_id
    FROM events
    WHERE run_dir = (SELECT run_dir FROM runs ORDER BY n_events DESC LIMIT 1)
    LIMIT 1
)
SELECT e.seq, e.seq_derived, e.stage, e.status, e.ms, e.detail_json
FROM events e JOIN pick p
  ON e.run_dir = p.run_dir AND e.arm = p.arm
 AND e.question_id = p.question_id AND e.turn_id IS p.turn_id
ORDER BY e.file_row;
""",
    ),
    (
        "Which fields does each run actually emit?\n"
        "  The field set is per-run: ROW_FIELDS declares 79, the 20260731 ladder wrote\n"
        "  73, the 20260802 oracle run wrote 78. This is how you check before comparing.",
        """
SELECT j.key AS field, COUNT(DISTINCT t.run_dir) AS in_n_runs
FROM turns t, json_each(t.row_json) j
GROUP BY j.key
HAVING in_n_runs < (SELECT COUNT(*) FROM runs WHERE n_turns > 0)
ORDER BY in_n_runs, field;
""",
    ),
    (
        "Stage error hot spots (the events table stands alone when generations are lost).",
        """
SELECT run_dir, arm, stage, status, COUNT(*) AS n,
       json_extract(detail_json, '$.error_type') AS error_type
FROM events
WHERE status <> 'ok'
GROUP BY run_dir, arm, stage, status, error_type
ORDER BY n DESC;
""",
    ),
)


def print_examples() -> None:
    for i, (title, sql) in enumerate(EXAMPLES, start=1):
        print(f"\n-- [{i}] {title}")
        print(sql.strip())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_table(cursor: sqlite3.Cursor, limit: int) -> None:
    rows = cursor.fetchmany(limit)
    if not rows:
        print("(no rows)")
        return
    headers = [d[0] for d in cursor.description]
    table = [headers] + [
        ["" if v is None else str(v).replace("\n", " ")[:60] for v in row] for row in rows
    ]
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in table[1:]:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    if len(rows) == limit:
        print(f"... (truncated at --limit {limit})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("run_dirs", nargs="*", type=Path, help="run directories to load")
    parser.add_argument("--db", type=Path, help="SQLite file to create/update")
    parser.add_argument(
        "--discover", type=Path, action="append", default=[],
        help="load every run directory under this root (prunes corpus_*/ and _staging/)",
    )
    parser.add_argument("--index", type=Path, help="path to runs/index.jsonl (auto-detected otherwise)")
    parser.add_argument("--no-index", action="store_true", help="do not join runs/index.jsonl")
    parser.add_argument("--examples", action="store_true", help="print example queries and exit")
    parser.add_argument("--sql", help="run one SQL statement against --db and print the result")
    parser.add_argument("--example", type=int, help="run example query N against --db")
    parser.add_argument("--limit", type=int, default=50, help="rows printed by --sql/--example")
    args = parser.parse_args(argv)

    if args.examples:
        print_examples()
        return 0

    if args.db is None:
        parser.error("--db is required (except with --examples)")

    targets: list[Path] = list(args.run_dirs)
    for root in args.discover:
        targets.extend(discover_runs(root))
    # de-duplicate, keep order
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in targets:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)

    conn = connect(args.db)
    try:
        index: dict[str, dict[str, Any]] | None = None
        if args.no_index:
            index = {}
        elif args.index is not None:
            index = load_index(args.index)

        failures = 0
        for path in ordered:
            run_index = index
            if run_index is None:
                run_index = load_index(find_index_file(path))
            try:
                report = load_run(conn, path, index=run_index)
            except Exception as exc:  # one bad run must not sink the rest
                failures += 1
                print(f"FAILED  {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            print(report.summary_line())
            for note in report.notes:
                print(f"        note: {note}")

        if ordered:
            totals = conn.execute(
                "SELECT (SELECT COUNT(*) FROM runs), (SELECT COUNT(*) FROM turns), "
                "(SELECT COUNT(*) FROM events)"
            ).fetchone()
            print(f"\ndatabase now holds {totals[0]} run(s), {totals[1]} turn(s), {totals[2]} event(s)")

        sql = args.sql
        if args.example is not None:
            if not 1 <= args.example <= len(EXAMPLES):
                parser.error(f"--example must be 1..{len(EXAMPLES)}")
            title, sql = EXAMPLES[args.example - 1]
            print(f"\n-- {title}")
        if sql:
            _print_table(conn.execute(sql), args.limit)
        return 1 if failures else 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
