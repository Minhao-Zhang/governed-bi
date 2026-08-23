# 0013: The declared abstention policy — deciding to withhold, and saying why

- **Status:** Accepted, shipped **off** (2026-08-12). The node, the closed vocabulary, the knob,
  the record field and the artifact column are in the tree; `abstention_policy_enabled` defaults
  to `False`, so v4 remains the measurement control and no number in `runs/` moves. Turning it on
  is `tools/run_datalake_eval.py --abstain`, which is one paired arm.
- **Deciders:** project owner (2026-08-12).
- **Scope:** *whether this turn should be answered*, decided before the agent spends a `run_query`
  attempt, in a named policy with a closed vocabulary of reasons and the evidence behind each one.
  **Not** a confidence score, not a graded certainty, not a learned abstainer, not a change to any
  layer's refusal.
- **Related:** [0006](0006-execution-time-governance.md) is the layer stack whose refusals this is
  *not*; [0007](0007-http-surface-and-the-ui-contract.md) forbids a trust field on the answer card
  and this stays on the right side of it; [0010](0010-live-stage-events.md) gains the `abstain`
  step; [0012](0012-access-seam-principal-and-authorization.md) split Layer 6 so that a refusal's
  reason means one thing, which is the same argument one level up.

---

## Context

### The engine declines by accident

The project's headline is *a system that answers with confidence and declines on purpose*. The
second half is not true yet, and [open work](../open-work.md) §4.1 and §4.2 say exactly how it
fails:

- On the v4 arm the engine abstains on 73 of 1 351 turns. **19 of the 20 refusals end on
  `r_table_not_licensed`**, and all four clarifications licensed nothing at all.
- `licensed` is simultaneously the retrieval budget and the governance allowlist, so a retrieval
  miss *becomes* a hard refusal. Nothing decided to withhold: the shortlist came up short, the
  agent wrote SQL against a table it had not been given, and Layer 6 refused it — five times, at
  the cost of five model calls.

The property this produces is real and worth keeping — 77.4% of the priced declines would have
been wrong, and delivered accuracy is 3.16× the accuracy withheld. But it is a property of the
plumbing. Ask the engine *why* it withheld and the only answer available is the rule the last
statement happened to trip, which sends a reader to the layer stack for what is a routing
problem.

### What is not available, and why this ADR does not reach for it

The obvious shape — score the turn, threshold the score — has been measured and it failed. This
is the part worth reading before proposing an alternative:

- **The reflector scored OOF AUC 0.597**, worse than counting the agent's output tokens, and
  combining the two is worse than the token count alone (open-work §3.11,
  `git-history:docs/analysis/risk-coverage-v4.md` §6). Its `unsure` bucket is **as likely to be
  right (0.766) as its `correct` bucket (0.763)** — a judge with no perception of its own
  uncertainty to express.
- **Everything that does not read meaning caps at 0.721.**
- **No signal beats the engine at the engine's own coverage.** Every risk-coverage curve reads
  0.7144 at coverage 0.9460 — arithmetic, not agreement: a ranking reorders turns the engine
  already agreed to answer. Buying accuracy costs answers, and the best available trade loses
  **162 right answers to gain 8.1 accuracy points**
  (`git-history:docs/analysis/selective-delivery-v4.md` §4).

So a graded `certainty` field is confidence theatre, and these are the measurements that
make the word fair rather than rude.

**The gap is not a score. It is that the decision is nowhere declared.**

---

## Decision

### 1. A named policy, evaluated before the agent

A node, `abstain`, between `assemble` and `agent_core`:

```
… → connect → assemble → abstain → agent_core → reflect → narrate → stamp
                            └── withhold ──→ decline ──────────────↗
```

**Before the agent, and that placement is the whole substance.** A policy evaluated after five
refused `run_query` attempts is a report on a decision, not one; it also costs six model calls to
reach a conclusion `connect` already had the evidence for. The test that pins this asserts the
withheld turn made *fewer model calls* than the committed one and wrote **no ledger row at all**.

The policy carries its own name and version, `context_sufficiency_v1`, on every verdict. The rule
set is the treatment: adding a fifth rule changes which turns are delivered, and two arms whose
verdicts both said `abstention_policy` would compare as one.

### 2. The reasons are a closed vocabulary, in the table the existing readers already use

`register/stages.py` declares `ABSTENTION_REASONS` and folds it into `REFUSED_BY_TO_STAGE`, all
four mapping to a new `Stage.abstain`:

