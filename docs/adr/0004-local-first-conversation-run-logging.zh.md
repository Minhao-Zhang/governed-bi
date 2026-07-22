# 0004: 本地优先的对话与运行日志

_[English](0004-local-first-conversation-run-logging.md) · [简体中文](0004-local-first-conversation-run-logging.zh.md)_

- **状态：** Proposed（提议中） (2026-07-21)。设计已与项目负责人达成一致；尚无
  代码。
- **决策者：** 项目负责人 + 设计会议
- **相关文档：** [0001](0001-langgraph-server-chat-runtime.zh.md)（LangGraph
  Server 线程 = 持久化）；[0002](0002-governed-agentic-serve-runtime.md)（治理
  ledger、Inv #10；本 ADR 构建了它被推迟的持久审计存储）；
  [0003](0003-governed-notes-tri-modal-retrieval.zh.md)；
  [design-decisions.zh.md](../design-decisions.zh.md)（D8 服务期内存；审计处置
  意见 R3 + R5）
- **细化：** **D8**（当下的工作记忆是短暂的，
  [design-decisions.md:137-147](../design-decisions.md)），并且是审计发现
  **R3**（厂商无关的交互日志，
  [design-decisions.md:428-455](../design-decisions.md)）与 **R5**（持久化
  ledger，加上 token/成本/耗时/时间戳，
  [design-decisions.md:488-517](../design-decisions.md)）的具体落地。

## 背景

- **需求（来自项目负责人）。** 保留可持久化的**对话历史以供日后查阅**，并
  **附带元数据**。存储后端不做强制规定（"我不关心它怎么存储，只要存下来就
  行"）；它必须落在**DeepAgents/LangGraph 后端**里，因此是**前端无关**的：
  Next.js UI、CLI 与 eval 都继承这一能力，而不必各自另起一套。
- **这恰好对应两项已经被推迟的审计发现。** R3（`design-decisions.md:428-455`）
  呼吁建立"一份专门的、可查询、厂商无关的交互日志"，以 turn + `corpus_release_hash`
  为键，"先捕获、后解读"，且"反馈是一个待验证的假设，绝不是一次直接编辑"。
  R5（`design-decisions.md:488-517`）发现 ledger "为每个接触数据的工具记录一个
  `verdict`……但不含耗时、不含 token/成本、也不含时间戳，且未做持久化"，而在
  追踪关闭的情况下，延迟或成本"没有厂商无关的记录"。
- **当前的缺口，附引用：**
  - **Token 在任何地方都未被捕获。** `eval/run_experiment.py:213` 在每一行
    eval 结果里硬编码 `"usage": None`；仓库里没有任何地方读取模型响应上的
    `usage_metadata`（对该字符串的全仓库搜索返回零命中）。
  - **可观测性只存在于云端，且在没有密钥时会静默变成空操作。** `obs.py:1-4`
    写明"两个追踪器，都靠环境变量选择性开启，未设置时都是空操作
    （no-op）"；LangSmith 靠环境变量把关（`obs.py:45-52`、
    `langsmith_enabled`），未设置 Langfuse 密钥时 `tracing_callbacks()` 返回
    `[]`（`obs.py:125-133`）。没有本地、厂商无关的后备方案。
  - **对话历史是短暂的，或者活在一个没人挂载的 checkpointer 里。**
    `InMemoryWorkingMemory`（`memory/store.py:36-70`，D8）明确写着"按设计是
    短暂的（重启即丢失）"。另一方面，`build_chat_graph`
    （`api/graph_app.py:99-107`）"默认在**没有** checkpointer 的情况下编译：
    在 LangGraph Server 上，运行时会注入持久化"（`graph_app.py:103-104`），
    而实际的 `compile(...)` 调用只在调用者传入 checkpointer 时才会挂上
    （`graph_app.py:181`）。走纯 REST 的 `/chat` 路径从不传入。serve 里唯一
    实例化过的 checkpointer 是 `stack.py` 里那个按进程存在的
    `InMemorySaver`，它的存在只是为了**内层** agent 的 `ask_user` HITL
    中断/恢复，不是为了对话的持久性（`api/stack.py:53-54` 的字段与注释、
    `stack.py:172-178` 的构造、`stack.py:222` 接入 `ServeStack` 的接线）。
  - **治理 ledger 只存在于 state 里，不是持久的。** `ledger:
    Annotated[list, operator.add]`（`analyst/middleware.py:47`，挂在
    `GovState` 上）为这一轮里每一次受治理的工具调用累积一条记录，但它只活在
    agent state 里；ADR 0002 的 Inv #10 明确把持久存储留作了"以后再补的接口"
    （`docs/adr/0002-governed-agentic-serve-runtime.md`，Inv #10 / Q3）。

