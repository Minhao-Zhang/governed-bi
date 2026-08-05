"""Cost rows for every model call the turn makes — one builder, used by every caller.

**The turn was under-reporting its own cost by six calls.** ``usage`` was written by exactly
one node, ``agent_core``, so the guard's BI-scope gate and the five facet query rewriters spent
tokens that no record ever mentioned. On an answered turn those six hid behind the agent's
several thousand; on a **refused** turn they were the only calls that happened, and the record
said this:

.. code-block:: text

    Q: 'hello'
      guard        = blocked, g_bi_scope, "model judged the question out of scope: 'no'"
      usage        = []
      cost_est_usd = None

The gate really ran, really called a model and really cost 136 tokens — LangSmith has the
trace. The engine's own ledger priced the turn as free. ``measure/price.py`` reads ``usage``,
so every ``cost_est_usd`` in the repository was low and refusals read as costless.

**Rows carry ``stage``, which the agent-only version had no need for.** With one producer the
question "where did this go" had one answer. With seven it is the interesting question — the
split between the agent model and the utility model is a comparability knob
(``llm_utility_model``), and its whole justification is cost and latency, which cannot be
argued from a single total.

**A helper, not a node.** Six call sites in three modules build the same row, and
``tools/check_one_implementation.py`` has already refused a copy of this repository's other
"read the model's output" function. One builder means a provider that changes its usage payload
is fixed once.
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
    beside a zero for the rest would be the defect this function exists to remove.

    Cache counts are included only when the provider reported them: ``measure/price.py`` reads
    an absent ``cache_read_tokens`` as nothing cached, which its docstring justifies from the
    artifacts, while a zero written here would be this code's claim rather than the provider's.
    """
    if isinstance(messages, Mapping) or not isinstance(messages, (list, tuple)):
        messages = [messages]
    total = {"input_tokens": 0, "output_tokens": 0}
    #: Only the cache keys a provider actually reported. It was a two-key dict initialised to
    #: zero and emitted whole as soon as **either** key appeared, so a provider reporting a
    #: cache read also produced ``cache_write_tokens: 0`` — this code's claim wearing the
    #: provider's clothes.
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
    and the price table both know how to refuse. The literal ``input_tokens: 0`` this replaced
    was on the **real-model** path, and ``measure/price.py`` prices that shape as free — which
    is v1's two ladders that produced no USD while reporting successfully.

    ``model_id(model)`` first and ``_llm_type`` only as the fallback. It was the other way
    round, so every OpenAI turn recorded ``model: "openai-chat"`` — a LangChain *class* label —
    while ``knobs_resolved["llm_model"]`` beside it held the real id. One turn, two answers, on
    a comparability field.
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
