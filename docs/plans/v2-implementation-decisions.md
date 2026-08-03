# v2 implementation decisions

Judgements made while implementing that the ADRs did not settle, plus the places
where the implementation **deviates** from what an ADR says. Written as I go so
they are reviewable rather than buried in a diff.

Status legend:

- **ADR needs updating** — the implementation is right and the ADR text is now stale.
- **Filled a gap** — the ADR said TBD or was silent; this is a starting value that must be calibrated.
- **Recorded** — a judgement with no ADR consequence.

---

## 1. No `ChatModel` port · *Recorded*

ADR 0005 §4.4 and the module design both listed a `ChatModel` port with two
adapters. I did not define one.

LangChain's `BaseChatModel` already *is* that port. v1 had
`llm/client.py` + `llm/langchain_client.py` + `llm/fake.py` — three layers over
someone else's abstraction, and ADR 0005's own instruction is "don't re-wrap
LangChain".

The one real requirement behind the proposed port — that a test double **record**
the messages and tool set it was handed, because v1's fake discarded `messages`
and both the system prompt and the tool set could have been emptied with a green
suite — is a requirement on the double, not a reason for a Protocol. The scripted
model will subclass `BaseChatModel` and expose `prompts_seen` / `tools_seen`.

## 2. `Embedder` *is* a port · *Recorded*

Asymmetric with the above, deliberately. LangChain's `Embeddings` lacks the two
attributes every cache key in this system needs: `model` and `dimensions`.

That is not stylistic. v1's vector cache omitted them, and because `cosine`
returns `0.0` on a width mismatch instead of raising, a cross-model cache hit
degraded routing to "nothing scores" with no error anywhere. The port exists to
make those two facts part of the interface rather than a convention each caller
remembers.

## 3. `ports.py` at the top level, not `register/ports.py` · *Recorded*

Both are stdlib-only, so either location satisfies the layering. Separated
because they answer different questions: `register/` holds **declared tables**,
`ports.py` holds **Protocols with zero implementations**. Folding Protocols into
a package named "register" would make the package name a lie the first time
someone looks for a capability there.

## 4. `Connector.execute` returns `(columns, rows, truncated)` · *Recorded*

Rather than rows alone, or raising on truncation.

`truncated` is derived from the base class's `max_rows + 1` limit, so "we hit the
cap" is a fact the caller **receives**. The alternative — infer it from a row
count — requires every caller to know the cap in order to interpret the count,
and v1's row-cap handling was one of the four grader-ceiling misses.

## 5. `CorpusStore.load` returns `(assets, problems)` · *Recorded*

Per-item error isolation, never a raise for a bad item.

v1's loader raised on the first unparseable file, and because the pooled driver
loads every schema × every arm through one call inside a `try/finally` with no
`except`, **one truncated YAML file discarded a fully paid 69-schema build with
no clue why.** The opposite failure matters too: a *silent* skip turns "a corpus
that lost half its assets" into "a corpus that merely looks small", and this
project has already published a result on top of that. So problems are returned
and the caller is expected to report them loudly.

## 6. `ColumnAsset.identifier_fields` is the bare `physical_name` · **ADR needs updating**

ADR 0005 §1.1's table says `ColumnAsset`'s identifier is
`{table.physical_name}.{physical_name}` — qualified. The implementation requires
only the bare `physical_name` to appear in `summary`.

Three reasons:

1. **Qualification spends the 250-character budget on text the reader does not
   need.** A wide table's name plus a dot is pure overhead in every one of its
   columns' summaries.
2. **A column's searchability comes from its own index entry**, and that entry's
   text contains the column name either way.
3. **The table association is established by the schema tag rule** (`parent_table`),
   not by prose. Requiring it in the text would be asserting in a string
   something the index already knows structurally.

The seed template still writes the qualified form (`table.column (text)`),
because at seed time there is no better information — and the curator rewriting it
to something shorter must not fail validation for dropping a qualifier.

**Action:** ADR 0005 §1.1's identifier table should say bare `physical_name`.

## 7. Per-type budgets live in `register/assets.py`, not `register/knobs.py` · **ADR needs updating**

ADR 0005 §5 lists the budgets in the knob table. They are implemented as a column
of the asset policy row instead, and the knob register references them through a
single content-hashed knob `asset_budgets`.