## 决策

让**LangGraph 原生的持久化机制充当存储**，在 ADR 0002 已经拥有的拦截点上捕获
元数据，再加上**一条单薄、解耦的可移植追加记录**，用于长期留存与 eval 复用。

### 1. 用持久化 checkpointer 作为对话存储

把短暂的现状（`build_chat_graph` 上完全没有 checkpointer，`graph_app.py:181`；
以及一个只服务于内层 agent HITL 的内存 saver，`stack.py:172-178`）换成一个
持久化的 checkpointer：dev 用 `SqliteSaver`（一个本地文件），prod 用
`PostgresSaver`。这**不是**一次单纯的配置切换。`SqliteSaver` / `PostgresSaver`
各自打包在独立的 `langgraph-checkpoint-sqlite` / `langgraph-checkpoint-postgres`
包里，两者目前都不是现有依赖（`pyproject.toml` 里只列出了基础的 `langgraph`
包），而且 `Settings` 或 `DataSourceConfig` 上今天也没有 checkpointer 路径或
DSN 配置字段（`config.py`）。Phase 1 必须把加依赖、加 checkpointer/DSN 配置
字段列为明确的工作项。`memory/store.py:3-4` 为 durable memory 写下的
dev→prod 模式（"Dev backing = in-memory / SQLite / files；prod = Postgres +
pgvector（一次配置切换）"）描述的是那些 durable memory 存储尚未实现的设想，
不是这个代码库里已经接好的先例。等依赖加上之后，在图独立编译的地方接上一个
持久化 saver；在 LangGraph Server 上运行时，由运行时注入持久化后端，所以
`build_chat_graph` 在 server 入口保持不带 checkpointer（`graph_app.py:103-104`）。
这样一来，对话历史就是 `ChatState` 上被持久化的
`messages`（`graph_app.py:38-46`），可以通过流式 / `useStream` 路径上每个客户端
共用的标准 LangGraph thread API（`get_state` / `get_state_history` / list
threads）在之后被引用。这就是 ADR 0001 的 thread 模型，只是变成了持久且前端无关
的（仅限该路径）。**它只覆盖 LangGraph-Server / `useStream` 路径，不包括纯 REST
的 `/chat` 路由**：该路由直接调用 `answer_question_agent`，按设计是无状态的
（`api/app.py:414-459`，"由调用方保存 transcript"，每次请求新建一个
`InMemoryWorkingMemory`）。让 REST `/chat` 变持久是一个独立的迁移步骤：要么让它
走上带 checkpointer 的图，要么在 `answer_question_agent` 内部加持久化。

**给各自的角色命名。** checkpointer 是线程恢复 / 用户体验存储：它不是缓存，不是
审计记录，也不对跨 LangGraph 版本升级的留存做任何保证。一旦 Decision §3 里的
写一致性契约与完整的终态覆盖都成立，那条可移植追加记录才是权威的历史 / 审计
记录，也就是"以后可供查阅的历史"这句话真正指向的产物。

### 2. 在已有的接口点上捕获元数据，随每一轮一起持久化

