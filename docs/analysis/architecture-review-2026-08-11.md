# Architecture review, 2026-08-11 — deepening candidates

Tree reviewed: `506ad9b` (the commit that merged `ui/` into this repository). **Line numbers are
anchored to that commit and will drift**; the identifiers quoted alongside them are the durable part.

This is not a measurement. Nothing here is graded against gold, and no number below comes from a run
— the figures are line counts, key counts and call-site counts, all recomputable by reading the tree.

## Method

Three parallel readers over the hot spots of the last three weeks of history — `serve/` + `register/`,
`eval/` + `tools/` + `measure/`, and `api/` + `model/` + the new `ui/` seam. Each was told to look for
friction rather than to run a checklist, and to apply the deletion test to anything it called shallow.

**A correction to the naive hot-spot list.** Ranking `git log --name-only` by touch count puts
`src/governed_bi/config.py`, `api/app.py`, `api/stack.py` and `eval/run_datalake.py` at the top. All
four were deleted in `2347ae3` ("Delete v1: 87,812 lines"). The churn moved with the code, not away
from it, so the review followed it to the successors: `tools/run_datalake_eval.py`,
`register/knobs.py`, `api/graph_app.py`.

**Status column**, in the sense [the 2026-08-10 audit](audit-2026-08-10.md) uses it:

- **V** — re-checked here by grep against the tree, not taken on the reader's word.
- **R** — reported with a `file:line` citation and a plausible mechanism; not independently re-run.

Strength (`Strong` / `Worth exploring`) is a judgement about whether the deepening is worth doing.
It is independent of status: a `Strong · R` candidate is one whose friction is likely real and whose
payoff is large, but whose specific citations have not been re-verified.

## Vocabulary

Architecture terms are used in one fixed sense throughout, and are not interchangeable with
"component", "service", "API", "boundary", "layer" or "wrapper":

| Term | Sense used here |
|---|---|
| **module** | anything with an interface and an implementation — a function, a file, a package |
| **interface** | everything a caller must know: the signature, plus invariants, ordering constraints, error modes, required configuration |
| **implementation** | what is inside the module |
| **depth** | behaviour a caller gets per unit of interface learned; **deep** = small interface over a lot of behaviour, **shallow** = interface nearly as wide as the implementation |
| **seam** | the place where behaviour can be altered without editing in that place |
| **adapter** | a concrete thing satisfying an interface at a seam |
| **leverage** | what callers gain from depth |
| **locality** | what maintainers gain from depth: change and verification concentrate in one module |

