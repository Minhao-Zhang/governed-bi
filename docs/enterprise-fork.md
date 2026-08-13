# Forking this engine into an enterprise deployment

**Who this is for.** You have a semantic layer, a warehouse, and a permission model that already
exists — an IdP, groups, a data catalogue with owners, maybe Postgres row-level security. You want
this engine's governed text-to-SQL path, and you want it to answer *for a particular person*.

**What this repository is.** A research engine with **no transport authentication at all**, one
principal, and a deterministic seven-layer gate between the model's SQL and the database
([ADR 0006](adr/0006-execution-time-governance.md)).
It has a **seam** for authorization ([ADR 0012](adr/0012-access-seam-principal-and-authorization.md)) —
ports, a default adapter that changes nothing, and one reference adapter.

**What it is not, and will not become.** A permission product. Read this table before you plan,
because the right-hand column is work you are going to do:

| this repository does | you do |
|---|---|
| refuses a statement that names a table your policy does not grant | decide what your policy grants |
| refuses a statement that names a column your policy denies | know which columns are PII |
| authenticates nobody — reaching the port is sufficient (2026-08-13) | authenticate people **first**, and map them to a `Principal` |
| carries a `Principal` with an id and roles | resolve the roles — directory, IdP claims, group expansion |
| declares a row-level predicate and **refuses** rather than applying it | enforce row-level security **in the database** |
| gives you two `AccessPolicy` adapters | write the third, against whatever you already have |
| records what ran, per turn, per statement | ship the ledger somewhere it is retained |

There is no tenant model, no policy admin UI, no user store, no per-caller token infrastructure and
no SELECT-level masking. Those are the product; this is the seam.

