# 0012: The access seam — principal, authorization, and the Layer 6 split

- **Status:** Accepted and wired (2026-08-12), **corrected the same day after review**. The ports,
  the two adapters, the three new TABLES/COLUMNS rules (`r_table_not_authorized`,
  `r_row_predicate_unenforced`, `r_column_not_authorized`), the tool bounds and the adversarial
  cases landed first; §8's four wires landed next; an independent review then found that three surfaces
  disclosed what the layer stack refused — `inspect_schema`'s column roster, a bare-spelled join's
  ON clause, and the whole browse HTTP surface — while §8 claimed "enforced end to end" and the
  Consequences simultaneously claimed the seam covered nothing in `serve/` or `api/`. All three are
  closed (§8.4a, §8.5, §8.6), the suite gained a disclosure half that can express them (§9a), and
  **§8's opening paragraph is now a sentence with a falsifier attached** rather than a slogan.
  `tests/serve/test_the_access_seam_reaches_the_served_app.py` runs every behavioural claim through
  `build_serve_graph`, not through a direct `check()` call.
- **Deciders:** project owner (2026-08-12).
- **Scope:** who a turn is executed *for*, and what that principal may read — a `Principal`, an
  `AccessPolicy` port, the value it returns, the two rules the layer stack gains, the tool
  bounds that have no statement for the stack to read, and the integrator's build order.
  **Not** authentication — there is none, as of [ADR 0007](0007-http-surface-and-the-ui-contract.md)
  Amendment 3 — not retrieval (ADR 0005), not the statement's shape (ADR 0006).
- **Related:** [0006](0006-execution-time-governance.md) is the layer stack this amends;
  [0008](0008-identifiers-end-to-end.md) D1/D2 is why keys fold where they do;
  [0005](0005-v2-memory-layer-and-faceted-retrieval.md) §8 owns `licensed`.
- **Amends 0006** in three places. §1's `check()` reads authorization from `GovernancePolicy`;
  §8's table "the licensed set" is now two sets with two meanings; §12's Consequences said row-level
  security and per-user identity were out of scope for the "enterprise fork" — the *seam* for both
  is in scope as of this ADR and the *product* is not.
- **Reverses** [`docs/analysis/strategy-checkpoint-2026-08-11.md`](../analysis/strategy-checkpoint-2026-08-11.md)
  §2.2, which locked "not an enterprise permission product; no RLS/RBAC stack; permissions stop at
  the connection role." That section is amended in place rather than overwritten — a reversed
  decision is recorded here.

---

## Context

### The question this answers

[`docs/open-work.md`](../open-work.md) §4.2 asks whether `licensed` should keep serving two
masters. It is simultaneously

- the **retrieval budget** — `ASSET_REGISTER[table].budget = 8`, plus join endpoints and Steiner
  points (ADR 0006 §8) — and
- the **governance allowlist** the TABLES layer enforces.

So a retrieval miss is a hard refusal, and 19 of the v4 arm's 20 refusals end on
`r_table_not_licensed`. §4.2's verdict: decoupling them "would change what 'governed' means and
needs an ADR, not a patch."

The interpretability cost is the part that is measured. §4.1's honest headline is *"the engine
declines when its own context is insufficient"* — and the only evidence for "its own context"
rather than "you may not" is that today there is no second thing `r_table_not_licensed` could
mean. That is an argument from the absence of a feature, and it stops working the moment anyone
deploys this behind a permission model. Splitting the rule now is what keeps the abstention story
falsifiable later.

### The decision that was reversed, and why

The strategy checkpoint of 2026-08-11 is a portfolio document, and against that goal §2.2 was
right: an enterprise permission product is a different product, and half of one is positioning
pollution. The goal changed on 2026-08-12 — this repository is to be a **fork-ready base for an
enterprise deployment where PII, RLS and RBAC are handled**.

That is not the same as becoming a permission product, and the difference is the whole content of
this ADR:

| built here | deliberately not built here |
|---|---|
| a `Principal` with an id and roles | a user store, an IdP, a tenant model, per-caller tokens |
| an `AccessPolicy` port, called once per turn | a policy admin UI, a policy language, policy versioning |
| a default adapter that changes nothing | a "secure by default" restrictive default |
| one reference adapter (roles → grants, from a file) | a directory integration, group expansion, inheritance |
| a table authorization rule with its own reason code | anything that widens `licensed` |
| a column denial rule | SELECT-level masking, tokenisation, differential privacy |
| a declared row predicate, and a refusal when it cannot be enforced | statement rewriting to inject `WHERE` |

The audit dispositions in the checkpoint's §3 stand unchanged: A5, A6 and B1 are still unfixed and
still for the reason given, because closing them needs the per-caller token infrastructure that is
in the right column above.

---

## Decision

### 1. `Principal` — the subject a turn is executed for

```python
@dataclass(frozen=True, slots=True)
class Principal:                       # ports.py
    id: str
    roles: frozenset[str] = frozenset()
```

**This repository has exactly one principal, and the ADR says so rather than implying
multi-tenancy exists.** `api/auth.py` returns `identity: "governed-bi-local"` for every caller,
and `govern/access.py::LOCAL_PRINCIPAL` is that identity as a value.

