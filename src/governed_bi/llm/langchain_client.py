"""LangChain-backed implementations of the model seams.

The project's harnesses are the LangChain stack (analyst = LangGraph, curator =
deepagents), which are built on LangChain chat models. So the stack-native model
client wraps a LangChain ``BaseChatModel`` / ``Embeddings`` rather than calling a
provider SDK directly. These adapters expose that behind the same
:class:`~governed_bi.llm.ChatClient` / :class:`~governed_bi.llm.Embedder`
protocols the rest of the system programs against, so:

- the analyst generator, curator proposer, retrieval, and cache are unchanged;
- production runs on LangChain (tracing, structured output, provider swap via
  ``init_chat_model``), and the same LangChain model instance can be handed to
  deepagents / a LangGraph node;
- tests inject LangChain's own fakes (``FakeListChatModel``,
  ``DeterministicFakeEmbedding``) - no network, no key.

The provider SDK is imported lazily inside ``from_config`` (keyed on
``ModelConfig.provider``) so importing this module needs only ``langchain-core``
(pulled in by the ``agents`` extra), and the raw-``openai`` clients remain
available for a minimal-dependency deployment. ``provider = "openai"`` builds
``ChatOpenAI`` / ``OpenAIEmbeddings``; ``provider = "bedrock"`` builds
``ChatBedrockConverse`` / ``BedrockEmbeddings`` from ``langchain-aws`` (the
``bedrock`` extra: ``uv sync --extra bedrock``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ModelConfig


def _require_langchain_aws() -> None:
    """Fail with a clear install hint when the ``bedrock`` extra is missing."""
    try:
        import langchain_aws  # noqa: F401, PLC0415
    except ModuleNotFoundError as err:  # pragma: no cover - exercised only sans dep
        raise ModuleNotFoundError(
            "provider = \"bedrock\" needs the 'langchain-aws' package. Install the "
            "extra: `uv sync --extra bedrock` (or `pip install "
            "'governed-bi[bedrock]'`)."
        ) from err


def _message_text(message: Any) -> str:
    """Extract plain text from a LangChain ``AIMessage``.

    Handles both a string ``content`` and the Responses-API content-block list
    (reasoning models), preferring the v1 ``.text`` accessor when present.
    """
    text = getattr(message, "text", None)
    if isinstance(text, str):  # v1 exposes .text as a property returning str
        if text:
            return text.strip()
    elif callable(text):  # older versions exposed .text() as a method
        called = text()
        if isinstance(called, str) and called:
            return called.strip()
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):  # list of content blocks
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return "".join(parts).strip()
    return str(content).strip()


class LangChainChatClient:
    """:class:`ChatClient` over any LangChain ``BaseChatModel``.

    Construct with a model instance (tests pass a fake; deepagents/LangGraph pass
    a shared ``ChatOpenAI``), or via :meth:`from_config` to build a ``ChatOpenAI``
    from :class:`ModelConfig`.
    """

    def __init__(self, model: Any) -> None:
        self.model = model

    @classmethod
    def from_config(cls, models: "ModelConfig") -> "LangChainChatClient":
        if models.provider == "bedrock":
            return cls(_build_bedrock_chat(models))

        from langchain_openai import ChatOpenAI  # noqa: PLC0415 (lazy: needs the agents extra)

        kwargs: dict[str, Any] = {"model": models.llm_model}
        if models.llm_reasoning_effort:
            # Reasoning models route to the Responses API via this dict.
            kwargs["reasoning"] = {"effort": models.llm_reasoning_effort}
        if models.llm_max_output_tokens:
            kwargs["max_tokens"] = models.llm_max_output_tokens
        # Only when set. Sending an explicit temperature to a reasoning model is an
        # API error on some providers, so `None` still means "provider default" — the
        # difference is that the default is now recorded rather than unknown (E5).
        if models.llm_temperature is not None:
            kwargs["temperature"] = models.llm_temperature
        # Bound wall-clock per call so a stalled connection can't hang a turn.
        if models.request_timeout_s is not None:
            kwargs["timeout"] = models.request_timeout_s
        kwargs["max_retries"] = models.max_retries
        key = os.environ.get(models.api_key_env)
        if key:
            kwargs["api_key"] = key
        return cls(ChatOpenAI(**kwargs))

    def complete_with_usage(
        self, system: str, user: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Complete and return ``(text, usage_metadata)`` from this call's response.

        Concurrent serve runs share one client instance; reading an instance field
        after ``complete`` races. Callers that need tokens must take them from this
        return value (M4 N14).
        """
        # Trace nesting: when this runs *inside* a LangGraph/LangChain run (e.g. the
        # serve-path narrator or schema router, called from a graph node), the
        # parent run already carries the tracing callbacks. Inherit them via the
        # ambient RunnableConfig — invoking with our *own* fresh handler would
        # override that inheritance and open a disconnected root trace, so the whole
        # question-answering turn would no longer group as one trace. Only attach a
        # handler when there is no active run (standalone .complete: eval baseline,
        # curator). LangSmith instruments itself from the environment either way.
        from langchain_core.runnables.config import ensure_config  # noqa: PLC0415

        messages = [("system", system), ("human", user)]
        if ensure_config().get("callbacks"):
            # Inside a run: let LangChain propagate the parent trace via contextvar.
            message = self.model.invoke(messages)
        else:
            from ..logging_setup import peek_run_id, peek_turn_id  # noqa: PLC0415
            from ..obs import (  # noqa: PLC0415
                RunContext,
                tracing_invoke_config,
            )

            rid = peek_run_id()
            ctx = (
                RunContext(run_id=rid, turn_id=peek_turn_id())
                if rid is not None
                else None
            )
            cfg = tracing_invoke_config(ctx=ctx)
            config = None if (not cfg["callbacks"] and ctx is None) else cfg
            message = self.model.invoke(messages, config=config)
        usage = getattr(message, "usage_metadata", None)
        return _message_text(message), (dict(usage) if usage else None)

    def complete(self, system: str, user: str) -> str:
        text, _usage = self.complete_with_usage(system, user)
        return text


