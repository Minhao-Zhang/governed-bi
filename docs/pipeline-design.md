# End-to-End Pipeline Design: Curator → Serve

_Status: agreed 2026-07-12; the **serve half has since shipped differently**, so
its original sections (Phase D DAG, single-schema LLM pick, grade-don't-refuse)
are **removed** from this doc. What remains describes the **curator / build side**
(§0–§3) — still the intended pipeline — plus the preserved invariants (§8). Serve
is authoritatively covered by [ADR 0002](adr/0002-governed-agentic-serve-runtime.md),
[D5 / D12–D16](design-decisions.md), and [analyst.md](analyst.md)._

> **One removed idea, flagged:** the earlier "reverse D15's missing-edge refuse"
> proposal did **not** ship. A cross-schema question with no curated join still
> **refuses** (D15); only coverage / **L4–L5** scope / execution failures
> deliver-and-grade on the `semantic_assurance` axis (D5). **L3 (column allowlist)
> is a hard refusal, never graded** — it also gates `governance.excluded` / suspect
> columns, so re-executing an L3-blocked query would leak hidden-column rows
> (it stays *repairable mid-loop*; only the final disposition is hard). Serve's
> authority stays deterministic while its reasoning is a bounded agentic loop
> (ADR 0002).

This document records the intended shape of the **curator / build side** as agreed
in design discussion. It is the target for that side, not a description of current
committed code.

## 0. The two phases

The system has a **build-time curator** (offline, exploratory, LLM-heavy) and a
**serve-time inference path** (online, guardrailed). They share one artifact: the
**corpus** (the governed semantic layer). The corpus is authored by the curator
and consumed, read-only, by serve.

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
  write; instead they become inputs to the reliability score. Facts remain
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

## 4. Phase C — SME clarification round-trip

See **[D12 (Clarification Protocol)](design-decisions.md#d12-clarification-protocol)**
and **[D14 (SME-growth benchmark, + 2026-07-15 amendment)](design-decisions.md#d14-sme-growth-benchmark-on-bird-obfuscation)**
for the current design: the curator records open clarification questions, a
pluggable **Responder** (a human SME in production, a Simulated SME in eval)
answers them from domain context only (never the gold SQL — the leakage
invariant), and the answers fold back into the corpus. The async
persist → answer → resume shape originally sketched here is realized via the
`clarifications.jsonl` ledger + `fill_clarifications_with_responder`, not the
schema-layer `accept_answer` primitive this section once named.

## 8. Preserved invariants (non-negotiable)

- **Safety / confidentiality is never graded away.** L2 policy + the curated
  negative-example gate + **L3 column-allowlist** (it gates excluded/suspect
  columns) stay hard rejects; only L4/L5 scope failures deliver-and-grade.
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
