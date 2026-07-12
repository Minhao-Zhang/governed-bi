# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

一个 agentic BI / Generative-BI 系统：自然语言问题 → 基于关系型数据的**接地（grounded）、受治理（governed）、可审计（auditable）**的答案。

近期目标是打造一个**在 SQLite 上得到验证的展示系统**（对其他数据库引擎留有方言可插拔的接口），它从一批已知良好的种子查询出发、逐步扩展出一个可审阅的语义层——这是**种子辅助的语义层生长(seed-assisted semantic-layer growth)**，而非零先验的冷启动——并在自建的 [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) 数据集上进行评测（执行准确率）。企业级抽象（身份/RLS、人工把关、按范围限定的记忆/缓存）已经以预留接口(seam)的方式接入，但默认关闭；其**强制执行属于私有的企业分支，而非本引擎**。

> **设计先行，且对成熟度诚实。** 设计（D1-D15）的进展远远领先于构建
> （参见 [`docs/design-decisions.md`](docs/design-decisions.zh.md)）。确定性核心可以**离线**端到端运行；LangGraph/deepagents 两个 harness 已经构建，但仅在确定性的模型替身(model doubles)上运行过——**尚未针对任何真实模型跑过**。哪些已被证明、哪些仅是设计，见下方[状态表](#状态)。

## 三句话讲清楚核心思路

- **两套 harness 共享同一个基座(substrate)。** `curator`（构建）*生成*corpus；`server`（服务）*使用*corpus 来回答问题。二者风险特征相反，却共享同一个基座。
- **corpus 是预期的护城河**——这是一个有待评测证明的**假设**，而非已被证实的结果。Git 跟踪的 YAML 类型化资产，加上 Markdown 技能(skill)文档，由 curator 撰写、经人工审核。Git 是唯一真实来源(source of truth)；graph、vector、BM25 存储都是可重建的投影(projection)。
- **失败即拒（fail-closed）。** 超出范围/覆盖缺失/触发护栏，都只会返回拒答或澄清性问题，绝不会给出一个自信却错误的数字。护栏是安全闸门，**不是正确性判官(correctness oracle)**——因此答案携带两个相互独立的标记：`safety_clearance`（是否通过护栏）与 `semantic_assurance`（接地程度如何），二者绝不折叠成单一的"可信度分数"。

## 状态

哪些已被证明、哪些仅是设计、哪些只是预留接口。**最大的未决风险是：LLM 生成器与 curator 从未针对真实模型运行过**——下表所有测试都使用确定性离线替身，因此真实的生成/策展质量尚未被度量。

| 能力 | 状态 | 证据 |
|---|---|---|
| SQLite 受治理服务路径（检索 → 上下文 → SQL 生成 → 五层护栏 → 执行 → 标记） | **已构建** | `uv run --extra agents --extra api pytest`——321 个离线测试 |
| corpus 契约 + 校验（类型化 YAML/MD、ID + 引用完整性） | **已构建** | `python -m governed_bi.corpus.cli`、CI |
| 有界自修复 + 双轴可靠性标记 | **已构建** | `tests/test_server.py` |
| 语义 SQL 缓存（命中时重新过护栏 + 重新执行，仅 `certified` 准入） | **已构建，默认关闭** | `tests/test_cache.py` |
| LangGraph 服务 harness（与核心 flow 的 Answer 等价） | **已构建，仅离线** | `tests/test_serve_graph.py` 等价性 |
| deepagents curator harness | **仅构建** | `tests/test_curator_deep_agent.py`（无真实运行） |
| 真实模型的生成/策展质量 | **未证明** | 需要 `OPENAI_API_KEY` + 一次真实运行 |
| BIRD-Obfuscation 三臂评测（no-layer / curator / gold） | **部分** | curator 臂已离线评分；混淆 DB + 基线/gold 臂待做 |
| `CorpusRelease`（不可变、按内容哈希锁定的服务发布） | **仅设计** | 未实现——见[设计决策](docs/design-decisions.zh.md) |
| 身份 → 查询范围（RLS / 租户隔离） | **仅接口** | 单一身份的 SQLite 展示；强制执行属企业分支范围 |
| Postgres / Redshift 执行 | **已构建，未经真实测试** | `PostgresConnector`（information_schema）+ `RedshiftConnector`（svv_*）；离线 fake 连接测试，未连真实服务器 |

**诚实的一句话定位：** 一个把模型输出当作不可信的受治理 NL2SQL 内核——它约束可访问的数据面、对生成的 SQL 做结构化校验、将策展与服务分离、并让语义层可审阅。已在 SQLite 上得到验证、以评测为导向；下一个里程碑是证明 curator 构建的资产在混淆 schema 上、相对一个公平的无 corpus 基线，能带来可度量的提升。

## Web 界面

前端在独立仓库中：
[Minhao-Zhang/governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)
(Next.js、`useStream`)。目前仍是纯 mock,尚未与本后端端到端接通。

## 文档

从 [`docs/README.md`](docs/README.zh.md) 开始阅读。核心文档：
[架构](docs/architecture.zh.md) ·
[设计决策(D1-D15)](docs/design-decisions.zh.md) ·
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

各模块的 docstring 都会指回对应的设计文档与决策 ID。

## 使用与开发

需要 [uv](https://docs.astral.sh/uv/) 与 Python 3.13。

```bash
uv sync                                   # create .venv, install deps + package
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
```

新手？[演练](docs/walkthrough.zh.md)是一份引导式的“克隆 → 第一个问题”教程。
[快速上手](docs/usage.zh.md)是参考（validate CLI、可编程调用的 corpus API）；要编写或
编辑 corpus 资产，参见 [corpus 编写](docs/corpus-authoring.zh.md)。

今天就可以在没有模型、没有网络的情况下运行：在已提交的 beer_factory 数据库上，
完整的问题到答案的 serve 流程（检索、上下文组装、模板化 SQL 生成、五层护栏、
受限自修复、可靠性标记）都能跑通，此外还有 curator 脚手架、memory、eval，以及
只读的审计面（presenter 视图模型 + `governed_bi.api` HTTP API）。核心依赖刻意保持精简（pydantic、pyyaml、networkx、sqlglot）；
Postgres/Redshift 连接器是可选的 extra。两个 agent harness（server 是 LangGraph
的 `StateGraph`，curator 是 deepagents，两者都配合 LangChain 模型客户端）都放在
`agents` extra 背后，且在没有 key 的情况下，也能在确定性的离线模型替身(double)
上运行。

### 模型与配置

所有非密钥策略集中在一个项目文件 [`governed_bi.toml`](governed_bi.toml)，由
`governed_bi.config.load_settings()` 解析：环境开关、模型、数据源形态、corpus
路径与 serve 标志。本机覆盖写在同目录下 git-ignored 的
`governed_bi.local.toml`（同表结构，本地覆盖优先）。密钥（API key、DSN 密码）
只放在环境变量或 git-ignored 的 `.env` 里。

```bash
uv sync --extra agents          # LangGraph + deepagents + LangChain model clients
uv sync --extra openai          # (alternative) the minimal raw-openai client only
export OPENAI_API_KEY=sk-...     # the key is read from the env, never stored
```

密钥从环境变量读取。若不想 export，把 [`.env.example`](.env.example) 复制为仓库
根目录的 `.env` 并写入密钥即可——导入时加载，且不会覆盖已在 shell 中设置的变量。
`.env` 已被 git-ignore；切勿提交真实密钥。本地要切到 Postgres 时，把
`[datasource]` 写进 `governed_bi.local.toml`，DSN 值放进 `.env`。

可选可观测性（详见 [`.env.example`](.env.example)）：

```bash
# LangSmith（原生；无需额外包）
export LANGSMITH_TRACING=true          # 或 LANGCHAIN_TRACING_V2=true
export LANGSMITH_API_KEY=lsv2_...

# Langfuse（LangChain callback）
uv sync --extra tracing
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
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

要跑一遍**真实**路径（离线测试唯一无法覆盖的部分），运行 live smoke 脚本——
它会用 LLM 生成器 + 真实 embedding 在 beer_factory 上跑一遍，并报告
EX / 拒答 / 诱饵触碰：

```bash
export OPENAI_API_KEY=sk-...
uv run --extra agents python scripts/live_smoke.py
```

## 许可证

本仓库中的代码采用 MIT 许可证（见 [LICENSE](LICENSE)），
© 2026 Minhao Zhang。

随附的数据属于第三方，并采用单独的许可证。`data/bird/beer_factory.sqlite`
是来自 [BIRD benchmark](https://bird-bench.github.io/) 的 `beer_factory` 数据库，
按 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可未经修改收录；
详见 [`data/bird/NOTICE`](data/bird/NOTICE)。MIT 许可证不覆盖这份数据。
