"""One intent, three gateways, and the places a wrong spelling would pass unnoticed.

The dangerous half of multi-provider config is not the branch that raises. It is the one
that is *accepted and ignored*: ``max_retries`` handed to a boto client is silently dropped,
so the run keeps the knob in ``knobs_resolved`` and does not honour it, and two arms record
the same retry budget while only one has it. Every assertion here is on a translation whose
failure mode is silence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from governed_bi.model import provider as P

#: Bedrock's half of the translation needs ``botocore.config.Config`` to build a real object
#: to assert on. CI installs it (`uv sync --frozen --extra bedrock`) so these run there; a
#: base install skips them rather than erroring, because `langchain-aws` is an extra on
#: purpose — it pulls a boto3 tree the OpenAI and proxy arms never touch.
needs_bedrock = pytest.mark.skipif(
    importlib.util.find_spec("botocore") is None,
    reason="needs the bedrock extra: uv sync --extra bedrock",
)

@pytest.fixture(autouse=True)
def _no_inherited_provider_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from "no provider configured anywhere".

    ``provider_for`` reads a precedence chain, so a test that sets one link asserts nothing
    unless the others are known to be empty. These tests set :data:`P.PROVIDER_VAR` and were
    silently relying on the per-surface variables being unset in the developer's shell — true
    until ``.env`` gained ``GOVERNED_BI_EMBEDDING_PROVIDER=openai`` (2026-08-10, to keep the
    embedder on OpenAI while the two chat surfaces moved to Bedrock). The whole-suite run then
    failed on the embedding surface while ``pytest tests/model`` alone still passed, because
    the leak arrives via another test's ``credentials.load_into_environ()`` and so depends on
    test order. Clearing here rather than in each test: the precondition is the file's, and the
    next variable added to ``SURFACE_PROVIDER_VARS`` is covered without touching the tests.
    """
    for name in (P.PROVIDER_VAR, *P.SURFACE_PROVIDER_VARS.values()):
        monkeypatch.delenv(name, raising=False)


# ── which gateway serves a surface ────────────────────────────────────────────


def test_one_variable_sets_every_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(P.PROVIDER_VAR, "bedrock")
    assert [P.provider_for(s) for s in ("agent", "utility", "embedding")] == ["bedrock"] * 3


def test_a_surface_can_differ_from_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configuration this exists for: a cheap rewriter beside an expensive agent."""
    monkeypatch.setenv(P.PROVIDER_VAR, "bedrock")
    monkeypatch.setenv(P.SURFACE_PROVIDER_VARS["utility"], "openai")
    assert P.provider_for("agent") == "bedrock"
    assert P.provider_for("utility") == "openai"
    assert P.provider_for("embedding") == "bedrock"


def test_an_explicit_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A driver's recorded arm must not be changeable by an exported variable."""
    monkeypatch.setenv(P.PROVIDER_VAR, "bedrock")
    assert P.provider_for("agent", override="openai") == "openai"


def test_the_default_matches_the_knob_default() -> None:
    """``llm_provider`` records what this module decides; disagreement mislabels every arm."""
    from governed_bi.register.knobs import knob_default

    assert P.DEFAULT_PROVIDER == knob_default("llm_provider")


# ── one intent, two spellings ─────────────────────────────────────────────────


def test_openai_keeps_its_own_spelling() -> None:
    kwargs = P._openai_kwargs(effort="high", timeout=300.0, max_retries=3, tools=True)
    assert kwargs == {
        "use_responses_api": True,
        "reasoning_effort": "high",
        "timeout": 300.0,
        "max_retries": 3,
    }


@needs_bedrock
def test_bedrock_never_receives_openai_keywords() -> None:
    """``use_responses_api`` raises on ``ChatBedrockConverse``; the other two are dropped."""
    kwargs = P._bedrock_kwargs(effort="high", timeout=300.0, max_retries=3, tools=True)
    assert not {"use_responses_api", "reasoning_effort", "timeout", "max_retries"} & set(kwargs)


@needs_bedrock
def test_bedrock_retries_count_the_first_attempt() -> None:
    """``max_attempts`` includes the first try; the knob counts retries after it.

    Off by one here silently halves or doubles a comparability knob, which is the whole
    reason the translation is centralised.
    """
    config = P._bedrock_kwargs(effort=None, timeout=90.0, max_retries=3, tools=False)["config"]
    assert config.retries["max_attempts"] == 4
    assert config.read_timeout == 90.0 and config.connect_timeout == 90.0


@pytest.mark.parametrize(
    "model_id,expected_key",
    [
        ("us.anthropic.claude-sonnet-5", "thinking"),
        ("amazon.nova-2-lite-v1:0", "reasoningConfig"),
    ],
)
def test_reasoning_is_spelled_per_model_family(model_id: str, expected_key: str) -> None:
    """Anthropic takes adaptive thinking, Nova a named effort. There is no shared field."""
    assert expected_key in P._bedrock_reasoning("high", model_id)


def test_anthropic_on_bedrock_asks_for_adaptive_thinking_not_a_token_budget() -> None:
    """The spelling ``us.anthropic.claude-sonnet-5`` actually accepts.

    Pinned because the wrong one was fully documented and looked measured. Converse answers a
    ``budget_tokens`` request with *"thinking.type.enabled is not supported for this model. Use
    thinking.type.adaptive and output_config"* -- a ``ValidationException`` on every turn, not a
    degraded one. Asserting the absence of ``budget_tokens`` is the half that would have caught
    it; asserting only that a ``thinking`` key exists does not.
    """
    fields = P._bedrock_reasoning("xhigh", "us.anthropic.claude-sonnet-5")
    assert fields["thinking"] == {"type": "adaptive"}
    assert fields["output_config"] == {"effort": "xhigh"}
    assert "budget_tokens" not in fields["thinking"]


