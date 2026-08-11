"""Each facet searches with its own restatement of the question.

The ask: *"for the schema level routing, we could ask what kind of tables and schema would help
us to resolve the question of blah blah blah. And for the metric is that, what kind of metric is
associated with the following sentence?"*

The reason it matters is concrete. A user asks *"what is the average star rating for restaurants
in this area"*; a schema summary reads *"stores basic information about restaurants"*. Neither
BM25 nor an embedder finds much between those two, and until now every facet searched with the
raw question — which is why the maintainer's own testing found retrieval was getting nothing right.

Two properties carry the weight here, and both are about honesty rather than quality: the
``extraction`` channel must be marked ``ran`` **only** when a rewrite actually came back, and a
rewrite must reach the *semantic* channel and not just BM25. A fallback that reports as a run is
how, per ADR 0005 §2.3, an arm quietly becomes v1's single-pass retrieval while every channel
claims to be working.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from governed_bi.register.facets import FACET_EXTRACTS, Channel, ChannelState
from governed_bi.register.prompts import FACET_QUERY_PROMPTS, PROMPT_REGISTRY, prompt_text
from governed_bi.register.stages import FACET_STAGES, Stage
from governed_bi.serve.nodes import facets as facets_mod


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
        # failure — a fake narrower than the interface turning a code change into a
        # plausible-looking wrong verdict. It is recorded so a caller cannot pass one
        # silently, and ignored because nothing here reads it.
        self.configs.append(config)
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return type("Reply", (), {"text": self.text})()

    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        """The nodes await now, and a double that only offers ``invoke`` fails them open.

        Same lesson as the ``config=`` parameter below: a fake that is narrower than
        ``BaseChatModel`` does not fail loudly, it makes the caller take its error branch. The
        scope gate's error branch is ``error_failed_open``.
        """
        return self.invoke(messages, config, **kwargs)

def _rewrite(
    question: str, stage: Stage, model: Any
) -> tuple[str, set[Channel], list[dict[str, Any]]]:
    ran: set[Channel] = set()
    #: The rewriter's cost rows. These five calls were absent from the engine's ledger
    #: entirely — ``usage`` was written only by ``agent_core`` — so a turn reported the
    #: agent's tokens and none of retrieval's. Returned here so the tests can say which
    #: paths spend and which do not.
    spent: list[dict[str, Any]] = []
    conf: dict[str, Any] = {} if model is None else {"utility_model": model}
    out = asyncio.run(facets_mod._rewritten_query(
        question, stage, {"configurable": conf}, ran=ran, spent=spent, turn_index=1
    ))
    return out, ran, spent


# ── every facet has a prompt, and the registry covers it ──


def test_every_extracting_facet_has_its_own_rewriter_prompt() -> None:
    """One prompt per *extracting* facet, not one parameterised prompt: each searches a different
    kind of object and will be tuned against a different number, and the registry exists so a
    variant of one can be compared without moving the others.

    **Keyed on ``FACET_EXTRACTS``, not on ``FACET_STAGES``.** ``facet_schema`` no longer rewrites
    — measured: the raw question beats every rewrite of it, by 0.65 on one decomposed question and
    by 1.8pp of recall@3 over 114 — so it has no entry in the mapping and must not be required to.
    Its prompt stays *registered*, which the test below holds."""
    for stage in FACET_EXTRACTS:
        assert stage.value in FACET_QUERY_PROMPTS, f"{stage.value} extracts but has no prompt"
        assert FACET_QUERY_PROMPTS[stage.value] in PROMPT_REGISTRY
    assert set(FACET_QUERY_PROMPTS) == {s.value for s in FACET_EXTRACTS}, (
        "the mapping and the extraction declaration must name the same facets, or a facet "
        "rewrites while its channel state says it does not — or the reverse, which is how "
        "`facet_schema` came to report `not_configured` while plainly rewriting"
    )

    texts = {prompt_text(n) for n in FACET_QUERY_PROMPTS.values()}
    assert len(texts) == len(FACET_QUERY_PROMPTS), (
        "two facets share a prompt text, so tuning one would silently move the other"
    )


def test_the_rewriter_prompts_are_hashed() -> None:
    """A prompt outside ``prompt_set_hash`` is a treatment the run cannot report."""
    from governed_bi.register.prompts import prompt_set_hash

    baseline = prompt_set_hash()
    assert baseline == prompt_set_hash()
    for name in FACET_QUERY_PROMPTS.values():
        assert PROMPT_REGISTRY[name].stage in {s.value for s in FACET_STAGES}


# ── the rewrite happens, and is attributed honestly ──


# The extracting facets only. `facet_schema` is deliberately not one of them any more, and the
# test below pins that it searches the raw question instead.
@pytest.mark.parametrize("stage", sorted(FACET_EXTRACTS, key=lambda s: s.value))
def test_a_rewrite_replaces_the_question_and_marks_extraction(stage: Stage) -> None:
    model = _Rewriter()
    out, ran, spent = _rewrite("average star rating for restaurants nearby", stage, model)
    assert out == "tables holding restaurant records and ratings"
    assert Channel.extraction in ran
    assert len(model.calls) == 1
    system, human = model.calls[0]
    assert system.content == prompt_text(FACET_QUERY_PROMPTS[stage.value])
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
    """**The property that keeps the measurement honest.**

    Falling back to the raw question is the right behaviour — retrieval on the original wording
    is what this replaced, so the worst case is yesterday, not a dead turn. Reporting
    ``extraction`` as ``ran`` while doing it is not: that is the arm claiming a treatment it did
    not receive, and the degradation gate reads exactly this field.
    """
    out, ran, spent = _rewrite("how many customers", Stage.facet_term, model)
    assert out == "how many customers", label
    assert Channel.extraction not in ran, f"{label}: the channel was claimed without a rewrite"


def test_an_empty_question_is_not_sent_to_the_model() -> None:
    model = _Rewriter()
    out, ran, spent = _rewrite("", Stage.facet_metric, model)
    assert out == ""
    assert model.calls == [], "a blank question must not cost a model call"
    assert Channel.extraction not in ran


# ── the rewrite reaches the semantic channel, not only BM25 ──


class _Embedder:
    """Records what it was asked to embed. Width is irrelevant here; identity is the point."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t)), 1.0] for t in texts]


