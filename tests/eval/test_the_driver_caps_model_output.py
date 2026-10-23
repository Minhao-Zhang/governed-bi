"""The driver sends the served app's output caps on every model it builds."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"


def _driver() -> Any:
    spec = importlib.util.spec_from_file_location("driver_caps_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_agent_and_utility_models_carry_their_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    from governed_bi.model import provider as provider_mod
    from governed_bi.registry.settings import setting_default

    built: dict[str, dict[str, Any]] = {}

    def fake_chat_model(model_id: str, **kwargs: Any) -> object:
        built[kwargs["surface"]] = kwargs
        return object()

    monkeypatch.setattr(provider_mod, "chat_model", fake_chat_model)
    args = argparse.Namespace(
        model="m", effort="xhigh", timeout=240, max_retries=8,
        utility_model="u", utility_effort="medium", embed=False, embedding_model=None,
    )
    _driver()._build_models(args)

    assert built["agent"]["max_output_tokens"] == setting_default("llm_max_output_tokens") == 16_000
    assert built["utility"]["max_output_tokens"] == setting_default("llm_utility_max_output_tokens") == 2_000
