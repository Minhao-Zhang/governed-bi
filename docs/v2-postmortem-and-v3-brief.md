# v2 post-mortem and the v3 brief

Written 2026-08-05, on the commit that froze v2 (`8745b44`). Written to be read without v2's
other 89 markdown files. Everything below was verified against the tree at that commit; where a
claim is a measurement, the method is stated so a reader can re-check it rather than believe it.

**Read on 2026-08-06 or later: the v3 rewrite this document briefs was abandoned and its branch
deleted. This file was moved onto v2 and is all that was kept.** So §1–§3 are the live part — an
audit of *this* tree, at *this* commit, which is the one you are reading it on. Every path it
names can be opened directly rather than through `git show`. §4 describes a plan that no longer
has a branch, and is left in place because the principle it states is the reason the audit was
worth doing.

---

## 1. What v2 was

An agentic BI engine: natural-language question in, governed read-only SQL out, with an audit
trail. A curated "semantic layer" of typed YAML assets (schemas, tables, columns, joins, metrics,
terms, few-shots) is retrieved against, a model writes SQL, the SQL passes seven deterministic
guardrail layers, executes read-only, and the turn is stamped with two independent verdicts
(`safety_clearance`, `semantic_assurance`).

The serve path at the freeze commit:

```
guard(LLM scope gate) → rewrite → negative_gate
  → fanout ─┬─ facet_schema   (raw question — the rewrite is deliberately disabled)
            ├─ facet_term     (utility model rewrite)
            ├─ facet_metric   (utility model rewrite)
            ├─ facet_entity   (utility model rewrite)
            └─ facet_example  (utility model rewrite; semantic channel only)
  → route(top_n schemas) → resolve(pass-two budgets)
  → connect(Steiner join over components + join completion) → assemble(render context block)
  → agent_core(create_agent loop, 5 read-only tools) → narrate → stamp
```

`agent_core` is a nested `create_agent` with `read_body`, `inspect_schema`, `sample_rows`,
`run_query`, `ask_user`. The retrieval context block is injected per model call through a
`wrap_model_call` middleware, so it never enters the `messages` channel.

**What is genuinely good, and should be judged on its own merits rather than by association
with the rest of this document:**

- `govern/` — the seven-layer check (`PARSE`, `NO_WRITE`, `FUNCTIONS`, `BINDING`, `COLUMNS`,
  `TABLES`, `COST`), identifier canonicalisation against declared corpus spellings, the
  `ToolBounds` licensing surface, and the attempt ledger. This is the hard part and it works.
- `retrieve/` — BM25 with saturation, a semantic channel, `scale_within_channel` + weighted
  fusion, and the Steiner-tree join planner that keeps one connected component per turn.
- `corpus/` — the asset contract, deterministic ID derivation, the reference-integrity validator.
- `serve/wrap.py` — turning any node exception into a recordable `crashed` outcome, so a turn
  that dies still produces a record.
- `serve/runtime.trust()` — forcing run constants over caller-supplied `configurable`, which
  closed a real hole where a request could replace the `GovernancePolicy`.
- The test suite: **811 passed, 26 xfailed** at the freeze commit.

---

## 2. The audit: why v2's own documentation cannot be used as evidence

### 2.1 The measurement that frames everything else

| Quantity | Value |
|---|---|
| `src/` Python files | 105 |
| `src/` lines | 23,658 |
| Share of `src/` **bytes** that are docstrings | **36.0%** |
| `docs/` markdown files | 89 |
| `docs/` bytes | **2.15 MB** |
| ADRs + plans | 9,545 lines |
| Executable code (estimate) | ~0.68 MB |

Prose to code is roughly **3.7 : 1**.

Method: count bytes in `src/**/*.py`, extract `"""…"""` spans by regex, sum; `du` and `wc` for
the rest.

The ratio is not itself the defect. The defect is the *genre*. Nearly every docstring in v2 is a
post-mortem: "here is what used to be wrong, here is the measurement that showed it, here is why
the current form is right." That genre is unusually persuasive, and its effect on a reader is to
**end verification**. Each finding below was produced by taking one such paragraph and checking
whether the wire it claims to have connected is actually connected.

