"""Every declared mutation: one defect, re-introduced on purpose, with the test that must fail.

Split out of ``tools/mutate.py`` on 2026-08-11 for the reason ADR 0005 §6 gives — the runner
plus the catalogue crossed the 1 000-line hard cap, and it will keep growing, because a
catalogue is the one thing in this repository that is *supposed* to be append-only. The runner
is a hundred lines and stable; this file is a list.

Read ``tools/mutate.py`` for what a run proves and what it does not.
"""

from __future__ import annotations

import dataclasses

__all__ = ["Mutation", "MUTATIONS"]


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One defect, re-introduced on purpose.

    ``anchor`` must appear **exactly once** in ``path``; a count of 0 or 2 fails the run rather
    than silently mutating the wrong line or nothing at all. ``tests`` is a pytest selection kept
    as narrow as the property allows, because the whole file runs once per mutation.
    """

    id: str
    what: str
    path: str
    anchor: str
    replacement: str
    tests: tuple[str, ...]
    #: The audit finding, so a failure here points at the reasoning rather than only the line.
    finding: str


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        id="m1-guard-bypass",
        what="a refused verdict yields executable SQL",
        path="src/governed_bi/govern/pipeline.py",
        anchor='    if not verdict["passed"]:',
        replacement="    if False:",
        tests=("tests/govern",),
        finding="M1 — 133/133 tests/govern tests passed against this",
    ),
    # ── the layer stack, against tests/govern/test_adversarial_suite.py ───────────────────────
    #
    # The adversarial suite is the first measurement of what governance buys (open-work.md 3.11),
    # and a measurement whose instrument cannot fail is audit finding D13 with a bigger
    # denominator. These are the positive control: each deletes one layer's decision, or
    # re-introduces a resolver defect open-work.md 3.2a reproduced, and each was
    # confirmed by hand to make that file fail before being written down here. There were seven
    # of them until `g6` was retired below — its defect class no longer has a line to break.
    #
    # `g7` re-introduces the *ordering* rather than the outcome. Rewriting the poison line to its
    # old text does not reproduce the defect — the cross-schema branch poisons the key anyway —
    # so the mutation puts back the early `continue`, which is the whole content of the bug.
    Mutation(
        id="g1-function-allowlist-open",
        what="every function call is permitted, so the B1 and B2 families walk through",
        path="src/governed_bi/govern/check.py",
        anchor="            if name not in policy.permitted_functions:",
        replacement="            if False:",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="ADR 0006 §2 — the positive allowlist is the only thing between pg_read_file, the "
                "XML-export family and the analyst; neither the column nor the table layer sees them",
    ),
    Mutation(
        id="g2-write-constructs-allowed",
        what="a DELETE or UPDATE hidden inside a read-rooted statement stops being seen",
        path="src/governed_bi/govern/check.py",
        anchor="        if isinstance(node, WRITE_NODES):",
        replacement="        if False:",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="ADR 0006 §1 — `WITH d AS (DELETE ... RETURNING *) SELECT * FROM d` is a Select at "
                "the root and deletes rows; the root check alone calls it a read. Caught as a "
                "*misattribution*: the two CTE cases then refuse at COLUMNS instead, so a gate that "
                "only asked 'was it refused' would have reported the NO_WRITE walk as working",
    ),
    Mutation(
        id="g3-excluded-column-allowed",
        what="a governance-excluded column stops being refused",
        path="src/governed_bi/govern/check.py",
        anchor="        if binding.column_key in excluded:",
        replacement="        if False:",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="the COLUMNS layer is the confidentiality control; without it `excluded` is a "
                "rendering preference. Caught as a *misattribution*, and that is the informative "
                "part: `for_analyst` also keeps excluded keys out of `allowed_columns`, so all five "
                "cases still refuse under `r_column_not_allowed`. Exclusion is defence in depth and "
                "only a rule-level check can tell which of the two is holding",
    ),
    Mutation(
        id="g4-table-layer-open",
        what="an unlicensed base table stops being refused",
        path="src/governed_bi/govern/check.py",
        anchor="        if key not in licensed:",
        replacement="        if False:",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="B4 — in a pooled 57-schema lake this is every other schema, reachable from a "
                "statement that names one licensed table and joins to anything",
    ),
    Mutation(
        id="g5-star-projection-allowed",
        what="`SELECT *` stops refusing, so a statement reads columns it never names",
        path="src/governed_bi/govern/binding.py",
        anchor="""        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Func):
            return LayerRefusal(
                "r_star_projection",""",
        replacement="""        if False:
            return LayerRefusal(
                "r_star_projection",""",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="ADR 0006 §4 — the allowlist cannot vouch for columns a query never enumerates, "
                "and the excluded column arrives without ever being written down",
    ),
    # `g6-derived-alias-blind` is **retired**, 2026-08-12, and deliberately not re-anchored. It
    # re-introduced 3.2a's first defect as `if handle in defined or handle in derived:` ->
    # `if handle in defined:`, and that line is gone: `pipeline._column_sources` now resolves each
    # reference in its own scope and then that scope's ancestors, mirroring `binding.py::_lookup`
    # over the same `scope.sources` mapping. There is no tree-wide set left to go blind to and no
    # single predicate whose loss re-opens the defect — the two resolvers agree by construction
    # rather than by a test noticing when they stop, so the class is structurally unreachable.
    #
    # **The obvious re-anchor was tried and rejected.** `_handles_in_scope`'s `elif alias:` branch,
    # which maps a derived source to `None`, looks like the successor line and bites nothing:
    # 210/210 tests/govern pass and 0/115 adversarial cases fail either way, because every statement
    # that reaches the difference refuses at the flat pass first. A mutation that cannot fail is
    # D13 with a bigger denominator, and it would claim coverage this catalogue no longer has.
    #
    # Read instead, if this regresses: `test_a_derived_alias_elsewhere_does_not_shadow_a_base_handle`
    # in `tests/govern/test_guard_pipeline_ledger.py` for the resolution, and in
    # `govern/adversarial.toml` the benign pair `..._does_not_shadow_this_table` and
    # `..._does_not_shadow_this_bare_table_name` for the false refusal the tree-wide fix cost.
    Mutation(
        id="g7-self-collision-not-poisoned",
        what="the own-collision guard returns before the cross-schema poison write again",
        path="src/governed_bi/govern/pipeline.py",
        anchor="""        own_spellings, own_ambiguous = fold_map(own)
        physical_name = getattr(table, "physical_name", None)""",
        replacement="""        own_spellings, own_ambiguous = fold_map(own)
        if own_ambiguous:
            continue
        physical_name = getattr(table, "physical_name", None)""",
        tests=("tests/govern/test_adversarial_suite.py",),
        finding="open-work.md 3.2a, second defect — a table whose own columns collide by case left "
                "its bare handle owned by another schema's table of the same name",
    ),
    Mutation(
        id="g8-whole-row-argument-rule-deleted",
        what="the function layer stops inspecting its arguments, so `count(t.*)` is just a count",
        path="src/governed_bi/govern/check.py",
        anchor="            for node in _scope_arguments(func, own):",
        replacement="            for node in ():",
        tests=(
            "tests/govern/test_adversarial_suite.py::"
            "test_no_attack_is_refused_by_the_wrong_layer_or_rule",
        ),
        finding="B2 — a whole-row argument emits every column of the row, excluded and suspect "
                "included, with zero Column nodes for any of them. The suite catches this as a "
                "*misattribution* and not as a bypass, which is the point of measuring the two "
                "separately: the star still refuses one layer later under a rule about "
                "projections, and a gate that only asked 'was it refused' would report the "
                "whole-row rule as working after it had been deleted. Written as a loop deletion "
                "rather than as `if False` on either branch, because the branches are not "
                "interchangeable and neither one alone is the rule: `count(t.*)` is an "
                "`exp.Column` whose `this` is a Star, so **only the qualified branch fires for "
                "it**, and a bare `f(*)` is an `exp.Star`, which only the other reaches. "
                "Instrumented over all 115 cases (2026-08-12), counting *executions* — each case "
                "runs the stack twice, once through `check()` and once through `prepare()`, so "
                "every figure here is two per case: the `count(*)` carve-out `continue` fires 12 "
                "(6 cases), the qualified branch 2 (`b2_count_qualified_star`) and the bare-Star "
                "refuse arm 2 (`b2_count_distinct_star`, the case that gave that arm any case at "
                "all). One case each, so `if False` on either branch is a mutation one case can "
                "see; deleting the loop is the one mutation that removes every arm together",
    ),
    Mutation(
        id="g9-star-refused-for-the-wrong-reason",
        what="a star projection refuses under another rule of the same layer",
        path="src/governed_bi/govern/binding.py",
        anchor="""            return LayerRefusal(
                "r_star_projection",
                "a star projection expands to columns the statement never names, so """,
        replacement="""            return LayerRefusal(
                "r_unbound_reference",
                "a star projection expands to columns the statement never names, so """,
        tests=(
            "tests/govern/test_adversarial_suite.py::"
            "test_no_attack_is_refused_by_the_wrong_layer_or_rule",
        ),
        finding="the misattribution half, which no other mutation reaches: the statement is still "
                "refused and the rule written to catch it never fired, so the next spelling of the "
                "shape walks through with a green suite behind it",
    ),
    Mutation(
        id="g10-refuse-everything",
        what="the checker refuses every statement, which scores a perfect bypass rate",
        path="src/governed_bi/govern/check.py",
        anchor="        return allow(evaluated=evaluated, bound=bound.as_bound())",
        replacement='        return refuse("r_column_not_allowed", "mutant", evaluated=evaluated)',
        tests=(
            "tests/govern/test_adversarial_suite.py::test_the_false_refusal_rate_is_reported",
        ),
        finding="the benign half's own positive control. `def check(...): return {'passed': False}` "
                "passes every attack test ever written, and v1 shipped a refuse gate whose "
                "false-positive rate nobody had measured",
    ),
    Mutation(
        id="c1-no-ledger-row",
        what="a checker that raises writes no ledger row",
        path="src/governed_bi/serve/tools.py",
        anchor="""            return _reply(
                runtime,
                f"run_query error: {type(exc).__name__}: {exc}",
                attempts_by_call={
                    call_id: pipeline_error_attempt("agent", f"{type(exc).__name__}: {exc}")
                },
            )""",
        replacement='            return _reply(runtime, f"run_query error: {type(exc).__name__}: {exc}")',
        tests=(
            "tests/serve/test_agent_tools_hitl.py::"
            "test_a_checker_that_raises_is_recorded_rather_than_returned_as_a_string",
        ),
        finding="C1 — empty ledger reads as 'answered from context'",
    ),
    Mutation(
        id="c3-guardrail-error-is-refused",
        what="a swallowed layer exception records as refused, not crashed",
        path="src/governed_bi/serve/nodes/stamp.py",
        anchor="""            if isinstance(errors, int) and errors > 0:
                return GUARDRAIL_ERROR, Stage.check.value, None, None, False
""",
        replacement="",
        tests=("tests/serve/test_a_swallowed_layer_exception_is_a_crash.py",),
        finding="C3 — our bug recorded as the product working",
    ),
    Mutation(
        id="c5-empty-knobs-substituted",
        what="stamp substitutes {} for an absent knobs_resolved",
        path="src/governed_bi/serve/nodes/stamp.py",
        anchor='''    if projected_state.get("n_re_served") is None:
        projected_state["n_re_served"] = 0
''',
        replacement='''    if projected_state.get("n_re_served") is None:
        projected_state["n_re_served"] = 0
    if projected_state.get("knobs_resolved") is None:
        projected_state["knobs_resolved"] = {}
''',
        tests=("tests/serve/test_unwired_knobs_are_not_quotable.py",),
        finding="C5 — an arm of empties passes the drift gate",
    ),
    Mutation(
        id="c7-no-shape-check",
        what="a node returning None escapes the wrapper uncaught",
        path="src/governed_bi/serve/wrap.py",
        anchor='''        if not isinstance(update, Mapping):
            raise TypeError(
                f"node {stage!r} returned {type(update).__name__}, not a mapping. A LangGraph "
                "node returns a partial state dict; returning None is not 'no update'."
            )
''',
        replacement="",
        tests=(
            "tests/serve/test_node_timeout_is_enforced_inside_the_wrapper.py::"
            "test_a_node_that_returns_no_mapping_crashes_inside_the_wrapper",
        ),
        finding="C7 — no crashed marker, no answer, no final event",
    ),
    Mutation(
        id="d7-corpus-gate-weakened",
        what="the corpus gate is swapped for a weak stand-in",
        path="src/governed_bi/measure/gates.py",
        anchor='    "corpus_content_hash": _corpus_content_hash_gate,',
        replacement='    "corpus_content_hash": _zero_count_gate("corpus_content_hash", "crashed"),',
        tests=("tests/measure/test_the_corpus_is_gated_not_only_declared.py",),
        finding="D7 — two arms over two corpora passed all six gates",
    ),
    Mutation(
        id="a1-custom-routes-open",
        what="the custom routes stop requiring a key",
        path="src/governed_bi/api/routes.py",
        anchor='    if request.method != "OPTIONS" and request.url.path not in _OPEN_PATHS:',
        replacement="    if False:",
        tests=("tests/api/test_the_custom_routes_require_a_key.py",),
        finding="A1/A7 — /audit/turns returned every thread's SQL to anybody",
    ),
    Mutation(
        id="a1-preflight-gated",
        what="a CORS preflight is refused for having no key",
        path="src/governed_bi/api/routes.py",
        anchor='    if request.method != "OPTIONS" and request.url.path not in _OPEN_PATHS:',
        replacement="    if request.url.path not in _OPEN_PATHS:",
        tests=(
            "tests/api/test_the_custom_routes_require_a_key.py::"
            "test_a_cors_preflight_is_not_refused",
        ),
        finding="a bug shipped and caught the same day — blocks every cross-origin call",
    ),
    Mutation(
        id="a4-reads-the-wrong-key",
        what="the hook reads value['command'], where the runtime does not put it",
        path="src/governed_bi/api/auth.py",
        anchor="    for holder in (value.get(\"kwargs\"), value):",
        replacement="    for holder in (value,):",
        tests=("tests/api/test_a_run_cannot_write_state.py",),
        finding="A4 as first shipped — `langgraph_api` nests the command under `kwargs`, so the "
                "handler returned early and allowed the forged payload end to end. The direct-call "
                "test, both mutations and the audit row all said it worked. Caught in review.",
    ),
    Mutation(
        id="a4-handler-not-registered",
        what="the decorator is removed, so run creation is fail-open and silent",
        path="src/governed_bi/api/auth.py",
        anchor="@auth.on.threads.create_run\n",
        replacement="",
        tests=(
            "tests/api/test_a_run_cannot_write_state.py::"
            "test_the_handler_is_actually_registered_for_run_creation",
        ),
        finding="`_get_handler` returns None on no match and `handle_event` treats that as allow; "
                "deleting this one line left the original A4 test green",
    ),
    Mutation(
        id="a4-resume-refused-too",
        what="the paused-turn protocol is broken by a blanket deny",
        path="src/governed_bi/api/auth.py",
        anchor='_STATE_WRITING_COMMANDS = ("update", "goto")',
        replacement='_STATE_WRITING_COMMANDS = ("update", "goto", "resume")',
        tests=(
            "tests/api/test_a_run_cannot_write_state.py::"
            "test_the_runtime_dispatch_still_allows_a_resume",
        ),
        finding="a blanket deny looks like the fix and removes the feature: `ask_user` interrupts "
                "and the UI answers with `command.resume`. Nearly lost when the A4 mutations were "
                "rewritten against the real path.",
    ),
    Mutation(
        id="a4-unknown-shape-fails-open",
        what="a command this hook cannot read is allowed instead of refused",
        path="src/governed_bi/api/auth.py",
        anchor="        if not isinstance(command, Mapping):",
        replacement="        if False:",
        tests=(
            "tests/api/test_a_run_cannot_write_state.py::"
            "test_a_command_shape_this_hook_cannot_read_is_refused_not_allowed",
        ),
        finding="failing open on an unexpected shape is how A4 survived its first fix; request "
                "encryption makes `command` ciphertext",
    ),
    Mutation(
        id="c2-wiring-failure-as-verdict",
        what="a missing connector is recorded as a governance refusal",
        path="src/governed_bi/serve/fetch.py",
        anchor='''        raise GovernanceUsageError(
            "run_query has no connector: configurable['connector'] is None. A missing connector "
            "is a wiring failure, and a turn served without one cannot tell a governance "
            "refusal from its own wiring failure."
        )''',
        replacement='''        from governed_bi.govern.layers import refuse

        return (
            "run_query error: no connector configured",
            attempt_record(refuse("r_not_a_read", "no connector"), "agent", executed_sql=None),
        )''',
        tests=("tests/serve/test_a_wiring_failure_is_not_a_verdict.py",),
        finding="C2 — infrastructure failure indistinguishable from a proposed write",
    ),
    Mutation(
        id="d6-block-scalar-blind",
        what="rule A goes blind to a YAML block-scalar summary",
        path="tools/check_no_benchmark_discriminators.py",
        anchor="            joined = BLOCK_SCALAR.sub(\"\", \" \".join(current).strip(), count=1).strip()\n"
        "            blocks.append((start, joined))",
        replacement='            blocks.append((start, " ".join(current).strip()))',
        tests=("tests/conformance/test_no_benchmark_discriminators.py",),
        finding="D6 — 32 of 57 live schema assets use `>-`",
    ),
    Mutation(
        id="d6-misspelled-asset-type-exempt",
        what="a misspelled asset_type goes exempt from rule B",
        path="tools/check_no_benchmark_discriminators.py",
        anchor="if declared is None or declared.group(1).casefold() not in EXEMPT_ASSET_TYPES:",
        replacement='if declared is None or declared.group(1).casefold() == "schema":',
        tests=("tests/conformance/test_no_benchmark_discriminators.py",),
        finding="D6 — `asset_type: schmea` was silently exempt",
    ),
    Mutation(
        id="d5-rival-mcnemar-returns",
        what="a second mcnemar reappears in tools/",
        path="tools/query_summary_alignment.py",
        anchor="def paired(",
        replacement="def mcnemar(",
        # The gate, through the conformance test that runs every gate on a clean tree.
        tests=(
            "tests/conformance/test_register_closure.py::"
            "test_lint_gate_passes_on_a_clean_tree[check_one_implementation.py]",
        ),
        finding="D5 — the copy intersected unit sets and returned no MDE",
    ),
    Mutation(
        id="d11-singleton-scan-vacuous",
        what="the singleton rule looks outside the package at the wrong directory",
        path="tools/check_one_implementation.py",
        anchor='        tools_dir = ROOT / "tools"',
        replacement='        tools_dir = ROOT / "tools_that_do_not_exist"',
        tests=(
            "tests/conformance/test_register_closure.py::"
            "test_lint_gate_passes_on_a_clean_tree[check_one_implementation.py]",
        ),
        finding="an off-by-one parent made the new rule pass vacuously; caught by hand once",
    ),
    Mutation(
        id="e1-coerce-none-to-wrong",
        what="a regrade counts an unjudgeable row as wrong",
        path="tools/regrade.py",
        anchor="    unmeasured = sum(1 for r in after_rows if r.get(\"correct\") is None)",
        replacement="    unmeasured = 0",
        tests=("tests/eval/test_a_regrade_reports_a_paired_result.py",),
        finding="E1 — a 25-point improvement invented by a row nobody could grade",
    ),
    Mutation(
        id="i7-substitute-another-texts-vector",
        what="a failed embed falls back to the raw question's vector and reports `ran`",
        path="src/governed_bi/serve/runtime.py",
        anchor="        return None, ChannelState.failed",
        replacement="        return (list(fallback) if fallback else None), ChannelState.ran",
        tests=(
            "tests/retrieve/test_semantic_channel_query_vector.py",
            "tests/serve/test_facet_query_rewrite.py",
        ),
        finding="I7 — BM25 over the rewrite, cosine over the question, one score, `semantic: ran`",
    ),
    Mutation(
        id="i7-node-ignores-the-verdict",
        what="the node scores the semantic channel anyway when the query embed failed",
        path="src/governed_bi/serve/nodes/facets.py",
        anchor="        if query_vector_state is ChannelState.failed:",
        replacement="        if False:",
        tests=(
            "tests/retrieve/test_semantic_channel_query_vector.py::"
            "test_a_dead_embedder_reports_failed_and_scores_nothing",
        ),
        finding="I7 wiring — the unit test passes while the record still says `ran`",
    ),
    Mutation(
        id="i8-embed-a-different-string",
        what="the cache key is built from the raw summary, not the indexed text",
        path="src/governed_bi/retrieve/index.py",
        anchor="            text = indexed_text[entry.id]",
        replacement="            text = entry.summary",
        tests=("tests/retrieve/test_one_text_one_space.py",),
        finding="I8 — the two channels scored different strings",
    ),
    Mutation(
        id="i9-mix-two-vector-spaces",
        what="rows from another embedder are reused without a check",
        path="src/governed_bi/retrieve/index.py",
        anchor="        _refuse_a_mixed_vector_space(cached, keys, absent, embedder=embedder)\n",
        replacement="",
        tests=("tests/retrieve/test_one_text_one_space.py",),
        finding="I9 — one index, two spaces, cosine between them is noise, nothing raises",
    ),
    Mutation(
        id="p1-keys-scan-drops-the-projection",
        what="the key scan stops projecting, so it reads the vector column again",
        path="src/governed_bi/retrieve/vectors.py",
        anchor="            .select([_KEY_COLUMN])\n",
        replacement="",
        tests=(
            "tests/retrieve/test_vector_store.py::test_keys_does_not_read_the_vector_column",
        ),
        finding="the one-token form of P1; the first version of that test was green against it "
                "because it monkeypatched a different object. Caught in review.",
    ),
    Mutation(
        id="p3-reconnect-after-the-overwrite",
        what="the reconnect happens after create_table, so the overwrite still leaks",
        path="src/governed_bi/retrieve/vectors.py",
        anchor=(
            "        self._db = lancedb.connect(self._uri)\n"
            "        self._table = self._db.create_table(\n"
            '            self._name, rows, schema=_schema(self._dimensions), mode="overwrite"\n'
            "        )"
        ),
        replacement=(
            "        self._table = self._db.create_table(\n"
            '            self._name, rows, schema=_schema(self._dimensions), mode="overwrite"\n'
            "        )\n"
            "        self._db = lancedb.connect(self._uri)"
        ),
        tests=(
            "tests/retrieve/test_vector_store.py::"
            "test_replace_reconnects_rather_than_reusing_the_connection",
        ),
        finding="ordering, which the first version of that test could not see. Caught in review.",
    ),
    Mutation(
        id="i9-minting-probes-only-what-the-build-reuses",
        what="minting vouches for rows it never examined when the build reuses none",
        path="src/governed_bi/retrieve/index.py",
        anchor="        mine = sorted(k for k in cached.keys() if k.startswith(prefix) "
               "and k != canary_key)",
        replacement="        mine = sorted(keys[t] for t in reused)",
        tests=(
            "tests/retrieve/test_one_text_one_space.py::"
            "test_minting_examines_the_rows_it_vouches_for_even_when_the_build_reuses_none",
        ),
        finding="a corpus rewrite minted the canary in the new space and stamped the store "
                "verified with nothing compared. Caught in review.",
    ),
    Mutation(
        id="i9-cold-store-unprobed",
        what="a cold store skips the probe, so a same-process repoint is not caught",
        path="src/governed_bi/retrieve/index.py",
        anchor="        if reused and absent:",
        replacement="        if False:",
        tests=(
            "tests/retrieve/test_one_text_one_space.py::"
            "test_a_repoint_within_one_process_is_caught_when_anything_misses",
        ),
        finding="the `opened_with` gate reopened I9 through the public API. Caught in review.",
    ),
    Mutation(
        id="i9-check-only-when-writing",
        what="the space check runs only when there are misses to write",
        path="src/governed_bi/retrieve/index.py",
        anchor="        _refuse_a_mixed_vector_space(cached, keys, absent, embedder=embedder)",
        replacement=(
            "        if missing:\n"
            "            _refuse_a_mixed_vector_space(cached, keys, absent, embedder=embedder)"
        ),
        tests=(
            "tests/retrieve/test_one_text_one_space.py::"
            "test_a_repointed_gateway_is_caught_with_no_cache_miss_at_all",
        ),
        finding="I9 as first shipped — a repoint with an unchanged corpus has no misses, so the "
                "check never ran and a test asserted it stayed unmade. Caught in review.",
    ),
    Mutation(
        id="i9-probe-misses-the-last-third",
        what="the bootstrap probes sample only the first two thirds of the store",
        path="src/governed_bi/retrieve/index.py",
        anchor=(
            "            chosen = list(dict.fromkeys([mine[0], mine[n // 2], mine[n - 1]]))"
            "[:_SPACE_PROBES]"
        ),
        replacement="            chosen = mine[:: max(1, n // _SPACE_PROBES)][:_SPACE_PROBES]",
        tests=("tests/retrieve/test_one_text_one_space.py",),
        finding="a partial re-embed confined to alphabetically-late assets was invisible",
    ),
    Mutation(
        id="i1-raw-cosine-against-saturated-bm25",
        what="the semantic channel is fused raw, on a scale where it cannot win",
        path="src/governed_bi/serve/runtime.py",
        anchor=(
            '        scores["semantic"] = scale_to_ceiling(\n'
            "            float(semantic), ceiling=scale.semantic_ceiling\n"
            "        )"
        ),
        replacement='        scores["semantic"] = float(semantic)',
        tests=("tests/serve/test_channel_scale.py",),
        finding="I1 — a raw cosine cannot outrank a saturated BM25, so the channel is decorative",
    ),
    Mutation(
        id="i1-ceiling-does-not-clamp",
        what="one unusually good cosine contributes more than its declared weight",
        path="src/governed_bi/retrieve/fuse.py",
        anchor="    return min(1.0, value / ceiling)",
        replacement="    return value / ceiling",
        tests=(
            "tests/serve/test_channel_scale.py::"
            "test_the_ceiling_clamps_rather_than_letting_one_channel_exceed_its_weight",
        ),
        finding="I1 — an unclamped map silently un-declares w_semantic; fuse cannot see it",
    ),
    Mutation(
        id="p1-keys-reads-every-vector",
        what="keys() materialises the whole table to read one column",
        path="src/governed_bi/retrieve/vectors.py",
        # The whole `return`, so the mutant is the original full read and not an AttributeError:
        # `self._table.to_arrow().select([...])` raises before it can read anything, and
        # `mutate.py` only asks that a named test fail — so it would have reported "caught" for a
        # mutant that cannot express the defect. Caught in review.
        anchor=(
            "        return (\n"
            "            self._table.search()\n"
            "            .select([_KEY_COLUMN])"
        ),
        replacement="        return (\n            self._table.to_arrow()",
        tests=(
            "tests/retrieve/test_vector_store.py::test_keys_does_not_read_the_vector_column",
        ),
        finding="P1 — +407 MB transient per index build, under a docstring saying otherwise",
    ),
    Mutation(
        id="p3-replace-reuses-the-connection",
        what="a table overwrite reuses the connection and leaks committed pages",
        path="src/governed_bi/retrieve/vectors.py",
        # `__init__` connects too, so the anchor carries the next line to stay unique.
        anchor=(
            "        self._db = lancedb.connect(self._uri)\n"
            "        self._table = self._db.create_table("
        ),
        replacement="        self._table = self._db.create_table(",
        tests=(
            "tests/retrieve/test_vector_store.py::"
            "test_replace_reconnects_rather_than_reusing_the_connection",
        ),
        finding="P3 — 43.9 MB per call on a retained store; the ~50 GB scope claim was withdrawn",
    ),
    Mutation(
        id="p2-write-a-materialised-table",
        what="the writer is handed a whole table again instead of a reader",
        path="src/governed_bi/retrieve/vectors.py",
        anchor=(
            "        self._replace(\n"
            "            pa.RecordBatchReader.from_batches(schema, rekeyed()), len(pairs)\n"
            "        )"
        ),
        replacement="        batches = list(rekeyed())\n"
                    "        self._replace(\n"
                    "            pa.Table.from_batches(batches, schema=schema), len(pairs)\n"
                    "        )",
        tests=(
            "tests/retrieve/test_vector_store.py::"
            "test_load_from_hands_the_writer_a_reader_and_never_a_whole_table",
        ),
        finding="P2 — the reader write is where every net megabyte came from: +944 -> +318 MB",
    ),
    Mutation(
        id="p2-read-the-whole-source",
        what="the source is materialised instead of streamed",
        path="src/governed_bi/retrieve/vectors.py",
        anchor="            for batch in source._batches():",
        replacement="            for batch in source.to_arrow().to_batches():",
        tests=(
            "tests/retrieve/test_vector_store.py::"
            "test_load_from_hands_the_writer_a_reader_and_never_a_whole_table",
        ),
        finding="P2 — worth peak rather than net: +840 MB against +566 MB",
    ),
    Mutation(
        id="p2-row-count-from-the-caller",
        what="the store believes the caller's row count instead of the table's",
        path="src/governed_bi/retrieve/vectors.py",
        anchor="        written = self._table.count_rows()",
        replacement="        written = count",
        tests=(
            "tests/retrieve/test_vector_store.py::"
            "test_the_row_count_comes_from_the_table_and_not_from_the_caller",
        ),
        finding="a reader yielding nothing left len(store) at 5 against a table of 0, and "
                "`search`'s limit = self._rows then returned a subset. Caught in review.",
    ),
    Mutation(
        id="p2-mispair-key-and-vector",
        what="every asset receives another asset's vector",
        path="src/governed_bi/retrieve/vectors.py",
        anchor="                        batch.column(_VECTOR_COLUMN).take(pa.array(take, type=pa.int64())),",
        replacement="                        batch.column(_VECTOR_COLUMN).take(\n"
                    "                            pa.array(list(reversed(take)), type=pa.int64())\n"
                    "                        ),",
        tests=("tests/retrieve/test_vector_store.py::test_every_asset_gets_its_own_vector",),
        finding="the rewrite is about re-keying and its own three tests did not check the pairing",
    ),
    Mutation(
        id="m2-absent-count-passes-as-clean",
        what="an unwritten count is substituted with zero, which the gate reads as a pass",
        path="src/governed_bi/eval/harness.py",
        anchor="    guardrail_errors = _int_or_absent(record.get(\"guardrail_errors\"))",
        replacement='    guardrail_errors = int(record.get("guardrail_errors") or 0)',
        tests=(
            "tests/eval/test_grading_contract.py::"
            "test_an_unwritten_count_does_not_pass_the_gate_as_a_clean_zero",
        ),
        finding="M2 — a record with guardrail_errors never written made all seven gates pass",
    ),
    Mutation(
        id="m2-absent-degradation-reads-clean",
        what="stamp's deliberate None for facet_degraded is turned back into False",
        path="src/governed_bi/eval/harness.py",
        anchor=(
            '        "facet_degraded": (\n'
            '            None if record.get("facet_degraded") is None'
        ),
        replacement=(
            '        "facet_degraded": (\n'
            '            False if record.get("facet_degraded") is None'
        ),
        tests=(
            "tests/eval/test_grading_contract.py::"
            "test_an_unwritten_count_does_not_pass_the_gate_as_a_clean_zero",
        ),
        finding="M2 — the C5 fix and its defeat shipped in the same repository",
    ),
    Mutation(
        id="e1-coverage-counts-compound-parts",
        what="coverage splits compounds, so a corpus holding the parts looks like it has the whole",
        path="src/governed_bi/retrieve/lexical.py",
        anchor="        terms = {m.lower() for m in _TOKEN.findall(query)} - _STOPWORDS",
        replacement="        terms = set(_tokenize(query)) - _STOPWORDS",
        tests=("tests/retrieve/test_tokenizer.py::test_coverage_counts_a_compound_as_one_term",),
        finding="I2's split leaked into coverage: 0.0 -> 0.667 for a compound not in the corpus",
    ),
    Mutation(
        id="e2-stopwords-eat-content-words",
        what="may, am, no, can and will go back into the stopword list",
        path="src/governed_bi/retrieve/lexical.py",
        anchor="    there here could might must shall should would",
        replacement="    there here can could may might must shall should will would am no",
        tests=("tests/retrieve/test_tokenizer.py",),
        finding="a question about a month the corpus lacks scored coverage 1.0",
    ),
    Mutation(
        id="e3-length-counts-index-terms",
        what="document length counts the expanded token list again",
        path="src/governed_bi/retrieve/lexical.py",
        anchor="            self._dl.append(len(_TOKEN.findall(text)))",
        replacement="            self._dl.append(len(tokens))",
        tests=(
            "tests/retrieve/test_tokenizer.py::"
            "test_document_length_counts_words_not_index_terms",
        ),
        finding="identifier-dense summaries were taxed by the change meant to reach them",
    ),
    Mutation(
        id="i10-weights-read-at-import",
        what="the fusion weights come from the register instead of from the turn",
        path="src/governed_bi/serve/runtime.py",
        anchor="    return float(fuse(scores, scale.weights, consulted=consulted))",
        replacement='    return float(fuse(scores, {"lexical": float(knob_default("w_lexical")), '
                    '"semantic": float(knob_default("w_semantic"))}, consulted=consulted))',
        tests=(
            "tests/serve/test_channel_scale.py::"
            "test_a_run_can_move_the_fusion_knobs_and_the_score_follows",
        ),
        finding="I10 — a run could publish w_semantic: 0.9, move its config hash, and behave "
                "identically to the default",
    ),
    Mutation(
        id="i10-ceiling-read-at-import",
        what="the semantic ceiling comes from the register instead of from the turn",
        path="src/governed_bi/serve/runtime.py",
        anchor="            float(semantic), ceiling=scale.semantic_ceiling",
        replacement='            float(semantic), ceiling=float(knob_default("semantic_scale_ceiling"))',
        tests=(
            "tests/serve/test_channel_scale.py::"
            "test_a_run_can_move_the_fusion_knobs_and_the_score_follows",
        ),
        finding="I10 — the third of the three, and the one added by this audit",
    ),
    Mutation(
        id="d9-replicate-check-deleted",
        what="two arms with an identical declared treatment are certified as a comparison",
        path="src/governed_bi/eval/report.py",
        anchor=(
            "    unmoved = sorted(k for k in treatment if values_a[k] == values_b[k])\n"
            "    if unmoved:"
        ),
        replacement=(
            "    unmoved = sorted(k for k in treatment if values_a[k] == values_b[k])\n"
            "    if False:"
        ),
        tests=(
            "tests/eval/test_the_delivery_gate_can_fail.py::"
            "test_two_arms_with_every_knob_identical_are_a_replicate_not_a_comparison",
        ),
        finding="D9's judgement had no mutation and its four artifact-backed controls were green "
                "against the whole treatment half deleted — the real null pair short-circuits on "
                "four absent knobs and never reaches it. Found in review of the fix.",
    ),
    Mutation(
        id="d9-no-treatment-is-a-pass",
        what="a pair with no declared treatment is certified rather than refused",
        path="src/governed_bi/eval/report.py",
        anchor="    if not treatment:\n        return _gate(",
        replacement="    if False:\n        return _gate(",
        tests=(
            "tests/eval/test_the_delivery_gate_can_fail.py::"
            "test_two_arms_with_every_knob_identical_are_a_replicate_not_a_comparison",
        ),
        finding="the other half of the same hole: nothing named a treatment, so nothing was compared",
    ),
    Mutation(
        id="d9-confounder-ignored",
        what="a knob moved outside the declared treatment stops being a confounder",
        path="src/governed_bi/eval/report.py",
        anchor=(
            "    differing = sorted(k for k in confounders if values_a[k] != values_b[k])\n"
            "    if differing:"
        ),
        replacement=(
            "    differing = sorted(k for k in confounders if values_a[k] != values_b[k])\n"
            "    if False:"
        ),
        tests=(
            "tests/eval/test_the_delivery_gate_can_fail.py::"
            "test_one_moved_knob_outside_the_declared_treatment_is_a_confounder",
        ),
        finding="two knobs moved and one declared is not a measurement of the declared one",
    ),
    # ── open-work 3.9: the eight instrument tests that could not fail ──────────
    #
    # All eight were one shape: a test asserting a constant equals itself (`assert
    # "corpus_content_hash" in row`, which `None` satisfies). Each was repaired and verified once
    # by hand — the habit this file exists to replace. Caught when declared, 2026-08-11.
    Mutation(
        id="s39-routing-pinned-always-true",
        what="every row claims its shortlist was replayed",
        path="src/governed_bi/eval/harness.py",
        anchor='        "routing_pinned": _routing_was_pinned(question, record),',
        replacement='        "routing_pinned": True,',
        tests=("tests/eval/test_routing_replay.py",),
        finding="3.9 — a constant reads as a fully pinned arm, plausible enough to be believed",
    ),
    Mutation(
        id="s39-routing-pinned-always-false",
        what="every row claims it routed for itself",
        path="src/governed_bi/eval/harness.py",
        anchor='        "routing_pinned": _routing_was_pinned(question, record),',
        replacement='        "routing_pinned": False,',
        tests=("tests/eval/test_routing_replay.py",),
        finding="3.9 — the other constant: an arm that ignored --replay-routing. One direction "
                "asserted is half a test.",
    ),
    Mutation(
        id="s39-row-forgets-its-corpus",
        what="the measurement row stops naming the corpus that produced it",
        path="src/governed_bi/eval/harness.py",
        anchor='        "corpus_content_hash": record.get("corpus_content_hash"),',
        replacement='        "corpus_content_hash": None,',
        tests=("tests/eval/test_the_row_names_its_configuration.py::"
               "test_a_measured_row_names_both_treatment_identities",),
        finding="3.9's named example, and the corpus IS the treatment identity",
    ),
    Mutation(
        id="s39-row-forgets-its-prompt",
        what="the measurement row stops naming the prompt wording that produced it",
        path="src/governed_bi/eval/harness.py",
        anchor='        "prompt_set_hash": record.get("prompt_set_hash"),',
        replacement='        "prompt_set_hash": None,',
        tests=("tests/eval/test_the_row_names_its_configuration.py::"
               "test_a_measured_row_names_both_treatment_identities",),
        finding="3.9 — a prompt A/B whose two artifacts cannot be told apart is not an A/B",
    ),
    Mutation(
        id="s39-attempt-trace-empty",
        what="the row records no per-attempt layer or reason code",
        path="src/governed_bi/eval/harness.py",
        anchor='        "attempts": _attempt_trace(record.get("execution")),',
        replacement='        "attempts": [],',
        tests=("tests/eval/test_eval_contract.py::"
               "test_a_measured_row_says_which_layer_refused_each_attempt",),
        finding="3.9 — an empty trace reads as 'governance rarely refused'",
    ),
    Mutation(
        id="s39-computed-correct-never-measured",
        what="an abstained turn is never priced",
        path="src/governed_bi/eval/harness.py",
        anchor=(
            '        "computed_correct": (\n'
            "            None if computed_fp is None or not gold_fp else computed_fp == "
            "str(gold_fp)\n"
            "        ),"
        ),
        replacement='        "computed_correct": None,',
        tests=("tests/eval/test_eval_contract.py::"
               "test_an_abstained_turn_is_priced_without_being_scored",),
        finding="3.9 — a constant None reads as 'no abstention had a runnable statement', on "
                "which the whole abstention-precision claim in 4.1 rests",
    ),
    Mutation(
        id="s39-eval-row-drops-the-eviction",
        what="the consumer end of the eviction chain reports nothing",
        path="src/governed_bi/eval/harness.py",
        anchor='        "context_evicted": (delivery.get("evicted") '
               "if isinstance(delivery, Mapping) else None),",
        replacement='        "context_evicted": None,',
        tests=("tests/serve/test_context_prefix_is_cacheable.py::"
               "test_the_eval_row_reports_what_was_evicted",),
        finding="3.9 — the only field saying whether a licensed table survived the char budget",
    ),
    Mutation(
        id="s39-assemble-drops-the-eviction",
        what="the producer end of the eviction chain writes nothing",
        path="src/governed_bi/serve/nodes/assemble.py",
        anchor='    if evicted:\n        delivery["evicted"] = evicted\n',
        replacement="",
        tests=("tests/serve/test_context_prefix_is_cacheable.py::"
               "test_assemble_writes_the_eviction_onto_the_delivery_it_returns",),
        finding="3.9 — three lines either neighbour can lose without noticing",
    ),
    Mutation(
        id="s39-stamp-drops-the-eviction",
        what="stamp's key set stops projecting the eviction into the record",
        path="src/governed_bi/serve/nodes/stamp.py",
        anchor='    delivery_keys = {"context_hash", "delivery_hash", "tool_delivered", "evicted"}',
        replacement='    delivery_keys = {"context_hash", "delivery_hash", "tool_delivered"}',
        tests=("tests/serve/test_context_prefix_is_cacheable.py::"
               "test_the_served_record_carries_what_the_budget_evicted",),
        finding="3.9 — one name in one literal away from runs/serve/*.jsonl",
    ),
    # ── open-work 3.6 / 3.7 / 3.13: the instrument's own identity ──────────────
    #
    # Each was a silent failure in the safe-looking direction: a guard returning "fine", a
    # baseline flattering what it measured, a swallowed exception reading as missing data.
    # Declared with the tests, per D16 and D30.
    Mutation(
        id="r1-reconcile-reads-the-knob-mapping",
        what="reconcile looks for corpus_content_hash where it never is",
        path="src/governed_bi/register/arm_profiles.py",
        anchor='    recorded = row.get("corpus_content_hash")',
        replacement='    recorded = (row.get("knobs_resolved") or {}).get("corpus_content_hash")',
        tests=("tests/conformance/test_arm_profiles_are_declared.py",),
        finding="D9 owed — `corpus_content_hash` is a RecordField, never in `knobs_resolved`, so "
                "the lookup always missed and every artifact reconciled. **Re-anchored "
                "2026-08-12**, after a run reported it SURVIVED at `anchor appears 0 times`: "
                "3.13's fix made the digest mandatory and turned the old `is not None` guard "
                "into an early return, so the two-line anchor went stale and proved nothing. The "
                "git-ref half of the original defect went stale with it — `reconcile` can no "
                "longer reach a comparison without a digest — and is pinned instead by "
                "`test_reconcile_compares_the_digest_and_never_the_git_ref`.",
    ),
    Mutation(
        id="r2-a-broken-arms-file-reads-as-no-declaration",
        what="a malformed arms.toml silently un-declares every arm",
        path="src/governed_bi/eval/report.py",
        anchor="    except KeyError:\n        return frozenset()",
        replacement="    except (KeyError, OSError, ValueError):\n        return frozenset()",
        tests=("tests/eval/test_arms_must_share_a_configuration.py",),
        finding="D9 owed — one typo turns every comparison into `cannot_evaluate`, which "
                "reads as a data problem",
    ),
    Mutation(
        id="r3-resume-ignores-the-knobs",
        what="--resume compares only the two hashes, so --out can merge two --top-n arms",
        path="src/governed_bi/eval/provenance.py",
        anchor="    problems.extend(knob_refusals)",
        replacement="    problems.extend([])",
        tests=("tests/eval/test_resume_will_not_merge_two_treatments.py",),
        finding="3.6 — neither hash moves with --top-n, --embed, --reflect or the model id",
    ),
    Mutation(
        id="r4-every-resume-warns-about-clarifications",
        what="a turn that abstained before routing is reported as an unexplained missing hash",
        path="src/governed_bi/eval/provenance.py",
        anchor='    return str(row.get("outcome")) == "clarification" and not (row.get("licensed") or ())',
        replacement="    return False",
        tests=("tests/eval/test_resume_will_not_merge_two_treatments.py",),
        finding="3.6a — fired on every legitimate resume, the shape that teaches a reader to "
                "ignore a warning",
    ),
    Mutation(
        id="r5-drift-baseline-counts-rows-the-pin-skipped",
        what="the residual includes turns that were never pinned",
        path="src/governed_bi/eval/replay.py",
        anchor=(
            "            if not qid or not isinstance(schemas, list) or not schemas:\n"
            "                continue\n"
            '            baseline[str(qid)] = [str(t) for t in (row.get("licensed") or ())]'
        ),
        replacement='            baseline[str(qid)] = [str(t) for t in (row.get("licensed") or ())]',
        tests=("tests/eval/test_routing_replay.py",),
        finding="3.7 — deflated v4's published mean Jaccard 0.7049 -> 0.7020, flattering the pin",
    ),
    Mutation(
        id="r6-a-rerun-appends-a-second-population",
        what="an existing artifact is appended to rather than refused",
        path="src/governed_bi/eval/provenance.py",
        anchor="    if resume or not out_path.exists() or not out_path.stat().st_size:",
        replacement="    if True:",
        tests=("tests/eval/test_resume_will_not_merge_two_treatments.py",),
        finding="3.6 — EX printed over the doubled population; the id check raised afterwards",
    ),
    Mutation(
        id="r7-the-harness-never-notices-a-dirty-tree",
        what="working_tree_dirty is a constant, so the resume-drift gate compares it to itself",
        path="src/governed_bi/eval/provenance.py",
        anchor="        dirty = status is not None",
        replacement="        dirty = False",
        tests=("tests/eval/test_the_row_names_the_harness_that_produced_it.py",),
        finding="3.10 — all four drift keys were null on all 8,106 rows of six arms, so a "
                "resume across an uncommitted edit blended two harness versions silently",
    ),
    Mutation(
        id="i4-coverage-counts-function-words",
        what="coverage credits the corpus for holding the word `the`",
        path="src/governed_bi/retrieve/lexical.py",
        anchor="        terms = {m.lower() for m in _TOKEN.findall(query)} - _STOPWORDS",
        replacement="        terms = {m.lower() for m in _TOKEN.findall(query)}",
        tests=("tests/retrieve/test_tokenizer.py",),
        finding="I4 — an unanswerable question floored at 0.50, so weak_retrieval never fired",
    ),
)
