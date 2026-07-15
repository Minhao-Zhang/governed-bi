# Agentic BI Design Decisions

_[English](design-decisions.md) · [简体中文](design-decisions.zh.md)_

Settled decisions D1-D16 for the [Agentic BI System](system-overview.md), with
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

> **Clarified (2026-07-09, external review): cold-start vs. seed-assisted.** The
> *engine* is designed for minimal-prior cold start (`{connection + a few
> known-good queries}`). The *current BIRD eval*, however, seeds the curator from
> the train split (gold SQL + `evidence` fields) — that is **seed-assisted
> semantic-layer growth**, not a handful-of-queries cold start. These are
> different claims. The repo's near-term proof is the seed-assisted one; a genuine
> minimal-seed eval (withholding whole DBs or nearly all Q/SQL supervision) is
> future work. Until it lands, **do not claim the BIRD train split demonstrates a
> cold start.** README positioning reworded accordingly.

> **Refined (2026-07-11): multi-schema, still not multi-tenant.** The engine now
> targets **one database holding many schemas**, including executable cross-schema
> joins and aggregations (**D15**). This widens the engine's reach but does *not*
> reverse "not a product": identity, RLS, and multi-tenancy stay toggled-off seams
> here (**D7**) and are adapted by an enterprise system, not built in this repo.

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
- The **SME-growth** dimension over these arms is **D14**: a point-estimate table across clarification rounds.

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
- **Built:** the fail-closed refuse-gate and a **two-axis reliability stamp** are implemented. The stamp reports two independent axes so neither is mistaken for the other: `safety_clearance` (guardrails + authorization passed — a *gate*, true for every delivered answer, false for every refusal) and `semantic_assurance` (`certified` / `heuristic` / `unverified` — how well-grounded the answer is, the axis that should drive automatic delivery). The legacy single-axis tier (`governed` / `lineage` / `fenced_raw` / `refused`) is kept as their 1:1 projection for compact display. A **bounded self-repair loop** feeds a *repairable* guardrail rejection (syntax + column/table scope — L3/L4 stay repairable [by decision, D11](#d11-external-review-2026-07-09)) or an execution error back to the generator before refusing; a repaired answer is `heuristic`, never `certified`. A **hard policy/DDL block (L2 `policy_blacklist`) fails closed immediately** — feeding it back would only coach the generator to evade the policy. Cache admission gates on `semantic_assurance == certified`, never on safety alone. The thresholds and the uncertainty-signal set are **uncalibrated heuristics**, to be tuned on the eval. `certified` / `governed` means safe + in-scope + no uncertainty flag fired, **not** "verified correct": the guardrails are a safety/governance gate, not a correctness oracle, so plausible-but-wrong SQL is caught by the stamp + fail-closed, not by a proof of correctness. **The `certified` label overclaims for a BI audience and is scheduled for rename + EX-calibration** — see [Audit dispositions (2026-07-15)](#audit-dispositions-2026-07-15), R2.

## D6: Ownership & Human Gate

> **Decided**
>
> Per-domain **named owner** certifies datasets / metrics / negative-examples.
> **Env-toggle:** test (BIRD) auto-accepts; prod (enterprise) requires PR + owner +
> CI behind every corpus change. Certification (blessing a *definition*) stays
> distinct from high-stakes answer sign-off (blessing an *answer*).

- **Built (scope):** this repo ships a **read-only** audit surface — the `viz.presenter` view models plus the optional `governed_bi.api` HTTP API — with the interactive UI as a separate project; interactive corpus editing and save-to-PR are **out of scope here** (generic git/PR + CI in dev, the enterprise app in prod). The repo owns the write *primitives* a downstream editor reuses: the asset schema, `corpus.serialize.write_corpus`, and `corpus.validate` + CLI (the CI gate). See [Viz](viz.md).
- **Extended by D12:** the **clarification protocol** adds curator-emitted questions and an `accept_answer` primitive on top of this gate, and keeps the interactive round-trip downstream.

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

> **Unchanged by D15 (2026-07-11).** Multi-schema serving adds **no** identity or
> RLS. The single all-access identity stays the dev/showcase default, and RLS
> stays a toggled-off gateway seam an enterprise system supplies. D15 spans
> *schemas*, not *users*.

## D8: Serve-time Memory

> **Decided (ADR-grade)**
>
> **Working memory always on** (session, identity-scoped). **Episodic /
> correction off by default**, adopted per-domain only when eval earns it,
> value-aware when used. **Durable memory is PR-gated exactly like the corpus.**

- **Reason:** more memory often hurts (EnterpriseMem-Bench: episodic swung +14pp to −16pp; retrieval biases the model). Working memory is the one universal win.
- **Consequence:** the memory/corpus distinction collapses. Correction memory ≈ correction-harvesting→PR-to-reference-doc; promoted episodic ≈ gated few-shots. One PR-gated corpus, not two governance models. See the *Data Agent Memory Design Overview*.
- **Built:** working memory is implemented as an in-process, session-scoped store (`InMemoryWorkingMemory`), the store the `before_model` middleware reads to inject prior context. Episodic and correction memory are **off-by-default protocol seams** (`EpisodicMemory` / `CorrectionMemory`), not implemented, consistent with "adopt per-domain only when eval earns it". **Profile** memory — the 4th store in Architecture §7's blueprint — exists only as config (a route budget + `profile_ttl_days`); it has no store protocol seam yet and is the lowest-priority durable store.

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

- **Asset types (YAML):** `table`, `column`, `join`, `few_shot`, `term`, `metric`, `rule`/`context`, `negative_example`; **markdown** for skills / gotchas / query-patterns. CI enforces reference integrity (`term→metric→column→table` all resolve) and regex IDs (`tbl_<schema>_<name>`, …). That check doubles as the curator's machine-checkable "done-enough" signal.
- **Column reliability is prose, not a flag.** No `decoy: true`. The curator writes a free-text **reliability caveat** ("UNRELIABLE: DO NOT USE" plus a reason) inferred from data evidence. *Same mechanism in BIRD and enterprise deployments.* In BIRD the decoy manifest lets us *grade* it (decoy-recall / decoy-touch); in the enterprise setting nobody knows ground truth, but the same inference runs. Transferability is the deciding reason.
- **BIRD scope is not only structure.** BIRD ships an `evidence` field (external-knowledge hints ≈ lightweight rules / derived metrics), so the curator also generates `metric`/`rule`/`term`/`context` for BIRD, seeded by evidence. These are scored end-to-end by EX (Arm 1 vs Arm 2), with **no per-asset gold** for those (gold stays limited to names, FK, and decoy-exclusions per D4). **Synonyms (`term`/`term_relationship`) are in-scope for BIRD too**: the obfuscation's *rewrite* dimension means one concept gets asked multiple ways, so synonym mappings aid paraphrase-robust retrieval. They're consumed via the dictionary engine or in-memory, still no Neo4j.
- **Graph is a projection (in-memory built; Neo4j deferred).** `join` (+ `term_relationship`, + metric/column lineage) project into a property graph. BIRD uses an **in-memory graph** (networkx) for Steiner-tree planning; **that projection and the Steiner join planner are built** (the planner cost model is a tunable heuristic). **Neo4j is an optional derived projection** for enterprise scale (and a stated learning goal), rebuilt from YAML by a loader, and stays deferred.
- **Alternatives:** custom DB-backed schema (loses git diff/PR/audit; authoring-in-DB breaks the source-of-truth invariant); typed decoy flag (not transferable to an enterprise deployment).
- **Namespace field renamed `db` → `schema` (D15, 2026-07-11).** The per-asset namespace historically named `db` always denoted a *schema* (one YAML subtree per namespace); it is renamed `schema` everywhere. IDs are unchanged — they already embed the namespace (`tbl_<schema>_<name>`) — so the rename is a projection fix, not an identity change. See **D15**.
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
- **Built:** the deterministic scaffold (programmatic Facts profiling, a `HeuristicProposer` for roles / confidence / provenance, an adversary `review` wrapping the CI validator, and a `curate` promote loop `proposed -> draft`), plus the **LLM proposer** (`LlmProposer`: authors descriptions + `suspect` reliability caveats over the heuristic, Facts untouched) and the **deepagents build harness** (`curator/deep_agent.py`: a deep agent over Facts-profiling + read-only-probe tools; construction verified offline, the autonomous run model-gated). Still seams: LLM authoring of joins / terms / metrics / rules / skills, the live per-asset adversary `refute`, and the self-eval train-EX loop; those are what make Arm 2 beat Arm 1. See [Curator](curator.md).

## D11: External review (2026-07-09)

Raised by an independent project review (2026-07-09). Recorded here so each item is settled deliberately, not by default. Several items are already reconciled above — the cold-start wording in D1, and the two-axis stamp + L2-immediate-refuse in D5. The remaining items below.

- **Repair by failure class (L3/L4 boundary) — DECIDED (2026-07-09): keep repairable.** `policy_blacklist` (L2) fails closed without a retry (feeding a DDL/policy block back only coaches evasion). The question was whether **column-allowlist (L3) and term-semantics (L4) scope failures should also refuse immediately** (the review's position: coaching a model past a scope block is pressure to find a query that passes while staying analytically wrong). **Decision: L3/L4 stay repairable.** The FK-neighborhood widening + repair loop is a deliberate *false-refusal-reduction* mechanism for retrieval under-recall, a repaired answer is already `heuristic` (never `certified`), and the attempt cap + no-progress guard bound the loop. Revisit if the live eval shows repair coaching inflating plausible-but-wrong answers.
- **`CorpusRelease` — an immutable, certified serving contract.** Git is the source of truth, but source control alone is not a *publication* contract: the server does not currently distinguish draft from certified assets at serve time, pin a corpus content hash, or record a release in each answer. Proposed: a `CorpusRelease` artifact (version + content hash + certified asset IDs + build/adversary evidence + timestamp); the curator writes only staging, CI builds a release, the server reads only a pinned release, every answer/audit event records the release hash. **Scope question:** a lightweight release hash + server pin is arguably an engine-level serving-correctness primitive (in scope here); the full "CI builds release, owners approve" workflow is product (enterprise-fork scope). **Decision pending**, but **partly addressed by D13**: for the benchmark, a separate corpus repo plus a git-SHA-per-checkpoint pins corpus state and defers the full release artifact.
- **Structured-intent SQL cache.** The semantic cache currently keys on an embedding-similarity gate. The review notes a global cosine threshold is a weak equivalence test (two questions can differ in period, denominator, entity, or metric). Proposed: key on structured intent (`corpus_release_hash + identity scope + metric ID + normalized dims/filters + join-plan fingerprint + policy version`), or restrict to exact normalized-query caching until that exists. The cache is off by default, so this is not urgent. **Decision pending.**

## D12: Clarification Protocol

> **Decided (ADR-grade, 2026-07-11)**
>
> The curator records what it does not know as **clarification questions**
> attached to the corpus asset they concern, on the never-served **Audit** tier. A
> pluggable **Responder** answers them in free text (a human **SME** in production,
> a **Simulated SME** in eval), and a parse step (the curator/LLM, or a data
> engineer) turns each answer into a structured edit committed to git via
> `accept_answer → write_corpus → validate`. SMEs never open a PR. A CSV or Excel
> sheet is only a rendering of the open questions, never the ingestion path. The
> engine gains exactly two primitives: a typed `Clarification` block and
> `accept_answer`. The Responder and the round-trip stay downstream.

- **Alternatives:** putting the whole loop inside the curator package (this reopens the D6 / 2026-07-08 engine-vs-product boundary); a standalone clarification ledger keyed independently of assets (this loses the natural asset attachment, needs its own store, and duplicates git).
- **Consequence:** while a question is open, the curator's provisional guess uses the existing Inference tier at low `confidence` plus a `suspect` reliability caveat, so an unanswered asset still serves a best-effort answer with an honest stamp. This extends the **D6** human gate, and a Responder's supporting "resources" land as `source_refs`. An asset-attached question cannot express a "missing entity" question such as "is there a returns table?"; that case is deferred.

## D13: Semantic Layer as Its Own Repository

> **Decided (ADR-grade, 2026-07-11)**
>
> The corpus (the semantic layer) lives in its **own git repository**, separate
> from the engine. The engine loads it by path (`[paths].corpus_root` in
> `governed_bi.toml`, optionally overlaid by `governed_bi.local.toml`), and
> `load_corpus` already reads every `<db>/` subtree, so a multi-DB corpus needs no
> engine change. That repo's **git history is the source of truth and the
> benchmark's checkpoint pin**: checkpoint N is the commit SHA after batch N. The
> same shape generalizes, since each deployment gets its own corpus repo that the
> engine points at.

- **Alternatives:** keeping the corpus vendored in the engine repo (this couples per-deployment data to engine releases and cannot track many deployments); building the full `CorpusRelease` machinery now (premature, per D11).
- **Consequence:** this concretizes **D9** (git is the source of truth) and **defers D11 `CorpusRelease`**, since the immutable hash-pinned *serving* release is a separate, later concern. The engine's `corpus/beer_factory/` stays a worked-example fixture for tests.
- **Renamed by D15 (2026-07-11):** the `<db>/` subtree is now `<schema>/`; each deployment's corpus repo holds the schemas of its one database. `load_corpus` reads every subtree unchanged.

## D14: SME-growth Benchmark on BIRD-Obfuscation

> **Decided (2026-07-11)**
>
> The corpus-as-moat claim is shown as a **point-estimate table**, not a fitted
> curve: `no-layer` (the baseline floor), `facts-only` (the auto-profiled start),
> after SME round 1, and after round 2, with `gold` as an optional reference row.
> A "round" is one batch of clarification questions answered. The curator learns
> from **train gold SQL plus the question** (D1's seed-assisted reading), so joins
> come from example SQL. The **Simulated SME** is an LLM briefed with the dataset's
> *domain meaning*, answering one question at a time, and **never handed a
> held-out test question's gold SQL** (the one leakage invariant). Serve-time
> compute is held identical across arms, and SME or curation effort is the
> training-time axis. Run **beer_factory first** to prove the mechanism, then pool
> across DBs for a credible number.

- **Alternatives:** a fitted learning curve with fine checkpoints (more compute, and it needs pre-registered breakpoints plus snapshot pinning, so it is deferred as unnecessary for a first result); a CI-enforced file-access firewall for the Simulated SME (rejected as over-complicated, since a careful prompt suffices and residual leakage is accepted and documented).
- **Consequence:** this refines **D4**'s three arms with a growth dimension. Small-N noise (26 test questions on beer_factory) and a possible collapse toward the **gold** reference are accepted, documented limitations, since gold is a reference line, not a ceiling. Pooling across the 69 BIRD DBs, via **D13**'s multi-DB corpus repo, is what makes the table credible.
- **Cross-schema is out of grading scope (D15).** BIRD's 69 db_ids are independent databases with no cross-db relationships, so cross-*schema* serving is un-graded by this benchmark. The table measures within-schema growth (and, at scale, schema-routing); cross-schema correctness is an accepted, separately-tested limitation. See **D15**.

- **Amendment (2026-07-15) — the arm ladder and a redefined ceiling.** The
  benchmark is run as a **train + test** measurement at each stage, and the
  headline is the **train↔test gap** — train accuracy alone is contaminated when
  few-shots are distilled from train pairs, so the gap (not raw train accuracy) is
  what measures generalization. The ladder:
  1. **facts-only** — bare-minimum metadata (the floor).
  2. **autonomous curator** — the agent explores and self-curates with **no SME
     answers**; isolates what the agent recovers *alone*.
  3. **SME (train-bounded), by round** — a **Simulated SME** with access to
     **training questions + evidence only** answers clarifications; measured after
     each round (r1, r2, …). The growth axis.
  4. **test-aware SME oracle (the ceiling)** — a Simulated SME that has seen the
     held-out **test questions + their evidence hints (never the test gold SQL)**
     in its retrieval index. A **deliberately-leaky oracle**, walled off from the
     fair arms (1–3) and reported only as the dashed
     "recoverable ceiling" line. **Replaces the retired de-obfuscation gold arm**
     (see [Audit dispositions → R-gold](#audit-dispositions-2026-07-15)), which was
     never a true ceiling.
  Two properties make this ceiling informative. It is **< 1.0 by design** (the
  agent still mis-generates SQL under perfect knowledge), so it decomposes the
  result: `1.0 − ceiling` = the agent's irreducible SQL-gen error; `ceiling − SME`
  = the test-relevant knowledge a train-bounded SME cannot reach. The ceiling is
  **elicitation-bounded** (see the SME design below), so it is a *practical* upper
  bound — best achievable given the curator's questions plus an SME who can see the
  test bank — not the theoretical maximum. Deferred implementation.
- **Simulated SME design (2026-07-15).** Knowledge transfer is **pull-based**: the
  curator must *ask* — the SME is a strictly reactive **Responder** that answers,
  says it doesn't know, or adds *tightly-related* context, but never proactively
  dumps the corpus. So the curator's **questioning ability is itself part of what
  the fair arm (A3) measures** — and the ceiling stays elicitation-bounded, because
  it does not change this.
  - **Mechanism = retrieval tools, not a stuffed brief.** The SME gets a
    BM25/regex tool + a vector-search tool over the question bank (questions +
    evidence + SQL), plus the existing read-only `run_probe_query`. This replaces
    `build_sme_brief`'s dump of *all* train evidence into the system prompt, which
    does not scale to 69 schemas / 8k questions.
  - **Index scope is the fair↔ceiling knob *and* the leakage boundary.** The
    **fair** SME indexes **train only** (questions + evidence + SQL); the
    **ceiling** SME's index additionally holds the **test questions + test
    evidence — but never test gold SQL**. The invariant is enforced at index-build
    time (topology, not trust): test SQL that is never indexed cannot leak.
  - **This makes the SME train-SQL-aware — a deliberate role change.** Previously
    only the curator read train SQL; now the SME can too. Its answers stay
    domain-shaped (prose, no query recipes) via `_sanitize_sme_answer` + the
    *reusable-asset-only* fold (SME answers may become descriptions / terms / joins
    / metrics / rules / reliability caveats, never a test-question few-shot).
  - **Validity caveat to measure: train↔test near-duplicate inflation.** Because
    the ceiling SME reads train SQL and sees test questions, a test question with a
    near-twin in train lets the SME surface the twin's pattern — so the ceiling
    partly reflects **train-test question similarity**, not pure semantic
    recoverability. Reuse the same BM25/vector to report the train↔test similarity
    distribution (or dedup near-twins) alongside the ceiling number.
  - **Reliability voiced naturally, never "decoy".** The SME simulates a real
    expert's reliability knowledge in plain terms ("that column is unreliable for
    revenue — prefer `net_total`") and must never use benchmark words like *decoy*
    / *trap*: naming the obfuscation construct is unrealistic and a subtle leak
    toward the de-obfuscation key.
  - **Touch-points:** `curator/sme.py` — replace the stuffed brief with the two
    retrieval tools, scope the index by split, drop the "decoy or trap" instruction
    in `_SME_SYSTEM_RULES`, and add a no-"decoy" guard beside
    `assert_brief_no_leakage`.

## D15: Multi-Schema Serving (one database, many schemas)

> **Decided (ADR-grade, 2026-07-11)**
>
> A run connects to **one database** that holds **many schemas**, each with its
> own tables. Relationships are common *within* a schema and also allowed
> *across* schemas, and cross-schema joins and aggregations are **executable** on
> the single engine via fully-qualified `schema.table` SQL — this is not
> federation. The database is a **connection-config constant**, not a modeled
> corpus level: the corpus models **schema → table** (two levels, not three).
> **Identity / RLS / multi-tenancy stay out of this repo** — the toggled-off
> gateway seam (**D7**) is retained and an enterprise system adapts it. The corpus
> namespace field historically named `db` is renamed **`schema`** everywhere;
> asset IDs are unchanged because they already embed the namespace
> (`tbl_<schema>_<name>`), so this is a projection fix, not an identity change.

- **Cross-schema relationships are curated, never discovered.** A cross-schema edge exists only as a memory/corpus-sourced `join` asset — SME-declared, distilled from example SQL, or mined from usage — and is **never** probed from database foreign keys or guessed from column names. This is how every governed semantic layer works (dbt MetricFlow, LookML, Cube, Malloy are all closed-world and declared-join-only; missing-FK guessing is the top failure mode in benchmarks like Spider 2.0). The honest consequence: on a fresh database the engine answers within-schema questions immediately, but **cannot answer a cross-schema question until a relationship is curated for it** — with no declared cross-schema join, it **refuses and escalates** rather than invent one. This is the textbook "curation beats accumulation" asset (the database will never reveal `crm.customer ↔ sales.orders`; an SME will) and it grows through the **D12** clarification loop.
- **Qualification is mode-conditional, to protect the graded path.** The single-schema path (SQLite, i.e. the BIRD eval) keeps emitting **bare, unqualified** SQL byte-for-byte — SQLite cannot resolve `schema.table`, so qualifying it would break execution accuracy on the one arm we grade. Only the multi-schema path (Postgres / Redshift) qualifies, so **cross-schema is a Postgres/Redshift-only capability for v0**, which lines up with its being un-graded by BIRD (below). `DataSourceConfig` distinguishes three modes — *SQLite-single*, *Postgres-pinned-single-schema*, *Postgres-span-all* — by an explicit signal, never by `schema is None` (SQLite already runs with `schema=None`).
- **The guardrail becomes schema-qualified and remains the sole table-scoping gate.** Retrieval and the L4 license scope span all schemas: a **schema router** shortlists the relevant schemas, then expands **along curated joins** so a bridge table sitting in a third schema is not dropped (a similarity-only shortlist would cause *spurious* refusals indistinguishable from the honest one above). The L4 allow-set becomes fully-qualified `schema.table` membership; a bare reference resolves only to a designated default schema and is **refused as ambiguous** when the licensed set holds that name in more than one schema — this is what forbids a self-authorized off-scope schema. L3 keys become three-part `schema.table.column`; L5 union-find keys on `schema.table`. The licensed *id* set was already schema-correct (IDs embed the schema), so this is a projection fix. Read-only, the forced row cap, and the statement timeout are untouched — they live in the connectors, not the guardrail — and `search_path` is **not** used (L2 forbids `Command`); full qualification is the mechanism.
- **Alternatives:** a true three-level `connection → schema → table` model with cross-connection federation (rejected — one engine cannot join across physical connections; federation is a warehouse concern); auto-discovering cross-schema joins from FK metadata or name heuristics (rejected — cross-schema FKs rarely exist, and guessing them is the dominant error mode in FK-less settings); unconditional qualification (rejected — it breaks the SQLite/BIRD graded path).
- **Consequence:** refines **D1**'s target (multi-schema-capable within one database, tenancy still out) and **D9**'s corpus contract (`db` → `schema`; the `<db>/` subtree becomes `<schema>/`). **Cross-schema serving is un-graded by BIRD** (**D14**), an accepted, documented limitation covered instead by guardrail unit tests, a two-schema Postgres integration fixture, and a CI check for `(schema, physical_name)` uniqueness and non-ambiguous allow-set keys. **Status: building in verified increments (from 2026-07-12).** Shipped: increment 1, the gateway foundation (span-all connector + `multi_schema` config); increment 2, the schema-qualified guardrail (L3/L4/L5 keyed on `schema.table`); increment 3, multi-schema as the Postgres/Redshift **serve default** (qualified SQL-gen + guardrails wired; SQLite stays single-schema for BIRD); increment 4, **missing-edge refusal** (cross-schema retrieval with no curated `JoinAsset` refuses before generate, with a D12 `clarification_hint`); increment 5, **API wire rename** (`db` → `schema` on presenter/OpenAPI responses and `?schema=` filters only — no `?db=` alias; graph **nodes** carry `schema`); increment 6, **server-side graph scoping** (`?schema=` / `focus` / `radius` / `node_budget` on `/graph` and `/knowledge-graph`, plus KG `kinds=`, with `boundary` + `meta.scope` envelope; param-less remains the full graph); increment 7, **on-disk YAML rename** (`TableAsset` / `FewShotAsset` / skill frontmatter field `db` → `schema`; `load_corpus`/`write_corpus`serve always loads every `corpus/<schema>/` subtree); and increment 8, **join-aware schema router** (BM25 schema shortlist + curated cross-schema join expansion before RVGD on the multi-schema path; single-schema/SQLite unchanged). Still deferred: server `/search` (client Fuse remains default per Q6), and collapsing `DataSourceConfig.db` (BIRD db_id / default write subtree) into the pin field. The LLM coarse-to-fine pruning pass stays deferred behind the pluggable generator seam.

## D16: Governed Agentic Serve Core

> **Decided (ADR-grade, 2026-07-13; cutover landed 2026-07-14).** Full rationale,
> invariants, and phased migration in
> [ADR 0002](adr/0002-governed-agentic-serve-runtime.md); the historical
> agent-vs-flow A/B (flow now deleted) is summarized in
> [three-arm results](plans/three-arm-experiment-results.md).
>
> Serve is reworked from a deterministic single-shot DAG into a **governed
> agentic core**: an outer deterministic `StateGraph` (thin governance rails)
> wrapping an inner bounded `create_agent` reasoning loop over **read-only,
> guardrailed tools**. This **reverses** the prior "serve stays a deterministic
> DAG, never an autonomous ReAct loop" invariant (pipeline-design §8) and
> replaces it with **"serve's *authority* is deterministic; its *reasoning* may be
> agentic."** Autonomy is granted for *how to find the answer*, never for *what may
> execute*, *what is trusted*, or *what goes unrecorded*.

- **Governance moves from convention to construction.** Every data touch passes
  through `AgentMiddleware` (`wrap_tool_call` normalizes → runs the L1-L5 guardrail
  → writes an **append-only audit ledger** entry; `wrap_model_call` scopes tools by
  identity). The agent never calls the gateway directly and never sets its own
  stamp: `safety_clearance` / `semantic_assurance` are computed by deterministic
  `finalize` code from what actually happened. Enforcement and audit share one
  interception point, so you can never execute (or refuse) without a record.
- **Rollout cut over at P2; the agentic core is now the only serve path.** The
  `agent_serve` flag, used to gate the agentic path behind the deterministic
  flow during P0/P1, is **removed**; there is no toggle, and serve always runs
  the agent. `TemplateSqlGenerator` (serve), the `flow.py` monolith, and the
  stale `server/graph.py` are **deleted**, and an LLM key is now mandatory: with
  no live model, serve fails closed at startup and `/chat` returns `503`.
  CI/offline determinism runs on a `FakeListChatModel` agent harness; equivalence
  tests moved from "same `Answer`" to "same governance invariants."
- **Relation to D5.** D5's **bounded self-repair loop** becomes the agent's
  tool-reflection loop, with the `run_query` attempt cap (=3) enforced in
  `wrap_tool_call` instead of a hand-rolled `while` loop. D5's **invariants are
  preserved**: the refuse-gate still runs before the agent; safety stays hard
  (L2 policy blocks are non-repairable hard stops, mirroring `_NON_REPAIRABLE_LAYERS`);
  the two-axis stamp is unchanged with `safety_clearance` binary-hard and only
  `semantic_assurance` graded; the gold-leakage boundary and pinned-corpus serving
  (D11/D13) hold.
- **Alternatives:** keep the 1-node wrapper (no observability/retry/HITL, blind
  generation persists); a hand-wired StateGraph tool loop instead of
  `create_agent` + middleware (unnecessary bespoke wiring once middleware was
  verified to enforce the guardrail at the tool boundary); keep the deterministic
  template path in parallel (the same two-implementations drift trap). See ADR 0002.
- **Status: Implemented; P2 cutover landed on `main` (commit `d2fdd6a`, 2026-07-14).**
  Mechanism verified by a 2026-07-13 spike (`wrap_tool_call` state updates +
  ledger writes on the pinned stack); Amendment 1 adds a deterministic `assemble`
  node that seeds the agent with the curated semantic layer so it cannot regress
  below the flow.

## Audit dispositions (2026-07-15)

> **Reviewed.** An internal architecture audit (`audit-2026-07-15.html`, EN +
> `audit-2026-07-15.zh.html`) raised nine findings. Dispositions below; each
> refines an existing decision rather than opening a new axis. Findings are
> referenced by their audit IDs (R1–R9).

- **R1 — Seed variance is resolved by scale, not by more seeds on small DBs
  (refines [D4](#d4-grading) / [D14](#d14-sme-growth-benchmark-on-bird-obfuscation)).**
  The single-seed fragility of the headline EX number is addressed by running the
  SME-growth benchmark at full scale: all 69 BIRD DBs loaded as 69 Postgres
  schemas, **8,134 train / 2,030 test** (see [architecture — Eval](architecture.md)).
  With N≈2,030 held-out test questions, per-arm EX becomes statistically stable and
  a 23-question single-DB delta stops being the unit of evidence. *Status: planned,
  gated on the multi-schema experiment ([D15](#d15-multi-schema-serving-one-database-many-schemas)).*

- **R2 — The `certified` label overclaims and will be renamed + calibrated
  (refines [D5](#d5-refusal--best-effort)).** `semantic_assurance = certified`
  reads to a BI user as "verified correct," but it means only "safe + in-scope +
  no uncertainty flag fired." Accepted: rename to a grounding-oriented term (e.g.
  `grounded`) and **calibrate the thresholds and uncertainty-signal set against
  actual EX** once the scale run exists — i.e. measure what fraction of stamped
  answers are in fact correct. *Status: accepted, scheduled (needs the scale run
  first).*

- **R3 — User-feedback loop: discussed 2026-07-15; direction set, build deferred
  (refines [D8](#d8-serve-time-memory)).** Outcome of the design session:
  - **Purpose = evaluation + development, not personalization.** *Evaluation:*
    recorded interactions become a production quality signal run against metrics —
    the only window into live correctness beyond offline BIRD EX. *Development:*
    interactions are mined **passively** for semantic-layer improvement (e.g. a
    user rephrasing a near-identical question, or correcting an answer). *Not
    personalization:* per-user tailoring is rejected — engine not product (D1),
    and it invites the degenerate-feedback-loop failure the source flags.
  - **Feedback is a validated hypothesis, never a direct edit.** A signal is run
    against the query/result to test whether it actually improves the semantic
    layer, and only then enters the corpus **via the existing PR-gated path**
    (`memory/store.py`: "one PR-gated corpus, not two governance models"). No
    auto-learning, no parallel store — this is the guard against the degenerate
    loop (audit R2/R6 concern).
  - **Capture-first, interpret-later.** Record all interaction types now (glossary:
    **Interaction signal**, with **Correction signal** the high-trust subtype);
    defer the trust-tiering/interpretation logic until real usage shows what
    correlates with a wrong answer.
  - **v0 mechanism = Langfuse + LangSmith; a dedicated interaction log is deferred.**
    No backend/UI captures user interactions today, so a first-class interaction
    log is not yet feasible; use the tracers' feedback/scores API on the per-turn
    trace for what can be captured now. A dedicated, queryable, vendor-independent
    **interaction log** (keyed by turn + `corpus_release_hash`) is required future
    work — part of a broader internal-systems / backend build, and a soft
    dependency on CorpusRelease (D11).
  *Status: direction set; v0 rides Langfuse/LangSmith; the dedicated interaction
  log + mining pipeline are deferred to the internal-systems build.*

- **R4 — Live execution: the audit note was stale; corrected.** Postgres **is**
  run live. `eval/run_experiment.py` executes the three arms against a local
  Postgres (BIRD-Obfuscation `pg_rename_decoy`, `127.0.0.1:5435`) with a live
  model — the working daily eval path, not an offline double. Connector docstrings
  (`gateway/connectors/{base,__init__}.py`, `gateway/__init__.py`), `usage.md`,
  and `system-overview.md` are corrected accordingly. **Redshift** remains
  genuinely unverified against a live cluster. Sub-points on the "one defensible
  run" milestone: **(4.1) live-model runs** — already happening (see above);
  **(4.2) the gold reference arm** — see R-gold below.

- **R-gold — Gold reference arm: resolved 2026-07-15 — de-obfuscation oracle
  retired; the ceiling is redefined as a *test-aware SME oracle* (refines
  [D4](#d4-grading) / [D14](#d14-sme-growth-benchmark-on-bird-obfuscation)).** The
  `gold.py` de-obfuscation oracle (rename-map read-back) is **not a ceiling** — by
  its own docstring, Arm 2's skills can exceed it — and on the identity-rename DBs
  it is a near no-op. It is **retired**. The recoverable ceiling is redefined as a
  **test-aware SME oracle**: a **Simulated SME** that holds the held-out **test
  questions + their evidence hints (never the test gold SQL)** in its retrieval
  index. This is a genuine upper bound on the SME axis,
  because the test set touches knowledge the train pairs never surface — a
  train-bounded SME structurally cannot reach it. Full ladder + properties
  recorded as the [D14 amendment (2026-07-15)](#d14-sme-growth-benchmark-on-bird-obfuscation).
  Follow-ups (deferred, decision-only now): delete `eval/gold.py` + the `Arm.gold`
  member (dead), and give the SME **BM25/vector retrieval tools scoped by split**
  (train-only = fair; + test questions/evidence, never test SQL = ceiling — see the
  [D14 amendment](#d14-sme-growth-benchmark-on-bird-obfuscation)).
  *Status: decided; implementation deferred.*

- **R5 — Cost / latency / token observability: the substrate already exists via
  Langfuse + LangSmith (refines [D16](#d16-governed-agentic-serve-core)).** Both
  tracers are wired ([`obs.py`](../src/governed_bi/obs.py)); both capture per-trace
  tokens, cost, and latency and aggregate them in their own dashboards, and an
  agentic turn groups as one trace. The gap is **not capture** but that tracing is
  env-opt-in (off in the CI/offline profile) and not surfaced in the product's own
  governance view. *Status: rely on Langfuse/LangSmith for now; a native
  aggregate/monitoring/alerting view is future work (deferred).*

- **R6 — Curator adversary `refute()` + self-eval/repair loop: deferred
  (refines [D10](#d10-curator--proposer--adversary)).** Confirmed unbuilt;
  `refute()` is `NotImplementedError` and the structural `review()` remains
  signal-only in `curator/pipeline.py` (annotates audit notes, docks confidence,
  never gates). Explicitly deferred for now. *Status: deferred.*

- **R7 — The refuse-gate is untested by the BIRD EX number — documented eval
  limitation (refines [D5](#d5-refusal--best-effort) /
  [D14](#d14-sme-growth-benchmark-on-bird-obfuscation)).** BIRD questions are all
  answerable, so the three-arm EX metric never exercises the refuse-gate and the
  **false-refusal rate is unmeasured** by it. The held-out unanswerable set (D5) is
  the separate instrument — minimally covered today (a small hand-built
  beer_factory set) and skipped in CI (needs a live model). This is an accepted,
  documented limitation of the current eval; closing it is part of the scale run.
  *Status: documented limitation.*

- **R8 / R9 — Design fragmentation and egress governance: no change of position.**
  R8 (two clarification representations, two curator orchestration paths, orphaned
  `.pyc`) is a maintainability signal to clean up opportunistically, not a decision.
  R9 (egress/privacy — "send everything," ADR 0002 Q5) remains deferred to the
  enterprise-fork scope. *Status: unchanged (deferred / cleanup).*
