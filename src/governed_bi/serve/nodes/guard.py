"""Pre-model input guard node (ADR 0005 §3.3 / ADR 0006 §6).

Writes only ``guard``. The graph conditions on ``guard["outcome"] == "blocked"``
to refuse — this node does not set ``path_kind``.

**Integrator contract.** Pass a
:class:`~governed_bi.govern.policy.GovernancePolicy` as
``config["configurable"]["policy"]`` (not in state — it is not msgpack-serialisable
for the checkpointer). ``guard_rules_enabled`` must be an explicit mapping.

**Two gates, one verdict.** The five deterministic rules live in ``govern/guard.py`` and are
pure. The sixth — *is this a BI question at all?* — needs a model, and ``govern/`` must stay
importable with no model, no settings and no I/O, so the call is here and only the rule id is
declared there. Both produce the same :class:`~governed_bi.govern.guard.GuardVerdict`: the
record has one ``guard`` field, and a second shape would be a second vocabulary for it.

The deterministic rules run **first** because they are free, and a question that trips one
should not cost a model call to refuse.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import configurable

__all__ = ["guard_node"]

#: The one token that clears the scope gate. Keying on the negative instead would make any
#: unexpected reply read as "in scope", failing **open** exactly when the model was confused.
_IN_SCOPE = "yes"


async def guard_node(state: dict, config: RunnableConfig) -> dict:
    """Screen ``state["question"]`` with the policy from runnable config.

    Reads through :func:`~governed_bi.serve.runtime.configurable` and never subscripts
    ``config["configurable"]``: that reader is the one place that can refuse a request's attempt
    to name a run constant, so a node reaching around it is a node a client can hand its own
    ``policy`` to.

    ``KeyError`` on a missing policy is deliberate — ``guard_rules_enabled`` ships ``UNSET`` and
    "no policy" must not become "no guard".
    """
    from governed_bi.govern.guard import BI_SCOPE_RULE_ID, guard

    cfg = configurable(config)
    policy = cfg["policy"]
    verdict = guard(state["question"], policy)
    if verdict["outcome"] != "clear":
        # A deterministic rule already decided. Do not pay for a model call to confirm it, and
        # do not overwrite a `blocked` with a `clear` — the first refusal is the reason.
        return {"guard": verdict}
    if not policy.guard_rule_enabled(BI_SCOPE_RULE_ID):
        return {"guard": verdict}
    # Both branches above return without a usage row because neither called a model.
    #
    # **The utility model, not the agent's**: a one-word classification in front of every turn,
    # so its latency is the delay before anything appears. Read without an `or agent_model`
    # beside it — `Session.configurable` resolves that fallback once, and a second copy here is
    # a rule six call sites must spell identically. A caller who hand-builds a config with only
    # `agent_model` gets `error_failed_open`, which is what that sentinel is for.
    model = cfg.get("utility_model")
    verdict, usage = await _bi_scope(state["question"], model, state.get("turn_index", 1))
    update: dict = {"guard": verdict}
    if usage is not None:
        update["usage"] = [usage]
    return update


async def _bi_scope(question: str, model: Any, turn_index: Any) -> tuple[Any, dict | None]:
    """Ask a model whether the question is in scope. Returns ``(GuardVerdict, usage row)``.

    The usage row is why this returns a pair: without it a turn the gate refused records
    ``usage: []`` and ``cost_est_usd: None`` having really spent ~136 tokens. ``None`` when no
    model call happened, which keeps "spent nothing" distinct from "spent and did not say".

    **Enabled with no model is ``error_failed_open``, not ``clear``**, and so is a model error
    — the question goes through either way, as ADR 0006 §1 has a raising deterministic rule do,
    because this gate is about *scope* and not safety. The sentinel is countable and joins a
    run's quotability gates, so a gate that was never wired up cannot pass for one that never
    fired (``register/record.py``).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.govern.guard import BI_SCOPE_RULE_ID, GuardVerdict
    from governed_bi.register.prompts import prompt_text
    from governed_bi.serve.usage import usage_row

    if model is None:
        return (
            GuardVerdict(
                outcome="error_failed_open",
                rule_id=BI_SCOPE_RULE_ID,
                detail=(
                    "the BI-scope rule is enabled but no utility_model is configured "
                    "(Session.configurable falls this back to agent_model, so a session with "
                    "either would have one)"
                ),
            ),
            None,
        )

    try:
        reply = await model.ainvoke(
            [SystemMessage(prompt_text("bi_scope")), HumanMessage(question)],
            # Named, because a turn makes eight model calls and LangChain names every one after
            # the client class. The name is the *registered prompt's*, so the trace and
            # ``register/prompts.py`` cannot drift apart.
            config={"run_name": "bi_scope"},
        )
        # ``.text`` rather than walking ``content``: the Responses API returns blocks and
        # ``langchain-core`` already concatenates the text ones.
        answer = str(getattr(reply, "text", "") or "").strip().lower()
    except Exception as err:  # noqa: BLE001 — a provider hiccup must not end a real turn
        return (
            GuardVerdict(
                outcome="error_failed_open",
                rule_id=BI_SCOPE_RULE_ID,
                detail=f"{type(err).__name__}: {err}",
            ),
            None,
        )

    # Built once, before the branch: the call cost the same whether it cleared or blocked, and a
    # row attached to only one outcome makes refusals look cheaper than they are.
    spent = usage_row(stage="guard", model=model, messages=reply, turn_index=turn_index)

    # ``startswith`` rather than equality, so "Yes." and "YES\n" count. The *affirmative* is
    # matched, so an apology, a clarifying question or an empty completion refuses.
    if answer.startswith(_IN_SCOPE):
        return GuardVerdict(outcome="clear", rule_id=None, detail=None), spent
    verdict = GuardVerdict(
        outcome="blocked",
        rule_id=BI_SCOPE_RULE_ID,
        # Ledger-only: ``GuardVerdict.detail`` is marked "Never surfaced" against rule probing.
        # The public refusal stays ``GUARD_PUBLIC_MESSAGE``.
        detail=f"model judged the question out of scope: {answer[:200]!r}",
    )
    return verdict, spent
