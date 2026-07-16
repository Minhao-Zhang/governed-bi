# governed-bi

_[English](README.md) · [简体中文](README.zh.md)_

一个 agentic BI 系统：针对关系型数据库回答自然语言问题，给出**接地（grounded）、受治理（governed）、可审计（auditable）**的 SQL。把它指向一个实时的 **Postgres** 数据库，给它一个模型 key，然后提问。它会从经过策展的语义层中检索相关切片，生成 SQL，过五层护栏，只读执行，并返回带审计轨迹的答案。

连接器层按方言可插拔：Postgres 是经过实测的实时路径，Redshift 已接入预留接口（seam），SQLite 只作为离线测试 / CI 用的基座保留，不是我们建议任何人部署的运行时。

## 工作原理

- **两套 harness，一个基座（substrate）。** `curator`（构建）从一批已知良好的 `(question, SQL)` 种子对中*生成*语义层（corpus）；`analyst`（服务）*使用*它来回答问题。二者风险特征相反，却共享同一个 corpus。
- **corpus 就是护城河。** Git 跟踪的类型化 YAML 资产，加上 Markdown 技能（skill）文档，由 curator 撰写、经人工审核。Git 是唯一真实来源（source of truth）；graph / vector / BM25 存储都是可重建的投影（projection）。
- **失败即拒（fail-closed）。** 超出范围、覆盖缺失，或触发了护栏，都只会返回拒答或澄清性问题，绝不会给出一个自信却错误的数字。答案携带两个相互独立的标记：`safety_clearance`（是否通过护栏）与 `semantic_assurance`（接地程度如何），二者绝不会合并成单一的信任分数。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/) 与 Python 3.13。

```bash
uv sync                       # 创建 .venv，安装默认（OpenAI）栈
uv sync --extra bedrock       # ……或额外装上 AWS Bedrock provider
```

**1. 指向你的数据库。** 已提交的默认配置是一个随附的 SQLite fixture（`beer_factory`），它**只为
demo 和测试而存在**——不要在它之上做真正的开发。除 demo 以外的任何用途，都应服务你自己的数据库：在
[`governed_bi.toml`](governed_bi.toml) 旁边添加一个 git-ignored 的 `governed_bi.local.toml`：

```toml
[datasource]
kind = "postgres"
corpus_pin = "your_schema"       # corpus subtree / BIRD db_id
dsn_env = "PG_DSN"       # names the env var that holds the DSN
```

**2. 设置密钥**，写在仓库根目录一个 git-ignored 的 `.env` 里（切勿提交这些密钥）：

```bash
OPENAI_API_KEY=sk-...
PG_DSN=host=... port=5432 dbname=... user=... password=...
```

**3. 启动服务并提问。** Serving 是纯 agent 路径，需要一个真实模型，因此**没有 key 就会失败即拒**：

```bash
uv run langgraph dev          # starts the `serve` graph; POST questions to /chat
```

[演练](docs/walkthrough.zh.md)是一份引导式的「第一个问题」教程，[快速上手](docs/usage.zh.md)是完整参考。要直接用 Python 驱动 agent 核心，见 [`docs/analyst.md`](docs/analyst.zh.md)。

## 配置

所有非密钥的策略都集中在一个文件里：[`governed_bi.toml`](governed_bi.toml)（由
`governed_bi.config.load_settings()` 解析），包括运行时开关、模型、数据源、corpus 路径、serve
标志。机器本地的覆盖写在同目录下 git-ignored 的 `governed_bi.local.toml` 里（表结构相同，本地优先）。
密钥（API key、DSN 密码）只存在于环境变量或 git-ignored 的 `.env` 里，`.env` 在导入时加载，且不会
覆盖已经导出的变量。

可选的 tracing（LangSmith 或 Langfuse）记录在 [`.env.example`](.env.example) 里。

## 开发

除了实时 serving 之外，其余一切都能在没有模型、没有网络的情况下离线运行，跑在内置的 SQLite
fixture 上：

```bash
uv run python -m governed_bi.corpus.cli   # validate the corpus (ID + reference integrity)
uv run pytest                             # run the test suite
uv run python scripts/live_smoke.py       # end-to-end over a real model (needs OPENAI_API_KEY)
```

