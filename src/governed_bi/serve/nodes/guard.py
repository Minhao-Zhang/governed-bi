"""Pre-model input guard node (ADR 0005 §3.3 / ADR 0006 §6).

Writes only ``guard``. The graph conditions on ``guard["outcome"] == "blocked"``
to refuse — this node does not set ``path_kind``.

**Integrator contract.** Pass a
:class:`~governed_bi.govern.policy.GovernancePolicy` as
``config["configurable"]["policy"]`` (not in state — it is not msgpack-serialisable
for the checkpointer). ``guard_rules_enabled`` must be an explicit mapping.
"""

from langchain_core.runnables import RunnableConfig

__all__ = ["guard_node"]


def guard_node(state: dict, config: RunnableConfig) -> dict:
    """Screen ``state["question"]`` with the policy from runnable config."""
    from governed_bi.govern.guard import guard

    policy = config["configurable"]["policy"]
    return {"guard": guard(state["question"], policy)}
