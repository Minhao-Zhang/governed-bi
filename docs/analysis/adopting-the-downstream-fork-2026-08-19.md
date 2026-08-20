# Adopting from the downstream fork — what was taken, what was rebuilt, what was declined

**Dates:** 2026-08-19, two rounds. **Branch:** `adopt/downstream-fork`. **Source:**
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

So this adoption takes the reader-facing half, the eval instrumentation and the fixes, and declines
the write path. The sections below record the seven owner decisions across two rounds, the facts each
one rests on (measured, not inferred), and what shipped.

---

## Owner decisions, 2026-08-19

| Decision | Chosen | Consequence |
|---|---|---|
| Where an operator's clarification answer lands | **Semantic layer only** — it does not resume the reader's thread, which expires | ADR 0006 B9 unchanged; but the *answering* half needs a provenance gate, so only a read-only queue shipped |
| Who sets the display mode default | **Client only**, `localStorage` | Zero backend change; nothing emits a `tier` key from `src/`, so ADR 0007 §3's test is unaffected |
| Whether `business` mode sees raw identifiers | **Yes**, as the fork does — a refusal may name tables | `schema_term_guard` not needed yet; cost accepted below |
| Scope of round one | **Reader-facing UI + queue; eval instrumentation deferred** | `attribution.py` / `power.py` / `corpus/snapshot.py` waited one round, and landed |

### Round two, same day

| Decision | Chosen | Consequence |
|---|---|---|
| Scope of round two | **Eval instrumentation + the six file-length splits** | Everything with a `curator/` dependency still declined; the two `serve/` guards deferred, not declined |
| Whether to take `assumptions` | **No** — dropped, not deferred with a plan | The field the fork's own PR calls "wired and never populated" does not enter this tree. Entry condition below |
| How it lands in history | **Semantic commits, our own messages** | Six commits, one per defect or capability; the fork's `cherry-pick -x` provenance lives in the prose instead |

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
   `tests/api/test_http_contract_answer_and_stream.py::test_the_api_never_synthesizes_a_reliability_field`
   (in `test_http_contract.py` until round two split it out), which
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

