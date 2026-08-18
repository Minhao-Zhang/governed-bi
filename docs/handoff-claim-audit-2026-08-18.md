# Claim audit — does the product do what we say it does?

**Date:** 2026-08-18. **Method:** live engine (`gpt-5.6-luna`, real Postgres, `app_store`
corpus, 44 assets), asked real questions, read the durable turn log and the corpus on disk.
Every verdict below is from an observation, not from reading code and inferring. Where a
verdict rests on code rather than a run, it says so.

Written for handoff. The point is to separate **what a reviewer can rely on** from **what is
built but not yet true**, so nobody discovers the second category in front of a customer.

---

## The claim we make most often

Both action plans state the goal in the same words, and the phrase recurs **eight times**
across the two:

> "answer a set of the owner's real questions in plain English — **each with its assumptions
> shown** — refusing when unsure"

Verdict below: **the refusing half is real. The assumptions half is not.**

---

## What is real — verified today, by running it

| Claim | Evidence |
|---|---|
| **Refuses instead of guessing** | `Which apps are popular?` → `outcome: refused`, `refused_by: no_schema_matched`. Deterministic: `route_retrieve.py:88` refuses when no schema scores > 0. |
| **A refusal is readable, and names what it can see** | *"I couldn't find anything about that in your data. What I can see: mobile_app_market, playstore, user_reviews."* |
| **Asks when a term is genuinely ambiguous** | `What is the average rating of apps?` → clarification: *"Which app listing should I use for the average rating: the Play Store listing or the mobile app market listing?"* Two tables carry a rating field and nothing declares which is authoritative. 9.6s. |
| **An answered clarification becomes a corpus draft** | Answered one via `POST /clarifications/{id}/answer` → `converted_to_corpus: true`, corpus 43 → 44, new `clarification.app_store.c8f848aaabd20fcc.yaml` at `status: proposed`. |
| **Provenance is recorded on the folded asset** | That file carries `audit.source: live_chat`. Before 2026-08-16 (task C-0) it carried nothing, and the metrics view would have reported zero. |
| **An admin can approve from the product** | `Drafts` tab → approve → `status: certified` on disk, list 7 → 6 **without a restart** (task D's fix: that route re-reads the corpus off disk rather than the session's frozen mapping). |
| **A reader can report a wrong answer, and it becomes a rule** | Real ledger row: engine said *10,840 apps*; reader objected *"that includes decommissioned apps"*; admin corrected to *8,512, exclude delisted*; `converted_to_corpus: true`. |
| **Re-asking after a correction returns the corrected value** | Same question now returns **8,512**, not 10,840. **But read finding 2 — this is weaker than it looks.** |
| **The loop is counted** | `GET /trust-loop/metrics` over 237 real turns: 50 refusals → 2 reader entrances → 2 approved rules → 2 retrieved again. |
| **`terminal` is legible at business tier** | Code-verified (`ui/lib/answer-delivery.ts:109-127`), not run in a browser: an empty attempt ledger renders *"answered without consulting your data at all"*, a non-empty one *"answered from a definition, without running a query"*. |

---

## Finding 1 — `assumptions` is rendered and never populated

**Severity: this is the claim in the goal sentence of both plans.**

Two answered turns today, both with `assumptions: []`:

| question | terminal | SQL | assumptions |
|---|---|---|---|
| How many apps are in the mobile_app_market table? | `no_sql` | none | `[]` |
| What is the average user_rating in mobile_app_market? | `answered` | `SELECT AVG("user_rating") … LIMIT 200001` | `[]` |

The second one is the sharp case. **It averaged every row in the table** — including the
delisted apps the certified rule says to exclude — and reported `4.19` with no statement that
it had done so. That is exactly an assumption a non-technical reader could have evaluated, and
exactly what the field exists to carry.

The field is not broken. It is declared in `ui/lib/schemas.ts`, on the wire, parsed, and (task
I-1) rendered. **Nothing ever puts anything in it.** The trust-loop plan already recorded that
`stamp.py` deliberately keeps it off the durable `record` (ADR 0006 §11), so nobody has ever
observed it firing — today's two turns are the first observations, and they are empty.

**Do not describe the product as showing its assumptions.** The honest sentence today is
*"refuses when it cannot answer, and tells you whether it queried your data"*. Closing this is
prompt and tool work — `state_assumption` exists as a concept in the register — not UI work.

## Finding 2 — a certified rule became a memorised number, not a rule

