"""The eval driver takes the utility model and effort from the served config when unset.

Without it, a run with no ``--utility-model`` reused the agent model at the agent's effort for
the scope gate and the facet rewriters, while the served app ran them on
``GOVERNED_BI_UTILITY_MODEL`` at ``GOVERNED_BI_UTILITY_MODEL_EFFORT``. The arm registered as
"the configuration ``.env`` ships" therefore measured another one.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from governed_bi.api.graph_app import UTILITY_MODEL_EFFORT_VAR, UTILITY_MODEL_VAR

TOOL = Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"


def _driver() -> Any:
    spec = importlib.util.spec_from_file_location("driver_utility_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(utility_model=kw.get("model"), utility_effort=kw.get("effort"))


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UTILITY_MODEL_VAR, "gpt-6-luna")
    monkeypatch.setenv(UTILITY_MODEL_EFFORT_VAR, "medium")


def test_an_unset_flag_takes_the_served_model_and_effort(served) -> None:
    args = _args()
    assert _driver().apply_served_utility_default(args) == UTILITY_MODEL_VAR
    assert (args.utility_model, args.utility_effort) == ("gpt-6-luna", "medium")


def test_an_explicit_model_wins_and_takes_no_effort_from_the_env(served) -> None:
    args = _args(model="other-model")
    assert _driver().apply_served_utility_default(args) is None
    assert (args.utility_model, args.utility_effort) == ("other-model", None)


def test_an_explicit_effort_is_kept_beside_the_served_model(served) -> None:
    args = _args(effort="low")
    _driver().apply_served_utility_default(args)
    assert (args.utility_model, args.utility_effort) == ("gpt-6-luna", "low")


def test_no_served_utility_model_leaves_the_agent_model_in_place(monkeypatch) -> None:
    monkeypatch.delenv(UTILITY_MODEL_VAR, raising=False)
    monkeypatch.setenv(UTILITY_MODEL_EFFORT_VAR, "medium")
    args = _args()
    assert _driver().apply_served_utility_default(args) is None
    assert (args.utility_model, args.utility_effort) == (None, None)
