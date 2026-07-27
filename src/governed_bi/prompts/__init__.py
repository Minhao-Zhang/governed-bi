"""In-repo prompt registry: named, versioned prompt text (see :mod:`.registry`)."""

from .registry import (
    DEFAULT_VARIANT,
    DEFAULTS,
    REGISTRY,
    PromptVariant,
    get,
    parse_cli_overrides,
    prompt_set_hash,
    resolve,
    stages,
    text,
    variants,
)

__all__ = [
    "DEFAULTS",
    "DEFAULT_VARIANT",
    "REGISTRY",
    "PromptVariant",
    "get",
    "parse_cli_overrides",
    "prompt_set_hash",
    "resolve",
    "stages",
    "text",
    "variants",
]