The 8/16 correction taught the corpus that "how many apps" means *active* apps, 8,512,
excluding delisted. Two things follow, and both are worse than "the loop works":

**a. The count is recited, never recomputed.** Asking it today returns 8,512 with
`terminal: no_sql`, **`ledger: []`**, `generated_sql: None` — it never touched the data. If an
app is delisted tomorrow, 8,512 is wrong and nothing notices. The loop converted a one-time
human correction into a hardcoded constant that presents as a current fact: *"There are 8,512
active apps in the table."*

The mitigation is real but partial: at business tier the stamp says *"answered without
consulting your data at all"*. So the **stamp** is honest while the **answer text** is not, and
a reader who reads only the bold number is misled.

**b. The rule did not generalise.** The same "exclude delisted" logic was **not** applied to
the average — that query averaged the whole table. So the semantic layer learned the *answer*
to one question, not the *rule* behind it.

This is the most important finding for a reviewer, because it is a consequence of the loop
**working as built**, not of a bug. A durable rule about a *count* is a stale number by
tomorrow. Rules that state a filter ("exclude apps flagged delisted") generalise; rules that
state a result ("8,512") do not, and today nothing distinguishes them at write time.

## Finding 3 — semantic retrieval was switched off entirely until today

`GOVERNED_BI_EMBEDDING_MODEL` had never been set on this engine, so every facet reported
`"semantic": "not_configured"` and routing ran on lexical matching alone. Effect: whether a
question routed at all depended on whether the model happened to emit a query string that
literally matched a column name. The same question — `What is the average rating of apps?` —
answered once (132s, retrying) and then refused twice in the same process.

Fixed today by setting `GOVERNED_BI_EMBEDDING_MODEL=text-embedding-3-small` in `.env`, using
the OpenAI credential already present. It is a supported, deliberate degraded mode (the code
says so: *"Unset it to serve with lexical retrieval only"*) — it just is not a mode anyone
should demo or measure on.

**Consequence for anything measured before 2026-08-18 on this configuration: it measured a
degraded system.** Check whether the arms and the published numbers set this variable before
comparing them to anything.

## Finding 4 — the loop's core switch is off by default and does not survive a restart

`enable_clarification_to_draft` defaults to `false`, and `POST /settings/toggles` stores the
override **in-process only**. So every fresh engine start has the loop's write path disabled,
and with it off, answering a clarification records the answer and produces **no draft, with no
error**. It reads as broken when it is only switched off. Two persistence paths exist and
neither works for this knob (`governed_bi.local.toml` "is read by nothing"; only three
float/int knobs declare an env var — no bool knob has one). Tracked; interim mitigation is
`UtkuAI/demo/preflight.sh`, which sets it and prints READY / NOT READY.

## Finding 5 — two counters and one path are weaker than their names

- **"Retrieved again"** in the metrics view counts `facet_hits.facet_term` candidate hits — *was
  a retrieval candidate*, not *was delivered to the model*. The stronger measure is not
  available (`licensed` is a table allowlist; a term id can never appear in it). The response
  labels which one it is, in `retrieved.method`. Good practice; just do not read the number as
  the stronger claim.
- **Refusal → re-ask.** Certifying a term does not reliably make the originally-refused phrasing
  route. The wrong-answer path (report → correct → re-ask) is verified end to end; the refusal
  path is not.
- **A `proposed` asset already reaches the model's context.** `serve/session.py`'s `_visible`
  filters only `governance.excluded`, with no provenance check. Certification *is* gated for
  licensing (`corpus/analyst.py` is certified-only), so the two halves disagree about what
  `proposed` means. Open question, needs the upstream owner.

---

## What to say when handing this over

**Say:** it refuses rather than guessing, and says so in plain language naming what it can see;
a reader can report a wrong answer; an admin can turn that into a certified rule from the
product; the loop is counted end to end, and the count is currently 50 → 2 → 2 → 2 on real
turns.

**Do not say:** that answers show their assumptions. The field is wired and empty.

**Say with the caveat attached:** that the system learns from corrections. It does — and a
correction about a count is stored as that count, recited without re-querying, and does not
generalise to a related question. Finding 2 is the one a technical reviewer will find first.

**Read the front half of the funnel, not the back.** 2 → 2 → 2 converts perfectly and means
almost nothing at n=2. 50 → 2 is the real number: 48 refusals happened with no way for the
reader to respond, because the entrances did not exist until 2026-08-16.