| reason | fires when | why withholding is right |
|---|---|---|
| `retrieval_channel_failed` | a facet channel that was configured to run, ran, and errored | the shortlist came from a retriever that is not the declared one; answering records one treatment and delivers another |
| `nothing_licensed` | the turn licensed no table | every statement the agent can write names a relation Layer 6 will refuse |
| `empty_context` | the rendered block is empty | the model has the question and nothing else, so any SQL is invented rather than grounded |
| `licensed_table_evicted` | the char budget dropped a whole licensed table before the model saw it | the turn is asking for SQL over a relation it did not show |

The reason is written to **`terminal_reason`** — the same channel `route` and `connect` write
their declines into — so an abstention is read exactly as every other terminal is, rather than
through a second field only a new reader would know to open.

> **This paragraph used to name three readers of `REFUSED_BY_TO_STAGE`, and on 2026-08-12 none of
> the three read it.** The claim was that `classify_outcome`, "the refusal histogram" and
> `eval/report.py` already consume that table, so putting the reasons there makes them consumed
> too. Measured:
>
> - `classify_outcome` never consults it. Any truthy `refused_by` returns `Outcome.refused`, so
>   `tests/serve/test_the_abstention_policy_is_declared.py`'s assertion on it held equally for
>   `'banana_not_declared_anywhere'` — a check that cannot fail for any member of a vocabulary is
>   not a check on the vocabulary.
> - The only histogram that existed, `tools/datalake_report.py::_refusal_layers`, counts
>   `attempt.reason_code` off the **ledger**. A withheld turn writes no ledger row at all —
>   acceptance criterion 3 — so the four reasons were not merely uncounted there, they were
>   unreachable.
> - `eval/report.py` had zero references to `refused_by`, `terminal_reason` or the vocabulary.
>
> Three named consumers, none real: the declared-machinery-with-no-reader shape the argument was
> itself invoking. **The reader was built rather than the sentence weakened.**

`eval/report.py::refusal_histogram` is that reader. It counts a refused turn's
`terminal_reason` (falling back to the coarser `refused_by`), attributes each to a `Stage` through
`REFUSED_BY_TO_STAGE`, and puts anything the table does not declare in a named `unattributed`
bucket — which is what "closed" has to mean once artifacts exist: a reader can see that the
histogram does not add up and see which string is why. It reaches every arm summary through
`eval/report.py::summarise`, and `tools/datalake_report.py::print_report` — the reporting half
`tools/run_datalake_eval.py` calls after the last question is graded — prints it beside
`_refusal_layers`' layer histogram.

What actually held the vocabulary closed before that, and still does the day-to-day work, is **two
import-time guards**, which this section previously barely credited: every reason must map to
`Stage.abstain` and to nothing else, and the policy's rules and the register's set must be **equal
in both directions**. A rule with an undeclared reason writes an unattributable terminal; a
declared reason with no rule reads, to anyone grepping the vocabulary, as a decision the engine
can take. What the guards cannot see is a *third* party writing a string neither of them declares —
they compare declarations to declarations, never to a row. That is the gap `unattributed` covers.

`Outcome` stays `refused`, never `crashed`. Declining on purpose is the product working, and
`stages.py` already asserts the two do not mix.

> **Amended 2026-08-18: a model declining in prose is not this policy, and now says so.**
> `Outcome` gained `no_sql` for a turn that ended having executed no governed statement
> ([0006](0006-execution-time-governance.md) §5). It matters here for two reasons. First, the
> engine's *accidental* decline — the thing this ADR exists to replace — had one more shape than
> §"The engine declines by accident" counted: besides the refusal that is really a routing miss,
> there is the turn where the model reads the context, finds the question's terms undefined, and
> answers in prose. That turn recorded `outcome: answered`, so it appeared in no abstention
> figure at all, and criterion 3 below asserted the appearance rather than noticing it. Second,
> the two must not be confused going forward: this policy is a **declared decision with a reason
> and its evidence**, taken before the agent spends an attempt, and `no_sql` is the absence of
> one — a turn the ledger can only report ran nothing. `Outcome.no_sql` carries no
> `terminal_reason` and no `abstention` verdict, so `refusal_histogram` still counts exactly the
> decisions and nothing else. If the case is worth a policy, it is worth a *declared* rule with
> its own reason; recording it as an answer was the only option this ADR ruled out.

### 3. Every rule is a deterministic predicate over recorded state

No model call, no threshold, no fitted parameter, no free variable. `decide()` is a **pure
function of `ServeState`**, which is what makes "the engine can say why it withheld, in terms a
person can check" true rather than asserted: a reader holding the artifact row can recompute the
verdict and disagree with it.

The evidence answers the three questions §4.1 asks an abstention to be able to answer:

| | field |
|---|---|
| what was licensed | `n_licensed`, `licensed` (capped at 12; the count is not capped, so the cap cannot hide the size), `schemas` |
| what was missing | `failed_channels`, `tables_evicted`, `bodies_evicted`, `context_chars` |
| what the question asked for that the context could not supply | `question_terms_in_corpus` |

