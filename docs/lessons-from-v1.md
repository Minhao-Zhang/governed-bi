# Lessons from v1

The v1 implementation (`src/`, `tests/`, `scripts/` — 86,746 lines) is being
deleted and rewritten. Most of that code is cheap to rewrite. This document is
the part that is not: **the failures it took a paid experiment run to find.**

Extracted 2026-08-02 by six readers over `analyst/`, `curator/`, `eval/`,
`retrieval+corpus+graph+gateway/`, `api+viz+infra+scripts/`, and `tests/`,
before deletion. Line references point at the pre-deletion tree (commit
`35d024a`); they resolve through `git show main:<path>`.

**Read this before writing v2 code.** Every entry cost something real — a
discarded run, a published wrong conclusion, or money.

---

## The five rules that cost the most

### R1. "Not measured" and "measured zero" must be different values, everywhere

The single most-repeated defect in the codebase — **at least 25 independent
recurrences**, each found as its own incident:

```python
len(ledger or [])           # "no ledger recorded" → "empty ledger"
round(x or 0.0, n)          # unrecorded field → measured zero
sum_token_usage([])         # → dict of zeros, priced a whole run as free
not r.get(key)              # ABSENT lands in the FALSE stratum
s.get(k) or 0               # a gate that was never computed → passes
(total_schemas or 0) <= 1   # missing field → "routing bypassed" → miss suppressed
```

