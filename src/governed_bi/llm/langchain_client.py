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
        # Bound wall-clock per call so a stalled connection can't hang a turn.
        if models.request_timeout_s is not None:
            kwargs["timeout"] = models.request_timeout_s
        kwargs["max_retries"] = models.max_retries
        key = os.environ.get(models.api_key_env)
        if key:
            kwargs["api_key"] = key
        return cls(ChatOpenAI(**kwargs))

    def complete(self, system: str, user: str) -> str:
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
            from ..obs import tracing_callbacks  # noqa: PLC0415 (lazy: avoid import cost when unused)

            callbacks = tracing_callbacks()
            config = {"callbacks": callbacks} if callbacks else None
            message = self.model.invoke(messages, config=config)
        return _message_text(message)


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
    # Reasoning ("thinking") config on Bedrock is model-family specific — the
    # Converse request field differs between Anthropic and Nova — so it is not
    # auto-translated from ``llm_reasoning_effort`` here. Set it per deployment
    # via a local overlay if a specific model needs it.
    return ChatBedrockConverse(**kwargs)


def _build_bedrock_embeddings(models: "ModelConfig") -> Any:
    _require_langchain_aws()
    from langchain_aws import BedrockEmbeddings  # noqa: PLC0415 (lazy: bedrock extra)

    kwargs: dict[str, Any] = {"model_id": models.embedding_model}
    if models.region:
        kwargs["region_name"] = models.region
    return BedrockEmbeddings(**kwargs)
