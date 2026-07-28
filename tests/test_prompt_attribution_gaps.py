"""Two attribution holes an adversarial review found after the registry landed.

Both had the same shape: the prompt id was stamped somewhere, so the mechanism
looked covered, but the stamp and the bytes actually sent came from different
places. That is worse than no stamp, because a wrong attribution gets quoted.

1. The curator and SME producers re-derived config with `load_settings()` instead
   of using the Settings they were handed, so a corpus built with `--prompt` was
   recorded under the TOML's prompt set.
2. A `--resume` under a different prompt set only warned. `_merge_resume_manifest`
   keeps the ORIGINAL manifest's top-level knobs, and the ledger reads only those,
   so a half-v1/half-v2 directory reported itself as a clean v1 run.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.eval.run_datalake import _check_resume_manifest
from governed_bi.prompts import prompt_set_hash, resolve

# --------------------------------------------------------------------------- #
# 1. The producer stamps the Settings it was given
# --------------------------------------------------------------------------- #


def test_curator_uses_the_callers_settings_not_a_fresh_load(monkeypatch):
    from governed_bi.curator import pipeline

    def _boom(*a, **kw):
        raise AssertionError(
            "curator re-loaded settings instead of using the caller's; the run "
            "record would be stamped with the TOML's prompt set, not the one the "
            "agent actually ran on"
        )

    monkeypatch.setattr("governed_bi.config.load_settings", _boom)
    mine = replace(
        Settings.for_env(Environment.dev), prompt_variants={"agent_core": "v2"}
    )
    assert pipeline._settings_or_load(mine) is mine


def test_curator_falls_back_to_a_load_only_when_given_nothing(monkeypatch):
    from governed_bi.curator import pipeline

    sentinel = Settings.for_env(Environment.dev)
    monkeypatch.setattr("governed_bi.config.load_settings", lambda **kw: sentinel)
    assert pipeline._settings_or_load(None) is sentinel


def test_curator_fallback_survives_an_unloadable_config(monkeypatch):
    from governed_bi.curator import pipeline

    def _boom(**kw):
        raise RuntimeError("no config")

    monkeypatch.setattr("governed_bi.config.load_settings", _boom)
    # A missing config must not crash curation; it degrades to no run record.
    assert pipeline._settings_or_load(None) is None


def test_sme_uses_the_callers_settings_not_a_fresh_load(monkeypatch):
    from governed_bi.curator.sme import SimulatedSme

    def _boom(*a, **kw):
        raise AssertionError("SME re-loaded settings instead of using the caller's")

    monkeypatch.setattr("governed_bi.config.load_settings", _boom)
    mine = replace(
        Settings.for_env(Environment.dev), prompt_variants={"agent_core": "v2"}
    )
    sme = SimulatedSme(chat=object(), brief="brief", settings=mine)
    assert sme._resolved_settings() is mine


def test_the_stamp_and_the_text_come_from_one_map():
    """The record stamps the prompt set the caller actually ran on.

    Previously this asserted only its own PRECONDITION — that two Settings hash
    differently — and stopped there; the test it was a precondition for was never
    written (AUDIT T2). The regression it should catch: a producer that stamps from
    a re-loaded Settings records the TOML's prompt set instead of the one in force.
    """
    from governed_bi.analyst.run_log import FinalizeCtx, build_metadata_record

    caller = replace(
        Settings.for_env(Environment.dev), prompt_variants={"agent_core": "v2"}
    )
    toml_default = Settings.for_env(Environment.dev)
    caller_hash = prompt_set_hash(resolve(caller.prompt_variants))
    default_hash = prompt_set_hash(resolve(toml_default.prompt_variants))
    assert caller_hash != default_hash, "the two configurations must be distinguishable"

    class _Answer:
        provenance: dict = {}
        sql = None
        text = None
        tier = None
        safety_clearance = False
        semantic_assurance = None
        escalation = None
        result = None

    ctx = FinalizeCtx(
        settings=caller,
        run_id="r1",
        thread_id="t1",
        n_human=1,
        model="m",
        serve_path="agent",
        token_usage=[],
        outcome="finalize",
        append=False,
        question="q",
    )
    record = build_metadata_record(
        _Answer(), ctx=ctx, provenance={"prompt_set_hash": caller_hash, "turn_id": "t1:1"}
    )
    assert record["prompt_set_hash"] == caller_hash
    assert record["prompt_set_hash"] != default_hash


# --------------------------------------------------------------------------- #
# 2. Resuming under a different prompt set is fatal
# --------------------------------------------------------------------------- #


def _manifest(tmp_path, **kw):
    import json

    base = {
        "split": "test",
        "model": "gpt-5.6-luna",
        "prompt_set_hash": "aaaa1111",
        "route_top_k": 10,
        "route_llm_pick": True,
        "schema_pick_max_columns": 12,
        "use_embedder": True,
        "skip_agent": False,
        "git_sha": "abc123",
    }
    base.update(kw)
    (tmp_path / "manifest.json").write_text(json.dumps(base), encoding="utf-8")
    return dict(base)


def test_resuming_under_a_different_prompt_set_is_fatal(tmp_path):
    expected = _manifest(tmp_path)
    expected["prompt_set_hash"] = "bbbb2222"
    with pytest.raises(RuntimeError, match="prompt set"):
        _check_resume_manifest(tmp_path, expected)


def test_resuming_under_the_same_prompt_set_is_allowed(tmp_path):
    expected = _manifest(tmp_path)
    _check_resume_manifest(tmp_path, expected)  # must not raise


def test_a_non_prompt_knob_still_only_warns(tmp_path, capsys):
    # The other knobs stay a warning on purpose: a reader can see them in the
    # manifest and judge. Escalating everything would make resume unusable.
    expected = _manifest(tmp_path)
    expected["route_top_k"] = 3
    _check_resume_manifest(tmp_path, expected)
    assert "changed knobs" in capsys.readouterr().out


def test_a_prompt_set_absent_from_the_prior_manifest_does_not_fire(tmp_path):
    # A run directory predating prompt attribution records no hash. That is a
    # missing measurement, not a conflict, and the ledger flags it separately.
    expected = _manifest(tmp_path, prompt_set_hash=None)
    expected["prompt_set_hash"] = "bbbb2222"
    _check_resume_manifest(tmp_path, expected)  # must not raise
