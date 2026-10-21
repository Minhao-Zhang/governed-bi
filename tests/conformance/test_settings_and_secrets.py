"""Settings resolve once, from code and the environment. Secrets stay out of the record."""

from __future__ import annotations

import json

import pytest

from governed_bi.api.graph_app import resolve_run as server_resolve
from governed_bi.boot import resolve_settings as eval_resolve
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.registry.record import project
from governed_bi.registry.settings import config_hash, reset_settings, resolve_settings
from governed_bi.serve.session import _resolved_settings


def test_changing_one_setting_changes_the_recorded_config_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOVERNED_BI_ROUTE_TOP_N", raising=False)
    reset_settings()
    first = resolve_settings()
    monkeypatch.setenv("GOVERNED_BI_ROUTE_TOP_N", "9")
    second = resolve_settings()
    assert first["route_top_n"] == 3
    assert second["route_top_n"] == 9
    assert first["config_hash"] != second["config_hash"]
    assert second["config_hash"] == config_hash({k: v for k, v in second.items() if k != "config_hash"})
    reset_settings()


def test_the_server_and_the_eval_driver_resolve_the_same_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text('OPENAI_API_KEY=sk-same\nLANGSMITH_API_KEY=ls-same\n', encoding="utf-8")
    monkeypatch.setattr("governed_bi.credentials.DOTENV", dotenv)
    # The loader writes the fake keys into os.environ; record their prior state so teardown
    # restores it, or later tests see a key and call OpenAI with it.
    for name in ("OPENAI_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("GOVERNED_BI_ROUTE_TOP_N", "4")
    reset_settings()
    server = server_resolve()
    reset_settings()
    driver = eval_resolve()
    assert server["config_hash"] == driver["config_hash"]
    assert server["route_top_n"] == driver["route_top_n"] == 4
    assert server["chat_model"] == driver["chat_model"] == "gpt-6-luna"
    reset_settings()


def test_a_setting_in_dotenv_stops_startup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("GOVERNED_BI_MODEL=x\n", encoding="utf-8")
    monkeypatch.setattr("governed_bi.credentials.DOTENV", dotenv)
    reset_settings()
    with pytest.raises(RuntimeError, match="GOVERNED_BI_MODEL"):
        server_resolve()
    with pytest.raises(RuntimeError, match="GOVERNED_BI_MODEL"):
        eval_resolve()
    reset_settings()


def test_a_secret_is_absent_from_the_config_hash_and_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-fake-not-recorded"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    reset_settings()
    recorded = _resolved_settings(GovernancePolicy(guard_rules_enabled={}))
    recorded["config_hash"] = config_hash(recorded)

    def extract(state, name):
        if name == "settings_resolved":
            return state.get("settings_resolved")
        return None

    turn = project({"settings_resolved": recorded}, extract=extract)
    blob = json.dumps(turn, default=str)
    assert secret not in blob
    assert secret not in turn["settings_resolved"]["config_hash"]
    reset_settings()
