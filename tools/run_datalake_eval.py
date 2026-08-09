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
import collections
import json
import pathlib
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

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
        "Without it that situation aborts, because it is almost always a changed --tag input "
        "rather than a genuine first run.",
    )
    args = parser.parse_args(argv)

    # A split utility model is wired on every provider since `model/provider.py` landed, so
    # the old "proxy only" refusal is gone. What remains refused is a *silent* one: an effort
    # with no model to apply it to would be accepted and dropped, putting an unrecorded
    # treatment in the artifact — the shape of the incident `llm_utility_model` was declared
    # to prevent.
    if args.utility_effort and not args.utility_model:
        parser.error("--utility-effort needs --utility-model; alone it is accepted and ignored")

    import credentials

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

    questions = load_questions(
        args.dataset / "test_final.jsonl",
        schemas=schemas,
        limit=args.limit,
        per_schema=args.per_schema,
    )
    if questions:
        questions[0].pop("_skipped_uncovered", None)

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
    tag = (
        f"{args.model}_{args.effort or 'default'}_top{args.top_n or 'default'}"
        f"_{'embed' if args.embed else 'lexical'}{provider_tag}{variant_tag}"
    )
    out_path = args.out or pathlib.Path("runs/eval") / f"live_full_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

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

    done: set[str] = set()
    retrying = 0
    if args.resume and out_path.exists():
        kept_lines: list[str] = []
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
            done.add(str(row.get("question_id")))
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

    if args.top_n is not None:
        for question in questions:
            question["knobs_resolved"] = {
                **session.knobs_resolved,
                "route_top_n": args.top_n,
            }

    total = len(questions)
    print(
        f"model={args.model} effort={args.effort or '(default)'} workers={args.workers} "
        f"top_n={args.top_n or '(register default)'}\n"
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

    _report(rows, out_path, args, observed_tokens, table_coverage)
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
        # The persisted store, shared with the server. Without it this driver re-embedded all
        # 13,968 pooled summaries on every invocation — paid tokens, before the first question.
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
    return model, utility_model, embedder, vector_cache


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
    codes: collections.Counter = collections.Counter()
    for row in every:
        for attempt in row.get("attempts") or ():
            if attempt.get("passed") is True:
                continue
            codes[f"{attempt.get('layer') or '-'}/{attempt.get('reason_code') or '-'}"] += 1
    if not codes:
        return
    print("\nfailed attempts by layer/rule:")
    for name, n in codes.most_common(12):
        print(f"  {name:<44}{n:>6}")


def _report(rows: list[dict], out_path: pathlib.Path, args, observed_tokens, table_coverage) -> None:
    """Print the whole file, not just this process's rows — a resumed run is one run."""
    every = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"\nrows in {out_path}: {len(every)}")

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
    _refusal_layers(every)
    if getattr(args, "replay_routing", None) is not None:
        from governed_bi.eval.replay import licensed_drift

        prior = {
            str(k): v
            for k, v in (
                (r.get("question_id"), r.get("licensed") or [])
                for r in (
                    json.loads(line)
                    for line in args.replay_routing.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            )
        }
        drift = licensed_drift(every, prior)
        rate = drift["identical_rate"]
        print(
            "\nrouting pinned; residual drift in the licensed table set:"
            f"\n  identical      {'unmeasured' if rate is None else f'{rate:.4f}'}"
            f"   {drift['identical']}/{drift['compared']}"
            f"\n  moved          {drift['moved']}"
            + (
                f"   mean Jaccard {drift['mean_jaccard_when_moved']:.3f} over those"
                if drift["mean_jaccard_when_moved"] is not None
                else ""
            )
            + "\n  Pass two re-searches inside the pinned schemas, so this is expected to be "
            "non-zero.\n  It is printed because an unquantified drift turns 'we pinned routing' "
            "into a wider claim than what was done."
        )

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
    print("\nquotability gates (single-arm; cross-arm distinctness needs a second arm):")
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


if __name__ == "__main__":
    raise SystemExit(main())
