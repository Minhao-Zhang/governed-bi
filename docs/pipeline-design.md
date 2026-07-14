# End-to-End Pipeline Design: Curator → Serve

_Status: agreed 2026-07-12. Supersedes the "refuse on undeclared cross-schema
join" behavior of **D15** and extends the reliability model of **D2**. The
decisions marked **(ADR candidate)** below should graduate to numbered ADRs
(D16+) in [design-decisions.md](design-decisions.md) once code lands._

> **§5 and the §8 "deterministic DAG / never ReAct" invariant are superseded by
> [ADR 0002](adr/0002-governed-agentic-serve-runtime.md).** Serve is being
> reworked into a *governed agentic core*: its **authority stays deterministic**
> (middleware-intercepted read-only tools + a deterministic reliability stamp),
> but its **reasoning may be agentic** (a bounded `create_agent` loop). The safety
> invariants below (L2 + refuse-gate hard, two-axis stamp, gold-leakage boundary,
> pinned corpus) are preserved.

This document records the intended end-to-end shape of the system as agreed in
design discussion. It is the target, not a description of current committed
code; a **Delta from today** section at the end lists what still has to be
built. It deliberately does not include the ad-hoc uncommitted code currently in
the working tree.

## 0. The two phases

The system has a **build-time curator** (offline, exploratory, LLM-heavy) and a
**serve-time inference path** (online, deterministic, guardrailed). They share
one artifact: the **corpus** (the governed semantic layer). The corpus is
authored by the curator and consumed, read-only, by serve.

## 1. Governance model: PR gate + version pinning, not an inline adversary

**(ADR candidate.)** Governance lives at the **batch boundary**, not per-asset
at write time.

- During a curator run we are building **from a blank corpus**, so the curator
  may modify the local corpus **freely** — writing a corpus asset is *not* a
  commitment to trust it.
- Trust is established by a **human engineer who reviews the whole batch at the
  end of the pipeline**, and by a **PR gate**: production only ever advances to a
  corpus state that has been merged.
- **Inference always reads a pinned git hash of the corpus**, never the live
  working copy. "Edit at will" is therefore a local-development property only;
  production is always pinned to a reviewed, merged revision.
- Consequence: the existing **adversary** (Facts/Inference refutation) is
  **demoted from a hard gate to a signal**. Its refutations no longer block a
  write; instead they become inputs to the reliability score (§5). Facts remain
  deterministic; Inference remains labeled as inferred, but is not gated inline.

Rationale: an inline adversarial gate is the right tool when a corpus grows
incrementally against a trusted baseline. When cold-starting from blank with a
human reviewing the entire output before it can ship, the human + PR + pinning
boundary is the cheaper and stronger guarantee, and it frees the curator to be
genuinely exploratory.

## 2. Phase A — Deterministic ingestion (Facts)

Already exists: `curator/profile.py::profile_database` →
`curator/build.py::build_facts_corpus` / `build_facts_all_schemas`.

- Connect to a bare-minimum database, iterate **every schema and table**, and
  write **Facts-tier** `TableAsset`/`Column` entries: physical name, physical
  type, logical type, nullable, PK-uniqueness, bounded sample values. No LLM.
- **Explicitly does not** discover foreign keys or join edges. Join *existence*
  is inferred (see §3), never ingested from catalog constraints. Phase A gives
  you tables and columns, not how they connect.

## 3. Phase B — Curator (batch, deep-agent, exploratory)

**Inputs:** the Facts corpus from Phase A + a **batch of known-good
`(question, gold SQL)` pairs**.

- The curator is a **deep agent** (the `deepagents` scaffold in
  `curator/deep_agent.py`), given tools to profile facts and run probe queries.
  It explores the batch and the database and proposes **Inference-tier** assets
  (descriptions, grain, joins, terms, metrics, rules, few-shots).
- **Deterministic join extraction from gold SQL.** Because the input SQL is
  known to work, parsing its `JOIN ... ON` equality predicates (sqlglot AST)
  yields strong evidence for join edges. These are proposed as `JoinAsset`s —
  still Inference-tier and still subject to human review, but the cheapest,
  strongest bootstrap signal for the join graph. This is how §2's "joins are
  inferred" is reconciled with "a batch of working SQL tells us most of the
  joins."
- The agent **proposes in all the ways it can**; per §1 nothing here is a
  trusted commitment — the engineer review + PR gate is what promotes it.
- While exploring, the curator **records the clarification questions it cannot
  answer from the batch + facts alone** (see §4).

The curator is the *only* place that uses the gold SQL. Gold answers never reach
the serve path (the leakage boundary).

## 4. Phase C — Asynchronous SME clarification round-trip

**(ADR candidate.)** The clarification loop is **asynchronous**, not the current
in-process emit-then-resolve helper.

1. The curator **persists** its open clarification questions to a durable store
   (a worksheet/queue artifact), then the run can exit.
2. A **subject-matter expert answers out of band** — a human via a worksheet/UI,
   or a Simulated SME (LLM stand-in) in eval. The SME sees domain context only,
   never the gold SQL/answer (leakage invariant).
