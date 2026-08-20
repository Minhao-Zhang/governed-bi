"""Run the pooled data-lake eval end to end. Crash-safe, resumable, bounded concurrency.

    uv run --frozen python tools/run_datalake_eval.py --workers 2 --effort xhigh --resume

In ``tools/`` rather than a scratchpad because the 1 351-question arm takes hours: it will be
interrupted, resumed, and re-read by someone who did not start it.

Three properties the earlier scratchpad driver lacked, each of which cost a run:

* **Rows are appended as they complete.** A driver that writes at the end is one interruption
  away from having measured nothing.
* **``--resume`` keeps what was measured and *retries what crashed*.** A crashed row is not a
  measurement, so skipping it bakes a permanent hole into the artifact and computes the final
  score over a denominator that silently included it.
* **Concurrency is bounded and declared.** ``--workers`` maps to ``harness.run_arm(workers=...)``,
  which gives each thread its own graph and connector. Default 2: three workers at ``xhigh``
  lost 30 of the first 194 questions to ``RateLimitError`` against a 500 k TPM ceiling, and a
  429 raised inside a node is caught by the graph wrapper and marked ``crashed`` — a lost
  measurement rather than a slow one. ``--max-retries`` (default 8) is the other half.

Never prints the DSN or the API key.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

#: Everything printed after the last question is graded, in its own module: this file reached the
#: 1 000-line hard cap, and the report half needs no database, model or corpus. The plan /
#: execute / report split, cut at report first because it is the third that already had no I/O
#: of its own.
from datalake_report import print_report  # noqa: E402  (needs the path insert above)

#: The corpus, in its own repository as of 2026-08-07 (D13). Derived from this file's location,
#: like ``DEFAULT_DATASET``: a relative sibling path resolves against the process's working
#: directory, and the corpus is what ``corpus_content_hash`` identifies.
DEFAULT_CORPUS = REPO.parent / "BIRD-corpus"
DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--effort",
        default="xhigh",
        help="reasoning effort (none/low/medium/high/xhigh); omit with --effort ''",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "bedrock", "proxy"],
        help="model provider for every surface. 'proxy' routes through the internal proxy "
        "(credentials from AWS Secrets Manager, GOVERNED_BI_PROXY_SECRET names the secret; no "
        "OPENAI_API_KEY needed). 'bedrock' needs the extra: `uv sync --extra bedrock`, plus a "
        "region in GOVERNED_BI_AWS_REGION/AWS_REGION and whatever boto3 resolves for "
        "credentials. It is in the artifact tag because it is an arm, not a detail.",
    )
    parser.add_argument(
        "--utility-provider",
        default=None,
        help="override the provider for the utility surface only (scope gate + facet "
        "rewriters). Defaults to --provider. A cheap rewriter on one gateway beside a large "
        "agent on another is a distinct arm, recorded as llm_utility_provider.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        help="override the provider for the embedder only. Defaults to --provider, and is "
        "recorded as embedding_provider.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="embedding model id. Defaults to the selected provider's own default, which is "
        "not the same string across providers.",
    )
    parser.add_argument(
        "--utility-model",
        default=None,
        help="separate model id for the guard's scope gate and the facet rewriters. Defaults to "
        "--model. Wired on every provider; pair it with --utility-provider to put it on a "
        "different gateway than the agent.",
    )
    parser.add_argument(
        "--utility-effort",
        default=None,
        help="reasoning effort for the utility model. Needs --utility-model.",
    )
    parser.add_argument("--top-n", type=int, default=None, help="override route_top_n")
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="turn on the post-hoc reflector (reflect_enabled). It is an observer -- it writes "
        "a verdict and changes no control flow -- so EX should not move, which is the arm's own "
        "sanity check. Costs one utility-model call per turn. The judge is `reflect_model` if "
        "set, otherwise --utility-model. It is a comparability knob and enters the artifact tag, "
        "because a reflected arm and an unreflected one are two arms.",
    )
    parser.add_argument(
        "--abstain",
        action="store_true",
        help="turn on the declared abstention policy (abstention_policy_enabled, ADR 0013). "
        "Unlike --reflect this is NOT an observer: it decides, before the agent spends a "
        "run_query attempt, whether the turn should be answered, so EX and coverage both move "
        "and the arm is only readable against an unabstained pair. Costs no model call. It is a "
        "comparability knob and enters the artifact tag, because an abstaining arm and a "
        "committing one are two arms -- and a resume that merged them would report the "
        "coverage of one with the accuracy of the other.",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="provider retries per call; 429s are retryable and the SDK default of 2 is not "
        "enough at any concurrency",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="build the index with an embedder. Costs ~420k embedding tokens (about $0.01) "
        "and raises the gold-table-coverage ceiling by an amount whose measurement is "
        "retired. Off by default so the lexical arm stays the reproducible baseline.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="per-request timeout in seconds. Without one a worker can block forever: a "
        "4-worker run stalled completely for 6+ minutes with 44 live threads and no rows, "
        "because every worker was inside a request that never returned or a backoff that "
        "never ended. A timeout turns that into a retry.",
    )
    parser.add_argument("--per-schema", type=int, default=None, help="cap questions per schema")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--prompt-variant",
        action="append",
        default=[],
        metavar="NAME=VARIANT",
        help="select a non-default variant of a registered prompt, e.g. "
        "`--prompt-variant analyst=v3`. Repeatable. The selection moves `prompt_set_hash`, so "
        "the artifact records which wording produced it; an unknown prompt or variant is "
        "refused here rather than falling back to the default three stages later.",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument(
        "--replay-routing",
        type=pathlib.Path,
        default=None,
        help="a prior run's JSONL whose `schemas` shortlist this run reuses instead of routing "
        "for itself. `route` is deterministic, but the five facet rewriters above it are model "
        "calls, so an unpinned A/B cannot tell its own effect from a shortlist that moved. "
        "Pass two still re-searches inside the pinned schemas -- the residual is measured and "
        "printed as licensed drift, not assumed away.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="start over even though --resume found no artifact but sibling artifacts exist. "
        "Without it that aborts, because a changed tag input is a far likelier explanation "
        "than a genuine first run. NON-DESTRUCTIVE: it relaxes an abort on a path where the "
        "output file does not exist, and never removes anything. To discard an artifact that "
        "does exist, see --truncate.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="DESTRUCTIVE. Discard the artifact at --out and start over. This is the only flag "
        "that deletes a measured run, and an arm on this dataset is hours of paid model calls, "
        "so it is separate from --force-fresh (which for a while did this silently) and it "
        "prints the row count it is discarding first. Contradicts --resume.",
    )
    parser.add_argument(
        "--arm",
        default=None,
        help="the arm this run is, by name, from register/arms.toml. Naming it makes the "
        "committed claim about what this arm changed checkable against what the run records: "
        "the declared corpus is reconciled against the session's corpus_content_hash BEFORE "
        "the first paid question, and every row is reconciled again in the report. Unnamed "
        "runs are still allowed and are simply unreconciled -- but a comparison against an "
        "arm with no profile is `cannot_evaluate`, by audit D9.",
    )
    args = parser.parse_args(argv)

    # A split utility model is wired on every provider since `model/provider.py` landed, so
    # the old "proxy only" refusal is gone. What remains refused is a *silent* one: an effort
    # with no model to apply it to would be accepted and dropped, putting an unrecorded
    # treatment in the artifact — the shape of the incident `llm_utility_model` was declared
    # to prevent.
    if args.utility_effort and not args.utility_model:
        parser.error("--utility-effort needs --utility-model; alone it is accepted and ignored")

    # "Keep what was measured" and "throw it away" are opposite instructions, and the file is
    # the same one. Refused rather than resolved in either direction. The decision itself lives
    # in `provenance.flag_conflict`, where a test can reach it without starting the driver.
    from governed_bi.eval.provenance import flag_conflict

    conflict = flag_conflict(resume=args.resume, truncate=args.truncate)
    if conflict:
        parser.error(conflict)

    from governed_bi import credentials

    credentials.load_into_environ()
    from governed_bi.model import provider as provider_mod

    # Asked per surface, because they no longer share a gateway. The proxy answers for itself
    # (it mints a bearer token from a secret it looks up) and Bedrock is asked through boto3's
    # own resolver, since an instance or task role authenticates with no variable set.
    for surface, chosen in (
        ("agent", args.provider),
        ("utility", args.utility_provider or args.provider),
        ("embedding", (args.embedding_provider or args.provider) if args.embed else None),
    ):
        if chosen is None or chosen == "proxy":
            continue
        if not provider_mod.credentials_present(chosen):
            names = " / ".join(provider_mod.credential_names(chosen)) or "none known"
            print(
                f"no {chosen} credential reachable for the {surface} surface ({names})",
                file=sys.stderr,
            )
            return 2
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    # The profile is loaded *before* the models are built, so a typo in --arm or a malformed
    # arms.toml costs nothing. `arm_profile` raises on an unknown name rather than returning an
    # empty treatment, which is the whole point of the file.
    profile = None
    if args.arm:
        from governed_bi.register.arm_profiles import arm_profile

        try:
            profile = arm_profile(args.arm)
        except (KeyError, OSError, ValueError) as err:
            print(f"--arm: {err}", file=sys.stderr)
            return 2

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.arms import live_arm
    from governed_bi.eval.datalake import (
        attach_gold_fingerprints,
        attach_quality_flags,
        dataset_leakage_qids,
        dataset_qid_lists,
        load_questions,
        observed_tokens,
        table_coverage,
    )
    from governed_bi.eval.harness import run_arm
    from governed_bi.eval.provenance import (
        append_refusal,
        arm_startup_refusal,
        harness_knobs,
        resume_identity_problem,
        scope_identity,
        truncation_notice,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve import session as session_mod

    model, utility_model, embedder, vector_cache = _build_models(args)

    # One connector for the session and the graph; each worker gets its own below.
    # `utility_model` is passed only when there is one: `session` writes `llm_utility_model`
    # from the agent model when it is absent, and "shared one model" and "split them" are two
    # treatments that must not resolve to the same knob set.
    session_kwargs: dict = {
        "connector": PostgresConnector(dsn),
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": model,
        "embedder": embedder,
        "vector_cache": vector_cache,
    }
    if utility_model is not None:
        session_kwargs["utility_model"] = utility_model
    if args.prompt_variant:
        from governed_bi.register.prompts import select

        variants = dict(pair.split("=", 1) for pair in args.prompt_variant)
        # `select` raises on an unknown prompt *or* an unknown variant, here rather than three
        # stages later when a node asks for text and silently gets the default.
        select(variants)
        session_kwargs["prompt_variants"] = variants
    session = session_mod.from_corpus_dir(args.corpus_dir, **session_kwargs)
    if args.prompt_variant:
        print(
            f"prompt variants: {session.prompt_variants} -> prompt_set_hash="
            f"{session.prompt_set_hash}",
            flush=True,
        )
    if session.fatal_problems:
        print(f"corpus has {len(session.fatal_problems)} fatal problem(s); refusing", file=sys.stderr)
        for problem in session.fatal_problems:
            print(f"  {problem}", file=sys.stderr)
        return 3
    schemas = sorted({s for s in session.structure.table_schemas.values() if s})

    # **Before the first paid question**, which is the only place this check is worth anything.
    # `reconcile` compares the profile's committed claim against what a run records, and until
    # now its only caller was its own tests -- declared machinery with no wire, which is the
    # defect open-work 3.10 is about, entered deliberately. A run labelled `v4` against a
    # corpus that is not v4's is a mislabelled artifact, and mislabelled artifacts are how a
    # number ends up quoted against the wrong treatment.
    if profile is not None:
        print(f"arm {profile.name}: {profile.description}", flush=True)
        if profile.compare_to:
            print(f"  compares against: {profile.compare_to}", flush=True)
        if profile.notes:
            print(f"  notes: {profile.notes}", flush=True)

    dataset_file = args.dataset / "test_final.jsonl"
    questions = load_questions(
        dataset_file,
        schemas=schemas,
        limit=args.limit,
        per_schema=args.per_schema,
    )
    if questions:
        questions[0].pop("_skipped_uncovered", None)
    # The **whole** population this run covers, taken before --resume narrows `questions` to
    # what is left. `question_subset` must name the same set on the first attempt and the
    # resume, or the scope key would report drift on every resume and mean nothing.
    covered_qids = {str(q["question_id"]) for q in questions}

    # **Both locks, at the first line where both are knowable.** The print above moved apart from
    # this check on 2026-08-20, when the profile gained a second reconcilable field: the corpus
    # digest was already knowable at the print, the question set is not knowable until
    # `covered_qids` exists, and checking each identity in a different place is how one of them
    # ends up unchecked. This is still ahead of everything that costs or destroys anything --
    # `append_refusal` and `--truncate` are below, and the first paid question is far below.
    #
    # Until this line existed the pre-flight passed the corpus alone, so `--arm v4` against a
    # different `--dataset` agreed with the profile on everything compared and was caught at
    # *report* time, over the finished artifact, which is after the money is spent. The two keys
    # go in one mapping with no `knobs_resolved`, which is the shape `recorded_question_subset`
    # falls through for.
    #
    # `scope_identity` is called here and again inside `harness_knobs` below. That is the same
    # producer twice, deliberately, not a second copy of the rule: the pre-flight must compare
    # exactly the value the rows will carry, and the only way to guarantee that is to derive it
    # from the same function on the same three arguments.
    if profile is not None:
        mislabelled = arm_startup_refusal(
            profile,
            {
                "corpus_content_hash": session.corpus_content_hash,
                "question_subset": scope_identity(
                    schemas=schemas,
                    question_ids=covered_qids,
                    dataset_file=dataset_file,
                )["question_subset"],
            },
        )
        if mislabelled:
            print(mislabelled, file=sys.stderr)
            return 5

    # The retrieval channel is in the tag because it is an arm, not a detail: lexical and
    # embedded runs have different coverage ceilings, so a tag that hid which one ran would
    # let two incomparable runs read as replicates. (The measured gap is retired.) The provider
    # is in it for the same reason, and only when it is not the default, so the OpenAI arm's
    # artifact names do not move: one model id served by two gateways is two treatments.
    provider_tag = f"_{args.provider}" if args.provider != "openai" else ""
    # The prompt selection belongs in the tag for exactly the reason the retrieval channel
    # does, and more sharply: an A/B differing *only* in --prompt-variant would otherwise
    # auto-name both arms to one path, and --resume would read the first arm's rows as this
    # one's and skip the questions. Two treatments, one artifact, no error anywhere.
    variant_tag = "".join(
        f"_{pair.replace('=', '')}" for pair in sorted(args.prompt_variant or ())
    )
    # `reflect_enabled` is a comparability knob, so it moves the config hash -- but the resume
    # guard compares the corpus and prompt hashes, not that one, so without a tag segment a
    # reflected arm would resume into an unreflected artifact and the two would be reported as
    # one. Same reason --prompt-variant is here.
    reflect_tag = "_reflect" if args.reflect else ""
    # Same argument as `reflect_tag`, and it bites harder: the abstention policy moves
    # *coverage*, so a resume that merged an abstaining run into a committing artifact would
    # report one arm's delivered set with the other's declines and every selective-accuracy
    # figure over the file would be a blend of two operating points.
    abstain_tag = "_abstain" if args.abstain else ""
    # A pinned arm and an unpinned one are two treatments: v3-fold vs v4 is discordant on 9.3%
    # of questions with the pin and 12.7% without it, which is the difference between an MDE of
    # 2.3pp and 2.7pp (`measure.stats.mde`, n=1351). It was the one treatment input with no tag
    # segment and no readable row, so `--resume` could merge a pinned run into an unpinned one.
    pinned_tag = "_pinned" if args.replay_routing is not None else ""
    tag = (
        f"{args.model}_{args.effort or 'default'}_top{args.top_n or 'default'}"
        f"_{'embed' if args.embed else 'lexical'}"
        f"{provider_tag}{variant_tag}{reflect_tag}{abstain_tag}{pinned_tag}"
    )
    out_path = args.out or pathlib.Path("runs/eval") / f"live_full_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # A second population appended into one artifact -- see `append_refusal` for what that
    # printed before anything raised.
    refusal = append_refusal(out_path, resume=args.resume, truncate=args.truncate)
    if refusal:
        print(refusal, file=sys.stderr)
        return 4
    # The destructive branch, and it says what it is destroying before it does. Both halves of
    # the decision are in `provenance.py` where a test can drive them; what is left here is the
    # print and the write.
    notice = truncation_notice(out_path, resume=args.resume, truncate=args.truncate)
    if notice:
        print(notice, flush=True)
        out_path.write_text("", encoding="utf-8")

    # ── resume: keep what was *measured*, retry what crashed ──────────────────────
    #
    # The file is rewritten with crashed rows dropped and their question ids requeued, because a
    # crashed row is not a measurement and skipping it leaves a permanent hole in the artifact.
    # A resume that finds nothing is usually a renamed artifact, not a first run: adding the
    # retrieval channel to the tag once orphaned a 515-row artifact and restarted a 1 351-question
    # run from scratch. Refusing here costs one flag and saves a multi-hour run.
    if args.resume and not out_path.exists():
        siblings = sorted(
            path
            for path in out_path.parent.glob(f"live_full_{args.model}_*.jsonl")
            if path != out_path
        )
        if siblings and not args.force_fresh:
            print(
                f"--resume found no artifact at {out_path}, but these exist:",
                file=sys.stderr,
            )
            for path in siblings:
                n_rows = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
                print(f"    {path}  ({n_rows} rows)", file=sys.stderr)
            print(
                "A changed tag input (--effort, --top-n, --embed) renames the artifact. Rename "
                "or merge the one you meant, or pass --force-fresh to start over.",
                file=sys.stderr,
            )
            return 4

    # Per-question knob overrides, composed into one dict. Two separate blocks each writing
    # `knobs_resolved` would mean the second silently dropped the first's override, which is the
    # defect `Session.turn` already caused once for `--top-n`.
    #
    # Built here rather than after the resume block, because the resume guard compares the
    # artifact's recorded comparability knobs against the ones this run is about to write.
    knob_overrides: dict[str, Any] = harness_knobs(
        repo=REPO,
        schemas=schemas,
        question_ids=covered_qids,
        dataset_file=dataset_file,
        serve_workers=args.workers,
    )
    if args.top_n is not None:
        knob_overrides["route_top_n"] = args.top_n
    if args.reflect:
        knob_overrides["reflect_enabled"] = True
    if args.abstain:
        knob_overrides["abstention_policy_enabled"] = True
    run_knobs = {**session.knobs_resolved, **knob_overrides}

    done: set[str] = set()
    retrying = 0
    if args.resume and out_path.exists():
        from governed_bi.register.knobs import comparability_keys

        kept_lines: list[str] = []
        kept_rows: list[dict] = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 — a truncated tail is one lost row, not a stop
                continue
            if str(row.get("outcome")) == "crashed":
                retrying += 1
                continue
            kept_lines.append(line)
            kept_rows.append(row)
            done.add(str(row.get("question_id")))
        refusal, warnings = resume_identity_problem(
            kept_rows,
            corpus_content_hash=session.corpus_content_hash,
            prompt_set_hash=session.prompt_set_hash,
            knobs_resolved=run_knobs,
            comparability=comparability_keys(),
            question_ids=covered_qids,
            replay_routing=args.replay_routing is not None,
        )
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if refusal:
            print(f"--resume refused for {out_path}:", file=sys.stderr)
            print(refusal, file=sys.stderr)
            return 4
        if retrying:
            body = "".join(f"{line}\n" for line in kept_lines)
            out_path.write_text(body, encoding="utf-8")
        questions = [q for q in questions if q["question_id"] not in done]

    qid_lists = dataset_qid_lists(args.dataset)
    order_sensitive = qid_lists["order_sensitive"]

    # ── what the dataset already knows, and the harness used to ignore ────────────
    #
    # Both files ship with the dataset and had no reader. `attach_gold_fingerprints` supplies the
    # published digest, without which every oracle-arm row is `correct=None`. `attach_quality_flags`
    # marks the questions the dataset warns about, so the headline can be recomputed under a
    # different exclusion policy without paying for the run twice. Both counts are printed: a
    # wiring that silently attaches nothing looks exactly like one that was never called.
    fingerprints = attach_gold_fingerprints(
        questions, args.dataset, dsn_key="rename_decoy", order_sensitive=order_sensitive
    )
    flags = attach_quality_flags(
        questions,
        leakage=dataset_leakage_qids(args.dataset),
        order_sensitive=order_sensitive,
        exec_failed=qid_lists["exec_failed"],
    )
    print(
        "gold digests: "
        + ", ".join(f"{k}={v}" for k, v in fingerprints.items() if v)
        + "\nflagged by the dataset: "
        + (", ".join(f"{k}={v}" for k, v in flags.items() if v) or "none"),
        flush=True,
    )

    # Before the `--top-n` override below, and deliberately: pinning a shortlist that was
    # produced under a different `route_top_n` is a contradiction, and printing both lets the
    # operator see it. The pin wins if they disagree -- it is the whole point of the flag.
    if args.replay_routing is not None:
        from governed_bi.eval.replay import attach_pinned_routing, routing_from_artifact

        pinned_baseline = routing_from_artifact(args.replay_routing)
        pin_counts = attach_pinned_routing(questions, pinned_baseline)
        print(
            f"routing pinned from {args.replay_routing}: "
            f"{pin_counts['pinned']} pinned, {pin_counts['unpinned']} routed live "
            "(a question the artifact does not cover routes for itself and is counted here, "
            "because an arm labelled pinned always has some fraction that is not)",
            flush=True,
        )

    # Composed above, applied here: `Session.turn` writes the session's own knobs over the
    # turn, and `harness._turn_knobs` prefers the question's mapping, so this is where the
    # harness half of the identity reaches the row.
    for question in questions:
        question["knobs_resolved"] = dict(run_knobs)

    total = len(questions)
    print(
        "harness: "
        + ", ".join(
            f"{k}={run_knobs[k]}"
            for k in (
                "git_sha",
                "working_tree_dirty",
                "serve_workers",
                "split",
                "schemas_under_test",
                "question_subset",
            )
        )
        + "\n"
        f"model={args.model} effort={args.effort or '(default)'} workers={args.workers} "
        f"top_n={args.top_n or '(register default)'}"
        + (
            f"\nreflect=ON, judged by {args.utility_model or args.model} "
            "(observer: writes a verdict, changes no control flow -- EX must not move)"
            if args.reflect
            else ""
        )
        + "\n"
        f"corpus={args.corpus_dir} ({len(session.assets_by_id)} assets, {len(schemas)} schemas, "
        f"{len(session.degradations)} degradations)\n"
        f"questions={total}"
        + (f" (resumed, {len(done)} measured" if done else "")
        + (f", {retrying} crashed rows requeued" if retrying else "")
        + (")" if done else ""),
        flush=True,
    )
    if not total:
        print("nothing to do", flush=True)
        return 0

    handle = out_path.open("a", encoding="utf-8")
    lock = threading.Lock()
    started = time.time()
    seen = {"n": 0}

    def append(_index: int, row: dict) -> None:
        with lock:
            handle.write(json.dumps(row, default=str) + "\n")
            handle.flush()
            seen["n"] += 1
            n = seen["n"]
            if n % 10 == 0 or n == total:
                rate = (time.time() - started) / n
                print(
                    f"  {n}/{total}  {rate:.1f}s/question  "
                    f"eta {(total - n) * rate / 60:.0f}min",
                    flush=True,
                )

    try:
        rows = run_arm(
            questions,
            live_arm(session, name=f"live_{tag}"),
            order_sensitive_qids=frozenset(order_sensitive),
            session=session,
            run_id=f"live-{tag}",
            workers=args.workers,
            connector_factory=lambda: PostgresConnector(dsn),
            on_row=append,
        )
    finally:
        handle.close()

    print_report(rows, out_path, args, observed_tokens, table_coverage, profile=profile)
    return 0




def _build_models(args):
    """``(model, utility_model, embedder, vector_cache)`` for the chosen provider.

    ``openai`` goes through ``init_chat_model`` + ``OpenAIEmbedder``; ``proxy`` through the
    proxy builders in ``governed_bi.model``. Both trees are imported here rather than at module
    scope so the arm that is not selected costs nothing — the internal proxy one needs ``boto3``, which
    this project does not declare.

    ``max_retries`` is not a nicety on either: a 429 inside a node is marked `crashed`, so a
    rate limit is a lost measurement rather than a slow one. A 3-worker run lost 30 of its
    first 194. The SDK default is 2.
    """
    embedder = None
    vector_cache = None
    utility_model = None

    if args.provider == "proxy":
        from governed_bi.model.proxy_gateway import build_chat_model

        model = build_chat_model(
            llm_model=args.model,
            reasoning_effort=args.effort or None,
            max_retries=max(0, int(args.max_retries)),
            request_timeout_s=float(args.timeout),
        )
        if args.utility_model:
            utility_model = build_chat_model(
                llm_model=args.utility_model,
                reasoning_effort=args.utility_effort or None,
                max_retries=max(0, int(args.max_retries)),
                request_timeout_s=float(args.timeout),
            )
        if args.embed:
            from governed_bi.model import provider as provider_mod
            from governed_bi.retrieve.vector_cache import vector_cache_from_environment

            # Honours --embedding-provider even on the proxy arm: the embedder is a separate
            # surface, and pairing a proxy agent with an OpenAI embedder is a real arm.
            embed_provider = args.embedding_provider or "proxy"
            embedder = provider_mod.embedder(
                args.embedding_model or provider_mod.default_embedding_model(embed_provider),
                provider=embed_provider,
            )
            # The requested name only chooses a directory. Each entry inside is keyed on the
            # provider-qualified `embedder.model`, so a proxy-served vector cannot be handed
            # to an OpenAI-served run of the same width.
            vector_cache = vector_cache_from_environment(model=embedder.requested_model)
        return model, utility_model, embedder, vector_cache

    from governed_bi.model import provider as provider_mod

    retries = max(0, int(args.max_retries))
    # tools=True: the agent binds tools, which on OpenAI selects the Responses API. Every
    # provider-specific spelling of effort/timeout/retries lives in model/provider.py, so this
    # driver and api/graph_app.py cannot drift on a comparability knob.
    model = provider_mod.chat_model(
        args.model,
        surface="agent",
        provider=args.provider,
        effort=args.effort or None,
        # Bounded, because unbounded is how a run stalls rather than fails. See --timeout.
        timeout=float(args.timeout),
        max_retries=retries,
        tools=True,
    )
    if args.utility_model:
        utility_model = provider_mod.chat_model(
            args.utility_model,
            surface="utility",
            provider=args.utility_provider or args.provider,
            effort=args.utility_effort or None,
            timeout=float(args.timeout),
            max_retries=retries,
        )
    if args.embed:
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        embed_provider = args.embedding_provider or args.provider
        embedder = provider_mod.embedder(
            args.embedding_model or provider_mod.default_embedding_model(embed_provider),
            provider=embed_provider,
            max_retries=retries,
        )
        # The persisted store, shared with the server. Without it this driver re-embedded every
        # pooled summary (13,304 in ``../BIRD-corpus``, 2026-08-12) on every invocation.
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
    return model, utility_model, embedder, vector_cache


if __name__ == "__main__":
    raise SystemExit(main())
