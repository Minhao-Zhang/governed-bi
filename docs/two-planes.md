# Two planes

> **Status: proposal, not behaviour.** Nothing here is built. This document describes a rewrite of
> the serve spine that has not been accepted, and every present-tense sentence about the *current*
> engine is sourced to a file or to [open work](open-work.md). Read
> [architecture](architecture.md) for what the tree actually does today.

A proposal to replace the sixteen-node serve rail with two planes: a **search plane** the agent
composes freely out of versioned scripts, and an **execute plane** that stays exactly one governed
call wide.

## 1. The claim, and what adversarial review left of it

**As first drafted:** the rails do not pay for themselves in accuracy; the licence freezes before
the agent runs and locks ~7% of the benchmark out of reach; a scripted search plane makes that
addressable, deletes ~10k lines, and gives the reliability signal something observable to read.

**After review, three of those four are wrong or unproven:**

| claim | state |
| --- | --- |
| the rails buy no measurable accuracy | **holds** — and partly because a third of the spine ships disabled (`rewrite`, `negative_gate`, `guard` rules, `abstain`) |
| ~7% is locked out by topology | **unproven.** §3 shows a one-line seeding bug under it and §4 shows the base population may be ~43, not 94 |
| deletes ~10k lines | **wrong.** ~1,950 lines of rail scaffolding evaporate; ~8,200 are load-bearing in any runtime and get rebuilt. `tests/serve/` is another 15,312 lines with no row in §6 |
| the trace gives the reliability signal something to read | **holds, and does not need this rewrite** — `execution` plus `tool_delivered` is already an ordered action trace. Ceiling is a measured OOF AUC 0.721 ([open work](open-work.md) §3.10), not unbounded |

What survives is a **maintenance** argument at ~1,950 lines, an **unresolved governance question**
(§5.1), and one good idea that is separable from all of it (§8). The 259 full-coverage failures —
the largest bucket — are untouched by any of it.

This page is kept as the record of an argument that did not survive contact with its own evidence,
because deleting it would leave the next person to re-derive it.

## 2. The rails do not pay

| | |
| --- | ---: |
| governed-bi v4, unfiltered EX | 0.676 |
| WrenAI, same questions and database, no abstention | 0.6773 |
| Smallest resolvable effect on 1,351 questions ([open work](open-work.md) §3.12) | **~2.3pp** |

Two runs with the configuration held fixed disagree on 12.7% of outcomes. So the gap above is not a
loss, it is *nothing measurable* — six governance layers, five-way facet fan-out, route, resolve and
Steiner join buy a difference this instrument cannot see.

Abstention is the other flagship, and its own contrast arm narrows it: on the 73 turns v4 declines,
WrenAI answers all 73 and scores 0.562 against 0.685 elsewhere — a ratio of 1.22×, not a collapse
([open work](open-work.md) §4.1). The declines track *this engine's retrieval on the turn*, not
question difficulty.

## 3. The structural defect, and the one-line bug sitting on top of it

**Read this section together with the correction below it.** An earlier draft attributed the whole
of §1.5 to topology. Most of it may be one line.

`serve/graph.py` builds a linear spine: retrieval runs to completion before `agent_core` starts, and
`ToolBounds` is constructed once at `serve/delivery.py:58`. It is frozen with no widening method
(`govern/bounds.py:68`), and `licensed` deliberately carries no reducer
(`serve/state.py:617-620`) — a union rule "would re-license a table the node had just refused".

Both properties are correct *given* a rail. Together they mean **the agent cannot re-retrieve.** It
can pull more detail about what it was handed (`read_body`, `inspect_schema`, `sample_rows`), and it
cannot reach a table routing missed.

### The confound: `licensed` is seeded post-budget

`serve/nodes/route_retrieve.py:146` sets `licensed` from `retrieved["by_type"]["table"]`, which
`serve/nodes/pass_two.py:461-467` assembles out of the hits `apply_budgets(...)` **kept**. The table
budget is **8** (`register/assets.py:110`). A gold table ranked ninth is never licensed, and Layer 6
refuses the statement `r_table_not_licensed` — a retrieval-budget outcome recorded as a governance
verdict.

[ADR 0006](adr/0006-execution-time-governance.md) §8 forbids exactly this: *"Explicitly **not** the
post-budget `by_type["table"]` — budgets shape what is rendered, and licensing what is reachable."*
It is filed at [open work](open-work.md) §4.2 (2026-08-22) with the decision still open: license the
pre-budget set, or accept the coupling and stop claiming the separation. `resolve` adds references
and `connect` adds Steiner points; neither restores a budget-cut table.

**So the numbers below measure a seam the ADRs do not describe, and they cannot be attributed to
topology until that line is fixed and the arm re-run.** That re-run replaces §9's experiment as the
first thing to do.

