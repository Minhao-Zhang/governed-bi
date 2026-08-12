# Every hand-parsed model reply in `src/governed_bi/`

**Two defects, both narrow, both real, both now fixed at `95e3b07`.** The scope gate's fail-closed
property did not hold for one class of reply — anything beginning with the affirmative token,
which includes the gate's own instruction sentence — and that class cleared the gate leaving no
trace. The reflector accepted a verbatim echo of its answer template as a fully-formed `answered`
verdict with a fabricated reason. Everything else checked is fine and is said so plainly below.

Audited at engine `a5727b0`. This is a code audit; no figure here is measured on a run. The
sections below keep the evidence, because each fix is shaped by what the probe found and a fix
without its evidence is a change nobody can review later.

## 0. The complete inventory

Four call sites in `src/governed_bi/` send a prompt and read the reply as text. There are no
others: `grep`ing for `ainvoke` over the package returns exactly these four plus `agent_core`'s
nested `create_agent`, whose output is consumed as **tool calls**, not text.

| Site | What it reads | Malformed reply | Verdict |
|---|---|---|---|
| `serve/nodes/guard.py:136` (`_bi_scope`) | `yes` prefix | **failed open** on a prefix-affirmative non-answer; fails closed on everything else | **defect, fixed at `95e3b07`** |
| `serve/nodes/reflect.py:207` (`_read_verdict`) | `VERDICT:` / `REASON:` lines | **failed silently** on a template echo; fails safe on everything else | **defect, fixed at `95e3b07`** |
| `serve/nodes/facets.py:309` (`_rewritten_query`) | free text | fails safe *and visibly* | fine |
| `serve/nodes/narrate.py:109` (`_generate`) | free text | fails safe | fine |
| `serve/nodes/agent_core.py` (`create_agent`) | tool-call args | not a text parse — see §2.3 | fine |

Embedder replies (`model/*_embedder.py`) are vectors read through LangChain's typed client and
are out of scope for this question.

## 1. The two defects, and the fix each got

### 1.1 The scope gate cleared on any reply that *started with* "yes", including its own prompt

`serve/nodes/guard.py`:

```python
_IN_SCOPE = "yes"
answer = str(getattr(reply, "text", "") or "").strip().lower()
...
if answer.startswith(_IN_SCOPE):
    return GuardVerdict(outcome="clear", rule_id=None, detail=None), spent
```

The module comment states the design intent and it is the right intent:

> Keying on the negative instead would make any unexpected reply read as "in scope", failing
> **open** exactly when the model was confused.

Keying on the affirmative does fail closed for *arbitrary* text. It does not fail closed for text
that happens to begin with `yes`. Probed against the live parser:

| reply | outcome |
|---|---|
| `"YES if it is in scope, NO if it is not."` — the prompt's own final sentence | **clear** |
| `"YES/NO"` | **clear** |
| `"Yes and no"` | **clear** |
| `""`, `"MAYBE"`, `"I'm sorry, I can't help with that."`, `"NO — general knowledge"` | blocked |

The bottom row is what `tests/serve/test_guard_bi_scope.py::test_an_unparseable_reply_fails_closed`
parametrises over. The test is not wrong; its sample is one-sided. Every case it lists is a
non-affirmative-prefixed reply, which is precisely the class where the rule already holds. No case
in the suite starts with the affirmative token without being an affirmative.

**Why this ranks first.** Two properties compound:

- The failure direction is *permissive*. A confused model's reply is recorded as `clear`, which
  in `graph.py::_after_guard` routes to `rewrite` and the turn proceeds.
- **The permissive branch records nothing.** `detail` is `None` on `clear`; only the `blocked`
  branch carries `answer[:200]` into the record. So a block caused by a parse failure is
  recoverable offline from `guard.detail`, and a *clear* caused by one is not recoverable at all.
  The two error directions have opposite observability, and the invisible one is the unsafe one.

**Scope of exposure.** The rule is enabled on exactly one path: `api/graph_app.py:125` sets
`guard_rules_enabled={BI_SCOPE_RULE_ID: True}`, which is the LangGraph-server path the UI uses.
Every eval arm (`eval/arms.py`, `tools/run_datalake_eval.py`) passes `guard_rules_enabled={}`, so
no measured number is affected. This is a production-path finding, not a measurement one.

**Likelihood** is low with a competent utility model asked for one word. It is not zero, and it is
not the kind of thing that gets noticed when it happens, because the record shows an ordinary
`clear`.

