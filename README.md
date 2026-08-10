# governed-bi

Ask a question in English. Get an answer backed by read-only SQL and a record of
how the engine got there.

Each turn does four things:

1. Retrieves a slice of a curated semantic layer — typed YAML assets that
   describe tables, columns, joins, metrics, and business terms.
2. Asks a model to write SQL, using only the tables that slice licensed.
3. Checks the statement through a stack of deterministic layers. A statement
   that names an unlicensed table or an excluded column never runs.
4. Executes it read-only and stamps the turn: `outcome`, `guardrail_errors`, a
   per-attempt ledger, and `terminal_reason` when the turn did not answer.

The model never holds a database handle. It can only send SQL that a checked
tool body chose to send, so a prompt cannot talk its way to the database.

Postgres is the live path. The SQLite file under `data/bird/` is an offline test
fixture, not a supported target.

## Quickstart

You need [uv](https://docs.astral.sh/uv/) and Python 3.13.

1. Install the stack:

   ```bash
   uv sync
   ```

   The OpenAI gateway needs only that. Add the one extra for AWS Bedrock chat
   or embeddings, and for the proxy gateway, which reads its API key and base
   URL from AWS Secrets Manager through `boto3`:

   ```bash
   uv sync --extra bedrock
   ```

2. Put your secrets in a git-ignored `.env` at the repository root:

   ```bash
   OPENAI_API_KEY=sk-...
   GOVERNED_BI_PG_DSN=host=... port=5432 dbname=... user=... password=...
   ```

3. Point the engine at a corpus and choose your models. Configuration is
   environment variables only — the `governed_bi.toml` in the repository root is
   not loaded by anything in `src/`, and defaults live in `register/knobs.py`.

   ```bash
   GOVERNED_BI_CORPUS_DIR=...     # or put a single corpus under corpora/
   GOVERNED_BI_MODEL=...          # unset → the graph runs with has_live_model=false
   GOVERNED_BI_UTILITY_MODEL=...  # smaller model for facet rewrites and the scope gate
   GOVERNED_BI_EMBEDDING_MODEL=...
   ```

   For the full list, including the Bedrock and proxy variables, see
   [the usage guide](docs/usage.md).

4. Serve the graph:

   ```bash
   uv run langgraph dev
   ```

A turn takes 30 to 120 seconds, so prefer the streaming surface:
`POST /threads/{id}/runs/stream`, with `stream_mode: ["values", "messages",
"custom"]` and `stream_subgraphs: true`. Without `stream_subgraphs`, tool and
token events never reach the client — see
[ADR 0010 on live stage events](docs/adr/0010-live-stage-events.md). `POST /chat`
is the blocking fallback.

To run one turn without LangGraph Server:

```bash
uv run python -m governed_bi.serve --schema … -q "…"
# --corpus-dir … ; --no-model for a stub path
```

## Development

```bash
uv run pytest
```

## Web UI

The UI lives in a separate repository,
[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) (Next.js), and
is available locally at `../governed-bi-ui`.

## Documentation

Start at [the docs index](docs/README.md), or go straight to
[usage](docs/usage.md), [architecture](docs/architecture.md),
[measurement](docs/measurement.md), the [ADRs](docs/adr/), the
[glossary](docs/glossary.md), or [what is still open](docs/open-work.md).

## Repository layout

```
docs/               design docs and ADRs
corpora/            corpus directories for serve (or set GOVERNED_BI_CORPUS_DIR)
data/bird/          beer_factory.sqlite offline fixture (BIRD, CC BY-SA 4.0)
scripts/            one-shot corpus build scripts, outside the package
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

## License

The code is under the MIT License (see [LICENSE](LICENSE)), © 2026 Minhao Zhang.

The bundled data is third-party: `data/bird/beer_factory.sqlite` comes from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). See
[`data/bird/NOTICE`](data/bird/NOTICE).
