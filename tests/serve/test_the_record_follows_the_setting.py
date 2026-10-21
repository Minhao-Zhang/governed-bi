"""Six settings whose behaviour moved while ``settings_resolved`` stood still.

Every one of these had a real consumer. The code read correctly at the reader and the wire
back to the record was missing, which is why a static sweep of declarations missed them — only the
artifacts showed them. The evidence is the six configs in ``runs/eval/``, 1,351 rows each, all
on corpus ``86ed1dbf…`` (``../BIRD-corpus`` @ ``30872d3``):

=========================== ============================================================
setting what all 8,106 rows said
=========================== ============================================================
``llm_reasoning_effort`` ``null`` — while ``runs/eval/driver_v4.log:6`` records
                             ``effort=high``
``llm_utility_provider`` ``"openai"`` — beside ``llm_provider: "custom:007df842"``
``embedding_provider`` ``"openai"`` — beside
                             ``embedding_model: "proxy:text-embedding-3-large"``
``chat_model`` ``null`` on run1 / run2 / v3-pinned / v3-fold; the value was
                             in ``llm_model``, which ``SETTING_REGISTER`` does not declare
three timeout env vars ``120.0`` / ``1200.0`` / ``40``, whatever the environment said
``sqlglot_version`` **absent**, and likewise ``the retired threshold`` and ``the retired budget``
``prompt_set`` ``null`` on v2, v3, v4 and v5 — the four configs whose entire
                             setting under test is a prompt variant
=========================== ============================================================

**Authoring rule, applied throughout: assert the value, never the presence.** ``None`` is
present, and eight tests in this repository were found asserting exactly that. Each value
below is compared against something this file supplied — a base URL it chose, an effort it
requested, a number it exported — or against an independent resolution of the same fact
(``importlib.metadata`` for the sqlglot version), never against the expression under test.

Network-free: the chat models in this file are scripted stand-ins with the attributes
the record reads.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.registry.settings import setting_default, setting_names
from governed_bi.serve.session import from_assets

#: Every variable the recording side now consults, cleared per test — a developer's own
#: shell must not be able to make these pass or fail for the wrong reason.
ENV_VARS = (
    "GOVERNED_BI_RAIL_NODE_TIMEOUT_S",
    "GOVERNED_BI_AGENT_NODE_TIMEOUT_S",
    "GOVERNED_BI_AGENT_RECURSION_LIMIT",
)


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _session(assets: dict[str, Any], **kwargs: Any):
    return from_assets(
        list(assets.values()),
        connector=None,
        policy=kwargs.pop("policy", None) or GovernancePolicy(guard_rules_enabled={}),
        db_id="ops_b",
        corpus_content_hash_="c",
        **kwargs,
    )


# ── 1. an embedder id names its gateway ───────────────────────────────────────


def test_an_unqualified_embedder_refuses_rather_than_naming_a_gateway():
    """``ports.py`` requires ``Embedder.model`` to be provider-qualified. Absent that, refuse.

    Guessing is the defect: the six configs recorded ``"openai"`` because something plausible was
    cheaper than something true.
    """
    from governed_bi.model.embedder import embedding_provider, embedding_settings

    assert embedding_provider("openai:text-embedding-3-large") == "openai"
    assert embedding_provider("deterministic:probe") == "deterministic"

    class Unqualified:
        model = "text-embedding-3-large"
        dimensions = 64

    with pytest.raises(ValueError, match="provider-qualified"):
        embedding_settings(Unqualified())


# ── 3. one spelling of the model, and it is the declared one ──────────────────


def test_the_model_is_recorded_under_the_declared_name_and_no_other(two_schema_assets):
    """``chat_model`` carries it; ``llm_model`` is gone.

    ``llm_model`` was written into ``settings_resolved`` and never declared, so it sat outside
    ``setting_names()``. On run1, run2, v3-pinned and v3-fold it was the *only* field
    carrying the model — which put the one value that could have told those configs apart
    outside the set of settings that affect results.
    """
    from governed_bi.serve.scripted_model import ScriptedChatModel

    class Named(ScriptedChatModel):
        model_name: str = "gpt-5.6-luna"

    settings = _session(two_schema_assets, agent_model=Named(responses=[])).settings_resolved

    assert settings["chat_model"] == "gpt-5.6-luna"
    assert "llm_model" not in settings
    assert "chat_model" in setting_names()
    # No second spelling anywhere: every key the record publishes is a declared setting, which
    # is the property `llm_model` broke.
    assert set(settings) - {"config_hash"} <= setting_names()


def test_the_two_model_settings_cannot_disagree_about_one_client(two_schema_assets):
    """A lone agent model is both surfaces, and both must name it identically."""
    from governed_bi.serve.scripted_model import ScriptedChatModel

    class Named(ScriptedChatModel):
        model_name: str = "gpt-5.6-luna"

    settings = _session(two_schema_assets, agent_model=Named(responses=[])).settings_resolved
    assert settings["chat_model"] == settings["llm_utility_model"] == "gpt-5.6-luna"


def test_the_utility_effort_is_recorded_apart_from_the_agents(two_schema_assets):
    """The served config runs the utility surface at its own effort, and the row must say so.

    With no field for it, an eval that reused the agent's effort and the served app that did
    not recorded identical ``settings_resolved``.
    """
    from governed_bi.serve.scripted_model import ScriptedChatModel

    class Agent(ScriptedChatModel):
        model_name: str = "gpt-6-luna"
        reasoning_effort: str = "xhigh"

    class Utility(ScriptedChatModel):
        model_name: str = "gpt-6-luna"
        reasoning_effort: str = "medium"

    settings = _session(
        two_schema_assets, agent_model=Agent(responses=[]), utility_model=Utility(responses=[])
    ).settings_resolved
    assert settings["llm_reasoning_effort"] == "xhigh"
    assert settings["llm_utility_reasoning_effort"] == "medium"
    assert "llm_utility_reasoning_effort" in setting_names()


# ── 4. the three environment variables ────────────────────────────────────────


@pytest.mark.parametrize(
    ("setting", "raw", "expected"),
    [
        ("rail_node_timeout_s", "7.5", 7.5),
        ("agent_node_timeout_s", "99", 99.0),
        ("agent_recursion_limit", "12", 12),
    ],
)
def test_an_env_var_that_moves_behaviour_moves_the_record(
    monkeypatch, two_schema_assets, setting, raw, expected
):
    """Set the variable, and the artifact says what the run will do.

    All three are read env-first by ``serve/graph.py`` and ``serve/nodes/agent_core.py``, and
    ``_resolved_settings`` built the record from ``setting_default()`` alone — so the record
    published 120.0 / 1200.0 / 40 whatever was exported.
    """
    from governed_bi.registry.settings import env_overrides

    var = env_overrides()[setting]
    monkeypatch.setenv(var, raw)

    settings = _session(two_schema_assets).settings_resolved
    assert settings[setting] == expected
    # Not the default, or the assertion above would hold for a record that ignored the
    # environment entirely.
    assert settings[setting] != setting_default(setting)
    # And the type is the reader's, because `_settings_resolved_check` compares by `repr`: 40 and
    # 40.0 are two configurations.
    assert type(settings[setting]) is type(setting_default(setting))


def test_the_record_and_the_reader_resolve_the_env_var_to_the_same_value(
    monkeypatch, two_schema_assets
):
    """Recording it is only half. The number in the artifact must be the number in force.

    The readers are driven here, not re-implemented: ``graph._node_timeout`` for the rail,
    ``agent_core._agent_node_timeout`` and ``agent_core._recursion_limit`` for the agent.
    """
    from governed_bi.serve.graph import _node_timeout
    from governed_bi.serve.nodes.agent_core import _agent_node_timeout, _recursion_limit

    monkeypatch.setenv("GOVERNED_BI_RAIL_NODE_TIMEOUT_S", "7.5")
    monkeypatch.setenv("GOVERNED_BI_AGENT_NODE_TIMEOUT_S", "99")
    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "12")

    settings = _session(two_schema_assets).settings_resolved
    state = {"settings_resolved": dict(settings)}

    assert settings["rail_node_timeout_s"] == _node_timeout("guard") == 7.5
    assert settings["agent_node_timeout_s"] == _agent_node_timeout(state) == 99.0
    assert settings["agent_recursion_limit"] == _recursion_limit(state) == 12


def test_a_blank_variable_is_unset_and_a_bad_one_refuses(monkeypatch, two_schema_assets):
    """The two parsing rules the readers already had, kept in step.

    Blank falls through to the setting (``graph.py`` tests ``if raw``, ``agent_core.py`` tests
    ``strip() != ""``). An unreadable value raises at session construction rather than
    mid-run, where the run is lost anyway and the message names the variable.
    """
    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "   ")
    assert _session(two_schema_assets).settings_resolved["agent_recursion_limit"] == 40

    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "forty")
    with pytest.raises(ValueError, match="GOVERNED_BI_AGENT_RECURSION_LIMIT"):
        _session(two_schema_assets)


def test_every_declared_env_var_is_one_a_reader_reads():
    """An ``env_var`` in the register that nothing consults is the defect inverted.

    The register would then publish an override no run can perform — the same false claim as
    a setting with no reader, in the other direction. Checked against the reader sources, which
    is where the literal lives.
    """
    import pathlib

    from governed_bi.registry import settings as settings_module
    from governed_bi.registry.settings import env_overrides

    root = pathlib.Path(__file__).resolve().parents[2]
    src = root / "src" / "governed_bi" / "serve"
    read_by = (src / "graph.py").read_text(encoding="utf-8") + (
        src / "nodes" / "agent_core.py"
    ).read_text(encoding="utf-8")
    settings_source = pathlib.Path(settings_module.__file__).read_text(encoding="utf-8")

    declared = env_overrides()
    assert set(declared) == set(settings_module.setting_names())
    specials = [
        var for name, var in declared.items() if var != f"GOVERNED_BI_{name.upper()}"
    ]
    missing = sorted(var for var in specials if var not in settings_source)
    assert missing == [], f"a declared variable is not named in the settings module: {missing}"
    assert 'f"GOVERNED_BI_{name.upper()}"' in settings_source

    behavioural = {"rail_node_timeout_s", "agent_node_timeout_s", "agent_recursion_limit"}
    unread = sorted(name for name in behavioural if name not in read_by)
    assert unread == [], f"a bound is declared and read by no node: {unread}"


# ── 5. nothing is absent ──────────────────────────────────────────────────────


def test_no_declared_setting_is_missing_from_the_record(two_schema_assets):
    """Absence was the defect, and the drift check cannot see it.

    ``_resolved_settings`` dropped every ``UNSET`` setting and re-added three, so
    ``sqlglot_version``, ``the retired threshold`` and ``the retired budget`` were absent from all 8,106 rows.
    ``measure/checks.py::_settings_resolved_check`` reads with ``row.get(key)`` — a key missing
    from every row compares equal to itself and the check passes on a configuration it never
    saw.
    """
    settings = _session(two_schema_assets).settings_resolved

    assert set(setting_names()) <= set(settings)
    assert "config_hash" in settings
    assert "sqlglot_version" in settings


def test_the_sqlglot_version_recorded_is_the_one_installed(two_schema_assets):
    """*"Resolved from installed metadata at config time; UNSET so it cannot be silently
    absent"* — its own note, and it was silently absent on every row.

    Compared against ``importlib.metadata`` resolved here, not against
    ``govern.functions.sqlglot_version()``, which would assert the resolver against itself.
    Canonical function names are release-dependent and the ADR 0006 allowlist is keyed on
    them, so this is which vocabulary the governance layer was enforcing.
    """
    from importlib.metadata import version

    installed = version("sqlglot")
    settings = _session(two_schema_assets).settings_resolved
    assert settings["sqlglot_version"] == installed
    assert settings["sqlglot_version"] not in (None, "", "unknown")
    # And it is a version, not the tested-major constant beside it.
    assert installed.split(".")[0] != settings["sqlglot_version"]


def test_an_uncalibrated_setting_records_null_and_a_set_one_records_its_value(two_schema_assets):
    """``UNSET`` becomes ``null`` — "this run had no calibrated value", which is a
    measurement — and a policy that carries a bound is recorded carrying it.

    Both halves. Null alone would pass for a record that still ignored the policy.
    """
    default_policy = _session(two_schema_assets, policy=GovernancePolicy()).settings_resolved
    assert default_policy["guard_rules_enabled"] is None

    bounded = _session(
        two_schema_assets,
        policy=GovernancePolicy(guard_rules_enabled={"g_length"}),
    ).settings_resolved
    assert bounded["guard_rules_enabled"] == ["g_length"]


def test_reading_an_uncalibrated_setting_still_refuses(two_schema_assets):
    """Recording ``null`` must not hand a node a value nobody chose.

    ``int_setting`` falls a ``None`` through to ``setting_default``, which is still ``UNSET`` and
    still raises. If recording the absence had also made it readable, this fix would have
    disabled the refusal that ``registry/settings.py`` exists to make.
    """
    from governed_bi.serve.runtime import int_setting

    settings = _session(two_schema_assets, policy=GovernancePolicy()).settings_resolved
    with pytest.raises(ValueError, match="UNSET"):
        int_setting({"settings_resolved": dict(settings)}, "guard_rules_enabled")


# ── 6. which prompt variant produced which digest ─────────────────────────────


def test_the_prompt_variant_is_named_and_not_only_hashed(two_schema_assets):
    """v2, v3, v4 and v5 differ by prompt wording and nothing else, and all four recorded
    ``prompt_set: null``.

    They were *distinguishable* — ``prompt_set_hash`` is on the row and does differ. They
    were not *nameable*: nothing in an artifact said which variant produced which digest,
    so reading the six configs could tell you two of them were not the same setting under test and not
    which setting under test either one was.

    The two must move **together**, from one selection, or the record carries a digest of
    one prompt set beside the name of another.
    """
    def config(variant: str):
        session = _session(two_schema_assets, prompt_variants={"analyst": variant})
        return session.settings_resolved["prompt_set"], session.prompt_set_hash

    v4_set, v4_hash = config("v4")
    v5_set, v5_hash = config("v5")

    # Named, not merely present. The variant this test asked for is the variant recorded.
    assert v4_set["analyst"] == "v4"
    assert v5_set["analyst"] == "v5"
    # Total over the registry, so a stage nobody overrode still says which variant it ran.
    assert v4_set["narrate"] == "v1"

    # The digests are the ones the four measured configs carry, so this is pinned to the real
    # prompt text rather than to whatever it happens to be today.
    assert v4_hash == "ca8905d1182d35eb"
    assert v5_hash == "9c29f1e14b31885b"
    assert (v4_set, v4_hash) != (v5_set, v5_hash)
    # ...and only the overridden stage moved, or "they differ" would hold for a resolver
    # that never repeats itself.
    assert {k for k in v4_set if v4_set[k] != v5_set[k]} == {"analyst"}


def test_the_recorded_prompt_set_is_the_one_the_hash_was_taken_over(two_schema_assets):
    """One selection behind both fields. The ``Session`` docstring already requires it:
    a ``prompt_set_hash`` computed from a different mapping records a setting under test the run
    did not send.

    Asserted by recomputing the digest from the *recorded names*, which fails the moment
    the two are derived from different inputs.
    """
    from governed_bi.registry.prompts import prompt_set_hash as digest_of

    session = _session(two_schema_assets, prompt_variants={"analyst": "v3"})
    recorded = session.settings_resolved["prompt_set"]
    assert digest_of(recorded) == session.prompt_set_hash == "32684b0db595aae6"


def test_an_undeclared_prompt_name_refuses_at_session_build(two_schema_assets):
    """Same refusal ``prompt_set_hash`` already makes, and it must not be softened into a
    silently dropped override — that would record a prompt set the run did not use."""
    with pytest.raises(KeyError, match="analyst_system"):
        _session(two_schema_assets, prompt_variants={"analyst_system": "v4"})


def test_the_environment_this_suite_runs_in_did_not_supply_the_answers():
    """The autouse fixture's own claim, asserted once rather than trusted."""
    assert [v for v in ENV_VARS if os.environ.get(v)] == []
