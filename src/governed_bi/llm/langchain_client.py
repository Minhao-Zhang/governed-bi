"""LangChain-backed implementations of the model seams.

The project's harnesses are the LangChain stack (server = LangGraph, curator =
deepagents), which are built on LangChain chat models. So the stack-native model
client wraps a LangChain ``BaseChatModel`` / ``Embeddings`` rather than calling a
provider SDK directly. These adapters expose that behind the same
:class:`~governed_bi.llm.ChatClient` / :class:`~governed_bi.llm.Embedder`
protocols the rest of the system programs against, so:

- the server generator, curator proposer, retrieval, and cache are unchanged;
- production runs on LangChain (tracing, structured output, provider swap via
  ``init_chat_model``), and the same LangChain model instance can be handed to
  deepagents / a LangGraph node;
- tests inject LangChain's own fakes (``FakeListChatModel``,
  ``DeterministicFakeEmbedding``) - no network, no key.

``langchain_openai`` is imported lazily inside ``from_config`` so importing this
module needs only ``langchain-core`` (pulled in by the ``agents`` extra), and the
raw-``openai`` clients remain available for a minimal-dependency deployment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ModelConfig


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
        from langchain_openai import ChatOpenAI  # noqa: PLC0415 (lazy: needs the agents extra)

        kwargs: dict[str, Any] = {"model": models.llm_model}
        if models.llm_reasoning_effort:
            # Reasoning models route to the Responses API via this dict.
            kwargs["reasoning"] = {"effort": models.llm_reasoning_effort}
        if models.llm_max_output_tokens:
            kwargs["max_tokens"] = models.llm_max_output_tokens
        key = os.environ.get(models.api_key_env)
        if key:
            kwargs["api_key"] = key
        return cls(ChatOpenAI(**kwargs))

    def complete(self, system: str, user: str) -> str:
        message = self.model.invoke([("system", system), ("human", user)])
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