Consequences actually observed: an arm that scored zero rows reported `0.0`
rates and **passed the quotability gate**; `routing_degraded_rate` returned
`0.0` when no turn recorded a channel ("the most misleading thing the field
could say"); `corpus_content_hash == "unknown"` compared equal to itself, so
two runs **with no recorded treatment at all** passed the comparability gate;
`refuse_gate._rate` still returns a perfect `0.0` false-refusal rate over an
empty answerable set.

**For v2:** a three-valued rate type (measured / unmeasured / inapplicable)
baked into the metric layer *including the rounding and formatting helpers*; a
ban on `or`-defaulting numeric and collection reads at every producer boundary;
an observed-count published beside every rate.

### R2. The treatment must be proven delivered before any comparison is quoted

**Two published null results were interventions that never reached the model.**

1. The Simulated-SME arm read its clarification ledger from a path a build step
   had already moved, folded nothing, and produced a corpus **byte-identical to
   `curated`**. "SME adds no accuracy" stood for weeks.
2. An "oracle" corpus of **9,154 gold business rules** wrote every note with
   `scope: ['<schema>']` where matching requires `schema:<name>`. All 9,154
   failed to match, the median per-question prompt moved by **one token**, and
   "+5 questions, not significant" was published as proof that enriching the
   semantic layer is an exhausted lever — with a roadmap written on top of it.

Both produced clean, different-looking numbers, because the model is
nondeterministic.

**For v2:** a per-row `context_hash` plus the **ids** of every injected asset,
and "the arms delivered materially different context" as an artifact-derived
**hard precondition** for quoting any comparison, failing closed when the fields
are absent.

### R3. One population per metric name, defined once, shared by headline and test

`summarise_rows` computed `ex_no_twin` over rows both gradeable and twin-free
(n=1085); the significance test built its twin-free stratum from the twin stamp
alone (n=1236). On 2026-07-31 `curated → curated_sme` that reads **+0.09pp as
the headline and −0.16pp beside the p-value** — opposite signs for one quantity
in one file.

**For v2:** numerator and denominator come from one filtered population object;
metric families share it; a test asserts the stratum's net reconstructs the
headline delta to floating-point equality.

### R4. A gate that reads a field nothing writes is not a gate

Repeatedly, **the evidence was already in the artifact** and the gate did not
read it:

- `COMPARABILITY_KEYS` was correctly derived from `MANIFEST_KNOBS`, but the
  ledger *record* was built from a hand-written subset — so eight gates were
  dead, including `corpus_content_hash`, the one labelled "the corpus IS the
  treatment". An absent key cannot make a diff.
- `schema_route_degraded` was added specifically to make a silent BM25 fallback
  visible. It reached `summary.json`. `quotable()` read neither it nor
  `n_routing_degraded`. A schema-pick accuracy of **69.9% was published while
  the embedding endpoint was rate-limited; re-measured at 91.0%**.
- Four kinds of attrition (absent-from-Postgres, failed-to-build,
  gold-unverified, curator-withheld) collapsed into one "covered N schemas"
  number. A run against a partially-loaded Postgres **scored 40 of 69 schemas
  and reported full coverage of what it attempted**.

**For v2:** wire every degradation counter into the gate **in the same commit
that introduces it**; derive the record projection from the same register as the
gate list; add a test that every gate key is non-absent in a real record.

### R5. EX cannot resolve what this project is testing

Observed McNemar discordance between adjacent arms is **16–20%**, putting the
minimum detectable effect at **3.23% (n=1351)** and **2.64% over the full
2030-question split**. The interventions under test move **1–2pp**.

Ladder cost: deepseek **$16**, luna **$60**, Opus-4.8 **$4,065**. Tiers 2 and 4
resolve the same effect size; the extra $2,778 buys a different absolute EX and
nothing else.

Meanwhile the grader ceiling is **1347/1351 = 99.70%** (`--oracle-only`, no
model, ~4 minutes, free), so 56.3% must be read against ~100%. A planning figure
of "69 unwinnable questions" was verified as **4** — a 17x overstatement.

**For v2:** build around deterministic proxies (shortlist recall, column recall,
pick accuracy) with a stated conversion factor. Treat the paid ladder as
confirmation, never screening. Run the free ceiling measurement first.

---

## 1. Measurement validity

**Crashes were counted as refusals, and arms do not crash equally.** A solver
exception and a governed refusal both arrived as "no SQL", so `refusal_rate`
absorbed the crash count and EX absorbed the loss — by a *different amount per
arm*, because a rate-limit storm hits whichever arm is serving. A `NameError`
in a tool helper sat in the serve path for a long time looking like an
intermittent model hiccup. Fixed by one central vocabulary (`stages.py`)
producing a complete partition. Permanent trap it had to dodge:
`OperationalError` is **not** an infra class, because sqlite wraps "no such
column" in it.

**Nine competing failure vocabularies** (ledger verdicts, guardrail layers, the
two-axis stamp, free-text `refused_by`, curator verdicts, validator codes,
grader error strings, note-gate pass/skip, the offline analyser's buckets) made
"which part is breaking?" uncomputable. `stages.py` exists to end that. Its
design rules: two orthogonal axes (`Outcome` = what happened, `Stage` = where);
gradeability is a **third** thing, deliberately excluded; stage names reuse what
the event stream already emits; declared-but-unemitted stages are kept on
purpose. **Port this module nearly verbatim — it is the cheapest artifact in
the repo and the most expensive lesson.**

**MDE was computed at the replicate's sample size and compared against nets from
a different one.** A 300-question replicate at 10% discordance yields 15.3
questions; the honest threshold at 1351 is 32.6. **Every delta between 15.3 and
32.6 was stamped `resolvable: true`.** The discordance *rate* travels between
populations; `n_pairs` does not.

**Zero observed discordance was read as zero noise**, making `resolves()` true
for any effect including no effect — worst exactly on small runs, where zero
disagreements is unremarkable. Fixed with the rule of three (0 events in n
bounds the rate at ~3/n) plus a flag saying the floor is a bound, not a
measurement.

**Six pairwise tests published raw = ~26% family-wise false-positive rate.**
Four arms is six pairs. The family must exclude errored pairs, zero-overlap
pairs (whose `p=1.0` is arithmetic, not measurement), and off-ladder replicate
pairs. Computed in two places, so every fix landed on one side first.

**2000 questions nested in 69 databases are not 2000 independent observations.**
A corpus change suiting five databases produces a hundred correlated "wins".
The cluster-level sign test is deliberately *less* powerful — the extra power of
the question-level test is borrowed against an independence assumption the data
does not support. "Nothing mapped" must serialise as `None`, not as `p=1.0`.

**A consistency check that was an algebraic identity.** `n_correct_unaccounted`
subtracted five buckets, one of which was itself defined as the residual — so
the check was identically zero for any input and shipped alongside impossible
values. **Count every bucket from a predicate; never by subtraction from the
thing it checks.**

**A metric taken downstream of a collapsing step measures the collapse.**
`routing_recall` and `schema_pick_accuracy` were bit-identical on every arm
(verified row-by-row on all 1351 rows) because `route_llm_pick=True` sets
`routed = frozenset([picked])` before the metric is taken. Split apart:
shortlist **0.952**, pick **0.873** — **106 questions had gold in the shortlist
and the pick chose otherwise; only 3 still answered correctly.** Two thirds of
routing loss is the pick discarding what retrieval already found.

**Structural train/test twins.** Id-level disjointness says nothing about test
questions whose *gold SQL statement* already exists in train: **246 of 2030
(12.1%)**, up to 46% in one schema. Frozen `VALUES` gold contaminated three
metrics in three directions at once (it normalises to one canonical form so it
twins everything; it parses to zero tables so it reads as an over-join; counted
as a generation failure it inflates every error class). The dataset also ships
`order_sensitive_qids.json` — **25 of 2030 questions (1.23%)** it tells you to
exclude — and nothing in the repo ever opened it.

**The harness is uniformly more permissive than serve on four knobs**:
`grade_semantic_failures=True` (production `False`), `hard_block_suspect_columns=False`,
`schema_route_top_k=10` (serve default 3), `schema_route_llm_pick=True`. Only
the routing pair reached `MANIFEST_KNOBS`. **No arm in any run has ever been
served on the serve defaults.**

**Error classes are incidence, never levers.** 61% of wrong answers were wrong
along more than one dimension, so summing per-class counts double-counts. One
report published "+46 points of headroom" and revised it to "3–5" with nothing
in between. Get headroom from a counterfactual arm, not from summing classes.

**Diagnostic arms must not share analysis buckets with fair arms.** An oracle
rung records no shortlist, giving `gold_schema_rank=None` — the same value as a
real retrieval miss — so a bucket documented as "retrieval never surfaced the
schema" published at **a perfect score over 2030 rows** off a run where
retrieval never ran. Oracle rungs also stamped `schema_pick` from the answer
key, publishing `schema_pick_accuracy: 1.0` for a picker that never ran.

---

## 2. Retrieval and indexing

**The "embedding beats BM25 2x" number was wrong by 2.4x and was repeated in six
places including an operator-facing warning — and a test asserted it.**

| | embedding | BM25 |
|---|---|---|
| recall@1 | 0.694 | **0.736** |
| recall@3 | 0.852 | 0.844 |
| recall@10 | **0.953** | 0.906 |

*(`runs/ablation/e1-shortlist-curated.json`, 2026-07-31 curated corpus, 57
schemas, all 1351 test questions, `text-embedding-3-large`.)* Re-measured RRF
wins at @1 (0.733) and @3 (0.871), loses at @10 (0.922). **"Don't fuse" is right
at top_k=10 and wrong at a tight one.** The retired figure was the *stated
reason* for not fusing.

**Mean-by-concatenation is the wrong pooling for wide schemas.** The embedding
channel embeds one document per *schema*, and `works_cycles`'s document is 73
tables concatenated — a question about sales orders is matched against a vector
averaging payroll, purchasing and geography. Max-pooling over per-table vectors
(`tbl_max`) costs the same tokens and reuses vectors already needed.

**BM25 IDF was computed within-schema, not corpus-wide**, because the job is
discriminating 73 sibling tables: a term every `works_cycles` table carries
("business", "entity") is worthless for that choice even if globally rare. **A
unified global index loses this** — worth measuring before assuming it does not
matter.

**The LLM picker's 15 candidate tables were filled alphabetically**
(`SCHEMA_PICK_MAX_TABLES = 15`, sorted on `physical_name`). On a 73-table schema
that is "a coin flip dressed as evidence". Measured on `mondial_geo`: **0/42
table and 0/275 column descriptions** — one of the six schemas where the curator
agent wrote nothing.

**Per-type retrieval budgets exist because a single pooled cut let a flood of
matching few-shots crowd every table out**, and the grounding fixpoint cannot
rescue a table nothing points at. Types with no budget entry are dropped
entirely (`budgets.get(cls, 0)`) — which is why `NegativeExampleAsset` was
structurally unreachable.

**`lexical_coverage` exists because with an embedder every asset scores above
zero**, so "what is the airspeed of a swallow" returns `top_k` tables and a
clean run stamps confidence. It is deliberately vocabulary-level and **not** a
score threshold: a fused RRF rank is not comparable across questions, and raw
BM25 on a small corpus is dominated by IDF noise.

**Cost incidents, all from rebuilding per question:**

- A simultaneous N-way embedding burst from parallel workers **took down a run
  and a co-running one**: ~118k embedding tokens per build × 24 workers ≈ 2.8M
  against a 1M-tokens-per-minute account limit.
- Asset-vector rebuilds: **994 builds for 171 distinct corpora**, 1.21M
  embedding tokens for 212k tokens of distinct text, and **~1.7 GB** of
  redundant vectors held across workers (a 3072-dim vector is ~97 KB as a Python
  list).
- `schema_documents()` deep-copying the corpus per question was **55% of the
  serve path's non-model CPU** — and `deepcopy` holds the GIL, so it capped what
  the `--workers` knob could buy.
- Every turn embedded its question **twice**.
- Rebuilding a BM25 index per question spent **25 minutes at 97% CPU** in an
  offline sweep.

**Cache keys.** An id-keyed vector cache is right *inside* one immutable corpus
and catastrophic process-wide: the ladder serves baseline/seeded/curated in one
process and curation rewrites descriptions **in place under the same asset id**,
so the curated arm would score against the baseline arm's vectors — a
wrong-answer bug no test and no artifact would show. Cross-variant caches must
be content-keyed, and every key must include **model + dimensions**, because
`cosine` returns `0.0` on a width mismatch instead of raising (a cross-model
cache hit degrades routing to "nothing scores" with no error).

**Blank documents are provider-dependent.** `asset_document` returns `""` for
joins by design. OpenAI accepts it and returns a vector that can score above
zero and pollute the ranking; **Bedrock Titan rejects it and kills the turn.**

**Bare-name resolution in a pooled lake resolves to whichever schema loaded
last.** `users` exists in many schemas. Rule: qualified `schema.table` always
resolves; a bare name resolves **only** when exactly one table corpus-wide
carries it; ambiguous bare names resolve to nothing.

**Postgres `synchronize_seqscans` defaults ON**, so an unordered `LIMIT n`
returns different rows depending on what else is touching the table — and
`profile_database` samples with exactly that shape. Observed: the same schema
profiled in two runs gave `2018/8/5` and `2018/8/1` for the same column.
**Arms of one experiment could differ for a reason unrelated to the
intervention.**

**networkx `steiner_tree` (mehlhorn) raises `KeyError`** when the graph contains
nodes disconnected from the terminals — routine in a real corpus. Restrict to
the terminals' connected component first; that check doubles as the
"required tables must be connected" refusal.

---

## 3. Curator

**A constant step budget silently discarded 30 of 57 schemas from a paid run.**
`recursion_limit = max(max_agent_steps * 4, 100)` pinned every budget at or
below the default to 100 super-steps = **33 sequential tool calls**, against a
prompt asking for 126–238. Signature of the bug: **cap rate flat across schema
size** — a 2-join schema capped as often as an 86-join one. The deepagents loop
costs **three** super-steps per sequential tool call (`model` →
`TodoListMiddleware.after_model` → `tools`), measured against deepagents 0.6.12
/ langgraph 1.2.8 — **re-measure this against whatever v2 pins.** N tool calls in
*one* assistant message still cost one super-step.

**The budget was never disclosed to the agent, and the triage order put the
irreplaceable work last.** Nothing in the prompt, the user turn, or the harness
mentioned a limit — while the deepagents base prompt pushes the other way
("Keep working until the task is fully complete"). And the ordering put the
agent-only work (reliability sweep, clarifications) *last*, so exhaustion took
exactly the assets no other mechanism produces; joins and metrics are seeded
deterministically and survive regardless. **Order triage by irreplaceability,
not importance.**

**Clarifications were scarce for priority reasons, not budget reasons.** 186
questions across 57 schemas, median 3, against ~104 columns per schema. Phase A
budgets ran 65–339 and **no schema capped**; budget-to-question correlation
**−0.353**; `works_cycles` spent **1,583 tool calls at a budget of 339 and asked
nothing**. Of the 186, **83 described a row-count or duplicate-shaped anomaly**
and **85 (45.7%) got answers disclaiming knowledge of the object asked about**,
because the responder is briefed from column documentation and cannot confirm a
statistic.

**A crashed agent reported "wrote nothing" for 13 of 55 schemas.**
`_count_tool_calls` reconstructs the tally from the returned message list, and
`_invoke_agent` nulled that list on any exception — producing a complete-looking
dict of zeros. But the write tools mutate the shared bag as they are called, so
`write_total: 0` described a **half-authored** corpus as an untouched one, and
the SME phase republished that zero as a reported metric. *"Zero is a
measurement. This is the absence of one."* **Stream, don't invoke** — `invoke`
returns state only on success.

**Those 13 partial corpora were then served, scored, and ranked by the pooled
router against the complete ones** — polluting the intact schemas too, since the
router ranks every schema against every question. The driver already knew and
spent the knowledge on a console warning.

**The curator saw only 47% of the train split.** `_render_train_batch` sliced
`items[:40]` and was called once; every one of the 57 schemas has more than 40
train pairs (49 min, 86 median, 306 max). **2,806 of 4,900 unique evidence hints
(57.3%) never reached the arm that produces the +11.5pp step** — and the rest
reached the SME brief, which caps nothing.

**That 40-slice was never a size bound either.** BIRD-Obfuscation rewrites some
gold as a literal `VALUES` list: 48 pairs exceed 2000 chars and **the largest
single pair renders 2.53 MB (~630k tokens)**. Uncapped, `language_corpus`'s
first 40 pairs rendered 323k chars — re-sent on every turn of the agent loop.

**`read_corpus` was unbounded**: median schema ~6 KB, widest **664 KB (~166k
tokens)**. Past ~80 KB (deepagents' `tool_token_limit_before_evict`, 20k tokens)
the middleware evicts the tool result to a file and returns a preview — **one
read silently becomes several turns out of the step budget.**

**Join identity without a normalised ON digest lost real edges before the agent
ran.** Keyed on `(schema, left, right)` alone, two different relationships
between the same table pair collapsed and the last write won. `soccer_2016` kept
**32 of 54** gold-derived edges, `mondial_geo` **67 of 87**, and **33 of 57
schemas lost at least one** — hitting `seeded`, `curated` and `curated_sme`
equally.

**`_mark_columns_absent_from_gold` is deleted and must not return.** It stamped
every column no train gold referenced as `suspect`. "BIRD never queried this
column" is not evidence a column is unreliable, and where the gold SQL was
defective the mask **banned columns the generator needed** (on one fixture it
suspected 53 of 61 columns). Consequence recorded honestly: between profiling
and the agent pass the corpus carries **zero** suspect columns, so the curated
arm's decoy defence is entirely whatever the agent authors.

**The governance boundary is enforced by the absence of a tool.** Reliability is
AI-authorable (`annotate_column(suspect=True)`); `governance.excluded` is
human-only, enforced by there being **no exclusion tool and no reference to
`excluded` anywhere under `curator/`**. *"Suspect argues against a column and
the analyst still sees it; excluded removes it from the corpus, which is a
decision a person signs for."* If v2 auto-generates tools from a schema, this
boundary is violated by construction.

**The agent could mint certified human facts.** It owns `clarifications.jsonl`
through ordinary file tools, so writing `{"status":"answered","answered_by":"Jane
Chen, Finance"}` came out of the fold as `source=human, status=certified`.
*"The prompt telling the agent to write `status: open` is not a control. This
is."* — the guard runs at the phase boundary, in code.

**A phase must never mutate its own input.** Phase B wrote answered records back
into `curated_root`'s ledger, so a second SME build found nothing open, folded
nothing, and produced a corpus identical to `curated` — caught only *after*
paying for the whole build.

**Batch tools must return partial success, never raise.** One bad spec in a
batched `annotate_columns` discarded every valid annotation in the call — pure
token churn against the budget.

---

## 4. Governance and security

**The attempt cap let unvalidated SQL reach the database (Audit Vuln 2).** On
cap, the middleware wrote a ledger entry *before* `check()` ran, so it carried
no layer; graded delivery saw `failed_layer=None`, treated it as non-hard, and
re-executed SQL that had cleared **no guardrail layer at all**. Confirmed chain:
three attempts blocked at L3, the fourth capped, card-number SQL would have
reached the gateway.

Two fail-open siblings in the same path: the pre-execute recheck was wrapped in
`if allowlist is not None` and fell through to `gateway.execute` when absent;
and omitting `allowed_tables` **skipped L4 entirely**. **An optional security
parameter whose absence means "skip the check" is a latent hole. Absence must
refuse.**

**Graded delivery is an allowlist, never a denylist.** Only SQL that failed a
*curated semantic* layer (L4 term-semantics, L5 cost) is re-executed and
delivered unverified — reaching one is a proof minted by `check()` itself that
L1/L2/L3 passed. `failed_layer=None` must never mean safe.

**Column-less function calls bypassed every allowlist layer.** `SELECT
pg_read_file('/etc/passwd')` references no table and no column, so L3/L4/L5 are
structurally blind, and a read-only connection does not stop read-side
functions. The whole Postgres XML-export family
(`query_to_xml`/`table_to_xml`/`schema_to_xml`/`database_to_xml` and their
`*_xmlschema` variants) takes its target as a **string literal**, so sqlglot
parses it as `exp.Anonymous`. Plus `setval`/`nextval` — SELECT-shaped write
primitives. **The code itself flags that a positive allowlist of permitted
functions is the right answer and was deferred; v2 should ship it.**

**Case folding: L3's leniency and the engine's strictness contradicted each
other.** Postgres folds unquoted identifiers, so `customerid` clears a
`CustomerID` allowlist — then quoting the model's spelling sends the engine a
column that does not exist. Canonicalise to the corpus's spelling before
quoting; **drop** ambiguous folds rather than guessing.

**Three-part `schema.table.column` references slipped past both L3 and L4** —
the key is in the corpus-wide allowlist and L4 inspects only FROM sources.
Sibling fail-closed shapes, each a real bypass: star projections, `NATURAL
JOIN`, bare columns in a mixed base+derived scope, and a bare name matching a
`suspect` column in **any** in-scope base (leftmost-table resolution could bind
it to the decoy).

**The agent grew its own authorisation set.** `inspect_schema` wrote straight
into `licensed`, which becomes L4's `allowed_tables` — so inspecting anything
authorised it, reaching into unrelated schemas in a pooled corpus. **A tool that
grants privilege must have a bound the model cannot widen.**

**The PII filter had never executed.** The inner `sample_rows` exclusion filter
was only ever tested behind `for_analyst()`, which strips those columns
upstream. **Test defence-in-depth layers with the outer layer removed, or the
inner layer is untested by construction.** The same shape produced a shipped
`NameError` in the note-withholding predicate: `any()` short-circuits on an
empty token set, so the bug only fired on corpora with an excluded column — and
the rails laundered it into an unremarkable `model_error` refusal.

**The routing index embedded governance-excluded PII columns verbatim** while
the picker summary filtered them — two definitions of "excluded" that drifted,
with the driver feeding the router a raw corpus because "callers are documented
as passing `for_analyst()`". **An unenforced caller contract is not a boundary;
the analyst-visible view should be a type.**

**Corpus prose is injected as authoritative instruction, and only notes were
sanitized.** A column *description* was the cheaper poisoning vector — the
corpus is writable via `POST /corpus/edit` and partly LLM-authored. Complementary
constraint: a metric `expression` and a join `on` are SQL the generator copies
character for character and **must be exempt**. Conversation history is also
deliberately unsanitized — those are the user's own words.

**`asset.schema` escaped the corpus root.** The write directory is derived from
it while `is_valid_id` guards only the asset id. Path components must be
validated **where they are used**, with `\A...\Z` (Python's `$` also matches
before a trailing newline, so `"beer_factory\n"` passes a `^...$` validator that
names a directory).

**A guessable `thread_id` was a handle on another caller's paused
clarification** — the clarify checkpointer is process-global, and a colliding
id landed on a victim's pause, which embeds their question. Namespacing and
hashing is a mitigation, **not** authentication.

**The gateway claimed RLS-as-user on four surfaces and had none.** `identity`
reads as provenance, not enforcement. **If v2 keeps an unimplemented seam, name
it as unimplemented at the seam itself.**

---

## 5. Concurrency, durability, cost control

**The serve phase — where all the money goes — had no inline gate.** A run
reached **48% crashed by row 655 of 1351** and ran to completion: ~2 hours,
~$30. The only signal was `crash_rate > 0 → not quotable`, computed after the
last question. BUILD had coverage assertions; SERVE had nothing.

**And aborting did not stop the spending.** `ThreadPoolExecutor.map` submits
every item up front and `__exit__` is `shutdown(wait=True)` with no
`cancel_futures` — aborting at row 12 of a 1351-question arm still paid for
**1,339 model calls**. **A circuit breaker is worthless if stopping costs the
same as finishing.**

**Head-of-line blocking made a healthy run look dead, twice, to an operator.**
`map` yields in submission order, so a slow head task blocks result *delivery*
while workers keep finishing — measured: 4 workers, 40 tasks, one 5s head task,
**39 finished and zero rows written at t=4s**. Both the JSONL and the progress
line tick from that same blocked callback. Split `done` (returned) from
`written` (delivered).

**The run ledger lost 16 of 17 records under 12 concurrent writers, silently.**
Upsert-by-run_dir is read-modify-rewrite. Two Windows follow-ons: a lock file
another writer is unlinking raises `PermissionError`, not `FileExistsError`; and
`os.replace` over a file a **reader** holds open raises `PermissionError:
[WinError 5]` — an editor, a virus scanner, or the reader the runbook itself
tells operators to run (**8 of 320 appends survived** at 8 writers with one
reader). Three copies of temp-then-replace existed and **none** was durable.

**Concurrent per-db builds shared one arm root**, and the curator writes five
sidecars there — so two concurrent builds leave one `clarifications.jsonl`
holding the second db's content, which the SME arm then folds into the *first*
db's corpus.

**An open SQLite handle made every curated build die on Windows**, ending a paid
run with "every db failed to build" and no `summary.json` — for a checkpointer
file **nothing ever read**. deepagents needs one only for `interrupt_on`, which
the curator never sets.

**A `:.3f` on a `None` rate raised after the whole serve loop and before
`summary.json` was written.** Hours of live model calls discarded to print a
progress line. **Nothing between the last model call and the artifact write may
raise.**

**A stale price entry overstated a measured run nine-fold** (`gpt-5.6-luna` at
`(2.0, 8.0)`, matching neither the new price nor the old). Both 2026-07
Anthropic ladders produced **no USD at all** because no Claude models were in
the table. Unknown model → `None`, never 0.

**Prompt caching:** measured hit rates on OpenAI-compatible providers are
**55–58% of input tokens**, and flat pricing overstated a real ladder by ~24%.
On the Anthropic path, `cache_read` is **0 across 49.4M input tokens** — no
`cache_control` breakpoint is set anywhere in `src/`. OpenAI caches
automatically; Anthropic requires the explicit marker. Cache *writes* bill at
1.25x and are not modelled.

**Full ladders cannot run locally.** The curator averages **~293k tokens per
turn** (58.1M input over 198 turns) against a ~500k TPM local quota, so the build
rate-limits even at `build_workers=1`. Observed: **schema 1 of 57 took 23
minutes with 110 SDK retries and zero errors** — healthy, just throttled.
Extrapolated ~30 hours for one ladder.

**Resume is a selection effect.** Re-serving crashed turns resamples those draws
*after* failure, laundering `crash_rate` back to zero and conditioning the arm's
EX on a re-roll. Record `n_re_served` and refuse to quote.

**Resume drift keys must be a superset of comparability keys.** Two runs at
different commits are the *normal* comparison; the same difference **inside one
directory** is corrupting. `RESUME_DRIFT_KEYS` checked `git_sha` only, so a
resume across an **uncommitted** edit blended two harness versions into one arm's
score — 1025 rows under one `diff_sha256` and 326 under another, averaged.

**Scope is not derivable from a directory.** `--arms`, `--dbs`, `--oracle`,
`--replicate` were re-read from argv every invocation, so the runbook's own
resume line dropped `--oracle` and picked up four default arms — two curator
passes and three serve passes, on a paid run.

**A shared corpus root is a cross-run contamination channel.** A db dropped from
one attempt leaves its YAML behind and competes as a router candidate for
**every other db's questions**, silently changing the routing problem's
difficulty between two runs of the same db set.

---

## 6. Observability

**Tool-call detail was computed on every turn and thrown away.** `_resolve_tool`
computed the search query and its hits, the inspected table, whether it
licensed, the sampled table and why it was refused — then handed it to a stream
whose first line is `if self._on_event is None: return`. **No eval arm passes an
`on_event`**, so zero `search_corpus` / `inspect_schema` / `sample_rows` rows
exist in any `stage_events.jsonl` on disk.

**An allow-list relay between runtime and measurement silently swallows new
instrumentation.** `schema_route_channel` and `schema_route_degraded` existed for
a year and reached no artifact, because `eval.arms`'s provenance relay never
named them.

**Nothing in `src/` ever called `basicConfig`**, so every `logger.*` call went
into the void and diagnostics had to be `print`.

**`httpx` at INFO produced an 8.8 MB run log** — but `openai._base_client` is
deliberately *not* quieted: its `Retrying request in 6.6 seconds` line is the
**only** early warning this stack gives before a rate limit turns into crashed
rows. There is no rate limiter and no 429 handling anywhere in the repo.

**A four-arm ladder's traces could not be told apart** — every trace carried
exactly one tag, `governed-bi`. `corpus_pin` is present and *reads* like a
corpus identity but carries the literal `"datalake"` on every pooled run, while
`corpus_content_hash` reached no trace. **Assert tracing metadata at the call
site that threads it, not at the builder.**

**Exports run on a background thread behind an `atexit` hook** that SIGTERM,
`os._exit` and CI cancellation all bypass. Short-lived processes need an
explicit drain.

**Redaction diverged between two sinks for the same record.** The anonymous
audit surface was leaking guardrail `reason` strings — for `verdict="error"`
that is raw `str(err)`, and libpq embeds the offending statement (`LINE 1:
SELECT ...`). The portable log had already dropped it; the API surface, reachable
anonymously, was using the weaker policy. The rule that works is **deny by
shape, not by key name**: drop every non-numeric detail value, because a
per-key whitelist cannot tell a closed vocabulary from a question echo.

**JSONL during the run, SQLite for export.** Up to 20 concurrent workers append;
append is contention-free, twenty writers on one SQLite file is `database is
locked` two hours in. And the loader's contract is **load-and-annotate, never
reject**: two run directories on disk have `stage_events.jsonl` and no
generations at all (a worktree cleanup deleted them, and the stage events were
the only surviving crash diagnosis). **The runs worth reading are exactly the
ones that broke.**

---

## 7. Test validity — the meta-lesson

Several guards shipped broken behind a green suite:

- The `sample_rows` license test asserted the substring `"sample"` was absent
  from generated SQL — **vacuous**, it never appears. Deleting the gate left it
  green.
- The fake model discarded `messages`, so the system prompt and the tool set
  were **never observed** — both could be emptied.
- `make_graph` is the entry point `langgraph.json` deploys and **no test
  referenced it**; renaming it left a green suite and a broken deploy.
- The gold-gate tests re-implemented `share > THRESHOLD` instead of calling the
  gate — so deleting the gate, flipping the comparison, or reversing the
  denominator all passed.
- The refuse gate's only test used the verbatim branch, so the **pattern** branch
  never executed and a recall regression shipped green.
- A test globbing gitignored `runs/datalake/*` always skipped in CI.
- Three sibling modules each stubbed the *other* side of the seam they were
  protecting, so a rename of `stage_events` left all three green.

**The five authoring rules the suite eventually wrote down for itself:**

1. **Strict xfail.** Non-strict means a fix XPASSes in silence and nobody learns
   the thing started working.
2. **Paired no-op controls.** Every cost test ships a cache-disabled control so
   it cannot pass for an unrelated reason; assert ratios, not timings.
3. **Scoped wiring assertions.** `index_cache=rt.index_cache` appears at three
   sites; an unscoped substring check stayed green when the one that mattered
   was deleted.
4. **Always-written gate state.** *"A gate that leaves a trace only when it fires
   cannot afterwards be told from a gate that was never wired up — half this
   repo's defects have that shape."*
5. **Never assert a module against its own constant** — that passes for an empty
   tuple.

Plus: **the hermetic suite could reach a live model with the developer's real
API key**, because `import governed_bi` auto-loads a repo-root `.env`. Strip
credentials, disable local config overlays, and reset every process-wide
singleton per test, from day one.

And: **assert instrumentation at the single producer**, not at a re-export —
patching a module's own re-exported name made a test pass while the real path
bypassed it.

---

## Appendix A: falsified numbers — do not quote these again

| retired claim | reality | where it spread |
|---|---|---|
| routing recall@3: embedding 0.70 vs BM25 0.35, RRF 0.535 | **0.852 vs 0.844**; BM25 *wins* at @1 (0.736 vs 0.694) | 6 places in `src/`, incl. an operator warning — and a test **asserted** it |
| `gpt-5.6-luna` price `(2.0, 8.0)` | `(0.20, 1.20, 0.02)` — overstated a run **9x** | price table |
| "69 unwinnable questions" | **4** — a 17x overstatement | planning docs |
| "+46 points of headroom" | revised to **3–5**, nothing in between | error-class summing |
| `routing_recall` (as published) | it was `schema_pick_accuracy`; the retrieval channel was unmeasured | ledger headline |
| `schema_pick_accuracy` 69.9% | **91.0%** with quota free — the 69.9% was measured through a rate-limited embedder | ledger |

The characteristic defect: **a number describing the world, written as a literal,
pinned to nothing** — and the fix lands where it was found and never reaches the
adjacent copies. v1 ended up with a repo-wide grep test forbidding retired
literals in `src/`. **v2 should require every world-describing number in code or
docs to carry its artifact path and date.**

## Appendix B: environment facts that will bite again

- **YAML 1.1 parses `on:` as boolean `True`** — and `JoinAsset` has a field
  named `on`. (Also: `yaml.CSafeLoader` is ~7x faster and YAML parsing was
  measured at **~23% of an offline run's wall clock**.)
- **`UnicodeDecodeError` is a `ValueError`, not an `OSError`** — a `.env` saved
  as cp1252 crashed `import governed_bi`; 11 non-UTF-8 BIRD CSVs killed schemas
  *after* three arms had been paid for.
- **83 of 597 BIRD description CSVs start with a BOM**, which lands inside the
  first header name and silently empties whole tables. Read with `utf-8-sig`.
- **3 of 569 BIRD CSVs are misfiled**; BIRD splits schemas across
  `train_databases/` and `dev_databases/`, so hardcoding one tree built 11
  schemas' briefs silently empty.
- **`Path.resolve()` / `Path.cwd()` trip LangGraph's ASGI blocking-call
  detector.** So does building the stack on the event loop.
- **A module LangGraph loads by file path must not use `from __future__ import
  annotations`** (it inspects raw parameter annotations to decide config
  injection) and cannot use relative imports.
- **On Windows, `os.replace` over a file any process holds open for *reading*
  raises `PermissionError`.**
- **`argparse` interpolates help text through `%`** — `"48% crashed"` made
  `--help` itself raise. It recurred three times.
- **BIRD obfuscation is translation, not randomisation**: German
  (`strassenadresse`), French (`nom_famille`), Spanish (`nombre_autor`), plus
  paired decoy columns (`zip_code`/`postal_code`, `alias`/`alt_alias`) and some
  identity renames. Physical names carry real semantics in the wrong language —
  which is why BM25 beats embedding at recall@1.
