# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

An agentic BI / Generative-BI system: natural-language questions → **grounded,
governed, auditable** answers over relational data.

Near-term target is a **SQLite-proven showcase** (with dialect-pluggable seams
for other engines) that grows a reviewable semantic layer from a seed of
known-good queries — *seed-assisted semantic-layer growth*, not a zero-prior cold
start — evaluated on the self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution
accuracy). Enterprise abstractions (identity/RLS, human gate, scoped
memory/cache) are seamed in but toggled off; enforcement belongs to a private
enterprise fork, not this engine.

> **Design-first, and honest about maturity.** The design (D1-D15) is well ahead
> of the build (see [`docs/design-decisions.md`](docs/design-decisions.md)). The
> deterministic core runs end-to-end **offline**; the LangGraph/deepagents
> harnesses are built but exercised only against deterministic model doubles —
> **nothing has yet run against a live model**. See the [status table](#status)
> for what is proven vs. designed vs. seamed.

## The idea in three lines

- **Two harnesses over one shared substrate.** A `curator` (build) *produces*
  the corpus; a `server` (serve) *consumes* it to answer. Opposite risk
  profiles, one substrate.
- **The corpus is the intended moat** — a hypothesis the eval must prove, not yet
  a demonstrated result. Git-tracked YAML typed assets + Markdown skills,
  curator-authored and human-audited. Git is the single source of truth; graph /
  vector / BM25 stores are rebuildable projections.
- **Fail-closed.** Out-of-scope / missing-coverage / tripped-guardrail returns a
  refusal or a clarifying question, never a confident wrong number. Guardrails are
  a safety gate, **not a correctness oracle** — so answers carry two separate
  stamps: `safety_clearance` (did it pass the guardrails) and `semantic_assurance`
  (how well-grounded), never collapsed into one "trust score".

## Status

What is proven vs. designed vs. merely seamed. The single biggest open risk is
that **the LLM generator and curator have never run against a live model** — every
test below uses deterministic offline doubles, so real generation/curation quality
is unmeasured.

| Capability | Status | Evidence |
|---|---|---|
| SQLite governed serve path (retrieve → context → SQL-gen → 5-layer guardrails → execute → stamp) | **Built** | `uv run --extra agents --extra api pytest` — 321 offline tests |
| Corpus contract + validation (typed YAML/MD, ID + reference integrity) | **Built** | `python -m governed_bi.corpus.cli`, CI |
| Bounded self-repair + two-axis reliability stamp | **Built** | `tests/test_server.py` |
| Semantic SQL cache (re-guardrail + re-execute on hit, `certified`-only admission) | **Built, off by default** | `tests/test_cache.py` |
| LangGraph server harness (Answer-equivalent to the core flow) | **Built, offline only** | `tests/test_serve_graph.py` equivalence |
| deepagents curator harness | **Construction-only** | `tests/test_curator_deep_agent.py` (no live run) |
| Live-model generation / curation quality | **Unproven** | needs `OPENAI_API_KEY` + a run |
| BIRD-Obfuscation 3-arm eval (no-layer / curator / gold) | **Partial** | curator arm scored offline; obfuscated DBs + baseline/gold arms pending |
| `CorpusRelease` (immutable, hash-pinned serving release) | **Designed** | not implemented — see [design decisions](docs/design-decisions.md) |
| Identity → query scope (RLS / tenant isolation) | **Seam only** | single-identity SQLite showcase; enforcement is enterprise-fork scope |
| Postgres / Redshift execution | **Built, not live-tested** | `PostgresConnector` (information_schema) + `RedshiftConnector` (svv_*); offline fake-connection tests, no live server run |

**Honest one-liner:** a governed NL2SQL kernel that treats model output as
untrusted — it constrains the accessible data surface, validates generated SQL
structurally, separates curation from serving, and keeps the semantic layer
reviewable. SQLite-proven and evaluation-oriented; the next milestone is showing
that curator-built assets measurably beat a fair no-corpus baseline on
obfuscated schemas.

## Web UI

The frontend lives in a separate repo:
[Minhao-Zhang/governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)
(Next.js, `useStream`). It is currently mock-only and not yet wired end-to-end to
this backend.

## Documentation

Start at [`docs/README.md`](docs/README.md). Key docs:
[architecture](docs/architecture.md) ·
[design decisions (D1-D15)](docs/design-decisions.md) ·
[asset schemas](docs/asset-schemas.md) ·
[curator](docs/curator.md) · [server](docs/server.md) · [viz](docs/viz.md) ·
[glossary](docs/glossary.md).

## Repo layout

```
docs/                  design docs (canonical)
data/bird/             beer_factory.sqlite (BIRD, CC BY-SA 4.0; see NOTICE)
corpus/                the semantic layer (Git = source of truth); worked example under beer_factory/
src/governed_bi/
  config.py            environment toggles + reusable numbers + model config (ModelConfig, load_settings)
  llm/                 done: ChatClient/Embedder seams (raw OpenAI + LangChain + deterministic offline defaults)
  corpus/              done: schemas, IDs, CI validator, loader, serializer, CLI
  gateway/             done: SQLite (proven) + Postgres/Redshift connectors (offline-tested); read-only gateway; five-layer guardrails
  curator/             done: Facts profiling, HeuristicProposer + LlmProposer, adversary review, curate loop
  graph/               done: FK graph projection + Steiner-tree join planning + FK join-neighborhood
  retrieval/           done: RVGD BM25 + grounding + vector channel (embedder-gated, RRF fusion)
  memory/              done: working memory (D8); episodic/correction protocol seams
  server/              done: serve DAG, routing, context assembly, SQL gen (template + LLM), self-repair, SQL cache, stamp; LangGraph harness in graph.py
  curator/             + deep_agent.py: the deepagents build harness
  eval/                done: execution accuracy, arm harness, refuse-gate
  viz/                 done: read-only audit surface — UI-agnostic presenter view models (no UI dependency)
tests/                 unit + end-to-end suites across all of the above
```

Modules carry docstrings that point back to the design docs and decision IDs.

## Usage & development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                                   # create .venv, install deps + package
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
```

New to the repo? The [walkthrough](docs/walkthrough.md) is a guided clone → first-question
tour. The [quickstart](docs/usage.md) is the reference (validate CLI, programmatic
corpus API); to write or edit corpus assets, see [corpus authoring](docs/corpus-authoring.md).

Runnable today with no model or network: the full question -> answer serve pipeline
(retrieval, context assembly, template SQL generation, five-layer guardrails,
bounded self-repair, reliability stamp) over the committed beer_factory DB, plus
the curator scaffold, memory, eval, and the read-only audit surface (presenter
view models + the `governed_bi.api` HTTP API). Core
dependencies are intentionally minimal (pydantic, pyyaml, networkx, sqlglot);
the Postgres/Redshift connectors are optional extras. The agent harnesses
(server = LangGraph `StateGraph`, curator = deepagents, with LangChain model
clients) live behind the `agents` extra and run on the deterministic offline
model doubles without a key.

### Models & configuration

All non-secret policy lives in one project file,
[`governed_bi.toml`](governed_bi.toml), parsed by
`governed_bi.config.load_settings()`: environment toggles, models, datasource
shape, corpus path, and serve flags. Local machine overrides go in a git-ignored
`governed_bi.local.toml` beside it (same tables; local wins on merge). Secrets
(API keys, DSN passwords) live only in the environment or a git-ignored `.env`.

```bash
uv sync --extra agents          # LangGraph + deepagents + LangChain model clients
uv sync --extra openai          # (alternative) the minimal raw-openai client only
export OPENAI_API_KEY=sk-...     # the key is read from the env, never stored
```

The key is read from the environment. If you'd rather not export it, copy
[`.env.example`](.env.example) to `.env` at the repo root and put the key there —
it is loaded on import and fills in only variables not already set, so an exported
environment variable always wins. `.env` is git-ignored; never commit a real key.
To point at Postgres locally without editing the committed TOML, put the
`[datasource]` switch in `governed_bi.local.toml` and the DSN value in `.env`.

Optional observability (also documented in [`.env.example`](.env.example)):

```bash
# LangSmith (native; no extra package)
export LANGSMITH_TRACING=true          # or LANGCHAIN_TRACING_V2=true
export LANGSMITH_API_KEY=lsv2_...

# Langfuse (LangChain callback)
uv sync --extra tracing
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

The model clients are imported lazily behind the `ChatClient` / `Embedder`
protocols, and each has a deterministic offline default (`StaticChatClient`,
`HashingEmbedder`) so tests and the default pipeline need neither the dependency
nor a key. To use a real model, build a LangChain client and inject it:

```python
from governed_bi.config import load_settings
from governed_bi.llm import LangChainChatClient, LangChainEmbedder
from governed_bi.server import LlmSqlGenerator, SqlCache
from governed_bi.server.graph import answer_question_graph  # LangGraph harness

models = load_settings().models
chat = LangChainChatClient.from_config(models)
answer = answer_question_graph(
    question, identity, corpus=corpus, gateway=gateway, settings=settings, session_id=sid,
    sql_generator=LlmSqlGenerator(chat, dialect="sqlite"),
    embedder=LangChainEmbedder.from_config(models),
    cache=SqlCache(LangChainEmbedder.from_config(models)),
)
```

To exercise the **real** path (the one thing the offline tests can't), run the
live smoke script — it drives the LLM generator + real embeddings over
beer_factory and reports EX / refusal / decoy-touch:

```bash
export OPENAI_API_KEY=sk-...
uv run --extra agents python scripts/live_smoke.py
```

## License

Code in this repository is under the MIT License (see [LICENSE](LICENSE)),
© 2026 Minhao Zhang.

Bundled data is third-party and separately licensed. `data/bird/beer_factory.sqlite`
is the `beer_factory` database from the [BIRD benchmark](https://bird-bench.github.io/),
included unmodified under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/);
see [`data/bird/NOTICE`](data/bird/NOTICE). The MIT license does not cover the data.
