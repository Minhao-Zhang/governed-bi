# v2 audit: what is broken, and in what order to fix it

Written 2026-08-06 against the tree at `9a3dc4b` (branch `v2`). This replaces the
2026-08-05 post-mortem, which was written against the freeze commit `8745b44` and whose own
framing measurements no longer reproduce (§13.1). Everything below was verified against the tree
you are reading it on, and every claim carries a verification tag so you can tell what was
adversarially checked from what is still a lead.

**Working document.** The intent is that this is the thing open on the second monitor for the next
few days, so §3 (the work order) comes before the evidence. §4–§13 are the reference material
behind it.

---

## 0. Status, 2026-08-06 — items 1–8 are done

**All eight items of §3's work order have landed**, one commit each, suite green at every step
(783 → 810 passing). The evidence sections below are **not** rewritten: they describe the tree as
audited, which is what makes them checkable against history. Where a section is closed, the code
it names now carries the correction in its own docstring, so the two never drift.

| # | Item | Commit | What actually happened |
|---|---|---|---|
| 1 | `sample_rows` through `govern` | `b74c525` | Statement built as a syntax tree, run through `prepare()`, ledgered as `path="sample"`. `sample_values` **deleted** from the port and both adapters rather than fixed — keeping it would have left a method with no caller. `answering_attempts` added so a passing sample row cannot make a turn look answered. |
| 2 | EX comparator | `2d4b52a` | BIRD-Obfuscation's `normalise_result` transcribed. `result_fingerprint` is now **byte-identical** to `hash_normalised_result`, verified on six rowsets. `EX 0.049` retired via `RETIRED_CLAIMS`; grep gate verified to fire on reinsertion. |
| 3 | Oracle ceiling arm | `ed77bb9` | Self-grade branch deleted; no independent gold ⇒ `correct=None` ⇒ EX *unmeasured*, not 1.000 and not 0.000. Also fixed the bare list comprehension that discarded a whole arm on one unexecutable gold. |
| 4 | Corpus contamination | `9320834` | `corpora/` is **not on disk in this tree**, so there was nothing to quarantine. The producers were the durable artifact: both deleted, plus four one-off scripts pointed at them. **The audit named one producer; running the new gate found a second** — `_revise_miss_summaries.py`, 26 phrases with *negative* discriminators, absent from §6.1. `check_train_only`'s self-comparison refused; `check_citations` + a new source-level gate added to CI. |
| 5 | Non-monotone fusion | `88713d9` | `fuse` takes `consulted` and renormalises over it. §7.2 fixed too: pass two now embeds each facet's rewritten query. **One unmeasured ranking change**, recorded inline — see §0.1. |
| 6 | Dead connections | `93bc7ad` | `_discard()` on every `ConnectionError`; no-SQLSTATE faults classified from `Connection.closed`/`broken` and the DB-API split, never prose. §9.2 **verified** and fixed: the SQLite adapter classified a governance-blocked write as infrastructure being down. |
| 7 | §10 wire-or-delete | `26739e4`, `2b8b10b`, `a296431` | Redaction vocabulary deleted (maintainer's call), `Sink` and `Responder` ports deleted, `latency_sec` **measured for the first time**, cache tokens summed, `lexical_coverage` given a real measurement, `knobs.Role` given a gate. Seven other declarations deleted. |
| 8 | The two verdicts | `5a20958` | Removed from every live doc and from `openapi.json`'s `AnswerResponse` (which was v1's shape entire). The stub test that named them is **written**, greps all of `src/`, and fails if either returns. |

### 0.1 What is owed, and what changed without a measurement

Three things a reader of the sections below should know before trusting them:

1. **Item 5 demoted single-channel retrieval hits, and nothing measured the effect.** With a
   fixed denominator, a document only one channel found is capped at that channel's weight
   (0.5). On the contract fixture a strong-cosine/no-shared-term asset goes 1.000 → 0.500 and
   now sits marginally *below* a mediocre two-channel asset. That is the price of monotonicity
   and the alternatives were worse (noisy-OR breaks the contract's property 3, `max` makes both
   weight knobs inert, a p=2 power mean fits the rule to one fixture). **It wants a routing-recall
   measurement.**
2. **§12 is still entirely `[U]`.** Nothing in it was verified or acted on. The test-suite
   claims — 25 of 26 xfails are stubs, 216 of 837 tests are one tautological grid, CI runs no
   Postgres — are unchanged findings, and one of them moved by exactly one: the §4.5 stub is now
   a real test.
3. **The completeness critic still has not run.** Its named blind spots stand, minus latency:
   cost accounting, multi-turn behaviour, the `../governed-bi-ui` contract, corpus migration.
   The `governed-bi-ui` one is now sharper, not softer — `openapi.json`'s answer schema changed,
   and nothing checks the spec against the served app.

Two findings **not** in the work order remain open and are not fixed: §6.2's corpus is neither
in version control nor reproducible from anything committed (documented as an open problem in
`corpus/README.md`), and §6.4's server-serves-what-the-CLI-refuses gap.

## How to read the tags

| Tag | Meaning |
|---|---|
| **[V]** | Found by an auditor, then survived a separate adversarial verifier whose default position was that the finding is wrong. Several were reproduced by executing the current tree. |
| **[V–]** | Same, but the verifier narrowed the scope or cut the severity. The statement printed here is the **corrected** one, not the original claim. |
| **[M]** | Checked directly, by hand, in the session that wrote this document. |
| **[U]** | Found by one auditor, never adversarially checked — the verification pass was cut off by a rate limit. **Treat as a lead, not a fact.** |

A note on the file:line references: they were opened in this tree. Where an auditor's original
number was off, the corrected statement's number is the one printed. If you find one that does not
open, that is a defect in this document and it should be fixed here, not worked around.

---

## 1. Method and coverage

The method is the one the previous post-mortem introduced, applied more widely: **take a claim — a
docstring, an ADR, a README line, a test name, a knob description, a commit message — and check
whether the wire it says is connected is actually connected.** Open the file. Follow the call.
Count the readers. Run it.

The reason this method keeps paying is that this repository writes in a post-mortem genre: nearly
every docstring explains what used to be wrong, cites the measurement that showed it, and argues
why the current form is right. That genre is persuasive, and its effect on a reader — including on
the next agent to open the file — is to **end verification**.

**Coverage.** Ten dimensions were audited in parallel (`govern`, `retrieve`, `eval`, `corpus`,
`api`, `tests`, `docs`, `secops`, `plumbing`, and the prior post-mortem itself), each finding then
handed to an independent verifier. 44 findings completed verification: **30 CONFIRMED, 11 PARTIAL,
0 REFUTED**, 3 left without a verdict. 20 lower-priority findings overflowed unverified.

