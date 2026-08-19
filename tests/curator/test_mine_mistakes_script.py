"""scripts/mine_mistakes_v2.py end to end: write an archived turn, mine it, find the draft.

The turn is written as **plain JSONL**, not through a logger. It used to go through
``api/trace_store.append_turn``, which upstream's ADR 0014 deleted along with ``runs/serve``:
the audit surface reads thread state now, and its reader refuses to run outside a live Agent
server. So the miner reads archived logs itself (``_archived_turns``), and this test writes that
format directly — which also makes the file format the miner accepts the thing under test,
rather than a detail borrowed from a module that no longer exists.
"""

from __future__ import annotations

import json
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
    turn_log_dir.mkdir(parents=True, exist_ok=True)
    (turn_log_dir / "2026-08-18.jsonl").write_text(
        json.dumps({
            "question": "how many customers?",
            "answer_text": "42",
            "outcome": "answered",
            "asked_at": "2026-08-18T00:00:00+00:00",
            "record": record,
        }) + "\n",
        encoding="utf-8",
    )

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
