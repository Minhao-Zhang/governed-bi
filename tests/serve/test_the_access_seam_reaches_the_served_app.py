"""ADR 0012 §8's four wires, asserted where they now run: the served app.

**The gap this closes, in the ADR's own words.** "The seam is enforced inside ``govern/`` and
is not an end-to-end security property. Nothing in ``serve/`` or ``api/`` constructs a
``GovernancePolicy`` with a non-open grant, so on this tree the authorization rules are
unreachable in the served app." Every assertion about a *governed turn* below is over
``api/graph_app.build_serve_graph`` — the topology ``langgraph.json`` runs, with ``accept`` in
front and ``record`` behind — and never over a direct ``check()`` call, because a direct call
was already covered and was exactly what could not prove reachability. The four tests at the
end assert the composition root and the bounds function directly; they are about wiring, not
about a turn.

**The pairing is the method.** Every behavioural test here runs the *same turn twice*, once
under the shipped open grant and once under a restrictive one, and asserts on the difference.
Run only the restrictive half and a refusal proves nothing: ``r_table_not_licensed`` would pass
a test asking "was it refused", which is the misattribution ``tools/govern_bench.py`` measures
separately for the same reason. Run only the open half and nothing says the seam can fire.

The connector is a double. Nothing here asserts what a database returned — the subject is a
governance verdict and the shape of what reached the prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.corpus.schema import ColumnAsset, JoinAsset, SchemaAsset, TableAsset
from governed_bi.govern.access import LOCAL_PRINCIPAL, OpenAccessPolicy, StaticRoleAccessPolicy
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.ports import OPEN_GRANT, Grant, Reach
from governed_bi.serve.graph import as_sync
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.session import from_assets

#: Routes to ``sales`` on the lexical channel alone and licenses **both** tables. Both, because
#: a narrowing that dropped everything would pass a test that only checked the denied one.
QUESTION = "how many audit log entries and how many customers are there"

#: The statement the scripted model proposes. Schema-qualified on purpose: bare ``audit_log``
#: normalises to ``audit_log``, which is not the licensed key, so the turn would refuse as
#: ``r_table_not_licensed`` and the test would pass against a deleted authorization rule.
STATEMENT = "SELECT 1 AS n FROM sales.audit_log"

#: Authorizes a table the turn does not license and **not** the one it does. Written this way
#: round so the grant cannot be mistaken for a retrieval filter: it names ``sales.customers``,
#: which retrieval may or may not have found, and stays silent about the table under test.
RESTRICTIVE = Grant(reach=Reach.listed, tables=frozenset({"sales.customers"}))

#: Authorizes the table under test and denies **one of its columns**. The other axis of ADR
#: 0012 §4: the table is legitimately in the prompt, and one column of it may not be named.
COLUMN_DENIED = Grant(
    reach=Reach.listed,
    tables=frozenset({"sales.customers", "sales.audit_log"}),
    denied_columns=frozenset({"sales.customers.email"}),
)


@pytest.fixture(autouse=True)
def _isolated():
    """``build_serve_graph`` registers its session's constants process-wide (``runtime.trust``).

    That is the design — one session per server process — and in a suite it is shared mutable
    state whose failure mode is a test passing because an earlier one registered a corpus.
    """
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


class _EchoConnector:
    """Real enough that a refusal is a refusal.

    With ``connector=None`` a governed block is a *wiring failure* and proves nothing —
    ``serve/fetch.py`` raises ``GovernanceUsageError`` rather than manufacturing a verdict, so
    a test on a null connector would assert about an exception, not an authorization.
    """

    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["n"], [(1,)], False)


class _TurnLog:
    """Everything ``record_node`` asks of a turn log, in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_turn(self, record: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        self.rows.append({"record": dict(record), **kwargs})
        return record.get("turn_id"), None


def _model() -> ScriptedChatModel:
    call = {"name": "run_query", "args": {"sql": STATEMENT}, "id": "rq-1", "type": "tool_call"}
    return ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="answered from the tool"),
        ]
    )


