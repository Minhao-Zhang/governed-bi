"""Adapter construction for the embedder contract.

The contract file's bodies are the sanctioned edit, so construction lives here.
A silent skip would make one adapter look like two. The skip names the variable, never the value.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.model import OPENAI_API_KEY_VAR, DeterministicEmbedder, OpenAIEmbedder

#: 64 is wide enough that hash collisions do not swamp a short text, and narrow enough to stay cheap.
NARROW_DIMENSIONS = 64

#: The ordering body crosses this. Proving the real batch size of 256 would spend tokens to say the same thing.
SMALL_BATCH = 4


def openai_available() -> bool:
    """Presence only, never the value. The same reader as the DSN fixture, so a key in ``.env`` is seen."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from governed_bi.credentials import have

    return have(OPENAI_API_KEY_VAR)


def make_embedder(adapter: str, **kwargs: Any) -> Any:
    """Skip with the variable's name when the provider is unreachable. Construction issues no request."""
    if adapter == "deterministic":
        return DeterministicEmbedder(**kwargs)
    if adapter == "openai":
        if not openai_available():
            pytest.skip(
                f"{OPENAI_API_KEY_VAR} is not set in the environment. THIS CONTRACT RAN "
                "AGAINST ONE ADAPTER, NOT TWO: only DeterministicEmbedder was exercised, "
                "so the port's 'two independent implementations satisfy one contract' "
                "claim is unverified for OpenAIEmbedder on this run."
            )
        return OpenAIEmbedder(**kwargs)
    raise AssertionError(f"unknown adapter {adapter!r}; ADAPTERS is out of sync")