def test_a_rewrite_is_embedded_so_the_semantic_channel_sees_it() -> None:
    """A facet that restates the question and then scores with the *original* question's vector
    has paid for the rewrite and thrown away the half that motivated it — the whole point of
    restating it in the vocabulary of the thing being searched is to move it semantically."""
    embedder = _Embedder()
    state = {"query_vector": [9.0, 9.0]}
    config = {"configurable": {"embedder": embedder}}

    vector, state_of = facets_mod._query_vector(
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

    vector, state_of = facets_mod._query_vector(state, config, query="same", question="same")
    assert embedder.seen == []
    assert vector == [9.0, 9.0]
    # The cached vector *is* this query's vector, so reusing it is a cache hit and not a
    # substitution — the distinction the test below turns on.
    assert state_of is ChannelState.ran


def test_an_embedder_failure_is_reported_and_not_papered_over(caplog) -> None:
    """Audit I7. A dead embedder yields ``failed``, **not** the raw question's vector.

    This test asserted the opposite until 2026-08-10, on the reasoning that "a dead embedder must
    degrade, not drop the semantic channel". The rejected alternative is worth stating, because it
    is the more intuitive one: returning ``fallback`` keeps *a* cosine in the score rather than
    none.

    What it actually produced was a facet whose lexical channel searched the rewrite while its
    semantic channel searched the original question, blended into one ``score``, recorded as
    ``semantic: ran``. That is not a weaker measurement of the same query — it is a measurement of
    a different one, presented as the first. Nothing in any artifact distinguished it from a
    healthy turn, which is the defect class this whole audit is about.

    Falling back on **both** channels was considered and rejected too: it gives one facet two
    possible search texts decided by a provider error, and ``queries`` would then have to carry
    which — a second hidden path in the scoring loop, to save a channel on the rare turn a
    retried embed call still fails.
    """
    class _Broken:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

    vector, state_of = facets_mod._query_vector(
        {"query_vector": [9.0, 9.0]},
        {"configurable": {"embedder": _Broken()}},
        query="rewritten",
        question="original",
    )
    assert vector is None, "the raw question's vector was substituted for the rewrite's"
    assert state_of is ChannelState.failed, (
        "a rate-limited embed must not be reported as a channel that ran"
    )


def test_the_schema_facet_searches_the_users_own_words() -> None:
    """``facet_schema`` sends no rewrite, and its prompt is still registered.

    Two halves, because dropping either would be a different defect. Sending no rewrite is the
    measured decision: decomposing one question's route score per facet with the rewriters off,
    the raw question won ``facet_schema`` outright and the whole shortlist by 0.65, while the
    rewritten form put the gold schema at #3 and then out of the top-3. Keeping the prompt
    registered is what leaves a future attempt something to be compared against — and keeps it
    inside ``prompt_set_hash``, so a build that re-enables it cannot report the same hash.
    """
    from governed_bi.register.facets import FACET_EXTRACTS
    from governed_bi.register.prompts import PROMPT_REGISTRY

    assert Stage.facet_schema not in FACET_EXTRACTS
    assert "facet_schema" not in FACET_QUERY_PROMPTS
    assert "facet_schema_query" in PROMPT_REGISTRY

    model = _Rewriter("catalogue words that would replace the question")
    out, ran, spent = _rewrite("how many restaurants are over 4 stars", Stage.facet_schema, model)

    assert out == "how many restaurants are over 4 stars", "the question must reach the index intact"
    assert model.calls == [], "no prompt for this facet means no model call, not a wasted one"
    assert Channel.extraction not in ran, "a facet that did not extract must not claim it did"
    assert spent == [], "and it bills nothing"