def _assets() -> list[Any]:
    """The corpus every turn here reads.

    **Two joins over one table pair, differing only in how their endpoints are spelled.**
    ``retrieve/structure.py::bind_endpoint`` binds a bare ``customers`` and a qualified
    ``sales.customers`` alike and its docstring says it declines to settle which, so both occur
    in one real corpus. ``withheld_by_grant`` matched only the qualified spelling, so the bare
    join rendered ``join customers >< audit_log on customers.id = audit_log.customer_id`` while
    its qualified twin was withheld — the same disclosure, present or absent depending on how
    the curator happened to write it down. The seam test's corpus had no join assets at all,
    which is why nothing caught it.
    """
    return [
        SchemaAsset(id="sales", name="sales", summary="sales audit log and customers"),
        TableAsset(
            id="sales.audit_log", schema="sales", physical_name="audit_log",
            summary="Audit log entries, one per change.",
            body="Every write to the sales schema lands here, with the actor's name.",
            columns=("sales.audit_log.note", "sales.audit_log.customer_id"),
        ),
        ColumnAsset(
            id="sales.audit_log.note", schema="sales", parent_table="sales.audit_log",
            physical_name="note", summary="Free text describing the change.",
        ),
        ColumnAsset(
            id="sales.audit_log.customer_id", schema="sales", parent_table="sales.audit_log",
            physical_name="customer_id", summary="Which customer the change was about.",
        ),
        TableAsset(
            id="sales.customers", schema="sales", physical_name="customers",
            summary="Customers of the shop.", body="One row per registered buyer.",
            columns=("sales.customers.email", "sales.customers.id"),
        ),
        ColumnAsset(
            id="sales.customers.email", schema="sales", parent_table="sales.customers",
            physical_name="email", summary="Contact address.",
        ),
        ColumnAsset(
            id="sales.customers.id", schema="sales", parent_table="sales.customers",
            physical_name="id", summary="Primary key.",
        ),
        JoinAsset(
            id="sales.join_bare_a1b2c3",
            left_table="customers",
            right_table="audit_log",
            on="customers.id = audit_log.customer_id",
            summary="Customers to their audit log entries, endpoints spelled bare.",
        ),
        JoinAsset(
            id="sales.join_qualified_d4e5f6",
            left_table="sales.customers",
            right_table="sales.audit_log",
            on="sales.customers.id = sales.audit_log.customer_id",
            summary="The same pair, endpoints spelled schema-qualified.",
        ),
    ]