**`question_terms_in_corpus` is evidence and is not a rule.** It is `lexical_coverage`, and
nothing branches on it. A threshold there would be exactly the uncalibrated refusal gate
`negative_tau` ships `UNSET` rather than guess at — *"this cannot be calibrated on a benchmark
whose questions are all answerable by construction, and an uncalibrated refusal gate is worse
than none"* — arriving under a different name. A test drives two states differing only in that
number, at 0.0 and 1.0, and requires the same verdict.

### 4. Off by default, and the verdict is written either way

`abstention_policy_enabled` ships `False`, `Role.comparability`. Comparability rather than
operational because the policy changes **which turns are delivered**, which is the coverage half
of every selective-accuracy figure; two operating points must not share a config hash.

The verdict is written on every turn that reaches the node, including the disabled ones, as
`{"outcome": "disabled", "evidence": {}}`. That is `negative`'s argument one gate over: *a gate
that leaves a trace only when it fires cannot afterwards be told from one that was never wired
up*. It also makes a control arm **nameable** rather than merely silent — a row saying "the
policy was off" is what a paired comparison needs from its control.

The node is registered `stream=False`, like `reflect`, and emits its own single row only when it
judged something. A disabled node putting two rows on every timeline would have changed the event
stream of every arm measured so far.

### 5. Where it must *not* appear

ADR 0007 deleted `tier`, `safety_clearance` and `semantic_assurance` from the answer card, and
`ui/lib/schemas.ts` pins that they must not come back. Nothing here goes near it:

> **Reporting why the engine withheld is the ledger. Scoring how sure it is is theatre.**

A test enforces the line over the module's **AST** — every identifier and every non-docstring
string literal — because a behavioural check that no verdict carries a score passes for every
input it happens to try, and the module's own prose argues at length about the confidence field
it does not have.

---

## Rejected alternatives

**A confidence score, graded certainty, or a learned abstainer.** Measured; see Context. The
follow-ups that suggest themselves — a graded `confidence`, `right` instead of `answered`, a
`TypedDict` of `Literal`s through `with_structured_output` — all address *expression*, and
expression is not the problem.

**A threshold on `lexical_coverage`, `n_licensed` or a fused retrieval score.** The tempting one,
because the numbers are already on the row. Rejected on `negative_tau`'s argument: a threshold
needs a calibration set, the benchmark's questions are answerable by construction, and a gate
whose false-refusal rate nobody has measured is worse than no gate. `n_licensed == 0` *is* a rule
here — but as a **structural** fact ("no relation exists for a statement to name"), not as the
bottom of a range.

**Decide inside `agent_core`, after the first refused attempt.** Cheaper to wire and it is what
the engine effectively does today. It is also a report rather than a decision: the attempt has
been paid for, and its `r_table_not_licensed` is what the histogram then records — the exact
conflation this ADR exists to end.

**Widen the vocabulary to cover clarification.** `ask_user` pauses a turn; it does not withhold
one, and `Outcome.clarification` is a separate value for a reason. A policy that could also
*ask* would need a responder and a resume protocol, both of which exist and neither of which is
this.

**Put the reasons in `serve/` beside the policy.** Then a fifth reason arrives by a node writing
a new string, and the refusal histogram grows a bucket nobody declared. The register is what
makes a vocabulary closed rather than habitual.

**Fold the reason into `terminal_reason` and stop there, with no evidence field.** Cheapest
honest option, and it fails the readable half: `nothing_licensed` on its own does not say *what*
was routed or *how much* context was rendered, so the first question anyone asks needs the turn
re-run. The evidence is a few hundred bytes and it makes the verdict falsifiable.

**On by default.** It would move every measured number in `runs/`, retire v4 as a control, and do
it before anyone has priced the trade. `selective-delivery-v4.md` is three hundred lines about
how easy it is to buy accuracy with coverage and call it a win.

---

## Consequences

**What this buys.** The engine has an answer to "why did you not answer" that is a named rule
with its evidence attached, in a vocabulary the refusal histogram already reads. §4.1's honest
framing — *the engine declines when its own context is insufficient* — becomes a mechanism with a
name rather than a description of a side effect. And the abstention claim gains a falsifier: a
turn withheld under `nothing_licensed` can be checked against the row's own `licensed` field.

**What it costs.** One node in the hot path, four frozenset/length tests deep, on every turn. One
more comparability knob, so every artifact predating it is absent that key.

