"""Server middleware (hooks) — where fail-closed lives (Architecture §3).

- ``before_model`` — inject context: working memory (D8), RLS scope (D7), the
  semantic-layer router. Deterministic; runs before every model call.
- ``wrap_tool_call`` — gate or veto actions: the five guardrails (``gateway.
  guardrails``) plus the refuse-gate (D5), which runs *concurrently*. Any veto
  is final (fail-closed).

These are the deterministic code on loop events; as models improve you shrink
the hooks, you don't rewrite them (engine vs fuel).
"""

from __future__ import annotations


def before_model(state: dict) -> dict:
    """Inject working memory + RLS scope + semantic-layer routing into state."""
    raise NotImplementedError("before_model hook pending")


def wrap_tool_call(state: dict, call: object) -> object:
    """Run guardrails + refuse-gate around a tool call; veto is final."""
    raise NotImplementedError("wrap_tool_call hook pending")
