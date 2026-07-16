# 演练：从克隆仓库到你的第一个受治理答案

_[English](walkthrough.md) · [简体中文](walkthrough.zh.md)_

一次从头到尾的完整走查：安装仓库、校验示例 corpus，然后提出你的第一个问题——
既通过 HTTP API，也从 Python 里。克隆、校验 corpus、运行测试都可**离线**运行
（无需 API key、无需网络），针对已随仓库提交的 `beer_factory` 数据库。serve
现在是**纯智能体路径**（ADR 0002），提问需要实时模型，没有模型就会失败即拒。
在进入第 4 步之前先设置好 OpenAI API key。最后一步会讲模型配置，以及一次
脚本化的真实检查。

走完之后，你会看到让本项目区别于普通 text-to-SQL 演示的两件事：一个**受治理的
答案**（带双轴可靠性标记与它实际运行的 SQL），以及一次**拒答**（当问题超出范围时
失败即拒，fail-closed）。

## 0. 前置条件

- [uv](https://docs.astral.sh/uv/)（包管理器 / 运行器）
- Python 3.13——如果没有，uv 会自动获取
- `git`

第 4 步需要：一个 OpenAI API key。serve 现在是纯智能体路径，提问需要实时模型；
只读的审计端点（`/health`、`/schema`、`/graph` 等）不需要 key。

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

离线即为绿灯。全部 **470** 个测试默认就会运行（`uv run pytest`）：
**462 个通过**、**8 个跳过**：跳过的都是只能靠实时模型才能验证的检查
（智能体生成质量），改由 `scripts/live_smoke.py` 来覆盖。

## 4. 提出你的第一个问题

有两种入口：HTTP API，或者几行 Python。两者驱动的是完全相同的受治理 Analyst 流程。

### 4a. 通过 HTTP API（推荐）

serve 需要实时模型（回答问题没有离线兜底）；只读端点（`/health`、`/schema`、
`/graph` 等）不需要：

```bash
export OPENAI_API_KEY=sk-...        # 从环境变量读取，绝不存进仓库
uv run uvicorn --factory governed_bi.api:create_app
```

这会在 http://localhost:8000 上提供受治理的 API（交互式文档在
http://localhost:8000/docs）。向 `/chat` 发起 POST 来提出你的第一个问题：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

你会得到一个受治理答案（智能体核心会调用模型来生成 SQL，所以每次运行的具体措辞
可能不同，下面是一个具有代表性的示例），其 JSON 里带有：

- **tier: governed**
- **safety_clearance: true** · **semantic_assurance: grounded**
- 答案，例如：`total_revenue = 18496.0`
- 它运行的 SQL，例如：`SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"`
- 一个 **provenance** 轨迹（路由、指标、涉及的表、连接置信度）

现在问一个语义层**并不**覆盖的问题：

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"How many employees work at the factory?"}'
```

系统不会去猜，而是**拒答**：

- **tier: refused**
- 一条升级提示：_"This question is outside the governed semantic layer.
  Contact the data owner to add coverage."_
- 没有 SQL，没有数字

这次拒答正是重点：范围内没有员工/薪酬数据，因此一个受治理的系统会如实说明，
而不是编造一个看似合理却错误的数字。

这个 API 是无状态的——要延续一段对话，在下一次 `/chat` 请求里把先前的轮次作为
`history` 回传（并使用稳定的 `session_id`）。

### 4b. 从 Python

同一条流程，作为一个可以嵌进你自己应用的小型 API。它需要实时模型，原生的
LangChain 模型通过 `model=` 传入：

```python
from governed_bi.config import Settings, Environment
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.llm import LangChainChatClient
from governed_bi.analyst.agent import answer_question_agent

settings = Settings.for_env(Environment.dev)
corpus = load_corpus("corpus", schema="beer_factory").for_analyst()
conn = SqliteConnector("data/bird/beer_factory.sqlite")
chat = LangChainChatClient.from_config(settings.models)  # 需要 OPENAI_API_KEY

ans = answer_question_agent(
    "What is the total revenue?",
    Identity(user="demo", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=settings,
    session_id="demo",
    model=chat.model,  # 智能体核心实际驱动的、原生的 LangChain 模型
)
print(ans.tier.value)            # governed（通常如此，实时模型的输出会有波动）
print(ans.safety_clearance)      # True
print(ans.semantic_assurance.value)  # grounded / heuristic
print(ans.sql)                   # 例如：SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"
print(ans.text)                  # 例如：total_revenue = 18496.0
conn.close()
```

## 5. 你正在看的是什么

- **双轴标记是最诚实的部分。** `safety_clearance` 是一道闸门——这条 SQL 是否通过了
  全部五层护栏、并以请求者身份执行？`semantic_assurance`（`grounded` /
  `heuristic` / `unverified`）则是答案的*接地程度*。二者刻意分开：一条查询可以
  完全安全，却仍是错误的计算，所以"安全"绝不能被读成"正确"。（见 [Analyst](analyst.zh.md)。）
- **你可以审计这条 SQL。** 模型的输出被当作不可信；实际运行的 SQL 会被展示，而且它
  只会触及 corpus 授权的列/表。
- **拒答是一项特性。** 覆盖缺失、触发护栏、或命中一条经过整理的越界模式，都会失败
  即拒。它的制衡面——不去拒答那些本可回答的问题——由评测里的误拒率来度量。

## 6. 模型配置与真实检查脚本

不想 export key？把 `.env.example` 复制成仓库根目录下的 `.env`，把 key 写在
那里，它会在导入时被加载，且绝不会覆盖你 shell 里已经设置的同名变量。`.env`
里**只放密钥**；策略（模型、数据源、corpus 路径）都放在
[`governed_bi.toml`](../governed_bi.toml) / `governed_bi.local.toml` 里。

模型是 `gpt-5.6-sol`、低推理强度（在 [`governed_bi.toml`](../governed_bi.toml)
里配置；如果你的账号只有 GA 权限，就回退到 `gpt-5.5`），通过 LangChain 的
`ChatOpenAI` 调用，它会把推理模型路由到 OpenAI 的 **Responses API**。通过
`/chat`，追问会针对对话进行消解（先前的轮次通过引擎的工作记忆回灌），答案会以
**自然语言**表述，而实际执行的行会出现在响应的 **result** 字段里；执行过的行
始终会附在答案上。

想要一次脚本化的真实检查（在 `beer_factory` 上打印执行准确率、拒答与诱饵触碰），
运行：

```bash
uv run python scripts/live_smoke.py
```

## 下一步

- [用法](usage.zh.md)——更完整的快速上手（校验 CLI、corpus API、gateway）。
- [Corpus 撰写](corpus-authoring.zh.md)——逐步撰写并校验你自己的资产。
- [系统总览](system-overview.zh.md) → [架构](architecture.zh.md)——这一切背后的设计。
- [Analyst](analyst.zh.md)——深入服务流程、护栏与可靠性标记。