That was written as "no pair on disk reaches `knobs_comparable`'s treatment judgement", which is
imprecise in the direction that flatters it. Measured: the **six proxy arms** went from 4 missing
comparability keys to 6, and no pair among them was comparable before this ADR or after it — for
them the knob changes nothing. `runs/eval/live_full_gpt-5.6-luna_xhigh_topdefault_lexical.jsonl`
went from **0 missing to 2** (`access_grant` from ADR 0012 and `abstention_policy_enabled` from
this one), so the two ADRs of 2026-08-12 are what took the one artifact that *would* have been
comparable out of comparability. It is a two-row smoke run and nothing is quoted from it, so the
cost is real and the consequence is not — but the blanket sentence hid a distinction the next run
will care about.

A record field of a few hundred bytes per turn. And a second vocabulary readers must keep apart
from the layer stack's `r_*` rules: **a layer refuses a statement, the policy withholds a turn**,
and §2 is the place that distinction lives.

**What it does not cover.** It does not decide anything about *the question* — only about the
context this turn assembled. It says nothing about whether an answer, once given, is right; that
is `reflect`'s job and `reflect` is off for its own measured reasons. It does not change any
layer, any refusal, or `licensed`. And it has **not been run**: the trade it makes — how many
turns it withholds and how many of those would have been right — is unmeasured, which is what the
paired arm is for and why the knob ships off.

---

## Acceptance criteria

**Met on this tree (2026-08-12):**

1. Off by default. `knob_default("abstention_policy_enabled") is False`, and with the knob off
   `abstain_node`'s **entire state update is the one declared field** — asserted on the update
   itself, since there is no "before" to compare a record against once the node is in the graph.
2. No timeline row when off; exactly one, `status: declined`, when it withholds. Measured on the
   wire by patching the stream writer, over the served graph.
3. A turn that licenses nothing **reaches the agent and comes back with prose and no
   statement** with the policy off, and is **withheld** with it on, through `build_serve_graph` —
   the topology `langgraph.json` runs. The withheld turn made fewer model calls and wrote no
   ledger row, which is the "before the agent spends its attempts" claim.
   *(Restated 2026-08-18. This criterion said "is **answered** with the policy off", which the
   test asserted and which passed — the control turn's model answers from context without a
   statement, and `stamp` called that `answered`. It records `Outcome.no_sql` now. The comparison
   the criterion is about is unchanged and the control is still the control; what changed is that
   the control's own outcome no longer overstates it.)*
4. The reason reaches `terminal_reason`, the turn record's `abstention` field, and the artifact
   row, and `classify_outcome` returns `Outcome.refused` for every member of the vocabulary.
5. Every rule fires on its own evidence and `rules_evaluated` stops at the rule that fired.
6. `not_configured` is not a failure — a lexical-only deployment does not abstain on every turn.
7. Two states differing only in `lexical_coverage` (0.0 and 1.0) reach the same verdict.
8. No trust vocabulary in the module, over its AST.
9. Eleven mutations against the policy, the knob, the wires and the graph edge; eleven caught.
10. `tools/run_datalake_eval.py --abstain` sets the knob and tags the artifact `_abstain`, so a
    resume cannot merge an abstaining run into a committing one.

**Owed, and not claimed:**

11. **The trade is unmeasured.** How many turns the policy withholds on a real arm, and what share
    of them would have been right, are the two numbers that decide whether it should ever be on.
    One paired arm, `--abstain` against the same corpus and prompt set.
12. **`retrieval_channel_failed` has never fired on real traffic.** `facet_degraded` is `False` on
    every row of every artifact read so far, so the rule is correct by construction and untested
    against a rate.
13. **`licensed_table_evicted` fires on a population measured at 0%.** The budget bit on 19 of
    1 351 v4 turns and dropped *bodies only*, never a whole table. The rule reads
    `tables_dropped`, so on that arm it would have withheld nothing.

---

## Open questions

1. **Should `empty_context` and `nothing_licensed` be one reason?** They almost always co-occur —
   an empty licence usually renders an empty block. Kept apart because the fixes differ: one is
   the router, the other is the renderer's budget, and a turn can license tables and still render
   nothing if the ladder exhausts.
2. **Should the policy be able to ask instead of withhold?** `ask_user` exists and a clarification
   is a better outcome than a decline when the missing thing is knowable from the analyst. It
   needs the responder protocol and a rule for which reasons are askable, which is its own ADR.
3. **Should a withheld turn tell the analyst which rule fired?** Today `decline_node` emits system
   copy and the reason stays in the ledger. `nothing_licensed` is safe to say out loud;
   `retrieval_channel_failed` is an operational fact an analyst can do nothing with. Undecided,
   and deliberately not split by hand until someone asks for it.
4. **Does the policy belong in the eval driver's arm profile?** `arms.toml` declares what each arm
   changed and `knobs_comparable` refuses a pair that cannot name it. An `--abstain` arm would
   need a profile row before it could be compared, which is the intended pressure and not a
   defect.
