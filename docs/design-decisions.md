# Agentic BI Design Decisions

_[English](design-decisions.md) · [简体中文](design-decisions.zh.md)_

Settled decisions D1-D10 for the [Agentic BI System](system-overview.md), with
the alternatives considered and the trade-offs. The **ADR-grade** ones are hard
to reverse. Treat them as ADRs.

## D1: Target

> **Decided (revised 2026-07-07)**
>
> Build a **general, DB-agnostic showcase project** (personal GitHub) that
> cold-starts from minimal priors, namely `{a DB connection + a handful of
> known-good example queries}`, and **grows the semantic layer over time**.
> Proven on [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (execution accuracy; cost logged). **Not a
> product**: enterprise abstractions (identity, gate, scoped memory/cache, RLS)
> are seamed in but toggled off. The justification now is a private **enterprise
> fork** that reuses this engine, not "prod is a config flip." That **enterprise
> fork is a private parallel effort (phase 2)**, not the first slice.

Reason: the showcase is the only place with real data, a real evaluator, and no access barrier. "Curation beats accumulation" is unprovable without a grader, and BIRD is that grader. The private enterprise fork faces the *same* core situation: no one owns the semantic layer, and there's no manpower to build it by hand. So the generic cold-start engine transfers directly, and that fork merely adds SME-request history as an extra seed signal. Building a shippable multi-tenant product now would mean governance and tenancy for users you can't yet measure, which is deliberately deferred.

## D2: Governed Unit

> **Decided (ADR-grade)**
>
> A **logical governed dataset** (canonical single-source-of-truth model:
> grain / columns / joins / hygiene defined once) + **compiled metrics** on top.
> Materialized views are an optimization, not the definition. UDH.ai terms
> ("category", "fabric object") retired.

- **Alternatives:** physical wide MV as the unit (locks you into an expensive hand-tuned materialization, criticized as unscalable); metric-only (loses the coarse retrieval target and the grain/join home).
- **Consequence:** dissolves the fabric-vs-discovery tension. Fabric and discovery become two ways to satisfy the *same* logical definition, and discovery output can be promoted into a materialized dataset later without changing semantics. Matches Anthropic and *《从数据到智能》*.

## D3: Eval Dataset

> **Decided**
>
> Near-term eval = the self-built *BIRD-Obfuscation* dataset (4 DB versions,
> ~10k verified Q&A, decoy manifest, rename map). Supplies the verified
> ground-truth answers we otherwise lack. Retrieval-at-scale eval on an
> enterprise-scale deployment added later.

Fit: the obfuscation dimensions *are* our target failure modes. Decoy = concept↔entity ambiguity, rename = memorized-names reliance, FK-withheld = join inference, rewrite = paraphrase robustness. The decoy manifest gives governed-path adherence for free. See *BIRD Bench Obfuscation Methodology*.

## D4: Grading

> **Decided**
>
> Headline metric = **execution accuracy vs gold** (automatable; trustworthy
> because the dataset re-runs gold SQL). **No hand-grading of semantic layers.**
> Three arms all scored on EX: (1) no semantic layer, (2) curator-built,
> (3) gold semantic layer auto-derived from the manifest.

- Arms 2/3 vs 1 = the moat proof. Arm 2 vs 3 = curator quality, for free.
- Free behavioral signals: decoy-touch rate, governed-path adherence.

> **Arm 3 "gold" = a deterministic de-obfuscation oracle, not an AI build (clarified 2026-07-07)**
>
> The gold layer is the **de-obfuscation key read back**: rename map → real
> names, decoy manifest → decoy exclusions, original BIRD → the withheld FK
> graph. **No AI, no owner, cannot drift**; scored on the same EX. It is a
> **reference line, not a strict ceiling**: Arm 3 is "perfect structure, no
> skills," so a curator whose skills/gotchas help can beat it on some questions.
> **Dropped for the enterprise fork / the metric-governance half**. No ground-truth gold
> exists there, so that setting runs Arm 1 vs Arm 2 only.

## D5: Refusal & Best-effort

> **Decided (ADR-grade)**
>
> Two concurrent gates: curated **negative examples** → canned escalation blob
> (owner contact); always-on hard guardrails (AST / cost / PII / RLS) via
> `wrap_tool_call`; else **best-effort** via the recommended path + a
> **reliability stamp**. High-stakes → human sign-off.

- **Alternative:** fail-closed the moment coverage runs out (safe but misses the long tail, and makes the agent guess whether it's out of scope).
- **Consequence:** refusal is driven by a curated signal, not a coverage heuristic. Two caveats: the stamp needs teeth, because a footer alone is weak against silent wrong answers; and BIRD won't test the refuse-gate, which needs a held-out unanswerable set.
- **Built:** the four-tier stamp (`governed` / `lineage` / `fenced_raw` / `refused`) and the fail-closed refuse-gate are implemented; a **bounded self-repair loop** feeds a guardrail rejection or execution error back to the generator for another attempt before refusing, and a repaired answer is stamped `lineage`, never `governed`. The tier thresholds and the uncertainty-signal set are **uncalibrated heuristics**, to be tuned on the eval. `governed` means safe + in-scope + no uncertainty flag fired, **not** "verified correct": the guardrails are a safety/governance gate, not a correctness oracle, so plausible-but-wrong SQL is caught by the stamp + fail-closed, not by a proof of correctness.

## D6: Ownership & Human Gate

> **Decided**
>
> Per-domain **named owner** certifies datasets / metrics / negative-examples.
> **Env-toggle:** test (BIRD) auto-accepts; prod (enterprise) requires PR + owner +
> CI behind every corpus change. Certification (blessing a *definition*) stays
> distinct from high-stakes answer sign-off (blessing an *answer*).

- **Built (scope):** this repo ships a **read-only** audit cockpit; interactive corpus editing and save-to-PR are **out of scope here** (generic git/PR + CI in dev, the enterprise app in prod). The repo owns the write *primitives* a downstream editor reuses: the asset schema, `corpus.serialize.write_corpus`, and `corpus.validate` + CLI (the CI gate). See [Viz](viz.md).

## D7: Identity

> **Decided**
>
> App acts **as the user** (RLS-as-user / identity propagation); one
> identity-scoped agent, never broader than the person behind it. Env-toggle:
> dev = single all-access identity; prod = real user + RLS at the gateway.

> **Scope covers memory + cache, not just the live query**
>
> Episodic memory and result caching leak across users if not identity-scoped.
> This is why we cache SQL (re-run as each user), never results.

## D8: Serve-time Memory

> **Decided (ADR-grade)**
>
> **Working memory always on** (session, identity-scoped). **Episodic /
> correction off by default**, adopted per-domain only when eval earns it,
> value-aware when used. **Durable memory is PR-gated exactly like the corpus.**

- **Reason:** more memory often hurts (EnterpriseMem-Bench: episodic swung +14pp to −16pp; retrieval biases the model). Working memory is the one universal win.
- **Consequence:** the memory/corpus distinction collapses. Correction memory ≈ correction-harvesting→PR-to-reference-doc; promoted episodic ≈ gated few-shots. One PR-gated corpus, not two governance models. See the *Data Agent Memory Design Overview*.
- **Built:** working memory is implemented as an in-process, session-scoped store (`InMemoryWorkingMemory`), the store the `before_model` middleware reads to inject prior context. Episodic and correction memory are **off-by-default protocol seams** (`EpisodicMemory` / `CorrectionMemory`), not implemented, consistent with "adopt per-domain only when eval earns it".

## Two-harness Split (ADR-grade, cross-cutting)

> **Decided (ADR-grade)**
>
> Curator (build, `deepagents`) and server (serve, `LangGraph` + middleware) are
> separate harnesses over one shared substrate; fork only the harness.

- **Alternatives:** one unified agent (can't satisfy opposite risk profiles); two fully separate systems (the corpus drifts between build and serve).
- **Consequence:** requires clean service interfaces; exploration at serve time is a fenced pocket that only emits promotion candidates. Detail in [Architecture](architecture.md) §2.

## Markdown-first Storage (ADR-grade, cross-cutting)

> **Decided (ADR-grade)**
>
> Markdown-first for skills/reference docs; compiled config for metric
> definitions; graph only for joins/lineage; heavy LLM knowledge graph deferred.

- **Alternative:** graph-DB-first (Neo4j) for the whole corpus. That adds a store to build and maintain, stale graphs are dangerous, and it doesn't fix a curation problem.
- **Consequence:** the curator's output is human-reviewable markdown; the correction loop is "edit a file + PR." Detail in [Architecture](architecture.md) §5.

## D9: Corpus File-Structure Contract

> **Decided (ADR-grade, 2026-07-07)**
>
> The corpus is **Git-tracked plain markdown + YAML typed assets**, adapted from
> *《从数据到智能》* Ch.3's 9-asset-type semantic layer, but with the **authoring
> model inverted**: the **curator generates** the assets, and a human **audits**
> them via the viz surface, rather than authoring them. **Git is the single
> source of truth.** Every other store (graph / vector / BM25 / Postgres) is a
> **derived, rebuildable projection**, never authored directly, Neo4j included.

- **Asset types (YAML):** `table`, `column`, `join`, `few_shot`, `term`, `metric`, `rule`/`context`, `negative_example`; **markdown** for skills / gotchas / query-patterns. CI enforces reference integrity (`term→metric→column→table` all resolve) and regex IDs (`tbl_<domain>_<name>`, …). That check doubles as the curator's machine-checkable "done-enough" signal.
- **Column reliability is prose, not a flag.** No `decoy: true`. The curator writes a free-text **reliability caveat** ("UNRELIABLE: DO NOT USE" plus a reason) inferred from data evidence. *Same mechanism in BIRD and enterprise deployments.* In BIRD the decoy manifest lets us *grade* it (decoy-recall / decoy-touch); in the enterprise setting nobody knows ground truth, but the same inference runs. Transferability is the deciding reason.
- **BIRD scope is not only structure.** BIRD ships an `evidence` field (external-knowledge hints ≈ lightweight rules / derived metrics), so the curator also generates `metric`/`rule`/`term`/`context` for BIRD, seeded by evidence. These are scored end-to-end by EX (Arm 1 vs Arm 2), with **no per-asset gold** for those (gold stays limited to names, FK, and decoy-exclusions per D4). **Synonyms (`term`/`term_relationship`) are in-scope for BIRD too**: the obfuscation's *rewrite* dimension means one concept gets asked multiple ways, so synonym mappings aid paraphrase-robust retrieval. They're consumed via the dictionary engine or in-memory, still no Neo4j.
- **Graph is a projection (in-memory built; Neo4j deferred).** `join` (+ `term_relationship`, + metric/column lineage) project into a property graph. BIRD uses an **in-memory graph** (networkx) for Steiner-tree planning; **that projection and the Steiner join planner are built** (the planner cost model is a tunable heuristic). **Neo4j is an optional derived projection** for enterprise scale (and a stated learning goal), rebuilt from YAML by a loader, and stays deferred.
- **Alternatives:** custom DB-backed schema (loses git diff/PR/audit; authoring-in-DB breaks the source-of-truth invariant); typed decoy flag (not transferable to an enterprise deployment).
- Concretizes the **Markdown-first Storage** ADR; detail in [Architecture](architecture.md) §5, per-asset field spec in [Asset schemas](asset-schemas.md).

## D10: Curator = Proposer + Adversary

> **Decided (ADR-grade, 2026-07-07)**
>
> The curator is a **proposer and an independent adversary**, not one agent. The
> proposer hypothesizes Inference-tier assets and skills; the adversary tries to
> **refute** each before it is committed (`proposed → draft`). **Facts** (dtypes,
> uniqueness, samples) are generated **programmatically** and never checked. The
> adversary boundary *is* the Facts/Inference boundary. Full loop in
> [Curator](curator.md).

- **Alternative:** a single-agent curator (cheaper, but self-review is weak: a model rarely refutes its own plausible inference, and that's where owner-less layers silently rot).
- **Consequence:** dev = adversary is the only reviewer (auto-accept on pass); prod = automated first-line reviewer before human certification (D6). Proposer claim + adversary verdict both land in the asset `audit` block → the viz/audit surface.
- **Built:** the deterministic scaffold (programmatic Facts profiling, a `HeuristicProposer` for roles / confidence / provenance, an adversary `review` wrapping the CI validator, and a `curate` promote loop `proposed -> draft`), plus the **LLM proposer** (`LlmProposer`: authors descriptions + `suspect` reliability caveats over the heuristic, Facts untouched) and the **deepagents build harness** (`curator/deep_agent.py`, behind the `agents` extra: a deep agent over Facts-profiling + read-only-probe tools; construction verified offline, the autonomous run model-gated). Still seams: LLM authoring of joins / terms / metrics / rules / skills, the live per-asset adversary `refute`, and the self-eval train-EX loop; those are what make Arm 2 beat Arm 1. See [Curator](curator.md).