**What was not covered**, and should be, before this document is treated as complete:

- The `tests` dimension's verification pass never ran. Everything in §12 is **[U]**.
- The completeness critic never ran, so nothing systematically asked *what did all ten dimensions
  miss.* The most likely blind spots, by construction: cost and latency accounting, behaviour on
  the second and third turn of a conversation, the sibling UI contract at `../governed-bi-ui`,
  and corpus migration/upgrade.
- Zero findings were refuted out of 44. Eleven were downgraded, which is evidence the verifiers
  were doing something. But a 0% refutation rate is itself worth a raised eyebrow; the mitigation
  is that the highest-severity items were reproduced by execution, not argument.

---

## 2. The diagnosis

The previous post-mortem's one-line summary was: *v2 governs what it can observe and does not
govern what it cannot.* That is true and too kind. The finding of this audit is one level worse:

> **v2 does not govern what it says it governs, and it cannot measure what it says it measures.**

The shape is consistent enough to name. This repository **declares invariants and then does not
wire them**. There is a register for record fields, a policy enum for redaction, an ADR for every
decision, an acceptance clause for every phase — and the line between those declarations and the
code that would enforce them is broken more often than it is connected. Three countable examples:

- Four executor paths are **declared** in the ledger. **One** is wired (`agent`). Nothing in `src/`
  ever writes `"sample"`, `"graded"` or `"profile"`.
- 37 record fields **declare** a redaction class. `redaction_of()` has **zero** callers. Four of
  those fields have zero writers and are permanently null.
- CI runs **four** gates. The register names **five**. The one that checks for benchmark leakage is
  in neither list.

There is a second thing, and it is the more urgent of the two, because it governs whether any
decision made in the next few days can be trusted:

> **The measurement instrument is wrong in four independent ways, and all four are silent.**

The EX grader is not BIRD's EX and understates systematically (§5.1). The "grader ceiling" baseline
is 1.000 by construction (§5.2). A swept knob never reaches the graph but is stamped into the run
id (§5.3). The leakage gate is not in CI and its default control is the corpus under test (§5.4).
Separately, a corpus with hand-written benchmark discriminators is already materialised on disk and
passes every check the repo has (§6.1).

The distinction that matters: most of the previous post-mortem's findings were **machines that were
never switched on** — wasteful, not misleading. Several findings here are **instruments that are
switched on and reading wrong**. The first kind costs effort. The second kind costs direction.

`memory/` already records three prior instances of the measurement instrument being the defect
(`experiment-numbers-void`, `eval-instrument-defects`, `retrieval-scoring-defect`). This is the
fourth, and the first where the grader itself is implicated.

---

## 3. Work order

Ordered by *"until this is fixed, fixing the others buys nothing"* — **not** by severity.

| # | Do | Why first | Where | §  |
|---|---|---|---|---|
| **1** | Route `sample_rows` through `govern`, and escape identifiers in the Postgres connector | The only item with a safety consequence. Closes an unconditional policy bypass and an injection surface in one change | `serve/fetch.py:144-192`, `datasource/postgres.py:237-243` | §4.1 |
| **2** | Replace the EX comparison with BIRD's own | **Every number produced before this is void.** Do not run another ladder until it lands | `eval/grade.py:164-181`; use `../BIRD-Data-Obfuscation/pipeline/_db.py:61` | §5.1 |
| **3** | Delete or fix the oracle "grader ceiling" arm | A baseline that is 1.000 by construction is worse than no baseline — it retired the "grader is the bottleneck" hypothesis on no evidence | `eval/oracle.py:52-60`, `tests/eval/test_eval_contract.py:85-98` | §5.2 |
| **4** | Quarantine `corpora/_variant-authored-20260805`; put `check_train_only` in CI with a genuinely held-out control | The contaminated corpus is on disk, loads cleanly, and passes the gate that exists to catch it | `corpora/_variant-authored-20260805/`, `tools/check_train_only.py:21`, `.github/workflows/ci.yml:46-56` | §6 |
| **5** | Fix the non-monotone fusion | Retrieval's only outright mathematical error, and it points the opposite way to intuition | `retrieve/fuse.py:16-46` | §7.1 |
| **6** | Invalidate dead Postgres connections | Three lines. Decides whether any long run's data is real | `datasource/postgres.py:59-124` | §9.1 |
| **7** | Decide, for each item in §10: wire it or delete it | Deleting is the honest option and the cheaper one. An inert declaration is a lie with a maintenance cost | §10 table | §10 |
| **8** | Either implement the two verdicts or stop claiming them | The product's headline output does not exist in the engine | README.md:1-7, `serve/nodes/stamp.py:120-180` | §4.5 |

Items 1–6 are the gate. **I would not trust a number this repository produces — including the 0.68
target — until 2, 3, 4 and 6 are done.**

Items 7 and 8 are cleanup, but 8 is the one a reader hits first, so it is cheap and high-leverage.

> **All eight landed on 2026-08-06; see §0 for the commit per item and for what changed without
> a measurement.** The instrument items (2, 3, 4) are the ones that unblock the next ladder: the
> grader is now the benchmark's own and fingerprint-identical to it, the oracle arm reports
> *unmeasured* instead of a constructed 1.000, and the contamination producers are deleted with a
> CI gate over their phrases. Nothing has been re-measured yet — no number in this repository has
> been produced by the fixed instrument.

---

## 4. Class A — governance that is declared and not enforced

### 4.1 The `sample` executor reaches Postgres with no guardrail layer, no ledger row, and an unescaped identifier — **[V] critical**

The hardest finding in the audit. `govern/` is the module the previous post-mortem called "the hard
part, and it works."

**Declared.** `govern/__init__.py:6` (G2): "Every executor in `EXECUTOR_PATHS` passes `check()` and
ledgers." `ledger.py:5`: "Every executor writes an entry stamped with its `path`." `ports.py:87`:
"`execute` does no governance check — only `govern.pipeline` may call it." ADR 0006 §7 says it
twice, and says of `sample_rows` specifically that "it still passes `check()` … and it still
applies the exclusion/suspect filter in the tool." `bounds.py:42-51` presents the current
`sample_rows` signature as *the fix* for exactly this class of hole in v1.

**Actual.** `serve/tools.py:223-232` → `serve/fetch.py:178-181` → `datasource/postgres.py:237-243`,
which hand-builds

```python
f'SELECT DISTINCT "{column}" FROM "{schema_name}"."{table_name}" '
f'WHERE "{column}" IS NOT NULL ORDER BY "{column}" LIMIT {int(limit)}'
```

and calls `self.execute(sql)` — the method `ports.py:87` reserves for `govern.pipeline`. No PARSE,
NO_WRITE, FUNCTIONS, BINDING, COLUMNS or TABLES. No `attempt_record`.

