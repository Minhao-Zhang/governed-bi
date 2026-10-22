"""Pre-model guard. Policy is not msgpack-serialisable, so it rides on configurable.

Deterministic rules run first: they are free. The scope question needs a model.
See ADR 0005 §3.3 and ADR 0006 §6.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from governed_bi.serve.runtime import assets_by_id, configurable, prompt_variants

__all__ = ["guard_node", "scope_catalogue", "scope_system_prompt"]

#: The one token that clears the scope check. Keying on the negative instead would make any
#: unexpected reply read as "in scope", failing **open** exactly when the model was confused.
_IN_SCOPE = "yes"

#: Named so a reply that says *both* can be rejected. It is not a clearing token and nothing
#: keys on it alone.
_OUT_OF_SCOPE = "no"


#: Where a ``bi_scope`` variant wants the served schemas. A variant without it ignores the corpus.
_SCHEMAS_SLOT = "{schemas}"


def scope_catalogue(assets: Iterable[Any]) -> str:
    """One line per schema asset, ``- name: summary``, sorted by name.

    Schema assets, not tables: one summary per namespace, about 45 tokens, instead of a line
    per table.
    """
    lines = []
    for asset in assets:
        kind = getattr(getattr(asset, "asset_type", None), "value", None)
        if kind != "schema":
            continue
        summary = " ".join(str(getattr(asset, "summary", "") or "").split())
        lines.append(f"- {asset.name}: {summary}" if summary else f"- {asset.name}")
    return "\n".join(sorted(lines))


def scope_system_prompt(variants: Mapping[str, str] | None, catalogue: str) -> str | None:
    """The exact system text the scope check sends, or ``None`` when the variant needs schemas
    and ``catalogue`` is empty. The probe hashes this, so a row names the prompt it measured."""
    from governed_bi.registry.prompts import prompt_text

    system = prompt_text("bi_scope", variants)
    if _SCHEMAS_SLOT not in system:
        return system
    return system.replace(_SCHEMAS_SLOT, catalogue) if catalogue else None


def _words(text: str) -> list[str]:
    """Lowercased alphabetic tokens, so punctuation and separators do not hide a word."""
    return re.findall(r"[a-z]+", text.lower())


def _clears_scope(answer: str) -> bool:
    """True only when the first word is yes and the reply does not also say no.

    ``startswith("yes")`` clears ``YES/NO`` and the prompt echoed back. A cleared turn leaves
    no ``detail``; an affirmative that explains itself still clears.
    """
    words = _words(answer)
    return bool(words) and words[0] == _IN_SCOPE and _OUT_OF_SCOPE not in words


async def guard_node(state: dict, config: RunnableConfig) -> dict:
    """Screen the question. Read policy through :func:`~governed_bi.serve.runtime.configurable`.

    Subscripting ``config["configurable"]`` would let a client hand in its own policy.
    ``KeyError`` on a missing policy is deliberate: no policy must not become no guard.
    """
    from governed_bi.govern.guard import guard
    from governed_bi.govern.policy import BI_SCOPE_RULE_ID

    cfg = configurable(config)
    policy = cfg["policy"]
    result = guard(state["question"], policy)
    if result["outcome"] != "clear":
        # A deterministic rule already decided. Do not pay for a model call to confirm it, and
        # do not overwrite a `blocked` with a `clear` — the first refusal is the reason.
        return {"guard": result}
    if not policy.guard_rule_enabled(BI_SCOPE_RULE_ID):
        return {"guard": result}
    # Utility model, not the agent's: this sits in front of every turn. The session resolves
    # the agent fallback once; a config with only `agent_model` gets `error_failed_open`.
    # The branches above called no model, so they return no usage row.
    model = cfg.get("utility_model")
    result, usage = await _bi_scope(
        state["question"],
        model,
        state.get("turn_index", 1),
        prompt_variants(config),
        scope_catalogue(assets_by_id(cfg).values()),
    )
    update: dict = {"guard": result}
    if usage is not None:
        update["usage"] = [usage]
    return update


async def _bi_scope(
    question: str,
    model: Any,
    turn_index: Any,
    variants: Mapping[str, str] | None = None,
    catalogue: str = "",
) -> tuple[Any, dict | None]:
    """Ask whether the question is in scope. Returns ``(GuardResult, usage)``.

    No model, an empty schema list, or a model error is ``error_failed_open``, not ``clear``
    (ADR 0006 §1). ``guard`` has no quotability ``check=``, so an error can still be quoted.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from governed_bi.govern.guard import GuardResult
    from governed_bi.govern.policy import BI_SCOPE_RULE_ID
    from governed_bi.serve.usage import usage_row

    if model is None:
        return (
            GuardResult(
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

    system = scope_system_prompt(variants, catalogue)
    if system is None:
        return (
            GuardResult(
                outcome="error_failed_open",
                rule_id=BI_SCOPE_RULE_ID,
                detail="the bi_scope variant lists the served schemas and the corpus has none",
            ),
            None,
        )

    try:
        reply = await model.ainvoke(
            [SystemMessage(system), HumanMessage(question)],
            # Named, because a turn makes eight model calls and LangChain names every one after
            # the client class. The name is the *registered prompt's*, so the trace and
            # ``registry/prompts.py`` cannot drift apart.
            config={"run_name": "bi_scope"},
        )
        # ``.text`` rather than walking ``content``: the Responses API returns blocks and
        # ``langchain-core`` already concatenates the text ones.
        answer = str(getattr(reply, "text", "") or "").strip().lower()
    except Exception as err:  # noqa: BLE001 — a provider hiccup must not end a real turn
        return (
            GuardResult(
                outcome="error_failed_open",
                rule_id=BI_SCOPE_RULE_ID,
                detail=f"{type(err).__name__}: {err}",
            ),
            None,
        )

    # Built once, before the branch: the call cost the same whether it cleared or blocked, and a
    # row attached to only one outcome makes refusals look cheaper than they are.
    spent = usage_row(stage="guard", model=model, messages=reply, turn_index=turn_index)

    # The *affirmative* is matched, so an apology, a clarifying question or an empty completion
    # refuses. See ``_clears_scope`` for why it is not ``startswith``.
    if _clears_scope(answer):
        return GuardResult(outcome="clear", rule_id=None, detail=None), spent
    result = GuardResult(
        outcome="blocked",
        rule_id=BI_SCOPE_RULE_ID,
        # Ledger-only: ``GuardResult.detail`` is marked "Never surfaced" against rule probing.
        # The public refusal stays ``GUARD_PUBLIC_MESSAGE``.
        detail=f"model judged the question out of scope: {answer[:200]!r}",
    )
    return result, spent
