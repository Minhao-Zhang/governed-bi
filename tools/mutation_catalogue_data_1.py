"""The catalogue's declared mutations, first half.

**Split out of ``tools/mutation_catalogue.py``** once that file reached 984 lines against ADR
0005 §6's hard 1000-line cap (``tools/check_file_length.py``) -- the cap forced the timing, not a
belief that this half deserved its own file on the merits. The catalogue itself is written as a
running log, one entry per audit finding in roughly the order each was found, not grouped by
parcel or defect class -- so there is no thematic seam to split along, only a line count. The
boundary here falls between ``p2-mispair-key-and-vector`` and ``m2-absent-count-passes-as-clean``,
which is exactly where the original tuple happened to sit at line 655 of 984. ``mutation_catalogue.py``
concatenates this and ``mutation_catalogue_data_2.py`` back into ``MUTATIONS`` in the original
order, so nothing about iteration order, ``--list``, or ``--only`` changes.

**A new entry appends to whichever of the two data files has the most room under the cap** --
check ``tools/check_file_length.py`` after adding one, and start a third file the same way once
both are full. Read ``tools/mutation_catalogue.py`` for what a run proves and what it does not.
"""

from __future__ import annotations

from mutation_catalogue_types import Mutation

__all__ = ["MUTATIONS_DATA_1"]

