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
