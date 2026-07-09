# Agentic BI Architecture

Full design for the [Agentic BI System](system-overview.md). Terms are in the
[Glossary](glossary.md). The reasoning and alternatives behind each choice are
in [Design decisions](design-decisions.md).

## 1. Design Spine (Non-negotiables)

1. **Two planes.** A semantic/control plane holds business meaning as versioned config and markdown, published offline via PR/CI. It stays separate from a data plane that executes only guardrail-passed SQL. Meaning is defined once and owned by humans.
2. **Deterministic DAG + conditional routing, not autonomous ReAct.** The question can be wide, but the SQL must be narrow. Routing is hard-wired and auditable. Free exploration is confined to a controlled pocket.
3. **Fail-closed.** Out-of-scope, missing-coverage, or tripped-guardrail returns a refusal or a clarifying question, never a confident wrong number.

## 2. Two Harnesses over One Shared Substrate

Curator and server have opposite risk profiles. They use different harnesses but share everything below the loop.

| | Curator (build) | Server (serve) |
|---|---|---|
| Output | a durable artifact (the corpus) | an answer to a user |
| Checked by | a human, before it ships | nobody (the user can't verify) |
| Failure cost | cheap, recoverable | catastrophic (silent wrong answer) |
| Autonomy | maximum (explore) | minimum (fail-closed) |
| Harness | `deepagents` | `LangGraph` + middleware |

> **Curator = permanent maintainer**
>
> Not a one-time bootstrapper. Cold-start is its first job, but drift-repair is
> ongoing. Untended corpora rot (~95%→65% in a month per *How Anthropic enables
> self-service data analytics with Claude*). Full loop (proposer and adversary):
> [Curator](curator.md).

## 3. Kernel Primitives (Survive model improvement)

- **Governed gateway**: read-only, RLS-as-user, credential-isolated, forced LIMIT/timeout, audit/replay. It can access everything, but a context layer routes to governed datasets first. Ideally raw tables are never touched.
- **Agentic loop**: the permanent control loop.
- **Tools**: coded functions the model may call. Keep them few and sharp.
- **Hooks (middleware)**: deterministic code on loop events. `before_model` injects context (working memory, RLS scope, the semantic-layer router). `wrap_tool_call` gates or vetoes actions (AST allowlist, cost/EXPLAIN, PII, RLS). This is where fail-closed lives.

> **Engine vs fuel**
>
> The kernel is the engine. The **corpus is the fuel** the hooks deliver into
> the loop. As models improve, you delete tools and shrink hooks. You don't
> rewrite the kernel.

## 4. Four Shared Services

Fork only the harness, but share the substrate. Sharing has three directions, and they tell you where the contracts live:

- **Curator writes → server reads:** semantic layer, skills, metadata/indexes. Contract: publish/certify (versioned).
- **Server writes → curator reads:** audit log, corrections, episodic signals. Contract: harvest (closes the loop).
- **Both read one definition:** gateway policy, eval set and ground truth, identity/access model, tool registry, provenance format.

1. **Gateway service**: access, policy enforcement, and audit (one boundary, two permission profiles).
2. **Corpus service**: semantic layer, skills, metadata, and indexes (publish/read API, versioned).
3. **Memory service**: working, profile, episodic, and correction memory. Correction is the cross-agent channel.
4. **Eval / telemetry service**: ground truth and run history, the shared scoreboard.

## 5. Storage: Match Representation to Access Pattern (RVGD)

| Part of the corpus | Representation |
|---|---|
| Skills, reference docs, gotchas, procedural knowledge | Markdown (git, colocated with transforms) |
| Metric / dimension / rule definitions | Compiled config (MetricFlow / MDL / OSI-style) |
| Schema, joins, FK connectivity, lineage | Graph (FK graph → Neo4j at scale) |
| "Which doc / table / example is relevant?" | Vector index + BM25 |
| Memory (working/profile/episodic/correction) | Postgres + pgvector |

Markdown-first. The graph earns its place only for joins and lineage. A heavy LLM knowledge graph is deferred. Rationale: curation and structure beat representation sophistication. Anthropic's null result showed raw-corpus grep moved accuracy <1pt. See the *Data Agent Memory Design Overview*.

*Built today:* retrieval runs the pure-Python **BM25** lexical channel plus deterministic grounding over the corpus relationships, and a **vector / semantic channel** (embeddings, fused with BM25 via Reciprocal Rank Fusion) behind an injected `Embedder` seam, off unless an embedder is passed. The FK graph is the in-memory `networkx` projection that drives Steiner join planning; Neo4j stays the enterprise-scale projection. Model choices (the OpenAI `gpt-5.5` LLM and `text-embedding-3-small` embedder, both swappable) live in a project config file (`governed_bi.toml`, parsed by `config.load_settings`); the API key is read from the environment, never stored. The clients live in `governed_bi.llm` behind `ChatClient` / `Embedder` protocols, each with a deterministic offline default so the pipeline runs with no model or network.

> **Corpus contract = Git+YAML typed assets, curator-authored / human-audited (D9)**
>
> The "compiled config" row is realized as *《从数据到智能》*-style typed YAML
> assets (`table/column/join/few_shot/term/metric/rule`). The curator writes
> them; a human audits via the viz surface. **Git is the single source of
> truth. The graph (in-memory for BIRD, Neo4j as a derived projection for
> enterprise scale), vector, BM25 and Postgres are all rebuildable projections, never
> authored directly.** Column reliability is AI-inferred *prose* ("UNRELIABLE,
> DO NOT USE"), not a typed decoy flag, so the mechanism transfers to an enterprise deployment.
> See D9 in [Design decisions](design-decisions.md).

## 6. Runtime Query Flow (Server)

```
ask → supervisor → query understanding → intent route → SQL cache check →
RVGD retrieval → Steiner-tree join plan → SQL gen → five-layer guardrails →
execute (as-user) → answer + provenance
```

The full stage-by-stage design is in [Server](server.md), along with the three points where the curator's inference drives serve behavior.

> **SQL semantic cache fast path**
>
> Embed the question → cosine similarity ≥0.92 against the cached-SQL library →
> hit skips retrieval, planning, and generation, but **always re-executes** the
> cached SQL (freshness over latency; cache SQL text only, never results,
> matching D7's identity scoping). Miss → full pipeline, then write back to the
> cache on success. TTL 15 min. Single global threshold, a known gap that is
> not tuned per domain. See the *Data Agent Memory Design Overview* §5.

Guardrails, in order (fail-closed on any, all five enforced): syntax → policy blacklist → AST column allowlist → term-semantics → cost. The AST allowlist is scope-aware (resolves each column against its own query scope and blocks star projections); term-semantics licenses the retrieved tables plus their FK join-neighborhood and the join plan's Steiner points (not just the exact retrieved set, so it is decoupled from retrieval recall) and blocks cross-namespace table names. The cost layer is a structural cross-join guard for now; numeric EXPLAIN-based cost (Postgres / Redshift) is future per-dialect work. Stage-by-stage detail is in [Server](server.md) step 8.

> **Bounded self-repair (generation → guardrails → execution)**
>
> Generation, guardrails, and execution run as a bounded loop. A guardrail
> rejection or an execution error is fed back to the generator for another
> attempt rather than refusing outright; every attempt is re-guardrailed, so
> un-vetted SQL never runs. It stops early when the generator repeats a query
> (no progress) and fails closed after a small cap. A repaired answer is stamped
> `lineage`, not `governed`.

> **Refusal & best-effort (two concurrent gates, not a waterfall)**
>
> - **Refuse-gate** (curated negative examples): match → canned escalation blob (owner contact). This is the fail-closed path.
> - **Hard guardrails** (`wrap_tool_call`): can veto any query regardless.
> - **Best-effort otherwise:** governed → lineage → fenced-raw, with a **reliability stamp** (provenance tier plus the uncertainty flags that fired). The tiers are uncalibrated governance/uncertainty heuristics, tuned on the eval: `governed` means safe, in-scope, and no uncertainty flag, **not** verified-correct. The guardrails are a safety/governance gate, not a correctness oracle, so a plausible-but-wrong query (valid, in-allowlist, wrong computation) is caught here and by the fail-closed paths, not at a guardrail. Give the stamp teeth: low-reliability answers get differential handling.
> - **High-stakes** (leadership/PII): human sign-off or return-SQL-only.

## 7. Memory Policy

- Working memory: always on (session, identity-scoped).
- Episodic and correction: off by default. Adopted per-domain only when eval earns it, with value-aware retrieval when used.
- Durable memory is PR-gated exactly like the corpus → the memory/corpus distinction collapses. Correction memory ≈ correction-harvesting→PR-to-reference-doc. Promoted episodic ≈ gated few-shots. Only working/ephemeral memory is outside the gate.

> **Reusable numbers** (starting point; tune against BIRD-Obfuscation eval before adopting)
>
> | Parameter | Value |
> |---|---|
> | Working memory | session-scoped, cleared at session end |
> | Profile TTL | 365 days |
> | Episodic TTL | 90 days + 0.02/day decay |
> | Correction TTL | 180 days |
> | SQL cache TTL | 15 min |
> | Cache-hit gate | cosine ≥ 0.92 (see §6) |
> | Few-shot recall gate | cosine ≥ 0.95, confidence ≥ 0.9, fail_count ≤ 3 |
> | Route memory budget (Profile / Episodic / Correction) | nl2sql 5/2/5 · kpi_lookup 2/0/1 · knowledge_qa 3/1/1 · deep_analysis 8/8/4 |
> | Few-shot promotion gate | `pending_review` → human `approve` → retrieval-time threshold check |
>
> Source: the book's directly-reusable blueprint. See the *Data Agent Memory Design Overview* §5.

## 8. Evaluation

- Near-term: [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (4 DB versions, ~10k verified Q&A, decoy manifest, rename map) supplies verified ground truth. See *BIRD Bench Obfuscation Methodology*.
- Headline metric: execution accuracy vs gold. No hand-grading of semantic layers.
- **Split (adopt BIRD-Obfuscation's):** per-DB **80/20 seeded holdout**: 8,134 train / 2,030 test, all 69 DBs in both. The **curator reads `train_final.jsonl` only** (distilled, not dumped) → grade on held-out `test_final.jsonl`. Leakage is structurally prevented by the disjoint seeded split.
- **Variant:** the 3-arm semantic eval runs on the **`rename_decoy`** instance (cryptic names and live decoys, where the layer's value is maximal), with `base` as a sanity reference. The server always executes against the one physical DB. Only the corpus differs across arms.
- Three arms, all scored on EX: (1) no semantic layer, (2) curator-built, (3) gold semantic layer (auto-derived from manifest). **Moat = the share of the obfuscation-induced accuracy drop the curator recovers; arm 3 = the recoverable ceiling.** Arm 2 vs 3 = curator quality.
- Free behavioral signals from the manifest and logs: decoy-touch rate, governed-path adherence. Cost and efficiency (wall-clock, tokens, rows; BIRD's VES is reusable) are logged, not headline.
- **Refuse-gate eval:** a held-out **unanswerable** set, built from cross-DB and removed-coverage cases (auto-generated) plus a small hand-built out-of-scope set. Scored on **refusal accuracy** (refuses the unanswerable) *and* **false-refusal rate** (on the answerable test set). This is the precision and recall of refusal.
- **Repo boundary:** BIRD-Obfuscation produces validated data and manifests, and explicitly scopes out "the downstream agent that exercises the traps". That downstream agent is *this* system.
- Later: retrieval-at-scale eval on an enterprise-scale deployment (Recall@K / MRR / nDCG, % answered via semantic layer).

> **Eval gaps**
>
> BIRD is small/clean → does **not** test retrieval-at-scale. BIRD questions are
> all answerable → does **not** test the refuse-gate (need a held-out
> unanswerable set). **Stretch arm:** withhold the train split for a few whole
> DBs to test *zero-seed* cold-start (~69 unfamiliar "companies"). This is
> deferred, not built first.

## 9. Environments (Toggles, not architecture forks)

| Concern | Dev / test (BIRD) | Prod (enterprise) |
|---|---|---|
| Human gate | auto-accept corpus changes | PR + owner + CI on every change |
| Identity / RLS | single all-access identity | real user, RLS at gateway |
| Serving | one process + files + SQLite | stateless server fleet; curator as async jobs; gateway/corpus/memory/eval as services; graph DB; caches |

Bake in the abstractions now (identity object, gate, scoped memory/cache) so prod is a config flip, not a rewrite.
