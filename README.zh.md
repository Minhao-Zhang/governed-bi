# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

一个 agentic BI / Generative-BI 系统：自然语言问题 → 基于关系型数据的**接地（grounded）、受治理（governed）、可审计（auditable）**的答案。

近期目标是打造一个**通用、与数据库无关(DB-agnostic)的展示系统**，从 `{a DB connection + a handful of known-good queries}` 冷启动，并随时间推移逐步扩展出语义层，在自建的 [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集上进行评测（执行准确率）。企业级抽象（身份、人工把关、RLS、按范围限定的记忆/缓存）已经以预留接口(seam)的方式接入，但默认处于关闭状态。

> **设计先行(Design-first)。** 设计的进展远远领先于构建：D1-D10 已经落定
> （参见 [`docs/design-decisions.md`](docs/design-decisions.zh.md)）。本仓库是脚手架。corpus 层已经实现；两个 harness 目前都只是带文档说明的占位实现(stub)。

## 三句话讲清楚核心思路

- **两套 harness 共享同一个基座(substrate)。** `curator`（构建）*生成*corpus；`server`（服务）*使用*corpus 来回答问题。二者风险特征相反，却共享同一个基座。
- **corpus 是护城河。** Git 跟踪的 YAML 类型化资产，加上 Markdown 技能(skill)文档，由 curator 撰写、经人工审核。Git 是唯一真实来源(source of truth)；graph、vector、BM25 存储都是可重建的投影(projection)。
- **失败即拒（fail-closed）。** 超出范围(out-of-scope)/覆盖缺失(missing-coverage)/触发护栏(tripped-guardrail)，都只会返回拒答或澄清性问题，绝不会给出一个自信却错误的数字。

## 文档

从 [`docs/README.md`](docs/README.zh.md) 开始阅读。核心文档：
[架构](docs/architecture.zh.md) ·
[设计决策(D1-D10)](docs/design-decisions.zh.md) ·
[资产模式](docs/asset-schemas.zh.md) ·
[curator](docs/curator.zh.md) · [server](docs/server.zh.md) · [viz](docs/viz.zh.md) ·
[术语表](docs/glossary.zh.md)。

## 仓库结构

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

各模块的 docstring 都会指回对应的设计文档与决策 ID。

## 使用与开发

需要 [uv](https://docs.astral.sh/uv/) 与 Python 3.13。

```bash
uv sync                                   # create .venv, install deps + package
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
```

完整的快速上手指南(validate CLI、可编程调用的 corpus API)见
[docs/usage.md](docs/usage.zh.md)。要编写或编辑 corpus 资产，参见
[docs/corpus-authoring.md](docs/corpus-authoring.zh.md)。

今天就可以在没有模型、没有网络的情况下运行：在已提交的 beer_factory 数据库上，
完整的问题到答案的 serve 流程（检索、上下文组装、模板化 SQL 生成、五层护栏、
受限自修复、可靠性标记）都能跑通，此外还有 curator 脚手架、memory、eval，以及
只读的 viz 驾驶舱。核心依赖刻意保持精简（pydantic、pyyaml、networkx、sqlglot）；
Postgres/Redshift 连接器是可选的 extra。两个 agent harness（server 是 LangGraph
的 `StateGraph`，curator 是 deepagents，两者都配合 LangChain 模型客户端）都放在
`agents` extra 背后，且在没有 key 的情况下，也能在确定性的离线模型替身(double)
上运行。

### 模型与配置

模型的选择集中在一个项目文件里，即 [`governed_bi.toml`](governed_bi.toml)，
由 `governed_bi.config.load_settings()` 解析：生成与 curation 使用 OpenAI 的
`gpt-5.5`（低推理强度），向量通道与 SQL 缓存使用 `text-embedding-3-small`。
全部都可以通过编辑该文件来替换。

```bash
uv sync --extra agents          # LangGraph + deepagents + LangChain model clients
uv sync --extra openai          # (alternative) the minimal raw-openai client only
export OPENAI_API_KEY=sk-...     # the key is read from the env, never stored
```

模型客户端是藏在 `ChatClient` / `Embedder` 协议(protocol)背后惰性导入的，且
各自都有一个确定性的离线默认实现（`StaticChatClient`、`HashingEmbedder`），
因此测试和默认流水线既不需要这个依赖，也不需要 key。若要使用真实模型，构建一个
LangChain 客户端并注入即可：

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

## 许可证

本仓库中的代码采用 MIT 许可证（见 [LICENSE](LICENSE)），
© 2026 Minhao Zhang。

随附的数据属于第三方，并采用单独的许可证。`data/bird/beer_factory.sqlite`
是来自 [BIRD benchmark](https://bird-bench.github.io/) 的 `beer_factory` 数据库，
按 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可未经修改收录；
详见 [`data/bird/NOTICE`](data/bird/NOTICE)。MIT 许可证不覆盖这份数据。