`attempt_record(` has exactly two call sites in `src/`, both `path="agent"` (`fetch.py:212`,
`fetch.py:247`) — confirmed by hand **[M]**. So `guardrail_errors == 0` and an empty attempt list
hold **vacuously** for the entire sample path.

Two consequences.

**(a) Policy bypass, unconditional, no attacker needed.** `reliability.status is suspect` columns
stay in `by_id` (`corpus/analyst.py:83-86`); `hard_block_suspect` is enforced only inside `check()`
(`govern/check.py:283-287`). So under one identical policy, **`run_query` refuses a suspect column
and `sample_rows` returns its real values.** ADR 0006 §7 mandates the filter in the tool; the tool
does not have it. (The `governance.excluded` half happens to be covered upstream at
`analyst.py:79` — by accident of a different control, not by this one.)

**(b) Injection, conditional on identifier content.** Postgres has no quote-doubling; its sibling
does it correctly (`datasource/sqlite.py:147-148`). `physical_name` is deliberately unvalidated for
content — `corpus/identity.py:100-104` says it holds the identifier "verbatim — any character, any
case, any script", and `corpus/validate.py:148-166` validates only `slug(physical_name)`. Executed
against the tree: `sample_values('orders', 'x" FROM "pg_catalog"."pg_shadow" -- ', limit=5,
schema='s')` escapes its intended table, and `problems_with()` raises no objection to the asset.

One port method, two implementations, one wrong, **and no test covers identifier quoting on
either**.

### 4.2 The seventh layer cannot run in any shippable configuration — **[V] medium**

`cost_budget` ships UNSET (`register/knobs.py:244`) and `govern/policy.py:78-88` asserts at import
that it must never acquire a default. All ten `GovernancePolicy(...)` constructions in `src/` omit
it, and **no deployment surface can set it** — no env var, no config key, and `int_knob` explicitly
refuses it. The verdict-consumer built for COST is unreachable and uncalled.

Runtime effect is fail-safe, not a hole. But "seven deterministic guardrail layers" appears in the
README, the ADRs and the previous post-mortem, and it is **six**.

### 4.3 `trust()` guards `configurable` only; the state channel is caller-writable — **[V] medium**

The previous post-mortem credits `runtime.trust()` with closing a real hole. It closed half of one.
On `POST /threads/{id}/runs/stream`, langgraph_api forwards the client's `input` dict to the graph
with no schema filtering (`stream.py:204,314`). `_accept_node` (`graph_app.py:226-248`) returns only
`PER_TURN_RESET` plus turn identity, which does not clear the six `TEST_HOOKS` channels or
`identity`, and nothing else does. Because `int_knob` reads state *before* `knobs_resolved`, **a
client can set the routing and bounds knobs directly, and the record then publishes the defaults it
did not use.**

`TEST_HOOKS` has no enforcement reader anywhere in `src/` — only a classification assertion in
`tests/serve/test_state_channels.py:257`.

### 4.4 The real recursion ceiling is 10011 and the client sets it — **[V–] medium**

Correction to the previous post-mortem's §2.8. `recursion_limit = 25` is `langchain_core`'s default
and governs nothing here: langgraph 1.2.10 resolves `DEFAULT_RECURSION_LIMIT` to **10007**
(`langgraph/_internal/_config.py:32`), and under LangGraph Server — the transport the UI uses —
`langgraph_api` supplies **10011**. It is a per-request field the client sets.

So the old finding was right that the bound is undeclared and unhashed, and wrong about the number
by two and a half orders of magnitude. There is effectively **no loop bound** in production.

### 4.5 The two verdicts that define the product do not exist in the engine — **[M] high**

`safety_clearance` and `semantic_assurance` are the two-axis stamp named in `README.md:1-7`, in
`docs/glossary.md`, `docs/architecture.md`, `docs/design-decisions.md`, `docs/openapi.json`, ADRs
0002 / 0004 / 0007, and in the previous post-mortem's own definition of what v2 is.

Grep of the whole repository: **10 files contain those names. Eight are docs, one is the README,
one is `tests/api/test_http_contract.py`. Zero are source files.** `serve/nodes/stamp.py:120-180`
projects `outcome`, `guardrail_errors`, `terminal_reason` and friends — there is no two-verdict
stamp, on any path.

The one test that names them is in the file where 8 of 9 tests are strict-xfail stubs (§11).

### 4.6 `resume_authorised` is a tautology on the shipped HTTP surface — **[V–] low**

`bounds.py:6-7` and ADR 0006 §10 both state that "a `thread_id` is not a capability" (B9). But
`api/routes.py:319-327`'s `_identity(body, thread_id)` returns `{"token": thread_id}` when the body
carries no `identity`, and `thread_id` *is* `body["session_id"]`. So `resume.py:52-55` compares a
string with itself and `hmac.compare_digest` returns True unconditionally.

**Downgraded to low, and the reason matters:** there is no authentication anywhere on this surface
— no `auth` in `langgraph.json`, and `/audit/turns` and `/corpus/assets` are equally open — so
anyone holding the `session_id` can already post arbitrary turns to that thread. The marginal
privilege is nil. Worse, `tests/serve/test_chat_transport.py:105-116` already tests this fallback
and its docstring already concedes it is "a same-thread check, not a same-caller one."

**So this is not a vulnerability. It is `bounds.py` and ADR 0006 stating as a guarantee something
the test suite documents as a deliberate compromise.** Fix the documents.

### 4.7 Smaller, same class

- `canonicalise` rewrites and quotes the invented aliases and CTE names its docstring promises to
  leave alone — **[V] low**
- `sample_rows`' row bound is a model-supplied argument, clamped only from below — **[V] medium**
- An admitted `run_query` attempt that raises outside `check()`'s wrapper writes no ledger row and
  gets its cap slot refunded — **[U] medium**
- `ports.Connector` documents a base class that does not exist and a caller rule four call sites
  violate — **[U] medium**
- All five deterministic guard rules are off in every configuration in the tree, leaving the
  free-text LLM gate as the only live input control — **[U] low**

---

## 5. Class B — the measurement instrument is wrong

**Read this section before quoting any number this repository has produced.**

### 5.1 The EX grader is not BIRD's EX, and it understates systematically — **[V] high**

`eval/grade.py:144` asserts alignment with "BIRD's own EX"; `harness.py:150-153` asserts the result
is "comparable to published BIRD". But `_cell`'s fallback is `return str(value)`
(`grade.py:164-181`, read by hand **[M]**), and `Decimal` is neither `int` nor `float` — so **every
Postgres `numeric` is compared as a string.**

Reproduced against the committed code; all six grade `correct=False` with
`grade_detail="result_mismatch"`, indistinguishable in the artifact from a genuinely wrong answer:

