"""Mutation testing for the invariants this repository cannot afford to lose.

**Why this exists.** ``AGENTS.md`` requires that a test guarding a defect be *mutation-verified* —
break the behaviour, watch the test fail, restore — and until 2026-08-10 that was a habit rather
than a mechanism. The habit failed: ``tests/govern`` (1,705 lines, owner of the layer stack,
carrying ADR 0006's B1–B10 bypass contract) could not detect a **total governance bypass**.
Setting ``pipeline.py``'s ``if not verdict["passed"]`` to ``if False:`` made ``prepare()`` hand back
``'SELECT token FROM secrets LIMIT 200001'`` for a refused verdict, and 133/133 tests passed.

A habit does not scale and does not survive the person who has it. This does: each entry below is
a mutation someone verified by hand once, written down so it is verified on every run.

**What a run proves.** For each mutation: the anchor still exists (so the entry has not silently
gone stale against a refactor), the mutated tree makes **at least one named test fail**, and the
tree is restored. What it does *not* prove is that the tests are otherwise good — a mutation the
suite catches says nothing about the mutations nobody wrote down.

**Not a ``check_*`` gate.** ``tests/conformance/test_register_closure.py`` requires every
``tools/check_*.py`` to be declared CI or declared manual; this is deliberately not named that
way, because it is slow (it runs a pytest selection per mutation) and belongs on a nightly or a
pre-release run rather than on every push.

Usage::

    uv run --frozen python tools/mutate.py            # every declared mutation
    uv run --frozen python tools/mutate.py --list     # names only, runs nothing
    uv run --frozen python tools/mutate.py --only c1  # one, by id substring

**Safety.** The target file is read into memory, written, and restored in a ``finally``, and the
restore is verified byte-for-byte before the next mutation runs. It does not use
``git checkout --``: ``AGENTS.md`` records that doing so has silently discarded uncommitted work
in the same file more than once, and this tool must be safe to run on a dirty tree.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


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
        id="a4-run-creation-writes-state",
        what="a new run may carry command.update, forging licensed and the corpus hash",
        path="src/governed_bi/api/auth.py",
        anchor="    offending = sorted(k for k in _STATE_WRITING_COMMANDS if command.get(k) is not None)",
        replacement="    offending: list[str] = []",
        tests=(
            "tests/api/test_the_custom_routes_require_a_key.py::"
            "test_a_new_run_may_not_carry_a_state_writing_command",
        ),
        finding="A4 — A2/A3 through the door closing them left open; `map_command` writes every "
                "key it is handed with no reference to the graph's input schema",
    ),
    Mutation(
        id="a4-resume-refused-too",
        what="the paused-turn protocol is broken by a blanket deny",
        path="src/governed_bi/api/auth.py",
        anchor='_STATE_WRITING_COMMANDS = ("update", "goto")',
        replacement='_STATE_WRITING_COMMANDS = ("update", "goto", "resume")',
        tests=(
            "tests/api/test_the_custom_routes_require_a_key.py::"
            "test_a_new_run_may_not_carry_a_state_writing_command",
        ),
        finding="a blanket deny looks like the fix and removes the feature: `ask_user` interrupts "
                "and the client answers with `command.resume`",
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
        id="i4-coverage-counts-function-words",
        what="coverage credits the corpus for holding the word `the`",
        path="src/governed_bi/retrieve/lexical.py",
        anchor="        terms = {m.lower() for m in _TOKEN.findall(query)} - _STOPWORDS",
        replacement="        terms = {m.lower() for m in _TOKEN.findall(query)}",
        tests=("tests/retrieve/test_tokenizer.py",),
        finding="I4 — an unanswerable question floored at 0.50, so weak_retrieval never fired",
    ),
)


def _run_tests(selection: tuple[str, ...]) -> tuple[bool, str]:
    """``(any test failed, last line of output)``.

    A non-zero exit is the signal, and *any* non-zero counts: a collection error caused by the
    mutation is still the suite noticing. What must not happen is exit 0.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *selection, "-q", "-x", "--no-header", "-p", "no:randomly"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return proc.returncode != 0, (tail[-1] if tail else f"exit {proc.returncode}")


def _apply(mutation: Mutation) -> tuple[bool, str]:
    """Run one mutation. Returns ``(survived, detail)`` — ``survived`` meaning **bad**."""
    target = REPO / mutation.path
    original = target.read_text(encoding="utf-8")

    count = original.count(mutation.anchor)
    if count != 1:
        return True, (
            f"anchor appears {count} times, expected exactly 1 — the entry is stale against "
            "the current file and this run proved nothing"
        )

    try:
        target.write_text(original.replace(mutation.anchor, mutation.replacement, 1), encoding="utf-8")
        caught, tail = _run_tests(mutation.tests)
    finally:
        target.write_text(original, encoding="utf-8")
        if target.read_text(encoding="utf-8") != original:  # pragma: no cover - paranoia
            raise SystemExit(f"FATAL: could not restore {mutation.path}; fix before continuing")

    return (not caught), tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run mutations whose id contains this substring")
    parser.add_argument("--list", action="store_true", help="print the declared mutations")
    args = parser.parse_args()

    chosen = [m for m in MUTATIONS if not args.only or args.only in m.id]
    if not chosen:
        print(f"no mutation id contains {args.only!r}", file=sys.stderr)
        return 2

    if args.list:
        for m in chosen:
            print(f"{m.id:34s} {m.what}\n{'':34s} {m.finding}")
        return 0

    survivors: list[tuple[Mutation, str]] = []
    for m in chosen:
        print(f"[{m.id}] {m.what} ... ", end="", flush=True)
        survived, detail = _apply(m)
        print("SURVIVED" if survived else "caught")
        if survived:
            survivors.append((m, detail))

    print()
    if survivors:
        print(f"{len(survivors)} of {len(chosen)} mutation(s) SURVIVED:\n", file=sys.stderr)
        for m, detail in survivors:
            print(f"  {m.id}: {m.what}", file=sys.stderr)
            print(f"    finding : {m.finding}", file=sys.stderr)
            print(f"    tests   : {' '.join(m.tests)}", file=sys.stderr)
            print(f"    observed: {detail}", file=sys.stderr)
        print(
            "\nA surviving mutation means the named tests pass against the reintroduced defect, "
            "so they report coverage they do not have.",
            file=sys.stderr,
        )
        return 1

    print(f"all {len(chosen)} declared mutation(s) were caught.")
    print(
        "This is coverage of the defects that are written down, and nothing else: a mutation "
        "nobody declared says nothing about the suite."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
