"""Eval arm specifications — factories for serve ``configurable`` (ADR 0005 §5).

Knobs match serve defaults. No eval-only permissive overrides.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.scripted_model import ScriptedChatModel

__all__ = ["ArmSpec", "oracle_arm", "stub_arm", "scripted_arm", "live_arm"]

BuildConfigurable = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ArmSpec:
    """One treatment arm: name + how to build LangGraph ``configurable``."""

    name: str
    build_configurable: BuildConfigurable
    #: When True, harness uses :mod:`oracle` instead of ``compile_durable``.
    oracle_only: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


def oracle_arm(*, connector: Any, **extra: Any) -> ArmSpec:
    """Grader ceiling — gold SQL only, no model.

    **Measures nothing unless the questions carry an independent gold** — a
    ``gold_fingerprint``, or ``gold_columns`` + ``gold_rows``. Without one every row is
    ``correct=None`` and the arm's EX is *unmeasured*, because the only comparison left is
    the executed gold against itself, which returns 1.000 for any statement whatsoever.
    """

    def build() -> dict[str, Any]:
        return {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "connector": connector,
            **extra,
        }

    return ArmSpec(name="oracle", build_configurable=build, oracle_only=True)


def live_arm(session: Any, *, name: str = "live", **extra: Any) -> ArmSpec:
    """A real model over a real corpus — the arm that costs money.

    Built from a :class:`~governed_bi.serve.session.Session`, not loose keyword arguments:
    the session is what mints ``corpus_content_hash``, ``prompt_set_hash`` and
    ``knobs_resolved``, the fields every quotability gate reads. A fabricated hash (the
    harness once used ``f"corpus-{arm}"``) makes two runs over *different* corpora compare
    equal — a forged comparison, not a wrong one.

    Pass the returned arm to ``run_arm(..., session=session)``; the harness then takes each
    turn from ``Session.turn`` instead of building one.
    """
    def build() -> dict[str, Any]:
        cfg = dict(session.configurable()["configurable"])
        cfg.update(extra)
        return cfg

    return ArmSpec(name=name, build_configurable=build, extra={"session": session})


def stub_arm(**extra: Any) -> ArmSpec:
    """Serve without ``agent_model`` (F3 stub answer path)."""

    def build() -> dict[str, Any]:
        cfg = {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            **extra,
        }
        cfg.pop("agent_model", None)
        return cfg

    return ArmSpec(name="stub", build_configurable=build)


def scripted_arm(
    *,
    gold_sql_by_qid: Mapping[str, str],
    **extra: Any,
) -> ArmSpec:
    """Scripted model that ``run_query``s the gold SQL for each question.

    The model is rebuilt per question inside the harness via
    ``extra['scripted_sql_lookup']`` — see :func:`model_for_question`.
    """

    def build() -> dict[str, Any]:
        return {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "scripted_sql_lookup": dict(gold_sql_by_qid),
            **extra,
        }

    return ArmSpec(name="scripted", build_configurable=build)


def model_for_question(configurable: Mapping[str, Any], question_id: str) -> Any | None:
    """Build a per-question :class:`ScriptedChatModel` when the arm is scripted."""
    lookup = configurable.get("scripted_sql_lookup")
    if not isinstance(lookup, Mapping):
        return configurable.get("agent_model")
    sql = lookup.get(question_id)
    if not sql:
        return ScriptedChatModel(responses=[AIMessage(content="no gold sql")])
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_query",
                "args": {"sql": str(sql)},
                "id": f"rq-{question_id}",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content=f"executed {question_id}")
    return ScriptedChatModel(responses=[call, final])
