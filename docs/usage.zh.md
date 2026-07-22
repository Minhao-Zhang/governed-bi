# 用法（快速上手）

_[English](usage.md) · [简体中文](usage.zh.md)_

> 新手？[演练](walkthrough.zh.md)是一份引导式的“克隆 → 第一个问题”教程；本页是参考性的快速上手。

从提问到应答的完整流水线，如今已经可以在已提交的 `beer_factory` 数据库上端到端运行。
corpus 校验、gateway 与检索都不需要模型或网络（embedder 仍会回退到一个基于哈希的
确定性默认实现）；serve 现在是**纯智能体路径**（ADR 0002），没有实时模型就会失败即拒
（fail closed）。本页只聚焦于当下可运行的部分；如需了解其背后的设计，请参见
[设计文档](README.zh.md)。

| 区域 | 状态 | 位置 |
|---|---|---|
| corpus 的模式、ID、验证器、加载器、序列化器、CLI | 可运行 | `src/governed_bi/corpus/` |
| 示例 corpus（`beer_factory`，真实 BIRD 数据库） | 可运行 | `corpus/beer_factory/` |
| SQLite 连接器 + gateway（只读、带审计）+ 五层护栏 | 可运行 | `src/governed_bi/gateway/` |
| Curator：Facts 层画像分析、启发式 proposer 与 LLM proposer、adversary、curate 循环 | 可运行 | `src/governed_bi/curator/` |
| 图投影 + Steiner 连接规划 | 可运行 | `src/governed_bi/graph/` |
| 检索（BM25 + 接地，外加由 embedder 门控的向量通道） | 可运行 | `src/governed_bi/retrieval/` |
| serve（智能体核心：路由、上下文、受治理的工具、护栏、自修复、缓存、标记） | 可运行，需要实时模型 | `src/governed_bi/analyst/` |
| Memory（working）+ eval（EX、评测阶梯（ladder）、refuse-gate）+ viz presenter（审计视图模型） | 可运行 | `src/governed_bi/{memory,eval,viz}/` |
| 模型客户端（原生 OpenAI / LangChain） | 可运行（由一次普通的 `uv sync` 安装，不需要任何 extra） | `src/governed_bi/llm/` |
| 智能体 harness（LangGraph 受治理 serve 核心、deepagents 的 curator） | 可运行（由一次普通的 `uv sync` 安装，不需要任何 extra） | `analyst/agent.py`, `curator/deep_agent.py` |
| Postgres / Redshift 连接器 | 已实现（基于 psycopg，由一次普通的 `uv sync` 安装）；Postgres 已由评测框架（`eval/run_experiment.py`，对本地 BIRD-Obfuscation Postgres）实际运行验证，Redshift 复用 Postgres 路径但尚未连过真实集群 | `src/governed_bi/gateway/connectors/` |

## 前置条件