3. The curator **resumes**, folds each answer into the corpus via the existing
   `corpus/clarify.py::accept_answer` primitive (which re-stamps provenance to
   `human`/`certified`), and **re-runs** its inference over the enriched corpus.

This round-trip *is* the curator process: explore → ask → (async wait) → fold →
re-explore, terminating on a bounded number of rounds or when no new open
questions are produced.

## 5. Phase D — Serve (deterministic LangGraph DAG)

Serve stays a **deterministic LangGraph DAG — never autonomous ReAct**
(`server/graph.py`, mirrored by `server/flow.py`). LLM calls are **bounded
classifiers/generators at specific nodes**, not drivers.

### 5.1 Schema selection node

When the database holds multiple schemas:

1. Retrieval **shortlists ~3 candidate schemas** (BM25, optionally embedding-RRF).
2. A dedicated **LLM node is shown those candidate schemas + their details and
   picks the single schema most likely to answer the question.**
3. Everything downstream (retrieval, generation, guardrails, execution) operates
   on **that one schema only.**

Rationale: most questions are answerable within a single schema. Committing to
one schema early removes the cross-schema join problem for the common case.
BM25/embeddings are the *shortlister*; the LLM is the *selector* — both are
needed. (Executable cross-schema joins via curated `JoinAsset` paths remain a
separate, later capability; they are not the default path.)

### 5.2 Guardrails and the two-axis outcome

Guardrail layers L1–L5 (`gateway/guardrails.py`) still run. Every answer carries
the existing two-axis stamp from `server/answer.py`, surfaced in
`api/schemas.py::AnswerResponse`:

- **`safety_clearance` (boolean, hard).** Driven by the **L2 policy layer**
  (single `SELECT`, no DDL/DML/injection) and the curated negative-example
  refuse-gate. These **stay hard rejects** — a query that fails safety is never
  delivered at any reliability score. "Lower the number and run it anyway" must
  not apply to safety.
- **`semantic_assurance` (graded: certified / heuristic / unverified / none).**
  This is where the reliability model lives (§6).

## 6. Reliability model: grade the assurance axis, don't hard-reject

**(ADR candidate. Reverses D15's "refuse on missing cross-schema edge".)**

Today, semantic/coverage failures hard-reject. Instead:

- Maintain a **reliability score / indicator per answered question** on the
  `semantic_assurance` axis. It **decreases** as the answer fails semantic and
  coverage checks — low join-plan confidence, suspect (decoy) columns in scope,
  L3/L4/L5 repair exhaustion, corrective-RAG/fallback, repeated repairs, **and
  the demoted adversary's refutations from §1**.
- The answer is **still delivered.** Below a threshold, `semantic_assurance`
  drops to `unverified`, and the UI **colors it differently** so the user knows
  the answer was produced but is not reliable.
- **Safety failures are exempt** — they remain binary hard rejects (§5.2). Only
  the semantic/coverage class of former hard-rejects becomes graded.

The two-axis stamp already models exactly this separation; the change is
behavioral (deliver-and-grade instead of refuse) plus threshold→color wiring in
the response and UI.

## 7. Delta from today (what still has to be built)

| Area | Today (committed baseline) | Target (this design) |
|---|---|---|
| Ingestion (§2) | `profile_database` exists, Facts-only | keep as-is |
| Curator engine (§3) | deterministic propose→refute→promote; deep-agent scaffold **unwired**; no Q&A-pair input | wire deep agent; ingest batch of (question, gold SQL); deterministic join extraction from gold SQL |
| Governance (§1) | inline adversary is a hard gate | adversary → signal; trust via engineer review + PR gate + inference reads pinned git hash |
| SME loop (§4) | synchronous, in-process emit/resolve | asynchronous persist → SME answers → resume → fold via `accept_answer` → re-run |
| Schema pick (§5.1) | BM25 shortlist + curated-join expansion, else **hard refuse** | shortlist ~3 → **LLM picks one** → single-schema downstream |
| Reliability (§6) | two-axis stamp exists but only grades answers that already cleared guardrails; semantic failures hard-reject | grade the assurance axis; deliver-and-color former semantic hard-rejects; safety stays hard |
| Safety (§5.2) | L2 + negative-example gate hard-reject | unchanged (stays hard) |
| Single-schema / SQLite BIRD path | unchanged | unchanged |

## 8. Preserved invariants (non-negotiable)

- **Safety is never graded away.** L2 policy + curated negative-example gate stay
  hard rejects.
- **Gold SQL/answers never reach the serve path**, and the SME never sees them
  (leakage boundary).
- **Facts stay deterministic**; Inference stays labeled as inferred.
- **Serve's *authority* is deterministic; its *reasoning* may be agentic**
  ([ADR 0002](adr/0002-governed-agentic-serve-runtime.md)). This replaces the
  earlier "deterministic DAG, never an autonomous loop" invariant: the LLM may
  now run a bounded agentic loop, but every data touch passes through
  middleware-intercepted read-only tools and the reliability stamp is set by
  deterministic code the agent cannot influence. Governance is enforced by
  construction (interception + audit ledger + deterministic stamp), not by
  forbidding autonomy.
- **Production inference reads a reviewed, merged, pinned corpus revision** — the
  live working copy is never served in production.
