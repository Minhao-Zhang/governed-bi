"""Run the pooled data-lake eval end to end. Crash-safe, resumable, bounded concurrency.

    uv run --frozen python tools/run_datalake_eval.py --workers 2 --effort xhigh --resume

In ``tools/`` rather than a scratchpad because the 1 351-question config takes hours: it will be
interrupted, resumed, and re-read by someone who did not start it.

Three properties the earlier scratchpad driver lacked, each of which cost a run:

* **Rows are appended as they complete.** A driver that writes at the end is one interruption
  away from having measured nothing.
* **``--resume`` keeps what was measured and *retries what crashed*.** A crashed row is not a
  measurement, so skipping it bakes a permanent hole into the artifact and computes the final
  score over a denominator that silently included it.
* **Concurrency is bounded and declared.** ``--workers`` maps to ``harness.run_config(workers=...)``,
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

#: Report printing lives in ``datalake_report``. It needs no database, model, or corpus.
#: This file is the plan and execute half.
from datalake_report import print_report  # noqa: E402  (needs the path insert above)

#: Sibling checkout (D13), not a path inside this repo. Derived from this file, like
#: ``DEFAULT_DATASET``: a relative path follows the working directory, and the corpus
#: is what ``corpus_content_hash`` identifies.
DEFAULT_CORPUS = REPO.parent / "BIRD-corpus"
DEFAULT_DATASET = REPO.parent / "BIRD-Data-Obfuscation" / "eval_dataset"


def apply_served_model_defaults(args: argparse.Namespace) -> None:
    """Fill ``--model`` and ``--effort`` from the settings module when the flag was omitted.

    An explicit flag wins, including ``--effort ''`` which means no reasoning effort.
    The settings module is the only place a default model id lives.
    """
    from governed_bi.registry.settings import effective_settings

    settings = effective_settings()
    if not args.model:
        args.model = settings["chat_model"]
    if args.effort is None:
        args.effort = settings["llm_reasoning_effort"] or None
    elif args.effort == "":
        args.effort = None
    if not args.embedding_model:
        args.embedding_model = settings["embedding_model"] or None


def apply_served_utility_default(args: argparse.Namespace) -> str | None:
    """Fill ``--utility-model`` and ``--utility-effort`` from the served config when unset.

    Without this, the driver reuses the agent model and measures a different config.
    An explicit flag wins. Returns the setting name filled, or ``None``.
    """
    from governed_bi.registry.settings import effective_settings

    if args.utility_model:
        return None
    settings = effective_settings()
    served = settings["llm_utility_model"] or None
    if not served:
        return None
    args.utility_model = served
    if not args.utility_effort:
        args.utility_effort = settings["llm_utility_reasoning_effort"] or None
    return "llm_utility_model"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--model",
        default=None,
        help="chat model id. Defaults to the settings module (chat_model).",
    )
    parser.add_argument(
        "--effort",
        default=None,
        help="reasoning effort. Defaults to the settings module (llm_reasoning_effort); "
        "--effort '' sends none.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="embedding model id. Defaults to the settings module.",
    )
    parser.add_argument(
        "--against",
        type=pathlib.Path,
        default=None,
        help="a second artifact. The report then includes run-to-run disagreement.",
    )
    parser.add_argument(
        "--utility-model",
        default=None,
        help="separate model id for the guard's scope check and the retrieval-channel rewriters. Defaults to "
        "the settings module, then to --model.",
    )
    parser.add_argument(
        "--utility-effort",
        default=None,
        help="reasoning effort for the utility model. Defaults to "
        "GOVERNED_BI_UTILITY_MODEL_EFFORT when the utility model came from the served config. "
        "Needs a utility model.",
    )
    parser.add_argument("--top-n", type=int, default=None, help="override route_top_n")
    parser.add_argument(
        "--abstain",
        action="store_true",
        help="turn on the declared abstention policy (abstention_policy_enabled, ADR 0013). "
        "This is not an observer: it decides, before the agent spends a "
        "run_query attempt, whether the turn should be answered, so EX and coverage both move "
        "and the config is only readable against an unabstained pair. Costs no model call. It is a "
        "comparability setting and enters the artifact tag, because an abstaining config and a "
        "committing one are two configs -- and a resume that merged them would report the "
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
        "retired. Off by default so the lexical config stays the reproducible baseline.",
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
        "for itself. `route` is deterministic, but the four retrieval-channel rewriters above it are model "
        "calls, so an unpinned A/B cannot tell its own effect from a shortlist that moved. "
        "Pass two still re-searches inside the pinned schemas -- the residual is measured and "
        "printed as retrieved-table drift, not assumed away.",
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
        "that deletes a measured run, and a config on this dataset is hours of paid model calls, "
        "so it is separate from --force-fresh (which for a while did this silently) and it "
        "prints the row count it is discarding first. Contradicts --resume.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="the config this run is, by name, from registry/configs.toml. Naming it makes the "
        "committed claim about what this config changed checkable against what the run records: "
        "the declared corpus is reconciled against the session's corpus_content_hash BEFORE "
        "the first paid question, and every row is reconciled again in the report. Unnamed "
        "runs are still allowed and are simply unreconciled -- but a comparison against an "
        "config with no profile is `cannot_evaluate`, by audit D9.",
    )
    args = parser.parse_args(argv)

    # ``--resume`` and ``--truncate`` contradict on one file. Refused in ``flag_conflict``
    # so a test can reach the decision without starting this driver.
    from governed_bi.eval.provenance import flag_conflict

    conflict = flag_conflict(resume=args.resume, truncate=args.truncate)
    if conflict:
        parser.error(conflict)

    from governed_bi import credentials
    from governed_bi.boot import resolve_settings

    resolve_settings()
    apply_served_model_defaults(args)
    source = apply_served_utility_default(args)
    if source:
        print(f"utility model {args.utility_model} (effort {args.utility_effort or 'none'}) from {source}")
    # An effort with no utility model would be accepted and then dropped.
    if args.utility_effort and not args.utility_model:
        parser.error("--utility-effort needs --utility-model; alone it is accepted and ignored")
    from governed_bi.model import provider as provider_mod

    if not provider_mod.credentials_present():
        names = " / ".join(provider_mod.credential_names())
        print(f"no openai credential reachable ({names})", file=sys.stderr)
        return 2
    dsn = credentials.secret(*credentials.PG_DSN_NAMES)
    if not dsn:
        print("no database credential reachable", file=sys.stderr)
        return 2

    # The profile is loaded *before* the models are built, so a typo in --config or a malformed
    # configs.toml costs nothing. `config_profile` raises on an unknown name rather than returning an
    # empty setting under test, which is the whole point of the file.
    profile = None
    if args.config:
        from governed_bi.registry.config_profiles import config_profile

        try:
            profile = config_profile(args.config)
        except (KeyError, OSError, ValueError) as err:
            print(f"--config: {err}", file=sys.stderr)
            return 2

    from governed_bi.datasource.postgres import PostgresConnector
    from governed_bi.eval.configs import live_config
    from governed_bi.eval.datalake import (
        attach_gold_fingerprints,
        attach_quality_flags,
        dataset_leakage_qids,
        dataset_qid_lists,
        load_questions,
        table_coverage,
    )
    from governed_bi.eval.harness import run_config
    from governed_bi.eval.provenance import (
        append_refusal,
        config_power_refusal,
        config_startup_refusal,
        harness_settings,
        resume_identity_problem,
        scope_identity,
        truncation_notice,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.registry.settings import env_override as _settings_env_override
    from governed_bi.serve import session as session_mod

    model, utility_model, embedder, vector_cache = _build_models(args)

    # One connector for the session and the graph; each worker gets its own below.
    # Pass ``utility_model`` only when set: if absent, ``session`` copies the agent model
    # into ``llm_utility_model``, and shared-versus-split must not become one setting.
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
        from governed_bi.registry.prompts import select

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

    # Before the first paid question. A named config whose corpus is not the one loaded
    # is a mislabelled artifact, and this is the only point where refusing is free.
    if profile is not None:
        print(f"config {profile.name}: {profile.description}", flush=True)
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

    # Both identities, once both are knowable, and still before spend or delete.
    # Flat keys, no ``settings_resolved``: ``recorded_question_subset`` reads that map first.
    # ``scope_identity`` is called again in ``harness_settings`` on the same arguments.
    if profile is not None:
        mislabelled = config_startup_refusal(
            profile,
            {
                "corpus_content_hash": session.corpus_content_hash,
                "question_subset": scope_identity(
                    schemas=schemas,
                    question_ids=covered_qids,
                    dataset_file=dataset_file,
                )["question_subset"],
                # The setting that names the corpus under test. Flat, not under
                # ``settings_resolved``: this mapping is not a row. ``None`` means the run
                # named no release, which ``reconcile`` reads as silence, not a mismatch.
                "corpus_release": _settings_env_override("corpus_release") or None,
            },
        )
        if mislabelled:
            print(mislabelled, file=sys.stderr)
            return 5

        # Power, before the first paid question, where a refusal is free. Silent unless
        # the profile declares a hypothesis: inventing one would quote a number this
        # file does not own as if the config had declared it.
        underpowered = config_power_refusal(profile, len(covered_qids))
        if underpowered:
            print(underpowered, file=sys.stderr)
            return 5

    # Retrieval channel and prompt selection are in the tag. Channels have different
    # coverage ceilings, and an A/B that differs only in ``--prompt-variant`` would
    # otherwise share one path, so ``--resume`` would skip the second config's questions.
    variant_tag = "".join(
        f"_{pair.replace('=', '')}" for pair in sorted(args.prompt_variant or ())
    )
    # Abstention moves coverage. A resume that merged an abstaining run into a
    # committing artifact would blend two operating points in every selective figure.
    abstain_tag = "_abstain" if args.abstain else ""
    # A pinned run and an unpinned one disagree often enough to move the detectable effect
    # (9.3% vs 12.7% of questions; MDE 2.3pp vs 2.7pp, n=1351). The pin is in the tag
    # so ``--resume`` cannot merge them.
    pinned_tag = "_pinned" if args.replay_routing is not None else ""
    tag = (
        f"{args.model}_{args.effort or 'default'}_top{args.top_n or 'default'}"
        f"_{'embed' if args.embed else 'lexical'}"
        f"{variant_tag}{abstain_tag}{pinned_tag}"
    )
    out_path = args.out or pathlib.Path("runs/eval") / f"live_full_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # A second population in one artifact is refused before anything is written.
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

    # Resume drops crashed rows and requeues their questions: a crash is not a measurement.
    # No artifact at this tag, with sibling artifacts present, is treated as a renamed run
    # unless ``--force-fresh``.
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

    # One dict for every per-question override. A second writer of ``settings_resolved``
    # would drop the first. Built before the resume guard, which compares these settings
    # with the ones recorded in the artifact.
    setting_overrides: dict[str, Any] = harness_settings(
        repo=REPO,
        schemas=schemas,
        question_ids=covered_qids,
        dataset_file=dataset_file,
        serve_workers=args.workers,
    )
    if args.top_n is not None:
        setting_overrides["route_top_n"] = args.top_n
    if args.abstain:
        setting_overrides["abstention_policy_enabled"] = True
    run_settings = {**session.settings_resolved, **setting_overrides}

    done: set[str] = set()
    retrying = 0
    if args.resume and out_path.exists():
        from governed_bi.registry.settings import setting_names

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
            settings_resolved=run_settings,
            affects_results=setting_names(),
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

    # Attach the published gold digest and the dataset's quality flags, and print both counts.
    # Without the digest every oracle row is ``correct=None``. A silent empty attach looks
    # like a call that never happened.
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

    # Writes the prior shortlist onto the questions. ``route_top_n`` stays a separate field.
    if args.replay_routing is not None:
        from governed_bi.eval.replay import attach_pinned_routing, routing_from_artifact

        pinned_baseline = routing_from_artifact(args.replay_routing)
        pin_counts = attach_pinned_routing(questions, pinned_baseline)
        print(
            f"routing pinned from {args.replay_routing}: "
            f"{pin_counts['pinned']} pinned, {pin_counts['unpinned']} routed live "
            "(a question the artifact does not cover routes for itself and is counted here, "
            "because a config labelled pinned always has some fraction that is not)",
            flush=True,
        )

    # Composed above, applied here: `Session.turn` writes the session's own settings over the
    # turn, and `harness._turn_settings` prefers the question's mapping, so this is where the
    # harness half of the identity reaches the row.
    for question in questions:
        question["settings_resolved"] = dict(run_settings)

    total = len(questions)
    print(
        "harness: "
        + ", ".join(
            f"{k}={run_settings[k]}"
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
        run_config(
            questions,
            live_config(session, name=f"live_{tag}"),
            order_sensitive_qids=frozenset(order_sensitive),
            session=session,
            run_id=f"live-{tag}",
            workers=args.workers,
            connector_factory=lambda: PostgresConnector(dsn),
            model_factory=lambda: _worker_models(args),
            on_row=append,
        )
    finally:
        handle.close()

    print_report(out_path, args, table_coverage, against=args.against)
    return 0




def _build_models(args, clients: dict | None = None):
    """``(model, utility_model, embedder, vector_cache)`` on the OpenAI gateway.

    A 429 inside a node is marked crashed, so retries are a measurement, not a nicety.
    """
    from governed_bi.model import provider as provider_mod

    embedder = None
    vector_cache = None
    utility_model = None
    retries = max(0, int(args.max_retries))
    from governed_bi.registry.settings import setting_default

    # The same caps as the served app, so the run measures that configuration.
    model = provider_mod.chat_model(
        args.model,
        surface="agent",
        effort=args.effort or None,
        timeout=float(args.timeout),
        max_retries=retries,
        max_output_tokens=int(setting_default("llm_max_output_tokens")),
        tools=True,
        **(clients or {}),
    )
    if args.utility_model:
        utility_model = provider_mod.chat_model(
            args.utility_model,
            surface="utility",
            effort=args.utility_effort or None,
            timeout=float(args.timeout),
            max_retries=retries,
            max_output_tokens=int(setting_default("llm_utility_max_output_tokens")),
            **(clients or {}),
        )
    if args.embed:
        from governed_bi.retrieve.vector_cache import vector_cache_from_environment

        embedder = provider_mod.embedder(
            args.embedding_model or provider_mod.default_embedding_model(),
            max_retries=retries,
        )
        vector_cache = vector_cache_from_environment(model=embedder.requested_model)
    return model, utility_model, embedder, vector_cache


def _worker_models(args) -> dict:
    """Chat models with this worker's own HTTP clients, configured as ``_build_models`` does.

    langchain-openai shares one cached async client per process, and it binds to the first
    worker's event loop. ``aclose`` closes the worker's sockets on that worker's loop.
    """
    import httpx

    sync_client, async_client = httpx.Client(), httpx.AsyncClient()
    clients = {"http_client": sync_client, "http_async_client": async_client}
    model, utility_model, _, _ = _build_models(
        argparse.Namespace(**{**vars(args), "embed": False}), clients=clients
    )

    async def aclose() -> None:
        await async_client.aclose()
        sync_client.close()

    return {"agent_model": model, "utility_model": utility_model or model, "aclose": aclose}


if __name__ == "__main__":
    raise SystemExit(main())
