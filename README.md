# governed-bi

Ask a business question in English. Get a result table, the read-only SQL that produced it, and a
record of how the engine got there.

## What it does

You point governed-bi at a Postgres database and a semantic layer: a set of curated files
describing your tables, columns, joins, metrics and business vocabulary. It then answers questions
against them.

Four things it does on every question:

- **Finds the right tables.** It searches the semantic layer for the tables, columns and joins the
  question needs, and works only from what it finds.
- **Checks the SQL before running it.** Every statement is inspected first. Reads only, no writes,
  and no access to tables outside the ones the question needs.
- **Says when it cannot answer.** If the semantic layer does not have what the question needs, you
  get a refusal that names the reason, rather than a confident answer built on the wrong table.
- **Records what happened.** Each turn leaves the outcome, the statement that ran, anything that
  was rejected, and why.

It runs as a server with a streaming API, or as a one-shot command.

## Get started

You need [uv](https://docs.astral.sh/uv/), Python 3.13, a Postgres database, and a semantic layer.

```bash
uv sync
```

Copy `.env.example` to `.env` and fill in three values:

```bash
GOVERNED_BI_PG_DSN=host=127.0.0.1 port=5432 dbname=... user=... password=...
OPENAI_API_KEY=sk-...
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

Ask one question:

```bash
uv run python -m governed_bi.serve --schema <schema> -q "How many orders shipped late last quarter?"
```

Or start the server:

```bash
uv run langgraph dev
```

Full setup, every environment variable, and the streaming API are in
[the usage guide](docs/usage.md).

### The semantic layer

A ready-made one for the BIRD data lake is in a separate repository,
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

The engine declines 5.4% of questions, and on the ones the dataset lets us check, three out of
four of those declines were right to happen. An engine that answers everything gives you no way to
sort its good answers from its bad ones, so knowing when it stops matters as much as the score.

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

## License

The code is under the MIT License (see [LICENSE](LICENSE)), copyright 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