离线测试套件是拿确定性的模型替身（model double）跑 serve 核心，因此既不需要 key，也不需要网络。
默认 OpenAI 栈下两套 harness 需要的一切依赖（LangGraph、deepagents、LangChain、OpenAI、Langfuse、psycopg）
都在 `[project.dependencies]` 里，所以一次普通的 `uv sync` 就能装齐。唯一的 extra 是 `bedrock`
（`uv sync --extra bedrock`）：它会拉入 `langchain-aws` + boto3，用于 AWS Bedrock provider——
在 `[models]` 里设 `provider = "bedrock"` 即可启用。

## 状态

已构建并测试：受治理的 agentic serve 核心（[ADR
0002](docs/adr/0002-governed-agentic-serve-runtime.md)：一个确定性的 rails 图，外层包着一个受限的
`create_agent` 循环，循环之下是若干只读工具，配合护栏 + 审计中间件）、corpus 契约与校验器、五层
护栏、curator、检索、eval harness、语义 SQL 缓存，以及只读的审计 API。

「corpus 即护城河」这一论断，已经在一个混淆版 Postgres 数据库上跑出了第一个真实结果：curator
构建的资产相比无 corpus 基线提升了执行准确率，并把 decoy-touch 压到零。但它只用了单一随机种子、
样本量也小，所以这个结果是方向性的，尚不能下定论。当前的里程碑是**规模化运行**——把全部 69 个
BIRD 库作为 Postgres schema 加载（8,134 训练 / 2,030 测试），用大规模留出测试集取代单种子差值
作为证据单位（见[审计处置](docs/design-decisions.md#audit-dispositions-2026-07-15)）。完整数据与方法见：[实验结果](docs/plans/eval-ladder-results.md)。

仅设计、尚未构建：`CorpusRelease`（不可变、按内容哈希锁定的服务发布）。已接入预留接口（seam）但
默认关闭（属于企业分支范围）：身份 → 查询范围（RLS / 租户隔离）、人工审批闸门、按范围限定的
记忆/缓存。Redshift 只有离线连接器测试。

## Web 界面

前端在一个独立仓库中：
[Minhao-Zhang/governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)（Next.js、
`useStream`），面向本后端的流式对话契约开发。其构建进度请以该仓库为准——本仓库不再跟踪。

## 文档

从 [`docs/README.md`](docs/README.zh.md) 开始阅读。核心文档：
[架构](docs/architecture.zh.md) · [设计决策](docs/design-decisions.zh.md) ·
[资产模式](docs/asset-schemas.zh.md) · [curator](docs/curator.zh.md) ·
[analyst](docs/analyst.zh.md) · [viz](docs/viz.zh.md) · [术语表](docs/glossary.zh.md)。

## 仓库结构

```
docs/               design docs (canonical)
corpus/             the semantic layer (Git = source of truth); worked example under beer_factory/
data/bird/          beer_factory.sqlite: offline test/CI fixture (BIRD, CC BY-SA 4.0; see NOTICE)
src/governed_bi/
  config.py         environment toggles, models, datasource shape (load_settings)
  llm/              ChatClient / Embedder seams (OpenAI + LangChain + offline defaults)
  corpus/           schemas, IDs, CI validator, loader, serializer, CLI
  gateway/          connectors (SQLite / Postgres / Redshift), read-only gateway, five-layer guardrails
  curator/          Facts profiling, proposers, adversary review, curate loop, deepagents build harness
  graph/            FK graph projection + Steiner-tree join planning
  retrieval/        BM25 + grounding + vector channel (RRF fusion)
  memory/           working memory; episodic/correction seams
  analyst/          the ADR-0002 governed agentic core (sole serve path): agent, tools, middleware, governance, cache, stamp
  eval/             execution accuracy, arm harness, refuse-gate
  viz/              read-only audit surface (UI-agnostic presenter view models)
tests/              unit + end-to-end suites
```

## 许可证

代码采用 MIT 许可证（见 [LICENSE](LICENSE)），© 2026 Minhao Zhang。

随附的数据属于第三方，采用单独的许可证：`data/bird/beer_factory.sqlite` 是来自
[BIRD benchmark](https://bird-bench.github.io/) 的 `beer_factory` 数据库，按
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可未经修改收录；
详见 [`data/bird/NOTICE`](data/bird/NOTICE)。MIT 许可证不覆盖这份数据。
