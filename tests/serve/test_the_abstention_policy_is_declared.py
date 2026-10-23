"""Withholding records a reason a check can read. A retrieval miss is not that decision.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.corpus.schema import ColumnAsset, SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.registry.channels import ChannelState
from governed_bi.registry.stages import (
    ABSTENTION_REASONS,
    REFUSED_BY_TO_STAGE,
    Outcome,
    Stage,
    classify_outcome,
)
from governed_bi.serve.abstention import (
    ABSTENTION_POLICY,
    ABSTENTION_RULES,
    AbstentionInputs,
    AbstentionPatch,
    AbstentionRule,
    abstention_evidence,
    apply_policy,
    decide,
)
from governed_bi.serve.graph import as_sync
from governed_bi.serve.nodes.abstain import abstain_node
from governed_bi.serve.scripted_model import ScriptedChatModel, answer_call
from governed_bi.serve.session import from_assets

QUESTION = "how many sales orders are there"

#: Inputs the policy has no objection to: a table retrieved_tables and a block rendered. Every other
#: field defaults, and the defaults are what "nothing failed, nothing was evicted" looks like —
#: which is why this is two facts and not a turn.
GOOD = AbstentionInputs(retrieved_tables=("sales.orders",), context_block="## Context\ntable sales.orders")

#: The smallest state dict the *node* needs to be a turn the policy would withhold. One key: the
#: adapter's own reads (``path_kind``, the setting) are absent, so the register's default applies.
WITHHELD_STATE: dict[str, Any] = {"retrieved_tables": []}


@pytest.fixture(autouse=True)
def _isolated():
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()




def test_every_reason_is_a_terminal_the_existing_readers_can_attribute() -> None:
    """A reason outside ``REFUSED_BY_TO_STAGE`` is free text wearing a vocabulary's clothes."""
    assert ABSTENTION_REASONS, "the policy declares no reasons at all"
    assert ABSTENTION_REASONS <= set(REFUSED_BY_TO_STAGE)
    for reason in sorted(ABSTENTION_REASONS):
        assert REFUSED_BY_TO_STAGE[reason] is Stage.abstain, reason
        assert (
            classify_outcome(error=None, refused_by=reason, has_sql=False) is Outcome.refused
        ), reason

    undeclared = "banana_not_declared_anywhere"
    assert undeclared not in REFUSED_BY_TO_STAGE
    assert (
        classify_outcome(error=None, refused_by=undeclared, has_sql=False) is Outcome.refused
    ), (
        "classify_outcome has started reading REFUSED_BY_TO_STAGE. That is a behaviour change "
        "worth having, and it makes the loop above a real check — rewrite this docstring and "
        "delete the control"
    )


def test_the_policy_and_the_register_declare_the_same_reasons() -> None:
    """Both directions, because they fail differently."""
    assert {rule.reason for rule in ABSTENTION_RULES} == ABSTENTION_REASONS
    assert len({rule.reason for rule in ABSTENTION_RULES}) == len(ABSTENTION_RULES)
    for rule in ABSTENTION_RULES:
        assert rule.why.strip(), f"{rule.reason} withholds a turn and gives no reason why"


#: Words a trust signal is spelled with. ADR 0007 deleted the first three from the answer card
#: and ``ui/lib/schemas.ts`` pins that they must not come back; the rest are the names the same
#: idea arrives under next.
_FORBIDDEN = (
    "confidence", "certainty", "tier", "safety_clearance", "semantic_assurance",
    "probability", "trust_score", "reliability_score",
)


def test_the_result_carries_no_trust_signal() -> None:
    """The line between a ledger and, asserted over the **source**."""
    found = sorted(word for word in _FORBIDDEN if word in _vocabulary_of_the_policy())
    assert not found, (
        f"the abstention policy grew a trust signal: {found}. ADR 0007 forbids it on the answer "
        "card and the reflector config measured why — a judge whose 'unsure' bucket is as likely "
        "to be right as its 'correct' one has no perception of its own uncertainty to express"
    )

    result = decide(GOOD)
    assert set(result) == {"policy", "outcome", "reason", "rules_evaluated", "evidence"}
    assert result["outcome"] in ("answer", "withhold")


