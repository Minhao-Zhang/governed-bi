"""L7: eval rows carry usage/cost from provenance (no longer hard-coded None)."""

from __future__ import annotations

from governed_bi.analyst.run_log import FinalizeCtx, finalize_and_log
from governed_bi.analyst.answer import refusal
from governed_bi.config import Environment, Settings
from governed_bi.provenance import new_run_id
from dataclasses import replace


def test_finalize_provenance_feeds_eval_usage_shape(tmp_path):
    settings = replace(
        Settings.for_env(Environment.dev),
        run_log_kind="sqlite",
        run_log_path=str(tmp_path / "runs.sqlite"),
    )
    ans = finalize_and_log(
        refusal(escalation="x", provenance={"refused_by": "refuse_gate"}),
        ctx=FinalizeCtx(
            settings=settings,
            run_id=new_run_id(),
            thread_id="eval",
            n_human=1,
            token_usage=[
                {
                    "source": "agent_core",
                    "usage_metadata": {
                        "input_tokens": 9,
                        "output_tokens": 1,
                        "total_tokens": 10,
                    },
                }
            ],
        ),
    )
    usage = ans.provenance.get("token_sum")
    assert usage is not None
    assert usage["total_tokens"] == 10
    # Mimic arms.py last_solve_meta → run_experiment row
    meta = {
        "usage": ans.provenance.get("token_sum"),
        "cost_est_usd": ans.provenance.get("cost_est_usd"),
    }
    row = {"usage": meta.get("usage"), "cost_est_usd": meta.get("cost_est_usd")}
    assert row["usage"] is not None
    assert row["usage"]["total_tokens"] == 10
