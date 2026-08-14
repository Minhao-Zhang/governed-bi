"""The eval drivers import. That is the whole test, and it was missing.

Deleting ``measure/price.py`` left ``eval/datalake.py`` importing ``estimate_run_cost`` from a
module that no longer existed, and ``tools/run_datalake_eval.py`` calling the function that
imported it. **739 tests passed.** Nothing in the suite imports either file, so the one command
that spends real money on a 1351-question run was broken and green at the same time.

An import test is the cheapest possible guard and it catches the whole class: a deleted symbol, a
renamed helper, a moved module. It does not need a database, a model or a corpus — argument
parsing and the imports at module scope are all it exercises, which is exactly the layer that
broke.

The drivers under ``tools/`` are loaded by path rather than imported as a package, because that
is how they are run: ``uv run python tools/run_datalake_eval.py``. Importing them any other way
would test a spelling nobody uses.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"

#: Modules the eval path reaches at import time. ``harness`` and ``datalake`` are the library;
#: ``arms`` and ``grade`` are what they call.
EVAL_MODULES = (
    "governed_bi.eval.arms",
    "governed_bi.eval.datalake",
    "governed_bi.eval.grade",
    "governed_bi.eval.harness",
    "governed_bi.eval.report",
)

#: The scripts a person actually runs. Both were broken by a deletion and neither had a test.
DRIVERS = ("run_datalake_eval.py", "routing_recall.py", "regrade.py")


@pytest.mark.parametrize("name", EVAL_MODULES)
def test_the_eval_library_imports(name: str) -> None:
    """A module that does not import cannot be run, however green the unit tests are."""
    importlib.import_module(name)


@pytest.mark.parametrize("script", DRIVERS)
def test_the_eval_drivers_import(script: str) -> None:
    """Loaded by path, the way they are invoked.

    Only the module body runs — every driver guards its work behind ``if __name__ ==
    "__main__"`` and ``main()``, so nothing here reaches a database, a model or a corpus.
    """
    path = TOOLS / script
    assert path.exists(), f"{script} is named here but not in tools/"

    spec = importlib.util.spec_from_file_location(f"_driver_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main"), (
        f"{script} has no main(); this test would then be asserting nothing about the entry point"
    )


def test_the_datalake_reports_tokens_and_not_money() -> None:
    """``observed_spend`` is gone and ``observed_tokens`` replaces it.

    Asserted rather than assumed because the rename is what the import test above would have
    caught, and because "the batch cost $X" and "the batch used N tokens" are different claims:
    the first needs a price list this repository no longer maintains, and a stale row in the one
    it had overstated a measured run nine-fold.
    """
    from governed_bi.eval import datalake

    assert not hasattr(datalake, "observed_spend")
    assert hasattr(datalake, "observed_tokens")

    rows = [
        {
            "usage": [
                {"turn_index": 1, "stage": "guard", "input_tokens": 100, "output_tokens": 4},
                {"turn_index": 1, "stage": "agent_core", "input_tokens": 3000, "output_tokens": 90},
            ]
        }
    ]
    out = datalake.observed_tokens(rows)
    assert out["calls"] == 2
    assert out["input_tokens"] == 3100
    assert out["output_tokens"] == 94
    # Per stage, which is the question the agent/utility split raises and a single total cannot
    # answer. A row with no `stage` lands under `unattributed` rather than being dropped.
    assert set(out["by_stage"]) == {"guard", "agent_core"}
    assert out["by_stage"]["guard"]["input_tokens"] == 100
    assert "usd_total" not in out and "cost_est_usd" not in out


def test_an_unmeasured_token_count_does_not_become_zero_in_a_field() -> None:
    """A ``Measured`` in the unmeasured state totals as 0 and stays unmeasured on the row.

    The total is allowed to be a lower bound; the row is not allowed to lie. This is the one
    place `int(x or 0)` would have been wrong, and the summary is the one place it is safe.
    """
    from governed_bi.eval import datalake
    from governed_bi.register.quantity import Measured

    unmeasured = Measured.unmeasured("the provider reported nothing")
    rows = [{"usage": [{"turn_index": 1, "stage": "narrate", "input_tokens": unmeasured}]}]
    out = datalake.observed_tokens(rows)

    assert out["calls"] == 1, "the call happened and must be counted"
    assert out["input_tokens"] == 0, "a total may be a lower bound"
    assert rows[0]["usage"][0]["input_tokens"] is unmeasured, "the row itself is untouched"
