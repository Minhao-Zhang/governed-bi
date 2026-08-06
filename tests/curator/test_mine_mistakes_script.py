"""scripts/mine_mistakes_v2.py end to end: log a turn, mine it, find the draft on disk."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contracts import needs  # noqa: E402

pytestmark = needs("D")

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "mine_mistakes_v2.py"


def test_script_mines_a_logged_fail_then_pass_turn(monkeypatch, tmp_path: Path) -> None:
    turn_log_dir = tmp_path / "runs"
    corpus_dir = tmp_path / "corpus"
    monkeypatch.setenv("GOVERNED_BI_TURN_LOG_DIR", str(turn_log_dir))

    import importlib

    from governed_bi.api import trace_store

    importlib.reload(trace_store)  # pick up the env var set above

    record = {
        "turn_id": "t1",
        "schemas": ["beer_factory"],
        "execution": {
            "attempts": [
                {"verdict_layer": "COLUMNS", "passed": False, "reason_code": "r_column_not_allowed",
                 "path": "agent", "executed_sql": None},
                {"verdict_layer": None, "passed": True, "reason_code": "r_ok",
                 "path": "agent", "executed_sql": "SELECT COUNT(*) FROM customers"},
            ],
            "terminal": "answered",
            "guardrail_errors": 0,
        },
    }
    trace_store.append_turn(record, question="how many customers?", answer_text="42")

    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus-dir", str(corpus_dir), "--schema", "beer_factory"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "GOVERNED_BI_TURN_LOG_DIR": str(turn_log_dir)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mined 1 draft" in result.stdout or "scanned 1 turn(s), mined 1 draft(s)" in result.stdout

    from governed_bi.corpus.store import load

    assets, problems = load(corpus_dir)
    assert not problems
    mined = [a for a in assets if a.asset_type.value == "few_shot"]
    assert len(mined) == 1
    assert mined[0].sql == "SELECT COUNT(*) FROM customers"
