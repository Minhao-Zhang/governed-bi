"""Governance middleware — enforcement + audit choke point (ADR 0002 Inv #2/#3/#10).

Every data-touching tool (``run_query``, ``sample_rows``) passes through
``wrap_tool_call``: normalize → attempt cap (run_query) → ``check()`` over the
current licensed set → execute → ledger with result snapshot. L2 raises
``GovernanceHardStop`` (propagates out of ``agent.invoke``) carrying the full
prior ledger. Reuses ``check`` / ``column_allowlist`` unchanged.

Middleware owns execution after PASS so finalize never re-executes (single audit
entry, single DB round-trip).
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Annotated, Any

import sqlglot
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from ..corpus.schemas import TableAsset
from ..gateway import GuardrailLayer, QueryResult, check, column_allowlist
from .tools import render_result

if TYPE_CHECKING:
    from ..config import Settings
    from ..corpus import Corpus
    from ..gateway import Gateway, Identity

RUN_QUERY_CAP = 3
# Super-step budget for one agent turn. Sequential tool calls (G1) mean each
# inspect/query costs ~2 steps, so a normal search→inspect×N→query→repair chain
# needs far more than the ADR Q6 "~15" first guess. Live cs_semester runs hit 15
# on ordinary questions; 40 gives headroom while still bounding runaways.
AGENT_RECURSION_LIMIT = 40
_HARD = {GuardrailLayer.policy_blacklist}
_GOVERNED_TOOLS = frozenset({"run_query", "sample_rows"})


class GovState(AgentState):
    """Agent subgraph state: chat messages plus governed channels."""

    licensed: Annotated[list, operator.add]  # table-asset ids licensed this turn
    ledger: Annotated[list, operator.add]  # one record per governed action


class GovernanceHardStop(Exception):
    """L2 policy block — propagates out of ``agent.invoke`` (Inv #3).

    ``ledger`` is the full prior turn ledger plus the hard-stop entry (Inv #10).
    """

    def __init__(self, entry: dict, ledger: list | None = None):
        super().__init__(entry.get("reason") or "governance hard stop")
        self.entry = entry
        prior = list(ledger or [])
        # Ensure the hard-stop entry is present exactly once at the end.
        if not prior or prior[-1] is not entry:
            prior = prior + [entry]
        self.ledger = prior


def licensed_physical_names(
    corpus: "Corpus",
    licensed_ids: list | set,
    *,
    multi_schema: bool = False,
) -> set[str]:
    """Project licensed asset ids to physical table names for ``check`` L4."""
    names: set[str] = set()
    for tid in licensed_ids:
        asset = corpus.by_id(tid)
        if not isinstance(asset, TableAsset):
            continue
        if multi_schema:
            names.add(f"{asset.schema}.{asset.physical_name}")
        else:
            names.add(asset.physical_name)
    return names


def serialize_result(result: QueryResult) -> dict:
    """JSON-friendly snapshot for the governance ledger."""
    return {
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "row_count": result.row_count,
        "truncated": result.truncated,
    }


def result_from_ledger(entry: dict) -> QueryResult | None:
    """Rehydrate a QueryResult from a pass ledger entry, or None."""
    raw = entry.get("result")
    if not isinstance(raw, dict):
        return None
    return QueryResult(
        columns=list(raw.get("columns") or []),
        rows=[tuple(r) for r in (raw.get("rows") or [])],
        row_count=int(raw.get("row_count") or 0),
        truncated=bool(raw.get("truncated")),
    )


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_by_id(corpus: "Corpus", table_id: str) -> TableAsset | None:
    asset = corpus.by_id(table_id)
    if isinstance(asset, TableAsset):
        gov = getattr(asset, "governance", None)
        if gov is not None and getattr(gov, "excluded", False):
            return None
        return asset
    for a in corpus.assets:
        if not isinstance(a, TableAsset):
            continue
        gov = getattr(a, "governance", None)
        if gov is not None and getattr(gov, "excluded", False):
            continue
        if a.physical_name == table_id:
            return a
    return None


class GovernanceMiddleware(AgentMiddleware):
    """Intercept data-touching tools; exploration tools pass through."""

    state_schema = GovState

    def __init__(
        self,
        corpus: "Corpus",
        gateway: "Gateway",
        identity: "Identity",
        *,
        dialect: str,
        multi_schema: bool,
        default_schema: str | None,
        settings: "Settings",
    ):
        super().__init__()
        self._corpus = corpus
        self._gateway = gateway
        self._identity = identity
        self._allowlist = column_allowlist(corpus, multi_schema=multi_schema)
        self._dialect = dialect
        self._multi = multi_schema
        self._default = default_schema
        self._settings = settings

    def wrap_model_call(self, request, handler):
        # Gotcha G1: force sequential tool calls on every model turn. Prefer
        # model_settings (survives create_agent's internal bind_tools); also
        # bind the model when supported.
        settings = dict(request.model_settings or {})
        settings["parallel_tool_calls"] = False
        model = request.model
        if hasattr(model, "bind"):
            try:
                model = model.bind(parallel_tool_calls=False)
            except Exception:
                pass
        response = handler(request.override(model=model, model_settings=settings))
        return self._coerce_single_tool_call(response)

    @staticmethod
    def _coerce_single_tool_call(response):
        """If the model emitted parallel tool_calls, keep only the first (G1)."""
        from langchain.agents.middleware.types import ModelResponse

        if isinstance(response, ModelResponse):
            messages = list(response.result or [])
            new_msgs = []
            changed = False
            for m in messages:
                if (
                    isinstance(m, AIMessage)
                    and getattr(m, "tool_calls", None)
                    and len(m.tool_calls) > 1
                ):
                    new_msgs.append(
                        AIMessage(
                            content=m.content,
                            tool_calls=m.tool_calls[:1],
                            id=getattr(m, "id", None),
                            additional_kwargs=getattr(m, "additional_kwargs", {}) or {},
                        )
                    )
                    changed = True
                else:
                    new_msgs.append(m)
            if changed:
                return ModelResponse(
                    result=new_msgs,
                    structured_response=response.structured_response,
                )
            return response
        if (
            isinstance(response, AIMessage)
            and getattr(response, "tool_calls", None)
            and len(response.tool_calls) > 1
        ):
            return AIMessage(
                content=response.content,
                tool_calls=response.tool_calls[:1],
                id=getattr(response, "id", None),
                additional_kwargs=getattr(response, "additional_kwargs", {}) or {},
            )
        return response

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in _GOVERNED_TOOLS:
            return handler(request)

        tcid = request.tool_call["id"]
        args = request.tool_call.get("args") or {}
        licensed_ids = list(request.state.get("licensed") or [])
        prior_ledger = list(request.state.get("ledger") or [])

        if name == "sample_rows":
            sql, err = self._sample_sql(args, licensed_ids)
            if err is not None:
                return Command(
                    update={
                        "messages": [ToolMessage(content=err, tool_call_id=tcid)],
                        "ledger": [
                            {
                                "action": "sample_rows",
                                "verdict": "deny",
                                "reason": err,
                                "table_id": args.get("table_id"),
                            }
                        ],
                    }
                )
            action = "sample_rows"
        else:
            raw = args.get("sql") or ""
            try:
                sql = sqlglot.transpile(
                    raw, read=self._dialect, write=self._dialect, identify=True
                )[0]
            except Exception:
                sql = raw
            action = "run_query"
            # Attempt cap only for run_query (ADR Q6)
            prior = sum(1 for e in prior_ledger if e.get("action") == "run_query")
            if prior >= RUN_QUERY_CAP:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(content="attempt cap reached", tool_call_id=tcid)
                        ],
                        "ledger": [
                            {"action": "run_query", "verdict": "cap", "sql": sql}
                        ],
                    }
                )

        allowed_tables = licensed_physical_names(
            self._corpus, licensed_ids, multi_schema=self._multi
        )
        verdict = check(
            sql,
            allowed_columns=set(self._allowlist.allowed),
            suspect_columns=self._allowlist.suspect,
            allowed_tables=frozenset(allowed_tables),
            hard_block_suspect=self._settings.hard_block_suspect_columns,
            dialect=self._dialect,
            multi_schema=self._multi,
            default_schema=self._default,
        )
        if not verdict.passed:
            entry = {
                "action": action,
                "verdict": "block",
                "layer": verdict.failed_layer.value if verdict.failed_layer else None,
                "reason": verdict.reason,
                "sql": sql,
                "allowed": sorted(allowed_tables),
                "licensed_ids": sorted(licensed_ids),
            }
            if verdict.failed_layer in _HARD:
                raise GovernanceHardStop(entry, ledger=prior_ledger)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"BLOCKED ({entry['layer']}): {verdict.reason}",
                            tool_call_id=tcid,
                        )
                    ],
                    "ledger": [entry],
                }
            )

        # PASS — middleware executes (single audit entry; finalize reuses result).
        try:
            result = self._gateway.execute(sql, self._identity)
        except Exception as err:
            entry = {
                "action": action,
                "verdict": "error",
                "sql": sql,
                "reason": str(err),
                "allowed": sorted(allowed_tables),
                "licensed_ids": sorted(licensed_ids),
            }
            return Command(
                update={
                    "messages": [
                        ToolMessage(content=f"execution failed: {err}", tool_call_id=tcid)
                    ],
                    "ledger": [entry],
                }
            )

        entry = {
            "action": action,
            "verdict": "pass",
            "sql": sql,
            "allowed": sorted(allowed_tables),
            "licensed_ids": sorted(licensed_ids),
            "result": serialize_result(result),
        }
        return Command(
            update={
                "messages": [
                    ToolMessage(content=render_result(result), tool_call_id=tcid)
                ],
                "ledger": [entry],
            }
        )

    def _sample_sql(
        self, args: dict, licensed_ids: list
    ) -> tuple[str | None, str | None]:
        """Build a column-allowlisted sample SELECT, or return (None, error)."""
        table_id = args.get("table_id") or ""
        n = max(1, min(int(args.get("n") or 5), 20))
        asset = _table_by_id(self._corpus, table_id)
        if asset is None:
            return None, f"{table_id}: not available"
        if asset.id not in set(licensed_ids):
            return None, f"{asset.id}: not licensed this turn — call inspect_schema first"

        prefix = (
            f"{asset.schema}.{asset.physical_name}"
            if self._multi
            else asset.physical_name
        )
        # Only columns in the L3 allowlist — never excluded/suspect (Inv #2).
        cols: list[str] = []
        for col in asset.columns:
            gov = getattr(col, "governance", None)
            if gov is not None and getattr(gov, "excluded", False):
                continue
            ref = f"{prefix}.{col.physical_name}"
            if ref not in self._allowlist.allowed:
                continue
            cols.append(_quote_ident(col.physical_name))
        if not cols:
            return None, f"{asset.id}: no allowlisted columns to sample"

        qual = (
            f"{_quote_ident(asset.schema)}.{_quote_ident(asset.physical_name)}"
            if self._multi
            else _quote_ident(asset.physical_name)
        )
        sql = f"SELECT {', '.join(cols)} FROM {qual} LIMIT {n}"
        try:
            sql = sqlglot.transpile(
                sql, read=self._dialect, write=self._dialect, identify=True
            )[0]
        except Exception:
            pass
        return sql, None