def _session(grant: Grant, *, model: Any = None) -> Any:
    session = from_assets(
        _assets(),
        connector=_EchoConnector(),
        policy=GovernancePolicy(guard_rules_enabled={}, access_grant=grant),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
        agent_model=model,
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return session


def _serve(grant: Grant, *, thread: str, model: Any = None) -> tuple[dict[str, Any], _TurnLog]:
    """One turn through the served topology. Returns ``(final state, turn log)``."""
    from governed_bi.api.graph_app import build_serve_graph

    log = _TurnLog()
    graph = as_sync(build_serve_graph(_session(grant, model=model), turn_log=log))
    out = graph.invoke(
        {"messages": [HumanMessage(content=QUESTION)]},
        {"configurable": {"thread_id": thread}},
    )
    return out, log


def _attempts(record: dict[str, Any]) -> list[dict[str, Any]]:
    return list((record.get("execution") or {}).get("attempts") or ())


# ── §8.1 + §8.2: the rule fires, through the served graph ─────────────────────


def test_a_licensed_but_unauthorized_table_refuses_as_r_table_not_authorized() -> None:
    """The case ADR 0012 exists for, run end to end instead of against ``check()``.

    Two turns, one statement, one difference. The open half must **execute** it — that is what
    makes the licensed set the same on both sides and rules out the refusal being a retrieval
    miss wearing a permission's name. The restrictive half must refuse it, and must refuse it
    under the authorization rule: a refusal as ``r_table_not_licensed`` would blame the router
    for a permission decision, which is the misattribution the Layer 6 split was made to end
    and which a gate asking only "was it refused" reports as the new rule working.

    ``executed_sql is None`` is acceptance criterion 6 — no executable string is produced for
    an authorization refusal — asserted on the ledger row rather than inferred from the
    outcome.
    """
    allowed, _ = _serve(OPEN_GRANT, thread="t-open", model=_model())
    denied, log = _serve(RESTRICTIVE, thread="t-denied", model=_model())

    open_record = allowed["answer"]["record"]
    denied_record = denied["answer"]["record"]

    assert "sales.audit_log" in (open_record.get("licensed") or ()), (
        "the open half never licensed the table under test, so the restrictive half's refusal "
        f"would be a retrieval miss: licensed={open_record.get('licensed')!r}"
    )
    assert open_record["licensed"] == denied_record["licensed"], (
        "the grant moved the licensed set. ADR 0012's rejected alternatives open with exactly "
        "this: filter `licensed` by the grant and every permission refusal is reported as a "
        "retrieval miss"
    )

    assert allowed["answer"]["outcome"] == "answered", allowed["answer"]
    passed = [a for a in _attempts(open_record) if a.get("passed")]
    assert passed and passed[0].get("executed_sql"), (
        f"the open grant did not execute the statement: {_attempts(open_record)!r}"
    )

    assert denied["answer"]["outcome"] == "refused", denied["answer"]
    rows = _attempts(denied_record)
    assert rows, "the restrictive turn wrote no ledger row, so nothing was governed"
    assert [r.get("reason_code") for r in rows] == ["r_table_not_authorized"], (
        f"reason codes {[r.get('reason_code') for r in rows]!r}. `r_table_not_licensed` here "
        "is a permission decision blamed on the router"
    )
    assert rows[0].get("verdict_layer") == "TABLES", rows[0]
    assert rows[0].get("executed_sql") is None, (
        "an authorization refusal produced an executable string (acceptance criterion 6)"
    )
    assert log.rows and log.rows[-1]["outcome"] == "refused", log.rows


def test_the_denied_tables_prose_never_reaches_the_prompt() -> None:
    """§8.4, and the reason §6 left ``read_body`` ungated.

    Refusing is not disclosing. Before this wire the renderer worked off ``licensed`` and
    ``retrieved``, so an unauthorized table's structural line and body reached the prompt and
    ``read_body`` returned its prose — the model could describe a table it could not query.

    Both directions, because a renderer that dropped *everything* would also pass the negative
    half: the open turn must show the table and its body, the restrictive turn must show
    neither, and the customers table must survive both so the narrowing is a filter rather than
    an outage.
    """
    open_model, denied_model = _model(), _model()
    allowed, _ = _serve(OPEN_GRANT, thread="t-open-ctx", model=open_model)
    denied, _ = _serve(RESTRICTIVE, thread="t-denied-ctx", model=denied_model)

    open_block = allowed["answer"]["record"]["context_hash"]
    denied_block = denied["answer"]["record"]["context_hash"]
    assert open_block and denied_block and open_block != denied_block, (
        "the two turns rendered the same block, so the grant narrowed nothing"
    )

    open_text = _seen_by_the_model(open_model)
    denied_text = _seen_by_the_model(denied_model)
    assert "audit_log" in open_text and "actor's name" in open_text, open_text
    assert "audit_log" not in denied_text, (
        f"an unauthorized table is still named in the prompt:\n{denied_text}"
    )
    assert "actor's name" not in denied_text, (
        f"an unauthorized table's body is still in the prompt:\n{denied_text}"
    )
    assert "customers" in denied_text, (
        "the authorized table vanished too, so this is an outage and not a narrowing:\n"
        f"{denied_text}"
    )


def test_neither_spelling_of_a_join_survives_the_grant_that_withholds_its_endpoint() -> None:
    """A join's ON clause names a table the principal may not read. Both spellings of it.

    ``withheld_by_grant`` collected ``{asset_id, table_qualifier(asset)}`` for every withheld
    table and matched endpoints against that set, which resolves the *qualified* spelling and
    nothing else. The bare-spelled join therefore rendered its ON clause in full — the withheld
    table's existence, its physical name and the column it joins on — while the qualified join
    over the identical pair was correctly dropped. A disclosure that depends on how the curator
    wrote the endpoint down is not a stated trade.

    The open half is the control: both joins must be in the prompt there, or the restrictive
    half asserts the absence of something that was never rendered.
    """
    open_model, denied_model = _model(), _model()
    _serve(OPEN_GRANT, thread="t-open-join", model=open_model)
    _serve(RESTRICTIVE, thread="t-denied-join", model=denied_model)

    open_text = _seen_by_the_model(open_model)
    denied_text = _seen_by_the_model(denied_model)

    assert "join customers >< audit_log" in open_text, (
        f"the bare-spelled join never reached the prompt, so its absence proves nothing:\n"
        f"{open_text}"
    )
    assert "join sales.customers >< sales.audit_log" in open_text, open_text

    assert "join customers >< audit_log" not in denied_text, (
        "the bare-spelled join still names the unauthorized table in its ON clause:\n"
        f"{denied_text}"
    )
    assert "join sales.customers >< sales.audit_log" not in denied_text, denied_text
    assert "audit_log" not in denied_text, (
        f"an unauthorized table is named somewhere in the prompt:\n{denied_text}"
    )


def test_inspect_schema_will_not_name_a_denied_column_of_a_table_it_may_inspect() -> None:
    """The severe half of ADR 0012 §6: a table-level bound in front of a column-level payload.

    ``may_inspect_schema`` asked ``licensed`` and ``authorizes_table`` and nothing else, and
    ``serve/fetch.py`` then enumerated every column's id, physical name, type and nullability.
    Under a grant authorizing ``sales.customers`` and denying ``sales.customers.email``, the
    rendered block correctly omitted the column and this tool handed the model its metadata —
    so the two answers to "what may this principal see" already disagreed in the shipped tree,
    which is the failure §8.4 says the one-function design prevents.

    Driven through ``build_serve_graph`` with a model that calls the tool, because the bound and
    the tool live in different packages and a direct ``fetch.inspect_schema`` call would leave
    open whether the served path builds the bounds that carry the withheld set.
    """
    wide_model, denied_model = _model_inspecting(), _model_inspecting()
    _serve(OPEN_GRANT, thread="t-open-inspect", model=wide_model)
    _serve(COLUMN_DENIED, thread="t-denied-inspect", model=denied_model)

    wide = _tool_result(wide_model, "inspect_schema")
    denied = _tool_result(denied_model, "inspect_schema")

    assert "sales.customers.email" in wide and '"email"' in wide, (
        f"the open grant did not return the column, so the negative half is vacuous:\n{wide}"
    )
    assert "sales.customers.id" in denied, (
        f"the authorized column vanished too, so this is an outage not a narrowing:\n{denied}"
    )
    assert "sales.customers.email" not in denied, (
        f"inspect_schema returned the denied column's asset id:\n{denied}"
    )
    assert "email" not in denied, (
        f"inspect_schema returned the denied column's physical name:\n{denied}"
    )


def _model_inspecting() -> ScriptedChatModel:
    """Calls ``inspect_schema`` on the authorized table, then answers without SQL."""
    call = {
        "name": "inspect_schema",
        "args": {"table_id": "sales.customers"},
        "id": "is-1",
        "type": "tool_call",
    }
    return ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="answered from the roster"),
        ]
    )


