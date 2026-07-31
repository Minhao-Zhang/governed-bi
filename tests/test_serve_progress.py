"""N11: serve progress ticker (driver-side, not parallel.py)."""

from __future__ import annotations

from governed_bi.eval.run_datalake import _ServeProgress


def test_serve_progress_prints_every_question_on_small_runs(capsys):
    prog = _ServeProgress(arm="curated", total=5)
    for _ in range(5):
        prog.tick()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "serve [curated]" in ln]
    assert len(lines) == 5
    assert "5/5" in lines[-1]


def test_serve_progress_throttles_on_large_runs(capsys):
    prog = _ServeProgress(arm="baseline", total=100)
    assert prog.every == 5
    for _ in range(100):
        prog.tick()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "serve [baseline]" in ln]
    # every 5 plus the final 100/100 (100 % 5 == 0 so final is already included)
    assert len(lines) == 20
    assert "100/100" in lines[-1]