| Predicted | Gold | BIRD's own comparator |
|---|---|---|
| `Decimal('0.5')` | `0.5` | equal |
| `Decimal('100.00')` | `Decimal('100.0')` | equal |
| `Decimal(100)` | `100` | equal |
| `1.0` | `1` | equal |
| `'abc '` (CHAR padding) | `'abc'` | equal |
| `'ABC'` | `'abc'` | equal |

All six are accepted by the comparators shipped with the benchmark being graded
(`../BIRD-Data-Obfuscation/pipeline/_db.py:61` `normalise_result` and `:101`
`normalise_result_strict`).

**Consequence.** Every EX number v2 has produced is an underestimate, and the size of the
underestimate depends on the numeric-column density of the schema — so **cross-schema comparisons
do not hold either.** Fixing this is cheaper than any intervention it would have been used to
evaluate.

### 5.2 The "free grader ceiling" arm is 1.000 by construction — **[V] high**

`eval/oracle.py:52-60` is the else-branch taken when a question carries neither `gold_fingerprint`
nor `gold_columns`+`gold_rows`. It calls `grade_results(pred_columns=pred[0], pred_rows=pred[1],
gold_columns=pred[0], gold_rows=pred[1])` — **the executed gold fingerprinted against itself**,
which returns `correct=True` unconditionally.

No producer in the repository supplies those three keys: `eval/datalake.py:130-139` emits six
fields, `eval/__main__.py:59-69` reads JSONL verbatim. Reproduced:
`oracle_grade({"question_id":"q","gold_sql":"SELECT 'garbage' AS wrong"}, conn)` → `correct=True`.
`tests/eval/test_eval_contract.py:85-98` enshrines it as `ex.value == 1.0`.

This arm exists to establish that the grader is not the bottleneck. It cannot establish anything.
Combined with §5.1 — where the grader **is** a bottleneck — this is the most expensive single
defect in the repository.

Second defect on the same path: `harness.py:56-67`'s oracle branch is a bare list comprehension
with no exception handling, sitting *before* the concurrency dispatch, so **one gold statement that
fails to execute aborts the whole arm and discards every row already computed.**

### 5.3 `--top-n` never reaches the graph but is stamped into the run id — **[V] medium**

It goes into the artifact name, the arm name and the run id. So a sweep over `top_n` produces
differently-named runs, differing config hashes, and identical behaviour. This is the previous
post-mortem's §2.4 defect ("a declared-but-unread knob actively lies") with the lie promoted into
the filenames.

### 5.4 The leakage gate is in no CI job and its default control is the corpus under test — **[U→partial] high**

CI runs four gates (`.github/workflows/ci.yml:46-56`): `check_file_length`,
`check_one_implementation`, `check_measurement_locality`, `check_imports`. The conformance suite's
list (`tests/conformance/test_register_closure.py:215-221`) names those four plus `check_citations`.
**`check_train_only` is in neither, and nothing in `tests/` or `.github/` references it.**

Worse: `DEFAULT_CONTROL = "corpora/gold-semantic-layer-20260804"` (`tools/check_train_only.py:21`)
is the same corpus `.env:18` serves and `tools/run_datalake_eval.py:41` evaluates. The default
invocation compares the shipped corpus against itself; it was run, and reports
`ratio to control: 1.00x (tolerance 2.0x)`. The rate arm is **arithmetically incapable of failing**
for the only corpus it matters for.

And it was pointed at the contaminated corpus of §6.1: **it passes, with statistics byte-identical
to the control.**

### 5.5 Smaller, same class

- `_base_turn` fabricates `prompt_set_hash` and truncates `knobs_resolved`, and `run_comparison`
  has no way to avoid it — **[U] medium**
- 10 of 19 entries in the citation register point at artifact paths that are not in the tree and
  never were; the gate named as their enforcer never looks at the path — **[V–] low**
- `run_live`, the function the data-lake eval module is written around, has zero callers — **[U] low**

---

## 6. Class C — corpus contamination is on disk, not hypothetical

### 6.1 The hand-written BIRD discriminators are materialised in a loadable corpus — **[V] high**

The previous post-mortem's §2.7 was **prospective**: the scripts stayed in the tree, "able to be
picked up by the next corpus build." That has already happened.

`corpora/_variant-authored-20260805/` holds 57 schema directories. **27 of them — every entry in
the `PREFIX` table at `tools/_nuclear_dense_plus_prefix.py:26-53` — carry their hand-authored
sibling-discriminating phrase verbatim in the `summary` field.** Verified programmatically 27/27,
zero misses, and confirmed by hand **[M]**:

```yaml
# corpora/_variant-authored-20260805/soccer_2016/soccer_2016.yaml:4
summary: 'soccer_2016: cricket IPL batsman bowling cricket IPL-style match analytics
  Despite schema name data matches two teams winner venue season. 21 tables — …'
```

`summary` is **the only text that enters either retrieval channel** (`retrieve/index.py:1,42`). The
corpus loads cleanly through the production loader with the contamination intact. It is one
environment variable from being served, and per §5.4 it passes the gate meant to catch it.

### 6.2 Not one corpus asset is under version control, while three files say git is the source of truth — **[V–] high**

`git ls-files corpus corpora` returns exactly two paths: `corpus/.gitignore` and
`corpus/README.md`. Meanwhile `corpus/README.md:3` calls the directory "Git-tracked typed YAML
assets for the semantic layer"; `corpus/.gitignore:1-2` ignores derived projections because "Git is
the single source of truth"; and `.gitignore:231-234` carves an explicit exception for "the
vendored demo corpus in `corpus/` [that] stays tracked" — **a corpus that does not exist.** The
assets were removed in `a506436` and the three documents were never updated.

`.gitignore:235` ignores `corpora/` wholesale: 59,661 files across 8 corpora, untracked. The
`.gitignore`'s claim that they are "reproducible from BIRD-Data-Obfuscation" is not supported by
this tree — there is still no curator module in `src/`.

**The declared moat is neither in version control nor reproducible from anything committed.**

### 6.3 No corpus producer stamps provenance; the function built to prevent that has zero callers — **[U] high**

`corpus/provenance.py:19`'s `restamp_model_authored` has zero callers outside one test. Every
producing script writes `summary` and leaves `audit` untouched (`densify_summaries.py:221`,
`_nuclear_dense_plus_prefix.py:85,109`, `_revise_miss_summaries.py:127,154`,
`_set_asset_fields.py:53`).

The result is checkable on disk: `soccer_2016.yaml` line 4 is a hand-written benchmark
discriminator, and lines 7 and 12 say `source: gold` and
`evidence: live introspection + schema_rename_map meaning`. **No asset records which script, which
model, or which run produced its summary.**