def test_an_effort_bedrock_cannot_express_raises() -> None:
    """Rather than reaching the API as a 400 halfway through a paid run.

    Bedrock does reject an unknown effort (*"unknown variant `enormous`"*), so the local check
    buys the failure earlier rather than at all.
    """
    with pytest.raises(ValueError, match="no Bedrock spelling"):
        P._bedrock_reasoning("enormous", "us.anthropic.claude-sonnet-5")


# ── refusals that name the fix ────────────────────────────────────────────────


def test_an_unknown_provider_names_the_variable_to_set() -> None:
    with pytest.raises(ValueError, match=P.SURFACE_PROVIDER_VARS["agent"]):
        P.chat_model("some-model", surface="agent", provider="vertex")


def test_the_proxy_is_not_built_here() -> None:
    """It needs credentials resolved before the client exists, so it has its own builder."""
    with pytest.raises(ValueError, match="proxy_gateway"):
        P.chat_model("some-model", surface="agent", provider="proxy")


# ── the cache-key contract every adapter owes ─────────────────────────────────


def test_every_embedder_qualifies_its_model_with_its_provider() -> None:
    """``cache_key`` is ``model|dimensions|text`` and carries no provider of its own.

    The prefix is the only thing keeping two gateways serving one nominal id out of each
    other's cached vectors, and ``cosine`` returns 0.0 on a width mismatch rather than
    raising — so a collision degrades routing to "nothing scores" with no error anywhere.

    Only Bedrock is asserted here, because only Bedrock can answer offline.
    ``OpenAIEmbedder.model`` reports the *served* id and probes to learn it, and
    ``ProxyEmbedder`` refuses to construct without a secret name — both are network facts,
    not assertions. Their prefixes are covered by their own adapters' tests.
    """
    from governed_bi.model.bedrock_embedder import BedrockEmbedder

    assert BedrockEmbedder(model="amazon.titan-embed-text-v2:0").model == (
        "bedrock:amazon.titan-embed-text-v2:0"
    )


def _adapters() -> list[type]:
    from governed_bi.model.bedrock_embedder import BedrockEmbedder
    from governed_bi.model.openai_embedder import OpenAIEmbedder
    from governed_bi.model.proxy_embedder import ProxyEmbedder

    return [OpenAIEmbedder, BedrockEmbedder, ProxyEmbedder]


@pytest.mark.parametrize("name", ["requested_model", "model", "dimensions"])
def test_every_adapter_carries_the_whole_identity_surface(name: str) -> None:
    """Across **all three**, not whichever one the test author happened to construct.

    ``ProxyEmbedder`` shipped without ``requested_model`` while the other two had it. Nothing
    noticed until the eval driver's proxy path was routed through the shared builder and
    raised ``AttributeError`` on the first embed. The earlier version of this file asserted
    the property on ``BedrockEmbedder`` alone, so it could not have caught that -- checking
    one implementation of a shared contract is the same blind spot in miniature.

    Asserted on the class, not an instance: two of the three need credentials to construct.
    """
    missing = [c.__name__ for c in _adapters() if not hasattr(c, name)]
    assert not missing, f"{missing} do not expose {name!r}, which every caller may rely on"


def test_the_cache_directory_uses_the_requested_name_not_the_probing_one() -> None:
    """``vector_cache_from_environment`` documents this: reading ``model`` can cost a request."""
    from governed_bi.model.bedrock_embedder import BedrockEmbedder

    embedder = BedrockEmbedder(model="amazon.titan-embed-text-v2:0")
    assert embedder.requested_model == "amazon.titan-embed-text-v2:0"


def test_a_bedrock_models_cache_directory_can_actually_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half the assertion above is missing: the requested name must be *usable* as a path.

    Every Bedrock id is versioned with a colon, and a colon cannot appear in a Windows path
    component -- NTFS reads ``name:stream`` as an alternate data stream, so ``mkdir`` raises
    ``NotADirectoryError`` (WinError 267). The test above names this exact model and checks
    only that the string survives a round trip, so nothing caught it and the server could not
    boot the first time the embedding surface moved to Bedrock.

    ``mkdir`` is the assertion, not the absence of a colon: the point is the filesystem
    accepting it, and asserting on the sanitised spelling would pass for a name that still
    cannot be created.
    """
    from governed_bi.retrieve.vector_cache import (
        VECTOR_CACHE_VAR,
        _directory_name,
        vector_cache_from_environment,
    )

    monkeypatch.setenv(VECTOR_CACHE_VAR, str(tmp_path))
    cache = vector_cache_from_environment(model="amazon.titan-embed-text-v2:0")
    Path(cache.uri).mkdir(parents=True)
    assert Path(cache.uri).is_dir()

    # And a name that already works must not move -- that directory holds rows already, so
    # sanitising it would silently abandon a paid-for store rather than reuse it.
    assert _directory_name("text-embedding-3-large") == "text-embedding-3-large"


def test_the_default_embedding_model_differs_by_provider() -> None:
    """A shared default would silently embed a Bedrock arm with an id Bedrock does not serve."""
    assert P.default_embedding_model("bedrock") != P.default_embedding_model("openai")