- **Token。** 从模型响应上读取 `usage_metadata`（输入/输出/总 token 数；
  Anthropic 与 OpenAI 都通过 LangChain 原生填充这个字段），并在共享的
  finalize-and-log helper（见下，Decision §3）里汇总。捕获接口点是
  `GovernanceMiddleware.wrap_model_call`（`middleware.py:159`，目前已经存在，
  用于强制顺序调用工具），它能拿到模型响应。要在响应到达
  `_coerce_single_tool_call`（`middleware.py:175`，重建逻辑在
  `middleware.py:189-216`）之前，也就是从**未经改写（PRE-coercion）**的响应上
  捕获 `usage_metadata`：那个 helper 只用 `content`、`tool_calls[:1]`、`id` 和
  `additional_kwargs` 重建 `AIMessage`，所以只要模型在这一轮发出了并行工具
  调用，`usage_metadata` 就会被丢弃。至于具体的 state 写入路径（一个
  `after_model` 钩子，或直接从返回的 AIMessage 上读取 usage），应对照已安装的
  中间件 API 确认，而不要想当然地认为它能照搬 `ledger` 那条 `wrap_tool_call`
  的 `Command(update=...)` 写法（`middleware.py:43-47`）：`wrap_model_call` 返回
  的是 `ModelResponse`，channel 写入机制不同。这是唯一真正新增的捕获动作；今天
  token 是被丢弃的（`run_experiment.py:213`）。`wrap_model_call` 只包裹了内层
  serve agent，看不到系统里每一次模型调用：schema 路由的 `select_schema` /
  `router_chat`（`agent.py:394`）、narrator（`narrate_node`，`agent.py:855`），
  以及 curator/SME 两个图（`curator/deep_agent.py:285`、`curator/sme.py:164`）
  各自在这个接口点之外发起模型调用，需要各自的捕获点。再加一个后备逻辑：当
  模型调用在返回响应之前就抛出异常时，记录一条失败调用的结果，避免调用中途
  的错误从 token/成本记录里静默消失。
- **Ledger + 耗时 + 时间戳。** `wrap_tool_call`（`middleware.py:219`）已经会
  为每一次受治理的操作写入一条 ledger 记录（`middleware.py:234-361`，例如
  `middleware.py:347-354` 处的 `pass` 记录）；给每条记录加上 `duration_ms`
  和一个时间戳（R5 第 1 项，`design-decisions.md:509-510`）。
- **汇总。** `_finalize_success`（`analyst/governance.py:561`，由
  `analyst/agent.py:837` 里的 `agent_core_node` 调用）是 SUCCESS 路径上把
  `base_provenance` 与 `governance_ledger` 以及这一轮的事实合并进
  `Answer.provenance`（`governance.py:587-599`）的那一步；把这次合并扩展为
  同时写入模型 + tier、token 总量加上按次调用的明细、一个估算成本（来自一张
  价目表）、延迟、结果（outcome）、两轴印章（`safety_clearance` /
  `semantic_assurance`）、`tables_used`、被路由到的 schema、ledger、
  `corpus_release_hash` / `corpus_pin`、`serve_config_hash`（对治理/路由配置
  做的哈希：阈值、`top_k`、RRF 权重、各种 flag，好让相同语料但不同配置的两次
  运行能被区分开）、`producer` / `data_split` / `export_allow`、稳定不变的
  `turn` / `run` / `thread` id、session/身份，以及 `serve_path`。推荐的后续
  工作，现在不做：note 生命周期事件、内容与上下文摘要（digest）、curator/SME
  到 note 的溯源；真正的 corpus-release 身份仍然推迟到 D11。`base_provenance`
  是从 `ServeRailsState`（`agent.py:141`；在 `agent.py:445` 处填充，在
  `agent.py:746` 处被消费）一路传下来的，所以这是在一个既有接口点上做加法，
  不是新开一个。`_finalize_success` 是**唯一**的 success 终结函数，只在这一处
  被调用；其他每一种终态都会走一个不同的函数返回：cache hit 走
  `_try_cache_hit` 的 `assemble(...)`（`governance.py:401`、
  `governance.py:457`）；拒答、安全拦截，或者 graded/unverified 交付走
  `_finish_unsuccessful` 的 `refusal(...)` / `graded_delivery(...)`
  （`governance.py:460`、`governance.py:497,518,542,550`）；
  `GovernanceHardStop` 直接在 `agent.py` 里被捕获，例如 `agent.py:691`；以及
  `ask_user` 的 clarify / declined 路径（`agent.py:671,675`）。上面这套汇总
  不能只放在 `_finalize_success` 里：它必须挪进一个共享的 finalize-and-log
  helper，让上面每一个终态函数都调用它，这样拒答或安全拦截才能带上和 success
  一样完整的元数据（见 Decision §3）。

### 3. 一条单薄、解耦的可移植追加记录（唯一超出"纯原生"范围的新增项）