### 6.4 ADR 0008 D9 is declared built; three of its four deliverables do not exist — **[U] high**

`problems_with_corpus` occurs zero times in `src/`, `tests/` or `tools/` — only in the ADR's own
prose. `corpus_problems` is not a record field. "Both entry points read the same function" is false:
only the CLI does (`serve/__main__.py:147`); `api/graph_app.py`, the module `langgraph.json:5` names
as the served graph, contains no occurrence of `fatal_problems` at all. **The server still serves
the corpus the CLI refuses.** The ADR's Phase-1 acceptance measurement (line 440) is a measurement
of a function that was never written.

### 6.5 Smaller

- `slug()` claims injectivity and is not injective — the docstring's own example collides — **[U] medium**

---

## 7. Class D — retrieval fusion

### 7.1 Weighted fusion is non-monotone in evidence — **[V] high**

`retrieve/fuse.py:34-46` averages over only the channels *present* in the score dict, while
`scale_within_channel` (`:16-31`) min-maxes each channel so the weakest document that channel scored
becomes exactly **0.0 — present, not absent.**

With the shipped weights (0.5/0.5, `register/knobs.py:111-114` → `serve/runtime.FUSE_WEIGHTS`), an
asset is **helped** by being found in a second channel only when that channel's scaled score exceeds
the first's, and **penalised otherwise — by exactly 50% when the second channel scaled it to 0.0.**
The min-max floor guarantees the case exists: whenever a channel scores two or more documents with
distinct values, exactly one is scaled to 0.0. And since the vector store returns a cosine for every
candidate (`retrieve/vectors.py:274-277`), that is the normal case on the semantic side.

> **An asset found by both channels can score below an asset found by only one.**

Verified end-to-end through the shipped `serve/nodes/facets.py:_pass_one_hits` with a real BM25 and
a real index. This is a different and more insidious defect than the `max(lexical, semantic)` bug
fixed in `5499ab2`: that one made the semantic channel lose every time; this one makes additional
evidence lower the score.

### 7.2 Pass two fuses BM25 over the rewritten query with cosine over the raw question — **[V] high**

`_accept_node` (`graph_app.py:244`) embeds the **raw last-human message** into
`state["query_vector"]` — the only writer of that key repo-wide. `route_node` passes it through
(`route_retrieve.py:123-130`); `pass_two_retrieve` uses it as one call-level vector for the whole
call (`pass_two.py:67-71`, `232-252`), while its lexical channel searches with the per-facet
`queries`, which `_run_facet` set to the utility-model rewrite (`facets.py:390-392,418`).

`facets.py:325-367` documents the fix — "both channels then search with it; a rewrite that reached
only BM25 would miss the point" — **and that fix applies to pass one only.** Pass two blends scores
over two different texts. Reproduced with a scripted rewriter and a real embedder-backed index.

### 7.3 A turn that retrieves zero tables proceeds to a billed agent loop — **[V] high**

With `(no context)` and no outcome code.

### 7.4 `connect` no longer keeps one connected component per turn — **[V] medium**

And `max_crossings` bounds nothing that matters. The previous post-mortem lists the Steiner planner
under what is "genuinely good" specifically for this property.

### 7.5 Smaller

- Two raw-score fusion sites survived the commensurability fix and still add saturated BM25 to a
  cosine at 0.5/0.5 — **[U] low**
- Pass two has no channel-state observation, so it can lose the semantic channel entirely while the
  record stamps `facet_degraded: false` — **[V–] medium**

---

## 8. Class E — data handling

### 8.1 The declared per-field redaction policy has zero enforcers; the durable log stores verbatim question, answer and raw SQL — **[V] high** (found independently by two dimensions)

- `register/record.py:311`'s `redaction_of()` has **zero callers** in the repository — confirmed by
  hand, only its own `def` and its `__all__` entry **[M]**. The `Redaction` enum (`:68`) is declared
  on 37 register rows and read by nothing.
- `ports.py:156`'s `Sink` port promises "Every record is redacted before write". **It has no
  implementation** — no `record/` package, no `jsonl_sink.py`.
- `govern/ledger.py:94`'s `ledger_entry()` — the only implementation of ADR 0006 §11's retention
  table (`executed` / `statement_sha256` / `statement_shape`) — has **zero production callers**: one
  re-export and four lines in a test file. 45 tests pass against dead code.

What actually reaches disk is `attempt_record()` (`ledger.py:137-153`), carrying `executed_sql`
raw, and `api/trace_store.py:48,65-68`, which writes `question`, `answer_text` and the whole record
verbatim. Verified on the live log: `runs/serve/2026-08-06.jsonl` line 2 holds a full
`generated_sql`; line 3 holds `… WHERE c."county" = 'ARECIBO' …` — **raw SQL with literals.**

### 8.2 `credentials.py`'s "never for `src/`" guarantee is false, and the server injects all of `.env` into `os.environ` — **[V] medium**

`tools/credentials.py:1` promises "One reader for credentials, for tests and tools. Never for
`src/`", and `:15-18` cites `tools/check_imports.py` as what keeps the layering. Both halves are
false: `api/graph_app.py:69-76` — the graph factory `langgraph.json` points `graphs.serve` at, also
imported by `api/routes.py:16` — and `serve/__main__.py:49-52` both import it and call
`load_into_environ()`, which copies **every** key in `.env` into `os.environ` with no allowlist.
`check_imports.py` checks nothing about this.

### 8.3 Smaller

- The retrieval index is built from unfiltered assets while only the analyst corpus is filtered —
  B10's two definitions of "excluded" both still exist — **[V–] medium**
- `.env.example` justifies having no trace masking by citing a datasource-level sensitive-column
  filter that does not exist — **[U] medium**
- 257 MB of untracked archive zips sit in the working tree and are not gitignored, while the
  directories they archive are — **[V] low**

---

## 9. Class F — runtime durability

### 9.1 One connection blip silently poisons the rest of a run — **[V] high**

`PostgresConnector.execute` (`postgres.py:108-124`) and `introspect` (`:126-188`) **never clear
`self._conn` on failure**, and `_connect` (`:59-61`) returns the cached handle unconditionally.
`close()` is the only writer of `_conn = None`, reached only from `__exit__`/`__del__`.

Reproduced against psycopg 3.3.4: injecting one `OperationalError('the connection is closed')`
yields `QueryError` on three consecutive `execute` calls and on `introspect`, with the connector
still holding the dead handle.

**Secondary defect that makes it invisible:** psycopg raises that error with `sqlstate=None`, so
`_raise_classified` falls past the class-42 test (`:90`) and the 08/53/57 test (`:92`) and reaches
`:106`, raising `QueryError` — **a query-fault verdict for an infrastructure fault.** So after one
network blip, every remaining question in the run is recorded as "the model wrote bad SQL." The
SQLSTATE taxonomy has zero consumers in `src/`.