What the numbers are, from [open work](open-work.md):

| | n | |
| --- | ---: | --- |
| §1.5 | **74** | questions whose gold tables were never licensed — 3 answered correctly, 71 missed |
| §1.4 | 22 | answers written against the wrong schema — gold schema *was* routed in 20 of them |
| §1.3 | 4 | turns that licensed nothing at all, all four asked a clarifying question |

All three are properties of the v4 artifact on engine `3c0079a`. The first one is **not** the live
number: re-checked against the current tree, ~20 of the 71 misses still reproduce (§4 item 3 below). It is
kept at 74 here because that is what the arm the rest of this page reasons about measured.

## 4. What a scripted plane can and cannot reach

The 438 failures partition six ways. Sorting them by whether an agent that could search again would
help:

| bucket | n | reachable |
| --- | ---: | --- |
| full-coverage answered wrong | 259 | **no** — genuine SQL semantics |
| frozen-literal gold (dataset defect) | 75 | no |
| capped | 49 | partly — 26 of the 49 were not fully covered |
| answered, coverage incomplete | 31 | yes |
| refused | 20 | yes — 19 of 20 had partial or no coverage |
| clarification | 4 | yes — all four licensed nothing |

Cross-cutting, 71 failures had incomplete table coverage. Adding the 23 capped turns that *were*
fully covered gives a nominal set of **94 questions, 7.0% of the benchmark**. The buckets are
disjoint and 94 is not double-counted.

**Three corrections that shrink it, all from this repository's own pages.**

1. **The ceiling is 5.25pp, not 7.0.** Conversion given full coverage is 0.7548, so perfect
   retrieval on all 94 yields ≈71 questions. A "3–5pp" band is 57–95% of the theoretical maximum
   quoted as a midpoint.
2. **The 23 are misread here.** [Open work](open-work.md) §1.6 says that in 12 of them *"the gold
   answer needs more than one table and the final draft joins none. The tables were in context.
   What is missing is **relationship grounding**, not table budget."* More attempts do not fix that.
3. **The base is stale, and this is the serious one.** [Open work](open-work.md) §3.10d re-measured the same coverage-miss
   population against the current tree on 2026-08-24, free, with `--embed`: **73 imported → 71 real
   → 20 still reproduce → 11 the only repair this loop offers could plausibly fix.** So the live
   coverage debt is ~20 questions, not 71.

   **The cause is the engine, not the corpus.** An earlier version of this item pointed at the
   `corpus_content_hash` mismatch — rows carrying `86ed1dbf…` against a corpus at `6e5c7b4b…` — and
   read it as the artifact having been measured on a different tree. It was not. Checking `30872d3`
   out and hashing it returns exactly the declared `86ed1dbf…`; the only difference to the tip is
   two added files, `LICENSE` and `README.md`, and every one of the 7 359 shared files is
   byte-identical. `corpus_content_hash` hashes every file in the subtree and `_is_tooling` drops
   only tool-owned directories, so two non-asset additions move the digest without moving an asset.
   [Open work](open-work.md) §1.5 carries the verification. What moved is 114 commits of engine, `retrieve/` among them:
   retrieval licenses those gold tables now and did not on 2026-08-09.

   **What that does to the numbers on this page.** Replacing 71 with ~20 in the nominal 94 leaves
   ~43, and at item 1's own conversion (0.7548) that is **~2.4pp — at the 2.3pp floor, not above
   it.** Item 2 then takes the 23 capped turns out, leaving ~20 questions and **~1.1pp, well under
   the floor.** The ceiling this section was built to bound is gone either way.

   **And it cuts the other way too, which is the part nobody has priced.** ~50 questions gained
   coverage between the arm and today. Some of them became correct answers, so the **0.676 headline
   itself is stale** — bounded above by ~2.8pp of EX, which is above the MDE and therefore an effect
   a re-run could see. No arm has been run on the current engine. Every figure on this page is a
   property of engine `3c0079a`.

## 5. The design

### The search plane is free

A versioned registry of scripts the agent calls by name with typed arguments, unlimited times, in
any order:

| script | implementation today |
| --- | --- |
| `search_corpus(query, types, k)` | `retrieve/` — the fused BM25 + embedding channels |
| `route_schemas(query)` | `retrieve/route.py`, called on demand instead of once |
| `expand_references(asset_ids)` | `retrieve/resolve.py`'s reference closure |
| `plan_joins(table_ids)` | the Steiner planner in `connect` |
| `read_body`, `inspect_schema`, `sample_rows` | `serve/fetch.py`, unchanged |

Every one of these reads the corpus index and read-only catalogue metadata. None reaches the
warehouse. Withholding stays where it already is: `withheld_by_grant` (`serve/context.py:518`) is
one function, applied at each script's own output, so the prompt and the tools cannot disagree.

