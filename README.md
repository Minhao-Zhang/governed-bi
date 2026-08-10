# governed-bi

Ask a question in English. Get an answer backed by read-only SQL, and a record of
exactly how the engine got there — or a refusal that says which rule stopped it.

**The model never holds a database handle.** There is no tool that executes
arbitrary SQL, no filesystem write channel, no escape hatch. The agent can only
propose a statement to a tool body that checks it first, and governance here is
the *absence* of the tool rather than a policy asking the model to behave. A
prompt injection cannot talk its way to the database, because there is nothing to
talk to.

Built on LangGraph, measured on an obfuscated 57-schema Postgres data lake, and
instrumented so that every number it produces names the corpus and the prompt
wording that produced it.

---

## What it can say about itself

Measured on 1,351 questions across 57 schemas, decoy tables included.

| | |
|---|---:|
| Execution accuracy | **0.664** |
| Accuracy on turns it commits to | **0.709** (n = 1,265) |
| Turns it declines | 86 (6.4%) |
| Declined turns that would have been **wrong** | **81.2%** |
| Delivered accuracy ÷ withheld accuracy | **3.76×** |

The last three rows are the point. An engine that answers everything gives you no
signal about which of its answers to distrust; this one abstains on 6.4% of turns
and is right about *why* four times out of five. That is a claim about
calibration, and it is orthogonal to accuracy — a system with a higher score can
still leave you unable to tell its good answers from its bad ones.

For reference, [WrenAI](https://github.com/Canner/WrenAI) scores 0.678 on the same
questions and the same database. Paired over the 1,351 questions that is a net
difference of 19, McNemar p = 0.240 — **a statistical tie**, and stated as one
rather than rounded into a win.

---

## How a turn works

1. **Retrieve.** Five parallel facets search a curated semantic layer — 13,304
   typed YAML assets describing tables, columns, joins, metrics and business
   terms — and license a slice of it for this turn.
2. **Generate.** A model writes SQL against that slice, with tools to inspect a
   table's columns, sample a column's real values, and read an asset's notes.
3. **Check.** The statement passes seven declared layers — parse, read-only,
   permitted functions, binding, columns, tables, cost. A statement naming an
   unlicensed table or an excluded column never reaches the database.
4. **Execute and stamp.** Read-only, then a record: the outcome, every attempt
   with the layer and rule that refused it, and why the turn ended.

Retrieval doubles as the allowlist. A table the router did not surface is a table
the checker will not permit, so a retrieval miss becomes a visible refusal rather
than a confident answer over the wrong table.

---

## The instrument

Most of the engineering here is not in the engine. A text-to-SQL score moves by a
point or two for reasons that have nothing to do with the change you made, so the
harder problem is knowing when a result is real.

- **Paired tests, not net deltas.** Arms are compared with McNemar over the
  discordant pairs. Two identical runs disagree on 12.7% of questions, which puts
  the noise floor at SE ≈ 1.0pp — so a 2-point "improvement" is not one.
- **Routing replay.** `--replay-routing` pins an arm to a prior run's shortlist,
  because five model-driven rewriters sit above retrieval and an unpinned A/B
  cannot separate its own effect from a shortlist that moved. It cuts discordance
  by 27%.
- **Treatment identity on every row.** A measurement row carries
  `corpus_content_hash` and `prompt_set_hash`. Resuming an artifact whose corpus
  differs from the running one is refused, rather than producing one file holding
  two experiments and reporting it as a single arm.
- **Instruments that can fail.** The measurement code is mutation-tested: break
  the field, confirm a test catches it. Eight tests that could not fail were found
  and fixed this way.
- **Abstentions are priced.** For every declined turn the harness runs the gold
  statement and records what the engine *would* have scored — reported separately,
  never folded into the headline.

The payoff is being able to tell a real finding from a lucky run. One example: a
governance rule scoped one level too wide was refusing 568 valid statements across
119 turns. Narrowing it to references it could actually resolve was worth
**+5.3 points and 14.4% fewer input tokens** — larger than every prompt
intervention tried alongside it, and invisible until the field recording which
rule refused each attempt was added.

---

## Try it

You need [uv](https://docs.astral.sh/uv/), Python 3.13, and a Postgres database.

```bash
uv sync
```

Put credentials in a git-ignored `.env`, point the engine at a corpus, and serve:

```bash
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
GOVERNED_BI_MODEL=...
```

```bash
uv run langgraph dev
```

A turn takes 30 to 120 seconds, so prefer the streaming surface —
`POST /threads/{id}/runs/stream` with `stream_mode: ["values", "messages",
"custom"]` and `stream_subgraphs: true`. Without `stream_subgraphs`, tool and
token events never reach the client
([ADR 0010](docs/adr/0010-live-stage-events.md)). `POST /chat` is the blocking
fallback.

One turn without the server:

```bash
uv run python -m governed_bi.serve --corpus-dir ../BIRD-corpus --schema … -q "…"
```

Full setup, every environment variable, and the Bedrock and proxy gateways are in
[the usage guide](docs/usage.md). How to run a measured arm is in
[measurement](docs/measurement.md).

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
[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) (Next.js). The
semantic layer is its own repository too,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus), because a corpus that
is not versioned makes every number measured against it unreproducible.

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

Postgres is the live path. The SQLite file under `data/bird/` is an offline test
fixture, not a supported target.

## License

The code is under the MIT License (see [LICENSE](LICENSE)), © 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
