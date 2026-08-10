# governed-bi

Ask a business question in English. Get an answer backed by read-only SQL, a record of how the
engine reached it, or a refusal naming the rule that stopped it.

governed-bi is a text-to-SQL engine for teams who have to know when not to trust the answer. It is
built on LangGraph and measured on an obfuscated 57-schema Postgres data lake.

---

## What is different here

This engine is built around the questions it should not answer, and three design decisions follow
from that.

### 1. The model has no database handle

There is no tool that executes arbitrary SQL. The connector is closed over inside `build_tools`,
so it never appears in the model's tool schema and never enters the message history. The agent
writes a statement and hands it to a tool body, which checks it before anything runs.

The governance boundary is a missing tool rather than a policy asking the model to behave. A
prompt injection can say whatever it likes; there is nothing on the other end to say it to. The
same property makes the boundary testable: `govern/`'s G2 invariant says every executor passes
`check()` and writes a ledger row, and the tests enumerate the executors, so adding one that
skips either fails the suite.

### 2. Retrieval is also the allowlist

The tables retrieval surfaces for a turn are exactly the tables the checker will permit for that
turn. One mechanism does both jobs, so the two cannot drift apart.

The consequence matters more than the mechanism. When retrieval misses the table a question
needs, the statement fails at the `TABLES` layer and the turn ends in a refusal that names
`r_table_not_licensed`. It does not end in a confident answer computed over some other table that
happened to be nearby. In a lake with decoy tables, which is what the measurement corpus has, that
distinction is the whole product.

The cost is real and worth stating: a retrieval miss becomes a hard refusal rather than a degraded
answer. At 0.936 gold-table coverage that trade is cheap. Whether to keep it is
[an open question](docs/open-work.md), not a settled one.

### 3. Declining is an outcome, with a taxonomy and a price

A turn that does not answer records *why*. `refused` means a governance layer stopped a
statement, and the row carries the layer and the rule. `capped` means the agent spent its five
attempts without producing a statement that passed. `clarification` means the engine asked the
user a question instead, which is what it does when a turn licenses nothing at all.

Every declined turn is then priced. The harness runs the gold statement and records what the
engine *would* have scored, in a separate field that never merges into the headline. So the claim
"declining is worth something" is a number rather than a posture.

---

## How a turn works

### Retrieve

`guard` runs five deterministic rules, then an optional model-backed check on whether the question
is a BI question at all.

Five facets then search the semantic layer in parallel. Each one rewrites the question into its
own search string first, because the words a user types rarely match the words a schema summary
uses. Someone asks about "average star rating for restaurants in this area"; the summary says
"stores basic information about restaurants". Neither BM25 nor an embedder bridges that on the raw
question, so each facet asks for what it needs:

| Facet | Looks for |
|---|---|
| `facet_schema` | which schema the question lives in |
| `facet_term` | business vocabulary, so "churn" reaches the column that defines it |
| `facet_metric` | named measures with an authored expression |
| `facet_entity` | tables, columns and the joins between them |
| `facet_example` | worked question-to-SQL pairs for this schema |

`route` reads those hits and picks a schema shortlist. A second retrieval pass then re-searches
inside the selected schemas with global IDF, which is a fresh search rather than a filter of the
first one. Budgets cap what survives: 8 tables, 30 columns, 5 joins, 5 metrics, 5 terms, 3
examples.

`connect` completes the join graph. Retrieval returns a set of tables that may not be connected to
each other, so a Steiner tree over the authored join edges pulls in the bridging tables needed to
make them joinable. Joins come from the corpus, not from foreign-key discovery: if two tables have
no declared relationship, the engine will not invent one.

`assemble` renders all of it into one context block. That block is injected on every model call
through `wrap_model_call` and never written into `messages`, so a long tool loop does not
accumulate copies of it.

### Generate

A nested `create_agent` loop writes SQL against the assembled context, with five read-only tools:

- `inspect_schema` lists a table's columns
- `sample_rows` reads real values from a column, which is how the agent learns that a status field
  holds `SHIPPED` and not `shipped`
- `read_body` opens an asset's authored notes
- `run_query` proposes a statement
- `ask_user` asks a clarifying question and pauses the turn

`run_query` is capped at five attempts. A governance refusal spends one of them; only an
infrastructure error refunds.

### Check

`run_query` calls `check()` before the connector sees anything. Six layers run in order, and the
first refusal ends the attempt:

| Layer | Refuses |
|---|---|
| `PARSE` | unparseable input, multiple statements, control characters |
| `NO_WRITE` | anything that is not a read, including `SELECT INTO` and locking clauses |
| `FUNCTIONS` | functions outside the allowlist, and whole-row arguments |
| `BINDING` | unbound and ambiguous references, and bare `SELECT *` |
| `COLUMNS` | columns the corpus marks excluded or suspect |
| `TABLES` | tables this turn was not licensed |

A seventh layer, `COST`, is declared and ships off. The allowlist itself is pinned to the sqlglot
generation the tree was built against, because the function list is keyed on expression classes
and those move across major versions.

### Execute and stamp

The read-only connector runs the statement. `stamp` then writes the turn record: the outcome,
every attempt with the layer and reason code that refused it, the terminal reason, and the token
usage per stage. That record is what the abstention analysis, the failure-mode taxonomy and the
evaluation harness all read. None of them re-derives it.

### Bring your own semantic layer

The engine ships with one for the BIRD data lake in a separate repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus): 13,304 typed YAML assets across 57
schemas, describing tables, columns, joins, metrics, business terms and worked examples. Point
`GOVERNED_BI_CORPUS_DIR` at your own to serve your own data.