### 2.2 The context budget is on the half that does not grow

- `assemble` renders the retrieval context block with a two-rung eviction ladder, an `evicted`
  out-parameter, and a `context_hash`. Default budget: **80,000 characters**.
- v2's own measurement (`docs/plans/context-engineering-2026-08-04.md`, item M3) records the real
  block at **8.5–12.5 KB**. The budget is an order of magnitude above the observed size, so the
  ladder has never fired in production. It is a machine that has never been switched on.
- Meanwhile `messages` grows without bound. `ServeState.messages` uses `add_messages`;
  `agent_core` writes `fresh = out_messages[len(inbound):]` back to the outer channel, and that
  slice **includes every `ToolMessage`**. Turn 2 sends `history + [question]`, so the whole of
  turn 1's tool output is re-sent verbatim.
- Those tool outputs are the large ones. `read_body`'s cap is `read_body_max_tokens (20,000) × 4`
  = **80,000 characters per call** — the entire context budget, in one tool result, permanently
  resident. `inspect_schema` has **no cap at all**: a 200-column table is one JSON blob in the
  history forever. `run_query` adds a 20-row preview per call.
- Repository-wide grep for `trim_messages`, `SummarizationMiddleware`, `pre_model_hook`,
  `recursion_limit`: **zero hits**.

**The thing that was budgeted, hashed and audited is the thing that does not grow. The thing that
grows was not measured at all.**

### 2.3 The prompt registry has an authority and no switch

`register/prompts.py` is 470 lines arguing that prompt variants are first-class and that
comparing prompts is the point. `prompt_set_hash()` correctly digests both variant names and
variant text. But:

- The only production call is `prompt_set_hash()` at `serve/session.py:425` — **no overrides**.
- Non-test callers of `select(overrides)`: **zero**.
- Readers of the declared knob `prompt_set` ("resolved variant per stage"): **zero**.
- The only way to change a variant is to edit `default=` in the source.

So the machinery can prove exactly one thing — that nobody edited a prompt — and cannot support
the experiment it exists for. This is the same defect the file accuses v1 of ("a knob reachable
only from an eval CLI"), one step further along: reachable from nowhere.

### 2.4 Declared-but-unread comparability knobs

`register/knobs.py` opens by explaining that a declared-but-unread knob is worse than an
undeclared constant, because it "actively lies." Readers in `src/`, excluding the register itself:

| Knob | Role | Readers |
|---|---|---|
| `prompt_set` | comparability | **0** |
| `chat_model` | comparability | **0** |
| `facet_model` | comparability | **0** |
| `rewrite_model` | comparability | **0** |
| `expand_hops` | comparability | **0** |

All five are `Role.comparability`, so all five enter `config_hash_keys()` and `knobs_resolved`.
Two runs can differ on these fields, publish different config hashes, and behave identically.

### 2.5 The eval-only knob defect is still live, and the gate that would catch it does not exist

`route_top_n`, `candidate_depth` and `context_budget_chars` are read through
`int_knob`/`float_knob`: state → `knobs_resolved` → register default. On the server path nothing
writes those state keys, and `knobs_resolved` is just the register defaults, so **in production
they are constants**. The deployment surface is environment variables (`GOVERNED_BI_*`, about
twelve of them) and covers only models, paths, timeouts, retries and the embedder.

`eval/datalake.py:180` sets `turn["route_top_n"]`. So the benchmark sweeps a knob no deployment
can set — verbatim the defect the knob register's own docstring describes as fixed, adding: "Every
knob here must be settable by the same mechanism a deployment uses; `tests/conformance` asserts
it." `tests/conformance/` contains two files (`test_quantity_presence.py`,
`test_register_closure.py`) and **no test asserts a deployment surface**.

### 2.6 The configuration file is inert, and the README describes a deleted system

- `governed_bi.toml` is 16 KB across 8 sections; `governed_bi.local.toml` is 7.5 KB. There is
  **no `import tomllib` anywhere in `src/`**.