**The fix, shipped at `95e3b07`**: tokenise and require an unambiguous affirmative, and separate
the "neither" case from the substantive `no`. `_clears_scope` in `guard.py` is the result. What
was proposed and what landed:

```python
words = re.findall(r"[a-z]+", answer)
if words[:1] == ["yes"] and "no" not in words[1:]:
    -> clear
elif words[:1] == ["no"] or (words and "no" in words and "yes" not in words):
    -> blocked, detail = "model judged the question out of scope: ..."
else:
    -> blocked, detail = "unparseable: ..."     # same safe outcome, distinguishable reason
```

This keeps every case the current test asserts (`"Yes."`, `"YES\n"`, `"yes, that is in scope"` all
still clear) and closes the three rows above. The `unparseable:` prefix in `detail` is enough to
count the two kinds of refusal apart offline, since `stamp` copies `guard` into the record whole.

### 1.2 The reflector read its own answer template as `answered`

`serve/nodes/reflect.py::_read_verdict` is, as the brief says, well-built: lenient about layout,
strict about vocabulary, and an undeclared label yields `verdict: null` with `why_unmeasured`
rather than being mapped onto the nearest legal value. One input defeats it. Probed:

| reply | `_read_verdict` returns |
|---|---|
| `"VERDICT: answered \| wrong \| unsure\nREASON: one sentence, under 25 words"` | `('answered', 'one sentence, under 25 words')` |
| `"VERDICT: answered\|wrong\|unsure"` (no spaces) | `(None, None)` |
| `"VERDICT: probably fine"` | `(None, None)` |
| `"verdict: **answered**"` | `('answered', None)` |

The first row is the REFLECT `v1` prompt's own closing instruction, echoed verbatim. The parser
takes `word[0]` of the colon's remainder, and `answered` is the first alternative listed. The
result is not an unmeasured row — it is a complete, plausible-looking verdict carrying the
favourable label and a "reason" lifted from the instruction text. Nothing downstream can tell it
from a real judgement, which is the definition of **fail silent**.

Three things make this worse than its blast radius suggests:

- **The favourable label wins by position.** The prompt lists `answered | wrong | unsure`, so an
  echo always reads as "the answer was fine". A biased instrument is worse than a noisy one.
- **The whole point of this node is to be scored.** `tools/score_reflector.py` builds a confusion
  matrix of verdict against EX. Echo rows land in the `answered` row and inflate whatever
  agreement the judge appears to have with gold.
- **It is whitespace-dependent.** The spaced form parses, the unspaced form does not. Two models
  that both echo the template produce different measurements.

Consequence was bounded: `reflect_enabled` ships `False`, the node changes no control flow, and
`reflect_verdict` has no quotability gate (deliberately — `register/record.py`). No shipped number
was contaminated.

**It also never fired.** The one reflected arm ran on `2da223c`, which predates the fix, so an
echo would have parsed as a complete favourable verdict. Zero of its 1,351 rows carry the
signature, and `why_unmeasured` is empty on every row — so the arm is uncontaminated and the
hand parser's robustness is settled ([risk coverage](risk-coverage-v4.md) §6).

**The fix, shipped at `95e3b07`** — two lines, no schema:

```python
declared = [w for w in word if w in REFLECT_VERDICTS]
if len(declared) == 1 and word[0] == declared[0]:
    verdict = declared[0]
```

A line naming two or three declared labels is a model that did not choose, which is exactly the
`why_unmeasured` case the parser already handles correctly for `"probably fine"`.

## 2. The parsers that are fine

### 2.1 `facets.py::_rewritten_query` — fails safe, and visibly

There is no vocabulary here, so there is nothing to be malformed against: the reply *is* the
search query. Both failure paths are handled correctly and, more importantly, are handled
*differently from success*:

- **Exception** → returns the raw question, `Channel.extraction` is not added to `ran`, and
  `_channels_for` reports `failed`.
- **Empty reply** → same, and the usage row is appended *before* the empty check, so a rewriter
  that billed and said nothing is priced.

That ordering is the load-bearing part. `register/record.py` gates `facet_channels` on
quotability precisely so a rate-limited extractor cannot let an arm quietly become single-pass
retrieval while every channel claims to be working. This node is the reason that gate has teeth.

