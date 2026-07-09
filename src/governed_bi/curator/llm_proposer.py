"""Curator loop step 2 (LLM): author the Inference-tier prose - the moat.

:class:`~governed_bi.curator.proposer.HeuristicProposer` fills what Facts alone
determine (roles, provenance) and deliberately leaves ``description`` and
reliability caveats to a model. This module is that model-backed proposer. It is
where the semantic layer earns its keep: precise column/table descriptions and,
critically, the **reliability caveats that flag decoy / unreliable columns**
(``reliability.status = suspect`` + a "DO NOT USE" note), which the server injects
into SQL generation and enforces at guardrail L3 - the lever that wins the
decoy-touch metric.

Design stance (same "deterministic core + real seam" split as the rest of the
system): :class:`LlmProposer` composes *over* a base :class:`Proposer` (the
heuristic by default), so roles/provenance are decided deterministically and the
LLM only adds prose it can justify from the table's Facts. It never touches the
Facts tier, and a malformed model response degrades to the base proposal rather
than fabricating or crashing (fail-safe). The ``ChatClient`` is injected, so
tests drive it with a scripted client and production injects the OpenAI client.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from ..corpus.schemas import Column, Reliability, ReliabilityStatus, TableAsset
from .proposer import HeuristicProposer, Proposer

if TYPE_CHECKING:
    from ..llm import ChatClient

_SYSTEM_PROMPT = """\
You are a data curator authoring the semantic layer for a governed analytics \
system. Given one table's catalog Facts (physical names, types, sample values, \
inferred roles), write concise, accurate business descriptions and flag any \
column that looks unreliable or like a decoy.

Rules:
- Ground every description in the Facts shown. Do not invent columns, values, or \
relationships you cannot see.
- Flag a column as "suspect" ONLY when the Facts suggest it is unreliable, \
ambiguous, or a decoy (e.g. a plausible-looking name whose samples contradict it). \
For a suspect column, write a short note starting with "DO NOT USE".
- Keep descriptions to one sentence.

Return ONLY a JSON object, no prose and no markdown fences, of the form:
{{
  "table_description": "<one sentence>",
  "grain": "<what one row represents>",
  "columns": {{
    "<physical_column_name>": {{
      "description": "<one sentence>",
      "reliability": "ok" | "suspect",
      "note": "<DO NOT USE ... , only when suspect>"
    }}
  }}
}}
"""


class LlmProposer:
    """Model-backed proposer that layers Inference prose over a base proposal.

    Runs ``base`` (the heuristic) to set roles/confidence/provenance, then asks the
    :class:`~governed_bi.llm.ChatClient` for descriptions + reliability caveats per
    table and applies them. Facts are never modified; a table whose model response
    cannot be parsed is returned exactly as the base proposed it. Implements the
    :class:`Proposer` protocol.
    """

    def __init__(
        self,
        chat: "ChatClient",
        *,
        base: Proposer | None = None,
        model_name: str | None = None,
    ) -> None:
        self.chat = chat
        self.base = base if base is not None else HeuristicProposer()
        self.model_name = model_name

    def propose(self, tables: list[TableAsset]) -> list[TableAsset]:
        based = self.base.propose(tables)
        return [self._enrich(t) for t in based]

    def _enrich(self, table: TableAsset) -> TableAsset:
        payload = self._ask(table)
        if payload is None:
            return table  # fail-safe: keep the base proposal untouched

        col_specs = payload.get("columns", {})
        if not isinstance(col_specs, dict):
            col_specs = {}  # fail-safe: a non-dict "columns" (list/null/str) is ignored
        new_columns = [self._enrich_column(c, col_specs.get(c.physical_name)) for c in table.columns]

        update: dict = {"columns": new_columns}
        desc = payload.get("table_description")
        if isinstance(desc, str) and desc.strip():
            update["description"] = desc.strip()
        grain = payload.get("grain")
        if isinstance(grain, str) and grain.strip():
            update["grain"] = grain.strip()
        self._stamp_model(update, table)
        return table.model_copy(update=update)

    def _enrich_column(self, column: Column, spec: object) -> Column:
        if not isinstance(spec, dict):
            return column  # no guidance for this column: leave the base copy
        update: dict = {}
        desc = spec.get("description")
        if isinstance(desc, str) and desc.strip():
            update["description"] = desc.strip()
        if spec.get("reliability") == ReliabilityStatus.suspect.value:
            note = spec.get("note")
            update["reliability"] = Reliability(
                status=ReliabilityStatus.suspect,
                note=note.strip() if isinstance(note, str) and note.strip() else None,
            )
        if not update:
            return column
        return column.model_copy(update=update)

    def _stamp_model(self, update: dict, table: TableAsset) -> None:
        """Record the authoring model in the provenance stamp, if one is set."""
        if self.model_name and table.audit is not None:
            audit = table.audit.model_copy(deep=True)
            audit.provenance.model = self.model_name
            update["audit"] = audit

    def _ask(self, table: TableAsset) -> dict | None:
        user = _render_table_facts(table)
        try:
            response = self.chat.complete(_SYSTEM_PROMPT, user)
        except Exception:
            return None
        return _parse_json(response)


def _render_table_facts(table: TableAsset) -> str:
    """Render a table's Facts as the prompt input the proposer reasons over."""
    lines = [f"Table physical name: {table.physical_name}"]
    if table.row_count is not None:
        lines.append(f"Row count: {table.row_count}")
    lines.append("Columns:")
    for c in table.columns:
        role = c.role.value if c.role is not None else "unknown"
        samples = ", ".join(str(v) for v in c.sample_values[:5])
        line = f"  - {c.physical_name} ({c.logical_type.value}, role={role})"
        if samples:
            line += f"; samples: {samples}"
        lines.append(line)
    return "\n".join(lines)


def _parse_json(response: str) -> dict | None:
    """Parse a JSON object from a model response, tolerating markdown fences.

    Returns None on any parse failure (fail-safe: the caller keeps the base
    proposal rather than fabricating).
    """
    text = response.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