**Step 0, and it is not in the table because it is not optional.** Until 2026-08-12 the engine
checked a shared key; on 2026-08-13 that was removed, for a reason that holds only here — a
single-operator engine on `127.0.0.1`, where a required key made LangGraph Studio unusable
([usage](usage.md#serve-langgraph-server)). The result is that **anything that can open a socket to
the port can post a turn and read every past turn's SQL out of `/audit/turns`**; audit findings A1
and A7 are open again as written. A fork does not inherit that trade. Terminate authentication in
front of this engine before anything else on this page matters.

---

## The one paragraph to read first

**On an unmodified checkout the access seam is wired and authorizes everything.** That is not the
same as unenforced, and it is not the same as a boundary: the shipped adapter is
`OpenAccessPolicy`, so every rule fires and every rule says yes. Point
`GOVERNED_BI_ACCESS_POLICY` at a TOML file and the reference adapter takes over — the layer stack
refuses, the tools refuse, the renderer stops putting the denied assets in the prompt, and the
browse routes stop serving them — with no code change. Write your own adapter (step 1) when a file
is not where your grants live.

**The sentence to hold us to:** a grant withholds an asset from everything this repository shows a
caller — the model's prompt, all four of the tools that name an asset (`ask_user`, the fifth
`build_tools` binds, names none), and every HTTP route that projects a corpus asset — and it
withholds nothing from a database, from a row, from an answer's prose, or from the
curation problems `/audit/corpus` reports. What you still owe in either case is everything in the
right-hand column above, and step 4: **this engine does not apply a row-level predicate.**

---

## Build order

Six steps. Step 3 is now **done for you** — it was owed on 2026-08-12 morning and landed the same
day — so read it as "what the engine already does with your policy" rather than as work. Step 5
likewise. What is left for you is steps 1, 2, 4 and 6: decide what your policy grants, decide what
a `Principal` is in your system, put row-level security in the database, and retain the ledger.

### 1. Write an `AccessPolicy`

One method:

```python
from governed_bi.ports import AccessPolicy, Grant, Principal, Reach

class DirectoryAccessPolicy:                       # your adapter
    def grant_for(self, principal: Principal) -> Grant:
        rows = self._catalogue.grants(principal.roles)   # your system
        return Grant(
            reach=Reach.listed,
            tables=frozenset(r.table for r in rows),
            denied_columns=frozenset(r.column for r in rows if r.pii),
        )
```

What the engine guarantees, so you do not have to:

- **Keys fold.** Write `Sales.Orders` or `sales.orders`; both match the corpus's declared spelling.
  Tables are `schema.table`, columns are `schema.table.column`.
- **A key with the wrong number of parts raises — but know *when*.** The reference adapter refuses
  it at **file load**, in `_require_keys`, so a typo in a policy file never reaches a query. A
  `Grant` your own adapter builds is not checked then: `Grant.__post_init__` rejects a blank key
  and the `every_table`-with-a-table-list contradiction, and nothing else. The part count is
  checked when `govern/access.py::resolve_grant` folds the grant, which is per statement inside
  `check()`. So validate keys where you build them if you want the failure at wiring time rather
  than on the first turn that touches one.
- **`Grant()` authorizes nothing.** If your adapter returns before deciding, the turn refuses. There
  is no return value meaning "no opinion".
- **`Reach.every_table` is the only way to say "everything".** There is no `None`-means-open.
- **The port says "once per turn"; this tree asks once per process.**
  `api/graph_app.py::session_from_environment` builds one `Session` and caches it in `_SESSION`, so
  `resolve_access_grant` calls your adapter at startup and the whole process serves that grant.
  Cache accordingly — and if your grants have to change while the server runs, or differ per
  caller, the thing you are changing is the composition root, not your adapter.
- **Raising is a wiring failure, not a refusal.** If your directory is down, let the exception
  propagate. The engine will not turn it into "this query was unsafe".

The two shipped adapters are in `src/governed_bi/govern/access.py`. Start by reading
`StaticRoleAccessPolicy` — it is about eighty lines and it is the shape of the problem.

### 2. Decide what a `Principal` is in your system

```python
Principal(id="alice@example.com", roles=frozenset({"analyst", "eu-region"}))
```

`id` is for the ledger and for your own audit. `roles` is what your policy keys on. There is no
`tenant` field and adding one is a real decision, not a formality — nothing downstream is
multi-tenant.

If you use `StaticRoleAccessPolicy`, its composition rules are:

- **grants union** — two roles that each name tables give you both sets;
- **denials union** — a role that grants a table cannot un-deny a column another role denied;
- **`every_table` beats `listed`**, and still does not lift a denial;
- **an unknown role contributes nothing** — not an error, not a wildcard;
- **two roles declaring different row predicates for one table raises.** Declare a table's
  predicate in at most one role.

### 3. Wire it — **already wired**

The three lines [ADR 0012 §8](adr/0012-access-seam-principal-and-authorization.md) recorded as
owed exist. What they do, so you know where to look when it misbehaves:

1. **`api/graph_app.py::resolve_access_grant`** asks your policy once per process, for the
   principal `api/auth.py::authenticated_principal()` returns, and passes the grant into the
   `GovernancePolicy` the session is built with. `check()` and `prepare()` read it off the policy
   they are already given. To use the reference adapter instead of writing one:

   ```
   GOVERNED_BI_ACCESS_POLICY=config/access.toml    # relative to the repo root, or absolute
   ```

   A path that is not a file **raises at startup**. That is deliberate: falling back to the open
   policy would serve every table to an operator who believes a restriction is in force.

   To use your own adapter, construct it in `access_policy_from_environment` — that function is
   the only place in `src/` that chooses one.

2. **`serve/delivery.py::tool_bounds_from_state`** folds the grant and passes it into
   `ToolBounds`, so `inspect_schema` and `sample_rows` are bounded. It also subtracts the
   withheld assets from `readable_assets`, which is what gates `read_body` (step 5).

3. **`serve/session.py::_resolved_knobs`** records `policy.access_grant.digest()` under the
   `access_grant` knob, which is `Role.comparability` and therefore in the config hash. Two runs
   under different authorization no longer hash identically
   ([ADR 0006 §13](adr/0006-execution-time-governance.md)).

**What you still have to decide** is who your principals are and what their roles grant. The
engine has one principal because nothing on its transport can produce a second one — there is no
credential to derive one from; the moment you have two, see the audit findings at the bottom of
this page.

### 4. Put row-level security in the database

The engine will not inject a `WHERE` clause, and [ADR 0012 §5](adr/0012-access-seam-principal-and-authorization.md)
is the argument for why you should not want it to: a predicate injected into a statement is
semantically wrong under an outer join, inside a `UNION` arm, and against a CTE that shadows the
table's name — each of which is a wrong answer delivered with a passing verdict.

Do this instead:

```sql
ALTER TABLE sales.orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY orders_by_region ON sales.orders
  USING (region_id = current_setting('app.region')::int);
```

and connect the engine through a role that policy applies to. Then declare the predicate to the
engine so it is visible in your policy file and in the ledger:

```toml
[[role.analyst.row_predicate]]
table = "sales.orders"
expression = "region_id = current_setting('app.region')::int"
enforcement = "database_role"          # you are asserting the database does it
```

`enforcement = "database_role"` is **a claim you are making**. The engine records it and does not
verify it. The default, `refuse`, is what you get if you declare a predicate and say nothing: every
statement touching that table refuses, because executing it would return exactly the rows the
predicate exists to withhold.

Two things to get right:

- The session variable (`app.region` above) has to be set per connection, which means your
  connection pool has to be per-principal or set it per checkout. This engine's connector opens
  with `autocommit=True` and applies session settings immediately
  ([ADR 0006 §10](adr/0006-execution-time-governance.md)); it does **not** set anything
  principal-derived today.
- A read-only database role is still required independently. Read-only does not stop read-side
  exfiltration — that is what the layer stack is for — but the layer stack is not the last line
  either.

### 5. Narrow what the model sees — **already done**

Refusing is not the same as not disclosing, and until 2026-08-12 an unauthorized table's
structural line and body still reached the prompt: the renderer worked off `licensed` and
`retrieved`, not off the grant, so a model could describe a table it could not query and
`read_body` returned its prose.

`serve/context.py::withheld_by_grant` now computes the asset ids your grant does not authorize —
a table whose qualified name is not granted, a column that is denied or whose table is withheld,
a join or metric with a withheld endpoint, a term whose binding points at any of those — and
**one set feeds all of it**: the rendered block, `ToolBounds.readable_assets`, `inspect_schema`'s
column roster, and every HTTP route that projects a corpus asset. Doing one without the others is
a bound that looks enforced, which is why they are one function.

Three of those four were added on 2026-08-12 **after review**, and it is worth knowing what they
were, because they are what a fork would otherwise rediscover:

- `inspect_schema` returned every column of an authorized table, denied ones included, while the
  rendered block correctly omitted them. A table-level bound in front of a column-level payload.
- A join's endpoint fields can hold a **bare** physical name, and the withholding rule matched only
  the qualified spelling — so `join customers >< audit_log on …` rendered while the same join,
  spelled `sales.customers >< sales.audit_log`, was dropped.
- The browse routes (`/corpus/rows`, `/schema/{table_id}`, `/graph`, …) read the corpus directly
  and served denied columns with their `sample_values`.

Four things the narrowing does not do, stated so you do not discover them:

- **A few-shot's `sql` is not parsed**, so an example query over a denied table still ships — it is
  the same non-fatal reference that `serve/session.py::_visible` declines to prune for
  `governance.excluded`, declined here for the same reason. A metric's `expression` is the same
  case; what is matched is its `base_table`.
- **`run_query`'s refusal names the denied table back to the model.** The verdict `detail` is
  returned to the agent, so it learns the table exists and is denied. ADR 0012 open question 4.
  If that matters to you, return `OUT_OF_SCOPE_MESSAGE` to the model and keep the true rule in
  the ledger.
- **`/audit/corpus` carries the curator's problem strings verbatim**, and one can name a withheld
  table. `servable` is `not fatal_problems`, so filtering them would let an unservable corpus read
  as servable. If your operators and your analysts are different people, put that route behind a
  different key.
- **A bare endpoint that matches a withheld table's name in *another* schema is withheld too.**
  Two schemas each holding a `customers`, one withheld, withholds both bare-spelled joins. A false
  refusal, chosen over naming a table the principal may not read.

### 6. Retain the ledger

Every governed statement writes an `AttemptRecord` carrying the layer, the rule id, and the exact
SQL that was sent. `api/trace_store.append_turn` writes turns to `runs/serve/<date>.jsonl`,
**verbatim and unredacted** — question, answer, statement, literals and all
([ADR 0006 §11](adr/0006-execution-time-governance.md) deleted the redaction vocabulary rather
than leave it declared and unenforced).

That is right for a single-user local tool and wrong for you. Before production: decide a retention
policy, decide whether result literals may be written at all, and replace the sink. The structural
fingerprint and `statement_sha256` in `govern/ledger.py` exist for a sink that wants auditability
without echoing values.

---

## What you get for free once it is wired

**Three new reason codes, and the three neighbours they must not be collapsed into.**
`r_table_not_authorized`, `r_row_predicate_unenforced` and `r_column_not_authorized` are what
[ADR 0012 §3 and §4](adr/0012-access-seam-principal-and-authorization.md) added; the other three
predate it, and the whole point is that they are no longer one bucket:

| reason code | means | who fixes it |
|---|---|---|
| `r_table_not_licensed` | retrieval did not find this table this turn | your corpus, or the router |
| `r_table_not_authorized` | this principal may not read it | your access policy |
| `r_row_predicate_unenforced` | a predicate is declared and this engine will not guess | you, in the database (step 4) |
| `r_column_excluded` | the corpus hides it from everyone | your corpus |
| `r_column_not_authorized` | this principal may not read this column | your access policy |
| `r_column_not_allowed` | no such column in this corpus | your corpus |

A support ticket that says "the engine refuses my query" is answerable from the ledger without
reproducing it.

**Ordering that does not leak.** The licence is checked before authorization, so a caller cannot
distinguish "exists but denied" from "does not exist" by reading which refusal came back. If you
change that order, you have built an oracle over your table list.

**A grant cannot widen the licence.** Authorizing a table retrieval never found does not make it
queryable. Authorization only ever narrows.

## Verifying your fork

Everything below runs offline, with no model and no credentials.

```
uv run --frozen pytest -q                        # the whole suite
uv run --frozen python tools/govern_bench.py     # the adversarial governance suite
uv run --frozen python tools/mutate.py           # does the suite notice when a layer is deleted?
```

`tools/mutate.py` **rewrites source files in place and restores them from HEAD**, so commit first
and run it with nothing else editing the tree. It is nightly here, not per-push.

`tools/govern_bench.py` prints a bypass rate, a misattribution rate and a false-refusal rate, each
with its denominator — and, since 2026-08-12, a **disclosure rate and an over-withheld rate** over
the `[[probe]]` half of the same file. That half is the one a PII fork should read first: a
`[[case]]` asks whether a statement was refused, a `[[probe]]` asks what the principal was *shown*
across the rendered block, `inspect_schema`, `read_body` and the `may_sample` bound. Every one of
the three step-5 disclosure defects above was found by review and none of them is SQL-shaped, so
the cases alone could not have caught any of them. Add cases and probes for **your** policy to
`src/governed_bi/govern/adversarial.toml` —
the world there declares `authorized`, `denied_columns` and row predicates, the loader refuses a
world that authorizes everything it licenses, and every case has to say why it exists. A benign case
is a declaration that a statement must pass; the false-refusal rate is the other side of the trade
your policy is making, and it is the number that tells you whether analysts will be able to work.

**The number to watch is misattribution, not bypass.** A refusal under the wrong rule is a refusal
that sends someone to debug the wrong system, and a gate asking only "was it refused" reports a
deleted rule as working.

## The parts of this that are not finished

Stated here rather than discovered by you:

- **The false-refusal rate of a real grant is unmeasured.** The 53 benign cases in the adversarial
  suite are a fictional world.
- **The tool bounds and the grant can disagree about a table's *name*, and only one half of that
  used to be written down here.** Both `may_inspect_schema` and `may_sample` compared an unfolded
  key against a folded grant. The first was a false refusal and this list said so; the second was
  the **denial** test, so `may_sample('sales.hr_notes.Employee_Note')` answered *allowed* under a
  grant denying exactly that column. `check()` folds both sides and refused the statement, so no
  value left the box — but a ledger row was spent, and "it fails closed" was true of one line and
  false of the one under it. Both keys are folded as of 2026-08-12.

  **What survives is the slug divergence, and it is yours to avoid.** Where the corpus builder's
  `slug()` fired, a table's asset id is not its `{schema}.{physical_name}` — `airline.Air Carriers`
  is licensed as `airline.Air_Carriers_66c534` — so a grant written against physical names does not
  match those ids in the bounds. Disclosure is safe there because `ToolBounds.withheld` is computed
  from the corpus and maps ids to qualified names correctly; what you get is a **false refusal**
  from the two bounds on slugged tables. If your physical names contain spaces, punctuation or
  leading digits, expect it.
- **A denied column is still named in the ledger as denied.** `r_column_not_authorized` tells a
  reader the column exists. ADR 0012 §4 trades that deliberately against collapsing it into
  "no such column"; the fix for a denied column being *visible* is to narrow the context, which
  step 5 does.
- **Audit findings A1 and A7 are open again** — every route is unauthenticated, and `/audit/turns`
  and `/audit/turns/{id}/trace` hand any caller every thread's SQL, the full turn records, and an
  absolute log path. They were closed on 2026-08-12 by a shared key and re-opened on 2026-08-13
  when it was removed (step 0 above). This is the finding a fork must not inherit.
- **Audit findings A5, A6 and B1 are open** — the streamed transport passes no identity, `/chat/resume`
  validates by thread rather than by caller, and `POST /threads/search` returns `identity` and the
  rendered context block. They are open because there is one principal, so there is no
  second caller to be wrong about. **The moment you have two principals, all three are live**, and
  they are the first things to fix. See [`docs/analysis/audit-2026-08-10.md`](analysis/audit-2026-08-10.md).
- **Corpus prose is not PII-gated.** Rule V5 stops literals reaching an asset's `summary`, so authors
  put them in `body` — and `body` reaches the prompt on every hit while `summary` never does. No gate
  checks `body` for PII. This is the finding most likely to bite a fork with real customer data, and
  it is a corpus-authoring problem, not an engine one.