Change locality: a budget changes when the asset type changes, so they belong in
the same row. Keeping the budget in the knob table and the type in the asset
table is the shape that produced v1's `budgets.get(cls, 0)` — two tables that
had to agree and did not, which is why `NegativeExampleAsset` was structurally
unreachable.

The knob reference preserves what §5 actually needs: a budget change moves the
serve config hash.

**Action:** ADR 0005 §5 should point at `register.assets` for the budgets rather
than restating them.

## 8. `verbatim_fields`, not `sanitized_fields` · *Recorded*

ADR 0005 §1.6 describes the policy as a table of field classes. Implemented as
**default-deny**: every string field is sanitized, and the exemptions are listed
per type.

v1 sanitized note text only, so a column *description* was the cheaper poisoning
vector. Listing what is *exempt* means a new prose field is protected the moment
it exists; listing what is *sanitized* means a new field is unprotected until
someone remembers.

## 9. `Channel.extraction` is a `Channel` member · *Recorded*

Extraction is not a scoring channel, so putting it in the same enum as `lexical`
and `semantic` is a small category stretch.

Kept because its failure mode is identical in shape — it either ran, was never
configured for this facet, or should have run and did not — and a separate
two-valued field for it would be the R1 defect in a different variable. One
three-valued vocabulary covering all three is worth the stretch.

## 10. ~~`FACET_TARGETS` names asset types as strings~~ · **Corrected — the stated reason was false**

I originally wrote that importing `AssetType` into `register/facets.py` would be
cyclic, and used strings instead. **That was wrong.** A consistency review checked
the import graph: `assets.py` imports only stdlib, `register/__init__.py` is empty,
so `facets → assets → stdlib` has no cycle.

So the table gave up type checking, and deferred its closure to a suite that does
not exist, for a constraint that does not apply. Now keyed on `AssetType`, and the
closure runs at import instead — which immediately paid for itself by making
decision §19 below possible.

Recording the correction rather than editing it away: I asserted a technical
constraint without checking it, and the cost was a weaker table plus a deferred
test. Worth noticing as a pattern.

## 11. Three knobs ship `UNSET`, including `cost_budget` · *Filled a gap*

ADR 0005 §5 marks `lexical_saturation_k` and `negative_tau` as unset and
`cost_budget` as "TBD". All three are implemented as `UNSET`, a sentinel whose
`__bool__` **raises**.

A `TBD` that resolves to a number is a fabricated measurement, and a gate reading
it is worse than no gate. `UNSET` forces the caller to handle the case, which for
`negative_tau` means the gate ships disabled — as ADR 0006 §2 requires.

## 12. `context_budget_tokens = 24_000` · *Filled a gap*

ADR 0005 §3.6 requires a total context budget and does not give a number.

Derived from v1's measurements: median context was 17,782 chars ≈ 4,450 tokens,
and median per-turn input was 30,923 tokens once accumulated tool returns are
included. 24,000 leaves room above the observed context while putting a ceiling
where none existed.

**This needs calibration**, and it is the number ADR 0005 §3.4's "input cost falls
by ≥30%" gate is a percentage *of* — so it must be set before that gate can be
evaluated at all.

## 13. `max_rows = 200_000` · *Filled a gap*

Inferred from v1 behaviour rather than stated in an ADR: three of the four
grader-ceiling misses were `retails` questions exceeding a 200k-row harness cap,
which pins the value v1 actually used.

## 14. `g_length_max_chars = 8_000` · *Filled a gap*

ADR 0006 §13 says "TBD from the question distribution". 8,000 chars is roughly
two orders of magnitude above a normal BI question and well below anything that
would exhaust a context window, so it blocks a paste-bomb without touching real
traffic. **Needs measuring against the actual distribution.**

## 15. `Outcome` has no `graded` member · *Recorded*

A graded-delivery turn **answered** — with low semantic assurance, which is a
reliability-stamp axis, not a turn-ending one. ADR 0006 §12's
`ExecutionRecord.terminal` tracks the execution-layer terminal state and answers a
different question.

Adding `graded` to `Outcome` would put two axes in one enum, which is the exact
thing `register/stages.py` exists to stop.

## 16. `TERMINAL_STAGES` excludes `stamp` · *Recorded*

