"""Gateway service: the only path to data.

Read-only, RLS-as-user, credential-isolated, forced LIMIT/timeout, audit/replay
(Architecture §3-4). One boundary, two permission profiles. Fail-closed lives in
the guardrails (server ``wrap_tool_call``).

Status: skeleton. See ``docs/server.md`` steps 8-9 and ``docs/architecture.md``.
"""