#: Every piece the policy and its adapter are made of, as objects — the *code*, not a list of
#: paths. :func:`_sources_of_the_policy` asks each one where it lives, which is what makes the
#:
_THE_POLICY: tuple[Any, ...] = (
    AbstentionInputs,
    AbstentionInputs.from_state,
    AbstentionPatch,
    AbstentionRule,
    abstention_evidence,
    apply_policy,
    decide,
    abstain_node,
    *(rule.fires for rule in ABSTENTION_RULES),
)


def _sources_of_the_policy() -> tuple[Path, ...]:
    """Every file the policy's own code lives in, derived from the code."""
    import inspect

    files = {Path(inspect.getsourcefile(obj) or "").resolve() for obj in _THE_POLICY}
    return tuple(sorted(files))


def _trees_of_the_policy() -> tuple[tuple[Path, Any], ...]:
    """One parsed module per file."""
    import ast

    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in _sources_of_the_policy()
    )


def _vocabulary_of_the_policy() -> str:
    """Every name and string literal the policy's files *use*, with prose excluded."""
    import ast

    words: list[str] = []
    for _path, tree in _trees_of_the_policy():
        prose = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                words.append(node.id)
            elif isinstance(node, ast.Attribute):
                words.append(node.attr)
            elif isinstance(node, ast.arg):
                words.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                words.append(node.name)
            elif isinstance(node, ast.keyword) and node.arg:
                words.append(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in prose:
                    words.append(node.value)
    return "\n".join(words)


def test_the_source_scan_covers_every_file_the_policy_lives_in() -> None:
    """The split's one dangerous consequence, closed."""
    import inspect

    from governed_bi.serve import abstention
    from governed_bi.serve.nodes import abstain

    sources = _sources_of_the_policy()
    for half in (decide, abstain_node):
        assert Path(inspect.getsourcefile(half) or "").resolve() in sources, half
    assert len(sources) >= 2, (
        f"the policy's code was found in {len(sources)} file(s): {sources}. The decision and the "
        "adapter live in two modules; a one-file scan is the narrowing this test exists to catch"
    )
    src = Path(__file__).resolve().parents[2] / "src" / "governed_bi" / "serve"
    for path in sources:
        assert path.suffix == ".py" and path.is_file(), path
        assert src in path.parents, f"{path} is not under {src}"

    vocabulary = _vocabulary_of_the_policy()
    exported = [*abstention.__all__, *abstain.__all__]
    assert len(exported) >= 8, exported
    missing = [name for name in exported if name not in vocabulary]
    assert not missing, (
        f"the scan read {len(sources)} file(s) and never saw {missing}, which both halves "
        "export. Whatever it read is not the policy"
    )


def test_no_rule_reads_a_threshold() -> None:
    """``lexical_coverage`` is evidence and must not become a check."""
    floor = replace(GOOD, question_terms_in_corpus=0.0)
    ceiling = replace(GOOD, question_terms_in_corpus=1.0)
    assert decide(floor)["outcome"] == decide(ceiling)["outcome"] == "answer"
    assert decide(floor)["reason"] is decide(ceiling)["reason"] is None
    assert abstention_evidence(floor)["question_terms_in_corpus"] == 0.0
    assert abstention_evidence(ceiling)["question_terms_in_corpus"] == 1.0




@pytest.mark.parametrize(
    ("reason", "inputs"),
    [
        ("retrieval_channel_failed", replace(GOOD, failed_channels=("channel_term.semantic",))),
        ("nothing_retrieved", replace(GOOD, retrieved_tables=())),
        ("empty_context", replace(GOOD, context_block="(no context)")),
        ("retrieved_table_dropped", replace(GOOD, tables_evicted=1)),
    ],
)
def test_each_rule_fires_on_its_own_evidence(reason: str, inputs: AbstentionInputs) -> None:
    """One case per rule, differing from a clean one in exactly the fact the rule reads."""
    result = decide(inputs)
    assert result["outcome"] == "withhold", result
    assert result["reason"] == reason, result
    assert result["rules_evaluated"][-1] == reason
    assert result["policy"] == ABSTENTION_POLICY
    assert result["evidence"], "a withheld turn carries no evidence for the decision"


def test_a_clean_turn_records_that_the_policy_let_it_through() -> None:
    """An ``answer`` result is a record, not an absence."""
    result = decide(GOOD)
    assert result["outcome"] == "answer"
    assert result["reason"] is None
    assert result["rules_evaluated"] == [rule.reason for rule in ABSTENTION_RULES]


def test_the_result_is_a_pure_function_of_its_inputs() -> None:
    """So a reader can recompute it from the row instead of trusting it."""
    inputs = replace(GOOD, retrieved_tables=())
    assert decide(inputs) == decide(replace(inputs))
    assert decide(inputs)["evidence"] == abstention_evidence(inputs)




def test_the_projection_names_every_channel_the_policy_reads() -> None:
    """The node's read set, written down — the thing ``(state, config) -> dict`` does not say."""
    state = {
        "retrieved_tables": ["sales.orders", "sales.customers"],
        "delivery": {
            "context_block": "## Context",
            "evicted": {"tables_dropped": 2, "bodies_dropped": 3},
        },
        "channels": {"channel_schema": {"channels": {"lexical": "ran", "semantic": ChannelState.failed}}},
        "retrieved": {"lexical_coverage": 0.5},
        "schemas": ["sales"],
        "settings_resolved": {"abstention_policy_enabled": True},
    }
    assert AbstentionInputs.from_state(state) == AbstentionInputs(
        retrieved_tables=("sales.orders", "sales.customers"),
        context_block="## Context",
            failed_channels=("channel_schema.semantic",),
        tables_evicted=2,
        bodies_evicted=3,
        schemas=("sales",),
        question_terms_in_corpus=0.5,
        policy_enabled=True,
    )
    assert AbstentionInputs.from_state({}) == AbstentionInputs()


def test_an_unconfigured_channel_is_not_a_failed_one() -> None:
    """The laptop case, and the reason this rule reads ``failed`` and not ``is_degraded``."""
    unconfigured = {
        "retrieved_tables": ["sales.orders"],
        "delivery": {"context_block": "## Context"},
        "channels": {"channel_term": {"channels": {"semantic": ChannelState.not_configured}}},
    }
    inputs = AbstentionInputs.from_state(unconfigured)
    assert inputs.failed_channels == ()
    assert decide(inputs)["outcome"] == "answer"


def test_only_the_projection_reads_the_state_dict() -> None:
    """One projection, structurally — the property the whole split rests on."""
    import ast

    functions: dict[str, Any] = {}
    lambdas: list[Any] = []
    for _path, tree in _trees_of_the_policy():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in functions, (
                    f"{node.name} is defined in two of the policy's files"
                )
                functions[node.name] = node
            elif isinstance(node, ast.Lambda):
                lambdas.append(node)

    projection = {
        "_failed_channels", "_evicted", "_context_block", "_retrieved_tables", "_lexical_coverage",
    }
    assert projection <= set(functions)
    assert {name for name, fn in functions.items()
            if any(arg.arg == "state" for arg in fn.args.args)} == projection | {
        "from_state", "abstain_node"
    }

    for name, fn in functions.items():
        if name in projection or name == "from_state":
            continue
        used = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}
        assert not used & projection, (
            f"{name} projects the state dict itself. AbstentionInputs.from_state is the one "
            "place that knows how to read a channel, and a second one is a second answer"
        )

    assert len(lambdas) == len(ABSTENTION_RULES)
    for fires in lambdas:
        assert [arg.arg for arg in fires.args.args] == ["inputs"], (
            "a rule that takes the state dict can grow a fifth input the node's declared "
            "interface does not carry"
        )




