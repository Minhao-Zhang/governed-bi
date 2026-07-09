# governed-bi

An agentic BI / Generative-BI system: natural-language questions → **grounded,
governed, auditable** answers over relational data.

Near-term target is a **general, DB-agnostic showcase** that cold-starts from
`{a DB connection + a handful of known-good queries}` and grows a semantic layer
over time, evaluated on the self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution
accuracy). Enterprise abstractions (identity, human gate, RLS, scoped
memory/cache) are seamed in but toggled off.

> **Design-first.** The design is well ahead of the build: D1-D10 are settled
> (see [`docs/design-decisions.md`](docs/design-decisions.md)). This repo is the
> scaffold. The corpus layer is implemented; the harnesses are documented stubs.

## The idea in three lines

- **Two harnesses over one shared substrate.** A `curator` (build) *produces*
  the corpus; a `server` (serve) *consumes* it to answer. Opposite risk
  profiles, one substrate.
- **The corpus is the moat.** Git-tracked YAML typed assets + Markdown skills,
  curator-authored and human-audited. Git is the single source of truth; graph /
  vector / BM25 stores are rebuildable projections.
- **Fail-closed.** Out-of-scope / missing-coverage / tripped-guardrail returns a
  refusal or a clarifying question, never a confident wrong number.

## Documentation

Start at [`docs/README.md`](docs/README.md). Key docs:
[architecture](docs/architecture.md) ·
[design decisions (D1-D10)](docs/design-decisions.md) ·
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
  gateway/             done (SQLite): connector + read-only gateway; Postgres/Redshift seams; five-layer guardrails
  curator/             done: Facts profiling, HeuristicProposer + LlmProposer, adversary review, curate loop
  graph/               done: FK graph projection + Steiner-tree join planning + FK join-neighborhood
  retrieval/           done: RVGD BM25 + grounding + vector channel (embedder-gated, RRF fusion)
  memory/              done: working memory (D8); episodic/correction protocol seams
  server/              done: serve DAG, routing, context assembly, SQL gen (template + LLM), self-repair, SQL cache, stamp; LangGraph harness in graph.py
  curator/             + deep_agent.py: the deepagents build harness
  eval/                done: execution accuracy, arm harness, refuse-gate
  viz/                 done: read-only audit cockpit (Streamlit, swappable UI)
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

The full quickstart (validate CLI, programmatic corpus API) is in
[docs/usage.md](docs/usage.md). To write or edit corpus assets, see
[docs/corpus-authoring.md](docs/corpus-authoring.md).

Runnable today with no model or network: the full ask -> answer serve pipeline
(retrieval, context assembly, template SQL generation, five-layer guardrails,
bounded self-repair, reliability stamp) over the committed beer_factory DB, plus
the curator scaffold, memory, eval, and the read-only viz cockpit. Core
dependencies are intentionally minimal (pydantic, pyyaml, networkx, sqlglot);
the Postgres/Redshift connectors are optional extras. The agent harnesses
(server = LangGraph `StateGraph`, curator = deepagents, with LangChain model
clients) live behind the `agents` extra and run on the deterministic offline
model doubles without a key.

### Models & configuration

Model choices live in one project file, [`governed_bi.toml`](governed_bi.toml),
parsed by `governed_bi.config.load_settings()`: OpenAI `gpt-5.5` (low reasoning
effort) for generation/curation and `text-embedding-3-small` for the vector
channel and SQL cache. All are swappable by editing the file.

```bash
uv sync --extra agents          # LangGraph + deepagents + LangChain model clients
uv sync --extra openai          # (alternative) the minimal raw-openai client only
export OPENAI_API_KEY=sk-...     # the key is read from the env, never stored
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

## License

Code in this repository is under the MIT License (see [LICENSE](LICENSE)),
© 2026 Minhao Zhang.

Bundled data is third-party and separately licensed. `data/bird/beer_factory.sqlite`
is the `beer_factory` database from the [BIRD benchmark](https://bird-bench.github.io/),
included unmodified under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/);
see [`data/bird/NOTICE`](data/bird/NOTICE). The MIT license does not cover the data.