The one thing nothing checks is a reply that is non-empty and useless — a refusal sentence, or 500
words in defiance of the prompt's "under 30 words". That marks `extraction: ran` and gets
searched. I am not calling it a defect: the rewritten text is published per facet in
`facet_hits.queries` (`stamp.py:241`), so it is fully auditable after the fact. What is absent is
a *counter*, not the evidence.

### 2.2 `narrate.py::_generate` — fails safe

Prose, no vocabulary, nothing to parse. `text or None`; an exception returns `(None, None)` and
the node returns `{}`, leaving `answer_text` at its reset value. `GraphBubbleUp` is re-raised
before the blanket catch, so a clarification interrupt is not swallowed. `surface_answer_text`
refuses to backfill model prose onto a non-`answered` outcome. The residual risk is
*content* — a narrator that misreads the table — which no parser and no schema addresses.

### 2.3 `agent_core` — not a text parse

The one place model output becomes executable is `run_query(sql=...)`, and it arrives as a
tool-call argument validated by the tool's own signature, then checked by the deterministic layer
stack in `fetch.run_query` before anything reaches a database. `_last_executed_sql` prefers the
**ledger's** `executed_sql` over the model's argument, so the recorded statement is the one that
ran. `_last_run_query_sql` is only the fallback for a refused attempt. Nothing here interprets
free text.

## 3. Where the vocabulary is declared more than once

| Vocabulary | Declarations | Bound by a test? |
|---|---|---|
| reflect verdicts | **three**: `REFLECT_VERDICTS` (`reflect.py:55`); the prompt's `answered \| wrong \| unsure` (`prompts.py:391`); `labels = ["wrong", "unsure", "answered", "(unmeasured)"]` (`tools/score_reflector.py:316`) | no |
| scope gate tokens | **two**: `_IN_SCOPE = "yes"` (`guard.py:32`); the prompt's `YES`/`NO` (`prompts.py:229`). The `NO` token is read by no code at all — only the *absence* of `yes` is | no |
| facet rewriters | one, in `_REWRITE_TAIL`; no code reads it | n/a |
| narrate | one, in the prompt; no code reads it | n/a |

`grep -rn "REFLECT_VERDICTS\|_IN_SCOPE"` over the whole tree returns four lines, all inside the
two defining modules. Neither vocabulary is referenced by a test, and
`tests/conformance/test_every_prompt_carries_its_variant.py` checks only that the prompts are
*registered*, not that they list the labels the code accepts.

The score_reflector duplicate is the one that most clearly violates this repo's own rule — the
module exports `REFLECT_VERDICTS` in `__all__` and its only would-be consumer re-types the list.
Importing it there is free and removes one of the three.

A cheap guard for the remaining split, in the spirit of the repo's conformance tests: assert every
member of `REFLECT_VERDICTS` appears in `prompt_text("reflect")` and that the prompt names no
label outside it. Same shape for `_IN_SCOPE` against `prompt_text("bi_scope")`. Both are
mutation-verifiable: delete a label from the prompt, watch it fail.

## 4. Would a typed schema help? Site by site

`with_structured_output` appears nowhere in this repository, and that is the right default for
three of the four sites.

### 4.1 `bi_scope` — a schema here would make the gate **worse**, unless `include_raw=True`

This is the one place where the brief's warning is not a caveat but the whole answer.
`with_structured_output` without `include_raw=True` raises on validation failure. The raise lands
in `_bi_scope`'s `except`, which returns `error_failed_open` — **and the question goes through**.
So swapping today's parser for a bare schema converts the current failure posture from *closed*
to *open* on exactly the input that motivated the change. That is a strictly worse trade, and it
is not obvious from reading either the LangChain docs or this file.

With `include_raw=True` the parse failure is a value rather than an exception, and can be mapped
to `blocked`, preserving today's posture. At that point the schema buys: one declaration instead
of two, and constrained decoding that structurally cannot emit the instruction sentence. Costs,
specifically:

- **Latency on the critical path.** This gate is a one-word classification in front of *every*
  turn and is the delay before anything appears in the UI. A JSON envelope replaces a ~1-token
  completion with ~10–15 output tokens; on providers that implement structured output as a forced
  tool call it is also a different, generally slower decode path.
- **`prompt_set_hash` moves**, because the instruction sentence about replying `YES`/`NO` becomes
  redundant and would be deleted. Any before/after comparison of refusal rates spans a prompt
  change.
- **It does not remove the branch it is meant to remove.** With `include_raw=True` there is still
  a "the schema did not validate" case to handle by hand, so the call site keeps a decision it
  keeps today.