§2 里的汇总与这条可移植追加记录必须都从同一个共享的 finalize-and-log helper
运行，由**每一个**终态函数调用，而不是只靠 `_finalize_success`：success
（`_finalize_success`，`governance.py:561`）、cache hit（`_try_cache_hit`，
`governance.py:401`，经由 `assemble(...)` 在 `governance.py:457` 返回）、
拒答、安全拦截，或 graded/unverified 交付（`_finish_unsuccessful`，
`governance.py:460`，经由 `refusal(...)` / `graded_delivery(...)` 在
`governance.py:497,518,542,550`）、`GovernanceHardStop`（直接在 `agent.py`
里被捕获，例如 `agent.py:691`），以及 `ask_user` 的 clarify / declined 路径
（`agent.py:671,675`）。如果汇总与追加记录只经过 `_finalize_success`，就会
悄悄漏掉拒答与拦截，而这恰恰是审计者最想看的那些轮次；共享 helper 就是用来
补上这个缺口的。

这个共享 helper 会在 LangGraph 内部的 checkpoint 结构之外，为上面**每一种**
终态各追加**一条可移植的记录**（一行 SQLite 记录或一行 JSONL），不只是
success 才有。理由：checkpoint 表与 LangGraph 的版本紧密耦合，形状是为恢复
（resume）而生的，不是为一年后回读或 eval 复用而生的。这条解耦的记录才是那份
持久、可移植、人可读的"以后可以引用"日志，以 turn + `corpus_release_hash`
为键，正是 R3 要求的键（`design-decisions.md:450-451`，"一份专门的、可查询、
厂商无关的交互日志……以 turn + `corpus_release_hash` 为键"）。
`corpus_release_hash` 本身今天还没有实现（在 `src/` 里搜索这个词零命中），
依赖尚未定案的 `CorpusRelease` 决策（D11，`design-decisions.md:453`）；在
D11 落地之前，按 R3 自己给出的提示，用一个 git-SHA-per-checkpoint 的临时值
作为过渡键。它同时也补上了 `run_experiment.py:213` 里 `"usage": None` 的那个
缺口，因为 eval 会从这同一条追加记录里读取 token/成本，而不再硬编码 `None`。

### 4. 覆盖范围：serve 对话与 DeepAgents 运行

serve agent（`create_agent` + `GovernanceMiddleware`，在 `build_agent_core`
里组装，`agent.py:163-211`）与 curator/SME 这两个 deep agent
（`create_deep_agent`：`curator/deep_agent.py:285`、`curator/sme.py:164`）
都是 LangGraph 图。给它们的 invoke 配置都接上同一个持久化 checkpointer，
再加一个 thread/run id（今天 `pipeline.py:263-268` 与 `sme.py:219-221` 各自
只传了 `recursion_limit` 和 `callbacks`），并发出同一种可移植的按次运行
记录。一套机制，三个生产者（serve、curator、SME）。

### 负责人不变式 + 本地优先姿态

- **元数据日志在运行期间只写不读：是历史存储，永远不是活路径的数据来源。**
  没有任何地方会回读*token/成本/ledger 元数据或那条可移植追加记录*来影响当前
  这一轮。（checkpointer 里的对话 `messages` **确实**每一轮都会被读取以构建
  后续追问的上下文，`graph_app.py:119-120`；那正是我们要的"可供引用的历史"，
  是一次合理的活路径读取，所以这条不变式约束的是元数据与可移植记录，不是对话
  存储本身。）这保住了 R3 的"先捕获"立场，也就是"反馈是一个待验证的假设，绝不
  是一次直接编辑"（`design-decisions.md:437-446`），并避开了 R2/R3 所警告的那种
  退化反馈环。对比一下：`SqlCache`（`analyst/cache.py:56-89`）按设计**就是**一个
  活路径输入：`_try_cache_hit`（`governance.py:401,417`）由 `cache_lookup` 节点
  （`agent.py:451-454`）调用，命中时可以让当前这一轮短路返回。元数据日志刻意
  没有对应的读取路径。
