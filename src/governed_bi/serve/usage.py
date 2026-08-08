"""Token rows for every model call the turn makes — one builder, used by every caller.

``usage`` used to be written by ``agent_core`` alone, so the guard's BI-scope gate and the five
facet query rewriters spent tokens no record mentioned. On an answered turn those six hid behind
the agent's several thousand; on a **refused** turn they were the only calls that happened, so
the record reported ``usage = []`` for a turn that really cost 136 tokens, and every token total
in the repository was low.

Rows carry ``stage``: with seven producers, the split between the agent model and the utility
model is a comparability knob (``llm_utility_model``) whose justification is cost and latency,
which cannot be argued from a single total.

A helper, not a node — six call sites in three modules build the same row, so a provider that
changes its usage payload is fixed once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.register.quantity import Measured
from governed_bi.serve.runtime import model_id

__all__ = ["NO_TOKEN_USAGE", "reported_tokens", "usage_row"]

NO_TOKEN_USAGE = "the provider returned no usage_metadata carrying both token counts"


def reported_tokens(messages: Any) -> dict[str, int] | None:
    """This turn's provider-reported token counts, or ``None`` if none were reported.

    LangChain puts them on ``AIMessage.usage_metadata``; a caller may pass one message or the
    several an agent loop produced, so the result is the sum. A payload that does not carry
    **both** counts as integers is not a measurement, and reporting the part it did carry
    beside a zero for the rest would be the defect this function exists to remove. Cache counts
    are included only when the provider reported them — a zero here would be this code's claim.
    """
    if isinstance(messages, Mapping) or not isinstance(messages, (list, tuple)):
        messages = [messages]
    total = {"input_tokens": 0, "output_tokens": 0}
    #: Only the cache keys a provider actually reported. A two-key dict initialised to zero and
    #: emitted whole as soon as **either** key appeared made ``cache_write_tokens: 0`` this
    #: code's claim wearing the provider's clothes.
    cache: dict[str, int] = {}
    seen = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            continue
        counts = {key: usage.get(key) for key in total}
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in counts.values()):
            return None
        seen = True
        for key, value in counts.items():
            total[key] += int(value)  # type: ignore[arg-type]
        details = usage.get("input_token_details")
        if isinstance(details, Mapping):
            for key, source in (
                ("cache_read_tokens", "cache_read"),
                ("cache_write_tokens", "cache_creation"),
            ):
                value = details.get(source)
                if isinstance(value, int) and not isinstance(value, bool):
                    cache[key] = cache.get(key, 0) + value
        out_details = usage.get("output_token_details")
        if isinstance(out_details, Mapping):
            value = out_details.get("reasoning")
            if isinstance(value, int) and not isinstance(value, bool):
                cache["reasoning_tokens"] = cache.get("reasoning_tokens", 0) + value
    if not seen:
        return None
    return {**total, **cache}


def usage_row(*, stage: str, model: Any, messages: Any, turn_index: Any) -> dict[str, Any]:
    """One cost row, with the counts the provider reported and the stage that spent them.

    A provider that reports nothing gets :meth:`Measured.unmeasured`, which the presence test
    can refuse and no total can silently absorb. The literal ``input_tokens: 0`` this replaced
    was on the **real-model** path, and a consumer totalling these rows reads that shape as free.

    ``model_id(model)`` first and ``_llm_type`` only as the fallback. Reversed, every OpenAI turn
    recorded ``model: "openai-chat"`` — a LangChain *class* label — while
    ``knobs_resolved["llm_model"]`` beside it held the real id, on a comparability field.
    """
    reported = reported_tokens(messages)
    if reported is None:
        unmeasured: Measured[int] = Measured.unmeasured(NO_TOKEN_USAGE)
        counts: dict[str, Any] = {"input_tokens": unmeasured, "output_tokens": unmeasured}
    else:
        counts = dict(reported)
    return {
        "turn_index": turn_index,
        "stage": stage,
        "model": model_id(model) or getattr(model, "_llm_type", None) or type(model).__name__,
        **counts,
    }
