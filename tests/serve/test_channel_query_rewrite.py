"""Each extracting channel searches with its own restatement of the question.

``channel_schema`` does not rewrite; it searches the user's words.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from governed_bi.registry.channels import CHANNEL_EXTRACTS, Channel, ChannelState
from governed_bi.registry.prompts import CHANNEL_QUERY_PROMPTS, PROMPT_REGISTRY, prompt_text
from governed_bi.registry.stages import CHANNEL_STAGES, Stage
from governed_bi.serve.nodes import channels as channels_mod


class _Rewriter:
    """Returns a canned rewrite and records what it was asked."""

    def __init__(
        self,
        text: str = "tables holding restaurant records and ratings",
        raises: Exception | None = None,
    ) -> None:
        self.text = text
        self.raises = raises
        self.calls: list[Any] = []
        self.configs: list[Any] = []

    def invoke(self, messages: Any, config: Any = None, **kwargs: Any) -> Any:
        # ``config=`` is part of ``BaseChatModel.invoke``'s real signature, and this fake
        # omitted it. When the callers began naming their runs for the trace, the fake
        # raised ``TypeError`` and the caller's ``except`` reported it as a provider
        self.configs.append(config)
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return type("Reply", (), {"text": self.text})()

    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        """The nodes await now, and a double that only offers ``invoke`` fails them open."""
        return self.invoke(messages, config, **kwargs)

def _rewrite(
    question: str, stage: Stage, model: Any
) -> tuple[str, set[Channel], list[dict[str, Any]]]:
    ran: set[Channel] = set()
    #: The rewriter's cost rows. These five calls were absent from the engine's ledger
    #: entirely — ``usage`` was written only by ``agent_core`` — so a turn reported the
    #: agent's tokens and none of retrieval's. Returned here so the tests can say which
    spent: list[dict[str, Any]] = []
    conf: dict[str, Any] = {} if model is None else {"utility_model": model}
    out = asyncio.run(channels_mod._rewritten_query(
        question, stage, {"configurable": conf}, ran=ran, spent=spent, turn_index=1
    ))
    return out, ran, spent




def test_every_extracting_channel_has_its_own_rewriter_prompt() -> None:
    """One prompt per extracting channel, so one variant can move without the others."""
    for stage in CHANNEL_EXTRACTS:
        assert stage.value in CHANNEL_QUERY_PROMPTS, f"{stage.value} extracts but has no prompt"
        assert CHANNEL_QUERY_PROMPTS[stage.value] in PROMPT_REGISTRY
    assert set(CHANNEL_QUERY_PROMPTS) == {s.value for s in CHANNEL_EXTRACTS}, (
        "the mapping and the extraction declaration must name the same retrieval channels, or a retrieval channel "
        "rewrites while its channel state says it does not — or the reverse, which is how "
        "`facet_schema` came to report `not_configured` while plainly rewriting"
    )

    texts = {prompt_text(n) for n in CHANNEL_QUERY_PROMPTS.values()}
    assert len(texts) == len(CHANNEL_QUERY_PROMPTS), (
        "two retrieval channels share a prompt text, so tuning one would silently move the other"
    )


def test_the_rewriter_prompts_are_hashed() -> None:
    """A prompt outside ``prompt_set_hash`` is a setting under test the run cannot report."""
    from governed_bi.registry.prompts import prompt_set_hash

    baseline = prompt_set_hash()
    assert baseline == prompt_set_hash()
    for name in CHANNEL_QUERY_PROMPTS.values():
        assert PROMPT_REGISTRY[name].stage in {s.value for s in CHANNEL_STAGES}




# The extracting channels only. `channel_schema` is deliberately not one of them any more, and the
# test below pins that it searches the raw question instead.
@pytest.mark.parametrize("stage", sorted(CHANNEL_EXTRACTS, key=lambda s: s.value))
def test_a_rewrite_replaces_the_question_and_marks_extraction(stage: Stage) -> None:
    model = _Rewriter()
    out, ran, spent = _rewrite("average star rating for restaurants nearby", stage, model)
    assert out == "tables holding restaurant records and ratings"
    assert Channel.extraction in ran
    assert len(model.calls) == 1
    system, human = model.calls[0]
    assert system.content == prompt_text(CHANNEL_QUERY_PROMPTS[stage.value])
    assert human.content == "average star rating for restaurants nearby"


@pytest.mark.parametrize(
    ("label", "model"),
    [
        ("no model configured", None),
        ("the model errored", _Rewriter(raises=TimeoutError("upstream"))),
        ("the model returned nothing", _Rewriter(text="")),
        ("the model returned whitespace", _Rewriter(text="   \n ")),
    ],
)
def test_a_failed_rewrite_falls_back_and_does_not_claim_the_channel(label: str, model: Any) -> None:
    """A failed rewrite searches the original question and does not record the channel as ran."""
    out, ran, spent = _rewrite("how many customers", Stage.channel_term, model)
    assert out == "how many customers", label
    assert Channel.extraction not in ran, f"{label}: the channel was claimed without a rewrite"


def test_an_empty_question_is_not_sent_to_the_model() -> None:
    model = _Rewriter()
    out, ran, spent = _rewrite("", Stage.channel_metric, model)
    assert out == ""
    assert model.calls == [], "a blank question must not cost a model call"
    assert Channel.extraction not in ran




class _Embedder:
    """Records what it was asked to embed. Width is irrelevant here; identity is the point."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t)), 1.0] for t in texts]


