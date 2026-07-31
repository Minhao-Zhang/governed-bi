# The conversation we never measured

> **STATUS 2026-07-31 — LOAD-BEARING. Do not delete yet.**
>
> [rebuild-checklist.md](rebuild-checklist.md) §7.1 took this file's *conclusions* and
> left the *reasoning*. Worse: the checklist's non-goals section says "multi-turn recovery is
> handled by **3.12 / 3.13**" and **neither number exists in that document** — §8's F1
> (retrieve on previous-turn + current question) and F2 (coverage floor + route stickiness) are
> the only definition of what those two items are.
>
> Still to migrate: §8's F1/F2 shapes → new checklist 7.1.1 / 7.1.2, replacing the dangling
> `3.12 / 3.13` reference · §3 (why AUDIT S4 closes the only recovery path) → 7.1's rationale ·
> §5 (working memory held twice, growth quadratic-ish) → the streaming-side `max_turns` item in
> 5.3.3 · §4's stamp-is-most-confident-where-it-has-least-right finding.
>
> Absorbed already: §6's synthetic-pronoun arm design → §7.1 · §7's `n_human` correction → 1.11 ·
> §8's F3 → non-goal (decision 19) · §8's F4 → needs an ADR amendment, recorded in decisions.

An adversarial analysis of the multi-turn path. One sentence version: **every number
this repo has ever produced is turn 1**, and the conversational path has a structural
defect whose consequence is governance-shaped — a correct follow-up can be refused, and
the control that causes it is one we added on purpose for a good reason.

Verified at `2187ead`. Companion to [framework-and-logging-audit.md](framework-and-logging-audit.md)
(§G2 is the persistence half of this) and [book-fidelity-assessment.md](book-fidelity-assessment.md)
(§0 explains why we have no Query Understanding node, which turns out to matter here).

---

## 1. What a turn actually sees

Order of operations for one question (`analyst/agent.py`):

| Step | Node | Input it works from | Sees history? |
|---|---|---|---|
| 1 | `ingest` (:552) | raw question | — |
| 2 | `refuse_gate` (:579) | **raw question** vs. negative-example patterns | **no** |
| 3a | `shortlist_schemas` / `pick_schema` (:699–706) | **raw question** | **no** |
| 3b | `retrieve` (:797) | **raw question** | **no** |
| 3c | `assemble_context` (:840) | retrieval result **+ `history`** | **yes** |
| 4 | `agent_core` (:1083) | the assembled prompt, bounded by step 3a's route | yes (in prompt) |
| 5 | `narrate` (:1370) | the answer | — |

History enters at exactly one point: `agent.py:651` reads
`working_memory.history(sid)` and passes it to `assemble_context(history=history)`
(`:840`), which renders it into the prompt (`context.py:366–378`) under this header:

> `## Conversation so far (oldest first; use ONLY to resolve references in the latest
> question, e.g. 'that', 'last year')`

So the design **knows** coreference exists and **explicitly delegates it to the model**.
The problem is that by the time the model can resolve "that", steps 2, 3a and 3b have
already committed.

---

## 2. The defect: routing and retrieval run on the unresolved question

Turn 1: *"How many customers does the Chicago branch have?"* → routes to the `customers`
schema, retrieves `customers` / `branch`, answers.

Turn 2: *"Now break that down by region."*

`retrieve(retrieval_corpus, question, ...)` (`agent.py:797`) receives the string
`"Now break that down by region."` and nothing else. Run it through §3 of the RVGD
analysis:

- BM25 tokenizes it to roughly `now break that down by region` — and `content_terms`
  would strip `now`, `that`, `by` as stopwords, except **the stopword list is not applied
  to the ranking query** (`rvgd.py:221` uses `tokenize`, not `content_terms`). So the
  query is six tokens, four of which are function words.
- The only content token that can match anything is `region`.
- `lexical_coverage` over the index vocabulary will be high (most of those words appear
  *somewhere* in a large corpus), so even the one signal we compute reads this as fine.
- On the pooled path, `schema_documents` ranking is asked to pick 3 schemas out of 69
  from `region` alone.

The entity the question is *about* — `customers`, `Chicago branch` — is present in the
turn, in working memory, and invisible to the retriever.

## 3. Why the agent cannot recover — two correct designs colliding

Normally the escape hatch is the model: it has `search_corpus` (which calls `retrieve`
with a model-authored query, `tools.py:300`) and `inspect_schema` (which licenses
tables). A competent model reading the history would call `search_corpus("customers by
region")` and recover.

It cannot, on the pooled path. `agent.py:1099–1101`:

```python
# Recorded by assemble_node; absent (or empty) on a single-schema corpus
# where routing never runs, which leaves the scope unbounded as before.
licensable_schemas=frozenset(
    state.get("base_provenance", {}).get("routed_schemas") or ()
```

and `build_agent_core`'s own comment (`agent.py:~250`):