### The execute plane is one call wide

`run_query` goes through `govern/pipeline.py::prepare` — six layers, canonicalisation, row limit,
ledger row — unchanged. It is not scriptable and not composable.

### Unanswered: what is `licensed` under two planes

This proposal does not say, and Layer 6 is built on the answer. `govern/check.py::_tables` refuses
`r_table_not_licensed` against `licensed`, whose docstring reads *"`licensed` is what retrieval
found this turn"*, and the licence-before-grant ordering is a security property built on that
meaning: asking the grant first would turn the pair into an existence oracle.

`ToolBounds.licensed` is *"closed at `connect`, never widened"* (`govern/bounds.py:4`). **Delete
`connect` and there is no closing point — the allowlist Layer 6 enforces becomes whatever the
agent's own searches returned, so the agent authors its own licence.** So §5's "six layers,
unchanged" is false for the layer that matters most here, and two things follow that this document
had not admitted: 19 of the 20 refusals end on `r_table_not_licensed`, so the abstention mechanism
is not reweighted but removed; and [open work](open-work.md) §4.2 already asks whether `licensed`
should keep serving two masters, and says answering it *"would change what 'governed' means and
needs an ADR, not a patch."*

The coherent answer is probably that authorization moves wholly to the `AccessPolicy` port and
`licensed` retires as a governance input. That is the decoupling open work has wanted since
2026-08-12 and it would make the safety story stronger than today's. **It is an ADR, and it is a
precondition of this design rather than a detail of it.**

### The line between them is the whole design

**A typed script registry is not a script environment.** The agent selects a script by name and
supplies arguments matching its schema; it never supplies code. The moment the search plane becomes
a general interpreter, `import psycopg` reaches the warehouse through none of the six layers, and
the 115-case adversarial suite becomes a set of facts about a path nobody is obliged to take.

This is the rule `pyproject.toml` already applies to harness selection, in the note retiring
`deepagents`: a harness that contributes a non-removable generic write channel "is not a harness we
can adopt", because "the governance boundary is enforced by the *absence* of a tool". Under this
proposal that sentence is unchanged and its scope widens by one case.

## 6. What survives

| package | lines | disposition |
| --- | ---: | --- |
| `govern` | 3,917 | unchanged; call sites go from two to one |
| `measure` | 2,072 | unchanged |
| `corpus` + `conform` | 3,842 | unchanged — format, identity, hash, the 22 rules |
| `feedback` | 2,632 | unchanged; nothing in it runs during a turn |
| `retrieve` | 2,408 | unchanged implementation, exposed as scripts rather than called by nodes |
| `register` | 3,612 | `knobs`, `record`, `quantity`, `citations` unchanged; `stages` and `facets` become a script vocabulary |
| `datasource`, `model` | 2,441 | unchanged |
| **`serve`** | **10,670** | **rewritten** — sixteen rail nodes and the LangGraph workarounds both go; an agent core plus a script registry is ~3k |
| `api` | 4,285 | thinner, still owes the 22 REST operations and auth |

Frontend: the stream envelope is repo-owned rather than a LangGraph type, and `ui/lib/steps.ts`
imports no SDK type, so `AgentTimeline` and the seven non-chat surfaces are portable in principle.

**With a caveat that weakens the "90% survives" reading.** `GovEvent` exists only as a TypeScript
interface (`ui/lib/steps.ts:61`); the Python side hand-builds a dict in `serve/events.py::emit`.
Two independent declarations of one wire format, with nothing binding them — and `steps.ts:13` says
*"The vocabulary is `register/stages.py`, not this file."* Replacing the stage vocabulary with a
script vocabulary therefore touches both ends with no compiler between them. `defaultLabel`'s ~230
lines of copy are rewritten; `use-stream-chat.ts` and `lib/threads.ts` are the coupled surface.

## 7. What measurement loses

Stated first because it is the real price.

1. **`facet_channels` dies.** Its premise is a declared fan-out that either ran as declared or did
   not.
2. **`context_hash` changes meaning.** There is no single rendered block. The replacement is a
   *trace hash* over the ordered script calls and their result digests. `context_hashes_distinct`
   was already retired by audit D9 for measuring retrieval noise, so nothing quotable is lost.
3. **`knobs_resolved` gets a new term.** The configuration now includes the script library.
   `register/prompts.py:492`'s `prompt_set_hash` is the pattern to copy: digest both the script
   names and their source, because names alone miss an in-place edit.
4. **`retrieval_funnel` restages.** Its six conditional stages are rail stages. "Was the gold table
   ever *seen*" replaces "was it licensed".
5. **The abstention predicates re-source.** They are deterministic predicates over recorded state
   (`serve/abstention.py`), and the state they read is the rail's.