class LangChainEmbedder:
    """:class:`Embedder` over any LangChain ``Embeddings``.

    Construct with an embeddings instance (tests pass a deterministic fake) or via
    :meth:`from_config` to build ``OpenAIEmbeddings`` from :class:`ModelConfig`.
    """

    def __init__(self, model: Any) -> None:
        self.model = model

    @classmethod
    def from_config(cls, models: "ModelConfig") -> "LangChainEmbedder":
        if models.provider == "bedrock":
            return cls(_build_bedrock_embeddings(models))

        from langchain_openai import OpenAIEmbeddings  # noqa: PLC0415 (lazy)

        kwargs: dict[str, Any] = {"model": models.embedding_model}
        if models.embedding_dimensions:
            kwargs["dimensions"] = models.embedding_dimensions
        # The same timeout and retry policy the chat client gets. These were omitted
        # here, so ``max_retries`` — the one knob meant to govern retry behaviour
        # stack-wide — reached only half the stack, and an embedding call had no
        # wall-clock bound at all. Both matter more under the eval's concurrency
        # knobs: every serve worker embeds its question, and a 429 or a stalled
        # connection on that call fails the turn as a crash.
        if models.request_timeout_s is not None:
            kwargs["timeout"] = models.request_timeout_s
        kwargs["max_retries"] = models.max_retries
        key = os.environ.get(models.api_key_env)
        if key:
            kwargs["api_key"] = key
        return cls(OpenAIEmbeddings(**kwargs))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [list(v) for v in self.model.embed_documents(texts)]

    def embed_one(self, text: str) -> list[float]:
        return list(self.model.embed_query(text))


