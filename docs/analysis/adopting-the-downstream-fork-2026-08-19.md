# Adopting from the downstream fork — what was taken, what was rebuilt, what was declined

**Date:** 2026-08-19. **Branch:** `adopt/downstream-fork`. **Source:**
[`RyanChenJung/governed-bi-utkuai`](https://github.com/RyanChenJung/governed-bi-utkuai) at
`12c3e15`, a fork of this repository. **Not an ADR** — the binding decisions it rests on are
0006, 0007, 0009, 0012, 0013 and 0014. Replace when superseded.

## Context

The fork is a strict superset: it had already merged `main` at `e2b2bb8`, and carries ~110 commits
of its own — 172 files, +33,353/−3,550. What it adds is an **admin-facing semantic-layer curation
layer plus a trust loop**: `curator/` (14 modules), four route files, 18 UI components. Its premise
is that this engine is well-governed on the backend and illegible on the front end — that an SMB
owner with no data engineer can neither judge whether an answer is trustworthy nor say that it is
wrong.

That premise is right, and it splits cleanly. **Legibility is this repository's job**: the engine
already produces `outcome`, `refused_by`, `terminal`, the attempt ledger, and hands them to a reader
as machine identifiers. **The curation write-back is not**, and `docs/enterprise-fork.md` already
says so in its own words — "no tenant model, no policy admin UI … those are the product; this is
the seam."

So this adoption takes the reader-facing half and the fixes, and declines the write path. The
sections below record the four owner decisions, the facts each one rests on (measured, not
inferred), and what shipped.

---

## Owner decisions, 2026-08-19

| Decision | Chosen | Consequence |
|---|---|---|
| Where an operator's clarification answer lands | **Semantic layer only** — it does not resume the reader's thread, which expires | ADR 0006 B9 unchanged; but the *answering* half needs a provenance gate, so only a read-only queue shipped |
| Who sets the display mode default | **Client only**, `localStorage` | Zero backend change; nothing emits a `tier` key from `src/`, so ADR 0007 §3's test is unaffected |
| Whether `business` mode sees raw identifiers | **Yes**, as the fork does — a refusal may name tables | `schema_term_guard` not needed yet; cost accepted below |
| Scope of this round | **Reader-facing UI + queue; eval instrumentation deferred** | `attribution.py` / `power.py` / `corpus/snapshot.py` wait |

### The first decision's consequence, stated first

Routing an operator's answer into the semantic layer requires a corpus write path, and this
repository has no gate on one. `serve/session.py::_visible` filters `governance.excluded` alone;
`corpus/analyst.py` gates `reliability.status is suspect`, not provenance. `ProvenanceStatus`
(`corpus/schema.py`) is **display-only** — projected by `api/browse_routes.py` and `api/routes.py`,
enforced nowhere. A `proposed` asset therefore already reaches the model's context.

Nothing in this repository writes one today, so it is not a live defect. It is a prerequisite: the
queue ships read-only, and answering is deferred behind the gate.

---

## Measured facts

### The pending queue needs no store of its own

Versions: `langgraph 1.2.11`, `langgraph-sdk 0.4.2`, `langgraph-api 0.12.3`,
`langgraph-runtime-inmem 0.32.3`.

- `ThreadStatus = Literal["idle","busy","interrupted","error"]` (`langgraph_sdk/schema.py`), and
  `status` is a first-class `threads.search` parameter (`_async/threads.py`).
- `interrupts` is a valid `ThreadSelectField`, so **no `extract` path budget is spent** on it.
  `Thread.interrupts` is `dict[task_id, list[Interrupt]]`.
- The in-memory runtime — the one `langgraph dev` uses — implements the `status` filter and
  populates `interrupts` (`langgraph_runtime_inmem/ops.py`), and a thread paused at `interrupt()`
  has `status == "interrupted"` because `checkpoint["next"]` is non-empty.

The queue is therefore one call. What makes it *complete* is asymmetric, and is the actual finding:

- a clarification that **was** answered lands in the `clarifications` channel, because
  `serve/tools.py` writes `clarifications_by_call` on the far side of `interrupt()`;
- one that was **not** — the reader closed the tab — writes nothing at all.

So the backlog is exactly the half thread state does not hold, and its only trace is the platform's
interrupt state — which ADR 0014's durable checkpointer made survive a restart. **Before ADR 0014
this reader could not have existed.** The fork built a JSONL ledger and an offline answer route
because it had neither.

`ask_user`'s interrupt payload is already `{kind, clarification_id, question, why}` — the shape ADR
0007 §6 fixed — and `clarification_id` is `f"clar-{turn_id}-{digest}"`, so the join back to the turn
needs no new field. The fork added its turn link in a later commit; here it falls out of the id.

### Where that ledger must not live

The fork keeps it at `<corpus_root>/clarifications.jsonl`. Measured on a scratch corpus:

```
before                     a15a0894be226bf7…
after adding the ledger    aa2cead7741e7f30…
after answering one        81b5f7553568b2e9…
```

`corpus_content_hash` passes no `suffixes`, so every file except VCS bookkeeping
(`corpus/identity.py::_is_tooling`) is digested. `measure/gates.py::_corpus_content_hash_gate` fails
on more than one hash inside an arm — so that placement would **void an arm every time someone
answered a question**. The fork is unaffected because its mining knob defaults off and no arm of
theirs answered one.

### The reader-facing layer already exists here

- `ui/lib/answer-delivery.ts` has `deriveDelivery` (four states, `no_sql` handled), `terminalOf`
  reading `record.execution.terminal`, and `FLAG_WHY` — a "one sentence per flag" map. It also
  states the rule: *branch on `deriveDelivery`, never on a raw field.*
- `ui/components/chat/clarification-prompt.tsx` already renders a pending clarification from
  `stream.interrupt?.value`. The gap was only ever the **cross-thread** view.
- `ui/app/settings/page.tsx` already existed, read-only.

**Trap:** `FLAG_WHY` / `uncertainty_flags` is inert — v1's producer did not survive the rewrite, so
`whyLines` returns `[]` on every live turn. Reader phrasing must not depend on it, or it becomes
another wired-and-empty field.

### Two hard constraints

1. **ADR 0007 §3** forbids the reliability tier on the answer card, pinned by
   `tests/api/test_http_contract.py::test_the_api_never_synthesizes_a_reliability_field`, which
   fails on any second producer of a `"tier"` key under `src/`. The rule is: render what the engine
   produced, never synthesise. Reader phrasing is a pure function of produced fields.
2. **`ask_user` is one of the four tools that can name an asset** (`docs/enterprise-fork.md`), and
   audit finding **A7** — nothing authenticates — is open.

---

## Accepted costs

- **The queue route has no credential.** It hands any caller every unanswered question, and by the
  third decision those may name tables. This is not wider than `/audit/turns`, which already
  discloses every thread's SQL to the same caller — but it is not narrower either. **Under a real
  `AccessPolicy` (ADR 0012) this route must apply the same withholding the tools do**, or it is a
  read path around a grant.
- **Display mode is not permission.** Client-only means a deployment cannot set a default. Both the
  module and the settings card say so; a control presented as protection would be precisely what
  `docs/enterprise-fork.md` warns against.
- **`open-work.md` §3.6a is untouched**: a clarification turn carries no `corpus_content_hash`,
  because it ends before routing. The queue makes those turns more visible without closing the hole.

---

## What shipped

**Fixes** (`20d3df8`). `SqliteConnector._connect` memoised `self._conn`, and `sqlite3` enforces
thread affinity on it — LangGraph runs each tool call on its own worker thread, so every call from
anywhere else raised `QueryError: SQLite objects created in a thread can only be used in that same
thread`. Reproduced before taking the fix; the three tests it brings fail 3/3 on the old code. It
mattered most where it was quietest: `eval/__main__.py` builds the connector bare, so a
SQLite-backed arm scored an instrument failure as a wrong answer.

That obsoleted six `connector._connect()` pre-open calls, which is where the suite's unclosed-database
`ResourceWarning` came from — open since 2026-08-18, closed by deleting them. One was in
`eval/__main__.py`, so a real CLI run leaked a connection too. Suite warnings 20 → 1.

Also: `check_file_length.py` gained a non-fatal `WARN_LIMIT = 900` tier, which immediately reported
six files within 80 lines of a build failure (`test_agent_tools_hitl.py` with five left) that the
57-file soft list could not distinguish; `check_one_implementation.py` declares `mde` a singleton,
rewritten rather than copied because the fork's rationale cited a file this repository does not have;
`.gitignore` ignores every `.env` sibling, since `.env` alone left a `.env.bak` untracked *and*
unignored; `.env.example` documents that an unset `GOVERNED_BI_EMBEDDING_MODEL` degrades routing to
lexical-only and presents as a wall of `no_schema_matched`.

**Display mode + reader phrasing** (`29068aa`). `ui/lib/display-mode.ts`, minus the fork's
`simple`/`audit` migration — no such value has been in this repository's `localStorage`, so adopting
it would ship an unreachable branch justified by another project's history. Named `DisplayMode`, not
`Tier`: `tier` already means the forbidden reliability tier and `RecordField.tier`, and a third
meaning is worse than a longer word.

`refusalSentence` in `answer-delivery.ts` translates all thirteen `refused_by` values. The sentences
are deliberately **not** the engine's own `why` text — `serve/nodes/abstain.py` writes each
abstention for whoever maintains the pipeline and names Layer 6, the character budget, the shortlist.
`tests/api/test_the_refusal_phrasing_covers_the_vocabulary.py` checks both directions against
`REFUSED_BY_TO_STAGE`, following the arrangement `provenance.ts` has with the record register — which
exists because that hand-copy rotted silently: 32 dead keys, 35 unlisted register fields, nothing
failing. A third test holds the two `CRASH_REFUSED_BY` reasons to saying "on our side", so our bug is
never described as a limitation of someone's corpus.

**The pending queue** (`29068aa`). `PendingClarifications` in `api/thread_turns.py` —
same module as `ThreadTurnLog` for one reason: `_in_process_client` must not exist twice, because
`get_client(url=None)` swallows its import failure and appends an `app=None` transport to an SDK
module global, a leak already fixed here once. `api/clarification_routes.py` serves
`GET /clarifications/pending`, read-only, with `meta.truncated` on the wire (ADR 0009 D2/D9 — a
silently short queue reads as "nobody is waiting", and the thing under-reported is a person).
`/clarifications` is its own route rather than a section of `/audit`, whose own description is "every
turn this server has served" — these are the ones it did not.

Two conventions were forced and are worth knowing. `make_app` grew a **third required** dependency
rather than a defaulted one, because its docstring demands it: *"a default would put the environment
back in the constructor, which is the thing this exists to remove."* And the factory is
`make_clarification_router`, not a second `make_router`, because `check_one_implementation.py`
refused the duplicate — the fork reached the same answer and left a half-written `KNOWN_DUPLICATES`
waiver comment behind as evidence the exemption was tried first. **The convention for a sixth router
module is `make_<surface>_router`.**

### Verification

```
1539 passed, 17 xfailed, 1 warning
ruff clean · import layering clean (125 files) · singletons 7/0 · file length OK
tsc clean · eslint 0 errors (3 pre-existing warnings)
```

In a browser: the mode control renders, switches, writes `localStorage`, and survives a reload with
no hydration warning; `/clarifications` calls `/clarifications/pending?limit=50&offset=0` and
degrades correctly with no engine attached.

**Not verified: the answer card's three modes.** That needs a real answer, so a live engine and a
paid model call. The collapse conditions are `atLeast(...)` guards checked only by `tsc`; the client
has no test runner. A live turn at each mode is the outstanding check, together with the queue's
end-to-end smoke — ask an ambiguous question, close the tab without answering, and the row must
appear. That smoke is also the first end-to-end proof that ADR 0014's checkpointer makes a pending
clarification survive a process restart.

**Known limitation:** the queue cannot filter by origin. Nothing in `src/` writes thread metadata,
so a thread carries only the platform's `{graph_id, assistant_id}` and a hand-tested conversation is
indistinguishable from a real reader's. Writing `metadata.source` at thread creation is the fix and
is not in this round.

---

## Deferred, with entry conditions

**Eval instrumentation** — `eval/attribution.py` (names *why* an answered-but-wrong turn was wrong,
as a pure function of fields already on the row), `eval/power.py` (`require_power` refuses to declare
an arm that cannot detect its own hypothesis), `corpus/snapshot.py` (restore to a known state; refuse
to delete a tree it cannot identify as a corpus). All three import only this repository's modules —
zero `curator/` dependency. Deferred because the taxonomy is one every future arm reads, and it
should not land while nobody can re-measure against it. Three real instrument defects come with it:
"could not be graded" scored as **wrong**, a CTE name counted as a base table, and
`missing_prediction` conflated with `unparseable`.

**The curation write-back** needs four things first, in order:

1. a provenance gate that is actually enforced, so `proposed` does not reach the model;
2. a write-time distinction between a rule that generalises ("exclude delisted") and a result that
   expires ("8,512") — the fork's own finding 2, where a correction became a memorised number recited
   with `terminal: no_sql` and an empty ledger;
3. the ledger somewhere other than `corpus_root` (measured above);
4. the `curator/` ↔ `serve/` layering fix — the governed-read helpers lifted below both — which
   should land upstream-side first, since it touches `serve/fetch.py`, the fork's largest seam into
   this code.

## Declined

- **`curator/` and its surfaces** — 4,000+ lines carrying an `UNLAYERED` import-cycle exemption. The
  fork's own position is not to offer it before the layering fix.
- **`register/prompts.py` renumbering** (381 lines) — would place published McNemar figures beside
  variants never measured. Their `v10` is composed and unmeasured.
- **`serve/session.py` runtime-override layering** — makes knobs mutable mid-process, so
  `knobs_resolved` stops being session identity and the quotability gates lose their meaning.
- **The `engine-toggles` / `corpus-tab-toggles` panels** — they drive two knobs
  (`enable_clarification_to_draft`, `enable_structured_percentage_check`) this repository does not
  have, so they would render empty. **The design is worth taking when a knob is ever exposed**: every
  row reports where its value came from, an environment-pinned knob is not editable and names its
  variable, and a write to one is **409** rather than accepted-and-ignored.
- **The fork's `AttemptBook.charged` fix** — `serve/agent_state.py::_chargeable` already does it, and
  selects on the row's `path` rather than on how its key is spelled.