- **先只存元数据；存全部内容要等一个隐私决定。** 一条只含元数据的追加记录
  （turn id、token、成本、耗时、outcome，不含原样内容）不带来原样日志那种
  暴露风险，所以先上线。原样存下问题、SQL 与行预览这份全部内容，虽然是一份
  历史记录、不会在运行过程中被消费，但在任何共享或已部署的环境里，都不能
  默认开启，除非留存默认值、访问控制姿态与本地文件权限至少都已被明确决定。
  `obs.py` 的 `GOVERNED_BI_TRACE_MAX_CHARS` 打码机制（`obs.py:61-91`，通过
  `_langfuse_handler` 里的 `_trace_mask` 应用，`obs.py:115`）只作用于云端
  追踪器这条路径。打码、留存，以及"全部内容默认开启"这个决定都被推迟，
  明确不在现在构建。
- **本地优先，默认开启。** 与"未设置密钥时都是空操作（no-op）"的云端追踪器
  （`obs.py:1-4`）不同，本地日志默认开启，不需要任何密钥。

## 影响

**正面**
- LangGraph-Server / `useStream` 路径拥有持久、前端无关的对话历史加元数据
  （REST `/chat` 的持久化是一个独立的迁移步骤，因为它今天按设计是无状态的）。
- R3 / R5 以及 ADR 0002 Inv #10 那个持久审计存储，终于有了具体的落地，而
  不是又一个被推迟的接口。
- 在该路径上修复了 D8 短暂性（ephemerality）对对话历史与治理 ledger 的影响。
  （HITL 恢复用的是另一个内层 `clarify_checkpointer`，`stack.py:172-178`，需要
  它自己的持久化步骤，不在 §1 覆盖范围内。）
- 补上了 eval 里 `usage: None` 的缺口（`run_experiment.py:213`）；
  token/成本/延迟终于可以在本地测量，不需要厂商仪表盘。
- deep agent（curator/SME）的运行拿到和 serve 轮次一样的持久记录：一套
  机制，不是三套各自为战的方案。

**负面 / 成本**
- 持久化 checkpointer 在 prod 里需要一个真正的数据库（Postgres），这与
  ADR 0001 已经写明的部署提示是同一条。
- 一份存全部内容的本地日志是一个敏感产物：原样保留的问题、SQL 与行预览。
  它会被门控上线：除了单人操作的本地开发环境之外默认关闭，直到留存默认值、
  访问控制姿态与本地文件权限都被决定为止。只含元数据的追加记录先上线，
  不带那种风险。打码，以及"全部内容默认开启"这个决定都被推迟。
- 这条可移植追加记录是在 checkpointer 写入之上，每一轮多一次写入。代价
  不高，但也不是零成本，而且如果两次写入没有严格同步，它就是一个可能与
  checkpoint 状态出现偏差的第二存储点。这两次写入需要一份具体的写一致性
  契约：要么是至少一次（at-least-once）交付，配合一个以稳定的 turn/run id
  为键的幂等 upsert；要么是一个带对账的单写者 outbox；而且这条追加记录在
  LangGraph resume 时必须是可重放幂等的。让两次写入都出自同一个共享 helper
  只是一个起点，本身并不构成持久性保证。

## 考虑过的替代方案

- **只用云端追踪器（Langfuse/LangSmith）。** 已否决：厂商锁定，没有密钥时
  会静默变成空操作（`obs.py:1-4,125-133`），不是后端自持有的前端无关记录，
  也没有本地的事实来源，恰好就是 R5 指出的那个缺口（"追踪关闭时……没有
  厂商无关的记录"，`design-decisions.md:501-503`）。
- **一个专门的、规范化的分析型 SQLite（早先的两存储方案）。** 已推迟：对
  "留存历史以供查阅"这个需求来说是过度设计。这条可移植追加记录覆盖了同样
  的需求，并且以后可以升级成关系型表，而不需要触碰捕获接口
  （`wrap_model_call` / `wrap_tool_call` / `_finalize_success`）。
- **把 checkpointer 超载来做分析用途。** 已否决：checkpoint 结构与版本
  紧密耦合，形状是为恢复而生的，不是为一年后的临时读取或 eval 复用而生
  的，这正是这条解耦的可移植追加记录存在的原因。
- **把日志变成一个活路径输入（回读过去的轮次来引导当前运行）。** 被项目
  负责人否决：日志只写不读；活路径复用是 `SqlCache`（`analyst/cache.py`）
  的职责，从日志里自动学习正是 R3 所警惕的那种退化循环
  （`design-decisions.md:437-446`）。