## What shipped in round one

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
1518 passed, 39 skipped, 10 xfailed, 1 warning
ruff clean · import layering clean (125 files) · singletons 7/0 · file length OK
tsc clean · eslint 0 errors (3 pre-existing warnings)
```

1,556 tests either way. The three fixes below added 13; how the rest split between passed and skipped
depends on which credentials the machine has, so a different total in the first column is not a
regression and the sum is what to compare.

In a browser: the mode control renders, switches, writes `localStorage`, and survives a reload with
no hydration warning; `/clarifications` calls `/clarifications/pending?limit=50&offset=0` and
degrades correctly with no engine attached.

### Verified against a live engine, later the same day

Both outstanding checks were run on 2026-08-19, against Bedrock `us.anthropic.claude-sonnet-5` over
the seven-table `gbi_demo_sales` schema from `tools/load_demo_schema.py`. **How to repeat it is at
the end of this section**, because getting the stack up took longer than the checks did.

**The answer card's three modes**, asserted against the DOM on one real answered turn rather than
read off a screenshot:

| | `business` | `analyst` | `engineer` |
|---|---|---|---|
| answer, narration | yes | yes | yes |
| outcome badge, `n passed governance` | — | yes | yes |
| `ledger:` terminal | — | — | yes |
| SQL panel | — | — | yes |
| Provenance drawer | — | — | yes |

The refusal path came free. "What was our profit margin by sales channel last quarter?" ended
`no_sql`, and in `business` the card carried `refusalSentence`'s one line and **no engine
identifier**. I checked explicitly for the absence of both `no_sql` and `ledger:`, since printing
either to a reader is the thing this mode exists to prevent. `engineer` showed both.

**The queue, end to end, across a real restart.** An ambiguous question ("Who are our top
performers?") raised `ask_user`; the tab was abandoned; the row appeared at `/clarifications`. Then
the API process was killed, with nothing left listening on 2024, and a fresh one started. The
queue still held it with the same `clarification_id`, the prompt **re-mounted in the new process**
from checkpointed interrupt state, and answering it resumed the turn to a correct answer, after
which the row left the backlog. `meta.truncated` flips to `true` at `limit=1`, and
`clarification_id` carries the `turn_id` as designed.

This is the observation `open-work.md` §4.4 asked for and `api/routes.py` says at the line has never
been made: *"a clarification answered after a restart has not been watched end to end"*. That
section warns about a specific way it could fail under `langgraph dev`, a SQLite checkpoint beside a
`.langgraph_ops.pckl` thread index on a ten-second flush, with the two halves disagreeing. It did not
happen. One hand-run observation is not a guarantee, which is why the procedure is written down.

**Three defects surfaced while running it**, each fixed in its own commit with a test:

1. `serve/__main__.py`'s `--model` defaulted to `gpt-4o-mini` and overrode `GOVERNED_BI_MODEL`, so
   under this repository's own `GOVERNED_BI_PROVIDER=bedrock` the documented one-turn CLI sent an
   OpenAI id to Bedrock and reported `outcome: crashed` naming nothing. The variable's name moved to
   `model/provider.py::SURFACE_MODEL_VARS`, beside the provider variables; a conformance test now
   refuses a model id in any `default=`.
2. `chat/serve-progress.tsx` was not a display-mode consumer, so the live trace rendered in every
   mode, physical names included, and then *vanished* when the turn completed, the card gating the
   same timeline on `analyst`. Gating it exposed a second bug underneath: the caller passed
   `isRunning={isRunning || awaitingClarification}`, so `business` sat spinning "Working…" while the
   engine was doing nothing but waiting on the reader.
3. `/audit/turns/{id}/trace` showed no clarification at all, so this very turn, whose SQL a person
   selected, looked like one the engine chose alone. `ThreadTurnLog.clarifications_of` projects the
   channel now, which closed the `clarifications` entry in `open-work.md` §3.10's table.

The first two are the same shape as the fork's own findings and the reason for running the thing:
neither was reachable by reading the code, and both sat on the path a reader takes.

### Repeating it

The committed `.env` does not work unmodified on a machine where 5432 is already taken, and nothing
in `docs/usage.md` said so. It does now, under "A stack that will actually answer". The short version
is a Postgres of this repository's own on a free port, `tools/load_demo_schema.py` to fill it,
`GOVERNED_BI_SCHEMA` pointed at it with `GOVERNED_BI_CORPUS_DIR` unset so the corpus seeds from the
live schema, and `uv sync --extra bedrock` because the provider here is Bedrock. The corpus in
`../BIRD-corpus` is semantic-layer YAML with no data behind it, so it cannot answer a question on
its own.

**Known limitation:** the queue cannot filter by origin. Nothing in `src/` writes thread metadata,
so a thread carries only the platform's `{graph_id, assistant_id}` and a hand-tested conversation is
indistinguishable from a real reader's. Writing `metadata.source` at thread creation is the fix and
is not in this round.

---

## What shipped in round two, the same day

Six commits, in this order. No `curator/` dependency anywhere in them, and nothing here needs a
database, a model or a corpus to verify.

**The six file-length splits** (`77d5f9f`). The WARN tier taken in `20d3df8` named six files within
80 lines of a build failure, and a warning nobody acts on trains people to read past it. The fork had
already split all six, so the seams are theirs; **the content is ours wherever the two trees
disagree**, and it disagreed in four places that would each have shipped a false claim:

- `test_register_closure.py`'s ratchet asserts *five* declared-but-unconsumed findings and names
  ours. `clarifications` is consumed here by `/audit/turns/{id}/trace` and there by `mine_corpus`;
  their file says six.
- `test_http_contract.py` asserts `why` is non-empty, because this tree still substitutes a default
  when the model omits one. They deleted that substitution and weakened the assertion to match.
  Their `ask_user` also takes a `basis` argument that does not exist here.
- `_Pending` stays behind: it is `make_app`'s third dependency, which their tree does not have, and
  three tests that did not move construct it.
- the outstanding-clarification latch tests stay in `test_agent_tools_hitl.py`. They are a separate
  module over there, so their split assumed a smaller file than ours.

The one split that is not test-only is `eval/harness.py` → **`eval/projection.py`**: `project_turn`
and the eleven helpers it calls are pure row-shaping, and what stays is orchestration.
`check_declared_is_consumed.py`'s R1 asserted every declared record field is named in *the*
projector, singular — `ARTIFACT_PROJECTOR` is a two-element frozenset now, because `run_id`,
`turn_id`, `thread_id` and `attempt_id` are written by the orchestration that stayed.
`check_file_length` now reports **no file approaching the cap at all**.

**`failure_cause`** (`aaf4741`). `eval/attribution.py` — nine named causes, first-match-wins, a pure
function of a row that is already graded. Three instrument defects come fixed: "could not be graded"
scored as **wrong**, a CTE reference counted as a base table (`exp.Table` in sqlglot's AST either
way), and `missing_prediction` conflated with `unparseable`. Its own field and not `error_type`,
which the register declares as an exception *class*; the fork wrote taxonomy labels there first and
measured the crashed count going 0 → 78. `ui/lib/provenance.ts` gains the key in the same commit
because `test_provenance_groups_match_the_register.py` requires that list to partition the register.

**`require_power`** (`4be7f37`). Refuses to declare an arm hypothesising an effect smaller than its
sample can resolve. It holds the *gate* and not the formula: `measure/stats.mde` is the singleton and
this calls it. Their first version restated the same arithmetic as `minimum_detectable_effect` with
its own z-constants, which is the duplicate `20d3df8`'s singleton entry was added for — this is the
other half of that observation, the duplicate itself deleted. `discordant` must be an `int`: a rate
passed as a count computed 0.0115 against a true 0.0956 and approved the exact arm shape the gate
exists to refuse. **Nothing calls it yet in either tree**, and `ArmSpec` carries no hypothesised
effect for anything to enforce it against.

**`corpus/snapshot.py`** (`991ab76`). `corpus_content_hash` detects that a corpus changed; it cannot
say what the tree was or undo it. Taken before its caller — nothing here writes to a corpus during a
run — because all three ways it went wrong are already guarded: it deleted a directory holding one
`IMPORTANT.txt` and no corpus (a hash succeeds on any directory, so hashing is not identification),
it accepted a snapshot nested inside the tree it later deletes, and delete-then-copy left a window
with the corpus in neither place.

**`outcome_rates`** (`a10fad4`). `correct / clarified / refused` as three rates over one denominator.
`outcome` already distinguished them; nothing stored which non-answered reason a row was as something
`Population.rate()` can aggregate, so a run reported "44/50 correct" and could not tell "asked a
person a question" from "declined to answer".

**The concurrent writer** (`613aa0e`). `_run_concurrently` collected with `pool.map`, which yields in
*input* order — one hung provider request holds every finished row behind it and the crash-safe
writer goes quiet, so a run making progress looks dead. `as_completed` over submitted futures; the
returned list stays ordered because callers index it.

**One thing was fixed on the way in, in the fork's own test.**
`test_a_run_concurrently_crash_never_reaches_the_classifier` called `connector._connect()` to
pre-open a connection — the pattern `20d3df8` deleted six of. With it in, suite warnings go 1 → 2.

**And the prose was rewritten.** These modules cite figures from experiments this repository does not
have (`008`, `009`) and name `curator/`, which it does not have either. Every number is kept and
every one now names the fork as its source, because each is the argument for a specific check or a
specific ordering, and a rule whose reason has been deleted is a rule the next reader deletes.

### Verification, round two

```
1557 passed, 39 skipped, 10 xfailed, 1 warning        (1518 before; +39 tests, no test deleted)
ruff clean · import layering clean (129 files) · singletons 7/0 · file length OK, nothing near the cap
check_citations · check_measurement_locality · check_no_benchmark_discriminators clean
check_declared_is_consumed: the same 5 findings, over 42 declared record fields (was 41)
tools/mutate.py --list still enumerates all 70 mutations in their original order
tsc clean · eslint 0 errors (3 pre-existing warnings)
```

The splits are behaviour-neutral by construction and were measured that way: 1518 either side of
`77d5f9f`, because that commit adds and removes no test. The +39 all arrive with the five capability
commits after it.

---

## What shipped in round three, 2026-08-20

**Nothing new appeared upstream.** The fork's PR is `12c3e15` plus one rename commit
(`c1e3bf0`, *UtkuAI → DetentAI*) — `git log 12c3e15..detentai-fork` is one line. So this round
took four things from the deferred list above, and one thing from the fork's *prose* that is
worth more than any of its code.

Three parallel agents, disjoint file sets, so the three landed independently and were verified
together at the end rather than one at a time.

**The two `serve/` guards** (deferred above, now taken). `serve/structured_check.py` and
`serve/schema_term_guard.py`, hand-ported, with two decisions made here rather than copied:

- `enable_structured_percentage_check` is registered `Role.comparability` with default
  **`True`**, and `POST /settings/toggles` is **not** ported. The fork's own handoff records that
  its override is stored in-process only, with no bool env path — so every fresh server start ran
  the check off, and a default-off knob with no persistence is a feature nobody ever runs. The
  cost is stated in the knob's own description: `knobs_comparable` keeps absent and `None` apart,
  so an arm measured before this key and one measured after cannot be compared. That price was
  already paid this week by `llm_max_output_tokens`; it is written down so the next reader can see
  it cost something at all.
- The schema guard is **ungated**, and it is a **retry**. `find_schema_leak` sits above
  `pending_clarification.append` and above `interrupt()`, returning through `_reply` — the same
  shape as the one-outstanding refusal directly above it — so the `tool_use` gets its
  `ToolMessage` on the same pass, the latch is never taken, and the resume path is untouched. That
  ordering is what makes retry affordable: a thread carrying a dangling `tool_use` is permanently
  unreplayable on Bedrock. Recording instead would have to `interrupt()` first, which means the
  leaked prose has already reached the reader the guard exists to protect, and the verdict would
  then need a home — a new field on `clarifications_by_call` is a state-channel plus record-field
  plus projector change, and a verdict nothing reads is the "declared, no consumer" defect
  `check_declared_is_consumed` exists to count.
- One divergence found by running it: `percentage_scale_suffix` returns `""` on a falsy `sql`
  where the fork returns the hint. `None` reaches it only on a governance refusal or a pre-verdict
  crash, so un-diverged the port appends engine advice to the text of a *governance verdict* —
  measured, `'run_query refused: … does not license'` followed by advice to multiply by 100.

**`ask_user` has no per-tool cap.** `_CapEndsTheTurn` is constructed `tool_name="run_query"` only,
so the retry above is bounded by `agent_recursion_limit` and nothing nearer. The one-outstanding
refusal it copies has the same property, so this is a pre-existing shape rather than a new defect
— but the guard adds a second path to it. Not fixed; recorded.

**A tool docstring is agent input that enters no identity hash.** Six lines were added to
`ask_user`'s docstring, which is the model-facing tool description. `prompt_set_hash` digests
`PROMPT_REGISTRY` and nothing digests tool descriptions, so that edit changes what the agent reads
and no field records it. Pre-existing, not introduced here, and the reason it is written down: it
is the same shape as the defect this repository has fixed twice already in other fields.

**`terminalLabel` and the catalog glimpse** (`ui/lib/answer-delivery.ts`, `answer-card.tsx`). At
`business` the `terminal` token reached a reader only as a raw `ledger:` string at `engineer`, so
an answer that queried the database and one recited out of a corpus definition looked identical.
`terminalLabel` translates all six declared terminals — the fork translates four, so a `crashed`
turn fell through to the raw word — and splits `no_sql` on whether the attempt ledger is empty,
which is the fork's own 2026-08-16 finding.

**This is preventive and says so.** Twelve answered turns measured 2026-08-19/20 against the
facilities corpus all recorded `attempts=1` with `reason_code=passed`: zero recitation observed.
What makes it worth taking now is that the served corpus carries hard figures in its prose
(`177,714`, `$83,521,791`, `613,685`), so recitation-without-query is a live risk rather than a
hypothetical.

`refused` is deliberately **excluded** from the translation, and that is correctness rather than
taste: `stamp.py::_execution` records `no_sql` "whether it was guard-blocked, declined or
stubbed", so a guard-blocked refusal carries `terminal: no_sql` with an empty ledger and
`NO_SQL_LABEL.untouched` would describe a refusal as an answer.

The glimpse went from the fork's one refusal reason to **four** — `no_schema_matched`,
`nothing_licensed`, `empty_context`, `guard` — on the rule that a glimpse belongs on a refusal
whose *meaning is a claim about coverage*, because then the table list is either orientation or a
visible contradiction. Nine reasons are excluded with a stated reason each; `negative_example` is
excluded because a curator's deliberate "do not answer this from this data" is undercut by a
catalog. `guard` is the measured case: 2026-08-19, two SOW questions refused at `Stage.guard` in
6.6–6.7 s on ~191 tokens, **one of them documented-answerable** against a 67,040-row table. The
fork additionally gates the glimpse on `text === null`, which `serve/nodes/guard.py` makes always
false — copying that gate would have excluded the one case this port exists for.

**`ui/` has no test runner at all** — no vitest, no jest, no `test` script — and the two existing
`ui/scripts/check-*.ts` files are not run by CI, by an explicit comment in the workflow. The new
`check-answer-delivery.ts` (35 assertions) follows that convention and inherits that gap.

**Dataset identity** — `docs/analysis/dataset-identity-2026-08-20.md`, and the one thing here that
came from the fork's prose rather than its code. `arms.toml` pinned the corpus twice and the
question set not at all, so a rerun on a replaced dataset produced the same *n*, a different
population, and **passed every quotability gate**. `ArmProfile.question_subset` is now mandatory,
reconciled out of `knobs_resolved` (where the knob actually is — the opposite of the corpus rule,
which is the point), and the pre-flight supplies it before anything is paid for or destroyed.

**And the fork's reconstruction was verified rather than attributed, because the artifacts are on
this machine.** The fork could not check its own conclusion — `runs/` is gitignored and theirs was
elsewhere — and this document's own brief repeated that. It is wrong here: `runs/eval/proxy_v3_fold_*`,
`proxy_v4_*` and `proxy_v5_*` all exist, and each carries **exactly** the 1,351 question ids of
`BIRD-Data-Obfuscation@22fe2a6`, set-equal, 0 extra and 0 missing, checked per arm. `1351:423a3f4b65fb`
recomputes from that commit through `scope_identity` and equals what `arms.toml` now declares. So
the three published arms carry a **measured** dataset identity, not a reconstructed one.

Nine test fixtures broke on the new mandatory field. Every one of them was inventing an
unreconcilable profile — which is where the *last* one came from — so they declare a question set
now, like the shipped file does.

### Verification, round three

```
1646 passed, 17 xfailed, 1 warning        (1610 before the three agents; +36 tests, none deleted)
ruff clean · import layering clean (131 files) · singletons 7/0 · file length OK
check_declared_is_consumed: the same 5 findings, over 60 knobs / 42 record fields / 41 channels
check_citations · check_measurement_locality · check_no_benchmark_discriminators clean
tsc clean · eslint 0 errors (the same 3 pre-existing warnings) · production build clean
```

**Round two's `1557 passed, 39 skipped` does not reproduce here, and the reason is in that
section already:** the skips are credential-gated and this machine has the credentials, and
`9a7a20c` landed tests after that record was written. The sum is what compares, as it says.

Two things this round did **not** do. `tools/mutation_catalogue_data_2.py` gained no entry for the
new `question_subset` branch (worth adding: mutate it to read the top level unconditionally).
And `tests/conformance/test_arm_profiles_are_declared.py` is now 429 lines, over the 400 **soft**
cap — not fatal, and 69 files are already there.

## Deferred, with entry conditions

**Eval instrumentation is no longer deferred** — it shipped in round two, above. It was held for one
round because the taxonomy is one every future arm reads and it should not land while nobody can
re-measure against it; what changed that is not a new measurement but the recognition that the entry
condition was backwards. Two of the three modules are called by nothing yet in either tree
(`require_power`, `snapshot`), and the point of both is to be callable *before* the run that wants
them.

**The two `serve/` guards are no longer deferred** — both shipped in round three above. The
estimate held: the hand-port was the whole cost, and it was smaller here than the fork's own
figure suggested, because our `ask_user` takes no `basis`/`choices` and so has only two strings to
check. The interesting half was the one predicted: the third owner decision above says a *refusal*
may name a table, and `ask_user`'s question is a different surface — prose written for a reader,
not a verdict — so the guard is scoped to `ask_user` alone and says so in its own docstring.

**A turn-level `reliability` caveat** — 40 lines in `serve/nodes/stamp.py` reusing
`corpus/schema.py`'s `Reliability`/`ReliabilityStatus` at the turn level, so a turn whose
clarification was *deferred* rather than answered says the answer rests on the agent's own guess.
Scoped to the turn, correctly: `state["clarifications"]` accumulates across the thread, so an unscoped
read flags every later turn. Deferred because it arrives in the same commit as
`_log_live_clarification`, which writes `<corpus_root>/clarifications.jsonl` — the placement measured
above to void an arm — and because nothing here sets `deferred` on a clarification yet. Taking it means
splitting that commit in half.

**`tools/dump_openapi.py`** (91 lines) — regenerates `docs/openapi.json`, which is hand-maintained
here. Undecided rather than deferred: taking it means accepting whatever it generates from our route
set as the file's new contents, which is a diff nobody has read yet.

**The curation write-back** needs four things first, in order:

1. a provenance gate that is actually enforced, so `proposed` does not reach the model. **The fork
   has one** — 18 lines in `corpus/analyst.py::for_analyst`, dropping any asset whose
   `audit.provenance.status` is not `certified`, placed there because it is the one function every
   retrieval and authorisation caller routes through. An asset with no `audit` at all stays visible,
   which is what keeps every asset this project has ever shipped from vanishing. Taking that alone
   would be a gate with nothing to gate;
2. a write-time distinction between a rule that generalises ("exclude delisted") and a result that
   expires ("8,512") — the fork's own finding 2, where a correction became a memorised number recited
   with `terminal: no_sql` and an empty ledger;
3. the ledger somewhere other than `corpus_root` (measured above);
4. the `curator/` ↔ `serve/` layering fix — the governed-read helpers lifted below both — which
   should land upstream-side first, since it touches `serve/fetch.py`, the fork's largest seam into
   this code.

## Declined

- **`assumptions`** — the `state_assumption` tool, the `assumptions_by_call` channel, and the
  unconditional `assumptions` field on the answer. **Dropped, on the owner's call, not deferred with
  a plan.** The fork's own PR leads with it: wired, on the wire, parsed, rendered, and nothing fills
  it — two answered turns measured 2026-08-18, both `[]`. The sharp case averaged every row in a
  table including rows a certified rule excludes, and reported the number with no statement that it
  had. The only thing that would fill it is a prompt naming the tool, and the variants that do are in
  the renumbering declined below. This repository already carries one wired-and-empty field
  (`uncertainty_flags`, whose producer did not survive the rewrite), and the cost of a second is not
  the code: it is that a reader who sees "no assumptions stated" cannot tell it from "the field does
  not work". **Entry condition:** a producer lands first — a prompt variant of ours that names the
  tool, measured to be non-empty on a real turn — and the field follows it, not the other way round.

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
