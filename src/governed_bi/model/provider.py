"""Build the OpenAI chat model and embedder.

One gateway. Reasoning effort, timeout and retries use OpenAI's spelling.
``tools=True`` selects the Responses API, the transport that carries tools and
reasoning effort together. ``reasoning_effort_of`` reads that effort back off
the client so the turn record and the call agree.
"""

from __future__ import annotations

from typing import Any, Literal

__all__ = [
    "SURFACE_MODEL_VARS",
    "Surface",
    "chat_model",
    "credential_names",
    "credentials_present",
    "default_embedding_model",
    "embedder",
    "reasoning_effort_of",
]

#: The variable naming each surface's model id. Shared by the server and the CLI,
#: which cannot import each other.
SURFACE_MODEL_VARS: dict[str, str] = {
    "agent": "GOVERNED_BI_MODEL",
    "utility": "GOVERNED_BI_UTILITY_MODEL",
    "embedding": "GOVERNED_BI_EMBEDDING_MODEL",
}

Surface = Literal["agent", "utility", "embedding"]

_OPENAI_KEY = "OPENAI_API_KEY"


def _openai_kwargs(
    *,
    effort: str | None,
    timeout: float | None,
    max_retries: int | None,
    max_output_tokens: int | None,
    tools: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if max_output_tokens is not None:
        kwargs["max_tokens"] = int(max_output_tokens)
    if tools:
        kwargs["use_responses_api"] = True
    if effort:
        kwargs["reasoning_effort"] = effort
    if timeout is not None:
        kwargs["timeout"] = float(timeout)
    if max_retries is not None:
        kwargs["max_retries"] = int(max_retries)
    return kwargs


def reasoning_effort_of(model: Any) -> str | None:
    """The reasoning effort ``model`` will send, or ``None`` when it sends none."""
    direct = getattr(model, "reasoning_effort", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    return None


def chat_model(
    model_id: str,
    *,
    surface: Surface = "agent",
    provider: str | None = None,
    effort: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    max_output_tokens: int | None = None,
    tools: bool = False,
    **extra: Any,
) -> Any:
    """An OpenAI chat model. ``provider`` other than ``openai`` is refused."""
    if provider not in (None, "openai"):
        raise ValueError(
            f"only the openai gateway is built; got {provider!r} for the {surface} surface"
        )
    kwargs = _openai_kwargs(
        effort=effort,
        timeout=timeout,
        max_retries=max_retries,
        max_output_tokens=max_output_tokens,
        tools=tools,
    )
    from langchain.chat_models import init_chat_model  # noqa: PLC0415

    return init_chat_model(model_id, model_provider="openai", **kwargs, **extra)


def credential_names() -> tuple[str, ...]:
    """The variable that satisfies the OpenAI gateway, for an error message."""
    return (_OPENAI_KEY,)


def credentials_present() -> bool:
    """Whether the OpenAI key is available. The value is not returned."""
    from governed_bi.credentials import have

    return have(*credential_names())


def default_embedding_model() -> str:
    """The embedding model id. One gateway, so one default."""
    from .openai_embedder import OPENAI_EMBEDDING_MODEL  # noqa: PLC0415

    return OPENAI_EMBEDDING_MODEL


def embedder(
    model_id: str,
    *,
    provider: str | None = None,
    dimensions: int | None = None,
    max_retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """The OpenAI embedder. ``model`` is qualified ``openai:`` so cache keys stay distinct."""
    if provider not in (None, "openai"):
        raise ValueError(f"only the openai embedder is built; got {provider!r}")
    from .openai_embedder import OpenAIEmbedder  # noqa: PLC0415

    return OpenAIEmbedder(
        model=model_id, dimensions=dimensions, max_retries=max_retries, timeout=timeout
    )
