"""Which gateway serves each model surface, and how one intent spells itself on each.

Three surfaces take a model, and they are configured **independently**: the agent, the
utility model behind the guard's scope gate and the facet rewriters, and the embedder. Each
resolves its own provider and its own model id. The provider falls back to one default so
the ordinary case is a single variable; the model ids do not, because running a cheap
rewriter beside an expensive agent is the normal configuration, not the exception.

**Why a translation layer and not `model_provider=` at each call site.** Three cross-provider
intents -- reasoning effort, per-call timeout, SDK retry count -- have no shared spelling:

=================  ==========================================  ==========================
intent             openai                                      bedrock_converse
=================  ==========================================  ==========================
reasoning effort   ``reasoning_effort="high"``                 ``additional_model_request_
                                                                fields`` -- and the shape
                                                                differs *per model family*
timeout            ``timeout=300.0``                           ``botocore`` ``Config(read_
                                                                timeout=...)``
retries            ``max_retries=3``                           ``Config(retries={"max_
                                                                attempts": ...})``
tools + reasoning  ``use_responses_api=True``                  n/a; Converse is native
=================  ==========================================  ==========================

Passing OpenAI's spelling to Bedrock is not a soft failure. ``use_responses_api`` reaches
``ChatBedrockConverse`` as an unexpected keyword and raises; ``max_retries`` and ``timeout``
are silently *accepted and ignored* by the boto client, which is worse -- the run keeps the
knob in ``knobs_resolved`` and does not honour it, so two arms record the same retry budget
and only one has it. Everything here exists to make that impossible from one place.

**Built on ``init_chat_model``**, not on the provider classes, so a provider LangChain adds
later needs a row in :data:`_TRANSLATORS` rather than a new branch at every call site.

**The table runs both ways.** :func:`reasoning_effort_of` reads the effort back off a built
client, whichever of the three spellings it is wearing, because ``knobs_resolved`` needs one
string and the recording side must not know three shapes. Reading only the OpenAI attribute
is what left ``llm_reasoning_effort`` null on every measured row.

**The provider is a comparability knob, per surface.** ``llm_provider`` already says why:
the same model id behind two gateways is two treatments that would otherwise share one
config hash. That argument does not weaken for the utility model or the embedder, so
``llm_utility_provider`` and ``embedding_provider`` exist beside it.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Literal, Mapping

__all__ = [
    "PROVIDER_VAR",
    "SURFACE_PROVIDER_VARS",
    "AWS_REGION_VARS",
    "Surface",
    "chat_model",
    "credential_names",
    "credentials_present",
    "default_embedding_model",
    "embedder",
    "provider_for",
    "reasoning_effort_of",
    "supported_providers",
]

#: The one variable that sets every surface's provider. A per-surface variable overrides it.
PROVIDER_VAR = "GOVERNED_BI_PROVIDER"

#: Per-surface overrides. Absent means "use :data:`PROVIDER_VAR`".
SURFACE_PROVIDER_VARS: dict[str, str] = {
    "agent": "GOVERNED_BI_MODEL_PROVIDER",
    "utility": "GOVERNED_BI_UTILITY_PROVIDER",
    "embedding": "GOVERNED_BI_EMBEDDING_PROVIDER",
}

#: Region, in precedence order. ``AWS_REGION``/``AWS_DEFAULT_REGION`` are read last so the
#: engine's own variable wins over whatever the surrounding shell exports for other tooling.
AWS_REGION_VARS = ("GOVERNED_BI_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")

Surface = Literal["agent", "utility", "embedding"]

#: Fallback when neither variable is set. Matches the ``llm_provider`` knob default, and is
#: restated here rather than read from the registry because this module must answer before
#: any registry import: ``knobs.py`` is what records the answer, not what decides it.
DEFAULT_PROVIDER = "openai"

#: ``init_chat_model``'s id for each gateway this engine selects. ``bedrock_converse`` and
#: not ``bedrock``: the legacy ``ChatBedrock`` path predates the Converse API and does not
#: carry tool calling uniformly across model families, which is the one thing agent_core
#: cannot do without.
_CHAT_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "bedrock": "bedrock_converse",
}


def supported_providers() -> tuple[str, ...]:
    """Providers this engine can select, for argparse choices and error messages."""
    return tuple(sorted({*_CHAT_PROVIDERS, "proxy"}))


def provider_for(surface: Surface, *, override: str | None = None) -> str:
    """The gateway serving ``surface``: explicit argument, then per-surface var, then default.

    ``override`` is what a ``--provider`` flag passes. It wins over the environment so a
    driver's recorded arm cannot be changed by an exported variable on the machine running it.
    """
    if override:
        return str(override).strip().lower()
    var = SURFACE_PROVIDER_VARS.get(surface)
    for name in (var, PROVIDER_VAR):
        if not name:
            continue
        value = (os.environ.get(name) or "").strip()
        if value:
            return value.lower()
    return DEFAULT_PROVIDER


def aws_region() -> str | None:
    for name in AWS_REGION_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _openai_kwargs(
    *, effort: str | None, timeout: float | None, max_retries: int | None, tools: bool
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if tools:
        # Responses API is what carries tools and reasoning_effort together.
        kwargs["use_responses_api"] = True
    if effort:
        kwargs["reasoning_effort"] = effort
    if timeout is not None:
        kwargs["timeout"] = float(timeout)
    if max_retries is not None:
        kwargs["max_retries"] = int(max_retries)
    return kwargs


def _bedrock_kwargs(
    *, effort: str | None, timeout: float | None, max_retries: int | None, tools: bool
) -> dict[str, Any]:
    """Bedrock's spelling of the same three intents. ``tools`` is unused: Converse is native.

    Timeout and retries go through ``botocore``'s ``Config`` rather than the constructor,
    because ``ChatBedrockConverse`` accepts ``max_tokens`` and friends but hands transport
    settings to the boto client. ``max_attempts`` counts the *first* try, so the engine's
    "retries after the first" becomes ``max_retries + 1``; getting that wrong silently
    halves or doubles a comparability knob.
    """
    from botocore.config import Config  # noqa: PLC0415 (lazy: needs boto3)

    config: dict[str, Any] = {}
    if timeout is not None:
        config["read_timeout"] = float(timeout)
        config["connect_timeout"] = float(timeout)
    if max_retries is not None:
        config["retries"] = {"max_attempts": int(max_retries) + 1, "mode": "adaptive"}

    kwargs: dict[str, Any] = {}
    if config:
        kwargs["config"] = Config(**config)
    region = aws_region()
    if region:
        kwargs["region_name"] = region
    if effort:
        kwargs["additional_model_request_fields"] = _bedrock_reasoning(effort)
    return kwargs


#: Anthropic-on-Bedrock takes a token budget; Nova takes a named effort. There is no shared
#: field, so the mapping is per family and keyed off the model id -- the alternative is
#: passing a field the family rejects, which surfaces as a 400 mid-run rather than at build.
_ANTHROPIC_THINKING_BUDGET: dict[str, int] = {
    "low": 1024, "medium": 4096, "high": 16384, "xhigh": 32768,
}


def _bedrock_reasoning(effort: str, model_id: str = "") -> dict[str, Any]:
    if "nova" in model_id.lower():
        return {"reasoningConfig": {"type": "enabled", "maxReasoningEffort": effort}}
    budget = _ANTHROPIC_THINKING_BUDGET.get(effort.lower())
    if budget is None:
        raise ValueError(
            f"reasoning effort {effort!r} has no Bedrock spelling; expected one of "
            f"{sorted(_ANTHROPIC_THINKING_BUDGET)} or a Nova model id"
        )
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}


_TRANSLATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "openai": _openai_kwargs,
    "bedrock": _bedrock_kwargs,
}


#: The Anthropic-on-Bedrock budget map, read backwards. Derived rather than restated, so a
#: budget edited above cannot leave the reporting side answering with the old number.
_EFFORT_BY_THINKING_BUDGET: dict[int, str] = {
    budget: effort for effort, budget in _ANTHROPIC_THINKING_BUDGET.items()
}


def reasoning_effort_of(model: Any) -> str | None:
    """The reasoning effort ``model`` will actually send, in the engine's vocabulary.

    **The reporting half of the table at the top of this module.** One intent has three
    spellings and the record needs one string, so the inverse belongs beside the forward
    translation rather than at the call site that happens to need it.

    It exists because reading it off a client's attributes does not work in general and the
    place that tried was ``session._resolved_knobs``:
    ``getattr(agent_model, "reasoning_effort", None)`` is only the *OpenAI-direct* spelling.
    On the proxy the effort is inside ``extra_body`` and on Bedrock it is a token budget or a
    Nova ``maxReasoningEffort``, so all 8,106 rows of the six arms in ``runs/eval/`` recorded
    ``llm_reasoning_effort: null`` while ``driver_v4.log`` records ``effort=high``. A
    high-vs-low A/B on the proxy therefore produced two artifacts with identical
    ``knobs_resolved``, which is the incident the knob's own note exists to prevent.

    ``None`` means *this client sends no reasoning configuration* — which is a real state
    (``effort=none`` on a GPT model behind the proxy), not a failure to look.
    """
    # OpenAI-direct first: it is the one spelling that is a plain attribute, and a client
    # carrying it is not carrying either of the other two.
    direct = getattr(model, "reasoning_effort", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    from .proxy_gateway import effort_from_extra_body  # noqa: PLC0415 (lazy, and cheap)

    proxied = effort_from_extra_body(getattr(model, "extra_body", None))
    if proxied:
        return proxied

    return _bedrock_effort(getattr(model, "additional_model_request_fields", None))


def _bedrock_effort(fields: Any) -> str | None:
    """Invert :func:`_bedrock_reasoning`. Both families, because both are produced above."""
    if not isinstance(fields, Mapping):
        return None
    nova = fields.get("reasoningConfig")
    if isinstance(nova, Mapping):
        effort = nova.get("maxReasoningEffort")
        return str(effort) if effort else None
    thinking = fields.get("thinking")
    if isinstance(thinking, Mapping):
        return _EFFORT_BY_THINKING_BUDGET.get(thinking.get("budget_tokens"))
    return None


def chat_model(
    model_id: str,
    *,
    surface: Surface = "agent",
    provider: str | None = None,
    effort: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    tools: bool = False,
    **extra: Any,
) -> Any:
    """Build a chat model for ``surface`` on whichever gateway serves it.

    ``tools=True`` means the caller binds tools to this model, which on OpenAI selects the
    Responses API and elsewhere is already the default transport. ``extra`` is passed to
    ``init_chat_model`` untouched, for the genuinely provider-specific.
    """
    name = provider or provider_for(surface)
    if name == "proxy":
        raise ValueError(
            "the internal proxy is not built here: it needs credentials resolved before the "
            "client exists. Call model.proxy_gateway.chat_model() and record "
            f"llm_provider='proxy' (surface={surface!r})"
        )
    if name not in _CHAT_PROVIDERS:
        raise ValueError(
            f"unknown model provider {name!r} for the {surface} surface; "
            f"expected one of {supported_providers()}. Set {PROVIDER_VAR} or "
            f"{SURFACE_PROVIDER_VARS.get(surface, PROVIDER_VAR)}"
        )

    translate = _TRANSLATORS[name]
    kwargs = translate(effort=effort, timeout=timeout, max_retries=max_retries, tools=tools)
    if name == "bedrock" and effort:
        # Re-derive with the id in hand: the Nova/Anthropic split cannot be seen without it.
        kwargs["additional_model_request_fields"] = _bedrock_reasoning(effort, model_id)

    from langchain.chat_models import init_chat_model  # noqa: PLC0415 (lazy: heavy import)

    return init_chat_model(model_id, model_provider=_CHAT_PROVIDERS[name], **kwargs, **extra)


#: What must be present for a gateway to answer, by **name**. Never read the values here.
#: Bedrock accepts several shapes -- static keys, a profile, or an instance/task role with no
#: variable at all -- so its check is "boto3 can find credentials", not "this name is set";
#: see :func:`credentials_present`.
_CREDENTIAL_NAMES: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"),
    "proxy": ("GOVERNED_BI_PROXY_SECRET",),
}


def credential_names(provider: str) -> tuple[str, ...]:
    """The variable names that could satisfy ``provider``, for an error message."""
    return _CREDENTIAL_NAMES.get(provider, ())


def credentials_present(provider: str) -> bool:
    """Whether ``provider`` has something to authenticate with.

    Bedrock is asked through ``botocore``'s own resolver rather than by reading variables:
    an EC2 instance role, an ECS task role and ``~/.aws/credentials`` all authenticate with
    no environment variable set, and refusing to start in those cases would be a false
    negative on exactly the deployments Bedrock is chosen for.
    """
    if provider == "bedrock":
        try:
            import botocore.session  # noqa: PLC0415
        except ImportError:
            return False
        return botocore.session.get_session().get_credentials() is not None
    return any(os.environ.get(name) for name in credential_names(provider))


def default_embedding_model(provider: str | None = None) -> str:
    """The embedding model id for ``provider``. There is no cross-provider default.

    Each provider's default is defined beside its adapter, because changing one without
    adding a ``Price`` row makes every USD figure on that arm a floor of unknown depth.
    """
    name = provider or provider_for("embedding")
    if name == "bedrock":
        from .bedrock_embedder import BEDROCK_EMBEDDING_MODEL  # noqa: PLC0415

        return BEDROCK_EMBEDDING_MODEL
    from .openai_embedder import OPENAI_EMBEDDING_MODEL  # noqa: PLC0415

    # The proxy serves OpenAI ids, so it shares the default rather than declaring its own.
    return OPENAI_EMBEDDING_MODEL


def embedder(
    model_id: str,
    *,
    provider: str | None = None,
    dimensions: int | None = None,
    max_retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """The :class:`~governed_bi.ports.Embedder` for the embedding surface's gateway.

    Each adapter qualifies its own ``model`` with the provider (``openai:``, ``bedrock:``,
    ``proxy:``) because ``retrieve.semantic.cache_key`` is ``model|dimensions|text`` and
    carries no provider of its own — the prefix is the only thing keeping two gateways
    serving one nominal id out of each other's cached vectors.
    """
    name = provider or provider_for("embedding")
    if name == "openai":
        from .openai_embedder import OpenAIEmbedder  # noqa: PLC0415

        return OpenAIEmbedder(
            model=model_id, dimensions=dimensions, max_retries=max_retries, timeout=timeout
        )
    if name == "bedrock":
        from .bedrock_embedder import BedrockEmbedder  # noqa: PLC0415

        return BedrockEmbedder(
            model=model_id, dimensions=dimensions, max_retries=max_retries, timeout=timeout
        )
    if name == "proxy":
        from .proxy_embedder import ProxyEmbedder  # noqa: PLC0415

        # Its own spelling: the endpoint and the credential are read from the environment,
        # never defaulted in a constructor, so nothing about them is passed through here.
        return ProxyEmbedder(embedding_model=model_id, embedding_dimensions=dimensions)
    raise ValueError(
        f"unknown embedding provider {name!r}; expected one of {supported_providers()}. "
        f"Set {PROVIDER_VAR} or {SURFACE_PROVIDER_VARS['embedding']}"
    )
