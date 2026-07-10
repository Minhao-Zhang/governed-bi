# 演练：从克隆仓库到你的第一个受治理答案

_[English](walkthrough.md) · [简体中文](walkthrough.zh.md)_

一次从头到尾的完整走查：安装仓库、校验示例 corpus，然后提出你的第一个问题——
既通过 HTTP API，也从 Python 里。这里的所有步骤都可**离线**运行（无需 API key、
无需网络），针对已随仓库提交的 `beer_factory` 数据库，使用确定性的模板 SQL
生成器。最后有一个可选步骤，演示如何切换到真实模型。

走完之后，你会看到让本项目区别于普通 text-to-SQL 演示的两件事：一个**受治理的
答案**（带双轴可靠性标记与它实际运行的 SQL），以及一次**拒答**（当问题超出范围时
失败即拒，fail-closed）。

## 0. 前置条件

- [uv](https://docs.astral.sh/uv/)（包管理器 / 运行器）
- Python 3.13——如果没有，uv 会自动获取
- `git`

可选，仅用于最后的真实模型步骤：一个 OpenAI API key。

## 1. 克隆并安装

```bash
git clone https://github.com/Minhao-Zhang/governed-bi.git
cd governed-bi
uv sync
```

`uv sync` 会创建 `.venv`、安装核心依赖，并以可编辑模式安装 `governed_bi`。
确认安装成功：

```bash
uv run python -c "import governed_bi; print(governed_bi.__version__)"
```

已提交的 `data/bird/beer_factory.sqlite`（一个真实的 BIRD 数据库，CC BY-SA 4.0）
意味着整条流水线可以立刻运行——无需下载任何东西。

## 2. 校验 corpus

corpus 就是那个受治理的语义层：Git 跟踪的 YAML 资产 + Markdown 技能。校验器检查
ID 约定与引用完整性——一次绿灯运行就是 corpus 的"足够完成"信号（D9）。

```bash
uv run python -m governed_bi.corpus.cli
```

预期输出：

```
CI green: 16 assets, 1 skills, 0 findings.
```

（每次 push 时 CI 都会运行同一条命令。）

## 3. 运行测试

```bash
uv run pytest -q
```

离线即为绿灯。装上 harness 与 API 的 extra 后
（`uv run --extra agents --extra api pytest`），全部 **321** 个测试都会运行，
包括 LangGraph 等价性测试与 HTTP API 测试；不装则会跳过少数几个。

## 4. 提出你的第一个问题

有两种入口：HTTP API，或者几行 Python。两者驱动的是完全相同的受治理服务流程。

### 4a. 通过 HTTP API（推荐）

```bash
uv run --extra api uvicorn --factory governed_bi.api:create_app
```

这会在 http://localhost:8000 上提供受治理的 API（交互式文档在
http://localhost:8000/docs）。向 `/chat` 发起 POST 来提出你的第一个问题：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

你会得到一个受治理答案，其 JSON 里带有：

- **tier: governed**
- **safety_clearance: true** · **semantic_assurance: certified**
- 答案：`total_revenue = 18496.0`
- 它运行的 SQL：`SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"`
- 一个 **provenance** 轨迹（路由、指标、涉及的表、连接置信度）

现在问一个语义层**并不**覆盖的问题：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"How many employees work at the factory?"}'
```

系统不会去猜，而是**拒答**：

- **tier: refused**
- 一条升级提示：_"not answerable from this data - contact &lt;owner&gt;"_
- 没有 SQL，没有数字

这次拒答正是重点：范围内没有员工/薪酬数据，因此一个受治理的系统会如实说明，
而不是编造一个看似合理却错误的数字。

这个 API 是无状态的——要延续一段对话，在下一次 `/chat` 请求里把先前的轮次作为
`history` 回传（并使用稳定的 `session_id`）。

### 4b. 从 Python

同一条流程，作为一个可以嵌进你自己应用的小型 API：

```python
from governed_bi.config import Settings, Environment
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.server import answer_question

corpus = load_corpus("corpus", db="beer_factory").for_server()
conn = SqliteConnector("data/bird/beer_factory.sqlite")

ans = answer_question(
    "What is the total revenue?",
    Identity(user="demo", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=Settings.for_env(Environment.dev),
    session_id="demo",
)
print(ans.tier.value)            # governed
print(ans.safety_clearance)      # True
print(ans.semantic_assurance.value)  # certified
print(ans.sql)                   # SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"
print(ans.text)                  # total_revenue = 18496.0
conn.close()
```

## 5. 你正在看的是什么

- **双轴标记是最诚实的部分。** `safety_clearance` 是一道闸门——这条 SQL 是否通过了
  全部五层护栏、并以请求者身份执行？`semantic_assurance`（`certified` /
  `heuristic` / `unverified`）则是答案的*接地程度*。二者刻意分开：一条查询可以
  完全安全，却仍是错误的计算，所以"安全"绝不能被读成"正确"。（见 [server.md](server.zh.md)。）
- **你可以审计这条 SQL。** 模型的输出被当作不可信；实际运行的 SQL 会被展示，而且它
  只会触及 corpus 授权的列/表。
- **拒答是一项特性。** 覆盖缺失、触发护栏、或命中一条经过整理的越界模式，都会失败
  即拒。它的制衡面——不去拒答那些本可回答的问题——由评测里的误拒率来度量。

## 6.（可选）切换到真实模型

离线时，确定性的模板生成器只回答指标/KPI 类问题，并忽略对话上下文。要使用真实
模型——它能启用自由形式的 SQL，以及聊天里带上下文的追问——设置一个 key 并装上
`agents` extra：

```bash
export OPENAI_API_KEY=sk-...        # 从环境变量读取，绝不存进仓库
uv run --extra agents --extra api uvicorn --factory governed_bi.api:create_app
```

模型是 `gpt-5.5`、低推理强度（在 [`governed_bi.toml`](../governed_bi.toml) 里
配置），通过 LangChain 的 `ChatOpenAI` 调用——它会把推理模型路由到 OpenAI 的
**Responses API**。通过 `/chat`，追问此时会针对对话进行消解（先前的轮次通过引擎的
工作记忆回灌）。

想要一次脚本化的真实检查（在 `beer_factory` 上打印执行准确率、拒答与诱饵触碰），
运行：

```bash
uv run --extra agents python scripts/live_smoke.py
```

## 下一步

- [用法](usage.zh.md)——更完整的快速上手（校验 CLI、corpus API、gateway）。
- [Corpus 撰写](corpus-authoring.zh.md)——逐步撰写并校验你自己的资产。
- [系统总览](system-overview.zh.md) → [架构](architecture.zh.md)——这一切背后的设计。
- [Server](server.zh.md)——深入服务流程、护栏与可靠性标记。
