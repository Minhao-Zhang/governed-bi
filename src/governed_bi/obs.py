"""Observability wiring: LangSmith, and nothing else.

**LangSmith is the only tracer.** Langfuse was removed on 2026-08-02 (it had been
a second, callback-attached tracer). The deciding fact is billing shape:
LangSmith's Developer tier counts **one trace per root invocation**, so a 48-second
agentic turn with 27 nested runs is one trace — a 20-question debug run costs 20
of the 5,000/month allowance. Self-hosting Langfuse to avoid a cost we do not have
was six containers of ops for nothing.

LangSmith needs no code here. Set ``LANGSMITH_TRACING=true`` (or the legacy
``LANGCHAIN_TRACING_V2=true``) and ``LANGSMITH_API_KEY`` and LangChain/LangGraph
emit traces automatically, so the whole chat run (under ``langgraph dev`` /
Platform) and every model call are traced with zero wiring.

What this module still owns:

- :func:`tracing_config` / :func:`tracing_invoke_config` — the ``metadata`` and
  ``tags`` a run is filterable by in the LangSmith UI. This is the only reason an
  eval ladder's traces can be sliced by arm or by corpus identity.
- :func:`usage_callbacks` — **not a tracer.** A ``UsageMetadataCallbackHandler``
  so deep-agent (curator / SME) token totals can be read back after ``invoke``
  (``analyst.run_log.usage_callback_entries``). It shared a function with the
  Langfuse handler and did not share a purpose; it survives the removal.
- :func:`flush_tracing` — deterministic export before a short-lived process exits.

**Traces carry inputs and outputs in full, unconditionally, and that is a
decision.** This repo is not production, and sensitive columns are filtered at the
datasource before they can reach a tool message, so there is nothing for a trace
mask to do here. The Langfuse client's ``mask`` hook and the once-per-process
"unmasked export" acknowledgement went with Langfuse on 2026-08-02 rather than
being ported. A production deployment would need a masking layer at this seam;
this repo has none, deliberately. (``viz.presenter._redact_provenance_for_client``
is unrelated — it guards an HTTP response body served to a caller, not a trace
export.)

See ``.env.example`` for the variable names.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("governed_bi.obs")


@dataclass(frozen=True)
class RunContext:
    """Correlation fields for one serve / curator / eval invoke.

    ``identity`` is carried for local use (logs, audit) but is **never** placed in
    trace metadata. Not a masking measure — nothing here is masked — just that a
    user principal is not an experiment variable and has no business in a trace
    filter.

    ``corpus_pin`` and ``corpus_content_hash`` are not the same kind of thing and
    the difference is load-bearing — see :func:`tracing_config`.
    """

    run_id: str
    turn_id: str | None = None
    corpus_pin: str | None = None
    arm: str | None = None
    schema: str | None = None
    corpus_content_hash: str | None = None
    prompt_set_hash: str | None = None
    identity: str | None = None


def tracing_config(ctx: RunContext) -> dict[str, Any]:
    """RunnableConfig fragment carrying LangSmith ``metadata`` and ``tags``.

    Tags are the coarse filter in the LangSmith UI; metadata is the exact one. A
    multi-arm ladder is unreadable without ``arm``, which is why it is both.

    Two corpus fields, deliberately, because they answer different questions:

    ``corpus_pin``
        The datasource *pin*, i.e. ``Settings.datasource.corpus_pin``: a BIRD
        ``db_id`` in a single-schema deployment and the literal ``"datalake"``
        under the pooled driver. It is a **mode label, not an identity** — every
        pooled run in the repo's history carries the same value — and it is kept
        only because it joins to the ``corpus_pin`` column of the durable run log.
    ``corpus_content_hash``
        The digest of the corpus that was actually served: the identity of the
        *treatment*, the thing that differs between two arms and matches across
        replicates. This is the field a trace has to carry to be attributable to
        an experiment. It was absent until 2026-08-02 while ``corpus_pin`` sat in
        its place looking like it.

    ``prompt_set_hash`` is here for the same reason and by the same precedent —
    see ``provenance.prompt_set_hash``: *"a fixed field list is exactly how prompt
    identity went unhashed in the first place."* The corpus side had not learned
    it.

    ``identity`` is deliberately omitted — see :class:`RunContext`.
    """
    tags = [t for t in (ctx.arm, ctx.schema, "governed-bi") if t]
    metadata: dict[str, Any] = {"run_id": ctx.run_id}
    for key, value in (
        ("turn_id", ctx.turn_id),
        ("corpus_pin", ctx.corpus_pin),
        ("arm", ctx.arm),
        ("schema", ctx.schema),
        ("corpus_content_hash", ctx.corpus_content_hash),
        ("prompt_set_hash", ctx.prompt_set_hash),
    ):
        if value is not None:
            metadata[key] = value
    return {"metadata": metadata, "tags": list(tags)}


_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(*names: str) -> bool:
    """True if any named env var is set to a truthy value."""
    for name in names:
        if os.environ.get(name, "").strip().lower() in _TRUTHY:
            return True
    return False


def langsmith_enabled() -> bool:
    """Whether LangSmith tracing is turned on by the environment.

    Accepts ``LANGSMITH_TRACING`` (current LangSmith docs) or the legacy
    ``LANGCHAIN_TRACING_V2``, plus ``LANGSMITH_API_KEY``. Two conditions and
    nothing else: turning tracing on exports run content in full, which is the
    intended behaviour here (see the module docstring), so there is no
    acknowledgement to obtain and no warning to emit.

    It gates nothing — LangChain reads the same variables itself. It is a
    predicate for callers that want to *report* whether tracing is on (and, until
    2026-08-02, the host of the unmasked-export warning, which is why it looks
    like it should do more than it does).
    """
    return _env_truthy("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2") and bool(
        os.environ.get("LANGSMITH_API_KEY")
    )


def usage_callbacks(*, enabled: bool = True) -> list:
    """``[UsageMetadataCallbackHandler()]`` when ``enabled``, else ``[]``.

    **Not tracing.** This used to be ``tracing_callbacks`` and returned a Langfuse
    handler plus, optionally, a usage handler; with Langfuse gone only the usage
    handler is left, and the name now says so. It exists so ``curator/pipeline.py``
    and ``curator/sme.py`` can read deep-agent token totals back off the handler
    after ``invoke`` (``analyst.run_log.usage_callback_entries``, F6). Deleting the
    callback plumbing along with Langfuse would have silently zeroed curator/SME
    token accounting — already the largest unpriced line in a run.

    LangSmith is not here: it instruments itself from the environment.
    """
    if not enabled:
        return []
    try:
        from langchain_core.callbacks import UsageMetadataCallbackHandler

        return [UsageMetadataCallbackHandler()]
    except Exception:
        return []


def tracing_invoke_config(
    *,
    with_usage: bool = False,
    ctx: RunContext | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Full RunnableConfig fragment: metadata/tags when ``ctx`` is set, plus the
    usage callback when ``with_usage``."""
    cfg: dict[str, Any] = {"callbacks": usage_callbacks(enabled=with_usage)}
    if ctx is not None:
        cfg.update(tracing_config(ctx))
    cfg.update(extra)
    return cfg


def flush_tracing() -> None:
    """Block until pending traces have been exported (safe no-op when off) — LF1.

    Written for Langfuse, kept for LangSmith, because the failure mode is the
    tracer-agnostic one: exports happen on a background thread behind an
    ``atexit`` hook that SIGTERM / ``os._exit`` / CI cancellation bypasses, and the
    final batch is lost. Short-lived processes (eval, curator, CLI) call this
    before exit so the tail of a run is delivered whatever the exit path.

    ``wait_for_all_tracers`` is LangChain's own drain for exactly this; it is a
    no-op when no LangSmith tracer was ever instantiated, so there is nothing to
    gate it on.
    """
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:
        logger.debug("LangSmith tracer flush failed", exc_info=True)
