# governed-bi design

_[English](README.md) · [简体中文](README.zh.md)_

Design for an agentic BI / Generative-BI system: natural-language questions →
grounded, governed, auditable answers over enterprise relational data.

It grows a reviewable semantic layer from a seed of known-good queries —
*seed-assisted growth*, not a zero-prior cold start. **Postgres** is the
exercised-live path; SQLite is the offline test / CI substrate only. Enterprise
abstractions are seamed in but toggled off. Evaluated on the self-built
[BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset
(execution accuracy; cost logged).

## Read in this order

1. [Architecture](architecture.md): the full design (spine, kernel, services, storage, flow, eval, environments).
2. [Design decisions](design-decisions.md): D1–D19 (+ 2026-07-15 audit dispositions) as ADRs, with alternatives and trade-offs.
3. [Asset schemas](asset-schemas.md): the per-asset YAML field spec (Facts / Inference / Audit tiers).
4. [Curator](curator.md): the build-side proposer + adversary loop. For the exact prompts, see [Curator LLM-call walkthrough](curator-llm-call.md).
5. [Analyst](analyst.md): the serve-side governed agentic core + guardrails. For the exact prompts, see [Analyst LLM-call walkthrough](analyst-llm-call.md).
6. [Viz](viz.md): the audit surface — the presenter view models plus the `governed_bi.api` HTTP API to browse the layer and chat with the governed Analyst (corpus write gated by `allow_edit`; the interactive UI is a separate project).
7. [Measurement](measurement.md): what the eval harness records and where a failure localises — read this when a number looks wrong. The field-by-field register is [Eval metrics](eval-metrics.md).
8. [Prompt-variant experiments](prompt-experiments.md): the prompt registry, how a run selects a variant, what gets stamped where, and how to decide which variant a measured failure actually calls for.
9. [Glossary](glossary.md): canonical terms.

[Open work](open-work.md) is the single tracker for what is still open.

[External design sources](references.md) that ground the design.

## Using the repo

The design docs above describe the intended system. For what actually runs
today (the corpus layer and the dev workflow):

- [Usage](usage.md): install, validate the example corpus, ask your first question. **Start here.**
- [Corpus authoring](corpus-authoring.md): write and validate corpus assets step by step.

Two step-by-step call traces sit alongside the design docs, useful when reading
the code: [analyst sequence](analyst-sequence.md) and
[curator sequence](curator-sequence.md).

## Decision records (ADRs)

Point-in-time decisions. An ADR is never edited to match later reality — a
superseding ADR is added instead, so a stale-looking statement inside one is
intentional history.

| ADR | Status |
|---|---|
| [0001 LangGraph Server chat runtime](adr/0001-langgraph-server-chat-runtime.md) | Accepted 2026-07-10; partly superseded by 0002 |
| [0002 Governed agentic serve runtime](adr/0002-governed-agentic-serve-runtime.md) | Accepted / implemented (`d2fdd6a`) — the sole serve path |
| [0003 Governed notes, tri-modal retrieval](adr/0003-governed-notes-tri-modal-retrieval.md) | Accepted 2026-07-22 (D17); built — `NoteAsset`, `note_inject.py`, `retrieval/triggers.py`, `read_notes` / `grep_notes`, `[notes]` config |
| [0004 Local-first conversation + run logging](adr/0004-local-first-conversation-run-logging.md) | Accepted 2026-07-22 (D18); built — `run_log.py`, `[logging]` config, `prune_full_content` retention |

> **The falsifier.** The one result that would make us conclude the corpus does not
> help — arm pair, metric, stratum, effect size, number of curator draws — is written
> down in
> [`plans/experiment-runbook.md`](plans/experiment-runbook.md#the-result-that-would-make-us-abandon-the-corpus-thesis).
> It was stated before the run, and it has not been evaluated yet: it needs three
> independent curator draws at 69 schemas, and no such run exists.

## Working docs (`plans/`) and reviews

Dated working docs, not canonical design. Where one disagrees with the docs
above, the docs above win. **No eval number anywhere in this repo is currently
quotable — every number produced before 2026-07-26 is discarded.**

*Live:*

- [Experiment runbook](plans/experiment-runbook.md): what to run, in what order, and what must be true before a number is worth quoting. **The entry point for any eval work.**
- [Data-lake run](plans/datalake-run.md): the pooled multi-schema run (D15) — runbook and status.
- [Serve transparency handoff](plans/serve-transparency-handoff.md): making the agent's *inputs* visible in the UI. The earlier design note [`serve-transparency.md`](plans/serve-transparency.md) is marked **SAFE TO DELETE** (no unique content; live event contract is [analyst.md](analyst.md)). Contract publication continues under [rebuild-checklist.md](plans/rebuild-checklist.md) §5.3.
- [SME channel repair](plans/sme-channel-repair.md): why the `curated_sme` arm moved nothing, and the fixes in order.
- [Eval rebuild](plans/eval-rebuild.md): why every pre-2026-07-26 number is discarded, and the four fixes that follow.
- **[Near-term plan](plans/near-term-plan.md) (Simplified Chinese): the seventeen items being implemented now, cut out of the checklist below on two tests — delegable, and machine-checkable. Five milestones ending in one quotable run. Says what a reviewer will reject on, and lists what is deliberately *not* in this batch. The entry point for anyone picking up work today.**
- **[Rebuild checklist](plans/rebuild-checklist.md) (Simplified Chinese): the current work queue. Eleven cross-cutting items in dependency order, plus the analysis-tooling, recording, observability, signal, and run tracks. Each item says what to change, which files it touches, and how to verify it. Start here.**
- **[Rebuild decisions](plans/rebuild-decisions.md) (Simplified Chinese): the twenty-two decisions behind that queue — what was chosen, why, and what was rejected. Read this before overturning anything in the checklist.**
- [Build sequence](plans/build-sequence.md): **superseded** by the two above, kept as the evidence index — it maps each item back to the analysis that found it (62 items → 41, four phases, plus the non-goals).
- [Grill agenda](plans/grill-agenda.md) (Simplified Chinese): the pre-grill briefing — the five analyses turned into eight contested decisions with the sharpest objection to each option. **Consumed by the 2026-07-30 grill**; its conclusions live in `rebuild-decisions.md`.
- [Multi-turn adversarial](plans/multi-turn-adversarial.md): every number in this repo is turn 1. Routing and retrieval run on the coreference-unresolved question, and AUDIT S4's bound makes a mis-routed follow-up unrecoverable — a correct follow-up can be refused.
- [Governance red team](plans/governance-red-team.md): attacking ADR 0002's topology-not-trust claim. Seven hypotheses, each with the test that settles it; the graded-delivery path re-checks with the scope argument disabled.
- [Corpus drift](plans/corpus-drift.md): drift is checked once at build time and never again; `/health` runs the validator with no connector, so it cannot report the failure it implies.
- [Framework + logging audit](plans/framework-and-logging-audit.md): best-practice scan of every LangGraph / LangChain-middleware / DeepAgents / Langfuse / checkpointer / memory site against the skills and vendor docs **as of 2026-07-30**, plus the one-identity design for logging everything. Its headline was a privacy finding — the Langfuse content mask did not cover callback data — which is moot: Langfuse was removed on 2026-08-02, and traces now log in full by decision (`governed_bi/obs.py`). Read the Langfuse sections as history.
- [Book fidelity assessment](plans/book-fidelity-assessment.md): this repo measured against the reference document it grew out of — which divergences were deliberate (9), which are drift nobody decided (15), and which gaps the reference shares. Focused on routing, the semantic layer, and a nine-axis systematic analysis of RVGD retrieval.

Two plans finished and were deleted; their wire contracts now live in
[Analyst](analyst.md) — the [governance event stream](analyst.md#the-event-contract-per-step)
and [serve-time clarification](analyst.md#serve-time-clarification-hitl). The module
deepening plan was deleted unstarted; the items from it that still matter are in
[open work](open-work.md), and the rest is in git history.

Closed trackers and superseded plans are not kept as files — git history holds
them. Open items from all of them live in [open work](open-work.md).

## The spine (non-negotiables)

- **Two planes.** A semantic/control plane (versioned config + markdown, published via PR/CI) stays separate from a data plane that executes only guardrail-passed SQL. Meaning is defined once and owned by humans.
- **Authority is deterministic; reasoning may be agentic.** The question can be wide and the model reasons in a bounded agentic loop, but *what may execute, what is trusted, and what is recorded* is fixed by middleware, not model discretion (ADR 0002 reversed the earlier "never an autonomous loop" rule). The SQL must be narrow.
- **Fail-closed under serve defaults.** With `grade_semantic_failures=False` (the serve default), out-of-scope / missing-coverage / tripped-guardrail returns a refusal or a clarifying question — not a confident wrong number. Graded delivery (on in the eval drivers today) can re-serve some L4/L5 failures as `unverified` rows; that is not the serve default.

## How the docs map to the code

| Doc | Package area |
|---|---|
| [Asset schemas](asset-schemas.md), [Design decisions](design-decisions.md) D9 | `src/governed_bi/corpus/` |
| [Curator](curator.md) | `src/governed_bi/curator/` |
| [Analyst](analyst.md), [Architecture](architecture.md) §6 | `src/governed_bi/analyst/`, `gateway/`, `graph/`, `retrieval/`, `memory/` |
| [Architecture](architecture.md) §8, [Measurement](measurement.md) | `src/governed_bi/eval/`, `src/governed_bi/stages.py` |
| [Prompt-variant experiments](prompt-experiments.md) | `src/governed_bi/prompts/` |
| [Viz](viz.md) | `src/governed_bi/viz/` |
| [Architecture](architecture.md) §9 (environment toggles) | `src/governed_bi/config.py` |
