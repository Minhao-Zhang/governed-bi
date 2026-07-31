"""N15.5: driver auto-writes analysis.json after summary.json."""

from __future__ import annotations

import inspect

from governed_bi.eval import run_datalake


def test_run_datalake_calls_analyse_run_after_summary():
    """The hook must stay wired: analysis.json was missing from every historical
    run because the driver never called analyse_run despite the CLI existing."""
    src = inspect.getsource(run_datalake)
    assert "analyse_run(" in src
    assert "write_questions_sidecar(" in src
    assert "analysis.json" in src
    # Loud failure, not a silent skip — the warning string is the operator signal.
    assert "analyse_run failed" in src