### 9.2 The SQLite connector classifies errors by prose regex — **[U] high**

And misclassifies exactly the two faults its acceptance test singles out. That test is
Postgres-gated and never runs against SQLite.

---

## 10. Class G — declared machinery with no wire

Individually minor; collectively this table **is** the diagnosis in §2. Each row is a declaration
the codebase makes about itself that nothing enforces or consumes.

| Declared | Real readers / callers | Tag |
|---|---|---|
| `redaction_of()` + `Redaction` on 37 record fields | 0 | **[M]** |
| `ledger_entry()` — ADR 0006 §11's strong projection | 0 (45 green tests) | **[V]** |
| `restamp_model_authored()` | 0 | **[U]** |
| `problems_with_corpus` (ADR 0008 D9) | does not exist | **[U]** |
| `classify_row()` — named by the register as the outcome-tier reader | 0 | **[U]** |
| `embedding_knobs()` | 0 | **[U]** |
| `run_live()` — what `eval/datalake` is written around | 0 | **[U]** |
| `llm_temperature` (comparability knob) | 0 | **[U]** |
| `final_sql_source`, `cache_read_tokens`, `cache_write_tokens`, `latency_sec` | 0 writers, permanently null | **[V]** |
| `lexical_coverage` (decision tier) | hard-coded 0.0 on every production turn | **[V]** |
| `n_re_served == 0` quotability gate | no path writes a nonzero value; can never fail | **[U]** |
| context eviction witness | deleted by the delivery merge before `stamp`, and no readers anyway | **[V]** |
| knob `Role` taxonomy | no config hash, no resume-drift check exist | **[U]** |
| `ports.py`'s "≥2 adapters per port" | 3 of 5 ports have 0; the named adapter files do not exist | **[U]** |

`latency_sec` deserves its own line: **no clock is read anywhere in `src/governed_bi`** — no
`perf_counter`, no `monotonic`, no `time.time()`. Latency is not merely unrecorded; it has never
been measured.

Carried forward from the previous post-mortem, still true, same class: `prompt_set`, `chat_model`,
`facet_model`, `rewrite_model` and `expand_hops` all have zero readers and all enter the config
hash.

---

## 11. Class H — documentation and ADR drift

The README was rewritten honestly in the 2026-08-05 cleanup and is no longer a trap — except for
§4.5, which it opens with. The rest of `docs/` was not audited then.

- **`docs/openapi.json`** is declared "the spec-of-record" by two ADRs and by `api/routes.py`'s
  docstring. It differs from the served app on **9 of 18 paths** — missing 6 served routes,
  specifying 3 that do not exist, including a write route the engine refuses to have. Nothing
  checks it. — **[V] medium**
- **ADR 0011** is Accepted and asserts "all five facets now call one [model]". `facet_schema` calls
  none, and the ADR's own caveat is now inverted. — **[V–] medium**
- **ADR 0005 and 0006** — the two the docs index tells you to start with — have Status fields
  saying packages are absent that are present in `src/`. — **[U] high**
- **ADR 0004**'s Status says the durable conversation checkpointer shipped; the tree has only
  `InMemorySaver`, and `pyproject.toml` says the packages were rejected. — **[U] medium**
- **`architecture.md`** describes the `rewrite` node as a utility-model facet rewrite. It is a stub
  that calls no model. — **[V] medium**
- **`glossary.md`**, which declares itself canonical for the current tree, defines `NoteAsset` and a
  "notes" asset type that do not exist; the ADR it cites says they were deleted. — **[V] medium**
- **`design-decisions.md`** was reduced to a 29-line index with zero D-numbered entries, while 15
  ADR cross-references still cite D2/D5/D6/D8/D9/D11/D15/D17/D18 and specific line ranges in it. —
  **[U] medium**
- **`register/citations.py`** enforces "every number carries an artifact path" while 10 of its 18
  artifact paths point into a directory git has never tracked; the conformance test only checks the
  string is non-empty. — **[V] medium**
- **Twelve dangling relative links** in the surviving ADRs, including `lessons-from-v1.md`, which
  two ADRs name as their evidence base and which was deleted — plus 20 inline `L§`/`L-R` citations
  into it. — **[U] medium**
- **`uv sync --extra bedrock`** is the second install command in both `README.md` and
  `docs/usage.md`. `pyproject.toml` states the extra was deliberately removed; the command exits 2.
  `usage.md` also names a DSN variable nothing reads. — **[U] medium**
- `pyproject.toml` says CI gates mypy over a file list living in `ci.yml`; `ci.yml` has no mypy step
  and no such list. — **[V] low**
- uvicorn is a declared runtime dependency with zero importers, under an explicit claim that every
  entry was grep-verified. — **[U] low**

---

## 12. Class I — the test suite — **all [U]**

The verification pass for this dimension was cut off. Every item here is a single auditor's
unchecked claim. They are listed because if even half hold, "811 passed" is not the credential the
previous post-mortem treats it as.

- The figure reproduces (837 collected at the time of audit), but **25 of the 26 xfails are
  `pytest.fail("not implemented")` stubs** — specifications, not tests.
- **216 of 837 tests (25.8%) are one tautological parametrized grid.**
- The grader executes model SQL outside `govern`; the tests that would say so are parked as "body
  not yet written."
- A no-model stub answer is indistinguishable from a real answer on every field a quotability gate
  reads.
- The step-vocabulary closure test compares a hand-written list to the register, never to the code
  — and `narrate` has already escaped it.
- `test_every_declared_ranking_knob_has_a_reader` is a four-name allowlist, not a closure over the
  40 comparability knobs.
- **CI runs neither Postgres nor any real SQL execution.** "811 passed" is a local number.
- The context eviction ladder is tested only at a budget 266× smaller than the shipped one.

**The structural question this section exists to ask:** a suite can pass 811 assertions while every
finding in §4, §5, §8 and §10 is simultaneously true. What is it asserting?

---

## 13. Class J — defects in the previous post-mortem

The document that warned "documentation cannot be used as evidence" cannot be used as evidence.
This section is why it was replaced rather than appended to.

### 13.1 §2's framing measurements are the freeze commit's, not HEAD's — **[V] high**

The preface added 2026-08-06 told the reader that §1–§3 are "an audit of *this* tree, at *this*
commit, which is the one you are reading it on," and that "every path it names can be opened
directly rather than through `git show`." Both halves were false.

| Quantity | Claimed | Actual at `9a3dc4b` |
|---|---|---|
| `src/` .py files | 105 | 105 ✓ |
| `src/` lines | 23,658 | **19,818** [M] |
| Docstring share of `src/` bytes | 36.0% | **25.7%** |
| `docs/` markdown files | 89 | **16** committed (17 on disk) |
| `docs/` bytes | 2.15 MB | **0.37 MB** markdown / 0.42 MB tree |
| ADRs + plans | 9,545 lines | **5,571** |

