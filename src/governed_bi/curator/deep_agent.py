"""The curator build harness as a deepagents agent (D10; docs/curator.md).

The curator is the design's **maximum-autonomy** harness (opposite risk profile to
the fail-closed server): it explores a database and authors the Inference-tier
semantic layer, with an independent adversary refuting each claim before it
commits. That "explore + plan + act over many steps" shape is exactly what
``deepagents`` provides (planning tool, sub-agents, file-system scratchpad), so
the curator agent is a deep agent over a small set of grounded tools:

- ``profile_facts`` - the programmatic Facts tier (never inferred), the
  deterministic foundation the agent reasons over.
- ``run_probe_query`` - a **read-only** SQL probe against the gateway, the
  falsification primitive the adversary/proposer uses to confirm or refute a claim
  before asserting it (this is the ``refute`` seam, done by the model live).
- Write/propose tools (when an :class:`~governed_bi.curator.asset_bag.AssetBag`
  is supplied) that mutate the local Inference-tier corpus in memory.

The model is a LangChain chat model (or a ``"provider:model"`` spec), so it plugs
straight into deepagents; production passes the OpenAI model from
:class:`~governed_bi.llm.LangChainChatClient`. **Construction is offline**
(no key needed, as the tests show); *running* the loop needs a real model, since
the agent's value is the LLM authoring/refutation it performs.

Requires the ``agents`` extra (deepagents). Imported only here, so
``import governed_bi.curator`` never needs deepagents; use
``from governed_bi.curator.deep_agent import build_curator_agent``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from deepagents import create_deep_agent

from ..gateway import Identity
from .profile import profile_database

if TYPE_CHECKING:
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from .asset_bag import AssetBag

# The curator runs with a maximum-autonomy, all-access identity (it profiles and
# probes raw tables). Probes still go through the read-only gateway.
_CURATOR_IDENTITY = Identity(user="curator", all_access=True)

_CURATOR_PROMPT = """\
You are the curator: you author the semantic layer (the Inference tier) for one \
database, and you are your own adversary.

