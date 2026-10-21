"""Eval config specifications — factories for serve ``configurable`` (ADR 0005 §5).

Settings match serve defaults. No eval-only permissive overrides.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.scripted_model import ScriptedChatModel

__all__ = ["ConfigSpec", "oracle_config", "stub_config", "scripted_config", "live_config"]

BuildConfigurable = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ConfigSpec:
    """One treatment config: name + how to build LangGraph ``configurable``."""

    name: str
    build_configurable: BuildConfigurable
    #: When True, harness uses :mod:`oracle` instead of ``compile_durable``.
    oracle_only: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


def oracle_config(*, connector: Any, **extra: Any) -> ConfigSpec:
    """Grader ceiling — gold SQL only, no model.

    **Measures nothing unless the questions carry an independent gold** — a
    ``gold_fingerprint``, or ``gold_columns`` + ``gold_rows``. Without one every row is
    ``correct=None`` and the config's EX is *unmeasured*, because the only comparison left is
    the executed gold against itself, which returns 1.000 for any statement whatsoever.
    """

    def build() -> dict[str, Any]:
        return {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "connector": connector,
            **extra,
        }

    return ConfigSpec(name="oracle", build_configurable=build, oracle_only=True)


def live_config(session: Any, *, name: str = "live", **extra: Any) -> ConfigSpec:
    """A real model over a real corpus — the config that costs money.

    Built from a :class:`~governed_bi.serve.session.Session`, not loose keyword arguments:
    the session is what mints ``corpus_content_hash``, ``prompt_set_hash`` and
    ``knobs_resolved``, the fields every quotability check reads. A fabricated hash (the
    harness once used ``f"corpus-{config}"``) makes two runs over *different* corpora compare
    equal — a forged comparison, not a wrong one.

    Pass the returned config to ``run_config(..., session=session)``; the harness then takes each
    turn from ``Session.turn`` instead of building one.
    """
    def build() -> dict[str, Any]:
        cfg = dict(session.configurable()["configurable"])
        cfg.update(extra)
        return cfg

    return ConfigSpec(name=name, build_configurable=build, extra={"session": session})


def stub_config(**extra: Any) -> ConfigSpec:
    """Serve without ``agent_model`` (F3 stub answer path)."""

    def build() -> dict[str, Any]:
        cfg = {
            "policy": GovernancePolicy(guard_rules_enabled={}),
            **extra,
        }
        cfg.pop("agent_model", None)
        return cfg

    return ConfigSpec(name="stub", build_configurable=build)


def scripted_config(
    *,
    gold_sql_by_qid: Mapping[str, str],
    **extra: Any,
) -> ConfigSpec:
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

    return ConfigSpec(name="scripted", build_configurable=build)


def model_for_question(configurable: Mapping[str, Any], question_id: str) -> Any | None:
    """Build a per-question :class:`ScriptedChatModel` when the config is scripted."""
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
