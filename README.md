# governed-bi

Ask a business question in English. Get an answer backed by read-only SQL, an audit record of how
the engine reached it, or a refusal naming the rule that stopped it.

governed-bi is a text-to-SQL engine for teams who need to know when *not* to trust the answer. It
serves questions against a curated semantic layer, checks every statement through a deterministic
layer stack before execution, and records what it did. When retrieval does not find the tables a
question needs, the engine says so instead of guessing.

**The model never holds a database handle.** No tool executes arbitrary SQL, there is no filesystem
write channel, and there is no escape hatch. The agent proposes a statement to a tool body that
checks it first. The governance boundary is the *absence* of the tool, not a policy asking the
model to behave — a prompt injection has nothing to talk to.

Built on LangGraph. Measured on an obfuscated 57-schema Postgres data lake.

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

## How a turn works

1. **Retrieve.** Five parallel facets search the semantic layer — typed YAML assets describing
   tables, columns, joins, metrics, business terms and worked examples — and license a slice of it
   for this turn.
2. **Generate.** A model writes SQL against that slice. It can inspect a table's columns, sample a
   column's real values, and read an asset's notes, all through read-only tools.
3. **Check.** Six layers run in order: parse, read-only, permitted functions, binding, columns,
   tables. A seventh, cost, is declared and ships off. A statement naming an unlicensed table or an
   excluded column never reaches the database.
4. **Execute and stamp.** The read-only connector runs the statement, and the turn record captures
   the outcome, every attempt with the layer and rule that refused it, and why the turn ended.

Retrieval doubles as the allowlist. A table the router did not surface is a table the checker will
not permit, so a retrieval miss becomes a visible refusal instead of a confident answer over the
wrong table.

The engine ships with a semantic layer for the BIRD data lake in a separate repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) — 13,304 assets across 57 schemas. Point
`GOVERNED_BI_CORPUS_DIR` at your own to serve your own data.

---

## What it measures about itself

The `v4` arm: 1,351 questions across 57 schemas with decoy tables present, corpus
[`BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) @ `30872d3`.

| | |
|---|---:|
| Execution accuracy | **0.676** |
| Accuracy on turns it commits to | **0.714** (n = 1,278) |
| Turns it declines | 73 (5.4%) |
| Declined turns that would have been **wrong** | **77.4%** (48 of the 62 that can be priced) |
| Delivered accuracy ÷ withheld accuracy | **3.16×** |

The last three rows are why this project exists. An engine that answers everything tells you
nothing about which of its answers to distrust. This one declines 5.4% of turns and is right about
why on three of every four declines the dataset lets us price. That is a claim about calibration,
and it is orthogonal to accuracy: a system with a higher score can still leave you unable to sort
its good answers from its bad ones.

### The ceiling on that claim

[WrenAI](https://github.com/Canner/WrenAI) never abstains, so it doubles as a governance-off
contrast arm. On the 73 turns this engine declines, WrenAI answers all 73 and gets **56.2%** right,
against 68.5% on the turns this engine commits to. A ratio of 1.22× means the declined questions
are mostly *answerable*.

So the honest version of the claim is narrower than "calibrated abstention": the engine declines
when its **own context** is insufficient, and almost all of that is retrieval. That is still worth
having — a missed table surfaces as "I cannot answer" rather than as a confident answer against the
wrong table — but it is not the stronger claim that the engine knows which questions are hard.

On accuracy the two systems are level. WrenAI scores 0.678 on the same questions and the same
database. Paired over all 1,351 that is a net difference of 3 in WrenAI's favour, McNemar
p = 0.895 — **a statistical tie**, stated as one rather than rounded either way.

---

## Four points of that score measure the grader, not the engine

The default prompt has one paragraph telling the model to select exactly the columns the question
asks for. The `v5` arm deletes that paragraph and changes nothing else:

| | v4 | v5 |
|---|---:|---:|
| Execution accuracy | 0.676 | **0.635** |
| Predictions wider than the gold | 43 | **125** |
| Abstention precision | 0.774 | **0.847** |

Paired, that is net −55 questions, −4.07pp, McNemar p < 0.0001 — one of the largest effects any
single change has produced here, from a rule about output formatting.

Nothing in the engine enforces select-list width. `govern/layers.py` has no rule about it, and the
grader hashes row tuples while explicitly *not* hashing column names. The only thing an extra
column can change is the grader's digest. So roughly four points of this engine's execution
accuracy come from matching the reference answer's column set rather than from finding the right
data.

That is not a reason to publish 0.635 as the honest number. Every published EX on this benchmark
contains an output-shaping component; WrenAI's 0.678 does too, and its size cannot be measured from
outside, so subtracting on one side only would make the comparison worse. It is also not a
reasoning regression — v5's abstention precision went *up*. The engine did not get worse at knowing
what it does not know. It got worse at matching a shape.

The finding is a measurement of a confound in the metric, which almost nobody measures because it
costs a full second arm to see.

---

## The instrument

Most of the engineering here is not in the engine. A text-to-SQL score moves by a point or two for
reasons that have nothing to do with the change you made, so the harder problem is knowing when a
result is real.

- **Paired tests, not net deltas.** Arms are compared with McNemar over the discordant pairs. Two
  identical runs of this engine disagree on 12.7% of questions, which puts the noise floor at
  SE ≈ 1.0pp — a 2-point "improvement" is often not one. That floor is a property of *this*
  architecture, not of the benchmark: WrenAI's two runs over the same questions disagree on 2.4%.
  An agentic loop with up to five attempts, five model-driven rewriters above retrieval, and a
  layer that can refuse buys expressiveness and pays for it in resolution.
- **Routing replay.** `--replay-routing` pins an arm to a prior run's shortlist, because those five
  rewriters mean an unpinned A/B cannot separate its own effect from a shortlist that moved. It
  cuts discordance by about a quarter.
- **Treatment identity on every row.** Each measurement row carries `corpus_content_hash` and
  `prompt_set_hash`. Resuming an artifact whose corpus differs from the running one is refused,
  rather than producing one file holding two experiments and reporting it as a single arm.
- **Instruments that can fail.** Measurement code is mutation-tested: break the field, confirm a
  test catches it. Eight tests that could not fail were found and fixed this way.
- **Abstentions are priced.** For every declined turn the harness runs the gold statement and
  records what the engine *would* have scored. Reported separately, never folded into the headline.

The payoff is being able to tell a real finding from a lucky run. One example: a governance rule
scoped one level too wide was refusing fully qualified references it should have allowed — 568
attempts across 119 turns. Narrowing it to references it could actually resolve was worth
**+5.3 points and 14.4% fewer input tokens** —
larger than every prompt intervention tried alongside it, and invisible until the field recording
which rule refused each attempt was added.

---

## Documentation

| | |
|---|---|
| [Usage](docs/usage.md) | install, environment, serve |
| [Architecture](docs/architecture.md) | the serve spine and the package map |
| [Measurement](docs/measurement.md) | how to run an arm and what makes a number quotable |
| [Failure modes](docs/failure-modes.md) | how the engine gets things wrong, per class, with repair experiments |
| [ADRs](docs/adr/) | binding decisions — start with 0005 and 0006 |
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

Postgres is the live path. The SQLite file under `data/bird/` is an offline test fixture, not a
supported target.

## License

The code is under the MIT License (see [LICENSE](LICENSE)), © 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
