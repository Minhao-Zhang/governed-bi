"""Six knobs whose behaviour moved while ``knobs_resolved`` stood still.

Every one of these had a real consumer. The code read correctly at the reader and the wire
back to the record was missing, which is why none of them is visible to
``tools/check_declared_is_consumed.py`` and all five survived a static sweep — only the
artifacts showed them. The evidence is the six arms in ``runs/eval/``, 1,351 rows each, all
on corpus ``86ed1dbf…`` (``../BIRD-corpus`` @ ``30872d3``):

===========================  ============================================================
knob                         what all 8,106 rows said
===========================  ============================================================
``llm_reasoning_effort``     ``null`` — while ``runs/eval/driver_v4.log:6`` records
                             ``effort=high``
``llm_utility_provider``     ``"openai"`` — beside ``llm_provider: "custom:007df842"``
``embedding_provider``       ``"openai"`` — beside
                             ``embedding_model: "proxy:text-embedding-3-large"``
``chat_model``               ``null`` on run1 / run2 / v3-pinned / v3-fold; the value was
                             in ``llm_model``, which ``KNOB_REGISTER`` does not declare
three timeout env vars       ``120.0`` / ``1200.0`` / ``40``, whatever the environment said
``sqlglot_version``          **absent**, and likewise ``negative_tau`` and ``cost_budget``
``prompt_set``               ``null`` on v2, v3, v4 and v5 — the four arms whose entire
                             treatment is a prompt variant
===========================  ============================================================

**Authoring rule, applied throughout: assert the value, never the presence.** ``None`` is
present, and eight tests in this repository were found asserting exactly that. Each value
below is compared against something this file supplied — a base URL it chose, an effort it
requested, a number it exported — or against an independent resolution of the same fact
(``importlib.metadata`` for the sqlglot version), never against the expression under test.

Network-free. ``get_proxy_credentials`` is stubbed so the *real* ``build_chat_model`` runs:
a hand-built stand-in for the client would assert our idea of what the gateway returns, and
the whole defect was that the real client does not look the way the reader assumed.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.knobs import (
    comparability_keys,
    knob_default,
    knob_names,
    resume_drift_keys,
)
from governed_bi.serve.session import from_assets

#: A host this file owns, so ``llm_provider``'s digest is a fact the test supplied rather
#: than a constant copied out of the implementation.
PROXY_BASE = "https://proxy.invalid"

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


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch):
    """The real proxy builders, with only the Secrets Manager lookup stubbed."""
    from governed_bi.model import proxy_gateway

    monkeypatch.setenv("GOVERNED_BI_PROXY_SECRET", "a-name-the-test-supplied")
    monkeypatch.setattr(
        proxy_gateway, "get_proxy_credentials", lambda *a, **k: ("not-a-key", PROXY_BASE)
    )
    proxy_gateway._reset_caches()
    yield proxy_gateway
    proxy_gateway._reset_caches()


def _session(assets: dict[str, Any], **kwargs: Any):
    return from_assets(
        list(assets.values()),
        connector=None,
        policy=kwargs.pop("policy", None) or GovernancePolicy(guard_rules_enabled={}),
        db_id="ops_b",
        corpus_content_hash_="c",
        **kwargs,
    )


# ── 1. the effort the client will actually send ───────────────────────────────


def test_the_recorded_effort_is_the_one_the_proxy_client_carries(proxy, two_schema_assets):
    """``high`` requested, ``high`` recorded — on the client the eval driver actually builds.

    ``session`` resolved this with ``getattr(agent_model, "reasoning_effort", None)``, which
    is only OpenAI's spelling. ``build_chat_model`` folds the effort into ``extra_body`` and
    returns a plain ``ChatOpenAI``, so the attribute is ``None`` and the ``if effort:`` branch
    never ran.
    """
    model = proxy.build_chat_model(llm_model="Claude-Opus-4.8", reasoning_effort="high")

    # The premise, stated so this cannot quietly become a test of the easy path: the client
    # really does carry no such attribute, and the envelope really does carry the effort.
    assert not getattr(model, "reasoning_effort", None)

    knobs = _session(two_schema_assets, agent_model=model).knobs_resolved
    assert knobs["llm_reasoning_effort"] == "high"


def test_two_efforts_on_the_proxy_are_two_comparability_sets(proxy, two_schema_assets):
    """The incident the knob's own note describes, reproduced end to end.

    *"Two v1 ladders differed ONLY in this and compared as one experiment; it moved the
    baseline arm past that ladder's detection threshold."* With the writer dead, a
    high-vs-low A/B on the proxy produced two artifacts whose comparability sets were
    **identical** — so the two treatments compared as one.
    """
    def comparability_of(effort: str) -> dict[str, Any]:
        model = proxy.build_chat_model(llm_model="Claude-Opus-4.8", reasoning_effort=effort)
        knobs = _session(two_schema_assets, agent_model=model).knobs_resolved
        return {k: v for k, v in knobs.items() if k in comparability_keys()}

    high, low = comparability_of("high"), comparability_of("low")
    assert high["llm_reasoning_effort"] == "high"
    assert low["llm_reasoning_effort"] == "low"
    assert high != low, (
        "a high-vs-low effort A/B on the proxy resolves to one comparability set, so the "
        "two arms carry one config hash and compare as one experiment"
    )
    # ...and the difference is *only* the effort, or the assertion above would pass for a
    # resolver that made every session unique.
    assert {k for k in high if high[k] != low.get(k)} == {"llm_reasoning_effort"}


def test_an_effort_the_proxy_drops_is_recorded_as_dropped(proxy, two_schema_assets):
    """``effort=none`` sends no thinking block, so the record must not claim one.

    The record reports what goes on the wire, not what was asked for. Rounding this to
    ``"none"`` would make a GPT-on-proxy arm and a Claude-at-low arm look like different
    treatments of the same knob when only one of them configured reasoning at all.
    """
    model = proxy.build_chat_model(llm_model="gpt-5.6-luna", reasoning_effort="none")
    knobs = _session(two_schema_assets, agent_model=model).knobs_resolved
    assert knobs["llm_reasoning_effort"] is None


def test_every_gateways_spelling_of_effort_reads_back(proxy):
    """One intent, three spellings, one string out. ``model/provider.py``'s table, backwards.

    Stand-ins here rather than built clients, because ``ChatBedrockConverse`` needs ``boto3``
    and a region — and the property under test is the decoding, which is the half that was
    missing.
    """
    from governed_bi.model.provider import reasoning_effort_of

    class Direct:  # OpenAI: a plain attribute
        reasoning_effort = "medium"

    class Proxied:  # the internal proxy: inside the request envelope
        extra_body = proxy.build_extra_body("s", "high")

    class Anthropic:  # Bedrock: beside the thinking block, not inside it
        additional_model_request_fields = {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }

    class Nova:  # Bedrock: a named effort under another key
        additional_model_request_fields = {
            "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "low"}
        }

    class Nothing:
        pass

    assert reasoning_effort_of(Direct()) == "medium"
    assert reasoning_effort_of(Proxied()) == "high"
    assert reasoning_effort_of(Anthropic()) == "high"
    assert reasoning_effort_of(Nova()) == "low"
    assert reasoning_effort_of(Nothing()) is None

    # Round-tripped through the encoder rather than against a hand-written dict, so a change
    # to the Bedrock spelling that forgets the reporting half fails here instead of landing a
    # run whose `llm_reasoning_effort` is null.
    from governed_bi.model.provider import _ANTHROPIC_EFFORTS, _bedrock_reasoning

    for effort in _ANTHROPIC_EFFORTS:
        class Round:
            additional_model_request_fields = _bedrock_reasoning(effort)

        assert reasoning_effort_of(Round()) == effort


# ── 2. which gateway served which surface ─────────────────────────────────────


def test_the_utility_and_embedding_gateways_are_recorded_not_assumed(proxy):
    """Three surfaces on the proxy, three fields that say so.

    Neither ``llm_utility_provider`` nor ``embedding_provider`` had a writer anywhere, so both
    sat at the register default ``"openai"`` while the row beside them said
    ``llm_provider: "custom:007df842"``. Each row contradicted itself, and a wrong value on a
    ``Role.comparability`` field reads as a measurement where a null reads as an absence.

    An empty asset set, because ``from_assets`` embeds every summary it is given and the
    proxy behind this embedder does not exist. The knobs under test are run constants; they
    do not depend on what was indexed.
    """
    from governed_bi.model import ProxyEmbedder

    agent = proxy.build_chat_model(llm_model="Claude-Opus-4.8")
    utility = proxy.build_chat_model(llm_model="Claude-Sonnet-5")
    embedder = ProxyEmbedder(embedding_dimensions=64)

    knobs = _session(
        {}, agent_model=agent, utility_model=utility, embedder=embedder
    ).knobs_resolved

    # The defaults these used to publish. Named, so the assertions below cannot pass by
    # happening to agree with the register.
    assert knob_default("llm_utility_provider") == "openai"
    assert knob_default("embedding_provider") == "openai"

    assert knobs["llm_utility_provider"] == knobs["llm_provider"] != "openai"
    assert knobs["embedding_provider"] == "proxy"
    # ...and the agent's gateway is a digest of the host *this file* chose, not of anything
    # the implementation names.
    import hashlib
    from urllib.parse import urlsplit

    host = urlsplit(PROXY_BASE).netloc
    expected = "custom:" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:8]
    assert knobs["llm_provider"] == expected


def test_a_split_gateway_configuration_is_two_values(proxy, two_schema_assets):
    """The configuration the per-surface knobs exist for: a cheap utility elsewhere.

    A shared knob would hash "proxy agent + proxy utility" and "proxy agent + OpenAI utility"
    as one treatment, which is the argument ``llm_utility_provider``'s own note makes.
    """
    from governed_bi.serve.scripted_model import ScriptedChatModel

    agent = proxy.build_chat_model(llm_model="Claude-Opus-4.8")
    # No base URL: the vendor's own endpoint, which is the library default.
    utility: Any = ScriptedChatModel(responses=[])

    knobs = _session(
        two_schema_assets, agent_model=agent, utility_model=utility
    ).knobs_resolved
    assert knobs["llm_provider"].startswith("custom:")
    assert knobs["llm_utility_provider"] == "openai"
    assert knobs["llm_provider"] != knobs["llm_utility_provider"]


def test_bedrock_is_not_reported_as_openai(two_schema_assets):
    """A gateway with no base URL is not therefore the vendor's.

    ``_provider_of`` returned ``"openai"`` for anything without a URL to digest, which on the
    Bedrock arm is the same lie one gateway over — a comparability field asserting a gateway
    the run did not use.
    """
    class Converse:
        _llm_type = "chat-bedrock-converse"
        model_name = "anthropic.claude-opus-4-8-v1:0"

    knobs = _session(two_schema_assets, agent_model=Converse()).knobs_resolved
    assert knobs["llm_provider"] == "bedrock"
    assert knobs["chat_model"] == "anthropic.claude-opus-4-8-v1:0"


def test_an_unqualified_embedder_refuses_rather_than_naming_a_gateway():
    """``ports.py`` requires ``Embedder.model`` to be provider-qualified. Absent that, refuse.

    Guessing is the defect: the six arms recorded ``"openai"`` because something plausible was
    cheaper than something true.
    """
    from governed_bi.model.embedder import embedding_knobs, embedding_provider

    assert embedding_provider("proxy:text-embedding-3-large") == "proxy"
    assert embedding_provider("bedrock:amazon.titan-embed-text-v2:0") == "bedrock"

    class Unqualified:
        model = "text-embedding-3-large"
        dimensions = 64

    with pytest.raises(ValueError, match="provider-qualified"):
        embedding_knobs(Unqualified())


# ── 3. one spelling of the model, and it is the declared one ──────────────────


def test_the_model_is_recorded_under_the_declared_name_and_no_other(two_schema_assets):
    """``chat_model`` carries it; ``llm_model`` is gone.

    ``llm_model`` was written into ``knobs_resolved`` and never declared, so it sat outside
    ``comparability_keys()``. On run1, run2, v3-pinned and v3-fold it was the *only* field
    carrying the model — which put the one value that could have told those arms apart
    outside the comparability set.
    """
    from governed_bi.serve.scripted_model import ScriptedChatModel

    class Named(ScriptedChatModel):
        model_name: str = "gpt-5.6-luna"

    knobs = _session(two_schema_assets, agent_model=Named(responses=[])).knobs_resolved

    assert knobs["chat_model"] == "gpt-5.6-luna"
    assert "llm_model" not in knobs
    assert "chat_model" in comparability_keys()
    # No second spelling anywhere: every key the record publishes is a declared knob, which
    # is the property `llm_model` broke.
    assert set(knobs) <= knob_names()


def test_the_two_model_knobs_cannot_disagree_about_one_client(two_schema_assets):
    """A lone agent model is both surfaces, and both must name it identically."""
    from governed_bi.serve.scripted_model import ScriptedChatModel

    class Named(ScriptedChatModel):
        model_name: str = "gpt-5.6-luna"

    knobs = _session(two_schema_assets, agent_model=Named(responses=[])).knobs_resolved
    assert knobs["chat_model"] == knobs["llm_utility_model"] == "gpt-5.6-luna"


# ── 4. the three environment variables ────────────────────────────────────────


@pytest.mark.parametrize(
    ("knob", "raw", "expected"),
    [
        ("rail_node_timeout_s", "7.5", 7.5),
        ("agent_node_timeout_s", "99", 99.0),
        ("agent_recursion_limit", "12", 12),
    ],
)
def test_an_env_var_that_moves_behaviour_moves_the_record(
    monkeypatch, two_schema_assets, knob, raw, expected
):
    """Set the variable, and the artifact says what the run will do.

    All three are read env-first by ``serve/graph.py`` and ``serve/nodes/agent_core.py``, and
    ``_resolved_knobs`` built the record from ``knob_defaults()`` alone — so the record
    published 120.0 / 1200.0 / 40 whatever was exported.
    """
    from governed_bi.register.knobs import env_overrides

    var = env_overrides()[knob]
    monkeypatch.setenv(var, raw)

    knobs = _session(two_schema_assets).knobs_resolved
    assert knobs[knob] == expected
    # Not the default, or the assertion above would hold for a record that ignored the
    # environment entirely.
    assert knobs[knob] != knob_default(knob)
    # And the type is the reader's, because `_knobs_resolved_gate` compares by `repr`: 40 and
    # 40.0 are two configurations.
    assert type(knobs[knob]) is type(knob_default(knob))


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

    knobs = _session(two_schema_assets).knobs_resolved
    state = {"knobs_resolved": dict(knobs)}

    assert knobs["rail_node_timeout_s"] == _node_timeout("guard") == 7.5
    assert knobs["agent_node_timeout_s"] == _agent_node_timeout(state) == 99.0
    assert knobs["agent_recursion_limit"] == _recursion_limit(state) == 12


def test_a_blank_variable_is_unset_and_a_bad_one_refuses(monkeypatch, two_schema_assets):
    """The two parsing rules the readers already had, kept in step.

    Blank falls through to the knob (``graph.py`` tests ``if raw``, ``agent_core.py`` tests
    ``strip() != ""``). An unreadable value raises at session construction rather than
    mid-run, where the run is lost anyway and the message names the variable.
    """
    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "   ")
    assert _session(two_schema_assets).knobs_resolved["agent_recursion_limit"] == 40

    monkeypatch.setenv("GOVERNED_BI_AGENT_RECURSION_LIMIT", "forty")
    with pytest.raises(ValueError, match="GOVERNED_BI_AGENT_RECURSION_LIMIT"):
        _session(two_schema_assets)


def test_every_declared_env_var_is_one_a_reader_actually_reads():
    """An ``env_var`` in the register that nothing consults is the defect inverted.

    The register would then publish an override no run can perform — the same false claim as
    a knob with no reader, in the other direction. Checked against the reader sources, which
    is where the literal lives.
    """
    import pathlib

    from governed_bi.register.knobs import env_overrides

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "governed_bi" / "serve"
    read_by = (src / "graph.py").read_text(encoding="utf-8") + (
        src / "nodes" / "agent_core.py"
    ).read_text(encoding="utf-8")

    declared = env_overrides()
    assert set(declared) == {
        "rail_node_timeout_s",
        "agent_node_timeout_s",
        "agent_recursion_limit",
    }
    unread = sorted(var for var in declared.values() if var not in read_by)
    assert unread == [], f"declared as an override and read by no node: {unread}"


# ── 5. nothing is absent ──────────────────────────────────────────────────────


def test_no_declared_knob_is_missing_from_the_record(two_schema_assets):
    """Absence was the defect, and the drift gate cannot see it.

    ``_resolved_knobs`` dropped every ``UNSET`` knob and re-added three, so
    ``sqlglot_version``, ``negative_tau`` and ``cost_budget`` were absent from all 8,106 rows.
    ``measure/gates.py::_knobs_resolved_gate`` reads with ``row.get(key)`` — a key missing
    from every row compares equal to itself and the gate passes on a configuration it never
    saw.
    """
    knobs = _session(two_schema_assets).knobs_resolved

    assert set(knobs) == set(knob_names())
    # Stated separately because this is the set the gate iterates.
    assert resume_drift_keys() <= set(knobs)
    for name in ("sqlglot_version", "negative_tau", "cost_budget"):
        assert name in knobs


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
    knobs = _session(two_schema_assets).knobs_resolved
    assert knobs["sqlglot_version"] == installed
    assert knobs["sqlglot_version"] not in (None, "", "unknown")
    # And it is a version, not the tested-major constant beside it.
    assert installed.split(".")[0] != knobs["sqlglot_version"]


def test_an_uncalibrated_knob_records_null_and_a_set_one_records_its_value(two_schema_assets):
    """``UNSET`` becomes ``null`` — "this run had no calibrated value", which is a
    measurement — and a policy that carries a bound is recorded carrying it.

    Both halves. Null alone would pass for a record that still ignored the policy.
    """
    default_policy = _session(two_schema_assets, policy=GovernancePolicy()).knobs_resolved
    assert default_policy["cost_budget"] is None
    # `negative_tau` is null and that is the true value, not a placeholder:
    # `serve/nodes/negative.py` writes `"tau": None` on every turn because the gate ships
    # disabled.
    assert default_policy["negative_tau"] is None

    bounded = _session(
        two_schema_assets, policy=GovernancePolicy(cost_budget=250_000, guard_rules_enabled={})
    ).knobs_resolved
    assert bounded["cost_budget"] == 250_000


def test_reading_an_uncalibrated_knob_still_refuses(two_schema_assets):
    """Recording ``null`` must not hand a node a value nobody chose.

    ``int_knob`` falls a ``None`` through to ``knob_default``, which is still ``UNSET`` and
    still raises. If recording the absence had also made it readable, this fix would have
    disabled the refusal that ``register/knobs.py`` exists to make.
    """
    from governed_bi.serve.runtime import int_knob

    knobs = _session(two_schema_assets, policy=GovernancePolicy()).knobs_resolved
    with pytest.raises(ValueError, match="UNSET"):
        int_knob({"knobs_resolved": dict(knobs)}, "cost_budget")


# ── 6. which prompt variant produced which digest ─────────────────────────────


def test_the_prompt_variant_is_named_and_not_only_hashed(two_schema_assets):
    """v2, v3, v4 and v5 differ by prompt wording and nothing else, and all four recorded
    ``prompt_set: null``.

    They were *distinguishable* — ``prompt_set_hash`` is on the row and does differ. They
    were not *nameable*: nothing in an artifact said which variant produced which digest,
    so reading the six arms could tell you two of them were not the same treatment and not
    which treatment either one was.

    The two must move **together**, from one selection, or the record carries a digest of
    one prompt set beside the name of another.
    """
    def arm(variant: str):
        session = _session(two_schema_assets, prompt_variants={"analyst": variant})
        return session.knobs_resolved["prompt_set"], session.prompt_set_hash

    v4_set, v4_hash = arm("v4")
    v5_set, v5_hash = arm("v5")

    # Named, not merely present. The variant this test asked for is the variant recorded.
    assert v4_set["analyst"] == "v4"
    assert v5_set["analyst"] == "v5"
    # Total over the registry, so a stage nobody overrode still says which variant it ran.
    assert v4_set["narrate"] == "v1"

    # The digests are the ones the four measured arms carry, so this is pinned to the real
    # prompt text rather than to whatever it happens to be today.
    assert v4_hash == "b1f9e4d7d230cb97"
    assert v5_hash == "7a9e710273998631"
    assert (v4_set, v4_hash) != (v5_set, v5_hash)
    # ...and only the overridden stage moved, or "they differ" would hold for a resolver
    # that never repeats itself.
    assert {k for k in v4_set if v4_set[k] != v5_set[k]} == {"analyst"}


def test_the_recorded_prompt_set_is_the_one_the_hash_was_taken_over(two_schema_assets):
    """One selection behind both fields. The ``Session`` docstring already requires it:
    a ``prompt_set_hash`` computed from a different mapping records a treatment the run
    did not send.

    Asserted by recomputing the digest from the *recorded names*, which fails the moment
    the two are derived from different inputs.
    """
    from governed_bi.register.prompts import prompt_set_hash as digest_of

    session = _session(two_schema_assets, prompt_variants={"analyst": "v3"})
    recorded = session.knobs_resolved["prompt_set"]
    assert digest_of(recorded) == session.prompt_set_hash == "ef30252f824de06c"


def test_an_undeclared_prompt_name_refuses_at_session_build(two_schema_assets):
    """Same refusal ``prompt_set_hash`` already makes, and it must not be softened into a
    silently dropped override — that would record a prompt set the run did not use."""
    with pytest.raises(KeyError, match="analyst_system"):
        _session(two_schema_assets, prompt_variants={"analyst_system": "v4"})


def test_the_environment_this_suite_runs_in_did_not_supply_the_answers():
    """The autouse fixture's own claim, asserted once rather than trusted."""
    assert [v for v in ENV_VARS if os.environ.get(v)] == []