> The routed schema set for this turn; bounds what `inspect_schema` may license
> (AUDIT S4). None = unbounded, correct for a single-schema corpus.

**The agent for this turn is hard-bounded to the schemas the router chose from the
unresolved fragment.** `inspect_schema` cannot license outside that set, and
`filter_corpus_for_retrieval(corpus, routed)` (`agent.py:716`) means `search_corpus`
searches only inside it too.

So the failure mode is:

1. Follow-up fragment mis-routes (or under-routes).
2. The agent is bounded to the wrong schema set for the whole turn.
3. Its options are: answer wrong from the wrong schema, or hit L4 `term_semantics` and be
   **refused as out-of-scope**.

**A correct follow-up gets refused, and the reason is a governance control working
exactly as designed.** AUDIT S4 bounded `inspect_schema` precisely to stop the agent
self-authorizing its way into an off-scope schema — that fix is right, and it closes the
only recovery path from a coreference mis-route. Two sound decisions, one bad interaction,
and nothing in the repo would show it.

## 4. What else runs on the fragment

- **`refuse_gate` (step 2).** Negative-example patterns are matched against the raw
  follow-up. A question class marked unanswerable will not be caught when asked as a
  pronoun-laden follow-up — the refuse gate is trivially evaded by conversational
  phrasing. (It is also never exercised end-to-end at all; see the red-team analysis.)
- **The assurance stamp.** `lexical_coverage` feeds `_weak_retrieval` →
  `UncertaintySignals` → `semantic_assurance` (`governance.py:808`). On a follow-up, high
  coverage on function words means the stamp reports **`unflagged`** — no uncertainty —
  on a turn whose retrieval was blind. The stamp is *most* confident exactly where it has
  least right to be.
- **`context_hash` / `n_*_injected`.** Per-turn retrieval provenance is recorded, so the
  evidence for all of the above is already being written to `stage_events.jsonl` on any
  multi-turn session. Nobody has looked, because no eval produces one.

## 5. Working memory: two stores, no cap, double growth

`InMemoryWorkingMemory` (`memory/store.py:36`) supports `max_turns` to bound growth.
**Both construction sites pass nothing** — `api/app.py:492` and
`api/graph_app.py:96` — so `max_turns=None`: keep every turn forever.

It is also not really a store. `_working_memory_from` (`graph_app.py:87`) **rebuilds it
from scratch every turn** out of LangGraph's own `messages` (which has an `add_messages`
reducer and is checkpointed). So the conversation is held twice: once durably by the
checkpointer, once as a per-turn projection whose docstring says "Ephemeral by design
(lost on restart)" — true of the projection, irrelevant to the actual source of truth.

Growth is therefore quadratic-ish in a long session, in two places at once:

- the rendered `## Conversation so far` block is **uncapped and verbatim**
  (`context.py:376–377`, a bare loop with no truncation), and it is rebuilt into every
  turn's prompt;
- the inner agent's own `messages` grow within the turn.

There is no summarization, no windowing, no token accounting for the history block
(`context_chars` measures the total after the fact — see the framework audit §8.6). At
turn 30 of a real session, the history block plausibly dominates the prompt, and the
per-type retrieval budgets (`top_k=8` tables etc.) that were tuned against a single-turn
prompt are competing with it for the model's attention.

## 6. The measurement hole

`arms.py:417`: *"Each question increments `n_human` and mints a fresh `run_id`."* Each
eval question is its own turn 1. The arms (`baseline`, `seeded`, `curated`,
`curated_sme`, plus oracle rungs) vary **corpus quality**; none varies **turn depth**.

Consequences for what we can claim:

- No accuracy number in this repo describes a follow-up.
- The retrieval-recall instrument we are (rightly) proud of —
  `eval/retrieval_eval.py`, table recall@k over gold SQL — takes `item.question`
  (`retrieval_eval.py:194`) and is therefore also single-turn. **The measurement that
  would catch §2 is itself blind to it.**
- The RVGD analysis's whole §3 ledger was computed against single-turn behaviour. If
  multi-turn is the dominant real pattern, the priorities in that ledger may be wrong —
  a coverage floor (B-2) matters far more when the query is a fragment.

### What an arm needs

The blocker is data: BIRD is single-turn. Three options, cheapest first.

1. **Synthetic pronoun follow-ups (recommended first).** For each BIRD item, emit a
   two-turn session: turn 1 a generic opener grounded in the gold tables, turn 2 the real
   question with its entities replaced by references ("that", "those", "it"). Gold SQL is
   unchanged, so grading and leakage handling are untouched, and the arm isolates exactly
   the §2 defect. Synthetic, and honest about it.
2. **Gold-decomposition pairs.** Split a complex gold question at a natural boundary
   (filter → grouping) into two turns, with the final gold as the turn-2 target. More
   realistic, more authoring effort, and needs a rule for which questions decompose.
