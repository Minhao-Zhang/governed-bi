# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

An agentic BI engine: natural-language question in, governed read-only SQL out,
with an audit trail. It retrieves a slice of a curated semantic layer (typed
YAML assets), a model writes SQL, seven deterministic guardrail layers check it,
the statement executes read-only, and the turn is stamped with
`safety_clearance` and `semantic_assurance`.

Postgres is the live serve path. SQLite under `data/bird/` is an offline
test/CI fixture only.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                       # create .venv, install the default (OpenAI) stack
uv sync --extra bedrock       # optional AWS Bedrock provider
```

**1. Secrets and connection** in a git-ignored `.env` at the repo root:

```bash
OPENAI_API_KEY=sk-...
PG_DSN=host=... port=5432 dbname=... user=... password=...
```

**2. Corpus and models** via environment variables (there is no live TOML
settings loader — defaults live in `register/knobs.py`):

```bash
GOVERNED_BI_CORPUS_DIR=...    # or place a single corpus under corpora/
GOVERNED_BI_MODEL=...         # optional; unset → graph runs with has_live_model=false
GOVERNED_BI_UTILITY_MODEL=... # optional small model for facet rewrite / scope gate
GOVERNED_BI_EMBEDDING_MODEL=...
```

**3. Serve:**

```bash
uv run langgraph dev          # graph "serve" from langgraph.json
```

A turn takes 30–120 seconds. Prefer the streamed surface:
`POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](docs/adr/0010-live-stage-events.md)). `POST /chat` is the blocking
fallback.

One-turn CLI (no LangGraph Server):

```bash
uv run python -m governed_bi.serve --schema … -q "…"
# --corpus-dir … ; --no-model for a stub path
```

## Development

```bash
uv run pytest
```

## Web UI

Separate repo: [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)
(Next.js). Available locally at `../governed-bi-ui`.

## Documentation

Start at [`docs/README.md`](docs/README.md):
[usage](docs/usage.md) · [architecture](docs/architecture.md) ·
[ADRs](docs/adr/) · [glossary](docs/glossary.md).

## Repo layout

```
docs/               design docs + ADRs
corpora/            corpus directories for serve (or set GOVERNED_BI_CORPUS_DIR)
data/bird/          beer_factory.sqlite offline fixture (BIRD, CC BY-SA 4.0)
src/governed_bi/
  api/              FastAPI app + LangGraph make_graph entry
  corpus/           asset schemas, load, validate
  datasource/       connectors
  eval/             measurement harness
  govern/           seven-layer check, ledger, tool bounds
  measure/          populations / stats helpers
  model/            chat + embedder adapters
  register/         knobs, prompts, records, citations
  retrieve/         BM25, semantic channel, Steiner joins
  serve/            rails graph + agent_core tools
tests/
tools/              structural checks and offline helpers
```

## License

Code is under the MIT License (see [LICENSE](LICENSE)), © 2026 Minhao Zhang.

Bundled data is third-party: `data/bird/beer_factory.sqlite` from the
[BIRD benchmark](https://bird-bench.github.io/) under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); see
[`data/bird/NOTICE`](data/bird/NOTICE).
