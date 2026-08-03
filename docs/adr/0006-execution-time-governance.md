# 0006: Execution-time governance

- **Status:** Proposed (design; 2026-08-02, second draft — the first was
  reviewed and had four holes of the same class it was written to close). No
  code written. A hard dependency of
  [ADR 0005](0005-v2-memory-layer-and-faceted-retrieval.md) and a precondition
  of the `v2` branch's first (deleting) commit.
- **Deciders:** project owner + design session (2026-08-02)
- **Scope:** everything between "the agent produced a string" and "the database
  saw a statement" — the layer stack, the function allowlist, identifier
  handling, scope resolution, graded delivery, the input guard, tool bounds, the
  connection contract, path validation, and the audit ledger. **The memory
  layer, retrieval and the serve graph are 0005.**
- **Related:** [lessons-from-v1.md](../lessons-from-v1.md) §4 is the evidence
  base for nearly every decision here; cited as **L§4**.
  [0002](0002-governed-agentic-serve-runtime.md) established the shape.
- **Supersedes:** ADR 0002's safety spine. It **amends invariant 1** ("the
  refuse gate runs before the agent") — 0005 ships `negative_gate` disabled
  until a negative corpus exists — and **overrides 0002's line 49**, which
  documents "graded delivery re-checks with `allowed_tables=None` and so skips
  L4" as an accepted convention. That convention is bypass 4 below.

---

## Context

### Why this is a separate ADR

**It is the part with an adversary.** Every other v2 decision trades against
cost or recall. These trade against an attacker, or against a model that will
eventually emit the one statement nobody anticipated.

**Its lifecycle is unrelated to retrieval's.** The layer stack does not change
when the index changes.

**v1 proved the failure mode of not having it.** ADR 0002 stated the shape and
contains none of the fixes below — worse, it documents one bypass as an accepted
convention. Those fixes lived only in code about to be deleted.

### The bypass list

**This list is canonical.** 0005's step-11 gate and this ADR's acceptance suite
both refer to *this* section; the first draft had them citing two different
sets. One test per item, each demonstrating the v1 chain and its refusal.