## 迁移（分阶段；每个阶段都可独立发布）

1. 加上 `langgraph-checkpoint-sqlite` / `langgraph-checkpoint-postgres` 依赖
   （两者今天都不随 `pyproject.toml` 里的基础 `langgraph` 依赖一起打包），
   再给 `Settings` / `DataSourceConfig` 加一个 checkpointer/DSN 配置字段
   （`config.py` 今天没有这个字段）。然后在图独立/本地编译时接上一个持久化
   saver，让 LangGraph-Server / `useStream` 路径持久化对话历史（原生做法，
   不新增 schema；`build_chat_graph` 在 server 入口保持不带 checkpointer，
   `graph_app.py:103-104`，以免与平台注入的持久化相撞）。让 REST `/chat`
   路由变持久（让它走上带 checkpointer 的图，或在 `answer_question_agent`
   内部加持久化，`api/app.py:414-459`）是一个独立的后续步骤。
2. 在 `wrap_model_call`（`middleware.py:159`）里把 token 捕获进一个新的
   `token_usage` channel，要从**未经改写（PRE-coercion）**的响应上读取
   `usage_metadata`，赶在 `_coerce_single_tool_call`（`middleware.py:175`，
   重建逻辑在 `middleware.py:189-216`）把它丢掉之前；给 schema 路由
   （`select_schema` / `router_chat`，`agent.py:394`）、narrator
   （`narrate_node`，`agent.py:855`），以及 curator/SME 两个图
   （`curator/deep_agent.py:285`、`curator/sme.py:164`）各加一个独立的
   捕获点，因为它们都在 `wrap_model_call` 接口点之外调用模型；给每条
   ledger 记录打上 `duration_ms` + 时间戳（`middleware.py:219`）；再加一个
   后备逻辑，当模型调用在返回响应之前就抛出异常时记录一条失败调用的结果。
3. 列出每一个终态函数（success、cache hit、拒答、安全拦截、graded/unverified
   交付、hard stop，以及 clarify/declined；见 Decision §3），让每一个都走
   同一个共享的 finalize-and-log helper，这样汇总进 `Answer.provenance` 的
   那部分与下面的可移植追加记录才能覆盖每一种结果，而不只是 success。
4. 把那条单薄的、按轮次的可移植追加记录先做成**只含元数据**的版本（turn id、
   token、成本、耗时、outcome；不含原样内容），由共享 helper 写入，以
   turn + `corpus_release_hash` 为键（过渡期：在 `CorpusRelease` 决策
   D11 落地之前，用一个 git-SHA-per-checkpoint 的临时值）；让
   `run_experiment.py` 从这里读取 token/成本，而不再硬编码
   `"usage": None`（`run_experiment.py:213`）。
5. 作为后续步骤，给追加记录加上**全部内容**日志（原样保留的问题、SQL、行
   预览），门控在留存默认值、访问控制姿态与本地文件权限都被决定之后；在此
   之前，在任何共享或已部署的环境里都不默认开启。
6. 扩展到 DeepAgents：给 curator（`pipeline.py:263-268`）与 SME
   （`sme.py:219-221`）的 invoke 也接上 checkpointer + run id + 可移植
   记录。
7. （推迟）打码开关 + 留存/轮转；把可移植存储可选地升级为关系型表，以支撑
   仪表盘/指标，对应 R5 第 4-5 项（`design-decisions.md:513-514`：
   OpenTelemetry/Prometheus 接口、失败即报警的追踪）。

## 待定问题

- 可移植记录的格式：SQLite 一行记录（可查询，依然能轻松导出，推荐）还是
  JSONL（追加/grep 都极其简单）。
- 价目表放在哪里，以及什么时候计算成本（config 里，还是在
  `_finalize_success` 处）。
- prod 的 checkpointer：复用 serving 用的 Postgres，还是用一个独立的日志
  数据库。
- 内层 HITL 用的 `clarify_checkpointer`（`stack.py:172-178`，今天是一个按
  进程存在、只服务于 `ask_user` 中断/恢复的 `InMemorySaver`）的持久性：是
  现在就给它一个自己的持久化后端，与 §1 里的对话 checkpointer 并列，还是
  接受现在这个 resume 窗口期的风险，把它记作以后的工作。