def _tool_result(model: ScriptedChatModel, tool: str) -> str:
    """What ``tool`` handed back, read off the messages the model was next prompted with.

    The tool's payload is only ever seen by the model, so this is where it is observable — the
    same argument :func:`_seen_by_the_model` makes about the context block.
    """
    for prompt in model.prompts_seen:
        for message in prompt:
            if getattr(message, "type", "") != "tool":
                continue
            if str(getattr(message, "name", "")) != tool:
                continue
            return str(getattr(message, "content", ""))
    raise AssertionError(
        f"no {tool} result reached the model, so this asserts nothing about the tool. "
        f"prompts seen: {len(model.prompts_seen)}"
    )


def _seen_by_the_model(model: ScriptedChatModel) -> str:
    """The **first** thing the agent sent the provider, as one string.

    **Not** read off the ``delivery`` channel, and the difference is the point: ``ServeOutput``
    keeps ``delivery`` out of what ``invoke`` returns (so :func:`_serve` cannot see it — the
    ``values`` frames still carry it, audit-2026-08-10 §B1), and asserting on a channel would
    leave open whether the block the renderer built is the block the model got.
    ``assemble`` deliberately keeps the context out of ``messages`` and ``agent_core`` passes
    it as an ephemeral message, so this is the only place the two claims meet.

    The **first prompt carrying the block**, for two reasons. The session shares one model
    across the agent and the five facet rewriters, so the earliest prompts are rewrites and
    carry no context at all. And later agent prompts carry tool results — ``run_query``'s
    refusal *names the denied table back to the model*, which is ADR 0012's open question 4
    ("should ``r_table_not_authorized`` be surfaced to the model at all?"), recorded there and
    deliberately not answered here. Folding either into this assertion would make it a test of
    something else.
    """
    for prompt in model.prompts_seen:
        text = "\n".join(str(getattr(message, "content", message)) for message in prompt)
        if "## Context" in text:
            return text
    raise AssertionError(
        "no prompt carried a context block, so this asserts nothing about the renderer"
    )