One row of seven was right. The verifier reproduced the 36.0% figure exactly against
`git archive 8745b44 src`, confirming every claimed value is the freeze commit's.

### 13.2 §3.4③ "execute all three candidates" crashes the turn and erases the ledger — **[V] high**

`GovernedAgentState.result_table` (`agent_state.py:38`) is the only channel that class declares
**without a reducer** — `attempts_by_call`, `tool_delivered` and `clarifications_by_call` all carry
`Annotated[..., merge_by_call]`. LangGraph backs it with a LastValue channel that raises
`InvalidUpdateError` on a second write in one super-step, and every successful `run_query` writes it
(`tools.py:290`).

Reproduced by executing the current tree with the real `agent_core_node`, real `build_tools`, real
`prepare()`/`check()` and a counting connector:

| Parallel `run_query` calls | Outcome | Ledger rows | Statements actually executed |
|---|---|---|---|
| 1 | `answered` | 1 | 1 |
| 3 | **`crashed`** | **0** | **3** |

So the prescription's stated benefit — "three candidates are three ledger rows, which is a clearer
audit trail than three sequential retries" — is exactly inverted: three statements run against the
database and **nothing is recorded**. Any k>1 candidate work must add a reducer to `result_table`
first.

### 13.3 "Five out of five have a generate-several-then-choose stage" is four out of five — **[V] medium**

The fifth row, the LangGraph official SQL agent, is single-candidate: `generate_query` emits one
query and `check_query` is a single-query review node. The sentence is the load-bearing claim of
§3.2.

### 13.4 The cost arithmetic that justifies moving budget off L1 is wrong in both directions — **[V] medium**

"Six L1 calls per turn (one scope gate, four facet rewriters, one narrator)" overcounts: `narrate`
normally calls no model — `narrate_node` returns `{}` for refuse/decline/crashed (`narrate.py:56-57`)
and returns the agent's adopted prose (`:59-61`) before `_generate` is reachable, and its own
docstring says "it usually calls no model." L2 is undercounted. The compute-allocation
recommendation was sized on this.

### 13.5 §2.8's "repository-wide grep: zero hits" is false, and hides a worse defect — **[U] medium**

See §4.4: the real bound is 10011 and the client sets it.

### 13.6 ReViSQL is cited accurately but against its own conclusion — **[U] medium**

The paper is real and the numbers quoted are right; its argument is not that multi-candidate
generation is where the gain is. It is being used to license a change it does not support.

### 13.7 What §2.6 said about the README was already fixed when the document was placed here — **[V–] medium**

The README had already been rewritten. The finding was manufactured against a README that says the
opposite.

---

## 14. Carried forward from the previous post-mortem

Still true at `9a3dc4b`, re-verified in the session that wrote this document **[M]**. These are
*not* superseded by anything above.

| Finding | Status |
|---|---|
| `messages` grows without bound; zero hits for `trim_messages`, `SummarizationMiddleware`, `pre_model_hook` | open |
| `agent_core.py:82` writes every `ToolMessage` back to the outer channel | open |
| `inspect_schema` has no output cap; `read_body`'s is 80,000 chars per call | open |
| The 80,000-char `assemble` eviction ladder guards the half that does not grow | open (and see §10 — its witness is deleted) |
| `prompt_set`, `chat_model`, `facet_model`, `rewrite_model`, `expand_hops` — zero readers, all in the config hash | open |
| `select(overrides)` has no non-test caller; the only way to change a prompt is to edit `default=` | open |
| `route_top_n` / `candidate_depth` / `context_budget_chars` are production constants, settable only from eval | open (and see §4.3 — also settable by any client) |
| `tests/conformance/` asserts no deployment surface | open |
| `governed_bi.toml` is inert | disclosed in a header comment; the dead `[notes]` section survives, and two files still cite the file as authoritative |
| No curator module in `src/` | open (and see §6) |
| The bi_scope gate parses free-text `YES`; facet rewriters emit free text; zero `with_structured_output` in `src/` | open |
| One SQL candidate, no candidate set, no selection, no execution voting | open |
| No deterministic value binding before generation | open |
| `row_count == 0` returns `status: ok` | open |
| Repair is one undifferentiated error string, capped at 3 | open |

---

## 15. What v2 is, corrected

An agentic BI engine: natural-language question in, governed read-only SQL out, with an audit
trail. A curated semantic layer of typed YAML assets is retrieved against, a model writes SQL, the
SQL passes deterministic guardrail layers, and executes read-only.

Corrections to the previous description, all established above:

- It is **six** layers in any shippable configuration, not seven (§4.2).
- The turn is **not** stamped with `safety_clearance` and `semantic_assurance`. Those exist only in
  documentation (§4.5).
- `sample_rows` does not pass through governance at all (§4.1).
- The Steiner planner does not guarantee one connected component per turn (§7.4).

The serve path at `9a3dc4b`:

```
guard(LLM scope gate) → rewrite(stub — calls no model) → negative_gate
  → fanout ─┬─ facet_schema   (raw question — rewrite deliberately disabled)
            ├─ facet_term     (utility model rewrite)
            ├─ facet_metric   (utility model rewrite)
            ├─ facet_entity   (utility model rewrite)
            └─ facet_example  (utility model rewrite; semantic channel only)
  → route(top_n schemas) → resolve(pass-two budgets)
  → connect(Steiner join over components + join completion) → assemble(render context block)
  → agent_core(create_agent loop, 5 read-only tools) → narrate(usually no model) → stamp
```

**What is genuinely good and expensive to rebuild** — judged after this audit, so the list is
shorter than the previous one:

- The guardrail layers themselves, on the `agent` path. Identifier canonicalisation, the
  `ToolBounds` licensing surface, the seven-rule structure. The defects found are in what bypasses
  them, not in the layers.
- `corpus/` — the asset contract, deterministic ID derivation, the reference-integrity validator.
- `serve/wrap.py` — node exception → recordable `crashed` outcome.
- The two-channel retrieval structure and the Steiner formulation. §7.1 and §7.2 are bugs in the
  scoring, not in the design.

---

## 16. The forward design, with the corrections applied

Retained from the previous document because the analysis is still the best available, minus the
claims §13 falsified.

### 16.1 What the field does