`stamp` is what every terminal path passes *through*, not a terminal itself. That
distinction is load-bearing: ADR 0005 §3.1 routes refusals, declines and node
exceptions all through `stamp` precisely so that `Answer` production has one site.

---

# Second pass — after the consistency review

An agent with no context on the design discussion reviewed the register layer
against both ADRs and the lessons doc, and found 16 real discrepancies. Three were
severe enough to change the code's shape; one it found only because it had read the
LangGraph skill. The fixes are below, and the ADR text it caught as stale is now
corrected.

## 17. `missing_required` treats null as absent · *Recorded*

The presence test could not fail. `project()` writes every declared key, so a
record of twenty nulls has every key and passed — **the same defect as v1's
`corpus_content_hash == "unknown"` comparing equal to itself**, in the module whose
docstring cites that incident.

Now a `never` field that is null counts as missing. The other two absence classes
are not checked, because null is a legal value there and the register is what tells
a reader which is which. That asymmetry is the register earning its place.

## 18. Eight fields moved from `never` to `not_applicable` · **ADR needs updating**

`facet_hits`, `facet_channels`, `pulled_in`, `crossings`, `tool_delivered`,
`negative`, `licensed`, `schemas` are owned by stages a refusal path never reaches.
Declaring them `never` meant either the presence test fails on every guard-blocked
turn, or the producer writes an empty collection — **and then the degradation gate
reads an empty `facet_channels` as *clean* on a turn where the facets never ran.**
Absence reading as agreement, in the field added to stop it.

The gate condition changed with it: *"on turns where the fan-out ran, no channel
state differs from its declared expectation; the observed count is published beside
the rate."*

**Action:** ADR 0005 §4.1's field list should mark which fields are
stage-conditional.

## 19. `Anomaly` separates degradation from configuration drift · *Recorded*

ADR 0005 §2.3 says "only `failed` is degradation". Right about `failed`, silent
about the other two, and my first implementation flagged all three — which would
have refused a run for having *more* retrieval than declared.

Three distinct facts now: `failed` and `unconfigured` are degradation (the arm runs
on fewer channels than it claims); `extra_channel` is drift, reported and not
gating. `unconfigured` matters most — it is the case where a channel silently stops
being wired up, which a gate looking only for `failed` would pass.

## 20. `GATE_CONSUMED_TYPES` closes the indexed-type loop · *Recorded*

`negative_example` is in the index and targeted by no facet — **exactly the type v1
made structurally unreachable via `budgets.get(cls, 0)`**. Now declared as
gate-consumed, with an import-time assertion that every indexed type is either
retrieved by a facet or consumed by a gate, and that no type is both.

## 21. Two assertions were tautologies · *Recorded*

`resume_drift_keys() ⊇ comparability_keys()` is definitionally true — the former is
built as the union of all three roles — so the assertion could never fire. That is
"never assert a module against its own constant", in the module citing the drift-key
incident.

Replaced with `_PLACEMENT_INVARIANTS`: nine named knobs and the role each must
have. Each line goes red if someone changes that knob's `Role`. It also asserts
that every name in it is a real knob, because a typo would otherwise make the
invariant skip silently.

## 22. Three security knobs moved from `None` to `UNSET` · *Recorded*

`permitted_functions`, `sqlglot_version`, `guard_rules_enabled`. ADR 0006 G1 is
"absence refuses", and `None` is falsy: `if not permitted_functions` reads the
allowlist that defines what may execute as *empty*, and `None` contributes `None`
to the config hash. `UNSET` raises on truth-testing, so config resolution must
supply them.

## 23. `sample_values` is verbatim, not sanitized · *Recorded*

Under default-deny it would have been sanitized — silently altering a code table,
and moving `context_hash` for a reason unrelated to the corpus. Exempted, with the
cost stated in the code: these values reach the model unfiltered, which is ADR
0006 §6's recorded data-boundary gap. Sanitizing here would corrupt the data
without closing the gap.

## 24. `run_query` has no `Stage` member · **ADR needs updating**

ADR 0005 §3.5 says "every tool call emits a stage record". A passing `run_query`
already emits `check` + `execute`; a third record double-counts an action the
ledger and every rate already agree on — which is the reasoning v1 recorded after
adding and then removing that third record.