def test_read_body_cannot_reach_a_withheld_asset() -> None:
    """The other half of §8.4: the tool bound narrows with the renderer, in one place.

    ``ToolBounds.may_read_body`` still asks no grant. What changed is that
    ``tool_bounds_from_state`` subtracts the same withheld set the renderer skips, so the
    prompt and the tool cannot disagree — which is the failure mode ADR 0012 §6 names when it
    calls a half-closed bound one that "looks enforced".
    """
    from governed_bi.serve.delivery import tool_bounds_from_state

    state = {
        "licensed": ["sales.audit_log", "sales.customers"],
        "retrieved": {
            "selected": {"sales.audit_log": {"score": 1.0}, "sales.customers": {"score": 0.9}},
            "pulled_in": {},
            "attributions": {},
        },
    }
    assets = {a.id: a for a in _assets()}

    wide = tool_bounds_from_state(state, {"assets_by_id": assets, "policy": GovernancePolicy()})
    assert wide.may_read_body("sales.audit_log")
    assert wide.may_inspect_schema("sales.audit_log")

    narrow = tool_bounds_from_state(
        state,
        {
            "assets_by_id": assets,
            "policy": GovernancePolicy(access_grant=RESTRICTIVE),
        },
    )
    assert not narrow.may_read_body("sales.audit_log"), (
        "read_body still returns the prose of a table this principal may not read"
    )
    assert not narrow.may_inspect_schema("sales.audit_log"), (
        "inspect_schema builds no statement, so the layer stack never sees it — §6 says this "
        "bound is the only thing standing in front of it"
    )
    assert not narrow.may_sample("sales.audit_log.note")
    assert narrow.may_read_body("sales.customers"), (
        "the authorized table became unreadable, so the narrowing is an outage"
    )
    assert narrow.licensed == wide.licensed, "the grant narrowed `licensed`"


# ── the default path is unchanged ─────────────────────────────────────────────


#: Fields that legitimately move between two runs of the same turn. ``latency_sec`` is wall
#: clock; the four ids are minted per session and per invoke.
_VOLATILE = frozenset({"latency_sec", "run_id", "turn_id", "thread_id", "attempt_id"})


def test_three_spellings_of_the_open_grant_produce_the_same_record() -> None:
    """The identity proof for the serve path, in the shape ADR 0012 used for ``govern/``.

    The default policy, an explicit :data:`~governed_bi.ports.OPEN_GRANT`, and
    ``OpenAccessPolicy().grant_for(LOCAL_PRINCIPAL)`` — the value the composition root now
    actually resolves — must produce the same turn record field for field, including
    ``context_hash``, ``delivery_hash``, ``execution`` and ``knobs_resolved``. If they do not,
    wiring the seam moved a measured number and the v4 arm has stopped being a control.

    The positive control is the test above: a restrictive grant *does* move this record. Both
    halves are needed — an equality test alone passes for a wire that was never connected.
    """
    grants = {
        "default": GovernancePolicy().access_grant,
        "explicit": OPEN_GRANT,
        "adapter": OpenAccessPolicy().grant_for(LOCAL_PRINCIPAL),
    }
    records = {}
    for name, grant in grants.items():
        out, _ = _serve(grant, thread=f"t-id-{name}", model=_model())
        records[name] = {
            k: v for k, v in out["answer"]["record"].items() if k not in _VOLATILE
        }

    assert records["default"] == records["explicit"] == records["adapter"], (
        "two spellings of 'authorize everything' produced different turn records"
    )
    assert records["default"]["outcome"] == "answered", records["default"]["outcome"]