Domain terms are [the glossary's](../glossary.md).

## The candidates

| # | Candidate | Strength | Status |
|---|---|---|---|
| [C1](#c1--let-the-retrieval-channel-own-its-own-merge) | Let the retrieval channel own its own merge | Strong | **V** |
| [C2](#c2--split-the-datalake-driver-into-plan--execute--report) | Split the datalake driver into plan / execute / report | Strong | **V** |
| [C3](#c3--wire-the-cross-arm-gates-to-a-driver-that-exists) | Wire the cross-arm gates to a driver that exists | Strong | **V** |
| [C4](#c4--one-turn-row-not-three-producers-of-an-undeclared-dict) | One turn row, not three producers of an undeclared dict | Strong | R |
| [C5](#c5--give-the-served-topology-a-constructor) | Give the served topology a constructor | Strong | R |
| [C6](#c6--one-turn-entry-not-one-per-transport) | One turn entry, not one per transport | Strong | R |
| [C7](#c7--one-knob-resolver-not-six-precedences) | One knob resolver, not six precedences | Strong | **V** |
| [C8](#c8--generate-the-wire-contract-from-one-declaration) | Generate the wire contract from one declaration | Strong | **V** |
| [C9](#c9--let-wrap_node-answer-is-the-turn-over) | Let `wrap_node` answer "is the turn over?" | Strong | R |
| [C10](#c10--one-governed-tool-adapter-not-three-plus-a-copy) | One governed-tool adapter, not three plus a copy | Strong | R |

---

### C1 — Let the retrieval channel own its own merge

**Strong · V.** `serve/nodes/route_retrieve.py:467-481, :157, :203` · `serve/nodes/pass_two.py:493-495`
· `serve/state.py:98-105` · `serve/delivery.py:78-92` · `register/citations.py:335`

**Problem.** `_copy_retrieved` rebuilds `retrieved` from a key list it maintains itself — exactly the
six keys `RetrievalResult` declares. `pass_two` writes two more onto the same dict (`budget_dropped`,
`budget_best_dropped_score`), and `resolve` destroys both one super-step later, on every turn that
hits the cap. Verified: those two keys have **no reader anywhere in `src/`**. `register/citations.py:335`
states the requirement they were added for — that a budget cap discarding a gold table must not read
as retrieval never having found it.

```
before   pass_two ──dict, 8 keys──▶ route ──▶ resolve ──▶ connect ──▶ stamp
                                                 │
                                     _copy_retrieved rebuilds the 6 declared keys
                                     budget_dropped + budget_best_dropped_score die here

after    pass_two ──Δ──▶ route ──Δ──▶ resolve ──Δ──▶ connect ──Δ──▶ stamp
         a reducer on `retrieved` merges by key; no node enumerates another node's keys
```

**Solution.** Give `retrieved`, `licensed` and `delivery` reducers — or make `RetrievalResult` a frozen
value with `with_pulled_in()` / `restricted_to()` — so downstream nodes write deltas instead of
read-modify-write against a hand-kept list.

**Wins.** Locality: one merge rule, not four. A new key survives a node that never heard of it.
`_copy_retrieved`'s key list is deleted. The witness becomes assertable at `stamp` — today
`tests/retrieve/test_budget_witness.py` can only prove `apply_budgets` produced it.

**Same failure, one channel over.** `serve/delivery.py:78-88` carries a comment about
`DeliveryTracker.merge_into` rebuilding a four-key dict and destroying `assemble`'s `evicted` "on every
turn that had one". That one was fixed by hand. This one is still live.

> **Built, 2026-08-11.** The reducer, on both channels: `state.merge_delta` merges a mapping channel
> by top-level key, `retrieved` and `delivery` carry it, and `resolve` / `connect` / `merge_into` write
> deltas. `_copy_retrieved` and the hand-carried `evicted` are deleted. `licensed` deliberately did
> **not** get one — `connect` *narrows* that set when a component cannot be joined, and a union
> reducer would re-license a table the node had just refused.
> `tests/serve/test_state_channels.py::test_the_budget_witness_reaches_stamp` spies on the real
> `stamp` and asserts the witness arrives; mutation-verified by removing the annotation.
>
> **Finished 2026-08-12.** The reducer moved the two keys one super-step and no further: `stamp`
> projects a named list off `retrieved`, and neither budget key was on it or in
> `register/record.py`, so the witness was live in state and absent from every turn record, trace
> page and gate — "no reader anywhere in `src/`" was still true of the artifact after it stopped
> being true of the channel. Both are register rows now (`Tier.decision`,
> `Absence.not_applicable`, owner `route`) and both are projected.
> `tests/serve/test_state_channels.py` asserts the capped path carries them and the uncapped path
> carries `null` rather than omitting them.

---

### C2 — Split the datalake driver into plan / execute / report

**Strong · V.** `tools/run_datalake_eval.py:46-470` (`main`, 425 lines) · `:265-311` · `:354-372` ·
`:381-407` · `:471-557` · `:705-855` · `eval/datalake.py:288-289`

**Problem.** The most-churned module in the tree is one function. Its interface is 22 flags, an
artifact filename composed from six of them, and four ordering constraints stated only in comments:

1. `dataset_qid_lists` before `attach_gold_fingerprints`, or the order-sensitive questions grade
   against an order-insensitive digest.
2. `--replay-routing` attached before the `--top-n` override.
3. Knob overrides composed into **one** dict — two blocks each writing `knobs_resolved` means the
   second drops the first, a defect `Session.turn` already caused once.
4. `session.fatal_problems` checked after `from_corpus_dir` and before anything spends money.

Only two functions in the file are pure and tested — `resume_identity_problem` and `_refusal_layers` —
and both were extracted *after* the incident they encode. The other ~570 lines have one test, that the
file imports and has a `main`.

```
before   main()   interface: 22 flags + a filename composed from 6 of them
                             + 4 ordering constraints, in comments
                  implementation: 425 lines, plus _report 151, _build_models 87

after    main()                          argparse + three calls, ~30 lines
           ├── plan(args) -> ArmPlan      pure: tag, out_path, questions,
           │                              knob overrides, arm name
           ├── execute(plan) -> rows      all the I/O
           └── report(plan, rows)         returns a Report instead of printing
```

**Solution.** Three modules with a value at the seam. Every ordering constraint becomes a construction
order inside `plan`, where it can be asserted on.

**Wins.** `plan` is testable with no database, model or corpus. The tag round-trips against `--resume`
instead of needing the 20-line sibling-scan at `:292-311`. Locality: knob composition happens once.

**Smallest symptom, verified.** `load_questions` computes `skipped_uncovered` — questions dropped
because the corpus does not cover their schema — and returns it by writing
`kept[0]["_skipped_uncovered"]` (`eval/datalake.py:289`). Its only reader in the tree pops it and drops
it (`tools/run_datalake_eval.py:258`). The docstring calls the number load-bearing; nobody prints it.

---

### C3 — Wire the cross-arm gates to a driver that exists

**Strong · V.** `eval/report.py:73-76, :312-345, :348-398, :401-414` · `eval/__main__.py:41-58` ·
`tools/run_datalake_eval.py:819-822` · `measure/gates.py:372-374` ·
`register/arms.toml` (at the repo root when this review was taken; moved into the register on
2026-08-11)

**Problem.** Three arm vocabularies that cannot join. `comparison_quotable` is reachable only through
`summarise`, whose only caller is the SQLite driver in `eval/__main__.py`, which builds arms named
`oracle` / `stub` / `scripted`. `arms.toml` declares `v3_fold` / `v4` / `v5`. `_declared_treatment`
turns an unknown name into `frozenset()` via `try/except KeyError`, and `knobs_comparable` turns an
empty treatment into `cannot_evaluate` — so the one wired path returns not-quotable on every input it
can be given. Meanwhile the live driver names its arm `live_{model}`, a third vocabulary, and reaches
only the single-arm half.

```
before   run_datalake_eval  ──arm "live_{model}"──▶ report.evaluate_arm ──▶ gates.evaluate
         eval/__main__      ──arms oracle/stub/scripted──▶ summarise
                                                              └──▶ comparison_quotable
                                                                       └──▶ knobs_comparable
         arms.toml: v3_fold / v4 / v5   ──── no name joins ────▶ cannot_evaluate

after    driver --arm v4 ──┐
         driver --arm v5 ──┴──▶ report.compare(a, b) ──▶ quotable verdict
         driver --arm v4 ─────▶ gates.quotable            single-arm, one home
```

**Solution.** Make `--arm` an input to the driver so the artifact carries the name `arm_profiles` is
keyed on, and add one `compare(a, b)` entry point. There is no cross-arm driver at all today.

**Wins.** The D9 treatment gate gets a caller. The tested entry point and the shipped one become the
same function — `gates.quotable` is exercised only by `tests/measure/test_measurement_semantics.py`
while the driver calls `report.evaluate_arm`.

**Deletion test, verified.** `report.evaluate_arm:76` and `gates.evaluate:374` have the same body.
Deleting the former makes complexity vanish; it does not reappear at the caller. Note that
`tools/check_one_implementation.py` declares `mcnemar` a singleton precisely because "v1 had two
McNemars" — it matches on names, so one body under two names passes it.

---

### C4 — One turn row, not three producers of an undeclared dict

**Strong · R.** `eval/harness.py:558-808` (`project_turn`, ~45 keys) · `:337-356` (crash literal,
14 keys) · `eval/oracle.py:121-157` (16 keys) · `measure/gates.py:86-93, :194-203, :241, :260-268` ·
`eval/datalake.py:458-463`

**Problem.** The measurement row has no declared shape. A crashed turn structurally cannot carry
`context_hash` (nothing writes `delivery`) or `corpus_content_hash` (`record` is `{}`), so one crash
flips three gates for reasons unrelated to the crash:

| gate | result on a crashed row | what it reports |
|---|---|---|
| `_context_hash_gate` | `failed` | "the treatment this arm delivered cannot be identified" |
| `_corpus_content_hash_gate` | `failed` | "which corpus this arm served is not answerable" |
| `guardrail_errors` | `cannot_evaluate` | field absent |
| `datalake.table_coverage` | `KeyError` | no `licensed` key |

One field of this class was already patched by hand: the crash literal adds `knobs_resolved` and
`db_id` with a comment saying that omitting them would make one crash turn the whole arm's knobs gate
`cannot_evaluate`. It stopped there. Complexity reappearing across callers is the signature of a module
that should exist.

```
before   project_turn    251 lines, ~45 keys ──┐
         crash literal    20 lines,  14 keys ──┼──▶ undeclared row dict ──▶ 7 gates + datalake
         oracle._row      37 lines,  16 keys ──┘

after    TurnRow.from_state / .crashed / .oracle     one key set
                 .reached_stamp()                    one declaration of stage-conditional fields
                        └──▶ gates ask the row, instead of each restricting its own population
```

**Solution.** A `TurnRow` module owning the row's shape, with one statement of which fields exist only
past `stamp`.

**Wins.** One property test — every producer emits the same key set — replaces the hand-maintained
comments. An arm with one crashed row can be asserted to fail only the `outcome` gate.

---

### C5 — Give the served topology a constructor

**Strong · R.** `api/graph_app.py:59, :62-140, :251-278, :281-299, :332, :350-352` ·
`serve/graph.py:140, :154-158` · `api/routes.py:31, :77-103` · `api/browse_routes.py:29-33` ·
`api/trace_store.py:27`

**Problem.** The topology that serves users is assembled from process globals, some of them eagerly at
import. Nothing accepts a `Session`; five modules reach for one. The consequence is that no test can
construct the served graph: `make_graph` is verified by splitting its own source string and asserting
`"trust("` appears in it; `_accept_node` is never executed by any test; every graph test uses
`compile_graph()`, the no-`accept` variant, which is a different topology with different input and
output schemas.

```
before   langgraph.json ─▶ graph_app.make_graph ─▶ session_from_environment ─▶ _SESSION (global)
                                  └─▶ _accept_node   embeds, counts turns, mints ids
         routes.app (module scope) ─▶ routes._session ─▶ session_from_environment
         browse_routes ─(imports routes at call time, to dodge a cycle)─▶ routes._session
         tests ─▶ compile_graph()      no accept, no record, full ServeState in and out

after    langgraph.json ─▶ graph_app (env adapter) ──┐
         tests (fake Session) ───────────────────────┴─▶ make_app(session, graph, turn_log)
                                                            ├─▶ serve/accept.accept_node(session)
                                                            └─▶ browse_routes(session)
```

**Solution.** `make_app(session, graph, turn_log)` in `api/routes.py`, and `accept_node` moved into
`serve/` taking its session as a parameter. `session_from_environment` shrinks to the adapter the
process entry calls. Optionally make the schema switch explicit (`build_graph(surface=...)`) so one
optional argument stops meaning four things at once.

**Wins.** Dependencies accepted rather than created. Two adapters justify the seam — environment in
production, a fake `Session` in tests — where today there is one. Four of the **seven** strict-xfail
stubs in `tests/api/test_http_contract.py` become writable, including both ADR 0007 acceptance
criteria. (Seven is the count of `@UNWRITTEN`-marked tests in that file at `506ad9b`; this said six
and the Built note below said seven.) The backwards import in `browse_routes` disappears with the
cycle it exists to dodge.

**What the seam costs today.** `tests/serve/test_chat_transport.py:15-18` states it plainly: the routes
call `session_from_environment`, which builds a Postgres connector and seeds a corpus, so `POST /chat`
and `/chat/resume` are tested by calling `routes._shape` directly. Elsewhere one dependency is supplied
by patching two private functions.

> **Built, 2026-08-11.** `routes.make_app(session, graph, turn_log)` and
> `graph_app.build_serve_graph(session, turn_log=…)`; `accept_node(session)` moved to `serve/accept.py`;
> `browse_routes.make_router(session)` replaced the module-level `router` and the backwards import.
> `session_from_environment` and the new `routes.app_from_environment` are the process adapter, and
> `app = app_from_environment()` stays a module attribute because `langgraph.json` names one.
> All seven strict-xfail stubs in `tests/api/test_http_contract.py` are written — including both
> ADR 0007 acceptance criteria — and `test_trusted_constants.py`'s source-string check of `"trust("`
> is now an executed one. `POST /chat` is exercised over HTTP with a four-asset in-memory corpus.
> **Not done here:** C6, C7, C8, and the `build_graph(surface=…)` rename. `compile_graph()` and the
> served graph are still two topologies, deliberately — `/chat` still uses the no-`accept` one.

---

### C6 — One turn entry, not one per transport

**Strong · R.** `api/routes.py:341-406, :409-425, :443-477` · `api/graph_app.py:251-299` ·
`serve/session.py:113-133` · `serve/nodes/facets.py:365` · `api/auth.py:38-39`

**Problem.** REST and the streamed graph each mint and record a turn, and they already differ:

| | REST (`routes.chat`) | streamed (`_accept_node`) |
|---|---|---|
| `turn_index` | count of `history` rows with `role == "user"` | count of `human` messages in state |
| `identity` | passed | **not passed** — `resume_authorised` is unreachable on the transport the UI uses |
| `query_vector` | on the config, via `session.configurable(question=…)` | embedded into state |
| logging | `_logged` sets `audit_logged` / `audit_error` on the reply | result discarded, returns `{}` |

The third has already leaked into a retrieval node: `serve/nodes/facets.py:365` reads
`state.get("query_vector") or cfg.get("query_vector")` — a node defending against which entry adapter
ran.

**Solution.** A `serve/entry.py` module — `turn_from_conversation(session, question, *, thread_id,
identity, turn_index)` and `record_turn(state)` — called by both. `routes.chat` collapses to about ten
lines of unpacking; `_accept_node` to five.

**Wins.** One test of `turn_from_conversation` covers both transports; today the streamed entry, the
one the UI actually uses, has none. `identity` and `query_vector` stop being per-transport facts, so
`facets` can read one channel.

> **ADR 0007 Amendment 2** ruled on this exact shape when `answer_text` was patched into `_shape`:
> "A boundary patch that fixes one of two transports is how a defect hides behind a route that passes."
> The remaining three divergences are the same shape. This extends the ADR rather than reopening it.

---

### C7 — One knob resolver, not six precedences

**Strong · V.** `serve/runtime.py:251-324, :38` · `serve/fetch.py:50, :64` ·
`serve/nodes/assemble.py:68` · `serve/graph.py:112` · `serve/nodes/agent_core.py:260, :311` ·
`register/knobs.py:392-451` · `api/graph_app.py:169-182`

**Problem.** `int_knob` / `float_knob` / `bool_knob` resolve `state → knobs_resolved → knob_default`.
The interface has no slot for `env_var`, so callers that need one go around it, and they disagree:

| call site | precedence it implements |
|---|---|
| `runtime.int_knob` | state → resolved → register |
| `fetch.read_body_cap` | state → resolved → cfg → literal `80_000` |
| `assemble._budget_chars` | state → resolved → cfg → literal `80_000` |
| `graph._node_timeout` | env → register, **no state** |
| `agent_core._recursion_limit` | env → resolved → register, **skips state** |
| `graph_app._retries` / `_timeout` | env → `knob_default`, **record never told** |
| `knobs.env_override` | the recording copy, with the parsing rules deliberately duplicated |

Two of those literals shadow the register: `read_body_max_tokens` is declared `20_000` (= 80 000 chars)
and `fetch.py:50` hardcodes `80_000` chars separately; `context_budget_chars` is declared `80_000` and
`runtime.py:38` hardcodes it again. They agree today by hand.

Verified: `llm_max_retries`, `llm_timeout_s` and `llm_utility_timeout_s` are all `Role.comparability`
and **none declares `env_var=`**, while `graph_app._retries` and `_timeout` read
`GOVERNED_BI_LLM_MAX_RETRIES` / `_TIMEOUT_S` / `GOVERNED_BI_UTILITY_TIMEOUT_S` directly. The server runs
on the environment; the record publishes the defaults.

**Solution.** One resolver — `env_override → state → knobs_resolved → knob_default`, raising on `UNSET`
— used by both the reader and the recorder, so `env_override` stops being a copied parser. Declare
`env_var=` on the three llm knobs and delete `_retries` / `_timeout`.

**Wins.** The record follows the knob by construction rather than by two functions agreeing. One
parametrised test over `env_overrides()` replaces per-knob assertions. Two shadow constants and two
dead `cfg` sources go.

**The guard freezes the gap.** `tests/serve/test_the_record_follows_the_knob.py:408-431` asserts the
declared set is exactly the three node-timeout knobs and greps only `serve/graph.py` and
`serve/nodes/agent_core.py`. It checks declared → read; it cannot see read → undeclared, which is where
`api/graph_app.py` sits. Widening it to grep all of `src/` for `GOVERNED_BI_*` reads — the shape
`tests/conformance/test_only_entry_points_read_the_environment.py` already uses for `.env` — closes the
direction.

---

### C8 — Generate the wire contract from one declaration

**Strong · V.** `api/routes.py` (10 routes, every handler `-> dict[str, Any]`) · `api/browse_routes.py`
(5) · `docs/openapi.json` (3,663 lines) · `ui/lib/schemas.ts` (610) · `ui/lib/types.ts` (159) ·
`ui/lib/mock/fixtures.ts` (979) · `ui/scripts/check-api-contract.ts`

**Problem.** One response contract, four hand-maintained mirrors, and nothing in CI compares any two.
Verified: no code in the tree generates or reads `docs/openapi.json` — the only mentions are prose. Its
own `info.description` concedes that every handler is annotated `-> dict[str, Any]`, so FastAPI neither
validates nor filters against the schemas it holds. The one live cross-check,
`ui/scripts/check-api-contract.ts`, needs a running engine plus a loaded corpus and is deliberately out
of CI. The whole Python-side enforcement is a regex in `tests/api/test_http_contract.py` scraping one
enum out of `schemas.ts`.

Who breaks on a rename: nobody, loudly. `z.object` strips undeclared keys, so a renamed producer field
is discarded silently; a renamed *required* field throws in the client and blanks a tab.

```
before   handlers  15 inline dict literals
            ├──▶ docs/openapi.json        3,663 lines, hand-kept, no reader
            ├──▶ ui/lib/schemas.ts          610 lines, hand-written, the real parse target
            ├──▶ ui/lib/types.ts            159 lines, inferred from it
            └──▶ ui/lib/mock/fixtures.ts    979 lines of values for the same shapes

after    api/wire  15 response shapes as typed records, response_model=
            ├──▶ docs/openapi.json   emitted in CI
            └──▶ ui/lib/schemas.ts   generated from the same source
```

**Solution.** Declare the response shapes once in Python; let FastAPI emit the spec, and derive the zod
from it. `check:api` stops being the only comparison and becomes a live-corpus smoke test.

**Wins.** Field maintenance goes from four places to one. Leverage: one declaration behind 16 client
methods. A rename becomes a CI diff.

**Deletion test.** `docs/openapi.json` — deleting it makes complexity vanish; it is a pass-through with
two prose citations. `ui/lib/schemas.ts` — deleting it makes complexity reappear across 30 importing
files; it earned its keep, and should be the generated end rather than the hand-written one.

> **ADR 0007** already decided this in its Consequences: "`docs/openapi.json` must be regenerated from
> the implementation rather than kept by hand; a spec no process checks is the defect this repository
> keeps rediscovering." This candidate is that consequence unimplemented, not a reopening of it.

---

### C9 — Let `wrap_node` answer "is the turn over?"

**Strong · R.** `serve/wrap.py:84` · `serve/events.py:44-46, :86-90` · `serve/state.py:49` ·
`serve/graph.py:44-95` · seven node heads: `route_retrieve.py:77, :153, :199`, `assemble.py:37`,
`agent_core.py:47`, `reflect.py:314`, `narrate.py:47`

**Problem.** One predicate, four spellings, twelve places. `wrap_node` already asks
`silenced_by_terminal_state(stage, state)` — and uses the answer only to decide whether to emit an
event, then runs the node anyway. Each node then re-derives the same predicate to return `{}`. Of the
seven node-head guards, only three are reachable in-graph: `route` (a real fan-in from five facet
edges) and `reflect` / `narrate` (deliberately plain edges, so the observer cannot route). The other
four sit behind conditional edges that already divert terminal paths to `stamp`. Nothing distinguishes
the live guards from the dead ones, and nothing makes a new node have one.

```
before   state.TERMINAL_PATH_KINDS ──┐
         events._TERMINAL_PATH_KINDS ├── four spellings of one predicate
         graph._skip_if_terminal     │
         narrate.py:47 literal ──────┘
         wrap_node  asks the predicate → suppresses an event → runs the node anyway
         node heads route · reflect · narrate      live
                    resolve · connect · assemble · agent_core   dead in-graph

after    state.TERMINAL_PATH_KINDS ──▶ wrap_node: terminal → return {} without calling fn
                                   └─▶ graph._router(table)
         nodes carry no guard at all
```

**Solution.** Give `wrap_node` the behaviour it already has the information for. Delete the seven
guards and the duplicate frozenset, and collapse the six near-identical `_after_*` router bodies into
one table (`crashed → stamp`, `decline → decline`, else the named successor).

**Wins.** One test over `build_graph().get_graph()` proves every rail node returns `{}` when entered
with `path_kind="crashed"` — provable once for all nodes, instead of per-node and only for the ones
someone remembered. Locality: the rail rule lives in one module.

---

### C10 — One governed-tool adapter, not three plus a copy

**Strong · R.** `serve/tools.py:109-181` (`_fetch`) · `:275-295` · `:314-392` (`run_query`) ·
`serve/fetch.py:7-10`

**Problem.** The seam between `fetch` and `tools` is a positional tuple whose *arity* carries the
meaning "I owe the ledger a row", and `run_query`'s shape puts the attempt in slot 1 rather than slot 2.
So the one tool where ledger correctness is an audit property is the one tool that cannot use the
adapter — it has its own 80-line copy, including a refund `_fetch` has no notion of. Status is then
recovered by string comparison against fetch's vocabulary (`payload == OUT_OF_SCOPE_MESSAGE`).

```
before   fetch.read_body      -> (payload, delivered)             ──┐
         fetch.inspect_schema -> (payload, delivered)               ├─▶ tools._fetch
         fetch.sample_rows    -> (payload, delivered, attempt?)   ──┘   decodes positionally
         fetch.run_query      -> (payload, attempt)               ────▶ tools.run_query
                                          ^ attempt in slot 1             own 80-line copy:
                                                                          emit, except, refund, ledger

after    all four -> ToolOutcome(payload, delivered, attempt=None)
                          └─▶ tools._fetch(ledger_path=…, book=…)
              one place owns: charge → run → GovernanceUsageError re-raise
                              → pipeline-error row + refund → verdict on the Command
```

**Solution.** A named result from all four `fetch` functions, and let `_fetch` take the ledger concern
as data.

**Wins.** The invariant "an executor that dies before a verdict still owes a row" becomes one
parametrised test over the executor paths, rather than a `run_query`-shaped test plus a
`sample_rows`-shaped test that took different code to satisfy. The refund lives with the charge.

---

## Smaller candidates

All **Worth exploring · R**.

**Declare where each record field comes from.** `register/record.py:347-354` ·
`serve/nodes/stamp.py:179-281`. `project` is a pass-through — deleting it makes complexity vanish,
since `stamp` would write the same comprehension over `RECORD_REGISTER` itself. The real content is a
~100-line if-ladder in `stamp` ending at `return state.get(name)`: add a row to `RECORD_REGISTER` and
forget `stamp`, and the field resolves to `None` for ever — an `Absence.not_measured` field that is
silently never measured. Giving `RecordField` a `source` makes the register say where a value comes
from as well as why it exists. Cheap first step, available before the refactor: assert that no declared
field falls through to the default.

**One projection of an asset.** `api/browse_routes.py` (71 `getattr`) · `api/routes.py` (25) ·
`api/browse.py` (5). Provenance status is read four ways; `excluded` is a nested `getattr` chain at
five sites. Two projections of a table survive inside the HTTP surface and compute `has_suspect`
differently. The corpus assets are typed, so the defaults buy only silence — rename a field and every
route still answers 200 with a null in that column. An `api/projection.py` typed against the asset
classes also removes the reason `browse_routes` imports `routes` backwards (see C5).
*ADR 0009 D11 deleted a whole route on the ground that two projections of a table can disagree; this
pair is inside the module that survived.*

**One eval composition root.** `tools/run_datalake_eval.py:172-249` · `tools/routing_recall.py:73-136`
· `tools/query_summary_alignment.py:97-141`. About 190 lines of triplicated session / provider /
connector setup, drifted three ways: one builds the embedder without `max_retries`, one calls
`chat_model` with no `provider=` and so silently takes a different gateway than the arm it claims to
replicate, one checks credentials per surface and the others do not. Both un-gated tools are the ones
whose numbers argue corpus and prompt-variant decisions.

**Two composition roots for the session.** `api/graph_app.py:62-140` · `serve/__main__.py:78-137`. Both
assemble connector + policy + models + cache, about 50 lines each, and they disagree: the model-backed
BI-scope gate is enabled on the server and not in the CLI, and the CLI passes neither of the two
resilience settings C7 is about. A `SessionSpec` record plus one `build_session(spec)` makes the policy
difference a visible field rather than two literals in distant files.

## Also on the merge seam

Noticed, not yet candidates. All **R**.

- `ui/lib/api-client.ts` has client methods for `GET /search` and `POST /corpus/edit`, which the engine
  does not have — kept honest only by two hardcoded capability flags in `routes.py`.
- `ui/lib/schemas.ts` cites `governed_bi.api.schemas` as the shape's source in three places. That module
  was deleted with v1. C8's generator would make the citation real.
- Stage *names* are well pinned by `register/stages.py` and `tests/serve/test_stream_events_end_to_end.py`;
  the ~35-key stage-event `detail` vocabulary is declared only in prose, on both sides of the seam.
- Two implementations of "give me `{id -> asset}`" — `runtime.assets_by_id` and `tools.resolve_assets` —
  disagree on a corpus shaped `{type: [assets]}`. Unreachable on the `Session` path, which always sets
  `assets_by_id`; invisible to `tools/check_one_implementation.py`, which matches on top-level names.
- `_model_name` has a fourth copy at `model/usage.py:104`, and `state.UsageRecord` does not declare
  `stage` although `usage_row` writes it on every row.

## Top recommendation

**C5 — give the served topology a constructor.**

It is the enabling move. C6, C7 and C8 all describe friction that survives because the surface it lives
on cannot be constructed in a test: seven strict-xfail stubs, a graph verified by splitting its own source
string, and two ADR 0007 acceptance criteria with no executing assertion. The deepening itself is small
— move `_accept_node` into `serve/` with its session as a parameter, and turn three module-scope
globals into arguments to `make_app`. Two adapters then justify the seam, and the interface becomes the
test surface for everything downstream.

One thing should not queue behind it: **C1** is a confirmed live loss. A declared retrieval measurement
is written and destroyed one super-step later on every turn that hits the budget cap, and nothing reads
it anywhere in `src/`. That is a small fix on its own.