3. **A real multi-turn benchmark** (SParC / CoSQL are the standard conversational
   text-to-SQL sets). Most realistic, but they sit on the Spider substrate, so adopting
   one changes databases and forfeits the obfuscation work that
   `BIRD-Data-Obfuscation` gives us. Probably not worth it.

Whichever arm lands, the comparability gate needs a new key (`turn_depth`) or a
two-turn run will compare as one configuration against a single-turn run — the same class
of hole `metrics.py`'s register was built to close.

## 7. A correction to an earlier claim

In the architecture review I described the two `n_human` derivations as *"off-by-one in
opposite directions by construction."* That is wrong, and it matters because it would
send someone chasing a bug that isn't there.

- `graph_app.py:147` counts human messages in `state["messages"]`, which **already
  includes** the current question (`add_messages` appended it before the node ran).
- `app.py:498` counts humans in `req.history`, which is **prior turns only**, and adds 1.

Both yield "human-turn count including this question." They are equivalent, and
`app.py:495–497` says so. The real (much smaller) issue stands: one fact derived twice
from two input shapes, reconciled by a comment rather than a shared function.

## 8. Fix options

The temptation is a coreference-resolution LLM call. Note what that is: **the book's
Query Understanding node, returning by the back door.** ADR 0002 deleted intent
classification along with the deterministic DAG, and that was right — but *reference
resolution* is the one piece of QU that pays for itself, because it feeds a retrieval
step that cannot see the conversation. Worth naming explicitly so the decision is made
on its merits, not by drift.

Four options, cheapest first:

| # | Option | Cost | Risk |
|---|---|---|---|
| F1 | **Retrieve on `(previous user turn + current question)`** as the query text | free, deterministic, offline-testable | crude; dilutes the query on turn-1-like follow-ups |
| F2 | **Route stickiness via the coverage floor** — when the current question's `lexical_coverage` (or content-term count) is low *and* a prior turn exists, reuse the previous turn's `routed_schemas` instead of re-routing | free, deterministic | needs a threshold; the coverage signal is uncalibrated |
| F3 | **Carry-forward licensing** — union the previous turn's licensed tables into this turn's licensable set | free | monotonic scope growth over a long session; needs decay or last-N |
| F4 | **Coreference rewrite** — one LLM call turning (history, question) into a standalone question, then route/retrieve on that | one model call per follow-up | latency + cost; a rewrite that drops a constraint is a new failure mode |

**Recommendation: F1 + F2 first.** Both are free, deterministic, and testable offline
through `retrieval_eval.py` once it accepts a session instead of a question. F2 is
especially attractive because it *uses the signal we already compute and currently ignore*
— the same `lexical_coverage` the RVGD analysis flagged as measured-but-unacted-on (B-2).
One mechanism, two problems.

Then measure whether F4 earns its call. That ordering is the repo's own
measure-before-building discipline applied to itself.

F3 needs a governance decision, not just a benchmark: carrying licenses forward
deliberately widens scope across turns, which is the thing AUDIT S4 narrowed. If it lands,
it needs to be visible in the stamp.

## 9. Worklist

| # | Item | Size | Why |
|---|---|---|---|
| 1 | Teach `retrieval_eval.py` to score a **session**, not a question | S | Nothing below is measurable without it; no LLM needed |
| 2 | Synthetic pronoun-follow-up arm (§6 option 1) + `turn_depth` in the comparability gate | M | Turns §2 from an argument into a number |
| 3 | F1 + F2 behind knobs, measured on 1–2 | S | Free, deterministic; F2 reuses the coverage signal |
| 4 | Set `max_turns` at both construction sites; cap/window the rendered history block | S | Unbounded verbatim history in every prompt |
| 5 | Make the assurance stamp honest on follow-ups — a blind-retrieval turn must not stamp `unflagged` | S | The stamp is most confident where it has least right to be (§4) |
| 6 | Decide F3, and if adopted make cross-turn licensing visible in the stamp | M | Governance decision, not a tuning knob |
| 7 | Collapse the two `n_human` derivations into one function (§7) | XS | Duplication, not a bug |
| 8 | Stop rebuilding `WorkingMemory` per turn from checkpointed `messages`, or state plainly that it is a projection (§5) | S | Two representations of one conversation |

Items 1–2 are the whole game. Everything else is cheap once a follow-up produces a
number.

## 10. What is already right

- Delegating coreference to the model **in the prompt** is a reasonable design; the bug is
  the stage ordering, not the delegation.
- `context.py:367–371` deliberately does **not** sanitize conversation turns, with a
  written reason: they are the user's own words, redacting them would silently rewrite the
  question, and the guardrails — not the prompt — stop a self-injected turn from
  executing. That is the right call and correctly reasoned.
- The clarify-thread derivation is namespaced and hashed against a real threat
  (`graph_app.py:141–148`), with an honest note that hashing is not authentication.
- `max_turns` exists and works; only the call sites neglect it.
