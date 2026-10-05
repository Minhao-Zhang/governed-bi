"""A probe run whose verdicts failed open is not a measurement, and its exit code must say so.

At concurrency 8 a full v2 pass failed 984 of 1,351 rows open against the rate limit and still
exited 0, and ``tools/shadow_replay.py`` then printed a projection that never mentioned them.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _tool(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_probe_defaults_to_one_call_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    seen: dict[str, Any] = {}

    class Stop(Exception):
        pass

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> None:
        seen.update({a.dest: a.default for a in self._actions})
        raise Stop

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(Stop):
        _tool("bi_scope_probe").main([])
    assert seen["concurrency"] == 1


def test_shadow_replay_exits_nonzero_when_a_scope_verdict_failed_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = _tool("shadow_replay")
    artifact = tmp_path / "arm.jsonl"
    artifact.write_text(
        json.dumps({"question_id": "q1", "outcome": "answered", "correct": True}) + "\n"
    )
    verdicts = tmp_path / "scope.jsonl"
    verdicts.write_text(json.dumps({"question_id": "q1", "outcome": "error_failed_open"}) + "\n")

    from governed_bi.eval import shadow

    def only_scope(rows, questions):  # noqa: ANN001
        return shadow.ShadowGate(shadow.DETERMINISTIC_GUARD, shadow.Counterfactual.replay, frozenset(), "stub")

    monkeypatch.setattr(shadow, "deterministic_guard_gate", only_scope)
    monkeypatch.setattr(shadow, "abstention_gate", lambda rows: only_scope(rows, None))
    monkeypatch.setattr(replay, "_questions", lambda path: {})
    code = replay.main([str(artifact), "--scope-verdicts", str(verdicts)])
    assert code == 1