def test_the_disabled_node_writes_one_key_and_nothing_else() -> None:
    """The node is registered with ``stream=False``, so it emits no timeline row."""
    import asyncio

    assert decide(AbstentionInputs.from_state(WITHHELD_STATE))["outcome"] == "withhold", (
        "precondition: this state must be one the policy would withhold, or the assertion "
        "below passes because there was nothing to suppress"
    )

    update = asyncio.run(_call(abstain_node, WITHHELD_STATE))
    assert set(update) == {"abstention"}, update
    assert update["abstention"] == {
        "policy": ABSTENTION_POLICY,
        "outcome": "disabled",
        "reason": None,
        "rules_evaluated": [],
        "evidence": {},
    }


async def _call(node: Any, state: dict) -> dict:
    """Run a node the way ``wrap_node`` does, minus the wrapper."""
    result = node(state, {"configurable": {}})
    if hasattr(result, "__await__"):
        return await result
    return result


def test_the_setting_is_what_turns_it_on() -> None:
    """Read through ``bool_setting``, so the precedence is state → ``settings_resolved`` → register."""
    import asyncio

    from governed_bi.registry.settings import setting_default, setting_names

    assert setting_default("abstention_policy_enabled") is False
    assert "abstention_policy_enabled" in setting_names(), (
        "a setting that changes which turns are delivered must move the config hash, or two "
        "operating points compare as one treatment"
    )

    state = {**WITHHELD_STATE, "settings_resolved": {"abstention_policy_enabled": True}}
    update = asyncio.run(_call(abstain_node, state))
    # Key order too: the update is a payload, and `finish` copies the result onto an artifact
    # row a reader diffs across configs. The adapter translates the patch and adds nothing.
    assert list(update) == ["abstention", "path_kind", "terminal_reason"], update
    assert update["path_kind"] == "decline"
    assert update["terminal_reason"] == "nothing_retrieved"
    assert update["abstention"]["outcome"] == "withhold"

    off = {**WITHHELD_STATE, "settings_resolved": {"abstention_policy_enabled": "false"}}
    assert set(asyncio.run(_call(abstain_node, off))) == {"abstention"}




