# 用法（快速上手）

_[English](usage.md) · [简体中文](usage.zh.md)_

> 新手？[演练](walkthrough.zh.md)是一份引导式的“克隆 → 第一个问题”教程；本页是参考性的快速上手。

从提问到应答的完整流水线，如今已经可以在已提交的 `beer_factory` 数据库上端到端运行，
且不需要模型或网络：它会回退到确定性的离线默认实现（一个模板化的 SQL 生成器、一个基于
哈希的 embedder）。本页只聚焦于当下可运行的部分；如需了解其背后的设计，请参见
[设计文档](README.zh.md)。

| 区域 | 状态 | 位置 |
|---|---|---|
| corpus 的模式、ID、验证器、加载器、序列化器、CLI | 可运行 | `src/governed_bi/corpus/` |
| 示例 corpus（`beer_factory`，真实 BIRD 数据库） | 可运行 | `corpus/beer_factory/` |
| SQLite 连接器 + gateway（只读、带审计）+ 五层护栏 | 可运行 | `src/governed_bi/gateway/` |
| Curator：Facts 层画像分析、启发式 proposer 与 LLM proposer、adversary、curate 循环 | 可运行 | `src/governed_bi/curator/` |
| 图投影 + Steiner 连接规划 | 可运行 | `src/governed_bi/graph/` |
| 检索（BM25 + 接地，外加由 embedder 门控的向量通道） | 可运行 | `src/governed_bi/retrieval/` |
| serve 流程（路由、上下文、SQL 生成、护栏、自修复、缓存、标记） | 可运行 | `src/governed_bi/server/` |
| Memory（working）+ eval（EX、arms、refuse-gate）+ viz presenter（审计视图模型） | 可运行 | `src/governed_bi/{memory,eval,viz}/` |
| 模型客户端（原生 OpenAI / LangChain） | 可运行（需启用 `openai` / `agents` 可选依赖组） | `src/governed_bi/llm/` |
| 智能体 harness（LangGraph 的 serve DAG、deepagents 的 curator） | 可运行（需启用 `agents` 可选依赖组） | `server/graph.py`, `curator/deep_agent.py` |
| Postgres / Redshift 连接器 | 已实现（需可选依赖组）；离线测试，未连真实库 | `src/governed_bi/gateway/connectors/` |

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
CI green: 16 assets, 1 skills, 0 findings.
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

（D15 将 corpus 的 `db` 命名空间改名为 `schema`；已决定、尚未落地——下面这些 `db=`
示例仍与当前代码保持一致。）

```python
from pathlib import Path
from governed_bi.corpus import load_corpus, validate_corpus, is_green, parse_asset

# Load a DB's corpus (YAML assets + Markdown skills) into typed models.
corpus = load_corpus(Path("corpus"), db="beer_factory")
print(len(corpus.assets), "assets;", len(corpus.skills), "skills")

# Run the same checks the CLI runs.
findings = validate_corpus(corpus.assets)
assert is_green(findings), findings

# The server-visible view: Audit tier stripped, governance.excluded removed.
server_view = corpus.for_server()

# Parse a single asset from a dict (raises pydantic.ValidationError if invalid).
table = parse_asset({
    "asset_type": "table",
    "id": "tbl_demo_orders",
    "db": "demo",
    "physical_name": "t_1",
})
print(table.id, table.asset_type)
```

`Corpus.for_server()` 是代码中定义的消费契约：它就是 server 被允许看到的内容
（Facts + Inference，绝不包含 Audit，也绝不包含被排除的资产）。

## 连接数据库

gateway 封装了按方言区分的连接器。SQLite 已充分验证（只读，带审计日志和强制的行数上限）；
Postgres（`information_schema`）与 Redshift（`svv_*`）也已实现，位于 `postgres` / `redshift`
可选依赖组之后，并有离线单元测试，但尚未连过真实服务器。将它指向一个 SQLite 文件，
就可以查看数据库目录、对 Facts 层做画像分析，并运行受护栏保护的查询：

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/mydb.sqlite")     # opens read-only
tables = profile_database(conn, db="mydb")           # Facts-tier table assets
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

serve 流程会对问题进行路由，检索并组装上下文，生成 SQL，执行五层护栏检查，以最终用户
身份（as-user）执行，并为答案打上可靠性标记。在没有模型的情况下，它会使用确定性的模板
生成器（面向 metric / KPI 类问题）：

```python
from pathlib import Path
from governed_bi.config import Settings, Environment
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.server import answer_question

corpus = load_corpus(Path("corpus"), db="beer_factory").for_server()
conn = SqliteConnector("data/bird/beer_factory.sqlite")
ans = answer_question(
    "What is the total revenue?",
    Identity(user="dev", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=Settings.for_env(Environment.dev),
    session_id="s",
)
print(ans.tier, ans.sql, ans.text)  # -> ReliabilityTier.governed  SELECT ...  total_revenue = ...
```

如果要使用真实的 OpenAI 模型（LLM generator、embeddings、SQL cache），或者 LangGraph 的
serve harness（`answer_question_graph`）以及 deepagents 的 curator，需要安装 `agents`
可选依赖组并注入相应的 client，参见 [README](../README.zh.md) 中的 **Models &
configuration** 一节。API key 从 `OPENAI_API_KEY` 读取，绝不会被存储。

## 审计界面（viz presenter + API）

本仓库**不附带任何 UI**。只读的审计/审查界面由两个与 UI 无关的部分组成：
`governed_bi.viz.presenter` 视图模型（corpus 健康度、表/档位视图、关系/知识图谱、
资产列表、技能，以及某个答案的双轴可靠性标记——不依赖任何 UI），以及
`governed_bi.api` 这个 FastAPI HTTP/JSON API，它在 `POST /chat` 处提供这些视图模型
以及受治理的 serve 流程。运行该 API（需启用可选依赖组 `api`）：

```bash
uv run --extra api uvicorn --factory governed_bi.api:create_app
```

随后在 http://localhost:8000/docs 打开交互式文档，或直接 POST 一个问题：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

设置 `GOVERNED_BI_CORPUS` 和 `GOVERNED_BI_SQLITE`，可以让它指向另一个
corpus / 数据库。由于展示逻辑位于与 UI 无关的 `governed_bi.viz.presenter` 中（不依赖任何
UI 框架），一个独立的前端可以消费同样的视图模型——交互式 UI 是一个独立项目，参见
[docs/ui-frontend-design.md](ui-frontend-design.zh.md)。

## 运行测试

```bash
uv run pytest -q
```

## 接下来

- 如需编写或编辑 corpus 资产，请参见 [Corpus authoring](corpus-authoring.zh.md)。
- 如需查看逐字段的资产规格，请参见 [Asset schemas](asset-schemas.zh.md)。
- 如需了解这一切背后的设计，请从 [docs/README.md](README.zh.md) 开始阅读。