def test_the_grant_digest_reaches_the_record_and_comes_from_the_policy() -> None:
    """§8.3 / §7, including the defect the ADR names rather than merely the field.

    ``access_grant`` is ``Role.comparability``, so it enters ``config_hash_keys()`` and two
    runs under different authorization can no longer hash identically. The part worth pinning
    is *where the value comes from*: the register's default is ``None`` and the resolver reads
    ``GovernancePolicy.access_grant``. A knob resolved from ``knob_default`` would publish the
    open grant's digest for a fork shipping a restrictive one — the ``agent_recursion_limit``
    defect, in the security register.
    """
    from governed_bi.register.knobs import comparability_keys, knob_default

    assert "access_grant" in comparability_keys()
    assert knob_default("access_grant") is None, (
        "the register now carries a digest of its own, so a run that never threaded a policy "
        "would publish one — and a null on a row would stop meaning 'no policy was threaded'"
    )
    # There used to be a `!= OPEN_GRANT.digest()` here, one line under `is None`. It could not
    # fail: `None` is unequal to every string, so it asserted nothing beyond the line above and
    # read as a second, independent check. The claim it was reaching for is that the *resolver*
    # reads the policy rather than the register, which needs two runs — below.

    wide, _ = _serve(OPEN_GRANT, thread="t-knob-open", model=_model())
    narrow, _ = _serve(RESTRICTIVE, thread="t-knob-denied", model=_model())

    open_digest = wide["answer"]["record"]["knobs_resolved"]["access_grant"]
    denied_digest = narrow["answer"]["record"]["knobs_resolved"]["access_grant"]
    assert open_digest == OPEN_GRANT.digest()
    assert denied_digest == RESTRICTIVE.digest()
    assert open_digest != denied_digest, (
        "two runs under different authorization published the same digest, so every "
        "comparison between them is unsound (ADR 0006 §13)"
    )


# ── §8.1: the composition root ────────────────────────────────────────────────


def test_the_composition_root_serves_open_unless_a_policy_file_is_configured(monkeypatch) -> None:
    """``api/graph_app.resolve_access_grant`` is the wire, and it has three behaviours.

    Unset ⇒ the open grant, which is what this repository ships and what keeps the v4 arm a
    control. Set ⇒ the reference adapter's grant for the one principal. Set to a path that is
    not there ⇒ **raise**, because an operator who configured a restriction and got an open
    server has a permission boundary they believe in and do not have.
    """
    from governed_bi.api.graph_app import ACCESS_POLICY_VAR, resolve_access_grant

    monkeypatch.delenv(ACCESS_POLICY_VAR, raising=False)
    assert resolve_access_grant(Path.cwd()).is_open

    monkeypatch.setenv(ACCESS_POLICY_VAR, "no/such/policy.toml")
    with pytest.raises(RuntimeError, match="not a file"):
        resolve_access_grant(Path.cwd())


def test_a_configured_policy_file_reaches_the_grant(monkeypatch, tmp_path: Path) -> None:
    """The reference adapter, through the composition root, for the real principal.

    ``LOCAL_PRINCIPAL`` holds the role ``local``; the file grants that role one table. A fork
    that renames the role gets ``Grant()``, which authorizes nothing — the fail-closed reading
    of "no known role", asserted here so the composition root cannot quietly widen it.
    """
    from governed_bi.api.graph_app import ACCESS_POLICY_VAR, resolve_access_grant

    policy_file = tmp_path / "access.toml"
    policy_file.write_text(
        'version = "1"\n\n[role.local]\ntables = ["sales.customers"]\n'
        'denied_columns = ["sales.customers.email"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(ACCESS_POLICY_VAR, str(policy_file))

    grant = resolve_access_grant(tmp_path)
    assert not grant.is_open
    assert grant.tables == frozenset({"sales.customers"})
    assert grant.denied_columns == frozenset({"sales.customers.email"})

    stranger = StaticRoleAccessPolicy.from_toml(policy_file).grant_for(
        LOCAL_PRINCIPAL.__class__(id="someone", roles=frozenset({"unknown"}))
    )
    assert stranger == Grant(), "an unknown role widened into something"


def test_the_served_identity_and_the_principal_are_one_string() -> None:
    """``govern/access.py`` said ``LOCAL_PRINCIPAL`` was "not imported by ``api/`` today" and
    that writing the literal twice would be the drift this repository keeps auditing for.

    It is imported now. ``resume_authorised`` compares the ``identity`` the auth handler
    returns; ``resolve_access_grant`` asks the policy about a ``Principal``. If those two
    strings drift, a fork keyed on principal id authorizes one subject and resumes another —
    and nothing would fail, because neither side reads the other.
    """
    from governed_bi.api.auth import authenticated_principal

    assert authenticated_principal() == LOCAL_PRINCIPAL
    assert authenticated_principal().id == "governed-bi-local"

    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "governed_bi" / "api" / "auth.py"
    ).read_text(encoding="utf-8")
    handler = source.split("async def _authenticate(", 1)[1].split("\n@auth.", 1)[0]
    assert 'return {"identity": authenticated_principal().id' in handler, (
        "the auth handler minted its own copy of the identity string again"
    )