Method (D10):
1. Start from Facts. Call profile_facts to see the tables, columns, types, \
uniqueness, and sample values. Facts are the ground truth; never contradict them.
2. Hypothesize Inference: concise business descriptions, column roles, join paths, \
and reliability caveats. Flag a column "suspect" (DO NOT USE) only when the data \
suggests it is unreliable or a decoy.
3. REFUTE before you assert. For every non-trivial claim, use run_probe_query \
(read-only SQL) to try to falsify it. Keep only claims that survive.
4. Ground everything you write in Facts or a probe result. Do not invent columns, \
values, or relationships you have not observed.
5. When write tools are available, persist surviving claims via propose_join, \
propose_metric, propose_term, propose_few_shot, set_column_description, \
set_table_description, and mark_column_suspect. Prefer verifying seed candidates \
over inventing new ones. Train gold SQL never references decoy/trap columns — \
columns that appear in the catalog but never in working SQL are strong suspect \
candidates.
"""


def _render_facts(tables: list) -> str:
    lines: list[str] = []
    for t in tables:
        header = t.physical_name
        if t.row_count is not None:
            header += f" ({t.row_count} rows)"
        lines.append(header)
        for c in t.columns:
            samples = ", ".join(str(v) for v in c.sample_values[:3])
            line = f"  - {c.physical_name}: {c.logical_type.value}, unique={c.is_unique}"
            if samples:
                line += f", e.g. {samples}"
            lines.append(line)
    return "\n".join(lines) if lines else "(no tables profiled)"


def _render_rows(result: Any, limit: int = 20) -> str:
    if result.row_count == 0:
        return "(no rows)"
    head = " | ".join(result.columns)
    body = "\n".join(" | ".join(str(v) for v in row) for row in result.rows[:limit])
    suffix = f"\n... ({result.row_count} rows total)" if result.row_count > limit else ""
    return f"{head}\n{body}{suffix}"


def curator_tools(
    connector: "Connector",
    schema: str,
    *,
    gateway: "Gateway | None" = None,
    bag: "AssetBag | None" = None,
) -> list[Callable[..., str]]:
    """Build the curator's grounded tool set (closures over the DB access).

    Always includes ``profile_facts``; includes ``run_probe_query`` when a
    read-only ``gateway`` is supplied; includes write/propose tools when an
    :class:`AssetBag` is supplied.
    """

    def profile_facts() -> str:
        """Profile the database's Facts tier: for each table, its columns with
        types, uniqueness, and sample values. Read-only; the deterministic
        foundation you must not contradict."""
        return _render_facts(profile_database(connector, schema=schema))

    tools: list[Callable[..., str]] = [profile_facts]

    if gateway is not None:

        def run_probe_query(sql: str) -> str:
            """Run a read-only SELECT to confirm or falsify a claim about the data.
            Returns the rows (truncated) or an error string. Never mutates data;
            use it to check a hypothesis before asserting it."""
            try:
                result = gateway.execute(sql, _CURATOR_IDENTITY)
            except Exception as err:
                return f"error: {err}"
            return _render_rows(result)

        tools.append(run_probe_query)

    if bag is not None:

        def propose_join(
            left_table: str,
            right_table: str,
            on: str,
            cardinality: str = "many_to_one",
            confidence: float = 0.7,
        ) -> str:
            """Record a JoinAsset between two physical tables (ON equality)."""
            return bag.propose_join(
                left_table, right_table, on, cardinality=cardinality, confidence=confidence
            )

        def propose_metric(
            name: str, base_table: str, expression: str, confidence: float = 0.6
        ) -> str:
            """Record a MetricAsset (aggregate expression over a base table)."""
            return bag.propose_metric(
                name, base_table, expression, confidence=confidence
            )

        def propose_term(
            name: str,
            binding_asset_type: str = "table",
            binding_asset_id: str = "",
            confidence: float = 0.6,
        ) -> str:
            """Record a TermAsset mapping business language to an asset."""
            return bag.propose_term(
                name,
                binding_asset_type=binding_asset_type,
                binding_asset_id=binding_asset_id or None,
                confidence=confidence,
            )

        def propose_few_shot(
            question: str,
            sql: str,
            complexity: str = "simple",
            confidence: float = 0.7,
        ) -> str:
            """Record a FewShotAsset exemplar (question + working SQL)."""
            return bag.propose_few_shot(
                question, sql, complexity=complexity, confidence=confidence
            )

        def set_column_description(
            table: str, column: str, description: str, confidence: float = 0.7
        ) -> str:
            """Set the Inference-tier description for one column."""
            return bag.set_column_description(
                table, column, description, confidence=confidence
            )

        def set_table_description(
            table: str, description: str, confidence: float = 0.7
        ) -> str:
            """Set the Inference-tier description for one table."""
            return bag.set_table_description(table, description, confidence=confidence)

        def mark_column_suspect(
            table: str, column: str, note: str = "DO NOT USE — likely decoy/trap"
        ) -> str:
            """Flag a column as suspect/decoy so the serve path hard-blocks it."""
            return bag.mark_column_suspect(table, column, note=note)

        tools.extend(
            [
                propose_join,
                propose_metric,
                propose_term,
                propose_few_shot,
                set_column_description,
                set_table_description,
                mark_column_suspect,
            ]
        )

    return tools


def build_curator_agent(
    model: Any,
    *,
    connector: "Connector",
    schema: str,
    gateway: "Gateway | None" = None,
    bag: "AssetBag | None" = None,
    system_prompt: str | None = None,
):
    """Build the curator deep agent for one corpus schema namespace.

    ``model`` is a LangChain chat model instance or a ``"provider:model"`` spec
    (e.g. ``"openai:gpt-5.5"``). ``connector`` is used for Facts profiling;
    ``gateway`` (read-only) enables the ``run_probe_query`` falsification tool.
    ``bag`` enables Inference-tier write tools. Returns a compiled agent; invoke
    it with ``{"messages": [{"role": "user", "content": "..."}]}``. Construction
    is offline; running the loop needs a live model.
    """
    return create_deep_agent(
        model=model,
        tools=curator_tools(connector, schema, gateway=gateway, bag=bag),
        system_prompt=system_prompt or _CURATOR_PROMPT,
    )