- The entire `[notes]` section (~45 lines) configures `note_inject`, `top_k`, `char_max`,
  `_TRIGGERS_PER_NOTE`, `always_note_global_max` — all with **zero readers**.
- `README.md` states that all non-secret policy lives in `governed_bi.toml`, "parsed by
  `governed_bi.config.load_settings()`". **`src/governed_bi/config.py` does not exist.**
- Real configuration is: environment variables, plus Python defaults in `register/knobs.py`.

The README carries a disclaimer saying it describes v1. A disclaimer is not a fix: it makes the
repository's entry point a trap that announces itself and stays.

### 2.7 The corpus — the declared moat — is produced outside all governance

The README's central claim is two harnesses: a `curator` that builds the corpus and an `analyst`
that consumes it, with opposite risk profiles. **There is no curator module in v2's `src/`.**

The corpus is produced by scripts in `tools/`, eight of which were untracked until the freeze
commit. `tools/_revise_miss_summaries.py` hand-writes a per-schema discriminating phrase,
including negations aimed squarely at the benchmark's confusion pairs:

```
"soccer_2016": "cricket IPL ball-by-ball … NEVER football soccer"
"ice_hockey_draft": "scouting draft prospects … NOT career HOF Stanley"
```

That is evaluation-set knowledge compiled into the retrieved corpus.

To v2's credit, the team caught both halves of this and said so in the commit log:
`dbe65bc "The regex is the whole effect. The hand-written prefixes are worse than nothing."` and
`5bd5a7b "The brief handed over the held-out miss list and then scored the result on the same
questions."` But the scripts stayed in the tree, unversioned, able to be picked up by the next
corpus build. **The corpus is called the moat; the process that produces it has no governance at
all.** That asymmetry is the largest structural hole in v2, larger than any single number.

### 2.8 The agent loop's real bound is undeclared

Only `run_query` is capped (`run_query_attempt_cap = 3`). `read_body`, `inspect_schema` and
`sample_rows` have no call limit. The actual ceiling on the loop is LangGraph's default
`recursion_limit = 25` — never set, never declared, never hashed, absent from `knobs_resolved`.
It governs every turn's behaviour and is precisely the kind of undeclared constant the register
exists to abolish.

### 2.9 Gates parse free text

The `bi_scope` gate asks for "exactly one word: YES or NO" and does `.strip().lower()` comparison.
The five facet rewriters ask for "search text only, under 30 words" and their raw output is fed to
BM25 and to an embedder. Both are prompt-discipline solutions to problems that belong in the
protocol layer.

### 2.10 One-sentence summary of the audit

**v2 governs what it can observe — SQL, guardrails, the ledger, the retrieval block — and does not
govern what it cannot: conversation history, tool output, prompt variants, corpus production. The
documentation density is what made the second list hard to see, because every paragraph is about
the first.**

---

## 3. The runtime: how v2 writes SQL, and how the field does it

### 3.1 What v2 actually does

`run_query` ([`serve/fetch.py`](../src/governed_bi/serve/fetch.py) at the freeze commit) returns
exactly three shapes:

| Situation | Returned to the model |
|---|---|
| A guardrail layer refuses | `"run_query refused: {detail}"` |
| Driver raises | `"run_query error: {type}: {exc}"` |
| Success | JSON `{columns, rows(first 20), row_count, truncated}` |

Attempt cap: 3.

**So the generation strategy is: one candidate, plus at most three sequential repairs driven by an
error string. No candidate set, no selector, no correctness verification.**

And the sharpest point: **the seven guardrail layers check permission, not correctness.** From
question in to answer out, nothing in v2 is responsible for whether the SQL is semantically right.
Governance is thick; correctness coverage is zero.

Cost shape: about eight model calls per turn, six of them on the small utility tier (one scope
gate, four facet rewriters, one narrator). The team's own measurement disabled the `facet_schema`
rewriter as a regression. The one tier that determines whether the answer is correct runs once.

### 3.2 What the field does

Five independent systems, one shared skeleton:

| System | Stages |
|---|---|
| **CHESS** (Stanford) | Information Retriever → Schema Selector → **Candidate Generator (multi-candidate + iterative refinement)** → **Unit Tester (LLM natural-language unit tests)** |
| **CHASE-SQL** (Google) | **Value Retrieval** → **Candidate Generator (3 generators: divide-and-conquer, execution-plan CoT, instance-aware synthetic examples)** → **Query Fixer** → **Selection Agent (pairwise)** |
| **XiYan-SQL** | Schema Linking (columns **and values**) → Candidate Generation (M-Schema format + self-refinement) → **Candidate Selection Agent** |
| **Agentar-Scale-SQL** (Ant Group; BIRD test **81.67%**, rank 1) | Internal scaling (RL reasoning) + Sequential scaling (iterative refinement) + **Parallel scaling (diverse synthesis + tournament selection)** |
| **LangGraph official SQL agent** | list_tables → get_schema (forced tool call) → generate → **check_query (dedicated LLM review node)** → run_query |

**Five out of five have a "generate several, then choose" stage. v2 has one generation.**

Three numbers that should shape the v3 design:

- **BIRD-CRITIC**, which measures self-correction specifically: humans **76.67%**, frontier models
  **44–45%**. v2 bets everything on the one capability that is weakest.
- **Agentar-Scale-SQL**'s authors state the framework has high latency and is *unsuitable for
  real-time applications*. The leaderboard recipe cannot be copied into a 30–120s budget as-is.
- **ReViSQL** reaches 93.2% EX on an expert-verified BIRD Mini-Dev (proxy human 92.96%), and its
  30B variant matches prior SOTA at **7.5× lower cost per query**. Multi-candidate does not have
  to be expensive; how candidates are generated and selected is what decides that.

### 3.3 Stage-by-stage: what belongs where

Three tiers: **L0 deterministic (no model)**, **L1 small model**, **L2 main model**.

| Stage | Tier | What it should be | v2 |
|---|---|---|---|
| Scope gate | L1 or L0 | Structured output, or deterministic rules | LLM free-text `YES` |
| **Value retrieval** | **L0** | Column-value index; bind the question's literals to real values, write them into context | Only a `sample_rows` tool the model must remember to call |
| Schema linking | L0+L1 | Two-channel retrieval, measured by recall@k; over-recall beats under-recall | ✅ facet fan-out + Steiner — better than most open frameworks |
| Schema formatting | L0 | Compact structured format: columns, types, sample values, notes | ✅ `context.py` terse/roster folding |
| **Candidate generation** | **L2** | **2–5 diverse candidates** (different generators / divide-and-conquer / execution-plan CoT) | **1** |
| **Repair** | L0 routing + L2 | **Dispatch on error class**: `column does not exist` → re-retrieve schema; literal mismatch → force value lookup | One undifferentiated error string, ≤3 times |
| **Selection** | L0 or L1 | Execution-result consistency vote / selection agent / unit tests | **absent** |
| Verification | L0 | Empty-result detection, row-count anomaly, NL unit tests | **absent** (`row_count == 0` returns `status: ok`) |
| Narration | L1 | Small model, reads the table only | ✅ correct as built |

**The compute allocation is inverted.** Six L1 calls per turn, of which the measured-positive
subset is small; one L2 call, which is the only one that determines correctness.

### 3.4 The changes that matter, by return on effort

**① Error-class-driven repair — no new model calls, one function.**

| Case | v2 returns | Should return |
|---|---|---|
| `r_table_not_licensed` | `run_query refused: …` | reason **+ the licensed table list** |
| `r_unbound_reference` | same | reason **+ instruction to call `inspect_schema` first** |
| driver `column does not exist` | raw driver text | error **+ auto re-inject that table's schema** |
| **`row_count == 0`** | **`status: ok`, normal JSON** | **flagged as actionable: check literals, suggest `sample_rows`** |

The last row deserves its own sentence. On BIRD, an empty result is usually a **wrong literal**
(`type = 'Residential'` where the column holds `'R'`), not an absence of data. v2 discards that
signal entirely: the agent narrates "no rows matched," EX scores zero, and every artifact reports
a healthy turn.

