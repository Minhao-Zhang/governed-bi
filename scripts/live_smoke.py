"""Live-model smoke run — the one action that punctures the self-consistent bubble.

Everything in the test suite uses deterministic offline doubles, so real
generation quality is unmeasured. This script swaps in the real OpenAI-backed
LangChain clients (per ``governed_bi.toml``) and runs the existing eval harness
over the committed, un-obfuscated beer_factory DB + hand-authored corpus:

  - the **curator arm** (server flow + LLM SQL generator): execution accuracy,
    decoy-touch rate, governed-path adherence over ``BEER_FACTORY_EVAL``;
  - the **refuse-gate**: refusal accuracy on the unanswerable set + the
    false-refusal cost on the answerable set;
  - a few **sample answers** printed with the two-axis stamp.

This is a *smoke test of the real path*, not the headline benchmark: the corpus,
gold set, and DB are all self-authored and un-obfuscated, so a high EX here only
proves the plumbing works end-to-end against a live model. The real moat proof is
the obfuscated BIRD three-arm eval (still pending the obfuscated DBs).

Run it (needs the ``agents`` extra + a key; real API spend, ~a dozen small calls):

    export OPENAI_API_KEY=sk-...
    uv run --extra agents python scripts/live_smoke.py

Nothing here is imported by the package or the tests; it is a manual entrypoint.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPO_ROOT / "corpus"
BIRD_DB = REPO_ROOT / "data" / "bird" / "beer_factory.sqlite"


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    # Fill OPENAI_API_KEY from a repo-root .env if it is not already in the env
    # (a real environment variable wins); then check it is present.
    from governed_bi.config import load_dotenv

    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        _fail("OPENAI_API_KEY is not set. Export it, or put it in a .env at the repo root "
              "(it is read from the env, never stored).")
    if not BIRD_DB.exists():
        _fail(f"missing demo DB at {BIRD_DB}")

    # Imported here (not at module top) so a helpful message replaces an opaque
    # ImportError when the `agents` extra is not installed.
    try:
        from governed_bi.llm import LangChainChatClient, LangChainEmbedder
    except ImportError as err:
        _fail(f"the `agents` extra is not installed ({err}). Run: uv sync --extra agents")

    from governed_bi.config import Environment, Settings, load_settings
    from governed_bi.corpus import load_corpus
    from governed_bi.eval import (
        BEER_FACTORY_EVAL,
        BEER_FACTORY_UNANSWERABLE,
        Arm,
        eval_refuse_gate,
        flow_refuser,
        flow_solver,
        run_arm,
    )
    from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist
    from governed_bi.server import LlmSqlGenerator, answer_question

    models = load_settings(REPO_ROOT / "governed_bi.toml").models
    print(f"models: llm={models.llm_model} (effort={models.llm_reasoning_effort}) "
          f"embed={models.embedding_model}\n")

    chat = LangChainChatClient.from_config(models)
    embedder = LangChainEmbedder.from_config(models)
    generator = LlmSqlGenerator(chat, dialect="sqlite")

    corpus = load_corpus(CORPUS_ROOT, db="beer_factory").for_server()
    settings = Settings.for_env(Environment.dev)
    identity = Identity(user="dev", all_access=True)
    connector = SqliteConnector(BIRD_DB)
    gateway = Gateway(connector)
    suspect = column_allowlist(corpus).suspect

    try:
        # ── Curator arm: EX + free behavioral signals, LLM generator ──
        solver = flow_solver(corpus, gateway, settings, identity, sql_generator=generator)
        arm = run_arm(Arm.curator, gateway, BEER_FACTORY_EVAL, solver,
                      suspect_columns=suspect, dialect="sqlite")
        print("== curator arm (LLM SQL generator, BM25 retrieval) ==")
        print(f"  execution accuracy : {arm.ex:.2f}  ({arm.n} items)")
        print(f"  decoy-touch rate   : {arm.decoy_touch_rate:.2f}")
        print(f"  governed-path adher: {arm.governed_path_adherence:.2f}\n")

        # ── Refuse-gate: recall on unanswerable, false-refusal on answerable ──
        refuser = flow_refuser(corpus, gateway, settings, identity, sql_generator=generator)
        rg = eval_refuse_gate(
            answerable=[item.question for item in BEER_FACTORY_EVAL],
            unanswerable=BEER_FACTORY_UNANSWERABLE,
            refused=refuser,
        )
        print("== refuse-gate ==")
        print(f"  refusal accuracy   : {rg.refusal_accuracy:.2f}  (want high)")
        print(f"  false-refusal rate : {rg.false_refusal_rate:.2f}  (want 0)\n")

        # ── Sample answers, with vector retrieval + the two-axis stamp ──
        print("== sample answers (LLM gen + vector retrieval) ==")
        for question in [item.question for item in BEER_FACTORY_EVAL] + [BEER_FACTORY_UNANSWERABLE[0]]:
            ans = answer_question(
                question, identity, corpus=corpus, gateway=gateway, settings=settings,
                session_id="live-smoke", sql_generator=generator, embedder=embedder,
            )
            print(f"  Q: {question}")
            print(f"     safety={ans.safety_clearance} assurance={ans.semantic_assurance.value} "
                  f"tier={ans.tier.value}")
            print(f"     sql: {ans.sql or '(refused)'}")
            if ans.text:
                print(f"     ->  {ans.text}")
            print()
    finally:
        connector.close()


if __name__ == "__main__":
    main()