---

## What it measures about itself

1,351 questions across 57 schemas with decoy tables present, corpus
[`BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) @ `30872d3`.

| | |
|---|---:|
| Execution accuracy | **0.676** |
| Accuracy on turns it commits to | **0.714** (n = 1,278) |
| Turns it declines | 73 (5.4%) |
| Declined turns that would have been **wrong** | **77.4%** (48 of the 62 that can be priced) |
| Delivered accuracy divided by withheld accuracy | **3.16x** |

The last three rows are why this project exists. An engine that answers everything tells you
nothing about which of its answers to distrust. This one declines 5.4% of turns and is right about
why on three of every four declines the dataset lets us price. That is a claim about calibration,
and it sits orthogonal to accuracy: a system with a higher score can still leave you unable to
sort its good answers from its bad ones.

The honest version of the claim is narrow. The engine declines when its **own context** is
insufficient, and almost all of that is retrieval: 19 of the 20 refusals end on
`r_table_not_licensed`, and all 4 clarifications licensed nothing at all. It does not know which
questions are hard. It knows when it is working blind, which turns out to be the more useful thing
for a reader who cannot check the SQL.

The measured conditions, the paired statistics behind every figure above, and the comparison
against a system that never abstains are in [failure modes](docs/failure-modes.md) and
[measurement](docs/measurement.md). Numbers here are quotable only with the corpus commit beside
them, which is why it appears above.

---

## Get started

You need [uv](https://docs.astral.sh/uv/), Python 3.13, a Postgres database, and a corpus.

```bash
uv sync
```

Copy `.env.example` to `.env` and fill in three values:

```bash
GOVERNED_BI_PG_DSN=host=127.0.0.1 port=5432 dbname=... user=... password=...
OPENAI_API_KEY=sk-...
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

Run one turn from the command line:

```bash
uv run python -m governed_bi.serve --schema <schema> -q "How many orders shipped late last quarter?"
```

Or start the server:

```bash
uv run langgraph dev
```

A turn takes long enough that you want the streaming surface: `POST /threads/{id}/runs/stream`
with `stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`. Without
`stream_subgraphs`, tool and token events never reach the client
([ADR 0010](docs/adr/0010-live-stage-events.md)). `POST /chat` is the blocking fallback.

Every environment variable, plus the Bedrock and proxy gateways, is in
[the usage guide](docs/usage.md).

---

## Measuring a change

A text-to-SQL score moves by a point or two for reasons unrelated to the change you made, so most
of the engineering here is in telling a real result from a lucky run.

- **Paired tests, not net deltas.** Comparisons use McNemar over the discordant pairs. Two
  identical runs of this engine disagree on 12.7% of questions, which puts the noise floor at
  SE around 1.0pp. An agentic loop with up to five attempts, five model-driven rewriters above
  retrieval, and a layer that can refuse buys expressiveness and pays for it in resolution.
- **Routing replay.** `--replay-routing` pins a run to a prior run's shortlist, because those five
  rewriters mean an unpinned A/B cannot separate its own effect from a shortlist that moved. It
  cuts discordance by about a quarter.
- **Treatment identity on every row.** Each measurement row carries `corpus_content_hash` and
  `prompt_set_hash`. Resuming an artifact whose corpus differs from the running one is refused,
  rather than producing one file holding two experiments and reporting it as one.
- **Instruments that can fail.** Measurement code is mutation-tested: break the field, confirm a
  test catches it. Eight tests that could not fail were found and fixed this way.

The payoff is being able to explain a number. One example: a governance rule scoped one level too
wide was refusing fully qualified references it should have allowed, 568 attempts across 119
turns. Narrowing it to references it could actually resolve was worth 5.3 points and 14.4% fewer
input tokens, more than every prompt change tried alongside it, and invisible until the field
recording which rule refused each attempt was added.

---

## Documentation

| | |
|---|---|
| [Usage](docs/usage.md) | install, environment, serve |
| [Architecture](docs/architecture.md) | the serve spine and the package map |
| [Measurement](docs/measurement.md) | how to run an arm and what makes a number quotable |
| [Failure modes](docs/failure-modes.md) | how the engine gets things wrong, per class, with repair experiments |
| [ADRs](docs/adr/) | binding decisions. Start with 0005 and 0006 |
| [Open work](docs/open-work.md) | what is unfinished, and the evidence for each item |

The web UI is a separate repository,
[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) (Next.js). The semantic layer is
its own repository too, [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus), because a
corpus that is not versioned makes every number measured against it unreproducible.

---

## Repository layout

```
docs/               design docs and ADRs
data/bird/          beer_factory.sqlite offline fixture (BIRD, CC BY-SA 4.0)
scripts/            one-shot corpus build kits, outside the package
src/governed_bi/
  api/              FastAPI app and the LangGraph make_graph entry point
  corpus/           asset schemas, loading, validation
  datasource/       connectors
  eval/             measurement harness
  govern/           the layer stack, ledger, and tool bounds
  measure/          populations and statistics helpers
  model/            chat and embedder adapters, gateway selection
  register/         knobs, prompts, records, citations
  retrieve/         BM25, semantic channel, Steiner joins
  serve/            the rails graph and agent_core tools
tests/
tools/              the eval driver (run_datalake_eval.py), structural checks
```

Postgres is the live path. The SQLite file under `data/bird/` is an offline test fixture rather
than a supported target.

## License

The code is under the MIT License (see [LICENSE](LICENSE)), copyright 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