| System | Stages |
|---|---|
| **CHESS** (Stanford) | Information Retriever → Schema Selector → **Candidate Generator (multi-candidate + iterative refinement)** → **Unit Tester** |
| **CHASE-SQL** (Google) | **Value Retrieval** → **Candidate Generator (3 generators)** → Query Fixer → **Selection Agent (pairwise)** |
| **XiYan-SQL** | Schema Linking (columns **and values**) → Candidate Generation (M-Schema + self-refinement) → **Candidate Selection Agent** |
| **Agentar-Scale-SQL** (Ant Group; BIRD test 81.67%) | Internal + sequential + **parallel scaling with tournament selection** |
| **LangGraph official SQL agent** | list_tables → get_schema → generate → check_query → run_query — **single-candidate** |

**Four of the five** have a generate-several-then-choose stage (corrected from "five of five", §13.3).
v2 has one generation.

Three numbers that should shape the work:

- **BIRD-CRITIC**, which measures self-correction specifically: humans 76.67%, frontier models
  44–45%. v2 bets everything on the one capability that is weakest.
- **Agentar-Scale-SQL**'s authors state it has high latency and is unsuitable for real-time
  applications. The leaderboard recipe does not fit a 30–120s budget as-is.
- **ReViSQL** reaches 93.2% EX on an expert-verified BIRD Mini-Dev, and its 30B variant matches
  prior SOTA at 7.5× lower cost. **Caveat (§13.6): the paper's own argument is not that
  multi-candidate generation is where the gain comes from.** Read it before citing it.

### 16.2 Stage-by-stage

Three tiers: **L0 deterministic (no model)**, **L1 small model**, **L2 main model**.

| Stage | Tier | What it should be | v2 |
|---|---|---|---|
| Scope gate | L1 or L0 | Structured output, or deterministic rules | LLM free-text `YES` |
| **Value retrieval** | **L0** | Column-value index; bind the question's literals to real values | only a `sample_rows` tool the model must remember to call — and see §4.1 |
| Schema linking | L0+L1 | Two-channel retrieval, measured by recall@k | facet fan-out + Steiner — good design, see §7.1/§7.2 for the scoring bugs |
| Schema formatting | L0 | Compact structured format | ✅ `context.py` terse/roster folding |
| **Candidate generation** | **L2** | **2–5 diverse candidates** | **1** |
| **Repair** | L0 routing + L2 | **Dispatch on error class** | one undifferentiated error string, ≤3 times |
| **Selection** | L0 or L1 | Execution-result consistency vote / unit tests | **absent** |
| Verification | L0 | Empty-result detection, row-count anomaly | **absent** |
| Narration | L1 | Small model, reads the table only | ✅ — and it usually calls no model at all (§13.4) |

### 16.3 Changes by return on effort

**① Error-class-driven repair — no new model calls, one function.**

| Case | v2 returns | Should return |
|---|---|---|
| `r_table_not_licensed` | `run_query refused: …` | reason **+ the licensed table list** |
| `r_unbound_reference` | same | reason **+ instruction to call `inspect_schema` first** |
| driver `column does not exist` | raw driver text | error **+ auto re-inject that table's schema** |
| **`row_count == 0`** | **`status: ok`** | **flagged actionable: check literals, suggest `sample_rows`** |

On BIRD an empty result is usually a **wrong literal** (`type = 'Residential'` where the column
holds `'R'`), not an absence of data. v2 discards that signal: the agent narrates "no rows matched",
EX scores zero, and every artifact reports a healthy turn.

**② Deterministic value binding before generation — no model at all.** Match the question's
literals against a column-value index and put the hits in the context block. One of the largest
single gains available on BIRD, and it is what BIRD's `evidence` field has been substituting for.

**③ k=3 candidates with execution-consistency voting — blocked, see §13.2.** The idea still holds:
three read-only, row-capped, already-ledgered executions, hash the result sets, take the majority,
no selector model. **But it cannot be built until `result_table` has a reducer** — today three
parallel `run_query` calls execute all three statements and then crash the turn with an empty
ledger. Add the reducer first, then split `run_query_attempt_cap` into a generation budget and a
repair budget.

**④ Separate exploration context from generation context.** v2 does schema exploration and SQL
writing in one loop over one `messages` list, so every raw `inspect_schema` JSON is still in context
when the SQL is written. Same root cause as the unbounded-`messages` finding in §14.

**⑤ Structured output.** Replace the `YES`/`NO` string comparison and the free-text facet rewriters
with `with_structured_output` or forced tool calls.

### 16.4 Target shape

```
[L0 deterministic]  value binding · identifier canonicalisation · guardrail check
                    Steiner join · context rendering · error classification
                              ↓
[L1 small model]    scope gate (structured) · facet rewriting (measured-positive ones only)
                              ↓
[L2 main model]     ┌─ candidate A (direct)          ─┐
                    ├─ candidate B (divide & conquer) ─┼→ all check+execute → result-hash vote
                    └─ candidate C (execution plan)   ─┘        → repair loop (error-class driven)
                              ↓
[L0 deterministic]  empty-result / row-count anomaly detection → retry or degrade
```

Move budget from the L1 calls to parallel L2 candidates, and replace reliance on model
self-correction with deterministic error classification plus execution voting. **Neither move can
be evaluated until §3 items 2–4 are done**, because the instrument that would score them is wrong.

---

## 17. Audit provenance

- **Run.** 2026-08-06, against `9a3dc4b`. Ten parallel dimension auditors, each finding handed to an
  independent adversarial verifier instructed to default to "the finding is wrong". A completeness
  critic and a follow-up round were planned and did not run.
- **Scale.** 56 agents, 50 completed, ~4.2M subagent tokens, ~1,600 tool calls.
- **Outcome.** 44 findings verified: 30 CONFIRMED, 11 PARTIAL, 0 REFUTED, 3 without verdict. 20
  overflowed unverified. The `tests` dimension's verification never ran.
- **Reproducing a claim.** Every finding here is a `file:line` in this tree. The method is in §1.
  If a number in this document does not reproduce, that is a defect **in this document** — fix it
  here. That is the failure mode §13 documents, and the reason this file was rewritten instead of
  appended to.

### Sources

- [LangGraph — Manage short-term memory](https://docs.langchain.com/oss/python/langgraph/add-memory) ·
  [Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in) ·
  [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) ·
  [Custom SQL agent](https://docs.langchain.com/oss/python/langgraph/sql-agent)
- [Agentar-Scale-SQL](https://arxiv.org/abs/2509.24403) ·
  [CHASE-SQL](https://arxiv.org/html/2410.01943v1) ·
  [CHESS](https://arxiv.org/html/2405.16755v1) ·
  [XiYan-SQL](https://arxiv.org/html/2411.08599v2) ·
  [ReViSQL](https://arxiv.org/abs/2603.20004) (see §13.6) ·
  [BIRD / BIRD-CRITIC](https://bird-bench.github.io/) ·
  [NL2SQL Handbook](https://github.com/HKUSTDial/NL2SQL_Handbook)
- [Effective context engineering for AI agents — Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
