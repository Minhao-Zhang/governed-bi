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
declared there. Both produce the same
:class:`~governed_bi.govern.guard.GuardVerdict`, because the record has one ``guard`` field and
a second shape beside it would be a second vocabulary for one decision.

The deterministic rules run **first**, and that order is not arbitrary: they are free, and a
question that trips one should not cost a model call to refuse.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import configurable

__all__ = ["guard_node"]

#: The one token that clears the scope gate. See ``register/prompts.py``: keying on the negative
#: would make any unexpected reply read as "in scope", so the gate would fail **open** exactly
#: when the model was confused.
_IN_SCOPE = "yes"


def guard_node(state: dict, config: RunnableConfig) -> dict:
    """Screen ``state["question"]`` with the policy from runnable config.

    Reads through :func:`~governed_bi.serve.runtime.configurable` rather than subscripting
    ``config["configurable"]`` directly. That is not style: the reader is the one place that
    can refuse a request's attempt to name a run constant, and a node reaching around it is a
    node a client can hand its own ``policy`` to. This was the second of two such nodes.

    ``KeyError`` on a missing policy is deliberate and unchanged — ``guard_rules_enabled``
    ships ``UNSET`` and "no policy" must not become "no guard".
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
    return {"guard": _bi_scope(state["question"], cfg.get("agent_model"))}


def _bi_scope(question: str, model: Any) -> Any:
    """Ask a model whether the question is in scope. Returns a ``GuardVerdict``.

    **Enabled with no model is ``error_failed_open``, not ``clear``.** The rule was switched on
    and could not run, and ``register/record.py`` is explicit that a gate leaving a trace only
    when it fires cannot afterwards be told from a gate that was never wired up. The sentinel is
    countable and joins a run's quotability gates, which is the right amount of alarm for "the
    operator asked for a check this deployment cannot perform".

    **A model error is also ``error_failed_open``, and the question goes through.** Same choice
    ADR 0006 §1 makes for a raising deterministic rule: this gate is about *scope*, not safety, so
    failing a real turn on a provider hiccup would trade an answer somebody wants for a
    hypothetical one. Recorded, so the trade is countable rather than invisible.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.govern.guard import BI_SCOPE_RULE_ID, GuardVerdict
    from governed_bi.register.prompts import prompt_text

    if model is None:
        return GuardVerdict(
            outcome="error_failed_open",
            rule_id=BI_SCOPE_RULE_ID,
            detail="the BI-scope rule is enabled but no agent_model is configured",
        )

    try:
        reply = model.invoke([SystemMessage(prompt_text("bi_scope")), HumanMessage(question)])
        # ``.text`` rather than walking ``content``: the Responses API returns blocks and
        # ``langchain-core`` already concatenates the text ones. Decision #1 records that v1's
        # three layers over ``BaseChatModel`` were a mistake for exactly this reason.
        answer = str(getattr(reply, "text", "") or "").strip().lower()
    except Exception as err:  # noqa: BLE001 — a provider hiccup must not end a real turn
        return GuardVerdict(
            outcome="error_failed_open",
            rule_id=BI_SCOPE_RULE_ID,
            detail=f"{type(err).__name__}: {err}",
        )

    # ``startswith`` rather than equality: a model answering "Yes." or "YES\n" has answered. The
    # *affirmative* is what is matched, so anything else — an apology, a clarifying question, an
    # empty completion — refuses rather than passes.
    if answer.startswith(_IN_SCOPE):
        return GuardVerdict(outcome="clear", rule_id=None, detail=None)
    return GuardVerdict(
        outcome="blocked",
        rule_id=BI_SCOPE_RULE_ID,
        # Free text, and therefore ledger-only: ``GuardVerdict.detail`` is marked "Never
        # surfaced" against rule probing, and this is the model's words about the user's
        # question. The public refusal stays ``GUARD_PUBLIC_MESSAGE``.
        detail=f"model judged the question out of scope: {answer[:200]!r}",
    )
