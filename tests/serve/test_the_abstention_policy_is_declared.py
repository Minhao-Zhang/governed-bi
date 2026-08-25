"""The engine must be able to say *why* it withheld, in a vocabulary a gate can read.

**The defect this closes.** On the v4 arm 19 of 20 refusals end on ``r_table_not_licensed`` and
all four clarifications licensed nothing, so the engine never *decided* to withhold — retrieval
missed and Layer 6 blocked the fifth statement of five. "Declines on purpose" was a description
of an outcome, not of a mechanism.

**What is deliberately not here.** No score. ADR 0013 and the register row both say so, and the
measurements are why: a learned abstainer reached OOF AUC 0.597 with an "unsure" bucket as likely
to be right as its "correct" one, everything not reading meaning capped at 0.721, and every
risk-coverage curve reads 0.7144 at the engine's own coverage. This file has a test whose whole
job is to fail if a confidence field appears, over the module's **source** and not only its
output, because a behavioural check passes on the day it is written.

**The two halves that make a knob honest.** Off, the node's entire effect on state is the one
declared field — asserted on the update itself, not inferred from a passing turn. On, the policy
withholds the same turn *before the agent is called at all*, which is the property "evaluated
before the agent spends its five attempts" actually means.

**Which tests build a state dict, and why so few of them do.** ``ServeState`` has 47 channels and
the node signature names none of them, so a policy test used to have to assemble a plausible turn
— nested ``delivery`` and ``facets`` mappings included — to say "nothing failed". The rules take
:class:`~governed_bi.serve.abstention.AbstentionInputs` now, so a rule test states the one fact
it is about and the dict appears in exactly two places: the tests of the projection itself, and
the end-to-end pair that goes through ``build_serve_graph``. That split is asserted, not merely
observed — ``test_only_the_projection_reads_the_state_dict`` fails if a second reader appears.

**Two files, one scan.** The policy moved to ``serve/abstention.py`` and the LangGraph adapter
stayed in ``serve/nodes/abstain.py``, which is where the seam already was. The two source-reading
tests below therefore had to stop naming a path: a scan that walks one file after the logic moved
into two has silently narrowed, and would go on passing. :func:`_sources_of_the_policy` derives
its file set from where the policy's own symbols *live*, so a further split moves the scan with
it, and :func:`test_the_source_scan_covers_every_file_the_policy_lives_in` fails if the set ever
collapses back to one file or stops containing the names it claims to have read.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.corpus.schema import ColumnAsset, SchemaAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.facets import ChannelState
from governed_bi.register.stages import (
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
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.session import from_assets

QUESTION = "how many sales orders are there"

#: Inputs the policy has no objection to: a table licensed and a block rendered. Every other
#: field defaults, and the defaults are what "nothing failed, nothing was evicted" looks like —
#: which is why this is two facts and not a turn.
GOOD = AbstentionInputs(licensed=("sales.orders",), context_block="## Context\ntable sales.orders")

#: The smallest state dict the *node* needs to be a turn the policy would withhold. One key: the
#: adapter's own reads (``path_kind``, the knob) are absent, so the register's default applies.
WITHHELD_STATE: dict[str, Any] = {"licensed": []}


@pytest.fixture(autouse=True)
def _isolated():
    from governed_bi.serve.runtime import trust

    trust()
    yield
    trust()


# ── the vocabulary is closed, and every reader already knows it ───────────────


def test_every_reason_is_a_terminal_the_existing_readers_can_attribute() -> None:
    """A reason outside ``REFUSED_BY_TO_STAGE`` is free text wearing a vocabulary's clothes.

    **``classify_outcome`` is not evidence of that, and the last two lines say so.** It never
    consults the table: any truthy ``refused_by`` returns ``Outcome.refused``, so the assertion
    this test used to make about the four reasons held equally for a string in no register — a
    check that cannot fail for any member of a vocabulary is not a check on the vocabulary. The
    negative control is asserted here rather than deleted, so the weakness is on the record
    instead of reading as coverage.

    The falsifiable reader is
    :func:`~governed_bi.eval.report.refusal_histogram`, and its tests are
    ``tests/eval/test_the_declared_reasons_have_a_reader.py``. What this function still holds is
    the *mapping*: every reason is in the table and every one is filed under ``abstain``.

    ``Outcome.refused`` and not ``crashed``: declining on purpose is the product working, and
    the two must stay apart or every arm's crash rate absorbs its abstentions.
    """
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
    """Both directions, because they fail differently.

    A rule with an undeclared reason writes a ``terminal_reason`` nothing can attribute. A
    declared reason with no rule reads, to anyone grepping the vocabulary, as a decision the
    engine can take — which is the declared-machinery-with-no-wire shape open-work.md §3.10 is
    a whole section about.
    """
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


def test_the_verdict_carries_no_trust_signal() -> None:
    """The line between a ledger and theatre, asserted over the **source**.

    Reporting *why* the engine withheld is the ledger. Scoring *how sure it is* is theatre —
    ADR 0013's word, and the measurements behind
    it are in open-work.md §3.11. A behavioural test that no verdict carries a score passes for
    every input it happens to try; this one fails when someone adds the field.

    ``question_terms_in_corpus`` is in the evidence and is *not* a violation: nothing reads it,
    no rule thresholds it, and it is there so a person can see what the question asked for that
    the corpus does not have. The test below pins that it stays evidence.
    """
    found = sorted(word for word in _FORBIDDEN if word in _vocabulary_of_the_policy())
    assert not found, (
        f"the abstention policy grew a trust signal: {found}. ADR 0007 forbids it on the answer "
        "card and the reflector arm measured why — a judge whose 'unsure' bucket is as likely "
        "to be right as its 'correct' one has no perception of its own uncertainty to express"
    )

    verdict = decide(GOOD)
    assert set(verdict) == {"policy", "outcome", "reason", "rules_evaluated", "evidence"}
    assert verdict["outcome"] in ("answer", "withhold")


#: Every piece the policy and its adapter are made of, as objects — the *code*, not a list of
#: paths. :func:`_sources_of_the_policy` asks each one where it lives, which is what makes the
#: source scans follow a split instead of quietly aiming at the file the logic used to be in.
#:
#: Public symbols and the rule predicates, because both halves declare an ``__all__`` and a rule
#: is a lambda that can be moved on its own. The residual gap is honest and small: a *private*
#: helper moved to a third module, with no public symbol going with it, would leave the scan
#: behind. The floor in :func:`test_the_source_scan_covers_every_file_the_policy_lives_in` is
#: what catches the likely version of that — a file set that shrank.
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
    """Every file the policy's own code lives in, derived from the code.

    Two files today — ``serve/abstention.py`` holds the decision and ``serve/nodes/abstain.py``
    the LangGraph adapter — and this function does not know that. It asks
    :mod:`inspect` where each object in :data:`_THE_POLICY` was defined, so the day the rules
    move again the forbidden-word scan moves with them. A hardcoded path would have gone on
    passing over the half that stayed behind, which is the failure this whole file exists to
    make impossible for a *verdict field* and would have been embarrassing to reintroduce for
    the scan itself.

    Sorted, so a diff of what was scanned is stable.
    """
    import inspect

    files = {Path(inspect.getsourcefile(obj) or "").resolve() for obj in _THE_POLICY}
    return tuple(sorted(files))


def _trees_of_the_policy() -> tuple[tuple[Path, Any], ...]:
    """One parsed module per file. Two tests read the source rather than the behaviour — a
    forbidden field and a second projection are both things a behavioural check passes on the
    day it is written — and they share this file set, so a moved definition changes both scans
    or neither.

    Per file and never over a concatenation: two modules each opening with a docstring and
    ``from __future__ import annotations`` do not parse as one module at all, so joining the
    text would trade a scan for a ``SyntaxError``.
    """
    import ast

    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in _sources_of_the_policy()
    )


def _vocabulary_of_the_policy() -> str:
    """Every name and string literal the policy's files *use*, with prose excluded.

    An AST walk rather than a grep over the files, because the modules' own docstrings argue at
    length about why there is no confidence field, and a grep would fail on the explanation of
    the rule it is enforcing. Bare string statements — docstrings, at module, class and function
    level — are the only thing dropped; a string used as a dict key, an argument or a value is
    code and is kept, because ``{"confidence": ...}`` is exactly the shape being forbidden.

    One tree per file — see :func:`_trees_of_the_policy` for why a concatenation is not an
    option — and the union of their vocabularies, because a forbidden name is forbidden wherever
    the decision keeps it.
    """
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
    """The split's one dangerous consequence, closed.

    ``serve/nodes/abstain.py`` was 470 lines against ADR 0005 §6's soft cap of 400, and the
    split that pays that back separates the decision from the adapter. The risk it creates is
    not behavioural: it is that :func:`test_the_verdict_carries_no_trust_signal` goes on walking
    one file while half the logic sits in another, and passes for a ``confidence`` field it
    can no longer see.

    So four properties, all of them about the *scan* rather than the policy:

    * both halves are in it **by name** — the file :func:`decide` lives in and the file
      :func:`~governed_bi.serve.nodes.abstain.abstain_node` lives in. This is the assertion that
      fails if :data:`_THE_POLICY` is ever pruned down to one module's symbols;
    * more than one file, which is a tripwire on today's structure and not a law: if the two
      halves are deliberately merged back into one module, this line is the one to delete, and
      having to delete it is the point;
    * every file is real source under ``src/governed_bi/serve/``, so a stale ``.pyc`` or a
      test-local shim cannot stand in for it;
    * every name both halves export appears in the vocabulary the scan built. That is the
      anti-vacuity floor — the same shape as ``MIN_SOURCE_FILES`` in
      ``tests/api/test_http_contract_answer_and_stream.py`` — and it is what fails if
      :func:`_sources_of_the_policy` ever returns a file set that does not contain the code.
    """
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
    """``lexical_coverage`` is evidence and must not become a gate.

    ``negative_tau`` ships ``UNSET`` rather than guessed, on the argument that "an uncalibrated
    refusal gate is worse than none" — a benchmark whose questions are answerable by
    construction cannot calibrate one. A coverage threshold here would be that same
    uncalibrated gate under a different name, so the rule is: the evidence may carry the
    number, and no rule may branch on it.

    Driven, not read: two states differing **only** in ``lexical_coverage``, one at the floor,
    must reach the same verdict.
    """
    floor = replace(GOOD, question_terms_in_corpus=0.0)
    ceiling = replace(GOOD, question_terms_in_corpus=1.0)
    assert decide(floor)["outcome"] == decide(ceiling)["outcome"] == "answer"
    assert decide(floor)["reason"] is decide(ceiling)["reason"] is None
    assert abstention_evidence(floor)["question_terms_in_corpus"] == 0.0
    assert abstention_evidence(ceiling)["question_terms_in_corpus"] == 1.0


# ── each rule fires on its own evidence ───────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "inputs"),
    [
        ("retrieval_channel_failed", replace(GOOD, failed_channels=("facet_term.semantic",))),
        ("nothing_licensed", replace(GOOD, licensed=())),
        ("empty_context", replace(GOOD, context_block="(no context)")),
        ("licensed_table_evicted", replace(GOOD, tables_evicted=1)),
    ],
)
def test_each_rule_fires_on_its_own_evidence(reason: str, inputs: AbstentionInputs) -> None:
    """One case per rule, differing from a clean one in exactly the fact the rule reads.

    ``replace`` and one field, which is the shape of a predicate over named facts. It used to be
    a nested state dict per case — ``{"facets": {"facet_term": {"channels": {...}}}}`` to say one
    channel errored — and the *state* those built was never the thing under test: how a channel
    map becomes ``failed_channels`` is the projection's, and is asserted once, below.

    ``rules_evaluated`` stops at the rule that fired, and that is asserted rather than
    incidental: listing the rules after it would claim a check the policy never performed,
    which is the same class of untruth as a gate that leaves no trace.
    """
    verdict = decide(inputs)
    assert verdict["outcome"] == "withhold", verdict
    assert verdict["reason"] == reason, verdict
    assert verdict["rules_evaluated"][-1] == reason
    assert verdict["policy"] == ABSTENTION_POLICY
    assert verdict["evidence"], "a withheld turn carries no evidence for the decision"


def test_a_clean_turn_records_that_the_policy_let_it_through() -> None:
    """An ``answer`` verdict is a record, not an absence.

    Every rule appears in ``rules_evaluated``, so "the policy considered this turn" is a fact in
    the artifact. A gate that leaves a trace only when it fires cannot afterwards be told from
    one that was never wired up — ``negative``'s argument, and the reason it writes
    ``outcome: disabled`` every turn.
    """
    verdict = decide(GOOD)
    assert verdict["outcome"] == "answer"
    assert verdict["reason"] is None
    assert verdict["rules_evaluated"] == [rule.reason for rule in ABSTENTION_RULES]


def test_the_verdict_is_a_pure_function_of_its_inputs() -> None:
    """So a reader can recompute it from the row instead of trusting it.

    That is the property a score does not have, and it is what "in terms a person can check"
    means. Nothing here reads a model, a clock, the environment or the network — and, since the
    policy is handed :class:`AbstentionInputs` rather than the graph's state, "a pure function
    of *what*" is now answerable by reading the signature. Two equal inputs, two equal verdicts.
    """
    inputs = replace(GOOD, licensed=())
    assert decide(inputs) == decide(replace(inputs))
    assert decide(inputs)["evidence"] == abstention_evidence(inputs)


# ── the projection: the one place a state dict is read ────────────────────────


def test_the_projection_names_every_channel_the_policy_reads() -> None:
    """The node's read set, written down — the thing ``(state, config) -> dict`` does not say.

    Asserted as **whole-object equality**, so a ninth field cannot be added without this test
    naming it. Six channels arrive here (``licensed``, ``delivery`` twice over, ``facets``,
    ``retrieved``, ``schemas``, ``knobs_resolved``); the two the adapter keeps for itself,
    ``path_kind`` and ``turn_id``, are asserted where the adapter is.

    The empty case is the second half: an absent channel projects to the field's default, so
    "nothing recorded" and "recorded as empty" are one value here on purpose — every one of these
    fields is a count or a collection, and the policy's question of it is "is there any".
    """
    state = {
        "licensed": ["sales.orders", "sales.customers"],
        "delivery": {
            "context_block": "## Context",
            "evicted": {"tables_dropped": 2, "bodies_dropped": 3},
        },
        "facets": {"facet_schema": {"channels": {"lexical": "ran", "semantic": ChannelState.failed}}},
        "retrieved": {"lexical_coverage": 0.5},
        "schemas": ["sales"],
        "knobs_resolved": {"abstention_policy_enabled": True},
    }
    assert AbstentionInputs.from_state(state) == AbstentionInputs(
        licensed=("sales.orders", "sales.customers"),
        context_block="## Context",
        failed_channels=("facet_schema.semantic",),
        tables_evicted=2,
        bodies_evicted=3,
        schemas=("sales",),
        question_terms_in_corpus=0.5,
        policy_enabled=True,
    )
    assert AbstentionInputs.from_state({}) == AbstentionInputs()


def test_an_unconfigured_channel_is_not_a_failed_one() -> None:
    """The laptop case, and the reason this rule reads ``failed`` and not ``is_degraded``.

    A deployment with no embedder reports ``not_configured`` on every semantic channel by
    design. ``register/facets.is_degraded`` counts that as degradation — correctly, for a
    *health* gate — and a policy built on it would withhold every turn on every machine without
    an embedding key, which is most of them.

    A test of the **projection**, and it always was: the distinction lives in how a channel map
    becomes ``failed_channels``, so it is asserted on the field and then once more on the verdict
    that reads it.
    """
    unconfigured = {
        "licensed": ["sales.orders"],
        "delivery": {"context_block": "## Context"},
        "facets": {"facet_term": {"channels": {"semantic": ChannelState.not_configured}}},
    }
    inputs = AbstentionInputs.from_state(unconfigured)
    assert inputs.failed_channels == ()
    assert decide(inputs)["outcome"] == "answer"


def test_only_the_projection_reads_the_state_dict() -> None:
    """One projection, structurally — the property the whole split rests on.

    If two functions both know how to pull ``licensed`` out of a state dict then the read set is
    not written down anywhere after all, and the second reader is free to disagree with the
    first. That is not hypothetical here: ``measure/gates.py`` read ``Outcome.clarification`` as
    a witness of "reached stamp" and silently dropped every row that carried the corpus hash out
    of a gate's denominator.

    So: the functions that take a ``state`` at all are the projection's parts plus the adapter,
    and the private readers are called from ``from_state`` and nowhere else — including not from
    ``abstain_node``, which is the regression that would look most reasonable while re-opening
    exactly this. The rules are checked from the other side: each takes ``inputs``.

    Across **every** file the policy lives in, since the split. Walking only the module the
    readers happen to sit in would let a second reader appear in the other one, which is the
    same defect with a file boundary in front of it.
    """
    import ast

    functions: dict[str, Any] = {}
    lambdas: list[Any] = []
    for _path, tree in _trees_of_the_policy():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in functions, (
                    f"{node.name} is defined in two of the policy's files; "
                    "tools/check_one_implementation.py has more to say about that"
                )
                functions[node.name] = node
            elif isinstance(node, ast.Lambda):
                lambdas.append(node)

    projection = {
        "_failed_channels", "_evicted", "_context_block", "_licensed", "_lexical_coverage",
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


# ── off by default, and the node's whole effect is the declared field ─────────


def test_the_disabled_node_writes_one_key_and_nothing_else() -> None:
    """**The default-path proof, taken at the source rather than inferred from a green turn.**

    The node is registered with ``stream=False``, so it emits no timeline row. Its update, with
    the knob off, is exactly one key — the declared record field. No ``path_kind``, no
    ``terminal_reason``, no channel any other node reads. Together those are the whole of the
    claim that inserting it between ``assemble`` and ``agent_core`` changed nothing: there is no
    third way for a node to affect a turn.

    Asserting the *shape of the update* rather than the equality of two records is deliberate.
    An equality test needs a "before", and there is no before once the node is in the graph;
    this states the property directly and fails on any new key.
    """
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


def test_the_knob_is_what_turns_it_on() -> None:
    """Read through ``bool_knob``, so the precedence is state → ``knobs_resolved`` → register.

    The register default is ``False`` and the string ``"false"`` must not read as ``True`` —
    ``bool_knob`` owns that and this is the assertion that the policy goes through it rather
    than testing the value itself.
    """
    import asyncio

    from governed_bi.register.knobs import comparability_keys, knob_default

    assert knob_default("abstention_policy_enabled") is False
    assert "abstention_policy_enabled" in comparability_keys(), (
        "a knob that changes which turns are delivered must move the config hash, or two "
        "operating points compare as one treatment"
    )

    state = {**WITHHELD_STATE, "knobs_resolved": {"abstention_policy_enabled": True}}
    update = asyncio.run(_call(abstain_node, state))
    # Key order too: the update is a payload, and `stamp` copies the verdict onto an artifact
    # row a reader diffs across arms. The adapter translates the patch and adds nothing.
    assert list(update) == ["abstention", "path_kind", "terminal_reason"], update
    assert update["path_kind"] == "decline"
    assert update["terminal_reason"] == "nothing_licensed"
    assert update["abstention"]["outcome"] == "withhold"

    off = {**WITHHELD_STATE, "knobs_resolved": {"abstention_policy_enabled": "false"}}
    assert set(asyncio.run(_call(abstain_node, off))) == {"abstention"}


# ── end to end, through the served graph ──────────────────────────────────────


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
    return ScriptedChatModel(responses=[AIMessage(content="there are some orders")])


def _session(*, enabled: bool, model: Any) -> Any:
    """A corpus with a schema and no table, so retrieval licenses nothing.

    Not contrived: open-work.md §1.3 records four turns of the v4 arm that licensed nothing at
    all, and today every one of them reaches the agent, which writes SQL against a relation
    Layer 6 will refuse.
    """
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
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    if not enabled:
        return session
    # How `tools/run_datalake_eval.py --abstain` turns it on: a knob override folded into the
    # session's resolved knobs, which `Session.turn` seeds every turn from.
    return replace(
        session,
        knobs_resolved={**session.knobs_resolved, "abstention_policy_enabled": True},
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


def test_a_turn_that_licensed_nothing_reaches_the_agent_today_and_is_withheld_by_the_policy() -> None:
    """The paired turn, through ``build_serve_graph`` — the topology ``langgraph.json`` runs.

    Off, the turn goes to the agent and comes back with prose and no statement, which is exactly
    what v4 does with its four unlicensed turns and is what keeps v4 the control. On, the policy
    withholds it, and the thing that makes this a *decision* rather than a report is the last
    assertion: **the agent was never called.** Deciding after five refused ``run_query`` attempts
    is a description of what happened; deciding before the first one is a policy.

    **Renamed on 2026-08-18, from "is answered today".** ``_model``'s docstring already said it
    "answers from the context without a statement", and the control turn recorded
    ``outcome: answered`` anyway — ``stamp`` hardcoded ``has_sql=True`` for a finished agent loop
    with an empty ledger. It records ``Outcome.no_sql`` now, which sharpens ADR 0013's own case
    rather than weakening it: without the policy the engine returns prose over a corpus that
    licensed nothing, and the outcome no longer calls that an answer.

    The reason reaches ``terminal_reason``, which is the field the refusal histogram and
    ``eval/report.py`` already read — not a second field a new reader would have to know about.
    """
    committed, committed_model = _serve(enabled=False, thread="t-commit")
    withheld, withheld_model = _serve(enabled=True, thread="t-withhold")

    on_record = committed["answer"]["record"]
    off_record = withheld["answer"]["record"]

    assert not on_record["licensed"], (
        f"precondition: the turn was supposed to license nothing, got {on_record['licensed']!r}"
    )
    assert committed["answer"]["outcome"] == Outcome.no_sql.value, committed["answer"]
    assert on_record["abstention"]["outcome"] == "disabled"
    assert on_record["terminal_reason"] is None

    assert withheld["answer"]["outcome"] == "refused", withheld["answer"]
    assert off_record["terminal_reason"] == "nothing_licensed"
    assert withheld["answer"]["refused_by"] == "nothing_licensed"
    verdict = off_record["abstention"]
    assert verdict["outcome"] == "withhold"
    assert verdict["reason"] == "nothing_licensed"
    assert verdict["policy"] == ABSTENTION_POLICY
    assert verdict["evidence"]["n_licensed"] == 0
    assert verdict["evidence"]["schemas"] == on_record["schemas"]

    # **Before the agent spends an attempt.** The committed turn called the agent; the withheld
    # one did not, and there is no ledger row to show for it either.
    assert len(withheld_model.prompts_seen) < len(committed_model.prompts_seen), (
        f"the withheld turn made {len(withheld_model.prompts_seen)} model calls against the "
        f"committed turn's {len(committed_model.prompts_seen)}; a policy that runs after the "
        "agent is a report on a decision, not one"
    )
    assert not (off_record["execution"] or {}).get("attempts"), off_record["execution"]

    # This pair used to assert that both turns reached an injected turn log and that the withheld
    # one was logged `refused`. The log is gone and `turns` is not in `ServeOutput`, so the
    # observable is the answer the same node stamped.
    assert withheld["answer"]["outcome"] == "refused", withheld["answer"]
    assert committed["answer"]["outcome"] == Outcome.no_sql.value, committed["answer"]


def test_a_disabled_policy_puts_no_row_on_the_timeline(monkeypatch) -> None:
    """The second half of the default-path claim, measured on the wire.

    The node's *state* update is one key (above); this is its *event* output. Registered with
    ``stream=False``, so ``wrap_node`` emits neither the start nor the resolve row, and the node
    emits its own only when it judged something. Both halves are needed: a disabled node that
    still put two rows on every timeline would have changed the event stream of every arm
    measured so far, which is the exact reason ``reflect`` is registered the same way.

    Patches the writer rather than ``emit``, so the payload construction under test is real.
    """
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
        "policy": ABSTENTION_POLICY, "reason": "nothing_licensed"
    }
    assert "agent_core" in committed_steps and "agent_core" not in [
        e.get("step") for e in seen
    ], "the withheld turn still entered the agent loop"


def test_the_reason_reaches_the_artifact_row() -> None:
    """A reason no gate can read is the declared-machinery defect this repository keeps finding.

    Two hops, and both had to be built: ``stamp`` projects the verdict into the turn record, and
    ``eval/harness.project_turn`` carries it onto the artifact row. Without the second, an arm
    run with ``--abstain`` would decide, record the decision on a turn record nobody keeps, and
    produce an artifact in which the abstention is a bare ``terminal_reason`` with no evidence.
    """
    from governed_bi.eval.harness import project_turn

    withheld, _model = _serve(enabled=True, thread="t-artifact")
    row = project_turn(withheld, question={"question_id": "q-1"}, arm="abstain")

    assert row["terminal_reason"] == "nothing_licensed"
    assert row["abstention"]["reason"] == "nothing_licensed"
    assert row["abstention"]["evidence"]["n_licensed"] == 0
    assert row["outcome"] == "refused"


def test_the_driver_can_run_the_paired_arm() -> None:
    """The knob's wire. A comparability knob no driver can set is a treatment nobody can run.

    Source-level, because running the driver needs a paid model and a live database. What is
    asserted is the three things that make the arm real and distinguishable: the flag exists,
    it sets the knob, and it tags the artifact — the last because ``--resume`` compares corpus
    and prompt hashes and not this one, so an untagged abstaining run would resume into a
    committing artifact and the two would be reported as one.
    """
    source = (
        Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"
    ).read_text(encoding="utf-8")
    assert '"--abstain"' in source
    assert 'knob_overrides["abstention_policy_enabled"] = True' in source
    assert 'abstain_tag = "_abstain" if args.abstain else ""' in source
    assert "{abstain_tag}" in source
