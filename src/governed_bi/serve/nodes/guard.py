"""Pre-model input guard node (ADR 0005 §3.3 / ADR 0006 §6).

Writes only ``guard``. The graph conditions on ``guard["outcome"] == "blocked"``
to refuse — this node does not set ``path_kind``.

**Integrator contract.** Pass a
:class:`~governed_bi.govern.policy.GovernancePolicy` as
``config["configurable"]["policy"]`` (not in state — it is not msgpack-serialisable
for the checkpointer). ``guard_rules_enabled`` must be an explicit mapping.
"""

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import configurable

__all__ = ["guard_node"]


def guard_node(state: dict, config: RunnableConfig) -> dict:
    """Screen ``state["question"]`` with the policy from runnable config.

    Reads through :func:`~governed_bi.serve.runtime.configurable` rather than subscripting
    ``config["configurable"]`` directly. That is not style: the reader is the one place that
    can refuse a request's attempt to name a run constant, and a node reaching around it is a
    node a client can hand its own ``policy`` to. This was the second of two such nodes.

    ``KeyError`` on a missing policy is deliberate and unchanged — ``guard_rules_enabled``
    ships ``UNSET`` and "no policy" must not become "no guard".
    """
    from governed_bi.govern.guard import guard

    policy = configurable(config)["policy"]
    return {"guard": guard(state["question"], policy)}