**Action:** done — §3.5 now reads "every tool call *that has no other trace*".

## 25. Added `Redaction` as a register column · *Recorded*

ADR 0006 §11 mandates one redactor for both sinks and describes the policy as a
table keyed by field class, but nothing declared the class. Adding it after the
sink exists would have meant two tables that must agree — v1 had exactly that and
the anonymously-reachable sink used the weaker policy.

## 26. Added `knobs_resolved`; the gold schema's rank is *not* a serve field · *Recorded*

ADR 0005 §4.1 lists both. The resolved knob set is now a record field. The gold
rank is not: deriving it needs gold, which only eval holds, so it is computed from
`schema_ranking` on the eval side. Recording it in serve would mean serve knowing
the answer key.

## 27. `record.py` is 497 lines, over the 400 soft cap · *Recorded*

Under the 800 hard cap. The natural split is declaration from derivation, and the
split line is where drift happens (`Absence` semantics away from
`missing_required`), so it stays whole until something forces the issue. Flagged so
that when it splits it is a decision with this note attached.

---

## 28. The enforcement the docstrings claimed now exists · *Recorded*

Five docstrings named `tools/check_imports.py`, `tools/check_citations.py` and
`tests/conformance` before any of them existed. All three now do, and **both gates
were verified to fire on an injected violation** — a gate that only leaves a trace
when it fires cannot afterwards be told from a gate that was never wired up, which
is L§7's rule and half of v1's second-largest defect class.

`tests/conformance/test_register_closure.py` is 22 passing tests plus one
`xfail(strict=True)`: the assertion that a **real turn on every terminal path**
writes every required field. That needs the graph. Strict xfail so it fails the
suite the moment it starts passing — a non-strict xfail would XPASS in silence and
nobody would learn the thing started working.

Two things the tools taught while being written:

**`check_citations.py` initially found nothing**, because the declarations carry
type annotations and an annotated assignment is `ast.AnnAssign`, not `ast.Assign`.
It reported zero patterns — and then *refused to treat zero as success*. That
refusal is the only reason this was caught instead of shipping as a permanently
green gate. Written the other way (`if not patterns: return 0`) it would have been
v1's exact defect in the tool built to prevent it.

**The inline exemption marker must sit on the line the pattern matches**, not the
line after it. Three of my first four markers were on the following line because the
prose wrapped. The gate now says so in its failure message.

## 29. The citations gate is two-tier, and `docs/` is advisory for now · **Open item**

`src/` and `tools/` are fatal. `docs/` reports 16 hits across 12 files and does not
fail, with `--strict-docs` to promote it once sorted.

The hits are not one problem:

* An **experiment record** saying "we measured 0.35" is a true statement about what
  was measured on that date. The record should stay and be annotated, not edited.
  (`docs/experiments/e1-shortlist-ablation.md`, `20260801-three-model-ladder.md`)
* A **plan** reasoning *from* 0.35 toward a design decision is stale in a way
  annotation cannot fix — and most of those plans describe code that no longer
  exists. (`docs/plans/routing-redesign.md`, `serve-transparency*.md`,
  `grill-agenda.md`, `adversarial-review-2026-07-31.md`, `docs/measurement.md`,
  `docs/oracle-ladder.md`, `docs/ui-frontend-handoff.md`)
* **ADR 0003** is superseded in full by 0005 and quotes the figure as its stated
  reason for a decision. An ADR is a historical record and should not be edited —
  but it should carry a superseded banner pointing at the correction.

Sorting these is a documentation pass, not a lint fix. Papering over it with 16
inline markers would make the gate useless in exactly the files that discuss
measurement most. **Reported on every run so the number cannot quietly grow.**

**Action:** decide per file — annotate, delete, or mark superseded. 12 files.

---

## Open items for review

| # | item | why it needs you |
|---|---|---|
| 6 | column identifier is bare, not qualified | changes what a validator rejects |
| 7 | budgets live with asset types | changes where §5 points |
| 12 | `context_budget_tokens = 24_000` | it is the denominator of the cost gate |
| 14 | `g_length_max_chars = 8_000` | a false-positive rate nobody has measured |
| 18 | eight fields are stage-conditional | changes what a refusal path must write |

Everything else is recorded for the record and needs no decision unless you
disagree.
