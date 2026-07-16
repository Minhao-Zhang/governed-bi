"""The curator build harness as a deepagents agent (D10; docs/curator.md).

The curator is the design's **maximum-autonomy** harness (opposite risk profile to
the fail-closed Analyst): it explores a database and authors the Inference-tier
semantic layer. That "explore + plan + act over many steps" shape is exactly what
``deepagents`` provides (planning tool, filesystem scratchpad via
``FilesystemBackend``), so the curator agent is a deep agent over a small set of
grounded tools:

- ``read_corpus`` — live Facts + Inference already written (filterable).
- ``run_probe_query`` — read-only SQL probe against the gateway.
- Six validated write tools (``upsert_*`` / ``annotate_*``) that mutate the
  in-memory :class:`~governed_bi.curator.asset_bag.AssetBag`.
- Built-in file tools (``ls``/``read_file``/``write_file``/``edit_file``/``grep``)
  via ``FilesystemBackend(run_dir)`` — **only** for ``clarifications.jsonl``.

Requires the ``agents`` extra (deepagents). Imported only here, so
``import governed_bi.curator`` never needs deepagents; use
``from governed_bi.curator.deep_agent import build_curator_agent``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from ..gateway import Identity
from .prompts import _CURATOR_PROMPT, _PHASE_A_PROMPT, _PHASE_B_PROMPT

if TYPE_CHECKING:
    from ..gateway import Gateway
    from ..gateway.connectors.base import Connector
    from .asset_bag import AssetBag

# Re-export prompts for callers that import from this module.
__all__ = [
    "build_curator_agent",
    "curator_tools",
    "_CURATOR_PROMPT",
    "_PHASE_A_PROMPT",
    "_PHASE_B_PROMPT",
]

# The curator runs with a maximum-autonomy, all-access identity (it profiles and
# probes raw tables). Probes still go through the read-only gateway.
_CURATOR_IDENTITY = Identity(user="curator", all_access=True)


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
    certified_writes: bool = False,
) -> list[Callable[..., str]]:
    """Build the curator's grounded tool set.

    Always includes ``read_corpus`` when a bag is supplied (and a stub error
    otherwise). Includes ``run_probe_query`` when a read-only ``gateway`` is
    supplied; includes the six write tools when ``bag`` is supplied.
    ``certified_writes`` stamps human/certified provenance on Phase B writes.
    """
    del connector, schema  # bag already holds the profiled Facts

    tools: list[Callable[..., str]] = []

    if bag is not None:

        def read_corpus(table: str = "", kind: str = "") -> str:
            """Return the live corpus — Facts and Inference written so far.
            Optional table (physical name) and kind (table/join/metric/term/
            few_shot) filters bound context on wide schemas."""
            return bag.read_corpus(
                table=table or None,
                kind=kind or None,
            )

        tools.append(read_corpus)
    else:

        def read_corpus(table: str = "", kind: str = "") -> str:
            """Return the live corpus (requires an AssetBag)."""
            del table, kind
            return "error: no corpus bag attached"

        tools.append(read_corpus)

    if gateway is not None:

        def run_probe_query(sql: str) -> str:
            """Run a read-only SELECT to confirm or falsify a claim about the data.
            Returns the rows (truncated) or an error string. Never mutates data."""
            try:
                result = gateway.execute(sql, _CURATOR_IDENTITY)
            except Exception as err:
                return f"error: {err}"
            return _render_rows(result)

        tools.append(run_probe_query)

    if bag is not None:

        def upsert_join(
            left_table: str,
            right_table: str,
            on: str,
            cardinality: str = "many_to_one",
            confidence: float = 0.7,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Record a validated JoinAsset between two physical tables."""
            return bag.upsert_join(
                left_table,
                right_table,
                on,
                cardinality=cardinality,
                confidence=confidence,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        def upsert_metric(
            name: str,
            base_table: str,
            expression: str,
            confidence: float = 0.6,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Record a validated MetricAsset (aggregate over a base table)."""
            return bag.upsert_metric(
                name,
                base_table,
                expression,
                confidence=confidence,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        def upsert_term(
            name: str,
            binding_asset_type: str = "table",
            binding_asset_id: str = "",
            confidence: float = 0.6,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Record a validated TermAsset mapping business language to an asset."""
            return bag.upsert_term(
                name,
                binding_asset_type=binding_asset_type,
                binding_asset_id=binding_asset_id or None,
                confidence=confidence,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        def upsert_few_shot(
            question: str,
            sql: str,
            complexity: str = "simple",
            confidence: float = 0.7,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Record a validated FewShotAsset (question + working SQL)."""
            return bag.upsert_few_shot(
                question,
                sql,
                complexity=complexity,
                confidence=confidence,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        def annotate_table(
            table: str,
            description: str = "",
            confidence: float = 0.0,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Set table-level Inference fields (description, confidence)."""
            return bag.annotate_table(
                table,
                description=description or None,
                confidence=confidence if confidence > 0 else None,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        def annotate_column(
            table: str,
            column: str,
            description: str = "",
            role: str = "",
            reliability: str = "",
            suspect: bool = False,
            note: str = "",
            confidence: float = 0.0,
            certified: bool = False,
            answered_by: str = "",
        ) -> str:
            """Set column Inference: description, role, reliability, and/or suspect."""
            return bag.annotate_column(
                table,
                column,
                description=description or None,
                role=role or None,
                reliability=reliability or None,
                suspect=True if suspect else None,
                note=note or None,
                confidence=confidence if confidence > 0 else None,
                certified=certified or certified_writes,
                answered_by=answered_by or None,
            )

        tools.extend(
            [
                upsert_join,
                upsert_metric,
                upsert_term,
                upsert_few_shot,
                annotate_table,
                annotate_column,
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
    run_dir: Path | str | None = None,
    certified_writes: bool = False,
):
    """Build the curator deep agent for one corpus schema namespace.

    ``model`` is a LangChain chat model instance or a ``"provider:model"`` spec.
    ``run_dir`` wires ``FilesystemBackend`` so built-in file tools persist
    ``clarifications.jsonl`` on disk. ``certified_writes`` enables Phase B
    human/certified stamping defaults.
    """
    backend = None
    if run_dir is not None:
        backend = FilesystemBackend(root_dir=str(Path(run_dir)), virtual_mode=True)

    kwargs: dict[str, Any] = {
        "model": model,
        "tools": curator_tools(
            connector,
            schema,
            gateway=gateway,
            bag=bag,
            certified_writes=certified_writes,
        ),
        "system_prompt": system_prompt or _PHASE_A_PROMPT,
    }
    if backend is not None:
        kwargs["backend"] = backend
    return create_deep_agent(**kwargs)
