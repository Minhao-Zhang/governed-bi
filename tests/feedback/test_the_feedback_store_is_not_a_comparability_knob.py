"""The store must never become a declared knob, and it must never point at the warehouse.

Two different mistakes with the same shape — a value that changes something it has no business
changing.

**The knob half.** ``serve/session.py::_resolved_knobs`` puts every declared knob on every serve
row and ``measure/gates.py::_knobs_resolved_gate`` compares them, so declaring the feedback store's
path as a comparability knob would move the config hash of every arm for a value no turn consumes.
That is ``expand_hops`` by construction: ``docs/open-work.md`` §3.10 keeps it red precisely because
"setting it changes no behaviour and does change the config hash".

**The warehouse half.** Two local SQLite stores are configured by environment variable and neither
may ever be pointed at the analytics warehouse. The failure is not a crash — a DSN would be
*accepted* by something, and the operational data would land in the database the engine reads from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from governed_bi.feedback.store import FeedbackStore
from governed_bi.register.knobs import comparability_keys

#: The names this design uses for its own configuration. None of them is a knob, and a future hand
#: adding one here rather than to ``register/knobs.py`` is the point of the list.
_FEEDBACK_ENV_NAMES = (
    "GOVERNED_BI_FEEDBACK_DB",
    "GOVERNED_BI_FEEDBACK_ADMIN",
    "GOVERNED_BI_PROPOSAL_DIR",
    "GOVERNED_BI_TRIAL_SCRATCH",
)


def test_no_comparability_knob_names_the_feedback_store() -> None:
    keys = set(comparability_keys())
    for name in _FEEDBACK_ENV_NAMES:
        bare = name.removeprefix("GOVERNED_BI_").lower()
        assert bare not in keys, (
            f"{bare!r} is a comparability knob. It would move knobs_resolved on every serve row "
            "for a value no turn consumes -- the expand_hops defect, on purpose."
        )
    assert not any("feedback" in key for key in keys), sorted(
        k for k in keys if "feedback" in k
    )
    assert not any("proposal" in key for key in keys)


def test_the_store_refuses_a_connection_string(tmp_path: Path) -> None:
    """Reuses ``paths.assert_not_a_warehouse``, which moved down a layer so both stores could
    share one definition instead of keeping two that can drift."""
    with pytest.raises(ValueError, match="connection string"):
        FeedbackStore("host=127.0.0.1 port=5435 dbname=bird user=bird password=bird")
    with pytest.raises(ValueError, match="connection string"):
        FeedbackStore("postgresql://bird@127.0.0.1:5435/bird")


def test_the_store_accepts_a_path_and_creates_its_parent(tmp_path: Path) -> None:
    store = FeedbackStore(tmp_path / "nested" / "deeper" / "feedback.sqlite")
    assert store.path.exists()
    assert store.queue().total == 0


def test_a_newer_schema_version_is_refused_rather_than_read(tmp_path: Path) -> None:
    """Older code reading a newer store silently drops columns nobody here writes."""
    import sqlite3
    from contextlib import closing

    path = tmp_path / "feedback.sqlite"
    FeedbackStore(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE schema_version SET version = 99")
        conn.commit()
    with pytest.raises(RuntimeError, match="schema version 99"):
        FeedbackStore(path)


def test_an_older_store_is_upgraded_rather_than_opened_as_if_current(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The direction an upgrade actually produces, and the one that had no code.

    ``_migrate`` handled the newer-store case above and was silent on this one, which is what
    made the gap hard to see: a refusal on one side reads as "migrations are thought about
    here". But ``executescript(SCHEMA)`` is all ``CREATE TABLE IF NOT EXISTS``, so on an
    existing file it adds no column and rewrites no version. A store at 1 opened by code that
    knows 2 reported itself up-to-date, started clean, and raised ``OperationalError: no such
    column`` at the **first request that touched the new field** — in front of a user, not at
    startup.

    Driven through the real constructor with a real ``ALTER TABLE``, because the property is
    that the column is *there afterwards*. Asserting only the version number would pass for a
    ``_migrate`` that bumped it and applied nothing, which is the same outage one step later.
    """
    import sqlite3
    from contextlib import closing

    import governed_bi.feedback.rows as rows_mod

    path = tmp_path / "feedback.sqlite"
    FeedbackStore(path)  # at 1

    monkeypatch.setattr(rows_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        rows_mod,
        "MIGRATIONS",
        {2: ("ALTER TABLE observation ADD COLUMN brand_new TEXT NOT NULL DEFAULT ''",)},
    )
    FeedbackStore(path)

    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        columns = {row[1] for row in conn.execute("PRAGMA table_info(observation)")}
        assert "brand_new" in columns, "the version moved and the column did not"
        assert conn.execute("SELECT brand_new FROM observation").fetchall() == []


def test_a_version_bump_with_no_declared_step_is_refused_at_open(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Forgetting the step must fail loudly at startup, not quietly at the first query.

    This is the guard that keeps the pair honest: with an upgrade path in place it becomes
    possible to bump ``SCHEMA_VERSION`` and write no statements, which restores the original
    defect exactly — a store that opens clean and is missing a column.
    """
    import governed_bi.feedback.rows as rows_mod

    path = tmp_path / "feedback.sqlite"
    FeedbackStore(path)

    monkeypatch.setattr(rows_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(rows_mod, "MIGRATIONS", {})
    with pytest.raises(RuntimeError, match=r"MIGRATIONS has no step for \[2\]"):
        FeedbackStore(path)


def test_a_failed_step_leaves_the_version_where_it_was(tmp_path: Path, monkeypatch: Any) -> None:
    """A half-applied upgrade the next process reads as finished is worse than a failed one.

    The bump and the statements share one transaction, so a step that raises rolls the whole
    thing back and the store still says 1 — which means the next open retries rather than
    treating a partial schema as current.
    """
    import sqlite3
    from contextlib import closing

    import governed_bi.feedback.rows as rows_mod

    path = tmp_path / "feedback.sqlite"
    FeedbackStore(path)

    monkeypatch.setattr(rows_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        rows_mod,
        "MIGRATIONS",
        {
            2: (
                "ALTER TABLE observation ADD COLUMN half_applied TEXT",
                "ALTER TABLE nonexistent_table ADD COLUMN boom TEXT",
            )
        },
    )
    with pytest.raises(sqlite3.OperationalError):
        FeedbackStore(path)

    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        columns = {row[1] for row in conn.execute("PRAGMA table_info(observation)")}
        assert "half_applied" not in columns, "the first statement survived a rolled-back step"
