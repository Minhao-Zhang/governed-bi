"""One token-row builder for every model call. Rows carry ``stage``.

Guard, narrate, reflect, and the rewriting channels spend tokens the agent row hid, and on a
refused turn they were the only calls. The agent and utility models are separate settings,
so one total cannot show which spent them. ``channel_schema`` calls no model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governed_bi.registry.quantity import Measured
from governed_bi.serve.runtime import model_id

__all__ = ["NO_TOKEN_USAGE", "reported_tokens", "usage_row"]

NO_TOKEN_USAGE = "the provider returned no usage_metadata carrying both token counts"


def reported_tokens(messages: Any) -> dict[str, int] | None:
    """Provider-reported token counts summed across messages, or ``None``.

    A payload missing either count as an integer is not a measurement. Cache counts are
    included only when the provider reported them; a zero would be this code's claim.
    """
    if isinstance(messages, Mapping) or not isinstance(messages, (list, tuple)):
        messages = [messages]
    total = {"input_tokens": 0, "output_tokens": 0}
    # One per AIMessage with usage, so a loop's row shows round trips, not only the sum.
    # The proxy reports no cache_read_tokens, so this count is the route to an exact figure
    # rather than a bound.
    calls = 0
    #: Only the cache keys a provider reported. A two-key dict initialised to zero and
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
        calls += 1
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
    return {**total, **cache, "model_calls": calls}


def usage_row(*, stage: str, model: Any, messages: Any, turn_index: Any) -> dict[str, Any]:
    """One cost row: provider counts plus the stage that spent them.

    No report becomes :meth:`Measured.unmeasured`, which a total cannot absorb as free.
    ``model_id`` first: ``_llm_type`` recorded every OpenAI turn as ``openai-chat``.
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