| # | bypass |
|---|---|
| **B1** | **Column-less function calls.** `SELECT pg_read_file('/etc/passwd')` references no table and no column, so the column and table layers are structurally blind. A read-only connection does not help — these are reads. The Postgres XML-export family (`query_to_xml`, `table_to_xml`, `schema_to_xml`, `database_to_xml`, `*_xmlschema`, `*_and_xmlschema`) takes its target as a **string literal**, so sqlglot parses it as `exp.Anonymous`; one call dumps a whole table. `setval`/`nextval` are SELECT-shaped write primitives |
| **B2** | **Whole-row aggregates.** `json_agg(t)`, `row_to_json(t)`, `array_agg(t)`, `to_jsonb(t)`, `count(t.*)` each emit every column of a row — including excluded and suspect ones — while producing **zero `Column` nodes** for them. Same structural blindness as B1, reached through functions any analytic allowlist would naively permit. *(Not a v1 incident: found reviewing this ADR's own first draft, whose allowlist admitted it.)* |
| **B3** | **The attempt cap let unvalidated SQL reach the database** (Audit Vuln 2). On cap the middleware wrote a ledger entry *before* `check()` ran, so it carried no layer; graded delivery read `failed_layer=None`, treated it as non-hard, and re-executed SQL that had cleared **no layer at all**. Confirmed chain: three attempts blocked at the column layer, the fourth capped, card-number SQL would have reached the gateway |
| **B4** | **`if allowlist is not None`** wrapped the pre-execute recheck, so a missing argument fell through to `gateway.execute`. The guard added to make the path defence-in-depth had removed the only authorization on it. Sibling: omitting `allowed_tables` skipped the table layer entirely, and the column allowlist is lake-wide, so SQL against an un-routed schema cleared it and never met a table-scope gate |
| **B5** | **Case folding.** Postgres folds unquoted identifiers, so `customerid` clears a `CustomerID` allowlist — and quoting the model's spelling then sends the engine a column that does not exist |
| **B6** | **Reference shapes.** Three-part `schema.table.column` slipped past the column layer (the key *is* in the lake-wide allowlist) and the table layer (which inspects only FROM sources). Siblings: star projections, `NATURAL JOIN`, bare columns in a mixed base+derived scope, and a bare name matching a `suspect` column in **any** in-scope base (leftmost-table resolution binds it to the decoy) |
| **B7** | **The agent grew its own authorisation set.** `inspect_schema` wrote straight into the licensed set, so inspecting anything authorised it — reaching into unrelated schemas in a pooled corpus |
| **B8** | **`asset.schema` escaped the corpus root.** The write directory is derived from it while `is_valid_id` guards only the asset id. The regex must be `\A...\Z`: Python's `$` also matches before a trailing newline, so `"beer_factory\n"` passes a `^...$` validator that then names a directory. **Recategorised 2026-08-03: this is accident prevention, not an attack defence.** 0005 §1.6 fixes the trust boundary — the corpus is trusted, the incoming question is not — so the threat here is not a hostile author. It is that `POST /corpus/edit` writes without a PR, so a mistyped field or a UI bug concatenating paths reaches the filesystem from a *trusted* author. The validator is kept because it is cheap and refuses rather than edits; only its justification changed. `SchemaAsset.name` being first-class and the corpus being partly model-authored are reasons the *path* is reachable, not reasons to distrust the author |
| **B9** | **A guessable `thread_id` was a handle on another caller's paused clarification**, which embeds their question. Namespacing is a mitigation, not authentication |
| **B10** | **The routing index embedded governance-excluded PII columns** while the picker summary filtered them — two definitions of "excluded" that drifted, because the caller contract was documentation rather than a type |

Two more that are not bypasses but are the same class:

- **The `sample_rows` PII filter had never executed in any test**, because every
  test passed the already-filtered corpus. The guard was only exercised behind
  another guard.
- **A `NameError` in the note-withholding predicate shipped**, invisible because
  `any()` short-circuits on an empty token set — so it fired only on corpora
  with an excluded column, and the rails laundered it into an unremarkable
  `model_error` refusal.

---

## Decision

### 0. Four invariants

> **G1. Absence refuses.**
> Every security parameter is required. No code path where a missing argument
> means "skip". A function that cannot evaluate its own precondition returns
> `blocked`.
>
> **G2. Every executor is enumerated, passes `check()`, and writes a ledger
> entry.**
> Not "one choke point" — that was aspirational and the first draft contradicted
> it in its own tool table. There are four executors (§7); each is named, each
> is checked, each stamps its `path`.
>
> **G3. Permission is proven, never inferred.**
> `failed_layer=None` never means safe. Graded delivery keys on a positively
> established verdict field, not on the absence of one.
>
> **G4. The string checked is the string executed.**
> Three transformations act on a statement — canonicalisation, checking, row-limit
> injection. §3 fixes their order and the ledger records a hash of the exact
> executed text.

G1 answers B3, B4. G3 answers B3. G4 answers B5.

### 1. The layer stack

```python
class Layer(IntEnum):
    PARSE      = 1   # single read statement, parses under the dialect
    NO_WRITE   = 2   # no write / DDL / transaction-control constructs
    FUNCTIONS  = 3   # every function call is permitted (§2)
    BINDING    = 4   # every reference binds to exactly one in-scope source (§4)
    COLUMNS    = 5   # every bound column is allowed and not excluded
    TABLES     = 6   # every base table is in the licensed set
    COST       = 7   # cost / shape estimate
```

**An ordered `IntEnum`, not strings, and no fractional members.** The first
draft bolted the function allowlist on as "L1.5", which is unrepresentable in a
`Literal["L1"…"L5"]` verdict — so a block by the highest-value layer in the
stack would have had to be reported as `None`, reopening B3 through the front
door. Ordering is load-bearing: `check()` returns on first failure, so reaching
layer N is a proof that 1..N−1 passed, and §5 expresses its rule as a
comparison.

```python
def check(sql: str, *, corpus: AnalystCorpus, licensed: frozenset[str],
          default_schema: str, dialect: str, policy: GovernancePolicy) -> Verdict

class Verdict(TypedDict):
    passed: bool
    failed_layer: Layer | None          # None ⟺ passed
    layers_evaluated: list[Layer]       # a layer with no entry did not run
    reason_code: str                    # closed vocabulary — see §9
    detail: str                         # free text, ledger-dropped
    bound: dict[str, str]               # each reference → the source it bound to
```

**Every keyword is required** (G1). The first draft passed a
`column_allowlist` set, which cannot answer the questions §3 and §4 ask —
"is this table a base source *in this scope*", "is this bare name ambiguous
*corpus-wide*", "what is the corpus's declared *spelling*", "is this column
`suspect`". Those need the resolved corpus, so `check()` takes **the same
`AnalystCorpus` type 0005 §1.5 makes authoritative**. This also removes the
lake-wide column allowlist that made B4 exploitable: with a scope-derived
binding (§4) the column layer no longer needs a corpus-wide set at all.

`GovernancePolicy` carries the knobs (§10), including `hard_block_suspect`,
which v1 had and the first draft dropped — dev and BIRD hard-block, production
soft-warns.

`layers_evaluated` distinguishes "ran and blocked nothing" from "never ran". A
test asserts `failed_layer` is the last element of it.

**Any exception inside `check()` is `passed=False`** — `RecursionError` from
pathological nesting and tokenizer errors from unterminated literals both
escaped v1's parse layer. But see §9: a swallowed exception must also be
*counted*, or a systematically broken `check()` presents as an arm that refuses
everything and passes every quotability gate.

**Instrumentation on the safety path fails open with a warning, once per
process; the safety path itself fails closed and always returns a verdict.**

### 2. The function allowlist

v1 used a denylist and its own code recorded the verdict: *"a positive allowlist
of permitted functions would be more robust and is a deferred follow-up."* v2
ships it. A denylist is unwinnable — the XML-export family alone had eight
spellings.

**It is a committed literal list keyed on sqlglot expression classes, against a
pinned sqlglot version.** Not a category description. The first draft said
"aggregates, window functions, string/date/numeric scalars, `CASE`/`COALESCE`/
`NULLIF`, casts, and the dialect's standard set operations" — three defects, and
the second is a live bypass:

1. Set operations are not function calls (`exp.Union` is not `exp.Func`), which
   proves the list was never enumerated.
2. **"Aggregates" admits B2.** `json_agg`, `array_agg`, `row_to_json`,
   `to_jsonb` are aggregates and each dumps a whole row with no `Column` nodes.
3. "Every function call" is ambiguous against sqlglot: matching only
   `exp.Anonymous` and matching all `exp.Func` are different allowlists, and
   `CASE`/`CAST` are typed nodes, not `Anonymous`.

**The rule, precisely:**

```
FUNCTIONS layer:
  for every exp.Func node (typed subclasses AND exp.Anonymous):
      canonical name (schema-qualification stripped: pg_catalog.setval → setval)
      must be in PERMITTED_FUNCTIONS
  for every argument of any function:
      a bare table alias, or alias.*, refuses          # closes B2
      exception: count(*) exactly
```

Two CI assertions, in **both** directions:

- **Not too narrow:** every function appearing in the gold SQL corpus is
  permitted, or explicitly recorded as intentionally absent with a reason.
- **Not too wide:** `PERMITTED_FUNCTIONS ∩ ADVERSARIAL_SET == ∅`, where
  `ADVERSARIAL_SET` is a committed fixture — the XML-export family,
  `pg_read_file`, `pg_ls_dir`, `lo_import`/`lo_export`, `dblink*`, `pg_sleep`,
  `setval`/`nextval`, `json_agg`, `array_agg`, `row_to_json`, `to_jsonb`.

The first draft had only the narrowness test, so a developer widening the list
to make a gold query pass would have satisfied it.

**`false_refusal_rate` is the required companion metric.** A positive allowlist
trades false refusals for closed holes; the trade is only honest if the other
side is measured. Two details from v1: `version()` needs its canonical name
`current_version` matched, and the date/time `current_*` family is legitimate
analytics and must be **in** the list.

### 3. The transformation pipeline

Fixed order, stated once (G4):

```
1. normalise    NFKC, after the control-character check in §6, never before
2. canonicalise rewrite each identifier to the corpus's declared spelling
3. check()      §1
4. limit        min(existing_limit, max_rows + 1) at the statement root
5. execute
6. ledger       records sha256 of the exact string sent at step 5
```

**Canonicalisation precedes checking**, so the verdict is about the statement
that runs. The first draft left the order unstated and claimed canonicalisation
was "cosmetic-but-recorded, never a control" — which is false: an ambiguous fold
(two corpus columns differing only by case) left unrewritten is folded by
Postgres to one of them, possibly the **decoy**, so the column layer approves one
binding and the engine reads another. That is B6's risk arriving through the
mechanism the draft declared harmless.

**Ambiguous folds refuse.** They are rare, and the alternative is silent
mis-binding. Unknown identifiers (model-invented aliases, CTE names) pass
through untouched — they are resolved by §4's binding rule, not by spelling.

**The row limit is `min(existing, max_rows + 1)`, and a parse failure at step 4
refuses.** v1 left the limit unchanged when a `LIMIT` already existed, so
`LIMIT 100000000` defeated the cap; and it left it unchanged on parse failure,
on a path that also serves executors where `check()` never ran.

### 4. Binding — one positive rule, six tests

The first draft listed six fail-closed shapes. That is a denylist of shapes in
the document that argues denylists are unwinnable, and I can construct shapes it
misses (two-part `unknown.col`; whole-row references; `SELECT *` inside a
derived table consumed by a whole-row function; table functions in `FROM`
position, which produce no `exp.Table` and are invisible to the table layer).

**The rule:**

> Every `Column` node, every `USING`/`NATURAL` join key, and every `FROM` source
> must bind to **exactly one** base source in its own scope, or in a named
> ancestor scope for correlated references. Binding to zero sources refuses.
> Binding to more than one refuses. The binding is recorded in `Verdict.bound`.

Everything downstream reads `bound`, so the column layer and the table layer
each have exactly one input and cannot disagree about what a reference means.

Mechanics that v1 paid for:

- **Per-scope resolution via `traverse_scope`, never a query-wide name map** — a
  CTE named after a base table deferred that table's excluded column.
- **Iterate every `Column` node in the statement, not `scope.columns`** — the
  latter omits bare `HAVING` references.
- **A separate pass for `USING (col)` and `NATURAL JOIN` keys** — they are not
  `Column` nodes and a `find_all(exp.Column)` sweep never sees them.
- **`NATURAL JOIN` refuses outright** — it joins on every common column, which
  is unenumerable, so no binding exists.
- **`SELECT *` and `t.*` refuse in a projection** — the allowlist cannot vouch
  for columns a query never enumerates. `count(*)` is the carve-out, handled in
  §2 rather than here.
- **A bare name that would bind to a `suspect` column in any in-scope base
  refuses** under `hard_block_suspect`, warns otherwise.

The six shapes stay as the **test list**, not the specification.

**Keys.** The table layer keys on `{schema}.{physical_name}`; the column layer
keys on `{schema}.{table}.{column}`. The first draft claimed one uniform
two-part key "everywhere", which would make two tables in one schema both having
an `id` column a corpus validation error — i.e. every corpus. What *is* a corpus
validation error is two assets sharing `(schema, physical_name)`, because the
table key would be ambiguous.

**Bare table names:** qualified always resolves; bare resolves **only** when
exactly one table corpus-wide carries the name; ambiguous resolves to nothing
and therefore refuses. `default_schema` supplies the qualification for
unqualified `FROM` when the datasource pins one — without it every unqualified
statement would false-refuse.

### 5. Graded delivery

Some SQL is executed and returned marked "unverified" rather than refused.

```
entry:    failed_layer == Layer.COST      →  eligible
recheck:  failed_layer == Layer.COST      →  execute, marked unverified
          anything else                   →  refuse
```

**Only `COST`.** The first draft wrote `{TABLES, COST}` — copied from v1's
*entry* set without noticing that v1's **recheck** forgives `COST` only, with
the comment *"an L4 failure means unauthorized base tables and must refuse"*
(`governance.py:765-772`). And the redefinition made it worse: v1's table-ish
layer was `term_semantics`, a curated-semantic check; v2's `TABLES` is pure
authorization. §5's own argument for excluding the column layer — *"it is a
confidentiality control, not a semantic one"* — applies verbatim to it. Under
the first draft's rule, a pooled 57-schema deployment would execute SQL against
unlicensed tables and show the analyst the rows.

Reaching `COST` is a proof minted by `check()` that PARSE, NO_WRITE, FUNCTIONS,
BINDING, COLUMNS and TABLES all passed. **Everything else hard-refuses** —
including any entry that never earned a verdict: cap, error, exhausted,
no-coverage and missing-pass-result all carry `failed_layer=None`, and treating
that as forgivable was B3.

Three structural requirements:

- **The pre-execute recheck always runs**, with a full license. No
  `if allowlist is not None`.
- **The cap terminates the turn**, it does not decline a call. v1's cap returned
  a "capped" tool message and the agent kept going, burning unbounded round-trips
  against a cap it could never clear.
- **The cap's ledger entry is written after `check()`**, so it carries a layer.
  Writing it before produced the `failed_layer=None` that B3 walked through.

**A cap-terminated turn gets its own `Outcome` member**, distinct from `crashed`
and from a model refusal — 0005's node wrapper would otherwise stamp
`Outcome.crashed`, which is the inverse of the defect that retired the
pre-2026-07-25 numbers.

> OQ4 asks whether this path earns its complexity at all. With the rule narrowed
> to one layer, deleting it is a small change.

### 6. `guard` — the input gate

0005 places `guard` first and specifies it calls no model. The rules live here.

```python
class GuardVerdict(TypedDict):     # 0006 owns this type; 0005 imports it
    outcome: Literal["clear", "blocked", "error_failed_open"]
    rule_id: str | None
    detail: str | None             # ledger only — never surfaced
```

| rule | blocks |
|---|---|
| `g_encoding` | control characters, bidi overrides, zero-width sequences — **before** NFKC normalisation, or normalisation hides them |
| `g_instruction_override` | imperative patterns aimed at the model |
| `g_role_injection` | role/turn markers that would forge a message boundary |
| `g_tool_forgery` | text shaped like a tool call or tool result |
| `g_length` | input above a hard character bound |

**`guard` runs twice: on `question`, and again on `rewrite.after`.** 0005's
`rewrite` is a model call with unguarded history in scope, and every downstream
node reads its output — so without the second pass **the guarded artifact is
never the delivered artifact**. The rules are deterministic and cheap; running
them twice costs nothing.

**Refusals return a fixed public string.** The `rule_id` is recorded in the
ledger only. Returning rule-derived text is a rule-probing oracle.

**Out-of-scope detection is deliberately not a `guard` rule.** v1's
keyword-and-Jaccard refuse gate tried exactly that and fired zero times in 5,404
rows. Semantic out-of-scope is 0005's `negative_gate`.

**A red-team corpus is a shipping requirement, not a follow-up.** A gate
measured only against benign traffic is v1's refuse gate with a new name. The
corpus must be **multilingual** — BIRD obfuscation is translation, the traffic is
not English-only, and English imperative patterns will fire at approximately
zero on it — and must include homoglyph and encoded variants. Both numbers ship:
recall on the red-team corpus, false-positive rate on real questions.

**Known gap, recorded not solved:** history contains the engine's own answers,
which contain data read from the database, so indirect injection through data
bypasses both guard passes. Closing it needs a defence at the data boundary.

### 7. The four executors

G2 enumerates them. Each passes `check()`, each writes a ledger entry stamped
with its `path`.

| executor | statement source | `path` |
|---|---|---|
| `run_query` | the model | `agent` |
| graded delivery re-execution | the model, post-recheck | `graded` |
| `sample_rows` | **constructed from corpus ids** | `sample` |
| the profiler / seed | constructed from the catalog | `profile` |

The first draft named `run_query` "the single choke point" while listing
`sample_rows` in the same table — which 0005 §3.5 calls *"the only path to real
values"*, i.e. it executes SQL. Its signature took a model-supplied `column`
**string**, and identifiers cannot be bound as parameters, so a hand-built
`SELECT {column} FROM {table}` was a direct injection surface with no parse
layer, no function layer, no column layer and no ledger entry.

```python
sample_rows(column_id: str, limit: int)   # a ColumnAsset id, not a name
```

The statement is constructed from the resolved asset, so no model string reaches
SQL. It still passes `check()` — it is one generated statement and trivially
checkable — and it still applies the exclusion/suspect filter **in the tool**,
tested with the outer corpus filter removed (v1's filter had never executed in
any test because every test passed the already-filtered corpus).

The profiler runs at seed time, on the same connector base class, with the same
session settings as §8 — including `synchronize_seqscans = off`, which 0005 §1.7
requires for reproducibility and which is therefore a property of the connector,
not of one code path.

### 8. Tool bounds and the licensed set

> **A tool that grants privilege must have a bound the model cannot widen.**
> (B7)

**`licensed: frozenset[str]` is an explicit output of 0005's `connect` node**,
carried in `ServeState`, closed for the turn. The first draft asserted it was
"produced by `resolve` and `connect`" while 0005 had no such field and used four
different names for what turned out to be four different sets. It is:

```
licensed = { table ids from facet hits }
         ∪ { table ids pulled in by resolve }      # join endpoints, few-shot SQL tables
         ∪ { Steiner points added by connect }
```

Explicitly **not** the post-budget `by_type["table"]` — budgets shape what is
*rendered*, and licensing what is *reachable*; a Steiner point must be licensed
or every multi-hop query refuses at the table layer, which is what `connect`
exists to prevent.

**`resolve` gets the same crossing accounting `connect` has.** Few-shot SQL
closure pulls in every table a gold statement touches, so without it a
`FewShotAsset` hit is an unbounded, unaudited licensing expansion in a pooled
lake — the same shape as B7.

| tool | bound |
|---|---|
| `read_body` | asset ids in this turn's `hits ∪ pulled_in` |
| `inspect_schema` | table ids in `licensed` |
| `sample_rows` | column ids whose table is in `licensed` |
| `run_query` | the table layer, against `licensed` |

**No tool writes to `licensed`.** A clarification resume continues from the
interrupt point (0005 §3.1) and therefore cannot widen it either.

**Out-of-scope and non-existent return the identical message**, so the model
cannot probe for existence.

**Every tool reads through `AnalystCorpus` as a type, not a convention** — B10
was two definitions of "excluded" drifting apart because the contract was a
docstring.

### 9. Path validation

B8, stated separately because it is the one bypass with no layer to live in.

Any string that becomes a filesystem path is validated **at the type boundary**
with `\A[A-Za-z0-9_][A-Za-z0-9_-]*\Z` — anchored with `\A`/`\Z`, never `^`/`$`,
because Python's `$` matches before a trailing newline and `"beer_factory\n"`
would pass a security-labelled validator that then names a directory.

Applies to `SchemaAsset.name` and any asset id used to derive a file path, at
**both** the parse boundary and the write site (defence in depth, deliberately
duplicated). The write site checks at string level and avoids
`os.getcwd`/`realpath`, which trip LangGraph's ASGI blocking-call detector.

### 10. The connection contract

**Read-only is enforced by the session setting, not the connection flag.**
Connections open `autocommit=True` so session `SET`s apply immediately, which
means `connection.read_only = True` only shapes a `BEGIN ... READ ONLY` that
never happens.

```
Postgres :  SET default_transaction_read_only = on
            SET synchronize_seqscans = off
SQLite   :  PRAGMA query_only = ON
```

**Both are belt-and-suspenders. Production must also connect through a
read-only database role** — an application bug should never be the last line,
and §2 exists precisely because read-only does not stop read-side exfiltration.

**The forced row limit lives in the connector base class**, not per connector —
v1 documented a gateway-wide cap and SQLite was the one path without it.

**Connectors are context-managed.** v1 left 131 unclosed SQLite handles across
the suite.

**`identity` is provenance, not enforcement**, named as such at the seam. v1
asserted RLS-as-user on four surfaces and had none. A toggle claiming to enable
it **raises at construction**.

**`thread_id` alone is not a capability** (B9). A clarification resume is bound
to `identity` and rejects a mismatch.

### 11. The ledger and redaction

**Every executor writes an entry** (G2), stamped with its `path`. v1's
graded-delivery path bypassed the middleware and produced answers whose record
showed a query that never happened.

**Retention is by vocabulary class, not by "drop every string".** The first
draft said "keeps numbers and drops every string" and four lines later "keeps
`columns` / `row_count`" — column names are strings, and dropping every string
also drops the statement, in a section whose stated purpose is that the record
must show what ran.

| field class | durable projection |
|---|---|
| closed vocabulary — `layer`, `passed`, `reason_code`, `path`, `rule_id`, `outcome` | **kept** (enums) |
| numbers — `row_count`, `truncated`, `ms`, `attempt` | **kept** |
| statement | **kept as `sha256` + a structural fingerprint**: the parsed AST with every literal elided |
| `detail`, libpq error text, `reason` prose, result rows | **dropped** |
| column names | **kept only as ids**, never as free text |

The structural fingerprint gives auditability — which tables, which shape,
which functions — without echoing literals. libpq embeds the offending statement
in error text (`LINE 1: SELECT ...`), which is why free text goes.

**One redactor, both sinks.** v1 had two sinks for the same record with
different policies, and the anonymously-reachable one used the weaker.

### 12. What the measurement layer must see

The first draft recorded nothing, which combined with §1's exception-to-block
rule to reproduce a v1 defect: a `NameError` in the function-layer walk turns
every turn in an arm into a refusal, `crash_rate == 0`, every register key
present, run declared quotable.

```python
class ExecutionRecord(TypedDict):     # total; written every turn, including "no SQL"
    attempts: list[AttemptRecord]
    terminal: Literal["answered", "graded", "refused", "capped", "no_sql"]
    guardrail_errors: int             # exceptions swallowed by check()

class AttemptRecord(TypedDict):
    verdict_layer: Layer | None
    passed: bool
    reason_code: str
    path: Literal["agent", "graded", "sample", "profile"]
```

`ExecutionRecord` goes in `ServeState` and in 0005 §4.1's record register.
**`guardrail_errors == 0` joins the quotability preconditions.**

### 13. Knobs

0005 §5 declares its table "the knob register", from which the manifest,
comparability keys and serve config hash derive. **These join it**, as a
declared 0006 section — otherwise two runs with different security configuration
hash identically, which is the strongest form of the arm confusion 0005 exists
to prevent.

| knob | default |
|---|---|
| `PERMITTED_FUNCTIONS` | committed list; **hashed by content** into the config hash |
| `sqlglot_version` | pinned; canonical names are release-dependent |
| `hard_block_suspect` | `True` in dev/BIRD, `False` in production |
| `graded_delivery_enabled` | `True` (OQ4 may retire it) |
| `run_query_attempt_cap` | 3 |
| `max_rows` | as today |
| `guard_rules_enabled` | per `rule_id` |
| `g_length_max_chars` | **8,000** — measured, see below |
| `cost_budget` | **unset**; ships disabled rather than guessed |

**`g_length_max_chars` is now measured, not TBD.** Across all 10,962 BIRD dev +
train questions:

| | min | p50 | p95 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|
| question | 23 | 75 | 135 | 180 | 255 | **325** |
| question + evidence | 32 | 163 | 329 | 440 | 609 | **906** |

Both fields matter because both reach the prompt. 8,000 sits **8.8x above the
longest input the corpus contains**, so the false-refusal rate on 10,962
questions is exactly 0.

State plainly what this does and does not establish. Any value ≥ 1,000 gives the
same zero on this corpus, so the measurement does not choose 8,000 over 2,000 —
it only rules out anything near the distribution. The guard's purpose is to stop
a paste-bomb or an injection payload, and how long a *legitimate* non-BIRD
question can be (a pasted table, a long business description) is a question BIRD
cannot answer. 8,000 is headroom against that unknown, and the number to revisit
is the observed max on real traffic, not this table.

**`Stage` members this ADR owns**, for 0005 §4.3's enum diff: `guard`,
`check`, `execute`, `graded_delivery`, `sample_rows`, `cap`. The first draft
never mentioned `Stage`, so 0005 would have frozen an incomplete enum.

---

## Consequences

**What this buys.** Ten bypasses close, including one (B2) found in this
document's own first draft. G1 makes "optional security parameter" bugs
unrepresentable. G2 replaces an aspirational single choke point with four named
ones, each checked. G4 means the verdict is about the statement that ran.

**What it costs.** A positive function allowlist will false-refuse until tuned —
hence the mandatory companion metric and the gold-coverage test. Per-scope
binding is more work per statement than a flat name map. `sample_rows` becomes
id-based, which is a small API change for the agent prompt.

**What it does not cover.** Row-level security and per-user identity (enterprise
fork). Indirect injection through data returned by the database (§6). Anything
above the statement — 0005 owns retrieval and the graph.

---

## Open questions

1. **What is the false-refusal rate of `PERMITTED_FUNCTIONS` on BIRD gold?**
   Free, no model. Run before enabling.
2. **Does `COST` earn its place?** v1's cost layer has no recorded instance of
   blocking something the other layers would have missed. A layer that never
   fires is indistinguishable from a layer that is not wired up. If it goes,
   §5's rule has no eligible layer and graded delivery goes with it.
3. **Does `guard` earn its false positives?** Each rule needs both numbers —
   red-team recall and benign firing rate — before it ships enabled.
4. **Does graded delivery earn its complexity?** It is the most intricate path
   in the stack and the source of the worst bypass. Measure how many turns it
   rescues; if the answer is small, hard-refusing everything is simpler and
   strictly safer.
5. **Does the corpus itself pass the gate?** `FewShotAsset.sql`,
   `MetricAsset.expression` and `JoinAsset.on` are copied into the prompt as
   authored (the corpus is trusted — ADR 0005 §1.6), and few-shot bodies are
   exemplars the model imitates. If gold-derived SQL contains `SELECT *` or
   `NATURAL JOIN`, the corpus teaches statements §4 refuses. Run every corpus SQL
   field through PARSE / FUNCTIONS / BINDING at corpus-validation time and report
   the conflict rate.

---

## Implementation order

**§§1–5 are model-free and are a hard prerequisite of 0005's step 9**, not step
11. The first draft placed the whole ADR after the serve graph, which would
leave the repository with no `check()` between the deletion at step 4 and step
11 — so step 9's cost gate would run either with `run_query` disabled (not
measuring what it argues about) or with an unguarded agent against Postgres.

1. `Layer`, `Verdict`, `check()` with all-required keywords, the
   exception-to-block wrapper, and `ExecutionRecord`.
2. PARSE / NO_WRITE / FUNCTIONS, with both CI assertions and a measured
   false-refusal rate on gold.
3. BINDING (§4) — the positive rule, with the six shapes as tests. **Each test
   drives the real gate function**, never a re-implementation of its arithmetic:
   v1's gold-gate tests re-derived `share > THRESHOLD` themselves, so deleting
   the gate, flipping the comparison and reversing the denominator all passed.
4. COLUMNS / TABLES over `bound`, plus canonicalisation and the §3 pipeline
   order.
5. The connection contract, the base-class row limit, path validation (§9).
   — *0005 step 9 unblocks here.*
6. Tool bounds and `licensed`, with the inner-filter-alone tests.
7. `guard` and the red-team corpus.
8. Graded delivery, last, because it depends on every layer verdict being
   trustworthy.
9. The ledger and the single redactor.

**Acceptance:** one test per bypass **B1–B10** in the Context section, each
demonstrating the v1 chain and its refusal. That suite is 0005's step-11 gate,
and it is the same list — the first draft had the two ADRs citing different
sets.