## 8. What measurement gains

The reliability signal is currently broken and known to be: the LLM reflector scores AUC **0.597**,
worse than counting output tokens, and its `unsure` bucket is as likely to be right (0.766) as its
`answered` bucket (0.763) ([open work](open-work.md) §3.11). A judge whose "I cannot tell" bucket
matches its "this is right" bucket has no perception of its own uncertainty.

It is broken because it reads prose and guesses. A trace gives it **actions**: did the turn sample
the literal before filtering on it, plan the join before writing it, cross-check the count against a
second formulation? `measure/signals.py` is already built for exactly this shape — `READABLE_FIELDS`
is an allowlist (`signals.py:48`), every signal declares a `direction` *and* a `why`, and
`assert_no_signal_reads_the_grade` runs at import. It has the discipline and no actions to read.

This is the largest thing on offer, and it is worth more than the accuracy points.

## 9. Falsify it for about 100 questions

Run before building anything. Both arms need the tree as it stands; `ToolBounds` has one
construction site (`serve/delivery.py:58`) and `compile_durable` already runs a single question.

- **Arm A** — current engine.
- **Arm B** — `licensed` seeded pre-budget, which is the §4.2 fix rather than a new design.
- **Arm C** — Arm B plus the attempt cap raised 5 → 10, **and `agent_node_timeout_s` raised with
  it.** `register/knobs.py:290-291` pairs them: `run_query_attempt_cap × statement_timeout_ms`
  = 5 × 120s = 600s, deliberately half of `agent_node_timeout_s = 1200.0`, so five statements can
  each time out and still leave the other half for model calls. A cap of 10 consumes the whole node
  budget and converts the extra attempts into `crashed` rather than recoveries.

Arm A must be re-run too, not read off disk: [open work](open-work.md) §3.13 records that all seven
proxy arms are missing four comparability knobs, so no pair on disk reaches the cross-arm gate. The
experiment is ~200 paid questions, not ~100. And Arm A must be re-run **against the same 100** — the
population is selected for having failed on one noisy run, and at 12.7% discordance roughly 20 of
them flip to correct on a re-run of the identical configuration. That regression-to-the-mean null,
not zero, is what "under 20" has to beat.

Three arms, not two, because licence width and attempt cap are separate treatments and an arm that
moves both cannot attribute its own delta. The cap number is not arbitrary: ReAct-SQL caps at 15 and
observes no query exceeding 13, averaging 3.3 turns on BIRD ([arXiv 2608.22651](https://arxiv.org/html/2608.22651)),
so a cap of 5 truncates the tail rather than bounding it.

Population: the 74 questions of §1.5 plus the 49 capped, deduplicated, ~100 questions. **Re-select
it before spending anything.** Those 74 are the arm's misses, and §4 item 3 says ~50 of them are
covered on the current engine, so ~100 selected today is a different set — and a smaller one.

| result | reading |
| --- | --- |
| Arm B ≫ Arm A rerun | **the one-line fix was the cause. Ship it; the rewrite's accuracy case is spent.** |
| Arm B ≈ Arm A, Arm C ≫ both | the cap was the cause; raise the cap and its paired timeout |
| both ≈ Arm A, residual concentrated in turns where the schema *was* routed but a second query was needed | this is the only branch on which topology is the remaining explanation, and the only one that argues for §5 |

**Note what that table says about this document.** Arm B is a one-line change to
`route_retrieve.py:146`. On the branch where it succeeds, what has been shown is that a one-line
seeding fix recovered the questions — which makes the rewrite's accuracy case *weaker*. This
proposal's own experiment cannot produce evidence for it except on the third branch. That is the
honest state of the argument and it is stated here rather than left for a reader to notice.

`--arm` already refuses a corpus-hash or question-subset mismatch before the first paid question,
and `mcnemar` refuses two arms whose unit sets differ. [Open work](open-work.md) §3.13 records that
no pair currently on disk can reach the cross-arm gate; this would be the first that does.

## 10. Risks

- **Cost and latency.** A loop that may search repeatedly will. Today a turn is ~18s against a fixed
  fan-out. The script-call count needs its own cap and its own knob, and the cap is a treatment.
- **Reproducibility.** A fresh run is less deterministic than a rail. The trace is recorded, so
  *replay* stays deterministic; a re-run does not.
- **The adversarial suite covers the execute plane only.** It always did — `check()` and `prepare()`
  are what it drives. The disclosure probes in `tools/govern_bench.py` cover four surfaces
  (`context`, `inspect_schema`, `read_body`, `may_sample`); a script registry adds surfaces, and each
  new one owes a probe.
- **The 259 stay.** Nothing here is aimed at them, and a rewrite that reports a 3pp gain while the
  largest bucket is untouched will read as more progress than it is.
