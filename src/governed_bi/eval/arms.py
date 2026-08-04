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

__all__ = ["ArmSpec", "oracle_arm", "stub_arm", "scripted_arm"]

BuildConfigurable = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ArmSpec:
    """One treatment arm: name + how to build LangGraph ``configurable``."""

    name: str
    build_configurable: BuildConfigurable
    #: When True, harness uses :mod:`oracle` instead of ``compile_graph``.
    oracle_only: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


def oracle_arm(*, connector: Any, **extra: Any) -> ArmSpec:
    """Free grader ceiling — gold SQL only, no model."""

    def build() -> dict[str, Any]:
        return {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "connector": connector,
            **extra,
        }

    return ArmSpec(name="oracle", build_configurable=build, oracle_only=True)


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
