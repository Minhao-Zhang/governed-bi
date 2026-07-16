"""Live-model smoke run — the one action that punctures the self-consistent bubble.

Everything in the test suite uses deterministic offline doubles (``FakeToolModel``),
so real generation quality is unmeasured. This script swaps in the real
OpenAI-backed LangChain clients (per ``governed_bi.toml``) and runs the existing
eval harness over the committed, un-obfuscated beer_factory DB + hand-authored
corpus, through the **agentic serve core** (ADR 0002 — the only serve path):

  - the **curated arm** (``agent_solver`` = create_agent + governance middleware):
    execution accuracy, decoy-touch rate, governed-path adherence over
    ``BEER_FACTORY_EVAL``;
  - the **refuse-gate** (``agent_refuser``): refusal accuracy on the unanswerable
    set + the false-refusal cost on the answerable set;
  - a few **sample answers** printed with the two-axis stamp.

This is a *smoke test of the real path*, not the headline benchmark: the corpus,
gold set, and DB are all self-authored and un-obfuscated, so a high EX here only
proves the plumbing works end-to-end against a live model. The real moat proof is
the obfuscated BIRD eval-ladder run.

Run it (needs a key; real API spend — the agent path makes several small model
calls per question):

    export OPENAI_API_KEY=sk-...
    uv run python scripts/live_smoke.py

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
    # Fill the API key from a repo-root .env if it is not already in the env
    # (secrets only; a real environment variable wins); then check it is present.
    from governed_bi.config import load_dotenv, load_settings

    load_dotenv()
    api_key_env = load_settings(apply_local=False).models.api_key_env
    if not os.environ.get(api_key_env):
        _fail(f"{api_key_env} is not set. Export it, or put it in a .env at the repo root "
              "(secrets only; policy lives in governed_bi.toml).")
    if not BIRD_DB.exists():
        _fail(f"missing demo DB at {BIRD_DB}")

    # Imported here (not at module top) so a helpful message replaces an opaque
    # ImportError when the `agents` extra is not installed.
    try:
        from governed_bi.llm import LangChainChatClient, LangChainEmbedder
    except ImportError as err:
        _fail(f"LangChain/deepagents deps failed to import ({err}). Run: uv sync")

    from governed_bi.config import Environment, Settings, load_settings
    from governed_bi.corpus import load_corpus
    from governed_bi.eval import (
        BEER_FACTORY_EVAL,
        BEER_FACTORY_UNANSWERABLE,
        Arm,
        agent_refuser,
        agent_solver,
        eval_refuse_gate,
        run_arm,
    )
    from governed_bi.gateway import Gateway, Identity, SqliteConnector, column_allowlist
    from governed_bi.analyst.agent import answer_question_agent

    models = load_settings(REPO_ROOT / "governed_bi.toml").models
    print(f"models: llm={models.llm_model} (effort={models.llm_reasoning_effort}) "
          f"embed={models.embedding_model}\n")

    chat = LangChainChatClient.from_config(models)
    embedder = LangChainEmbedder.from_config(models)
    model = chat.model  # raw LangChain BaseChatModel the agent core drives

    corpus = load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()
    settings = Settings.for_env(Environment.dev)
    identity = Identity(user="dev", all_access=True)
    connector = SqliteConnector(BIRD_DB)
    gateway = Gateway(connector)
    suspect = column_allowlist(corpus).suspect

    try:
        # ── Curated arm: EX + free behavioral signals, agentic serve core ──
        solver = agent_solver(corpus, gateway, settings, identity, model=model, embedder=embedder)
        arm = run_arm(Arm.curated, gateway, BEER_FACTORY_EVAL, solver,
                      suspect_columns=suspect, dialect="sqlite")
        print("== curated arm (agentic serve core: create_agent + governance) ==")
        print(f"  execution accuracy : {arm.ex:.2f}  ({arm.n} items)")
        print(f"  decoy-touch rate   : {arm.decoy_touch_rate:.2f}")
        print(f"  governed-path adher: {arm.governed_path_adherence:.2f}\n")

        # ── Refuse-gate: recall on unanswerable, false-refusal on answerable ──
        refuser = agent_refuser(corpus, gateway, settings, identity, model=model, embedder=embedder)
        rg = eval_refuse_gate(
            answerable=[item.question for item in BEER_FACTORY_EVAL],
            unanswerable=BEER_FACTORY_UNANSWERABLE,
            refused=refuser,
        )
        print("== refuse-gate ==")
        print(f"  refusal accuracy   : {rg.refusal_accuracy:.2f}  (want high)")
        print(f"  false-refusal rate : {rg.false_refusal_rate:.2f}  (want 0)\n")

        # ── Sample answers, with vector retrieval + the two-axis stamp ──
        print("== sample answers (agent core + vector retrieval) ==")
        for question in [item.question for item in BEER_FACTORY_EVAL] + [BEER_FACTORY_UNANSWERABLE[0]]:
            ans = answer_question_agent(
                question, identity, corpus=corpus, gateway=gateway, settings=settings,
                session_id="live-smoke", model=model, embedder=embedder,
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
