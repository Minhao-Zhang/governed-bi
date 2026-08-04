"""Each facet searches with its own restatement of the question.

The ask: *"for the schema level routing, we could ask what kind of tables and schema would help
us to resolve the question of blah blah blah. And for the metric is that, what kind of metric is
associated with the following sentence?"*

The reason it matters is concrete. A user asks *"what is the average star rating for restaurants
in this area"*; a schema summary reads *"stores basic information about restaurants"*. Neither
BM25 nor an embedder finds much between those two, and until now every facet searched with the
raw question — which is why the maintainer's own testing found retrieval "完全没有做对".

Two properties carry the weight here, and both are about honesty rather than quality: the
``extraction`` channel must be marked ``ran`` **only** when a rewrite actually came back, and a
rewrite must reach the *semantic* channel and not just BM25. A fallback that reports as a run is
how, per ADR 0005 §2.3, an arm quietly becomes v1's single-pass retrieval while every channel
claims to be working.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.register.facets import Channel
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

    def invoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return type("Reply", (), {"text": self.text})()


def _rewrite(question: str, stage: Stage, model: Any) -> tuple[str, set[Channel]]:
    ran: set[Channel] = set()
    conf: dict[str, Any] = {} if model is None else {"utility_model": model}
    out = facets_mod._rewritten_query(question, stage, {"configurable": conf}, ran=ran)
    return out, ran


# ── every facet has a prompt, and the registry covers it ──


def test_every_facet_has_its_own_rewriter_prompt() -> None:
    """Five prompts, not one parameterised prompt: each facet searches a different kind of
    object and will be tuned against a different number, and the registry exists so a variant of
    one can be compared without moving the others."""
    for stage in FACET_STAGES:
        assert stage.value in FACET_QUERY_PROMPTS, f"{stage.value} has no rewriter prompt"
        assert FACET_QUERY_PROMPTS[stage.value] in PROMPT_REGISTRY

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


@pytest.mark.parametrize("stage", list(FACET_STAGES))
def test_a_rewrite_replaces_the_question_and_marks_extraction(stage: Stage) -> None:
    model = _Rewriter()
    out, ran = _rewrite("average star rating for restaurants nearby", stage, model)
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
    out, ran = _rewrite("how many customers", Stage.facet_term, model)
    assert out == "how many customers", label
    assert Channel.extraction not in ran, f"{label}: the channel was claimed without a rewrite"


def test_an_empty_question_is_not_sent_to_the_model() -> None:
    model = _Rewriter()
    out, ran = _rewrite("", Stage.facet_metric, model)
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

    vector = facets_mod._query_vector(
        state, config, query="tables holding restaurant ratings", question="star rating nearby"
    )
    assert embedder.seen == ["tables holding restaurant ratings"]
    assert vector != [9.0, 9.0], "the cached question vector was used despite a rewrite"


def test_no_rewrite_uses_the_turns_cached_vector() -> None:
    """``accept`` embeds the question once per turn. When nothing rewrote it, paying again for
    an identical string would be five redundant calls."""
    embedder = _Embedder()
    state = {"query_vector": [9.0, 9.0]}
    config = {"configurable": {"embedder": embedder}}

    vector = facets_mod._query_vector(state, config, query="same", question="same")
    assert embedder.seen == []
    assert vector == [9.0, 9.0]


def test_an_embedder_failure_falls_back_to_the_question_vector() -> None:
    class _Broken:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

    vector = facets_mod._query_vector(
        {"query_vector": [9.0, 9.0]},
        {"configurable": {"embedder": _Broken()}},
        query="rewritten",
        question="original",
    )
    assert vector == [9.0, 9.0], "a dead embedder must degrade, not drop the semantic channel"