**② Deterministic value binding before generation — no model at all.** Match the question's
literals against a column-value index and put the hits in the context block. This is CHASE-SQL's
value retrieval and one of the largest single gains available on BIRD. It is also what BIRD's
`evidence` field has been substituting for — computing it ourselves is strictly better than being
handed it by the dataset.

**③ k=3 candidates with execution-consistency voting.** The one genuinely expensive change, and
it is cheaper here than in the papers: generate 3 SQL statements in parallel, run **all three**
through the seven layers and execute all three — they are read-only, row-capped, and already
ledgered — then hash the result sets and take the majority. **No selector model needed; execution
is the judge.** This fits the existing architecture: `AttemptBook` already keys attempts by
`tool_call_id`, and three candidates are three ledger rows, which is a clearer audit trail than
three sequential retries of one. Requires splitting `run_query_attempt_cap` into a *generation*
budget and a *repair* budget.

**④ Separate exploration context from generation context.** v2's agent does schema exploration and
SQL writing in one loop over one `messages` list, so every raw `inspect_schema` JSON is still in
context when the SQL is written. CHESS and XiYan both split these, compressing exploration output
into a structured schema fragment rather than carrying the raw tool result. Same root cause as
§2.2.

**⑤ Structured output.** Replace the `YES`/`NO` string comparison and the free-text facet
rewriters with `with_structured_output` or forced tool calls, so a formatting change cannot make a
gate fail.

### 3.5 Target shape

```
[L0 deterministic]  value binding · identifier canonicalisation · 7-layer check
                    Steiner join · context rendering · error classification
                              ↓
[L1 small model]    scope gate (structured) · facet rewriting (only the measured-positive ones)
                    narration
                              ↓
[L2 main model]     ┌─ candidate A (direct)          ─┐
                    ├─ candidate B (divide & conquer) ─┼→ all check+execute → result-hash vote
                    └─ candidate C (execution plan)   ─┘        → repair loop (error-class driven)
                              ↓
[L0 deterministic]  empty-result / row-count anomaly detection → retry or degrade
```

Move budget from the six L1 calls to parallel L2 candidates, and replace reliance on model
self-correction with deterministic error classification plus execution voting. The first is a
compute-allocation fix; the second is, per BIRD-CRITIC, getting off the model's weakest capability.

---

## 4. What v3 kept

**Historical.** v3 was cut, built for a day, and abandoned; nothing below is in effect. The
keep/delete ledger it refers to was deleted with the branch.

The principle it was cut on, which is the part worth keeping: **keep what was verified to work
and is expensive to rebuild; delete what was being redesigned, and delete the measurement
scaffolding that reported on itself.**

---

## 5. Sources

Retrieval and agent framework documentation:

- [LangGraph — Manage short-term memory (trim / summarize)](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangChain — Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- [LangGraph — Use subgraphs (sub-agent namespace isolation)](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [LangGraph — Build a custom SQL agent](https://docs.langchain.com/oss/python/langgraph/sql-agent)

Text-to-SQL systems:

- [Agentar-Scale-SQL: Orchestrated Test-Time Scaling](https://arxiv.org/abs/2509.24403) · [code](https://github.com/antgroup/Agentar-Scale-SQL)
- [CHASE-SQL: Multi-Path Reasoning and Preference Optimized Candidate Selection](https://arxiv.org/html/2410.01943v1)
- [CHESS: Contextual Harnessing for Efficient SQL Synthesis](https://arxiv.org/html/2405.16755v1) · [code](https://github.com/ShayanTalaei/CHESS)
- [XiYan-SQL: A Multi-Generator Ensemble Framework](https://arxiv.org/html/2411.08599v2)
- [ReViSQL: Achieving Human-Level Text-to-SQL](https://arxiv.org/abs/2603.20004)
- [BIRD benchmark / BIRD-CRITIC](https://bird-bench.github.io/)
- [NL2SQL Handbook (HKUST)](https://github.com/HKUSTDial/NL2SQL_Handbook)

Context engineering:

- [Effective context engineering for AI agents — Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Why AI Agents Need Versioned Context — Atlan](https://atlan.com/know/ai-agent/context-versioning-for-ai-agents/)