MUTATIONS_DATA_1: tuple[Mutation, ...] = (
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
                _attempt_budget(
                    f"run_query error: {type(exc).__name__}: {exc}",
                    attempt_number,
                    book.cap,
                ),
                attempts_by_call={
                    call_id: pipeline_error_attempt("agent", f"{type(exc).__name__}: {exc}")
                },
            )""",
        replacement="""            return _reply(
                runtime,
                _attempt_budget(
                    f"run_query error: {type(exc).__name__}: {exc}",
                    attempt_number,
                    book.cap,
                ),
            )""",
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
                return GUARDRAIL_ERROR, Stage.check.value, None, None, False, terminal
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
    # `a1-custom-routes-open` and `a1-preflight-gated` were here and are **deleted**, not
    # disabled. Both were anchored on `routes.py`'s `_require_api_key` middleware and both named
    # `tests/api/test_the_custom_routes_require_a_key.py`; the middleware and the test file were
    # removed on 2026-08-13 when transport auth was dropped (see `api/auth.py`'s module
    # docstring for why). A mutation entry whose anchor no longer exists fails `tools/mutate.py`
    # as stale, which is the right behaviour and the reason these could not simply be left.
    #
    # Nothing replaces them, and that is the honest state: A1/A7 are open by decision now, so
    # there is no gate left for a mutant to try to slip past. Restoring transport auth means
    # restoring these two entries with it.
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
        anchor="@auth.on.threads.create_run  # type: ignore[arg-type]\n",
        replacement="",
        # Re-anchored 2026-09-18: the decorator line gained a `type: ignore[arg-type]` when the
        # governance spine went under a CI mypy gate (the SDK's `_ActionHandler` protocol types
        # `value` as `RunsCreate`, which is not the shape the runtime passes — see the comment
        # above the decorator). The staleness gate caught the anchor the same hour, which is the
        # second time it has paid for itself on this one entry.
        #
        # Re-pointed 2026-08-25. This named
        # `test_the_handler_is_actually_registered_for_run_creation`, which was renamed to
        # `test_both_handlers_are_actually_registered` when the second action was added, and the
        # entry was not moved with it. Pytest exits 4 on a node id it cannot find, which
        # `mutate.py` read as the suite noticing — so this entry printed `caught` while running
        # nothing. `why_it_proves_nothing` is the guard that stops it recurring.
        tests=(
            "tests/api/test_a_run_cannot_write_state.py::test_both_handlers_are_actually_registered",
        ),
        finding="`_get_handler` returns None on no match and `handle_event` treats that as allow; "
                "deleting this one line left the original A4 test green",
    ),
    Mutation(
        id="access-policy-with-no-reachable-role-starts-anyway",
        what="a policy whose roles nobody holds loads, and every query then refuses silently",
        path="src/governed_bi/api/graph_app.py",
        anchor="    if declared and not (set(declared) & set(principal.roles)):",
        replacement="    if False:",
        tests=(
            "tests/conformance/test_a_declared_mechanism_is_reachable_from_the_shipped_config"
            ".py::test_an_access_policy_no_principal_can_hold_is_refused_at_startup",
        ),
        finding="`StaticRoleAccessPolicy.grant_for` returns `Grant()` — reach=listed, no "
                "tables — for a principal holding none of the declared roles, and "
                "`authenticated_principal` returns one constant with one role. So an "
                "operator who writes `[role.analyst]` gets a server that starts, looks "
                "configured, and refuses every query with nothing anywhere naming the "
                "mismatch. Fail-closed and silent, which is the pair that wastes a day.",
    ),
    Mutation(
        id="report-a-non-quotable-delta-reads-as-a-result",
        what="the rendered comparison drops its quotability, so a copied line loses the gate",
        path="src/governed_bi/eval/report.py",
        anchor='    return line if quotable else f"{line}  [NOT QUOTABLE: a gate did not pass]"',
        replacement="    return line",
        tests=(
            "tests/eval/test_a_non_quotable_delta_says_so_in_the_line.py::"
            "test_a_non_quotable_delta_carries_the_verdict_in_the_string",
        ),
        finding="Every real pair in runs/eval/ is non-quotable — `knobs_comparable` returns "
                "`cannot_evaluate` because ~50 of the 51 comparability knobs are absent from "
                "those rows — so every line this has ever rendered was a diagnostic that read "
                "exactly like a result. `quotable` sits one key away in the same dict and does "
                "not travel with a copied line.",
    ),
    Mutation(
        id="provenance-hides-a-pinned-shortlist",
        what="an arm that replayed another arm's routing stops saying so",
        path="src/governed_bi/eval/provenance.py",
        anchor='    pinned = sum(1 for row in rows if row.get("routing_pinned"))',
        replacement="    pinned = 0",
        tests=(
            "tests/eval/test_the_measured_graph_and_the_served_graph.py",
        ),
        finding="`eval/replay.py` writes `pinned_schemas` and `route_retrieve.py` honours it "
                "by replacing the score-ranked shortlist outright, so a pinned arm measures "
                "generation against a router that did not run. v4, v4_reflect and v5 each "
                "carry it on 1,345 of 1,351 rows and README's three caveats do not mention "
                "it. Not a contradiction of arms.toml, which is why it is a note and not a "
                "problem — and why nothing reported it before.",
    ),
    Mutation(
        id="resolve-closure-unbounded-again",
        what="the reference closure loses its ceiling, so a corpus edge widens `licensed` freely",
        path="src/governed_bi/serve/nodes/route_retrieve.py",
        anchor="    if len(added) > max_added:",
        replacement="    if False:",
        tests=(
            "tests/serve/test_the_reference_closure_is_bounded.py::"
            "test_a_closure_past_the_bound_declines_rather_than_truncating",
        ),
        finding="Every table the closure reaches enters `licensed`, which is what the TABLES "
                "layer accepts, so one `references` edge widened what SQL the guard approves "
                "with no access-policy change. Measured on the shipped corpus before a bound "
                "was chosen: a realistic seed adds median 52-69 and max 279, but every table "
                "of the three largest schemas adds 1,480 — 11% of the corpus. The only thing "
                "in front of that today is the `table` budget of 8, whose placement "
                "`govern/bounds.py` already records as costing a wrong refusal.",
    ),
    Mutation(
        id="clarification-flag-goes-back-to-last-write",
        what="the fail-closed flag loses its reducer, so two answers abort the turn",
        path="src/governed_bi/serve/agent_state.py",
        anchor="    clarification_requested: Annotated[bool, any_fail_closed]",
        replacement="    clarification_requested: bool",
        tests=(
            "tests/serve/test_two_clarifications_do_not_destroy_the_turn.py::"
            "test_two_writers_in_one_super_step_commit_instead_of_aborting",
        ),
        finding="The only channel on GovernedAgentState without a reducer, and `keep_newest`'s "
                "docstring three declarations above already recorded what that costs for "
                "`result_table`. Two `ask_user` calls in one assistant message reach it on "
                "resume; `agent_core._run` swallows the InvalidUpdateError into "
                "`path_kind=crashed`, so both human answers are discarded and `_sealed` tells "
                "each call 'Not executed: the turn ended before this tool ran' when both ran "
                "and a person answered both.",
    ),
    Mutation(
        id="resume-unreadable-payload-becomes-an-answer",
        what="a payload parse_resume cannot read is reported as the analyst's answer again",
        path="src/governed_bi/serve/clarification.py",
        anchor=(
            "    if isinstance(resume, str) and resume.strip():\n"
            "        return resume, RESOLUTION_ANSWERED, False\n"
            "    return MALFORMED_CLOSED_TEXT, RESOLUTION_MALFORMED, True"
        ),
        replacement="    return str(resume), RESOLUTION_ANSWERED, False",
        tests=(
            "tests/serve/test_an_unreadable_resume_does_not_become_an_answer.py",
        ),
        finding="`{}` told the model the answer was the empty string, `None` told it the "
                "answer was the literal 'None', and each was stamped resolution='answered' in "
                "the record beside the real ones — then the turn went on to run_query on the "
                "strength of a reply nobody gave. The `declined` branch in the same function "
                "was already fail-closed, so the two halves disagreed about what an "
                "uninterpretable payload means.",
    ),
    Mutation(
        id="feedback-store-opens-an-older-file-as-current",
        what="the upgrade path is skipped, so an older store opens clean and is missing columns",
        path="src/governed_bi/feedback/rows.py",
        anchor="    steps = range(found + 1, SCHEMA_VERSION + 1)",
        replacement="    return\n    steps = range(found + 1, SCHEMA_VERSION + 1)",
        tests=(
            "tests/feedback/test_the_feedback_store_is_not_a_comparability_knob.py::"
            "test_an_older_store_is_upgraded_rather_than_opened_as_if_current",
        ),
        finding="The shipped state until 2026-09-18. `executescript(SCHEMA)` is all CREATE "
                "TABLE IF NOT EXISTS, so on an existing file it adds no column and rewrites "
                "no version — a store at 1 opened by code that knows 2 reported itself "
                "up-to-date and raised OperationalError at the first request touching the new "
                "field. The newer-store direction *was* handled and tested, which is what made "
                "the gap hard to see.",
    ),
    Mutation(
        id="corpus-writes-platform-line-endings",
        what="the corpus hash goes back to depending on which OS wrote the file",
        path="src/governed_bi/corpus/store.py",
        anchor='        encoding="utf-8",\n        newline="\\n",\n    )\n    return target',
        replacement='        encoding="utf-8",\n    )\n    return target',
        tests=(
            "tests/corpus/test_the_hash_does_not_depend_on_the_operating_system.py",
        ),
        finding="`corpus/hash.py` digests read_bytes(), so a `write_text` with no `newline=` "
                "makes `corpus_content_hash` OS-dependent — and every arm digest in "
                "`register/arms.toml` is keyed on it. `../BIRD-corpus/.gitattributes` defends "
                "the checkout against exactly this with a nine-line header about it; this line "
                "defeated that defence from the write side. Passes on Linux CI either way, "
                "which is why the test asserts on bytes rather than on os.linesep.",
    ),
    Mutation(
        id="pending-union-pages-one-half-only",
        what="the merged slice goes back to taking the head, so page two repeats page one",
        path="src/governed_bi/api/feedback_routes.py",
        # The *slice*, not the one-sided read. Reverting only `store.queue`'s `limit` leaves
        # the merge correct and the mutation survives — which is worth knowing: the defect
        # needed both halves, and the slice is the half that produces the duplication.
        anchor="        visible = rows[offset : offset + limit]",
        replacement="        visible = rows[:limit]",
        tests=(
            "tests/api/test_the_pending_queue_unions_two_stores.py::"
            "test_the_second_page_does_not_repeat_the_first",
        ),
        finding="The shipped state until 2026-09-18, and the union had no test at all — the "
                "sibling file's comment pointed at a `test_the_pending_queue_unions_two_stores"
                ".py` that did not exist. `offset` was pushed into one of two stores, which "
                "cannot be a partial fix because neither store knows the other's rows. "
                "Measured on 60+60 interleaved rows at limit=50: three pages served 150 rows "
                "for 120, 50 duplicated and 45 unreachable, while `meta.offset` echoed the "
                "request so nothing on the wire said so.",
    ),
    Mutation(
        id="wrap-the-turn-clock-never-restarts",
        what="the per-turn reset is dropped again, so turn two keeps turn one's clock",
        path="src/governed_bi/serve/wrap.py",
        anchor=(
            '        return {} if update["turn_started_at"] is not None '
            'else {"turn_started_at": entered_at}'
        ),
        replacement="        return {}",
        tests=(
            "tests/serve/test_the_turn_clock_restarts_every_turn.py::"
            "test_a_second_turn_does_not_inherit_the_first_turns_clock",
        ),
        finding="The shipped state until 2026-09-18, and invisible to the whole suite: "
                "`test_state_channels.py` asserted `turn_started_at` is in `PER_TURN_RESET` "
                "and explained in a comment why it must be, while `_without_cleared_clock` "
                "stripped that exact key out of every node update. Membership of a dict is "
                "not the dict reaching the channel. Turn n reported the conversation's wall "
                "clock, and `_LIST_VIEW_KEYS` projects `latency_sec` to the audit list, so "
                "the number was served as well as stored. The eval graph has no `accept` "
                "node, so no measured arm could observe it.",
    ),
    Mutation(
        id="access-denied-columns-accepts-a-schemaless-key",
        what="a two-part denied_columns key loads again, and denies nothing",
        path="src/governed_bi/govern/access.py",
        anchor='                body.get("denied_columns"), parts=(3,), where=f"{where}.denied_columns"',
        replacement='                body.get("denied_columns"), parts=(2, 3), where=f"{where}.denied_columns"',
        tests=(
            "tests/govern/test_access_seam.py::"
            "test_the_policy_file_fails_at_load_not_at_query_time",
        ),
        finding="The shipped state until 2026-09-18. `resolve_grant` folds with "
                "`default_schema=None` on the serve path, so `denied_columns = "
                "['orders.salary']` never matched the 3-part key `denies_column` is asked "
                "about: the file parsed, the server started, no warning was issued, and the "
                "column was neither refused by COLUMNS nor withheld from the prompt. The "
                "sibling `tables` typo fails closed and is noticed on the first query; this "
                "one fails open and is noticed never.",
    ),
    Mutation(
        id="guard-served-policy-runs-no-rule",
        what="the served policy goes back to enabling the scope gate and nothing else",
        path="src/governed_bi/api/graph_app.py",
        anchor="        guard_rules_enabled={rule_id: True for rule_id in sorted(KNOWN_GUARD_RULE_IDS)},",
        replacement='        guard_rules_enabled={"g_bi_scope": True},',
        tests=(
            "tests/api/test_the_served_surface_is_not_the_benchmark_arm.py::"
            "test_the_served_policy_blocks_a_textbook_prompt_injection",
        ),
        finding="This was the shipped state until 2026-09-18 and nothing failed. `g_bi_scope` is "
                "not in `GUARD_RULES`, `guard()` iterates that mapping, and an absent id is "
                "silently False — so the served surface ran zero deterministic predicates while "
                "all five were 100% line-covered by a fixture that built its own all-on policy. "
                "The one test that read the shipped mapping pinned it with `==`, so it failed on "
                "the fix and passed on the regression. Named here against the *behavioural* test, "
                "not the configuration one: comparing a dict to a dict is what missed it.",
    ),
    Mutation(
        id="guard-policy-accepts-an-unknown-rule-id",
        what="the key-space check is removed, so a typo'd rule id is silently disabled again",
        path="src/governed_bi/govern/policy.py",
        anchor="        unknown = sorted(set(self.guard_rules_enabled) - KNOWN_GUARD_RULE_IDS)",
        replacement="        unknown: list[str] = []",
        tests=("tests/govern/test_guard_pipeline_ledger.py",),
        finding="The root cause rather than the instance: one mapping consumed by two modules "
                "over two disjoint namespaces, with `.get(rule_id, False)` turning every "
                "unrecognised key into a silent off. Without this check the next `g_bi_scope` "
                "— a retired id, a typo, a rule renamed on one side — is the same outage.",
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
        #
        # Re-pointed 2026-08-25, with `d11` below: both named this node id in
        # `test_register_closure.py`, the file the lint-gate parametrisation was split *out of*.
        # The file still exists, so only resolving the node id finds it — and until it was
        # resolved, pytest's exit 4 read as a catch and neither entry ran.
        tests=(
            "tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py::"
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
            "tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py::"
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
)