> **Amendment (2026-08-13): the single principal is now asserted, not proven.** This section was
> written when `auth.py` compared one shared key in constant time; the argument was that a single
> shared key cannot distinguish two callers, so deriving an identity from it would be a fiction.
> The key is gone — no route asks for a credential at all
> ([usage](../usage.md#serve-langgraph-server)) — and the conclusion is unchanged while the
> premise is weaker: `authenticated_principal()` is still a function of nothing, but now because
> there is nothing to be a function *of*. Everything §8 wires is untouched, and a fork that
> authenticates people still replaces exactly this one function.

`roles` exists and `tenant` does not. Roles are the smallest thing an adapter can key on that is
not the id itself — an adapter mapping id → grant *is* a user store, and the port would then be
the wrong shape for the first fork that has a directory. A tenant field, by contrast, buys nothing
until something is multi-tenant, and a field nobody sets is the declared-not-consumed defect this
repository keeps auditing for.

### 2. `AccessPolicy` — one method, and the four decisions that buys

```python
class AccessPolicy(Protocol):          # ports.py
    def grant_for(self, principal: Principal) -> Grant: ...
```

The obvious shape is three getters — `authorized_tables()`, `denied_columns()`,
`row_predicate(table)`. It is rejected. Measured the way
[`docs/analysis/architecture-review-2026-08-11.md`](../analysis/architecture-review-2026-08-11.md)
measures depth — *behaviour a caller gets per unit of interface learned* — three getters are
almost pure interface. Every integrator would have to learn, and could get wrong:

1. **When they are called.** Per statement? Per tool call? A policy that can change inside a turn
   is a policy the ledger cannot describe.
2. **How keys fold.** `Sales.Orders` against a corpus that declares `sales."Orders"`. Getting this
   wrong is ADR 0006 B5, in a new place, with no test that would notice.
3. **What an empty answer means.** `frozenset()` from `authorized_tables()` is either "nothing" or
   "I have no opinion", and v1's `if allowlist is not None` is what the second reading looks like
   when it ships.
4. **How two roles compose.** Union? Intersection? Does a grant lift another role's denial?

Returning one value answers all four once, in this repository, where the tests are:

```python
@dataclass(frozen=True, slots=True)
class Grant:                           # ports.py
    reach: Reach = Reach.listed        # every_table | listed
    tables: frozenset[str] = frozenset()
    denied_columns: frozenset[str] = frozenset()
    row_predicates: tuple[RowPredicate, ...] = ()

    @property
    def is_open(self) -> bool: ...
    def digest(self) -> str: ...

OPEN_GRANT = Grant(reach=Reach.every_table)
```

- **`Reach` is an enum, not `None`-means-everything.** ADR 0006 G1 — "absence is not permission" —
  applies to the new set exactly as it does to `licensed`, where `check()` raises on `None`.
  Openness is a value with a name, so it is greppable, diffable and hashable.
- **`Grant()` authorizes nothing.** An adapter that returned before deciding must not open a door.
- **The value validates itself.** `reach=every_table` *and* a table list raises; two predicates for
  one table raises; a blank key raises. Each is a policy file meaning two things, and each fails
  where it is written rather than at the first query that touches it.
- **Folding is `govern/access.resolve_grant`'s job**, using the same `identifiers` functions the
  licence and the corpus column sets go through. The integrator writes whichever spelling they
  have and never learns there were two.
- **`digest()`** exists so whoever records the turn can name which authorization ran. §7 records
  that nothing carries it yet, and why declaring the knob today would be worse than not.

Two adapters, because `ports.py`'s own rule is that a single-adapter seam is rejected:

| adapter | what it is |
|---|---|
| `govern/access.py::OpenAccessPolicy` | authorizes everything. The default, and the honest description of a laptop deployment where the connection role is the whole story |
| `govern/access.py::StaticRoleAccessPolicy` | roles → grants from a committed TOML file. The reference: the smallest thing that is not a toy, and what an enterprise fork's first week looks like |

`StaticRoleAccessPolicy` owns the composition algebra, stated once so no fork restates it:
**grants union, denials union, `every_table` beats `listed`, and two roles declaring different
predicates for one table raises.** Grants are additive; denials are absolute. An unknown role is
neither an error nor a wildcard — it contributes no grant, so a principal holding only unknown
roles authorizes nothing.

### 3. The Layer 6 split

`licensed` keeps its meaning exactly: **what retrieval found this turn**. Authorization is a
second, independent question, and the TABLES layer asks three in a fixed order:

```
TABLES, per bound base source:
  1. key not in licensed          → r_table_not_licensed        (retrieval missed)
  2. grant does not authorize key → r_table_not_authorized      (you may not)
  3. key carries an unenforceable
     row predicate                → r_row_predicate_unenforced  (§5)
```

**The order is the security property and not a style choice.** Asking the grant first would make
the pair of rules an oracle: a caller could distinguish a table that exists-but-is-denied from one
that does not exist by reading which refusal came back. That is the probing channel
`bounds.OUT_OF_SCOPE_MESSAGE` exists to close, reopened in the ledger. Asking the licence first
means `r_table_not_authorized` fires only for a table this turn already put in front of the model,
so it discloses nothing the context did not.

The corollary is worth stating because an integrator will assume the opposite: **a grant cannot
widen `licensed`.** A table the policy authorizes and retrieval never found still refuses, as
`r_table_not_licensed`. A grant that could add a table would be ADR 0006 B7 — the agent growing
its own authorization set — arriving through the policy file instead of through a tool.

What this buys §4.2: the refusal histogram can now separate "retrieval missed" from "you may not"
without a second implementation, and the abstention claim keeps its narrow, checkable form under a
deployment that has permissions.

### 4. Column denial — the PII seam

```
COLUMNS, per bound column:
  1. excluded  → r_column_excluded         (the corpus hides it from everyone)
  2. suspect   → r_column_suspect          (reliability, under hard_block_suspect)
  3. denied    → r_column_not_authorized   (this principal may not read it)
  4. not allowed → r_column_not_allowed    (no such column here)
```

Denial runs **after** the corpus rules and **before** the fallthrough, and both halves of that are
arguments.

*After the corpus rules*, because `excluded` and `suspect` are facts about the deployment that
precede any principal: reporting them first reveals nothing about this caller, while reporting
`r_column_not_authorized` for a column the corpus hides from everyone would tell a denied
principal that the column exists and is merely denied *to them*.

*Before `r_column_not_allowed`*, because collapsing "you may not read this" into "there is no such
column" is exactly the conflation §4.2 asks to end one layer up. It would be the more secretive
choice; it is not the more honest one, and the ADR trades that deliberately — a denied column is
one the corpus declares, and the fix for it being visible in context is to narrow the context
(§8), not to lie in the ledger.

Denial **narrows** the corpus's allowlist and never replaces it. A denied column that the corpus
does not declare still needs the positive membership test to pass, so a policy file cannot
authorize a column into existence.

This is a *statement-level* control. It is not masking: the value is never fetched and never
transformed. §2.3 of the strategy checkpoint — "not a privacy scrubbing layer, PII responsibility
is inbound" — survives this ADR intact for values; what changes is that a fork can now stop a
principal from naming the column at all.

> **"Naming the column at all" was false when it was written, and §8.4/§8.6 are what made it
> true.** A statement-level rule stops the column being *read*. It does not stop the column being
> *shown*, and until 2026-08-12 three surfaces showed it: `inspect_schema` returned every column
> of an authorized table including the denied one, a bare-spelled join's ON clause named a
> withheld table, and the browse HTTP routes served the whole corpus. A principal who is handed a
> column's id, physical name and type can name it — the layer stack then refuses, which is a
> different property and a weaker one. Refusing is not withholding, and this ADR spent §8.4
> saying so about the renderer while the same sentence was untrue of the tools beside it.

### 5. Row predicates — declared, and **not enforced**

> **State it plainly, because a reader takes a declaration for the behaviour.** This repository
> does not apply a row-level predicate. `AccessPolicy` can declare one; the only enforcement
> shipped is a refusal.

```python
class PredicateEnforcement(str, Enum):
    refuse = "refuse"                 # default: this engine refuses the statement
    database_role = "database_role"   # the operator asserts the DB applies it
```

**There is deliberately no `inject` member**, and the vocabulary being closed is how it stays
rejected. Three reasons, in order of weight:

1. **It breaks G4.** ADR 0006 fixes the transformation pipeline at six steps and makes the row
   limit the *only* post-check rewrite; `prepare()` is the only function that may produce an
   executable string, and the ledger hashes exactly that string. Adding a `WHERE` after `check()`
   means the verdict is about a statement that did not run. Adding it before means re-deriving
   which scope each predicate belongs in, which is `binding.py` again with a second
   implementation — the tax this repository has paid twice.
2. **It is semantically wrong in shapes that are common, not exotic.** Under a `LEFT JOIN` a
   predicate in `WHERE` silently converts the join to an inner one and hides rows the predicate
   was not about. Inside a `UNION` arm it applies to one arm. Against a CTE that shadows the
   table's name it binds to the wrong relation. Each of those is a *wrong answer* delivered with
   a passing verdict, which is worse than a refusal.
3. **The right enforcer already exists and is not us.** ADR 0006 §10 already says production must
   connect through a read-only database role, on the argument that an application bug should never
   be the last line. Row-level security is the same argument: Postgres has `ROW LEVEL SECURITY`,
   it applies to every statement including ones this engine never sees, and it cannot be defeated
   by a parser disagreement.

So `refuse` is the default and it is the safe reading: executing a statement whose table carries a
predicate nobody applied returns exactly the rows the predicate exists to withhold. A fork that has
put the policy in the database changes the declaration to `database_role`, and this engine then
records the claim, does not verify it, and does not refuse. **That is a claim, not a mechanism**,
and the enum member is named after who is doing the work so a reader cannot mistake it.

The alternative — declare the hook and ignore it — is rejected outright. A declared control with no
enforcer is worse than an absent one, which is ADR 0006 §11's own conclusion about redaction, in a
section whose subject was auditability.

### 6. Tool bounds

`run_query` is bounded by the layer stack, so §3 and §4 cover it. The other three tools build no
statement for the stack to read, so `ToolBounds` asks the grant itself — **and asks the withheld
set, which is the correction of 2026-08-12**:

| tool | bound before | bound now |
|---|---|---|
| `read_body` | `hits ∪ pulled_in` | `hits ∪ pulled_in` **minus** `withheld` (§8.4) |
| `inspect_schema` | `licensed` | table: `licensed`, not withheld, authorized — **and each column of the roster is filtered by `withheld`** |
| `sample_rows` | column's table in `licensed`, then the layer stack | plus not withheld, plus authorized, plus not denied **on a folded key**, then the layer stack |
| `run_query` | the table layer | the table layer, now three rules |

**Two sets, and the division of labour between them is the fix.** `grant` answers at *table*
granularity about a *folded key*. `ToolBounds.withheld` holds the asset ids
`serve/context.py::withheld_by_grant` computed for this turn — the same set the renderer skipped —
and only a caller holding the corpus can compute it, because mapping an asset id to a table key is
`table_qualifier(asset)`. Every tool that names an asset asks `ToolBounds.discloses`; the grant
predicates stay as the fail-closed backstop for the table the tool was called on.

The row this table used to carry for `inspect_schema` — "`licensed` **and** authorized" — was the
whole of that bound, and it is the wrong granularity for a tool returning **column** metadata.
Under a grant authorizing `sales.customers` and denying `sales.customers.email`, the rendered
block omitted the column and the tool handed the model its id, physical name, type and
nullability. `withheld_by_grant` had already computed that column as withheld; nothing passed the
set to the tool. A withheld column is now *omitted* rather than marked — a payload saying "3
columns hidden" is `OUT_OF_SCOPE_MESSAGE`'s probing channel reopened in JSON.

**A restrictive grant with no `withheld` set no longer constructs.** ADR 0008 D7 — an optional
control argument is a control that will be un-wired — applied to the thing that had just been
un-wired, so `ToolBounds.__post_init__` raises. An *empty* set is a legitimate answer (a grant may
deny a column no corpus declares), so emptiness cannot be the signal; `None` is.

`ToolBounds.grant` defaults to the open resolved grant and `withheld` defaults to `None`, so every
existing construction site keeps its answers and the guard cannot fire on one.

### 7. What the measurement layer must see — and does not yet

ADR 0006 §13 requires security configuration to enter the config hash, "otherwise two runs with
different security configuration hash identically." `Grant.digest()` exists for that.

**Wired 2026-08-12.** `access_grant` is a `Role.comparability` knob, so it enters
`config_hash_keys()`, and `serve/session.py::_resolved_knobs` resolves it by calling
`policy.access_grant.digest()`.

Its register default is **`None`, and that is the whole design of the row**. A default carrying
the open grant's digest would publish "open" for a fork that shipped a restrictive one — the
`agent_recursion_limit` defect (behaviour moves, the artifact says the default) reproduced in the
security register, which is what open-work.md §3.10 is a section about. So the resolver reads the
policy or writes nothing, and a null on a row means "no policy was threaded", never "the grant was
open". `tests/serve/test_the_access_seam_reaches_the_served_app.py::
test_the_grant_digest_reaches_the_record_and_comes_from_the_policy` asserts both halves, and the
mutation that publishes `OPEN_GRANT.digest()` unconditionally is caught by it.

`govern/policy.py` still asserts at import that the shipped default is open, with that sentence in
the failure message.

### 8. What is enforced where — the honest boundary

> **The sentence, and it is the one a reader should quote.** *A grant withholds an asset from
> everything this repository shows a caller — the model's prompt, all four of its tools, and every
> HTTP route that projects a corpus asset — and it does not withhold anything from a database, from
> a row, from an answer's prose, or from the curation problems `/audit/corpus` reports.*
>
> "The seam is now enforced end to end" stood here from 2026-08-12 while three surfaces were not,
> and the Consequences section below simultaneously said the seam covered nothing "in `serve/` or
> `api/`". Both cannot be true and neither was. §8.4 through §8.6 are what the sentence above now
> rests on, and §8.7 is the list of what it deliberately excludes.

Four wires landed on 2026-08-12, the day after the rest of this ADR; §8.5 and §8.6 landed the same
day, after review. What each one is, and what it cost:

1. **`api/graph_app.py`** — `access_policy_from_environment` builds the policy at the composition
   root: `OpenAccessPolicy()` unless `GOVERNED_BI_ACCESS_POLICY` names a
   `StaticRoleAccessPolicy` file, and a **`RuntimeError` if that file is not there**, because an
   operator who configured a restriction and got an open server has a boundary they believe in
   and do not have. `resolve_access_grant` asks it once, for the principal
   `api/auth.py::authenticated_principal()` resolves, and hands the result to the
   `GovernancePolicy` already constructed there. `auth.py` now returns
   `authenticated_principal().id` rather than its own copy of `"governed-bi-local"`, closing the
   duplicated literal `govern/access.py` flags.
2. **`serve/delivery.py::tool_bounds_from_state`** — takes the turn's `configurable` as a
   **required** second argument (ADR 0008 D7: an optional control argument is a control that
   will be un-wired) and folds `policy.access_grant` with `default_schema=None`, which is what
   the serve path gives `prepare()`. §6's bounds are reachable.
3. **`serve/session.py::_resolved_knobs`** — §7.
4. **The renderer** — `serve/context.py::withheld_by_grant` computes the asset ids a grant does
   not authorize, `assemble` passes them to `render_context`, and
   `tool_bounds_from_state` subtracts the *same set* from `readable_assets`. That is what gates
   `read_body`, and it is why `ToolBounds.may_read_body` still asks no grant: one answer to "what
   may this principal see", in the one place that also narrows the prompt.

#### 8.4a Three spellings, not two — the joins, the metrics and the terms

`withheld_by_grant`'s endpoint matching was **spelling-dependent**, which is not a trade anyone
stated. It collected `{asset_id, table_qualifier(asset)}` for each withheld table and matched a
join's `left_table` / `right_table` and a metric's `base_table` against that set. Those fields may
also hold the **bare** physical name: `retrieve/structure.py::table_lookup` builds a key per table
for the asset id, `table_id(schema, physical_name)`, the bare `physical_name` and the engine
spelling `{schema}.{physical_name}`, and `bind_endpoint` resolves an endpoint against any of them
without recording which it matched. (In `../BIRD-corpus` as it stands, all 1 412 join endpoints and
all 478 metric `base_table`s are asset ids, so the bare path is a tolerance of the binder rather
than a shape that corpus exhibits; the world under test in `adversarial.toml` carries both
spellings deliberately — acceptance criterion 13.) With `sales.audit_log` withheld, the qualified
join was dropped and

```
join customers >< audit_log on customers.id = audit_log.customer_id
```

rendered: the withheld table's existence, its physical name and its join key, in the prompt. A
bare name is now matched against every withheld table's bare name lake-wide, so two schemas each
holding a `customers` withhold both bare-spelled joins when one is withheld. That is a false
refusal and it is the deliberate direction — an endpoint whose schema nobody wrote down is
undecidable, and the two readings are "withhold something the principal may read" and "name a
table they may not".

The same review of the rule found the exemption for **terms** to be wrong. The list said a term
"names no table", which is true and beside the point: `_structural_line` renders
`binding=<target id>`, and a binding's target is usually a *column*, which a denial withholds. A
term bound to `sales.customers.email` spelled the denied column into the prompt under a business
phrase. A term whose binding target is withheld is now withheld; one whose target survives renders
as before. `few_shot` stays exempt — its `sql` is not parsed here, and that is recorded rather
than decided.

#### 8.5 The HTTP corpus surface — `api/visibility.py`

Every browse route read `session.assets_by_id` directly and `api/routes.py` had no reference to
the access policy at all, so a deployment that set `GOVERNED_BI_ACCESS_POLICY` withheld a column
from the model and served it — with its `sample_values` — from `GET /corpus/rows?type=column` and
`GET /schema/{table_id}`.

**Filtered rather than declared out of scope, and the argument is the shipped configuration.** The
honest alternative was to narrow this ADR's claim and name the browse surface as excluded. It was
rejected on three grounds. The hole is reachable with a supported configuration and no fork, on
this tree, today. It serves *values* (`sample_values`) and not only metadata, so "a statement-level
control does not cover a catalog" does not reach it. And the grant is a process constant here —
one principal — so the filter is a `frozenset` subtraction and not a per-caller authorization
system, which is the thing this ADR does not build.

`api/visibility.py::visible(session)` returns a read-through view whose `assets_by_id` and
`CorpusStructure` are narrowed by the *same* `withheld_by_grant`, and every corpus-projecting route
reads through it. Three details are load-bearing:

- **`structure` is narrowed too.** `/graph`'s edges come from `CorpusStructure.join_edges`, which is
  keyed on table asset ids and is not the asset map — filter the map alone and the withheld table
  loses its node while an edge keeps its id in `target`.
- **Surviving assets are rewritten.** `browse.row_for` projects every dataclass field, so
  `/corpus/rows?type=table` returned `columns: ["sales.customers.email", …]` for a table whose
  `email` column is denied. `TableAsset.columns`, `MetricAsset.dimensions` and
  `ColumnAsset.references` are filtered.
- **Under the open grant `visible` returns the session itself** — not an equal copy — so the
  default path pays one boolean and no response can differ.

The control is a **sweep**, not a list of assertions: `tests/api/test_the_browse_surface_respects_
the_grant.py` calls every corpus route under a restrictive grant and searches the whole JSON body,
recursively, keys and values, for any spelling of the withheld table and column — and calls each
one under the open grant and requires the identifier to be *present*, so a route that returns
nothing cannot pass. A per-route assertion would only ever cover the routes whoever wrote it
thought of, and the surface that produced this finding is exactly the one nobody thought of.

**The one exemption, declared:** `/audit/corpus`'s `problems` are carried verbatim. `servable` is
`not fatal_problems`, so filtering a curation defect that happens to name a withheld table would
let an unservable corpus read as servable — trading a health signal the operator needs for a
disclosure they already have, since the same strings are on the server's stdout at startup. It has
its own test so the exemption is a declaration rather than something a reader discovers.

#### 8.6 The folding wrinkle, both halves

The paragraph that used to sit here described one half and asserted "it fails closed". It did, and
**the adjacent line failed open**. `may_inspect_schema` compared a raw licensed key against a
folded grant — a false refusal on a mixed-case corpus. `may_sample` compared a raw asset id against
a folded *denial* set, so `may_sample('sales.hr_notes.Employee_Note')` returned `True` under a
grant denying exactly that column. Mitigated, because `check()` folds both sides and refused the
statement — but `bounds.py`'s own "same answer twice, one turn earlier, **no ledger row spent**"
was false: the row is spent. A column id is model-supplied, so its spelling is the attacker's
choice, which is what makes this a bound and not a typo.

Both keys are now folded through the same `identifiers` functions `resolve_grant` folds the grant
with, and an unfoldable key falls back to itself rather than raising — this is a bound, not a
parser. What survives is the *slug* divergence, not the case one: where `slug()` fired, a table's
asset id is not its `{schema}.{physical_name}`, so the grant predicates answer about a key the
operator never wrote. `ToolBounds.withheld` is the authoritative answer for exactly that reason —
it is computed from the corpus, by the caller that has one.

Relatedly, `ToolBounds.licensed`'s docstring said it held "table keys
(`{schema}.{physical_name}`)". It holds **asset ids**, and has since `connect` wrote it. That
sentence is the root of this section: it is what made a raw-key comparison look correct.

#### 8.7 What is deliberately still true

- `licensed` is **not** narrowed by the grant — the rejected alternative below explains why.
- `run_query` still returns the verdict's `detail` to the model, so a refused agent is told the
  table exists and is denied. Open question 4.
- Nothing tests a real corpus's false-refusal rate under a real grant. Acceptance criterion 12.
- The seam withholds **assets**, never **values already read**, never **rows**, and never the
  prose of an answer built from data the principal may see.
- `/audit/turns` and `/audit/turns/{id}/trace` are not filtered. They project turn records —
  since 2026-08-18 out of LangGraph thread state rather than out of a JSONL log
  ([ADR 0014](0014-one-conversation-store.md)) — whose rows are turns that were already governed
  at serve time. Audit A7 once put a
  transport key in front of them; that key was removed on 2026-08-13, so today these two routes
  are unfiltered *and* unauthenticated — the grant narrows what a turn may read, and narrows
  nothing about who may read a turn that already ran. The change of store widened the *other*
  leak beside them: `values` and `get_state` on the platform's unauthenticated `/threads` routes
  now carry every prior turn's record rather than the newest one.

### 9. The adversarial suite

The suite (`govern/adversarial.toml`, ADR 0006's OQ3 instrument) gains an `authorization` family
rather than folding into `table` and `column`, so the report's family table shows how thick the
new half is instead of hiding it inside the licensing counts.

The world gains an `authorized` set, a `denied_columns` set and two row predicates, and **the
loader refuses a world that authorizes every table it licenses** — the same argument that already
forbids a world licensing every table it declares: an access layer that authorized everything
would otherwise score perfectly. Four new fictional tables carry the whole new dimension, so not
one pre-existing expectation was edited.

Measured, 2026-08-12, offline and deterministic:

| | before | after |
|---|---|---|
| cases | 95 (49 attack / 46 benign) | **115 (62 attack / 53 benign)** |
| bypass rate | 0/49 | **0/62** |
| misattribution rate | 0/49 | **0/62** |
| false-refusal rate | 0/46 | **0/53** |
| guardrail errors | 0/95 | **0/115** |
| TABLES recall | 6/6 | **15/15** |
| COLUMNS recall | 7/7 | **11/11** |

#### 9a. Disclosure probes — the half a statement cannot express

Every one of the three defects §8.4a and §8.5 fix was found by review and **none of them is
SQL-shaped**, so the suite as it stood could not have caught any of them and would have let all
three back in. A `[[case]]` asks whether a statement was refused; a `[[probe]]` asks what the
principal was *shown*.

Twelve probes over four surfaces, against the same world the cases read: the rendered context
block, `inspect_schema`, `read_body`, and the `may_sample` bound. Seven attacks and five controls,
and both halves are required per surface — a renderer returning nothing scores a perfect
disclosure rate, exactly as `def check(...): return {"passed": False}` scores a perfect bypass
rate on attacks alone. The control that matters most is
`p_benign_inspect_schema_of_a_same_named_column_that_is_not_denied`: the world's `sales.leads`
declares a column with the same bare name as the denied `sales.hr_notes.employee_note` and the
grant does not deny it, so a filter keyed on bare names fails there. B5's lesson, on the
disclosure surface.

| | measured 2026-08-12 |
|---|---|
| probes | **12 (7 attack / 5 benign)** |
| disclosure rate | **0/7** |
| over-withheld rate | **0/5** |

Reverting each of the four fixes in turn makes exactly the probe written for it report
`disclosed`; verified by hand, one mutation at a time.

Three joins/terms the probes need are declared as `[[probe_asset]]` rather than in
`[world.tables]`, so no `[[case]]` names them, `check()` never sees them, and not one
pre-existing verdict moved.

**The runner is in `tools/govern_bench.py` and not in `govern/adversarial_run.py`**, which is a
layering fact and not a preference: a probe must call `serve/context.py::withheld_by_grant` and
`serve/fetch.py`, both above `govern/` in `tools/check_imports.py`'s order. A tool is above every
layer. The cases stay data in `adversarial.toml`, so the driver and
`tests/govern/test_adversarial_suite.py` still read one file.

The case that matters most is `a_authz_licensed_but_not_authorized`: a licensed, unauthorized
table must refuse, **and must refuse under `r_table_not_authorized`**. The suite measures
misattribution separately from bypass precisely so that refusing it as `r_table_not_licensed`
fails — that is a permission decision blamed on the router, and a gate asking only "was it
refused" would report the new rule as working after it had been deleted.

---

## Rejected alternatives

**Filter `licensed` by the grant at `connect`, and change no layer.** Cheapest by far, and it
makes `r_table_not_authorized` unreachable — which is the objection. Every authorization refusal
would then be reported as a retrieval miss, so the refusal histogram would say the opposite of
what happened and §4.2 would be worse off than before. Narrowing the *rendered context* is right
and is §8's item 4; narrowing the *enforcement set* is the conflation this ADR removes.

**A `grant=` keyword on `check()` and `prepare()`.** ADR 0008 D7 is the argument against, in this
repository's own words: "an optional control argument is a control that will be un-wired" — which
is what happened to `spellings`, whose only production caller omitted it until 610 mixed-case
columns had failed after a passing verdict. A required keyword instead would change every call
site including tests, for a value that is constant for a turn. `GovernancePolicy` already threads
everywhere and is already "the security configuration of one turn".

**Three getters on the port.** §2.

**A restrictive default — deny unless granted.** Correct for a product, wrong for this repository:
it would move every number in `runs/`, retire the v4 arm as a control, and do it for a deployment
that has exactly one principal who is the operator. The default is open and `policy.py` asserts it
at import so the change is deliberate when it comes.

**A `tenant` field on `Principal`, "for later".** A field nobody sets is exactly what
`check_declared_is_consumed` exists to find. A fork that needs one adds it in the same commit as
the thing that reads it.

**Injecting the row predicate.** §5.

**Declaring the row predicate and ignoring it.** §5. A hook that looks enforced is a security
defect; one that says it is not enforced is a seam.

**A knob for the grant digest, now.** §7.

---

## Consequences

**What this buys.** A fork has one interface to implement and a worked example of implementing it.
The layer stack can say "you may not" in a reason code that the ledger records per attempt and
`tools/datalake_report.py::_refusal_layers` counts by layer and rule, so §4.2's abstention
accounting can separate "retrieval missed" from "you may not" without a second implementation. PII
gets a per-principal control that is not a corpus edit, and — as of §8.4a/§8.5 — one that withholds
the column rather than only refusing statements that name it. RLS gets a declaration that cannot be
mistaken for an enforcement.

**What it costs.** Two more rules in the TABLES layer's hot path and one in COLUMNS, each a
frozenset membership test against a cached resolution. One pass over the corpus per
corpus-projecting HTTP request **under a restrictive grant only** — `withheld_by_grant` returns on
its first line when the grant is open, which is what this repository ships and what every artifact
in `runs/` was measured under. That cost is uncached deliberately: it belongs to the deployment
that asked for it, not to an invalidation nobody would own. And a second authorization vocabulary
that readers must keep apart from `excluded` (corpus-wide) and `licensed` (per-turn) — §3 and §4
are written to be the place that distinction lives.

The reference adapter **has a caller in `src/`**: `api/graph_app.py::access_policy_from_environment`
calls `StaticRoleAccessPolicy.from_toml` when `GOVERNED_BI_ACCESS_POLICY` is set. This paragraph
said it had none, which was true the day §8's wires were designed and false the day they landed —
the same class of stale note as `govern/access.py`'s "not imported by `api/` today".

**What it does not cover.** Authentication, tenancy, per-caller tokens (audit B1 stands unfixed
and still says why; A5 closed and A6 retired on 2026-08-18 with the route it named, and neither
was closed by this seam). Masking or obfuscating a value that was legitimately read. Row-level
predicates (§5: declared, refused, never applied). Indirect disclosure through an answer's prose.
The curation problem strings on `/audit/corpus` (§8.5). The turn-record routes under
`/audit/turns` (§8.7). It is **not** true that the seam covers nothing in `serve/` or `api/`; that
sentence stood here while §8 opened "enforced end to end", and §8.7 is the replacement for both.

---

## Acceptance criteria

**Met on this tree (2026-08-12):**

1. `GovernancePolicy().access_grant.is_open`, asserted at import in `govern/policy.py`.
2. Under the open grant every predicate of `ResolvedGrant` is a constant function, so the three
   new branches are unreachable — `tests/govern/test_access_seam.py::
   test_every_predicate_of_the_open_grant_is_a_constant_function`.
3. All 95 pre-ADR adversarial cases produce byte-identical verdicts — `passed`, `failed_layer`,
   `layers_evaluated`, `reason_code`, `detail`, `bound`, `Prepared.sql` and the canonical string —
   before and after, under the expanded world *and* the world's restrictive grant.
4. The default policy, an explicit `OPEN_GRANT` and `OpenAccessPolicy().grant_for(LOCAL_PRINCIPAL)`
   agree on every case, **with a positive control** that a restrictive grant moves one.
5. A licensed-but-unauthorized table refuses as `r_table_not_authorized`, and the suite's
   misattribution rate stays 0.
6. `prepare()` produces no executable string for any authorization refusal.
7. Both adapters satisfy `ports.AccessPolicy` by `isinstance`.
8. Suite rates unchanged in kind and larger in denominator: 0/62 bypass, 0/62 misattribution,
   0/53 false refusal, 0/115 guardrail errors.

**Met on this tree (2026-08-12, second pass — §8's wires):**

9. §8's four wires, all four. `tests/serve/test_the_access_seam_reaches_the_served_app.py` runs
   every claim through `build_serve_graph` — the topology `langgraph.json` loads, with `accept`
   in front and `record` behind. A licensed, unauthorized table refuses as
   `r_table_not_authorized` at `TABLES` with `executed_sql: null`, and the **paired open-grant
   run of the same statement executes it**, which is what rules out the refusal being a
   retrieval miss wearing a permission's name.
10. §7's knob, resolved from the policy. Two runs under different grants publish different
    digests; the register's default is `None` and is asserted not to be the open grant's.
11. The default path is unchanged, proven the way the first pass proved it for `govern/`: the
    default `GovernancePolicy()`, an explicit `OPEN_GRANT`, and
    `OpenAccessPolicy().grant_for(LOCAL_PRINCIPAL)` produce **the same turn record field for
    field** — including `context_hash`, `delivery_hash`, `execution`, `licensed` and
    `knobs_resolved` — over the served graph. Seven mutations against the four wires, seven
    caught.

**Met on this tree (2026-08-12, third pass — the review of the second pass):**

12. **`inspect_schema` names no denied column of a table it may inspect.** Driven through
    `build_serve_graph` with a model that calls the tool, paired against an open-grant run that
    must return the column — so a fix returning nothing would fail. Probe
    `p_inspect_schema_names_a_denied_column` in `govern_bench.py`.
13. **Neither spelling of a join to a withheld table survives**, and the corpus under test carries
    both, over one table pair, differing only in qualification. Same for a term whose binding
    target is denied.
14. **A restrictive grant with no disclosure set does not construct.** The wiring failure that
    produced 12 is unrepresentable rather than tested for.
15. **`may_sample` refuses a denied column spelled in mixed case**, and the licensed key is spelled
    in the corpus case so the licence check cannot refuse first and pass the probe for free.
16. **No corpus-projecting HTTP route names a withheld asset**, swept over the whole JSON body of
    every such route, keys and values, at any depth — with the open-grant control on each.
17. **12 disclosure probes: 0/7 disclosed, 0/5 over-withheld.** Four mutations, one per fix, each
    caught by the probe written for it.

**Owed, and not claimed:**

18. A false-refusal number for the denial rule against a *real* corpus. The 53 benign cases and the
    5 benign probes are a fictional world; the shape of the trade is measured, its magnitude on
    real traffic is not.
19. **The slug divergence.** Where `slug()` fired, a table's asset id is not its
    `{schema}.{physical_name}`, so `ToolBounds`'s grant predicates answer about a key the operator
    never wrote. `withheld` covers it for disclosure; the *bounds'* own answer is still a false
    refusal there, and no test drives a slugged corpus under a listed grant. The case half of the
    wrinkle — mixed case — is closed by 15.
20. **No fork has run this.** Every number above is from a fictional world and a scripted model.

---

## Open questions

1. **Should `licensed` be renamed?** It now means "what retrieval found", which is not what the
   word says, and `authorized` is the word a reader reaches for. A rename touches `ServeState`,
   the record register, every artifact in `runs/` and every comparison against them. Deferred, and
   named here so the next reader knows it was considered rather than missed.
2. **Does the denial rule earn its place beside `excluded`?** A fork could express every denial as
   a per-principal corpus view instead. That is one corpus per principal, which is a corpus-identity
   problem (`corpus_content_hash` is the treatment identity), so probably not — but nobody has
   built the alternative to find out.
3. **What is the false-refusal rate of a real grant?** §2's whole argument is that the trade is
   only honest if both sides are measured, and only one side is.
4. **Should `r_table_not_authorized` be surfaced to the model at all?** Today `run_query` returns
   the verdict's `detail` to the agent, which tells it a table exists and is denied. The
   alternative is `OUT_OF_SCOPE_MESSAGE` to the model and the true rule to the ledger — cheap, and
   it needs a decision about whether the model should be able to *say* "you are not authorized" to
   the analyst.
