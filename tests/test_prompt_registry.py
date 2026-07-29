"""The prompt registry: v1 identity, fail-closed lookups, and text hashing.

Three things here are load-bearing rather than merely nice:

* **``v1`` is pinned by digest.** The call sites now derive their constants from
  the registry, so the two cannot silently disagree — but an in-place edit of
  ``v1`` would still redefine the baseline that every prior number was measured
  under. The pinned digests below make that edit a failing test that names the
  consequence, instead of a quiet reinterpretation of history. Repinning one is a
  deliberate act that discards the numbers taken under it, and has happened once
  (``sme_rules``; see the table).
* **An unknown stage or variant raises.** A silent fall back to ``v1`` is how a
  prompt experiment becomes a lie: the run reports a variant it never sent.
* **The hash covers the text, not just the id.** Otherwise an edited prompt
  masquerades as the prompt it replaced and two incomparable runs read as one
  experiment.
"""

from __future__ import annotations

import hashlib

import pytest

from governed_bi import prompts

#: sha256 of each stage's ``v1``, recorded from the module-level constants as they
#: stood before the registry existed. A mismatch means either the registry text
#: was edited or a call site stopped deriving from it. Both invalidate the
#: baseline; neither should be fixed by editing this table without saying so.
#:
#: ``sme_rules`` is the one entry that is **not** pre-registry text. Its original
#: v1 and v2 both forbade database queries outright while the runtime user message
#: in the same call invited a read-only probe, and 11 of 381 measured answers were
#: destroyed by the contradiction; both were deleted and the digest repinned to
#: the single replacement variant. Every number taken under the old text is
#: discarded, which is what licenses the repin.
V1_DIGESTS = {
    "agent_core": "12944b9758e09b8edf08667f11e6f59ac9b512d93ef63580e3a15f526ff7fe97",
    "schema_pick": "5d7f170fe6d12c96ceec36ef7fd1e69eb2396f52efedeaed691c756a8d8b8253",
    "narrator": "91886100a9010256574f6ca3bc0264726bf27117b0096cdf05934e8458b07824",
    "curator_phase_a": "207a2737d6d58d542f851ce49fba698b1e4dc19e0687bf1f0c0979d26556cba2",
    "curator_phase_b": "473708cc6ec6737defdf6318fbda0fd6653b167e19a060a4c5cbe0fc7ac665c5",
    "sme_rules": "c4c9fbff43d00c59f658485fe6b4fa8f790362206aa2388a40f889ab3aae67c0",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# v1 identity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stage", sorted(V1_DIGESTS))
def test_v1_is_byte_identical_to_its_pinned_digest(stage):
    assert _sha(prompts.get(stage, "v1").text) == V1_DIGESTS[stage]


def test_every_registered_stage_has_a_pinned_v1_digest():
    """A new stage must arrive with its baseline pinned, or its v1 is unguarded."""
    assert set(prompts.stages()) == set(V1_DIGESTS)


def test_the_live_call_site_constants_are_the_registry_text():
    """Derived, not copied: a divergence must be impossible, not merely detected."""
    from governed_bi.analyst.agent import SYSTEM_PROMPT
    from governed_bi.analyst.narrate import _NARRATOR_SYSTEM
    from governed_bi.curator.prompts import _PHASE_A_PROMPT, _PHASE_B_PROMPT
    from governed_bi.curator.sme import _SME_SYSTEM_RULES
    from governed_bi.retrieval.schema_router import SCHEMA_PICK_SYSTEM

    for stage, live in (
        ("agent_core", SYSTEM_PROMPT),
        ("schema_pick", SCHEMA_PICK_SYSTEM),
        ("narrator", _NARRATOR_SYSTEM),
        ("curator_phase_a", _PHASE_A_PROMPT),
        ("curator_phase_b", _PHASE_B_PROMPT),
        ("sme_rules", _SME_SYSTEM_RULES),
    ):
        assert live is prompts.get(stage, "v1").text, stage


def test_defaults_name_v1_for_every_stage():
    assert prompts.DEFAULTS == {s: "v1" for s in prompts.stages()}
    assert prompts.resolve(None) == prompts.DEFAULTS
    assert prompts.resolve({}) == prompts.DEFAULTS


def test_every_variant_carries_its_own_stage_and_id():
    """The dict key and the record must agree, or a lookup returns another
    stage's text under this stage's name."""
    for stage, by_id in prompts.REGISTRY.items():
        for variant_id, variant in by_id.items():
            assert (variant.stage, variant.variant) == (stage, variant_id)
            assert variant.text.strip()
            assert variant.rationale.strip()


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #


def test_an_unknown_variant_raises_and_names_the_valid_ids():
    with pytest.raises(KeyError) as err:
        prompts.get("agent_core", "v9")
    message = str(err.value)
    assert "v1" in message and "v2" in message and "v3" in message


def test_an_unknown_stage_raises_and_names_the_known_stages():
    with pytest.raises(KeyError) as err:
        prompts.get("sqlgen", "v1")
    assert "agent_core" in str(err.value)


def test_resolve_refuses_an_unknown_override_instead_of_defaulting():
    """The whole point: a typo must not resolve to v1 while the artifacts claim v9."""
    with pytest.raises(KeyError):
        prompts.resolve({"sqlgen": "v9"})
    with pytest.raises(KeyError):
        prompts.resolve({"agent_core": "v9"})


def test_resolve_keeps_the_other_stages_at_v1():
    resolved = prompts.resolve({"schema_pick": "v2"})
    assert resolved["schema_pick"] == "v2"
    assert resolved["agent_core"] == "v1"
    assert set(resolved) == set(prompts.stages())


def test_text_returns_the_selected_variant():
    assert prompts.text("schema_pick", {"schema_pick": "v2"}) == prompts.get(
        "schema_pick", "v2"
    ).text
    assert prompts.text("agent_core", {"schema_pick": "v2"}) == prompts.get(
        "agent_core", "v1"
    ).text


# --------------------------------------------------------------------------- #
# prompt_set_hash
# --------------------------------------------------------------------------- #


def test_hash_is_stable_and_independent_of_how_the_map_was_spelled():
    """A partial map, an empty map and the full default map all describe the same
    run, so they must not read as three different prompt sets."""
    assert prompts.prompt_set_hash() == prompts.prompt_set_hash({})
    assert prompts.prompt_set_hash() == prompts.prompt_set_hash(prompts.DEFAULTS)
    assert prompts.prompt_set_hash({"agent_core": "v1"}) == prompts.prompt_set_hash()


def test_hash_moves_when_a_variant_is_selected():
    assert prompts.prompt_set_hash({"schema_pick": "v2"}) != prompts.prompt_set_hash()
    assert prompts.prompt_set_hash({"agent_core": "v2"}) != prompts.prompt_set_hash(
        {"agent_core": "v3"}
    )


def test_hash_moves_when_a_variant_text_is_edited_in_place(monkeypatch):
    """The trap ``serve_config_hash``'s fixed field list already fell into: an id
    that stayed the same while the bytes changed. Editing v1 must be visible."""
    before = prompts.prompt_set_hash()
    edited = dict(prompts.REGISTRY["agent_core"])
    edited["v1"] = prompts.PromptVariant(
        stage="agent_core",
        variant="v1",
        text=prompts.get("agent_core", "v1").text + " one more sentence.",
        rationale="edited in place",
    )
    monkeypatch.setitem(prompts.REGISTRY, "agent_core", edited)
    assert prompts.prompt_set_hash() != before


def test_hash_does_not_depend_on_dict_insertion_order():
    a = prompts.prompt_set_hash({"agent_core": "v2", "schema_pick": "v2"})
    b = prompts.prompt_set_hash({"schema_pick": "v2", "agent_core": "v2"})
    assert a == b


# --------------------------------------------------------------------------- #
# CLI overrides
# --------------------------------------------------------------------------- #


def test_cli_overrides_parse_repeated_pairs():
    assert prompts.parse_cli_overrides(["schema_pick=v2", "agent_core=v3"]) == {
        "schema_pick": "v2",
        "agent_core": "v3",
    }
    assert prompts.parse_cli_overrides([" schema_pick = v2 "]) == {"schema_pick": "v2"}
    assert prompts.parse_cli_overrides(None) == {}


@pytest.mark.parametrize(
    "spec", ["schema_pick", "=v2", "schema_pick=", "", "schema_pick:v2"]
)
def test_a_malformed_pair_is_a_usage_error(spec):
    with pytest.raises(ValueError):
        prompts.parse_cli_overrides([spec])


def test_cli_overrides_validate_against_the_registry():
    with pytest.raises(KeyError):
        prompts.parse_cli_overrides(["sqlgen=v9"])
    with pytest.raises(KeyError):
        prompts.parse_cli_overrides(["agent_core=v9"])


def test_the_same_stage_twice_with_different_variants_raises():
    """Last-wins would drop one of two contradictory flags and report a prompt set
    the run did not send."""
    with pytest.raises(ValueError, match="twice"):
        prompts.parse_cli_overrides(["agent_core=v2", "agent_core=v3"])
    # Repeating the SAME value is harmless and stays legal.
    assert prompts.parse_cli_overrides(["agent_core=v2", "agent_core=v2"]) == {
        "agent_core": "v2"
    }


# --------------------------------------------------------------------------- #
# [prompts] in TOML
# --------------------------------------------------------------------------- #


def test_toml_prompts_table_is_loaded(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[prompts]\nschema_pick = "v2"\n', encoding="utf-8")
    settings = load_settings(cfg, apply_local=False)
    assert settings.prompt_variants == {"schema_pick": "v2"}


def test_a_bad_toml_variant_fails_at_load_not_mid_run(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[prompts]\nagent_core = "v9"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="agent_core"):
        load_settings(cfg, apply_local=False)


def test_a_bad_toml_stage_fails_at_load(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[prompts]\nsqlgen = "v1"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="sqlgen"):
        load_settings(cfg, apply_local=False)


def test_no_prompts_table_means_every_stage_on_v1(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text('[runtime]\nenvironment = "dev"\n', encoding="utf-8")
    settings = load_settings(cfg, apply_local=False)
    assert settings.prompt_variants == {}
    assert prompts.resolve(settings.prompt_variants) == prompts.DEFAULTS


def test_the_repo_config_still_resolves():
    """The checked-in governed_bi.toml must load: a [prompts] typo there would take
    down every entry point at startup, which is the point of validating it early."""
    from governed_bi.config import load_settings

    settings = load_settings(apply_local=False)
    assert prompts.resolve(settings.prompt_variants)