# --------------------------------------------------------------------------- #
# AWS Bedrock builders (langchain-aws; the ``bedrock`` extra)
# --------------------------------------------------------------------------- #
#
# Credentials are NOT passed here: ChatBedrockConverse / BedrockEmbeddings resolve
# them through boto3's default chain (env AWS_* vars, shared profile, or an
# instance/task role). ``api_key_env`` still gates going live in the stack builder
# — point it at whichever variable must be set for this deployment. Region falls
# back to boto3's own default (``AWS_REGION`` / ``AWS_DEFAULT_REGION``) when
# ``models.region`` is unset.


def _bedrock_reasoning_fields(models: "ModelConfig") -> dict[str, Any]:
    """Translate ``llm_reasoning_effort`` into the Converse request field the model's
    family expects, for ``additionalModelRequestFields``.

    Converse has no portable reasoning parameter: the field name and shape differ per
    family, which is why this used to be left to a hand-written overlay. But an
    unset knob meant a configured effort was **silently dropped** on Bedrock — the
    run then reported an effort it never sent, which is exactly the class of drift the
    run log exists to catch. Translating here keeps the stamp honest.

    - **Anthropic (Claude)** — ``output_config.effort``, the Messages API effort
      parameter, passed through Converse. Note what is deliberately NOT sent: on
      Claude Sonnet 5 / Opus 5 the old ``thinking: {type: "enabled", budget_tokens: N}``
      block is **rejected with a 400**, and adaptive thinking is already on by
      default — so effort is the whole configuration, and emitting a thinking block
      here would break every call.
    - **Amazon Nova** — ``reasoningConfig.maxReasoningEffort`` (``low``/``medium``/``high``
      only; a higher level configured here will be rejected by the API rather than
      silently downgraded).

    An unrecognized family returns ``{}`` rather than guessing a shape: sending the
    wrong field is a hard API error, and silently sending nothing is the bug above.
    Use ``[models].bedrock_request_fields`` for a family this does not cover.
    """
    effort = (models.llm_reasoning_effort or "").strip().lower()
    if not effort or effort == "none":
        return {}
    model = models.llm_model.lower()
    if "anthropic" in model or "claude" in model:
        return {"output_config": {"effort": effort}}
    if "nova" in model:
        return {"reasoningConfig": {"type": "enabled", "maxReasoningEffort": effort}}
    return {}


def _build_bedrock_chat(models: "ModelConfig") -> Any:
    _require_langchain_aws()
    from langchain_aws import ChatBedrockConverse  # noqa: PLC0415 (lazy: bedrock extra)

    kwargs: dict[str, Any] = {"model": models.llm_model}
    if models.region:
        kwargs["region_name"] = models.region
    if models.llm_max_output_tokens:
        kwargs["max_tokens"] = models.llm_max_output_tokens
    # Timeout/retries on Bedrock are botocore-client settings
    # (``config=Config(read_timeout=..., retries=...)``), not top-level kwargs, and
    # are model/region specific — set them per deployment via a local overlay rather
    # than forwarding ``request_timeout_s``/``max_retries`` to args
    # ChatBedrockConverse may reject. (The OpenAI path wires both directly.)
    #
    # Reasoning: the family-specific translation, with an explicit overlay on top so a
    # deployment can correct or extend it without an engine change.
    extra: dict[str, Any] = _bedrock_reasoning_fields(models)
    if models.bedrock_request_fields:
        extra.update(models.bedrock_request_fields)
    if extra:
        kwargs["additional_model_request_fields"] = extra
    # `llm_temperature` is deliberately NOT forwarded: current Claude models reject a
    # non-default sampling parameter outright, so honoring it here would turn a
    # recorded-but-unused default into a 400 on every call.
    return ChatBedrockConverse(**kwargs)


def _build_bedrock_embeddings(models: "ModelConfig") -> Any:
    _require_langchain_aws()
    from langchain_aws import BedrockEmbeddings  # noqa: PLC0415 (lazy: bedrock extra)

    kwargs: dict[str, Any] = {"model_id": models.embedding_model}
    if models.region:
        kwargs["region_name"] = models.region
    return BedrockEmbeddings(**kwargs)
