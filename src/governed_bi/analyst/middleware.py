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
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

import sqlglot
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from ..corpus.schemas import TableAsset
from ..gateway import GuardrailLayer, QueryResult, check, column_allowlist
from ..graph import build_graph, detect_missing_join_path
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


def _ledger_stamp(t0: float) -> dict[str, Any]:
    """``duration_ms`` + UTC ``ts`` for every ledger entry (ADR 0004 L1)."""
    return {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


class GovState(AgentState):
    """Agent subgraph state: chat messages plus governed channels."""

    licensed: Annotated[list, operator.add]  # table-asset ids licensed this turn
    ledger: Annotated[list, operator.add]  # one record per governed action
    token_usage: Annotated[list, operator.add]  # per-model-call usage snapshots (L4)


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


def licensed_physical_names(corpus: "Corpus", licensed_ids: list | set) -> set[str]:
    """Project licensed asset ids to schema-qualified table names for ``check`` L4."""
    names: set[str] = set()
    for tid in licensed_ids:
        asset = corpus.by_id(tid)
        if not isinstance(asset, TableAsset):
            continue
        names.add(f"{asset.schema}.{asset.physical_name}")
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
        default_schema: str | None,
        settings: "Settings",
    ):
        super().__init__()
        self._corpus = corpus
        self._gateway = gateway
        self._identity = identity
        self._allowlist = column_allowlist(corpus)
        self._dialect = dialect
        self._default = default_schema
        self._settings = settings
        # D15 cross-schema enforcement (a no-op for a single-schema corpus, i.e. the
        # BIRD/demo path): the join graph + a physical→id map let run_query re-check
        # that any cross-schema join it reaches is backed by a CURATED JoinAsset, not
        # merely a structural equality (L5). Retrieval's missing-edge refusal does
        # not cover a table the agent self-licensed via inspect_schema, so re-check
        # here at execution time.
        self._graph = build_graph(corpus)
        self._phys_to_id = {
            f"{a.schema}.{a.physical_name}": a.id
            for a in corpus.assets
            if isinstance(a, TableAsset)
            and not getattr(getattr(a, "governance", None), "excluded", False)
        }
        # L4 failed-call stubs when wrap_model_call raises before a response.
        self.failed_model_calls: list[dict[str, Any]] = []

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
        try:
            response = handler(request.override(model=model, model_settings=settings))
        except Exception as exc:
            # Metadata-only stub (no exception message — may echo user/SQL content).
            self.failed_model_calls.append(
                {
                    "source": "agent_core",
                    "failed": True,
                    "error_type": type(exc).__name__,
                    "usage_metadata": {},
                }
            )
            raise
        return self._coerce_single_tool_call(response)

    def after_model(self, state, runtime):  # noqa: ARG002 — LangChain middleware hook
        """Capture usage from the last AIMessage onto ``token_usage`` (SPIKE-1 / L4)."""
        messages = state.get("messages") or []
        for m in reversed(messages):
            if not isinstance(m, AIMessage):
                continue
            usage = getattr(m, "usage_metadata", None)
            if not usage:
                return None
            entry: dict[str, Any] = {
                "source": "agent_core",
                "usage_metadata": dict(usage) if hasattr(usage, "keys") else usage,
            }
            resp_meta = getattr(m, "response_metadata", None)
            if resp_meta:
                entry["response_metadata"] = dict(resp_meta)
            return {"token_usage": [entry]}
        return None

    @staticmethod
    def _coerce_single_tool_call(response):
        """If the model emitted parallel tool_calls, keep only the first (G1).

        Preserves ``usage_metadata`` and ``response_metadata`` through rebuild
        (SPIKE-1 / L4) so token capture survives coercion.
        """
        from langchain.agents.middleware.types import ModelResponse

        def _rebuild(m: AIMessage) -> AIMessage:
            kwargs: dict[str, Any] = {
                "content": m.content,
                "tool_calls": m.tool_calls[:1],
                "id": getattr(m, "id", None),
                "additional_kwargs": getattr(m, "additional_kwargs", {}) or {},
            }
            usage = getattr(m, "usage_metadata", None)
            if usage is not None:
                kwargs["usage_metadata"] = usage
            resp_meta = getattr(m, "response_metadata", None)
            if resp_meta is not None:
                kwargs["response_metadata"] = resp_meta
            return AIMessage(**kwargs)

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
                    new_msgs.append(_rebuild(m))
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
            return _rebuild(response)
        return response

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in _GOVERNED_TOOLS:
            return handler(request)

        t0 = time.perf_counter()
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
                                **_ledger_stamp(t0),
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
                            {
                                "action": "run_query",
                                "verdict": "cap",
                                "sql": sql,
                                **_ledger_stamp(t0),
                            }
                        ],
                    }
                )

        allowed_tables = licensed_physical_names(self._corpus, licensed_ids)
        verdict = check(
            sql,
            allowed_columns=set(self._allowlist.allowed),
            suspect_columns=self._allowlist.suspect,
            allowed_tables=frozenset(allowed_tables),
            hard_block_suspect=self._settings.hard_block_suspect_columns,
            dialect=self._dialect,
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
                **_ledger_stamp(t0),
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

        # D15: a passing query that reaches ACROSS schemas must be connected by a
        # curated JoinAsset, never a self-authorized structural join. No-op unless the
        # SQL spans >=2 schemas with no curated path (single-schema always None).
        if action == "run_query":
            missing = self._cross_schema_missing_join(sql)
            if missing is not None:
                entry = {
                    "action": "run_query",
                    "verdict": "block",
                    "layer": GuardrailLayer.term_semantics.value,
                    "reason": (
                        "cross-schema join is not backed by a curated JoinAsset "
                        f"(D15 missing edge): schemas {sorted(missing.schemas)}"
                    ),
                    "sql": sql,
                    "allowed": sorted(allowed_tables),
                    "licensed_ids": sorted(licensed_ids),
                    **_ledger_stamp(t0),
                }
                # Hard stop, mirroring the retrieval-time missing-edge refusal: an
                # undeclared cross-schema join is never executed nor graded-delivered
                # (D15 refuses + escalates), so it cannot be self-authorized.
                raise GovernanceHardStop(entry, ledger=prior_ledger)

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
                **_ledger_stamp(t0),
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
            **_ledger_stamp(t0),
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

        prefix = f"{asset.schema}.{asset.physical_name}"
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

        qual = f"{_quote_ident(asset.schema)}.{_quote_ident(asset.physical_name)}"
        sql = f"SELECT {', '.join(cols)} FROM {qual} LIMIT {n}"
        try:
            sql = sqlglot.transpile(
                sql, read=self._dialect, write=self._dialect, identify=True
            )[0]
        except Exception:
            pass
        return sql, None

    def _cross_schema_missing_join(self, sql: str):
        """A ``MissingJoinPath`` when ``sql`` joins across schemas with no curated
        join path, else ``None``. Best-effort parse; a single-schema query (the BIRD
        path) is always ``None`` — ``detect_missing_join_path`` gates on >=2 schemas.
        """
        from .sqlgen import _tables_used  # lazy: keep the import graph acyclic

        # Best-effort and correctness-neutral: a parse/plan hiccup must never turn a
        # governed answer into an error, so any failure here yields "no missing edge"
        # (the query has already passed check()). Fail-open is safe because a genuine
        # cross-schema-without-curated-join case is what this catches, not a leak.
        try:
            tables_used = _tables_used(
                sql, self._phys_to_id, self._dialect, default_schema=self._default
            )
            return detect_missing_join_path(self._corpus, self._graph, set(tables_used))
        except Exception:
            return None