The §1.1 tokeniser fix costs none of that and closes the same hole. **Recommendation: fix the
parser; do not adopt a schema here.**

### 4.2 `reflect` — the arm ran, and the answer is now no

This section recommended a typed schema for the reflector. **The arm that would have been its
baseline has since run, and it closed the direction**: the judge scores AUC 0.597, below the count
of tokens the agent emitted, and the turns it calls `unsure` are as likely to be right (0.766) as
the ones it calls correct (0.763). A judge whose "I cannot tell" bucket matches its "this is
right" bucket has no perception of its own uncertainty, and no output format supplies one. See
[risk coverage](risk-coverage-v4.md) §6 and [open work](../open-work.md) §3.11.

Two carried notes survive and are worth keeping if anyone revisits it:

- `include_raw=True` is mandatory for the reason §3.11 states, and §4.1 above is the concrete
  demonstration of what the bare form does to a call site whose `except` has a consequence.
- The transport objection is dead. `tools` in `model/provider.py` only selects OpenAI's Responses
  API; `response_format` is available on the path the utility model already uses.

The argument for the schema was a `confidence: int` field, on the grounds that three labels are
three operating points and [risk coverage](risk-coverage-v4.md) is an analysis of a curve. The arm
answered that too: the three-way label has no resolution to grade more finely, because the
middle label carries the same accuracy as the favourable one.

### 4.3 `facets` and `narrate` — no

Both emit free text with no closed vocabulary. A `TypedDict {query: str}` or `{sentence: str}`
adds envelope tokens, adds a validation failure mode where none exists, and constrains nothing —
the field is an unconstrained string either way. For facets it would multiply by five, on five
concurrent calls per turn. There is no defect to fix and no ambiguity to remove.

## 5. Two adjacent findings this audit turned up

Neither is a parser, both are on the governance path, and both are cheap to state.

### 5.1 There is no quotability gate on `guard`, and two docstrings used to claim one

`serve/nodes/guard.py` said "the sentinel … joins a run's quotability gates", and
`serve/nodes/stamp.py` said "`register/record.py` gates on it". **Both are corrected; the missing
gate is still missing.**

`GATE_CONDITIONS` is derived from `_f(..., gate=...)` in `register/record.py`, and
the `guard` field is declared with no `gate=`. `measure/gates.py::GATE_IMPLEMENTATIONS` has six
entries and none of them reads `guard`; the analogous sentinel one field down **is** gated
(`negative` → `"no negative_gate error_failed_open"`). So an arm in which the scope gate was
enabled and could not run — every turn `error_failed_open` — is fully quotable and no gate
objects.

Practically inert today, because the rule is off in every eval arm. It is a live trap for the
first arm that turns it on.

Of the two available repairs — add `gate="no guard error_failed_open"` to the field with a
matching `_zero_count_gate`, or correct the docstrings — the second is done, because a call site
reasoning from a guarantee that is not there is the worse of the two failures. Adding the gate is
still open, and it should be decided by whoever turns the rule on for an arm.

### 5.2 The record cannot distinguish "the scope gate passed" from "the scope gate was off"

With `guard_rules_enabled={}` the node returns `{"outcome": "clear", "rule_id": None}` — byte
identical to a gate that ran and cleared. This is the exact failure `register/record.py`'s
description of the field warns about ("a gate that leaves a trace only when it fires cannot
afterwards be told from one never wired up"); the `error_failed_open` sentinel covers only
*enabled with no model*, not *disabled*.

It is recoverable: `guard_rules_enabled` is a `Role.comparability` knob written into
`knobs_resolved` (`serve/session.py:325`), so which rules were on is in every record at run level.
Worth knowing that the recovery is one join away rather than on the row.

## 6. What this audit did not check

- Prompt *content* — whether `bi_scope`'s scope definition is right, whether `narrate`'s rules
  produce faithful sentences. Only the reply-handling code.
- Anything outside `src/governed_bi/`, except `tools/score_reflector.py`, which is named only
  because it re-declares a vocabulary `src/` already owns.
- Whether any of the failure modes above has ever occurred on a real run. No run artifact was
  read; the two defects are demonstrated against the parsers in isolation.
- `RetryPolicy` is deliberately not discussed. Re-running a node after it failed resamples a draw
  after seeing it, and none of the fixes above wants one: each is a one-shot parse correction.