class _EchoConnector:
    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["n"], [(1,)], False)


class _TurnLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_turn(self, record: Any, **kwargs: Any) -> tuple[str | None, str | None]:
        self.rows.append({"record": dict(record), **kwargs})
        return record.get("turn_id"), None


def _model() -> ScriptedChatModel:
    """Answers from the context without a statement. Its **call count** is the assertion."""
    return ScriptedChatModel(responses=[answer_call("there are some orders")])


def _session(*, enabled: bool, model: Any) -> Any:
    """A corpus with a schema and no table, so retrieval licenses nothing."""
    assets = [
        SchemaAsset(id="sales", name="sales", summary="sales orders and customers"),
        TableAsset(
            id="hr.headcount", schema="hr", physical_name="headcount",
            summary="Headcount by department.", columns=("hr.headcount.n",),
        ),
        ColumnAsset(
            id="hr.headcount.n", schema="hr", parent_table="hr.headcount",
            physical_name="n", summary="How many people.",
        ),
    ]
    session = from_assets(
        assets,
        connector=_EchoConnector(),
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
        agent_model=model,
        # Its own model: the rewriters read prose, and the agent's script ends in a tool call.
        utility_model=ScriptedChatModel(responses=[AIMessage(content="there are some orders")]),
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    if not enabled:
        return session
    # How `tools/run_datalake_eval.py --abstain` turns it on: a setting override folded into the
    # session's resolved settings, which `Session.turn` seeds every turn from.
    return replace(
        session,
        settings_resolved={**session.settings_resolved, "abstention_policy_enabled": True},
    )


def _serve(*, enabled: bool, thread: str) -> tuple[dict[str, Any], ScriptedChatModel]:
    from governed_bi.api.graph_app import build_serve_graph

    model = _model()
    graph = as_sync(build_serve_graph(_session(enabled=enabled, model=model)))
    out = graph.invoke(
        {"messages": [HumanMessage(content=QUESTION)]},
        {"configurable": {"thread_id": thread}},
    )
    return out, model


def test_a_turn_that_retrieved_tables_nothing_reaches_the_agent_today_and_is_withheld_by_the_policy() -> None:
    """The paired turn, through ``build_serve_graph`` — the topology ``langgraph.json`` runs."""
    committed, committed_model = _serve(enabled=False, thread="t-commit")
    withheld, withheld_model = _serve(enabled=True, thread="t-withhold")

    on_record = committed["answer"]["record"]
    off_record = withheld["answer"]["record"]

    assert not on_record["retrieved_tables"], (
        f"precondition: the turn was supposed to retrieve nothing, got {on_record['retrieved_tables']!r}"
    )
    assert committed["answer"]["outcome"] == Outcome.no_sql.value, committed["answer"]
    assert on_record["abstention"]["outcome"] == "disabled"
    assert on_record["terminal_reason"] is None

    assert withheld["answer"]["outcome"] == "refused", withheld["answer"]
    assert off_record["terminal_reason"] == "nothing_retrieved"
    assert withheld["answer"]["refused_by"] == "nothing_retrieved"
    result = off_record["abstention"]
    assert result["outcome"] == "withhold"
    assert result["reason"] == "nothing_retrieved"
    assert result["policy"] == ABSTENTION_POLICY
    assert result["evidence"]["n_retrieved_tables"] == 0
    assert result["evidence"]["schemas"] == on_record["schemas"]

    # **Before the agent spends an attempt.** The committed turn called the agent; the withheld
    # one did not, and there is no ledger row to show for it either.
    assert len(withheld_model.prompts_seen) < len(committed_model.prompts_seen), (
        f"the withheld turn made {len(withheld_model.prompts_seen)} model calls against the "
        f"committed turn's {len(committed_model.prompts_seen)}; a policy that runs after the "
        "agent is a report on a decision, not one"
    )
    assert not (off_record["execution"] or {}).get("attempts"), off_record["execution"]

    # one was logged `refused`. The log is gone and `turns` is not in `ServeOutput`, so the
    # observable is the answer the same node wrote.
    assert withheld["answer"]["outcome"] == "refused", withheld["answer"]
    assert committed["answer"]["outcome"] == Outcome.no_sql.value, committed["answer"]


def test_a_disabled_policy_puts_no_row_on_the_timeline(monkeypatch) -> None:
    """The second half of the default-path claim, measured on the wire."""
    from governed_bi.serve import events

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(events, "get_stream_writer", lambda: seen.append)

    _serve(enabled=False, thread="t-quiet")
    assert not [e for e in seen if e.get("step") == "abstain"], (
        "a disabled abstention policy put rows on the timeline"
    )
    committed_steps = [e.get("step") for e in seen]

    seen.clear()
    _serve(enabled=True, thread="t-loud")
    rows = [e for e in seen if e.get("step") == "abstain"]
    assert len(rows) == 1, f"one row per judged turn, got {rows}"
    assert rows[0]["status"] == "declined"
    assert rows[0]["detail"] == {
        "policy": ABSTENTION_POLICY, "reason": "nothing_retrieved"
    }
    assert "agent_core" in committed_steps and "agent_core" not in [
        e.get("step") for e in seen
    ], "the withheld turn still entered the agent loop"


def test_the_reason_reaches_the_artifact_row() -> None:
    """A reason no check can read is the declared-machinery defect this repository keeps finding."""
    from governed_bi.eval.harness import project_turn

    withheld, _model = _serve(enabled=True, thread="t-artifact")
    row = project_turn(withheld, question={"question_id": "q-1"}, config="abstain")

    assert row["terminal_reason"] == "nothing_retrieved"
    assert row["abstention"]["reason"] == "nothing_retrieved"
    assert row["abstention"]["evidence"]["n_retrieved_tables"] == 0
    assert row["outcome"] == "refused"


def test_the_driver_can_run_the_side_by_side_config() -> None:
    """The setting's wire."""
    source = (
        Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"
    ).read_text(encoding="utf-8")
    assert '"--abstain"' in source
    assert 'setting_overrides["abstention_policy_enabled"] = True' in source
    assert 'abstain_tag = "_abstain" if args.abstain else ""' in source
    assert "{abstain_tag}" in source