- [uv](https://docs.astral.sh/uv/)
- Python 3.13（如果缺失，uv 会自动获取；版本号已经在 `.python-version` 中锁定）

## 安装

```bash
uv sync
```

这会创建 `.venv`、安装依赖，并以可编辑模式（editable mode）安装 `governed_bi`。检查是否
安装成功：

```bash
uv run python -c "import governed_bi; print(governed_bi.__version__)"
```

## 验证 corpus

corpus 的 CLI 会检查 ID 规范和引用完整性。一次绿色的运行，就是该 corpus「足够完备」
（done-enough）的信号（D9）。

```bash
# validate the bundled example (defaults to the corpus/ directory)
uv run python -m governed_bi.corpus.cli corpus/beer_factory

# validate everything under corpus/
uv run python -m governed_bi.corpus.cli

# see all options
uv run python -m governed_bi.corpus.cli --help
```

成功时的输出：

```
CI green: 17 assets, 0 findings.
```

验证失败时，会列出每一条问题项（例如 `dangling-ref [metric_revenue]:
metric.base_table -> 'tbl_missing' does not resolve`），并以非零状态退出。

退出码：`0` 为绿色，`1` 为存在问题项，`2` 为用法错误或路径不存在。这使它可以用作 CI
关卡。物理存在性检查（即列在真实数据库中确实存在）和 few-shot 泄漏防护（few-shot
leakage guard）不会在此运行；它们需要数据库连接或 eval 的数据切分（split），因此属于
eval harness 的范畴。

## 在 Python 中使用 corpus

同样的加载器、模式与验证器，构成了一个小型的公共 API。所有内容都会被解析为带类型的
Pydantic 模型，因此格式有误的资产会立刻失败并报错。

```python
from pathlib import Path
from governed_bi.corpus import load_corpus, validate_corpus, is_green, parse_asset

# Load a schema's corpus (YAML typed assets) into models.
corpus = load_corpus(Path("corpus"), schema="beer_factory")
print(len(corpus.assets), "assets")

# Run the same checks the CLI runs.
findings = validate_corpus(corpus.assets)
assert is_green(findings), findings

# The Analyst-visible view: Audit tier stripped, governance.excluded removed.
analyst_view = corpus.for_analyst()

# Parse a single asset from a dict (raises pydantic.ValidationError if invalid).
table = parse_asset({
    "asset_type": "table",
    "id": "tbl_demo_orders",
    "schema": "demo",
    "physical_name": "t_1",
})
print(table.id, table.asset_type)
```

`Corpus.for_analyst()` 是代码中定义的消费契约：它就是 Analyst 被允许看到的内容
（Facts + Inference，绝不包含 Audit，也绝不包含被排除的资产）。

## 连接数据库

gateway 封装了按方言区分的连接器。SQLite 已充分验证（只读，带审计日志和强制的行数上限）；
Postgres（`information_schema`）已由评测框架（`eval/run_experiment.py`，对本地
BIRD-Obfuscation Postgres）实际运行验证并有离线单元测试；Redshift（`svv_*`）复用
Postgres 路径但尚未连过真实集群（两者都基于 psycopg，由一次普通的 `uv sync` 安装）。将它指向一个 SQLite 文件，
就可以查看数据库目录、对 Facts 层做画像分析，并运行受护栏保护的查询：

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/mydb.sqlite")     # opens read-only
tables = profile_database(conn, schema="mydb")       # Facts-tier table assets
gw = Gateway(conn)
result = gw.execute(
    "SELECT COUNT(*) FROM some_table",
    Identity(user="dev", all_access=True),
)
print(result.rows, gw.audit_log)
```

关于如何在本地引入（vendor）一个小型的 BIRD SQLite 文件，参见
[`data/README.md`](../data/README.zh.md)。有了它之后，`validate_corpus(assets,
connector=conn)` 还会运行物理存在性检查（确保每个 `physical_name` 都存在于真实的数据库
目录中）。

## 提问（serve 流水线）

serve 现在是纯智能体路径（ADR 0002）：`create_agent` + `GovernanceMiddleware` +
受治理的只读工具，外面包了一层 LangGraph rails 图，负责给问题路由、检查语义缓存、
运行智能体核心，并为答案打上标记。回答问题不再有确定性的兜底实现，它需要实时
模型，没有模型就会失败即拒（fail closed），而不是去猜：

```python
from pathlib import Path
from governed_bi.config import load_settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.llm import LangChainChatClient
from governed_bi.analyst.agent import answer_question_agent

settings = load_settings()
corpus = load_corpus(Path(settings.corpus_root), schema="beer_factory").for_analyst()
conn = SqliteConnector(settings.datasource.sqlite_path)
chat = LangChainChatClient.from_config(settings.models)  # 需要 OPENAI_API_KEY
ans = answer_question_agent(
    "What is the total revenue?",
    Identity(user="dev", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=settings,
    session_id="s",
    model=chat.model,  # 智能体核心实际驱动的、原生的 LangChain 模型
)
print(ans.tier, ans.sql, ans.text)  # -> ReliabilityTier.governed  SELECT ...  total_revenue = ...
```

这需要一个真实的 key，并按上面那样注入 client——这些 agent harness（curator 与
serve）都由一次普通的 `uv sync` 安装，不需要任何 extra；详见
[README](../README.zh.md) 中的 **Models & configuration** 一节。API key 从
`[models].api_key_env` 命名的环境变量读取（默认 `OPENAI_API_KEY`），绝不会被存储。
deepagents 的 curator 也需要同一个 key。

## 审计界面（viz presenter + API）

本仓库**不附带任何 UI**。只读的审计/审查界面由两个与 UI 无关的部分组成：
`governed_bi.viz.presenter` 视图模型（corpus 健康度、表/档位视图、关系/知识图谱、
资产列表，以及某个答案的双轴可靠性标记，不依赖任何 UI），以及
`governed_bi.api` 这个 FastAPI HTTP/JSON API，它在 `POST /chat` 处提供这些视图模型
以及受治理的智能体核心。视图模型相关的端点（`/health`、`/schema`、`/graph`、
`/corpus/assets` 等）不需要模型；`/chat` 需要，没有模型会返回 `503`。
运行该 API：

```bash
uv run uvicorn --factory governed_bi.api:create_app
```

随后在 http://localhost:8000/docs 打开交互式文档，或直接 POST 一个问题（需要设置
`OPENAI_API_KEY`——智能体 harness 由一次普通的 `uv sync` 安装）：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

策略来自 [`governed_bi.toml`](../governed_bi.toml)（corpus 路径、数据源、serve
标志）。本机覆盖写在 git-ignored 的 `governed_bi.local.toml`。由于展示逻辑位于与
UI 无关的 `governed_bi.viz.presenter` 中（不依赖任何 UI 框架），一个独立的前端可以
消费同样的视图模型——交互式 UI 是一个独立项目，参见
[docs/ui-frontend-design.md](ui-frontend-design.zh.md)。

## 运行测试

```bash
uv run pytest -q
```

## 接下来

- 如需编写或编辑 corpus 资产，请参见 [Corpus authoring](corpus-authoring.zh.md)。
- 如需查看逐字段的资产规格，请参见 [Asset schemas](asset-schemas.zh.md)。
- 如需了解这一切背后的设计，请从 [docs/README.md](README.zh.md) 开始阅读。
