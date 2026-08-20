# Declared machinery with no consumer

**Tree swept: `75554be` (2026-08-10)**, the commit `tools/check_declared_is_consumed.py` was
written into. The counts and the closure notes below were re-derived on 2026-08-12 by running the
checker against the tree of that day. **Line numbers are anchored to the sweep and have drifted** —
resolve a citation by the symbol beside it, not by the coordinate.

A sweep for the defect class in `open-work.md` §3.10: something is declared — a knob, a record
field, a state channel, an env var, a docstring promise — and nothing on the other end reads it.

Two instruments. The mechanical one is the six artifacts this sweep ran over, 1,351 rows each, all
on corpus `86ed1dbf…` (`../BIRD-corpus` @ `30872d3`): a field constant across all 8,106 rows of
all six arms is evidence of no writer, which is how `facet_degraded` and `git_sha` were originally
caught. The static one is `tools/check_declared_is_consumed.py`, written in the same pass. It
found 27 of the items below when it was written, 14 on 2026-08-10, and finds **5** now
(`expand_hops`, `negative_tau`, `facet_model`, `rewrite_model`, `build_workers`).
`clarifications` closed 2026-08-19. The rule for
that last number is the checker's own output — `uv run --frozen python
tools/check_declared_is_consumed.py` prints one line per violation and the four-rule breakdown is
[at the bottom of this page](#the-checker) — so it is re-derivable in one command rather than by
counting sections here.

Findings are ranked by consequence. Tier 1 means a **recorded number is wrong**, not missing.

---

## Tier 1 — a measurement is silently corrupted — **all five closed, 2026-08-12**

**Read this before the five sections below, which are kept as the diagnosis rather than the
state.** Every tier-1 item now has a writer that fires, and each is asserted **on its value** in
`tests/serve/test_the_record_follows_the_knob.py` — never on presence, because `None` is present
and eight tests in this repository were once found asserting exactly that:

| finding | closed by | asserted against |
|---|---|---|
| 1 `llm_reasoning_effort` | `model/provider.py::reasoning_effort_of` — reads all three spellings (OpenAI attribute, proxy `extra_body`, Bedrock `output_config` / Nova `maxReasoningEffort`) | an effort the test itself requested, through the real `build_chat_model` |
| 2 `llm_utility_provider` | `session.from_assets` calls `_provider_of(resolved_utility)` | a base URL the test chose |
| 2 `embedding_provider` | `model/embedder.py::embedding_knobs`, from the port's required provider-qualified `model`; an unqualified id **raises** rather than guessing | the prefix the test's embedder declares |
| 3 `chat_model` / `llm_model` | `chat_model` written by name; the undeclared `llm_model` key deleted | the model id the session was built with |
| 4 three environment variables | `register/knobs.py::env_override`, applied last in `session._resolved_knobs`, with the readers' own parsing rules (blank is unset; the declared default decides the cast) | numbers the test exported |
| 5 `sqlglot_version` (and `negative_tau`, `cost_budget`) | `_resolved_knobs` no longer drops `UNSET` — it writes `None` — and calls `govern/functions.py::sqlglot_version()` | an independent `importlib.metadata` lookup |

**What this does *not* mean.** No artifact in `runs/eval/` gains anything: all six arms were
written before these wires and still say what §1–§5 record. The fix is for the next run, and
until one exists the sections below are the correct description of every number on disk.

**What it unblocks, and what it does not.** The condition
`tests/conformance/test_the_lint_gates_fire_on_a_synthetic_violation.py` names for putting the gate
in CI was "tier 1 clear",
and it is. The gate still exits 1 on the five findings in tiers 2–3, so a CI step would fail every
commit — and waiving five genuine findings to go green is the lie the gate exists to catch. The
half that was actually missing is now there:
`test_the_declared_but_unconsumed_set_does_not_grow` runs the gate on every commit against a
pinned set of five names, so a **sixth** fails the build and closing one fails it too. Two of
the five need a decision rather than a wire (`expand_hops` and `negative_tau` in `retrieve/`),
which is why the ratchet is the honest step and a CI step is not yet.

---

### 1. `llm_reasoning_effort` is null on every arm, and every arm ran at `high`

`runs/eval/driver_v4.log:6` records `model=Claude-Opus-4.8 effort=high`. All 8,106 rows carry
`knobs_resolved.llm_reasoning_effort: null`.

The writer exists and cannot fire. `serve/session.py:367` resolves the knob with
`getattr(agent_model, "reasoning_effort", None)`, but the proxy arm's model comes from
`model/proxy_gateway.py::build_chat_model`, which folds effort into the request body
(`extra_body → additionalModelRequestFields → thinking → output_config → effort`) and returns a
plain `ChatOpenAI`. That object has no `reasoning_effort` attribute, so the `if effort:` branch
never runs.

Consequence is named by the knob's own note: *"two v1 ladders differed ONLY in this and compared
as one experiment; it moved the baseline arm past that ladder's detection threshold."* A
high-vs-low effort A/B on the internal proxy today produces two artifacts with identical
`knobs_resolved` — the comparability set does not move, so the two treatments compare as one.

The checker does not catch this: the knob has a writer site that names it. Only the artifacts do.

### 2. `llm_utility_provider` and `embedding_provider` record the wrong gateway

Both are `Role.comparability`, both are constant `"openai"` on all six arms — and all six ran the
utility model and the embedder through the internal proxy. The same rows carry
`llm_provider: "custom:007df842"` and `embedding_model: "proxy:text-embedding-3-large"`, so each
row contradicts itself.

Neither knob has a writer anywhere. `model/embedder.py::embedding_knobs` returns
`embedding_model` and `embedding_dimensions` only, even though `embedding_provider`'s own note
says *"This knob is the reporting half."* This is worse than an absence: a null reads as
unmeasured, `"openai"` reads as a measurement.

Caught by rule K1.

### 3. `chat_model` is null on four of six arms; the value lives in an undeclared key

`knobs_resolved.chat_model` is `null` on run1, run2, v3-pinned and v3-fold, and
`"Claude-Opus-4.8"` on v4 and v5 — the resolver at `serve/session.py:378` landed between the two
groups. On those four arms the agent's identity survives only in the artifact filename, which is
a naming convention and not a record.

`serve/session.py:363` also writes `knobs["llm_model"]`, which `KNOB_REGISTER` does not declare.
An undeclared key is outside `comparability_keys()` and therefore outside the comparability set,
so the one field that did carry the model on those four arms could not be used to tell them
apart.

Caught by rule K2 (the only K2 hit).

### 4. Three environment variables move behaviour and not the record

| variable | read at | knob it overrides |
|---|---|---|
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | `serve/nodes/agent_core.py:272` | `agent_node_timeout_s` |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | `serve/graph.py:133` | `rail_node_timeout_s` |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | `serve/nodes/agent_core.py:308` | `agent_recursion_limit` |

All three are read env-first. `knobs_resolved` is built by `session._resolved_knobs` from
`knob_defaults()` plus the policy, the embedder and the model client — never from the
environment. Set any of them and the record still publishes 1200.0 / 120.0 / 40. All three are
`Role.comparability`. This is §3.8, re-confirmed against the current tree.

The checker cannot see it: each knob has a reader site that names it, which is the whole point —
the missing wire is on the *recording* side.

### 5. `sqlglot_version`, `negative_tau` and `cost_budget` are absent from `knobs_resolved` entirely

Not null — absent. `session._resolved_knobs` drops every `UNSET` knob, then re-adds exactly three
from the policy (`guard_rules_enabled`, `permitted_functions`, `cost_budget`); the rest never
return.

`sqlglot_version` is the sharp one. Its note says *"Resolved from installed metadata at config
time; UNSET so it cannot be silently absent"* — and it is silently absent on every row.
`govern/functions.py:36` implements exactly the resolver the note describes and nothing calls it
for this purpose. Canonical function names are release-dependent and the ADR 0006 allowlist is
keyed on them, so no artifact can say which sqlglot's vocabulary the governance layer was
enforcing.

`measure/gates.py::_knobs_resolved_gate` compares `resume_drift_keys()` across rows using
`row.get(key)`, so a key absent from every row compares equal to itself and the gate passes.

---

## Tier 2 — a measurement that cannot be made

### 6. `expand_hops` has no reader, and neither end of its evaluation exists

`expand_hops` is `Role.comparability` and is named nowhere outside `register/`. Setting it to 1
changes no behaviour and does change the config hash — the exact inversion of what a
comparability knob is for. The knob's note asks for a measurement ("of the tables gold SQL uses,
how many entered neither by facet hit nor by Steiner path?"); the field that would answer it,
`pulled_in`, is declared *"Answers what expand_hops is worth"* and never reaches an artifact row.
Both halves are missing.

### 7. `prompt_set` is null on the four arms whose entire treatment is a prompt variant

v2, v3, v4 and v5 differ by prompt wording and nothing else. `knobs_resolved.prompt_set` is
`null` on all of them. `register/prompts.py::select()` computes precisely the declared value
("resolved variant per stage") and no caller writes it into the knobs.

Not fatal, because `prompt_set_hash` is carried on the row and does differ across the four — so
the arms are *distinguishable*. They are not *nameable*: nothing in an artifact says which
variant produced which digest.

### 8. Nine declared record fields never reach any artifact row

`project_turn` is the only projector, and these are absent from it:

| field | why the register declares it |
|---|---|
| `schema_ranking` | "without it, 'the gold schema was not a candidate' and 'it ranked 4th' are the same observation" |
| `facet_hits` | "counts alone cannot attribute a finding to an asset, so no feedback loop is possible" |
| `lexical_coverage` | feeds `weak_retrieval` |
| `guard` | "written every turn including clear. A gate that leaves a trace only when it fires cannot afterwards be told from one never wired up" |
| `crossings` | "'how often does connect cross, and what is accuracy on those turns' is a query rather than a guess" |
| `pulled_in` | "answers what `expand_hops` is worth" |
| `delivery_hash` | "the only field that answers whether curated bodies reached the model" |
| `tool_delivered` | "real database values are the largest source of arm-to-arm variation in what the model sees" |
| `latency_sec` | wall clock |

`schema_ranking` is the costly one: the situation its note warns about — publishing a documented
failure bucket at a perfect score — is the situation anyone reading `runs/eval/` is in now.
`latency_sec` means no artifact records wall clock at all; `usage` carries tokens only.

Caught by rule R1. Three further R1 hits are legitimate and waived in the checker: `rewrite` (the
node cannot run on a single-turn eval), `cache_read_tokens` and `cache_write_tokens` (carried
per-call inside `usage`).

### 9. The four resume-drift keys — **fixed 2026-08-11**

`git_sha`, `git_main_sha`, `working_tree_dirty`, `diff_sha256`, all `Role.operational` and all
null on every row of every arm. `_knobs_resolved_gate` looks for disagreement across rows within
an arm, and four constants cannot disagree — so the gate that exists to stop a resume blending
two harness versions into one score could not fire, which is exactly what `diff_sha256`'s own
note says the absence would cost.

Resolved by `eval/provenance.py::git_provenance` and stamped onto every row by
`tools/run_datalake_eval.py`. `git_main_sha` is asked of `main` and then `origin/main`, because
on the experiment server the branch tip is never equal to main. A tree that is not a checkout
records four `None`s rather than raising: a run must not die because the harness is a tarball,
and an unanswerable question must read as unmeasured.

### 10. `serve_workers` — **fixed 2026-08-11**; `build_workers` — **deliberately not**

The README says "10 workers" in prose and nothing wrote it. `serve_workers` now comes from
`--workers`.

`build_workers` is left as a violation on purpose. This driver serves and does not build a
corpus; the knob's note is about a worker that "holds a connection AND a long-lived agent
conversation", which is the curator, and the curator is not in this repository. Writing a number
for a stage that did not run is finding 2 in a different hat — a null reads as unmeasured, a
value reads as a measurement. Wiring it from the driver would satisfy rule K1 through exactly the
laundering the rule's own docstring warns about.

### 11. The three scope keys — **fixed 2026-08-11**

`schemas_under_test`, `question_subset`, `split`: `Role.scope`, fatal on resume, never written.

Resolved by `eval/provenance.py::scope_identity`. Each is a **count and a digest**, not a list.
`schemas_under_test`'s note describes drift *between attempts at one run* ("a schema dropped from
one attempt leaves its YAML behind and competes as a router candidate"), which a digest detects
exactly, and carrying 57 names verbatim would add roughly 740 bytes to each of a 1 351-row
artifact's 6.4 KB rows. `question_subset`'s note is explicit that "a probe set's identity is not
its count", which is why the digest is there and not just the count. `split` is the dataset file
stem, `test_final` — not the word "train": the ids in that file are BIRD `train_*` ids re-split
by another repository, so answering "train or test" from them would be a claim about that
repository's intent.

`arms` escapes rule K1 only by a coincidental `"arms"` dict key in `eval/report.py`, and still
has no writer.

---

## Tier 3 — dead declarations, no measurement at risk

- **`facet_model`, `rewrite_model`** — `Role.comparability`, no reader, no writer. `rewrite_model`'s
  note argues at length for being separate from `facet_model`; neither is wired, and
  `llm_utility_model` is what both call sites actually use.
- **`negative_tau`** — no reader. `serve/nodes/negative.py:24` hard-codes `"tau": None`.
- **`live_capture_keys()` and the `reconstructable` column on `RecordField`** — zero readers
  anywhere, tests included. Two fields carry `reconstructable=True` and nothing asks.
- **`config_hash_keys()`** — still no reader, and the name is the finding. `register/knobs.py`'s
  module docstring says it derives "the serve config hash"; no serve config hash exists and the
  record carries `context_hash`, `delivery_hash`, `corpus_content_hash` and `prompt_set_hash`
  only. It is an alias returning `comparability_keys()`, which since 2026-08-11 *is* consumed —
  by `eval/report.py::knobs_comparable`. Either rename this to say what it returns or delete it;
  a function named for an artifact the system does not produce is how a reader comes to believe
  the artifact exists.
- **Tests-only readers**: `record_keys()`, `required_keys()`,
  `gate_keys()`, `defaults()`, `unknown_prompts()`, `KNOB_REGISTER`, `Tier`, `Role`, `Absence`,
  `RETIRED_CLAIMS`, `GATE_CONSUMED_TYPES`, `FACET_EXTRACTS`, `PROMPT_REGISTRY`. Most are
  legitimate — a register accessor whose only caller is a conformance test is doing its job — but
  the group is where the "eight tests that assert constants against themselves" pattern breeds.
- **`GRADER_ANSWER_PREFIXES`** (`register/stages.py:156`) — zero readers, including inside its own
  module. Its siblings `INFRA_ERROR_PREFIX` and `CRASH_REFUSED_BY` are both read by
  `classify_outcome`; this one is not, and the literals it holds appear nowhere else.
- **`CONTEXT_TYPES`** (`register/assets.py:155`) — zero readers anywhere.
- **`retrieve_hooks` state channel** — `serve/nodes/facets.py:385` already says so in a comment:
  "ADR 0011 put all five facets inside `FACET_EXTRACTS`, so an `else` here is unreachable and
  `retrieve_hooks` is dead with no test failing."
- **`cache_cost_reduction_target`** — an acceptance criterion recorded for the reader rather than
  read by code. Waived in the checker, but the role is wrong: it is `Role.comparability`, so
  changing a measurement *target* would register as a different treatment.
- **`facet_degraded`** — constant `False` on all 8,106 rows, re-confirmed. Already §3.10.

---

## Things that look like findings and are not

- `GREP_EXEMPT_PATHS`, `CITATIONS`, `RETIRED_CLAIMS` — `register/citations.py` says "nothing
  imports this module at runtime" and `tools/check_citations.py` reads it by AST, matching
  `GREP_EXEMPT_PATHS` as a string name. Real consumer, invisible to an import graph.
- `evidence` state channel — read at `serve/nodes/agent_core.py:337`.
- `terminal_reason` is null on all 8,106 rows, but no arm produced a `declined` outcome
  (7,365 answered / 576 capped / 128 refused / 37 clarification), so the field had no
  opportunity. Untested, not unwired.
- `error_type`, `failed_stage`, `crashed`, `guardrail_error`, `re_served`,
  `negative_failed_open` are all constant across the six arms because no arm crashed. Measured
  constants, not missing writers.

---

## The checker

`tools/check_declared_is_consumed.py`, AST-only, exit 1 on violation. Four rules:

| | rule | when written | 2026-08-10 | today |
|---|---|---:|---:|---:|
| K1 | every declared knob is named outside `register/` | 16 | 13 | 5 |
| K2 | every key written into a `knobs` mapping is a declared knob | 1 | 0 | 0 |
| R1 | every declared record field is named in `eval/harness.py` or `eval/projection.py` | 9 | 0 | 0 |
| S1 | every `ServeState` channel has a writer *and* a reader outside `state.py` | 1 | 1 | 1 |

It is red on purpose. A conformance check that went green on first run against a tree with this
population would be the same defect as the eight tests that asserted constants against
themselves. Six waivers are in place, each with a reason that says why a declaration with no
consumer is correct; removing all six adds six to whatever the count is.

Two laundering bugs were found by mutation-testing the checker against a fixture tree under
`--root`, and both are fixed and commented at the site:

- it scanned `tools/` including itself, so every knob it *named in a waiver* counted as consumed;
- the register's own `_f("run_id", …)` call counted as a read of the `run_id` channel, which made
  S1 pass on a channel nothing consumed.

Its known blind spot is stated in its docstring: K1's evidence is any occurrence of the name, so
a coincidental literal launders a knob (`arms`, `split` before it was caught). The sharper rule —
reachability from `knob_default` / `float_knob` / a `knobs[...]` write — was tried and rejected,
because `context_budget_chars` and `read_body_max_tokens` are read through hand-rolled
`for source in (state, knobs, cfg)` loops, and a gate that must be waived for correct code
teaches people to waive it.

Findings 1, 4 and 5 are invisible to it by construction: in each the declaration *has* a
consumer, and what is missing is the wire back to the record. Nothing static can see that; the
artifacts can, and the sweep that produced this document is the reproducible half — read every
row of every arm and report each field's distinct-value count.
