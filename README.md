# governed-bi

**Ask your database questions in English. Get an answer, the SQL behind it, or a reason it could
not answer.**

[![CI](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minhao-Zhang/governed-bi/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

governed-bi turns a business question into read-only SQL, checks that SQL before it runs, and
hands back the statement along with the answer. When it cannot find what a question needs, it
says so instead of guessing.

## What it looks like

Ask a question:

```bash
uv run python -m governed_bi.serve --schema airline \
  -q "What is the total number of flights that have Oklahoma as their origin?"
```

It writes the SQL, runs it read-only, and shows you both. Abbreviated from what the run recorded:

```
question : What is the total number of flights that have Oklahoma as their origin?
outcome  : answered
sql      : SELECT COUNT(*) FROM "airline"."Airlines" WHERE "ORIGIN" = 'OKC' LIMIT 200001
```

Notice `'OKC'`. Nobody typed an airport code into the question. That mapping lives in the semantic
layer, which is the part of this system you curate, and it is how the engine answers questions
phrased in your own business vocabulary. The trailing `LIMIT` is a row guard the engine appends to
every statement.

Now a question it could not answer, because retrieval failed to find the product and cost tables
it needed:

```
question : What is the average profit of all the products from the Clothing category?
outcome  : refused
attempt  : TABLES  r_table_not_licensed
```

The tables it did have in hand included a different company's product catalog. Averaging profit
over those would have produced a confident, plausible, wrong number, which is what an engine
without this check returns.

Both are real turns from the [evaluation run](docs/failure-modes.md).

## Why it behaves this way

Every statement is parsed and inspected before the database sees it. The engine rejects writes,
and it rejects any reference to a table the question did not call for. The tables retrieval found
for a question are the only tables the query may touch, so when retrieval misses, you get a
refusal you can see instead of an answer computed over whatever else was nearby.

Each turn also leaves a record of what ran, what was rejected, and why, so you can audit an answer
after the fact rather than taking it on faith.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.13
- A Postgres database
- A semantic layer (see below)

## Install

```bash
uv sync
```

Copy `.env.example` to `.env` and fill in three values:

```bash
GOVERNED_BI_PG_DSN=host=127.0.0.1 port=5432 dbname=... user=... password=...
OPENAI_API_KEY=sk-...
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

## Usage

One question from the command line:

```bash
uv run python -m governed_bi.serve --schema <schema> -q "your question"
```

As a server, with a streaming API for chat interfaces:

```bash
uv run langgraph dev
```

Every environment variable and both API surfaces are in [the usage guide](docs/usage.md).

## The semantic layer

governed-bi does not read your database and guess. It reads a semantic layer you curate: files
describing your tables, columns, joins, metrics and business vocabulary. That is where "Oklahoma"
learns to mean `'OKC'`, and where "active customer" gets a definition your finance team agrees
with.

A ready-made one for the BIRD benchmark lake is in a separate repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus): 13,304 assets across 57 schemas. Point
`GOVERNED_BI_CORPUS_DIR` at your own to serve your own data. The format is in
[corpus format](docs/corpus-format.md).

## How well it works

Measured on 1,351 questions across 57 schemas, corpus
[`BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) @ `30872d3`.

| | |
|---|---:|
| Answers correct | **0.676** |
| Correct, among questions it chose to answer | **0.714** (n = 1,278) |
| Questions it declined | 73 (5.4%) |
| Declined questions it would have got **wrong** | **77.4%** (48 of the 62 that can be checked) |

Of the questions this engine refused, three in four were questions it would have got wrong. An
engine that answers everything gives you no way to sort its good answers from its bad ones, so
the refusal rate is worth as much attention here as the accuracy.

How the measurement works, and where the engine still gets things wrong, are in
[measurement](docs/measurement.md) and [failure modes](docs/failure-modes.md).

## Documentation

| | |
|---|---|
| [Usage](docs/usage.md) | install, configure, serve |
| [Architecture](docs/architecture.md) | how a turn is put together |
| [Corpus format](docs/corpus-format.md) | writing a semantic layer |
| [Measurement](docs/measurement.md) | running an evaluation and reading the result |
| [Failure modes](docs/failure-modes.md) | how the engine gets things wrong |
| [ADRs](docs/adr/) | the binding design decisions |
| [Open work](docs/open-work.md) | what is unfinished |

The web UI is a separate repository,
[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) (Next.js).

## Project status

Research code, actively developed, no production users. The API and the corpus format still
change. [Open work](docs/open-work.md) lists what is unfinished and what is known to be wrong.

## License

MIT (see [LICENSE](LICENSE)), copyright 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
