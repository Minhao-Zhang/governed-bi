"""Everything ``run_datalake_eval.py`` prints after the last question is graded.

The **report** third of the plan / execute / report split the architecture review asks for
(``docs/analysis/architecture-review-2026-08-11.md`` C2). Split out because the driver reached
the 1 000-line hard cap ``tools/check_file_length.py`` enforces, and because this half needs no
database, no model and no corpus: it reads the artifact off disk and prints. ``print_report`` is
handed ``observed_tokens`` and ``table_coverage`` rather than importing them, so the driver still
decides which measurement functions the run was scored with.

Every rate here is over the **whole artifact**, not this process's rows: a resumed run is one
run, and reporting only what this invocation happened to compute would publish a number over a
population that depends on when somebody pressed Ctrl-C.

Rendering that a test also needs lives in ``eval/report.py`` instead -- the refusal histogram is
built there and only printed here, for the reason that file gives.
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))


def _abstention(every: list[dict]) -> None:
    """What declining to answer bought, and what it cost.

    The engine's committed answers are the ones it stood behind; the abstained ones are the
    turns it capped, refused or asked about. Two numbers make that a measurement rather than a
    posture: how accurate the committed set is, and how accurate the abstained set *would have
    been*. If the second is near the first, abstention is noise. If it is far below, the engine
    knows what it does not know, and that is a property no single EX figure can show.

    ``computed_correct`` is the abstained turn's last statement re-executed by the harness and
    never counted as correct. Rows written before that field existed carry ``None`` and are
    reported as unmeasured rather than as zero.
    """
    committed = [r for r in every if r.get("outcome") == "answered"]
    # Wider than the harness's `PRICED_ABSTENTIONS`, on purpose: a clarification is an
    # abstention, it just has no statement to re-execute. Counting it here and not there keeps
    # "how often did the engine decline" separate from "of those, how many could be priced".
    abstained = [r for r in every if r.get("outcome") in ("capped", "refused", "clarification")]
    if not committed or not abstained:
        return
    ok = sum(1 for r in committed if r.get("correct") is True)
    priced = [r for r in abstained if r.get("computed_correct") is not None]
    would = sum(1 for r in priced if r.get("computed_correct") is True)

    print("\nabstention (the engine declined; scoring is unchanged, this only prices it):")
    print(f"  committed        {ok}/{len(committed)} = {ok / len(committed):.3f}   accuracy of delivered answers")
    print(f"  abstained        {len(abstained)} turn(s) = {len(abstained) / len(every):.3f} of the run")
    if not priced:
        print("  would-have-been  unmeasured (no `computed_correct` on these rows)")
        return
    print(
        f"  would have been  {would}/{len(priced)} = {would / len(priced):.3f} correct if forced to commit"
        + (f"   ({len(abstained) - len(priced)} had no runnable statement)" if len(priced) != len(abstained) else "")
    )
    print(
        f"  abstention precision {len(priced) - would}/{len(priced)} = "
        f"{(len(priced) - would) / len(priced):.3f} of priced abstentions would have been wrong"
    )
    print(
        f"  computed EX      {ok + would}/{len(every)} = {(ok + would) / len(every):.3f}"
        "   <- NOT the headline: it credits statements the engine refused to stand behind"
    )


def _refusal_layers(every: list[dict]) -> None:
    """Which governance layer refused, per attempt.

    A refusal reported only as ``refused_by: guardrail`` names the *stage* and not the rule, and
    the two suggest opposite work: ``r_table_not_licensed`` is a retrieval failure the corpus or
    the router owns, while an excluded-column refusal is the policy working as designed. Reading
    the 2026-08-09 run needed every refused statement replayed through ``check()`` offline to
    tell them apart. This prints it.
    """
    from governed_bi.serve.ledger import answering_attempts

    # **`answering_attempts`, not every row.** A `sample_rows` probe is refused by the same
    # layers as a draft answer and lands in the same ledger, so counting the raw list reports
    # introspection as governance declining to answer. What the filter actually removes from
    # *this* histogram, measured on every artifact in `runs/eval/`, is failed probes only:
    # 25 on v3-pinned, 3 on v3-fold, 1 on v4, 1 on v5, 3 on v4-reflect, all
    # `PARSE/r_ambiguous_fold`; run1 and run2 record no ledger at all. The "21 `passed`" this
    # comment used to cite is a real figure over a narrower slice -- v3-fold's *capped* turns
    # hold 24 sample-path attempts, 21 passing and those same 3 refused -- and a histogram of
    # failed attempts never counted the passing ones on any slice. Every other reader of this ledger
    # (`execution_from_attempts`, `stamp`, `agent_core`) already goes through this function, and
    # `serve/ledger.py` says why: three copies of "which attempts count" is three answers. This
    # was the fourth copy, and it disagreed. The row keeps every attempt including `path`, so
    # the filter belongs here in the reader and no artifact loses information.
    codes: collections.Counter = collections.Counter()
    for row in every:
        for attempt in answering_attempts(row.get("attempts") or ()):
            if attempt.get("passed") is True:
                continue
            codes[f"{attempt.get('layer') or '-'}/{attempt.get('reason_code') or '-'}"] += 1
    if not codes:
        return
    print("\nfailed attempts by layer/rule (answering attempts only):")
    for name, n in codes.most_common(12):
        print(f"  {name:<44}{n:>6}")


def _terminal_reasons(every: list[dict]) -> None:
    """Why the *turn* ended, in the ``REFUSED_BY_TO_STAGE`` vocabulary.

    Not a second copy of :func:`_refusal_layers`. That one counts **ledger attempts**, so it can
    only ever see turns that wrote SQL — and a turn the abstention policy withholds writes no
    ledger row at all (ADR 0013's own acceptance criterion), so the four abstention reasons are
    structurally invisible to it. The lines are built in ``eval/report.py`` beside the
    histogram, which is where ADR 0013 §2 says the reader lives.
    """
    from governed_bi.eval.report import refusal_histogram, refusal_report_lines

    for line in refusal_report_lines(refusal_histogram(every)):
        print(line)


def _pin_report(every: list[dict], replay_routing: pathlib.Path) -> list[str]:
    """How much of the arm actually ran on the pinned shortlist, and what still moved.

    Both halves come out of ``eval/replay.py``: the drift through :func:`drift_against`, which
    is the *same* function any unpinned reference figure must go through, and the pin counts
    through :func:`pin_realised`, which reads an old-semantics artifact correctly.

    The one-liner this replaces -- ``sum(r["routing_pinned"] is True)`` -- returns 1 345 on v4,
    v5 and v4-reflect, because those rows were written when the field recorded the driver's
    intent rather than the turn's outcome. The corrected counts (1 342 / 1 340 / 1 333) had been
    published with no producer; ``pin_realised`` is the producer and prints both readings side
    by side so the difference is visible instead of asserted.
    """
    from governed_bi.eval.replay import drift_against, pin_realised, routing_from_artifact

    pinned = routing_from_artifact(replay_routing)
    drift = drift_against(replay_routing, every)
    counts = pin_realised(every, pinned)
    rate = drift["identical_rate"]
    jaccard = drift["mean_jaccard_when_moved"]
    lines = [
        "routing pinned; residual drift in the licensed table set:",
        f"  shortlist replayed {counts['realised']}/{len(every)}"
        f"   (the artifact offered {len(pinned)} pinnable questions)",
        f"  flagged routing_pinned {counts['flagged']}"
        f"   ({counts['flagged'] - counts['realised']} of those recorded no shortlist at all --"
        " a turn that ended before route_node. Rows written under the old intent semantics"
        " carry the flag anyway, so the realised count above is the one to read)",
        f"  shortlist matches the pin exactly {counts['exact']}"
        f"   ({counts['same_set_out_of_order']} hold the same schemas in a different order)",
        f"  identical      {'unmeasured' if rate is None else f'{rate:.4f}'}"
        f"   {drift['identical']}/{drift['compared']}",
        f"  moved          {drift['moved']}"
        + (f"   mean Jaccard {jaccard:.4f} over those" if jaccard is not None else ""),
        f"  not in the pinned baseline {drift['not_in_baseline']}",
        "  mean Jaccard is over the movers only. The mean over every compared row is a"
        " different statistic",
        "  and must not be differenced against this one -- see replay.drift_against.",
        "  Pass two re-searches inside the pinned schemas, so this is expected to be non-zero.",
        "  It is printed because an unquantified drift turns 'we pinned routing' into a wider"
        " claim than what was done.",
    ]
    return lines


def print_report(
    rows: list[dict],
    out_path: pathlib.Path,
    args,
    observed_tokens,
    table_coverage,
    *,
    profile=None,
) -> None:
    """Print the whole file, not just this process's rows — a resumed run is one run."""
    every = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"\nrows in {out_path}: {len(every)}")
    if profile is not None:
        from governed_bi.eval.provenance import reconciliation_lines

        print("\n" + "\n".join(reconciliation_lines(every, profile)))

    # `correct` has three values, and reading it with `r.get("correct")` counted a row the grader
    # could not judge as a wrong answer. Split first; every rate below is over graded rows only.
    graded = [r for r in every if r.get("correct") is not None]
    unmeasured = [r for r in every if r.get("correct") is None]
    if unmeasured:
        print(
            f"UNMEASURED        = {len(unmeasured)} row(s) the grader could not judge, "
            "excluded from every EX below (not counted as wrong): "
            + str(dict(collections.Counter(str(r.get("grade_detail")) for r in unmeasured)))
        )

    def ex(rows: list[dict], label: str, note: str = "") -> None:
        ok = sum(1 for r in rows if r.get("correct"))
        print(f"{label:<18}= {ok}/{len(rows)} = {ok / max(1, len(rows)):.3f}{note}")

    ex(graded, "EX")
    ex([r for r in graded if r.get("outcome") != "clarification"], "EX over attempted")

    # The population, stated rather than quietly changed. The dataset says "Exclude both from
    # cross-variant EX": `order_sensitive` golds return a different-but-valid result on the decoy
    # instances, and `exec_failed` golds are degenerate BIRD (>200k rows / 60s timeout) that score
    # `missing_gold` against any engine. Leakage is its third warning. All printed, never applied
    # silently — dropping rows shrinks a denominator with nothing in the artifact saying so.
    from governed_bi.eval.datalake import dataset_leakage_qids, dataset_qid_lists

    lists = dataset_qid_lists(args.dataset)
    leaked = dataset_leakage_qids(args.dataset)
    excluded = lists["order_sensitive"] | lists["exec_failed"] | leaked
    present = {str(r.get("question_id")) for r in graded}
    stable = [r for r in graded if str(r.get("question_id")) not in excluded]
    if len(stable) != len(graded):
        ex(
            stable,
            "EX over clean",
            f"   (excludes {len(graded) - len(stable)}: "
            f"{len(lists['order_sensitive'] & present)} order-sensitive, "
            f"{len(lists['exec_failed'] & present)} exec-failed gold, "
            f"{len(leaked & present)} split-leaked)",
        )
    print("outcomes:", dict(collections.Counter(str(r.get("outcome")) for r in every)))
    crashed = [r for r in every if r.get("outcome") == "crashed"]
    if crashed:
        print("crashes:", dict(collections.Counter(str(r.get("error_type")) for r in crashed)))

    # The EX ceiling first, because it decides how to read everything below it: a question whose
    # gold tables were never licensed could not have been answered by any model.
    cov = table_coverage(every, _gold_sql_by_qid(args.dataset))
    print(
        f"all gold tables licensed = {cov['all_gold_tables_licensed']:.3f}  "
        f"(some {cov['some_licensed']:.3f}, none {cov['none_licensed']:.3f}, "
        f"unparsed gold {cov['gold_sql_unparsed']})"
    )

    _abstention(every)
    _terminal_reasons(every)
    _refusal_layers(every)
    if getattr(args, "replay_routing", None) is not None:
        print("\n" + "\n".join(_pin_report(every, args.replay_routing)))

    # The funnel, before the flat rates below. Each stage is conditional on the one above, so a
    # drop is attributable: `all_gold_tables_licensed` over correctly-routed questions is a
    # table-selection number; over every question it blends two failures wanting opposite work.
    from governed_bi.eval.datalake import retrieval_funnel

    funnel = retrieval_funnel(every, _gold_sql_by_qid(args.dataset), _gold_db_by_qid(args.dataset))

    def _rate(cell: dict) -> str:
        """A rate, or the word for its absence. Never ``0.0000`` for an empty population."""
        return "unmeasured" if cell["rate"] is None else f"{cell['rate']:.4f}"

    print("\nfunnel (each stage given the one above):")
    for stage, cell in funnel["conditional"].items():
        print(f"  {stage:<28}{_rate(cell):>12}   {cell['n']}/{cell['of']}")
    e2e = funnel["end_to_end"]
    print(f"  {'end to end':<28}{_rate(e2e):>12}   {e2e['n']}/{e2e['of']}")
    print(f"  counts: {json.dumps(funnel['counts'])}")

    # The gates, on the path that actually produces numbers. `eval/report.py`'s only caller was
    # `eval/__main__.py` — SQLite-only, unable to run the live datalake arm — so this driver,
    # `routing_recall.py` and `query_summary_alignment.py` produced every quoted figure without
    # reaching a single quotability gate. Printed rather than enforced: a driver that refused to
    # report a run would lose the run.
    from governed_bi.eval.report import evaluate_arm
    from governed_bi.measure.population import Population

    verdicts = evaluate_arm(Population.of(f"live_{args.model}", every))
    # Not "cross-arm distinctness needs a second arm": audit D9 retired distinctness as a
    # treatment test, so a second arm would not buy it. What a second arm buys is
    # `eval/report.knobs_comparable`, which judges the treatment from the declared knobs.
    print("\nquotability gates (single-arm; the cross-arm judgement needs a second arm):")
    for verdict in verdicts:
        print(f"  {verdict.render()}")
    blocking = [v for v in verdicts if v.verdict.value != "pass"]
    print(
        "  ALL GATES PASS -- these numbers are quotable as a single arm"
        if not blocking
        else f"  {len(blocking)} gate(s) did not pass; a check that did not happen is not a "
        "check that passed"
    )

    gold = _gold_db_by_qid(args.dataset)
    reach = [
        r
        for r in every
        if any(str(t).startswith(f"{gold.get(r['question_id'], chr(0))}.") for t in (r.get("licensed") or []))
    ]
    # Reachability is over every row (it is a routing fact, true or false regardless of grading);
    # the EX beneath it is over the graded ones only.
    reach_graded = [r for r in reach if r.get("correct") is not None]
    ok = [r for r in reach_graded if r["correct"]]
    print(f"gold schema reachable = {len(reach)}/{len(every)} = {len(reach) / max(1, len(every)):.3f}")
    print(
        f"EX among reachable    = {len(ok)}/{len(reach_graded)} = "
        f"{len(ok) / max(1, len(reach_graded)):.3f}"
    )
    clar_reach = sum(1 for r in reach if r.get("outcome") == "clarification")
    unreach = [r for r in every if r not in reach]
    print(
        f"clarification: {clar_reach}/{len(reach)} when reachable, "
        f"{sum(1 for r in unreach if r.get('outcome') == 'clarification')}/{len(unreach)} when not"
    )
    print("tokens:", json.dumps(observed_tokens(every), indent=2, default=str))


def _gold_sql_by_qid(dataset: pathlib.Path) -> dict[str, str]:
    """``question_id -> sql_rename``. The statement written against the obfuscated schemas,
    which is what this database is; ``sql_base`` and ``sql_sqlite`` do not execute here."""
    out: dict[str, str] = {}
    for line in (dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if row.get("sql_rename"):
                out[str(row["question_id"])] = str(row["sql_rename"])
    return out


def _gold_db_by_qid(dataset: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (dataset / "test_final.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            out[str(row["question_id"])] = str(row["db_id"])
    return out