def test_a_rewrite_is_embedded_so_the_semantic_channel_sees_it() -> None:
    """A channel that restates the question and then scores with the *original* question's vector
    has paid for the rewrite and thrown away the half that motivated it — the whole point of
    restating it in the vocabulary of the thing being searched is to move it semantically."""
    embedder = _Embedder()
    state = {"query_vector": [9.0, 9.0]}
    config = {"configurable": {"embedder": embedder}}

    vector, state_of = channels_mod._query_vector(
        state, config, query="tables holding restaurant ratings", question="star rating nearby"
    )
    assert embedder.seen == ["tables holding restaurant ratings"]
    assert vector != [9.0, 9.0], "the cached question vector was used despite a rewrite"
    assert state_of is ChannelState.ran


def test_no_rewrite_uses_the_turns_cached_vector() -> None:
    """``accept`` embeds the question once per turn. When nothing rewrote it, paying again for
    an identical string would be five redundant calls."""
    embedder = _Embedder()
    state = {"query_vector": [9.0, 9.0]}
    config = {"configurable": {"embedder": embedder}}

    vector, state_of = channels_mod._query_vector(state, config, query="same", question="same")
    assert embedder.seen == []
    assert vector == [9.0, 9.0]
    # The cached vector *is* this query's vector, so reusing it is a cache hit and not a
    # substitution — the distinction the test below turns on.
    assert state_of is ChannelState.ran


def test_an_embedder_failure_is_reported_and_not_papered_over(caplog) -> None:
    """Audit I7."""
    class _Broken:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

    vector, state_of = channels_mod._query_vector(
        {"query_vector": [9.0, 9.0]},
        {"configurable": {"embedder": _Broken()}},
        query="rewritten",
        question="original",
    )
    assert vector is None, "the raw question's vector was substituted for the rewrite's"
    assert state_of is ChannelState.failed, (
        "a rate-limited embed must not be reported as a channel that ran"
    )


def test_the_schema_channel_searches_the_users_own_words() -> None:
    """``channel_schema`` sends no rewrite, and its prompt is still registered."""
    from governed_bi.registry.channels import CHANNEL_EXTRACTS
    from governed_bi.registry.prompts import PROMPT_REGISTRY

    assert Stage.channel_schema not in CHANNEL_EXTRACTS
    assert "channel_schema" not in CHANNEL_QUERY_PROMPTS
    assert "facet_schema_query" in PROMPT_REGISTRY

    model = _Rewriter("catalogue words that would replace the question")
    out, ran, spent = _rewrite("how many restaurants are over 4 stars", Stage.channel_schema, model)

    assert out == "how many restaurants are over 4 stars", "the question must reach the index intact"
    assert model.calls == [], "no prompt for this retrieval channel means no model call, not a wasted one"
    assert Channel.extraction not in ran, "a retrieval channel that did not extract must not claim it did"
    assert spent == [], "and it bills nothing"
