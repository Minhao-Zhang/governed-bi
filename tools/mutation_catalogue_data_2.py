"""The catalogue's declared mutations, second half.

**Split out of ``tools/mutation_catalogue.py``** once that file reached 984 lines against ADR
0005 §6's hard 1000-line cap (``tools/check_file_length.py``) -- the cap forced the timing, not a
belief that this half deserved its own file on the merits. See
``mutation_catalogue_data_1.py``'s module docstring for why the boundary is a line count and not a
theme, and for where a new entry belongs.
"""

from __future__ import annotations

from mutation_catalogue_types import Mutation

__all__ = ["MUTATIONS_DATA_2"]

MUTATIONS_DATA_2: tuple[Mutation, ...] = (
    Mutation(
        id="m2-absent-count-passes-as-clean",
        what="an unwritten count is substituted with zero, which the gate reads as a pass",
        path="src/governed_bi/eval/projection.py",
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
        path="src/governed_bi/eval/projection.py",
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
        path="src/governed_bi/eval/projection.py",
        anchor='        "routing_pinned": _routing_was_pinned(question, record),',
        replacement='        "routing_pinned": True,',
        tests=("tests/eval/test_routing_replay.py",),
        finding="3.9 — a constant reads as a fully pinned arm, plausible enough to be believed",
    ),
    Mutation(
        id="s39-routing-pinned-always-false",
        what="every row claims it routed for itself",
        path="src/governed_bi/eval/projection.py",
        anchor='        "routing_pinned": _routing_was_pinned(question, record),',
        replacement='        "routing_pinned": False,',
        tests=("tests/eval/test_routing_replay.py",),
        finding="3.9 — the other constant: an arm that ignored --replay-routing. One direction "
                "asserted is half a test.",
    ),
    Mutation(
        id="s39-row-forgets-its-corpus",
        what="the measurement row stops naming the corpus that produced it",
        path="src/governed_bi/eval/projection.py",
        anchor='        "corpus_content_hash": record.get("corpus_content_hash"),',
        replacement='        "corpus_content_hash": None,',
        tests=("tests/eval/test_the_row_names_its_configuration.py::"
               "test_a_measured_row_names_both_treatment_identities",),
        finding="3.9's named example, and the corpus IS the treatment identity",
    ),
    Mutation(
        id="s39-row-forgets-its-prompt",
        what="the measurement row stops naming the prompt wording that produced it",
        path="src/governed_bi/eval/projection.py",
        anchor='        "prompt_set_hash": record.get("prompt_set_hash"),',
        replacement='        "prompt_set_hash": None,',
        tests=("tests/eval/test_the_row_names_its_configuration.py::"
               "test_a_measured_row_names_both_treatment_identities",),
        finding="3.9 — a prompt A/B whose two artifacts cannot be told apart is not an A/B",
    ),
    Mutation(
        id="s39-attempt-trace-empty",
        what="the row records no per-attempt layer or reason code",
        path="src/governed_bi/eval/projection.py",
        anchor='        "attempts": _attempt_trace(record.get("execution")),',
        replacement='        "attempts": [],',
        tests=("tests/eval/test_eval_contract.py::"
               "test_a_measured_row_says_which_layer_refused_each_attempt",),
        finding="3.9 — an empty trace reads as 'governance rarely refused'",
    ),
    Mutation(
        id="s39-computed-correct-never-measured",
        what="an abstained turn is never priced",
        path="src/governed_bi/eval/projection.py",
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
        path="src/governed_bi/eval/projection.py",
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
        finding="3.9 — one name in one literal away from the turn log ADR 0014 deleted",
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
        anchor=(
            '    return (\n'
            '        str(row.get("outcome")) == "clarification"\n'
            '        and not (row.get("licensed") or ())\n'
            '        and row.get("corpus_content_hash") is None\n'
            '        and row.get("prompt_set_hash") is None\n'
            '    )'
        ),
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
