# 0015：回流路径 —— 读者反馈进入语料

- **状态：** 已接受，部分建成（2026-08-23）。**第 0-6 步在 `design/return-path` 上**：`feedback/` 包
  （存储、封闭词汇表、生命周期表、聚类器）、从 `tools/import_eval_failures.py` 到
  `tools/check_ratchet.py` 的六个 CLI 工具、五条新的全树一致性规则以及把它们既有发现钉住的 ratchet、
  `corpus_release` 可比性 knob、藏在 `GOVERNED_BI_FEEDBACK_ADMIN` 之后的 steward 四个动词，以及
  `/review` 界面。`ServeState.raised`、`serve/raised.py`、`api/raised_write.py` 和
  `api/clarification_routes.py` 已**删除**（`4a0d11a`），所以状态带 47 个通道而不是 48 个。
  **设计了、没有建，且在下文每一处出现的地方都点明是设计：** 智能体分诊流水线（Reproducer、
  Diagnoser、Author、Curator，以及 `triage/` 包）、验证层 T4 与 T5、读者侧的上报 UI、`/reports`，
  以及 re-ask 动作。建设过程中的五项实测改变了本文记录的决定；下面的回顾逐条点名。
- **English:** [0015: The return path](0015-the-return-path.md).
- **决策者：** 项目所有者 + 设计会议（2026-08-23）—— 五份独立提案（接入、流水线、验证、工作流，
  以及一份带实测的原型），随后三轮对抗式批判。
- **配套工作参考：** [回流路径](../return-path.zh.md) —— 构建顺序、数据形状、路由表和测试名。
  本页是决策与推理；那一页是工程师照着实现的东西。
- **阅读须知。** Context 一节里有四条数字是为这次决策专门测出来的，别处任何文档都没有。它们标注为
  **实测**，并给出产生它的命令。

> **建设改变了本记录里的什么。** 建设第 0-6 步时的**五项**实测改变了本页的决定。证据在
> `docs/open-work.md` §3.10a-3.10c，此处刻意不重复 —— 本文件是决定记录，那一份是工作清单。
>
> | 实测 | 它改变了哪个决定 |
> |---|---|
> | `raised` 通道里**一行都没有**，三种独立检查都是如此 | 迁移不需要抽干（drain）工具，所以删除通道的动作*提前*了 —— 在评审界面按它的契约写出来之前 —— 兼容性联合（union）直接取消 |
> | 删掉一个通道，代价在**契约**上，不在代码上 | 决定 2 的成本估计；`docs/return-path.md` §1 里「这是一次所有者已被删除的改名，不是一次带来大量改动的改名」说错了一半，现在已经写明 |
> | 对已存在的资产，`corpus/store.py::write` 会写出**第二个同 id 的文件** | bundle 必须是 `git apply` 的 diff、绝不是目录复制（决定 4）；`corpus/patch.py` 之所以存在；以及一致性规则 V23 尽管当前发现数为零仍然上线 |
> | `corpus/snapshot.py` 的 `rmtree` **删掉了一个装着无关文件的临时目录** | 先修了守卫；然后验证阶梯（ladder）被建成在**内存里**施加编辑，因此它根本不调用 `snapshot` |
> | 投诉的聚类**很弱** —— 在真实的 73 条上，最大簇 3、49% 落在簇里 | **开放问题 7 得到了否定的回答。** 批处理论证撑不住，所以评审界面是一个列表加可选分组，而不是以簇为先的界面 |
>
> 建设中收窄了两个决定，两份文档都已写明：**T2 不需要数据库**（corpus 自己声明了 join，所以解析器离线
> 且免费），以及**V18 砍掉**（没有活体样本，没有校准过的误报率 —— 五条新规则，不是六条）。上报 UI 和
> `/reports` 没有建、本轮也不打算建：这里所有角色都由同一个人担任，所以输入是 eval artifact，而不是
> 某个人点按钮。

---

## Context

### 1. 引擎能被告知自己错了，而这个「告知」无处可去

这是做出决定时那棵树的样子；`4a0d11a` 删掉了本节点名的每一个模块。当时有两个界面。
`POST /turns/{turn_id}/raised`（`api/clarification_routes.py:66`）让读者对一个已结束的 turn 提交一条
笔记 —— `kind ∈ {from_refusal, wrong_answer}`，一段上限 `RAISED_NOTE_MAX_CHARS = 4000` 的自由文本
`note` —— 并把一行追加到 checkpoint 化的累积通道 `ServeState.raised` 上。
`GET /clarifications/pending` 把这些 open 行与活跃的 `ask_user` interrupt 并集，按最早优先展示。

**当时没有任何东西关闭一个 open 行，也没有任何东西据此行动。** `serve/raised.py::raised_row` 写下
`open: True`，注释是「until a later closer exists」；那个后来的关闭者不存在。UI 告诉读者
"Filed. It is on the pending list."——这句话是真的，而且这就是当时全部发生的事。
`ui/components/clarifications/pending-queue.tsx` 在自己的 docstring 里把这个缺口说清楚了：

> the owner's decision routes an operator's answer into the semantic layer instead, and that path
> waits on a provenance gate the engine does not have yet.
>
> （所有者的决定是把操作员的回答改道送进语义层，而那条路径还等着引擎尚不具备的一道 provenance 闸门。）

这份 ADR 就是那道闸门。

### 2. 两类人群，他们知道的东西不同

分析师懂业务。他们知道「活跃客户」不包括暂停中的那些，知道上月的收入数字差了十倍，知道引擎把每笔
订单算了两遍。他们不知道 `sales.orders.customer_id`；一个要求他们提供 asset id 的界面，收集到的是
「看起来很确定但指错地方」的指针。

工程师或数据管理员 (data steward) 懂 schema，能读语料，而且 —— 这是需求的前提 ——
**在语料仓库上有提交权**，所以他们的修改经由那个仓库自己的评审和 CI 落地。引擎不需要写 git。这种
不对称就是整份设计的形状：**这个环制造出可复核、有证据支撑的变更；由人来提交。**

### 3. 语料不在本仓库里，而这一点是承重的

`GOVERNED_BI_CORPUS_DIR` 指向一个兄弟 checkout。`docs/open-work.md` §3.2：语料「is in git and cannot
be regenerated from this repository. This engine loads a versioned tree; it does not write one.」
§3.10 故意让 `build_workers` knob 保持红色，理由是「the curator is not in this repository」。

一份悄悄把本仓库变成语料作者的设计，等于一次推翻三项既有决定。这一份不这么做：
**本 ADR 新增的任何路径都不写入 `GOVERNED_BI_CORPUS_DIR`。**

### 4. 为这次决策测出的四条事实

| | 发现 | 怎么测的 |
|---|---|---|
| **M1** | **`corpus/store.py::write` 无法编辑一个 asset。** 加载一个 table asset、改它的 `summary`、再调 `write`，产生了**第二个携带同一 asset id 的文件**；`store.load` 把两个都返回，**problems 为零**；随后 `retrieve` 的 `build_index` 抛出 `ValueError: duplicate index id`。而且这次写入是整文件重排：`store.py:256` 是 `yaml.safe_dump(to_mapping(asset), sort_keys=False, allow_unicode=True)`，没有 `width`，而 `parse.py::to_mapping` 省略默认值 —— 所以一次往返会丢注释、把超过 80 列的字符串全部重排、丢掉显式写出的默认值，并把键按 dataclass 字段序重排。 | 在服务语料的一份拷贝上跑原型 |
| **M2** | **一致性检查三个只抓住一个。** 在服务语料的三份新拷贝上：`TermAsset.binding.target_id` 指向不存在的 asset **抓住了**（V9，退出码 1）；`MetricAsset.expression` 引用 `base_table` 上不存在的列 **没抓住**；`TermAsset` 的 prose 里写出一个 `governance.excluded` 列 **没抓住**；两个 asset 共用一个 id **没抓住**。在 BIRD 语料上，16/16 条规则在 13,304 个 asset 上全绿，耗时 26 秒 —— 同时 **478 个 metric `expression` 里有 28 个根本不是合法 SQL**，**23 个 metric 引用的列在自己的 `base_table` 上什么都解析不到**。 | `tools/check_corpus_conformance.py --corpus-dir …`；`sqlglot` 用引擎的 `postgres` dialect |
| **M3** | **prose 注入的洞在 `body`，不在 `summary`。** 走真实的 `_visible` + `render_context`：被排除的 `ColumnAsset` 被正确丢弃，table 的 inline 列被正确裁剪，而渲染出的 block 里仍然含有另一个 asset 的 `body`，那里写着被排除的列名。`summary` **不在** block 里 —— `serve/context.py` 写着「`summary` never enters the prompt」；它进的是检索索引。所以 ADR 0003 的复盘对机制的判断是对的，对字段的判断不准，而这两个通道需要**不同的**规则：`body` 是披露面，`summary` 是检索投毒面。**两个语料里 `governance.excluded` 的 asset 数都是零**，所以这条规则从来没有过活跃对象。 | 在语料拷贝上跑离线 harness；在 `corpus/validate.py` 和一致性检查工具里 `grep` 内容扫描器，无结果 |
| **M4** | **离线阶梯是免费的，而且加一个 asset 不会重算整个语料的 embedding。** 快照 0.21 秒（0.89 MB，179 个文件）、`store.load` 0.47 秒、`build_structure` 0.05 秒、纯词法 `build_index` 0.03 秒、全树一致性 3.4 秒、暖态语义 `build_index` 0.27 秒、`govern_bench` 1.7 秒 —— 除最后那次 embed 外都不需要凭据。用一个 spy embedder 在向量缓存的拷贝上测得：暖态全量构建 **1** 次 embed 调用，**加 1 个 asset 变成 2** 次。 | 在服务语料上跑原型计时 |

M1 决定补丁怎么写。M2 决定阶梯必须补上什么。M3 决定一条内容规则该管哪个字段。M4 是阶梯能当闸门
而不只是报告的原因。

### 5. 需求假定了、而代码树不满足的那个前置条件

**实测：`../MS Fabric Facilities` —— 写下这句话时在服务的语料 —— 不是一个 git 仓库。** 它有
`.gitignore`，
没有 `.git`。`../BIRD-corpus` 是。所以「工程师提交，语料仓库的 CI 跑起来」这句话对基准语料成立，对
生产中那个不成立。

更糟的是，它记录下来的身份复现不出来。`.env:70` 写着「Verified with `corpus.store.load`: 1,432
assets, 0 problems, one namespace, content hash `2f2b296e321d89ba`」。实测是无限定条件下
`ddabcc43dc32b4a5…`、`schemas=["facilities"]` 下 `8fb6e79f4008d7de`。两个都不匹配，asset 计数匹配，
而且**没有任何历史能说清是这棵树变了、还是当初写下那条注释时就错了。**

这是本文档能给出的、关于自身存在理由的最强论据；同时它也是一个前置条件：回流路径的「落地」那一半
需要一棵受版本控制的树。在那个语料成为 git 仓库之前，这个环可以捕获、分诊、验证、交接 ——
但无法区分 `landed` 和 `superseded`。一次 `git init` 加一次首提交；这是构建顺序的第 0 步。

**第 0 步是用另一种方式结掉的（`222d1bf`）。** 与其给一棵没有历史的树 `git init`，`.env` 现在服务
`../BIRD-corpus`，它是一个 git 仓库；而因为 `.env` 被 gitignore，这次切换记在
`docs/corpus-format.md` 里。`../MS Fabric Facilities` 仍然没有版本控制，所以对它而言「落地」那一半
依然不可用 —— 区别在于引擎现在服务的那个语料里，`landed` 和 `superseded` 分得开，而这正是这个前置
条件真正要的东西。

### 6. 一个补丁不能用什么来验证

`docs/open-work.md` §3.12：本引擎在配置固定不变的情况下跑两次，**12.7%** 的结果会不一致；
`SE(net)` 未固定路由时约 1.0pp、固定后 0.83pp；一个 1,351 题的 arm 在 80% 检验力下能分辨的最小
效应约 **2.3pp**。§1.5 中最大的单个 coverage 桶是某个 schema 里的 7 道题 —— **0.52pp**，探测下限的
四分之一。跑完一整个 arm 在 `workers=10` 下约 52 分钟挂钟时间、约 74M 输入 token
（`runs/eval/driver_v4.log`）。

所以用 EX 给一个单 asset 补丁定价，得到的置信区间会包含零，而正确的写法是「我们什么也没学到」——
这正是 `eval/power.py::require_power` 存在的目的：提前拒绝这种事。而写下这句话时它没有调用者 ——
现在有了，Decision 7 说了在哪。

---

## Decision

### 1. 两层，读者永远不撰写变更

**Observation** 是读者用业务语言说出的所见，归属到恰好一个 turn。**Patch** 是工程师或 agent 撰写的
带类型的语料变更。一个 observation 对应零个或多个 patch，而**零是常见且诚实的结果** —— 一个
observation 可以被分诊为「引擎是对的」、「数仓是错的」、或者「这是引擎缺陷，不是语料缺口」，三者
都不是语料编辑。

这两层是两张表，不是一行带若干可空列。合成一张之后：`asset_id`/`field`/`was`/`becomes` 这几列在
每一个读者提交的行上都是 null；一个 observation 再也无法承载两处变更 —— 缺一个同义词**加**一处错误
的 join，是一次投诉；而一对作者/时间戳列要回答两个问题。

observation 词汇表是封闭的，标签写的是读者会读的话，不是策展人会写的话。完整表格在
[工作参考](../return-path.zh.md)里；规则是：**第一次点击一定提交出一条有效记录，细化永远不是关卡，
任何选项都不点名某张表或某个列。** `wrong_answer` 作为「有问题但我说不清是什么」这个兜底桶保留，
因为一个在投诉提交之前就强迫人做分类选择的词汇表，会把投诉丢掉。

两层都建了：`feedback/events.py` 装着两个形状和那份封闭词汇表，`feedback/store.py` 装着两张表。
进来的路有两条，而它们被使用的程度不同。`POST /turns/{turn_id}/raised` 以**挂载且启用**的状态发布 ——
它是本 ADR 里唯一一个不在管理开关之后的写动词 —— 而 `ui/lib/api-client.ts` 能调它，但没有任何组件调，
因为上报 UI 没有建。所以现存的那些行都是从 `tools/import_eval_failures.py` 进来的，而那条路由是一个
活着但没有调用方的写入口，不是一个被关掉的。

### 2. 删掉 `ServeState.raised`，observation 住进自己的存储

`runs/feedback.sqlite`，标准库 `sqlite3`，**同步**，放在新的 `feedback/` 包里。每一条 observation
都写在那里。`serve/raised.py`、`api/raised_write.py` 以及
`ThreadTurnLog.append_raised` / `raised_of` 已删除。

**分阶段这件事是定下来的，然后被一次实测取消了。** 一次性删除有一个没设计过的迁移问题：已经躺在
checkpoint 里的行变得不可达，而「没有迁移，那些行不可达」是一句得有人签字的话。所以定下来的做法是把
删除摊到不止一个 commit 上 —— 存储先落地并接下所有新写入，通道继续为已有内容工作，
`tools/drain_raised.py` 遍历 thread 把那些行抄进来，在 drain 还有活干的期间读取方并集两个来源，而当
drain 报告为零并保持时删掉这个并集。一个具名的终止条件，因为一个没有出口的兼容并集，正是两个事实
来源变成永久的方式。

然后有人去数了这个通道。**`raised` 行数为零，三种独立检查都是如此 —— checkpoint 存储、harness 存储，
以及全部 23 个平台 thread 行。** 没有东西可抽干，所以 `tools/drain_raised.py` 从未被写出来，读取并集
也从未建成；`4a0d11a` 在一个 commit 里删掉了这个通道。替那句话签字的是一条断言：
`tests/api/test_an_observation_is_filed_on_a_turn.py` 里的「暂停中的 thread」用例要求 turn log 那道接缝
不再暴露 `append_raised`，于是第二个写入方不能悄悄回来。分阶段本来要管的迁移风险，是一个没人数过的量，
而数它比为它做计划更便宜。删除**真正**花掉的是线上契约 —— `docs/openapi.json` 钉住了带七个必填字段的
`RaisedRowResponse`，spec 测试对那个操作有四条断言 —— 恰好与本 ADR 预计代价落在哪里相反。

明确**不采纳**的是某位批判者主张的更省事的方案：把通道当作不可变的接入回执，把处置结果放进一个
只追加的 JSONL，读取时按 last-write-wins 折叠。它在第一周确实工作量更小，而且这些工作会被扔掉 ——
那个折叠之所以存在，只因为底层存储装不下一个可变的行，而这正是要解决的问题本身。它还让操作员队列
继续停在下面那个 40 次往返的 thread 遍历上。

通道在四点上不合格，而第一点是决定性的：**一个累积通道装不下一个会变的行。** `operator.add` 意味着
一次关闭就是第二行，而每一个读取方都得折叠。然后：它不可查询 —— `threads.search` 的 `values` 过滤是
JSONB containment，内存 runtime 对一个 list 值的键把它实现成对整个 list 的相等比较，所以操作员队列
是对每个 thread 的一次全量无过滤扫描（`api/thread_turns.py::_pending_async` 用四段话说了这件事）。
它不可清扫 —— `Threads.sweep_ttl` 是 `return (0, 0)`，所以 `langgraph.json` 里那个 90 天 TTL 是死的，
而这一行会被重新序列化进该 thread 之后的每一个 checkpoint。而写入路径是约 250 行绕事件循环的代码
（`run_coroutine_threadsafe`、一个 `_in_flight` runtime 探测、一个 `InFlightUnknown` 的 fail-closed
降级），它们存在的唯一目的就是安全地写 graph state。

**这重新打开了 ADR 0014 点名拒绝过的一个替代方案** —— 「a hand-rolled SQLite table… Rejected by the
owner: the point is a LangGraph-native primitive, for maintainability.」那次拒绝针对的是**turn 记录**，
而且对那件事它是对的：`ACCUMULATING` 本来就存在，所以一个跑在持久 checkpointer 上的累积通道精确地
满足了需求。而这次的需求没有原生原语可用：需要的是**一个可变的行，跨 thread 按 checkpoint 未建索引的
字段查询**，而 0014 自己也因为 `BaseStore.search()` 没有 sort 参数而拒绝把 LangGraph Store 用作审计
索引。一个 turn 只发生一次；一个 observation 会被改四次。

同步而不是 `aiosqlite`，这是故意的：`serve/checkpointer.py` 记录的每一个事件循环绑定陷阱，都源于存储
与 graph 共用一个循环。这一个由同步 FastAPI handler 写和读，从不碰那个循环。

放弃了什么，说清楚：`runs/` 下第三个 SQLite 文件，以及一份由本仓库自己拥有和迁移的 schema。保住了
什么：`sqlite3 runs/feedback.sqlite "select …"` —— 也就是 0014 列为「失去了的东西」的那份可 grep 性。

有一个后果是好事，而且它上线了。`api/raised_write.py` 曾拒绝在暂停的 thread 上提交，因为
`as_node="raise_note"` 会消耗掉活跃的 `ask_user` interrupt。现在不再有任何东西写 graph state，就没有
interrupt 可被消耗，于是 **turn 正处于暂停中的那位读者 —— 最可能想投诉的那一位 —— 可以提交了。**
那个 409 没有了，而原先以它为主题的那条测试，现在是断言它不存在的那条。

### 3. 一个状态只有在存在具名行动者推动它时才存储

其余全部在读取时派生。这是解开生命周期的那条规则，而且它是靠**搭状态机**发现的，不是靠争论：一份
一次性原型有七个转移写不出来，除非临时编一个答案，而七个里有四个是同一个错误 —— 存了一个没人推动的
状态。

**存储的**（由 steward 推动）：`open → triaged → {declined, duplicate, addressed}`。
`decline_reason` 与状态一并存储，因为**理由就是通知** —— 不存在一个没有句子的「已驳回」徽章。

**叫 `addressed` 而不是 `resolved`，这个词是对着一条实测选的。** 一个落地的补丁确立的是语料变了。
它不确立读者的问题现在能被正确回答：一次 asset 编辑不代表检索能取到它，而且即便在**每一张** gold
表都被许可的那些 turn 上，引擎的实测准确率是 0.7555 —— 所以凭一次落地 commit 就标记为已解决的投诉里，
大约**每四个有一个**仍然是错的。有一个便宜的升级，且只有一个：重跑受影响问题的 T3 coverage fixture
花费约 $0，它许可一个更窄的说法 `retrieval_verified` —— *回答这个问题所需的表现在可以取到了*。
本设计中没有任何东西许可 `resolved`。

**派生的**（由语料决定），每次读取时依据已加载的语料、bundle 记录的两个哈希、以及 bundle 的
post-state 文本重新计算：

| 派生状态 | 条件 | 为什么两状态模型表达不了它 |
|---|---|---|
| `handed_off` | 已加载语料的哈希仍等于该补丁的 base | — |
| `landed_verified` | 已加载语料的哈希等于该补丁预期的 post-hash | — |
| `landed_matched` | 哈希不同，但 bundle 触碰过的每个 asset 都在，且其 `summary`/`body` 与 bundle 的 post-state 一致 | **常见的真实情形**：一周内落地两个 bundle，于是一个**确实**上线了的变更在精确哈希匹配上失败 |
| `retrieval_verified` | 由上面任一条判定为已落地，**并且**该 observation 的检索 fixture 又通过了 | 免费阶梯能许可的最窄说法 —— 回答这个问题所需的表现在可以取到了 —— 也正是 `addressed` 刻意止步的那一步 |
| `superseded` | 哈希已离开 base，而内容不在那里 | 一次 `git apply` 冲突、一次语料 CI 重排、或评审者在提交前改了补丁 —— 三者都正常，而两状态模型会把它们统统静默标成「已交接，永远」，那正是本设计要取代的那个关不掉的 `open: true` 在上一层的复现 |

**上线的是五个派生状态，不是这张表起草时的四个。** `retrieval_verified` 就是上面两段说的那个升级；
它是一个状态而不是一个标志位，因为「这个变更落地了吗」和「引擎现在够得着它了吗」是两个问题。
`feedback/lifecycle.py::derived_state` 只在 fixture 真的跑过并通过时才给出它：一个没跑的 fixture 不算
通过，这与 `tools/verify_patch.py` 对一个没跑的层级采用的是同一条规则。

**`closed` 不是一个状态。** 没有任何东西分支于它。`open` 计算为 `state not in TERMINAL`，从不存储，
于是本设计要取代的那个关不掉的行不再是可表达的东西。

### 4. 本仓库不写语料，补丁由人手动应用

流水线把 asset 暂存到 `<proposal dir>/assets/<namespace>/<id>.yaml`，在 `corpus_root` 之外，理由有两条
且都不是品味问题。`corpus/hash.py::corpus_content_hash` 会摘要根目录下的一切，所以一个暂存文件就会通过
`measure/gates.py::_corpus_content_hash_gate` 作废一个正在跑的 arm。而 `corpus/store.py::load` 会遍历
整棵树，所以一个带合法 `asset_type` 的暂存文件会**被加载并被服务** —— 一份模型撰写的草稿未经任何评审
就进了分析师的 prompt，这正是 v1 的伪造缺陷原样重现。

**因为 M1，一次编辑不是一次 `write`。** 创建走 `corpus/store.py::write`。编辑走新的
`corpus/patch.py`：它用 PyYAML composer 的 node mark 定位字段，做外科式文本替换，于是改一个词的
`summary` 是一行 diff，而注释存活。`store.write` 是一个创建原语，本 ADR 就这么称呼它。

建了，而且比起草时更窄。`patch.py` 导出 `locate`、`read_field` 和 `apply_edit`，**没有创建函数** ——
创建仍然走 `store.write`，于是这个模块永远不必推理一个还不存在的文件。`apply_edit` 返回新的文件文本
而不写盘：调用方是一个想要 diff 的 bundle 导出器，而一个既计算又提交变更的函数无法用来预览一次变更。
有两道栏杆出自建设而不是设计。它拒绝 `EDITABLE = {summary, body}` 之外的任何字段；它彻底拒绝
`governance`、`provenance`、`audit` 和 `columns`，于是 Decision 8 的禁令写在模块里而不是写在 prompt 里。
而且它会把自己刚产出的文本重新解析一遍，要求那个字段读回来就是被要求写入的值 —— 没有这一步，一个渲染
器缺陷会让一个补丁落地成「值不是它被给定的那个值」，这类缺陷以各种形式出现，而在此之前没有任何东西
在看。

交接物是一个 **bundle**：一个目录，含 `MANIFEST.yaml`、`COMMIT_MSG.txt`、`changes.patch`、`after/`
和 `evidence/`，由本地 CLI 产出。应用它是在本进程无法写入的那个仓库里 `git apply` 加一次 commit。
那就是 provenance 闸门。

**它是 `git apply`，绝不是目录拷贝，而这是正确性要求，不是偏好。** 设计会议的初稿用
`cp -r assets/. $CORPUS_DIR/` 来应用 bundle。服务语料把一张表的列放在**内联**位置，一表一文件，而
`corpus/store.py::_split_inline_columns` 在加载时把它们拆成各自的 asset。于是一个暂存的独立 `column`
文件被拷进那棵树后，加载器会拿到同一个 asset id 两次 —— `store.load` 会带着 **problems 为零** 返回它
（M1），随后 `build_index` 里抛出 `ValueError: duplicate index id`，让每一次 `Session` 构建都失败。
那是一次完整的服务中断，发生在**commit 之后**，而且绕过了一个看不见它的一致性检查器。两个结构性
答案，都采纳：这个环**根本不许创建 `column` asset** —— 列在其所属表下内联撰写，id 是派生出来的
（`corpus/identity.py::derive_column_id`）—— 而 asset id 的唯一性成为一条在语料仓库 CI 里、合并之前
运行的一致性规则。

### 5. 流水线是一个本地进程，不是一个被服务的 graph；每个角色的边界就是它的工具清单

**本节没有任何东西被建成。** 没有 `triage/` 包，没有 Reproducer、Diagnoser、Author 或 Curator，而
`tools/check_imports.py::LAYERS` 里有 `feedback`、没有 `triage`。第 0-6 步交付的是存储、阶梯和 steward
界面；分诊和撰写由人做，下面的推理是「一旦真去建流水线，它必须回答的东西」。本节按设计读。

`triage` 是一个由 `python -m governed_bi.triage` 调起的 `StateGraph`。它**不是** `langgraph.json` 的
条目，也**不是** `serve` 的子图 —— `ServeInput` 是 A2/A3 的信任边界，而一次 triage 运行跨越多个 thread，
所以也没有一个 thread 能让它嵌进去。

**设计会议提议把它注册进去，而红队推翻这一点是对的。**
`api/auth.py::_no_state_writes_on_a_new_run` 只检查 `command`，拒绝 `command.update` 和
`command.goto`；一个 `{"assistant_id": "triage", "input": …}` 形状的建 run 载荷根本不带 `command`，
于是 `_command_of` 返回 `None`，钩子一声不吭地返回。把这个 graph 注册进去，等于把一次「花掉五个 serve
turn、碰数仓、写一个没人清扫的 checkpoint」的运行，交给任何能对这个端口开 socket 的东西。

诚实地陈述这个增量，而不要把它说成一个新的洞：平台自己的 `/threads` 和 `/runs` 本来就允许匿名调用方
在 `serve` 上花模型预算，`api/routes.py` 原话就是这么写的。注册 `triage` 改变的是**每次请求的上限**——
从一个 turn（约 45k token）变成一次只由操作员设定的 cap 兜底的 fan-out（默认约 290k）—— 而且它是在
那个还会写文件的 graph 上改变的。一个本地入口点不花任何代价，而且这本来就是所有者需求里说过的话：
产出补丁的那一步是 CLI，不是路由。

**一个后果，而且它是简化。** 没有部署级 checkpointer 就没有 `interrupt()`，所以流水线没有
human-in-the-loop 暂停。当 Diagnoser 无法定夺一个语义问题时，这次运行**结束**，并向存储写入一条
`needs_sme` observation；由 steward 在复核界面上回答，那个动作起草补丁。这从设计里删掉了
`authorise_resume`，连带删掉了它解决不了的那个问题 —— 在单一 principal 下，这道闸门比较的是批次
**发起者**和恢复者，而不是投诉的那位读者，所以它谁也区分不了。一个没人能被指认为「回答者」的暂停，
价值低于一行写明「谁在等什么」的队列记录。

**不使用 `deepagents`**，而 `pyproject.toml` 已经写了原因：`FilesystemMiddleware` 贡献一个不可移除的
`write_file`，「which is exactly the generic write channel that let v1 forge
`source=human, status=certified` on curated assets」。一个整个主题就是「模型可以做哪些写入」的流水线，
不能建在一个强制提供通用写工具的 harness 上。所以用 `StateGraph` + `langchain.agents.create_agent`
节点，也就是 `serve/nodes/agent_core.py` 本来的样子，并给**每个角色不同的工具清单** —— 这是本仓库唯一
信任的边界，依 `corpus/schema.py::Governance` 的原话：「exclusion is human-only, **enforced by the
absence of a tool**」。

四个角色，这种分离不是风格问题：

| 角色 | 模型 | 是否看数据库 | 看到的语料 | 可写 |
|---|---|---|---|---|
| **Reproducer** | 无 —— 它*调用 serve graph* | 仅经由受治理路径 | 该 turn 自己的 | 无 |
| **Diagnoser** | 主模型 | 是，受治理 | 该 cluster 的 schema —— **比失败 turn 的许可范围更宽** | 无 |
| **Author** | 主模型 | **否** | 该 cluster 的 schema | 一个暂存工具 |
| **Curator** | 无 —— 确定性文件操作 | 否 | — | triage 运行记录 |

**设计会议提议了第五个 —— 一个 Adversary，把 held-out 问题在试验语料上重放，看补丁是否破坏了别的东西
—— 它被裁掉了。** 杀掉它的论证是它自己那个承重工具：`replay_question` 读的是一个*低于引擎噪声底*的
信号。配置固定不变的两次运行有 12.7% 的结果不一致（`open-work.md` §3.12），所以少量重放无法把「补丁
造成的回退」和「这台引擎本来就会掀的硬币」区分开。而它的 `withdraw` 投票是一个模型在评判自己引擎的
输出，也就是 OOF AUC 0.597 的 reflector。取代它的东西更便宜且确定：全树的 T1 一致性检查，约 3–26 秒、
$0，再加一个人读 diff。

这不是说对抗式复核没有价值。这是说**这一个**对抗者的仪器受噪声限制，而一个其度量分辨不出自己在找什么
的控制项，正是本仓库已经上线过两次、然后不得不撤回的那类东西。如果它回来，它得带着 null 回来：给机制
读数提供噪声底的那次夜间重跑（见 Open questions 4），也正是能告诉任何人「一个重放面板到底看不看得见
东西」的那次。

Reproducer 没有 agent，因为重新实现一遍就会复现出一台**不同的**引擎，而这恰是一个复现器唯一不能做的事。
Diagnoser 的放宽是重点：最常见的缺陷就是正确的表从来没被许可，而一个被限制在失败 turn 许可范围内的
agent 看不到本该在那里的表。Author 对数据库盲视，是为了让评审者读到的论证其前提都在文件里 —— 这是四个
角色决策中最弱的一个，而反方论证是真实的：最有价值的 `column` prose 说的是这些值实际长什么样，而一个
写这句话时不能跑 `sample_column` 的 agent 会写出一句貌似合理的，那种错**比**一句明显的错更容易通过复核。
如果真发生了，修法是给 Author `sample_column`，并要求每个暂存 asset 引用它用到的观察 —— 一个字段加一条
validate 规则，不是重新设计。

**「certified」的含义保持不变。** 一个暂存 asset 的 `provenance.status` 永远是 `proposed`，由代码写入。
ADR 0003 复盘里那句话 ——「'Certified' still means a human signed off, not that an independent model tried
to break it」—— 依然成立，而且现在是平凡地成立：本设计里没有任何模型试图破坏任何东西。即便 Adversary
上线了，它的判决也依然会被禁止写入 `Provenance`，因为它是一个模型在读另一个模型写的文本 —— 一段被注入
的 `body` 可以对它讲话 —— 而且它没有像 `govern/adversarial.toml` 对 SQL 闸门那样的 0/62 实测绕过率。
一个未经度量的仪器，不得铸造那一个由人拥有的状态。

### 6. 一个补丁绝不用 EX 来验证

六层阶梯。**T0–T3 在 agent 面上不花任何钱，也没有噪声底**；T4 和 T5 是唯二花钱的层。完整定义与通过
条件在工作参考里。

| 层 | 是什么 | 成本 | 能查出 |
|---|---|---|---|
| T0 | 只看补丁文件：parse、identity、加载器自己用的那些校验器 | 约 1.6 秒 | 引擎加载不了的文件 |
| T1 | 整棵树：一致性检查、`build_structure`、`build_index`、对抗套件 | 约 4–30 秒 | 重复 id（M2）、prose 规则、起不来的语料 |
| T2 | 树对语料自己声明的东西做绑定 | 约数秒，离线，$0 | 引用了不存在列的 metric（M2） |
| T3 | **成对检索，单进程，agent 模型关闭** | 约数分钟，约 $0（M4） | coverage：gold 表是否被许可了，逐题 |
| T4 | 对受影响问题做定向付费重放 | 数十次调用 | 答案是否翻转 |
| T5 | 一对成对 arm | 约 52 分钟，约 74M 输入 token | 一个 release，绝不是一个补丁 |

**六层里建了四层。** T0–T2 是 `tools/verify_patch.py`，T3 是 `tools/reproduce_observation.py`。
**T4 和 T5 没有建**，所以那两个花钱的层也正是不存在的那两层，而一个补丁真正携带的阶梯停在 T3。一个
没跑的层级在那份阶梯里是**缺席**，不是记成「跳过所以没问题」—— 因为一个跑不了的层级绝不能读作一个
通过了的层级。T2 比这张表最初写的更便宜：语料自己声明了它的表、列和 join，所以解析器离线、免费，
不需要数据库。

**T3 是核心**，而这道闸门是**逐题的，不是按比率的**：任何一道题丢失了 gold 表 coverage 就失败。这在
约 $0 且零方差的条件下分辨出单独一道题，而同一个统计量从两个付费 arm 读出来的分辨率是 1.94pp。它也
瞄准了正确的桶 —— v4 arm 的 438 个失败里有 73 个是 coverage miss，是 257 个语义错误之后最大的**可赢**
桶，而那些语义错误在 T5 以下不可见。**瞄准 coverage 的补丁验证起来便宜；瞄准语义的不便宜，所以这个环
必须在被验证之前说清自己瞄的是哪一个。**

**读数是分层后的 EX，而机制计数的职责是划定分层。** 设计会议提议的是反过来的做法 —— 退役 EX，改读
一个机制指标，理由是更稀有的事件不一致数更少、因而 MDE 更小。**那个论证是单位错误，予以撤回。**
MDE 以全体人群的百分点计量，而这两个读数的基线率差两个数量级：`BINDING/r_star_projection` 的**最大
可能**效应是 2.15pp，对上它自己 1.12pp 的 MDE，只有 **1.92 个可分辨档位**，而 EX 有 28.5 个。一把量程
只有两格的尺子刻度再细也不是更好的仪器。`COLUMNS/r_column_not_allowed` 更糟，只有 1.16 倍 ——
早已饱和，而会议那张表把它标成了 decisive。

会议手上有、却打错分的，是那个真正回答「读者问的问题」的读数。限制到两个 arm 中任一命中该机制的 30
个 turn 上，**EX 走 +23.33pp，9 个不一致对，精确 McNemar p = 0.0391** —— 显著。它被判为「not decisive」
是因为 23.33pp 低于该分层自己的事后 MDE 28.02pp，而事后 MDE 不是显著性阈值；
`measure/stats.py::mde` 自己的 docstring 就这么说。所以：**用机制计数选出补丁可能触碰到的那些 turn，
再在那个分层上读 EX，那才是判决。** 一个仪器负责选人群，另一个负责给答案。

**而且 T3 在构造上对半个语料是盲的。** 它以 agent 模型关闭的方式运行，所以它检验的是检索 —— 而检索
索引的是 `summary`。`body` 进的是 *prompt*（M3）。所以一个只改 `body` 的补丁会在 T3 的每一项条件上
拿到干净的通过，而它改动的恰恰是模型会读的那段文本。这不是 T3 需要修的缺陷；这就是一道只看检索的闸门
能做到的事。这个后果必须由阶梯承载，而不是被撞见：**一个只改 `body` 的补丁没有免费验证器，直接进
T4**，而补丁触碰的字段决定它最便宜的诚实层级。这一点记在补丁上，好让一次 `body` 编辑不能凭一个绿色的
T3 被挥手放过。

**每一道闸门都是增量闸门。** 服务语料本身就产生 361 个 `build_structure` problem，所以一个「零 problem」
的闸门会拒掉生产。一道在既有存量上就触发的闸门就是一道会被豁免的闸门，而豁免正是一个真实发现变绿的
方式。

### 7. 一个 corpus release 是一个被声明的处理变量，而它的 knob 当时不存在

**实测：`comparability_keys()` 当时是 50 个名字，没有一个含 "corpus"。** 所以一个处理变量**就是**语料的
arm 无法声明它，而 `register/arm_profiles.py` 会把每一个这样的 arm 判为 `cannot_evaluate`。另外，
`corpus_content_hash('../BIRD-corpus')` 在 HEAD 上是 `6e5c7b4be83d5682…`，而 `arms.toml` 在四个 arm 上
都声明 `86ed1dbf…` —— 中间那两个 commit 只加了 `LICENSE` 和 `README.md`，没有任何 asset 变化，摘要还是
动了。**所以今天用 `--arm v4` 跑当前 checkout 会被拒绝。**

因此：一个新的可比性 knob `corpus_release`，命名的是一个**tag** 而不是一个目录；补丁持续落地进语料
仓库，而 arm 钉住 release，于是控制组不会在一次度量底下移动。`require_power` 通过 `ArmProfile` 上的
`hypothesised_effect` 和 `readout` 拿到它缺的那个调用者 —— 到那时，一个探测不到自己假设的 arm 会在
花掉任何东西之前就失败。

三件都建了：`register/knobs.py` 把 `corpus_release` 声明为可比性 knob，所以 `comparability_keys()` 是
51 个名字，其中一个含 "corpus"；`register/arm_profiles.py::recorded_corpus_release` 从一行记录里把它
读回来，并拒绝一个声明了别的 release 的 profile；而 `ArmProfile.hypothesised_effect` / `.readout` 存在，
`eval/provenance.py` 就是 `require_power` 的那个调用者 —— 也就是 `open-work.md` §3.10 记为缺失的那个。
`readout` 必须与 `hypothesised_effect` 一起给出，理由就是 Decision 6 说的那条：MDE 以全体人群的百分点
计量，而一个不说清自己以什么量计量的声明，正是本 ADR 撤回的那个单位错误。没有 `CorpusRelease` 这个
类型 —— 这个 knob 命名的是一个 tag，而表里的一个 tag 不是一个类。

**但约束 release 节奏的不是钱 —— 是可探测效应的存量，而这个存量快见底了。** T3 能看见的全部就是
coverage 欠账：79 道 gold 表从未被许可的题，最多值 +5.85pp，按实测 EX 折算是 +3.98pp。对上 EX 的 MDE
2.33pp，整个欠账里只有 **1.7 个可探测的 release** —— 而且每个 release 需要**两条**新 arm，因为磁盘上
没有任何一对能通过 `knobs_comparable`，所以第一个 release 得自己买一条控制组（约 150M 输入 token、
约 104 分钟）。一个花这些钱去度量一个只剩不到两格量程的量的 release 计划，是一个结果为「我们什么也
没学到」、并且写得很贵的计划。

所以 release 的头条读数是 **T3 的逐题 coverage delta**，分辨率一道题（0.08pp），成本约 $0；而一对成对
arm 是**代码**变更需要定价时你才买的东西 —— 不是一次 corpus release 例行支付的东西。一条 release arm
本会花掉的那 75M token，更该花在产出上面那个读数目前缺失的 null 上（Open questions 4）。

### 8. 这个环不许撰写的东西

由「工具的缺席」执行，以及由覆写的代码执行 —— 而不是由一句请求模型配合的 prompt 执行。

| 字段 | 规则 | 为什么 |
|---|---|---|
| `governance.excluded` | 这个环发出一条 prose **请求**；由人手动转写 | 「human-only, enforced by the absence of a tool」（`corpus/schema.py::Governance`）。流水线里没有任何东西可以设置它，而复核界面也不许渲染它 —— 一个能提议排除的屏幕**就是**那个「其缺席即控制」的工具 |
| `provenance` | 在代码里被剥除并重新盖章为 `source: curator, status: proposed` | 这就是 v1 的伪造 —— 一个通用写通道铸造出了 `source=human, status=certified` |
| `confidence` | 绝不从复现率写入 | `corpus/validate.py:132` 已经用 prose 警告过：「a curation-time belief and never an outcome score — the first thing a feedback loop will want is to write a hit rate here」。那个比率写在 triage 运行记录上 |
| `reliability.status` | **允许** | ADR 0005 声明它可由 AI 撰写：「`suspect` argues against a column and the analyst still sees it」 |

**上线的东西比这张表更窄，而且只有一行是活的。** `corpus/patch.py::EDITABLE` 就是
`{summary, body}`，别无其他；而 `governance`、`provenance`、`audit` 和 `columns` 作为字段路径被彻底
拒绝 —— 于是前两行由模块而不是由 prompt 执行，后两行则根本没有可被行使的路径：`confidence` 和
`reliability.status` 在已上线的阶梯里完全不可编辑，这让 `reliability.status` 在设计里被允许、在实现里
够不着。这张表是「一条能撰写整个 asset 的流水线」必须遵守的东西。

### 9. `body` 和 `summary` 是不同的通道，需要不同的规则

来自 M3。`body` 进模型的 prompt；`summary` 进检索索引。一条不说清管哪个字段就去管「prose」的规则，
就是一条会漏掉两者之一的规则。

新增的全树一致性规则，加进 `tools/check_corpus_conformance.py` 的 `RULES`：一个 metric 的 `expression`
必须能解析并在其 `base_table` 上解析得到（M2 的 28 + 23 条发现）；任何模型可见的 **`body`** 不许点名
一个 `governance.excluded` 的列或 asset（M3，当前活跃对象为零，所以加上它是免费的，也不可能造成回退）；
模型可见文本用 `govern/guard.py::GUARD_RULES` 检查而不是把它们抄一遍；`certified` 要求一个 human
source；以及 asset id 在全树唯一（M2 那个静默的重复）。

**本 ADR 对自己设计会议的一处更正。** V10 和 V12 曾被当作可以倚靠的既有内容扫描器提出。它们不是披露
规则：V10 是「no text discloses how an unreliable column was made」，它是为 BIRD 的混淆诱饵而存在的；
V12 管的是 held-out 问题泄漏。两条都在管**基准完整性**。在一个生产语料上它们什么也不管。所以 ADR 0003
那个洞今天一条规则都没有，而新规则不是对既有控制的加固 —— 它是第一条。

### 10. 分析师可以重新提问，而这才让它成为一个环

**没有建，本轮也不打算建。** 没有 `/reports` 页面，也没有 re-ask 动作。理由与砍掉上报 UI 的是同一条：
这个部署上所有角色都由同一个人担任，所以一份按读者的上报清单和一次通知都没有服务对象，而进入这个环的
输入是 eval artifact，经由 `tools/import_eval_failures.py`，而不是某个人点按钮。照本节自己的定义，上线
的东西是一个队列而不是一个环；等到真有第二类受众，要建的就是这一半。设计如下。

本设计的每一处都在 prose 里承诺这件事 —— `landed_verified` 的文案原话就是「ask your question again」——
而设计会议没有交付任何做这件事的途径。所以：reports 页面在任何派生状态为 `landed_verified` 或
`landed_matched` 的 observation 上带一个 **re-ask** 动作。它在一个**新** thread 上打开聊天界面，预填
存储早已从 turn 记录上抄下来的问题文本。

用新 thread 而不是原来那个，理由是 `api/raised_write.py` 当年详细记录过的「不要写进别人的 thread」，
以及在旧 thread 上再开一个 turn 会继承 25 个 turn 的上下文，而那不该进入这次比较。

它花大约半天，而且它是本设计中唯一让提交投诉的那个人自己搞清楚修复有没有奏效的东西。没有它，这是一个
队列，不是一个环。**它绝不能做的是给自己打分：** 引擎不比较新答案和旧答案，也不因为这次重问而推动任何
状态。配置固定的两次运行有 12.7% 的结果会翻转，所以一次重问不是证据 —— 它是读者在看，而这是唯一可得的
判断，也正是一开始被请求的那个判断。

### 11. 回执藏在内容里

一个落地的 asset 在 `Provenance.source_refs` 里带 `obs:<observation_id>`。引擎通过**读取它本来就要加载
的语料**来得知一个变更落地了 —— 没有 webhook，没有回调，没有第二个事实来源。除了「变更真的在那里」，
没有任何东西能把一个 observation 标记为 `addressed`。

**`source_refs` 不是什么：不是一个证明。** 它是一个人类可编辑文件里未经校验的自由文本。一个拼写错误
会让一个 observation 隐形；一段被复制的块会把一个变更归因到并非其来源的投诉上；而一个存储从未听说过的
id 是一个悬空引用，引擎必须**报告**它而不是忽略它。所以这个核销器是一个**报告者**：它打印 matched、
unmatched、dangling，绝不从一个它无法佐证的字符串里发明出一个状态。Decision 3 里的派生状态检查才是
那份佐证 —— asset 必须在，*而且*它的文本必须与 bundle 的 post-state 一致 —— 而 `source_refs` 让这次
join 变便宜，不是让它变真。

不用 `Audit.extra`：那是未知键的逃生口，是故意留出的、唯一一处「未知键被保留而非拒绝」的地方，把一个
join key 放进去会让它变得找不到。也不新增 `ProvenanceSource` 成员：`source` 说的是谁撰写了这个 asset，
不是什么促成了它。

建成 `tools/check_landed.py`，而且它就是一个报告者：matched、unmatched、dangling，任何地方都不写状态。

---

## 被拒绝的替代方案

**引擎去开一个 pull request。** 拒绝。它需要一个本仓库已经决定不持有的凭据，它让本仓库变成语料作者
（Context 3），而整份设计倚靠的那一个控制项就是「人的 commit 才是写入」。一个 bundle 加 `git apply`
对工程师而言机械成本相同，而权限留在原处。

**保留通道，用一条同 `report_id` 的关闭行追加、按 last-write-wins 折叠来关闭。** 依 Decision 2 的四点
拒绝。它是对一个装不下可变行的存储的绕行，而且每关闭一次投诉就让一个没人清扫的通道多一行。

**保留通道，让关闭完全变成对语料的读时 join。** 以**不充分**为由拒绝，不是以错误为由 —— 读时 join
被采纳用于落地状态（Decision 3）。但一个被分诊为「引擎是对的」的 observation 永远不会落地任何东西，
所以对语料的 join 永远关不掉它。关闭同时需要一个 steward 推动的存储态**和**一个语料决定的派生态，
这正是 Decision 3 说的。

**用一对成对 arm 作为补丁闸门。** 依 Context 6 的算术拒绝。这道闸门拒绝不了任何可探测的东西，而每次
尝试花约 74M 输入 token。

**要求聚成三条才分诊。** 拒绝。一个错误答案是关于**信念**的弱证据，不是关于「是否该去看」的弱证据；
而在一个新部署上，如果没人看第一条投诉，就没有从零条到三条的路径。数量买到的是置信度，而置信度属于
记录，由 steward 在那里定价。

**由一个模型来组装 bundle。** 拒绝。组装 diff 不是一个判断，而一个模型组装器正是本仓库会意外变成
语料作者的方式。

**把流水线的 prompt 放进 `PROMPT_REGISTRY`。** 带实测拒绝。`register/prompts.py::prompt_set_hash`
摘要整个注册表 —— 名字、变体和正文 —— 在本树上它是 `b1f9e4d7d230cb97`，其前缀正是 `open-work.md`
§3.13 里 v4 的 `b1f9e4d7`。一个 triage prompt 放进那个注册表，会让每一次有人重写 Diagnoser 措辞都移动
所有 serve arm 的处理身份，而 serve 行为零变化。那是故意复现 `expand_hops` 缺陷。做法是一个模块里两个
注册表，加一个导入期断言：它们**划分**这个模块 —— 因为一个**两边都不在**的 prompt 是一个没有任何哈希
覆盖的 prompt，严格更糟。

**在 `register/knobs.py` 里声明流水线的配置。** 同理拒绝：`_resolved_knobs` 把每一个已声明 knob 放到
每一行 serve 记录上，而 `_knobs_resolved_gate` 会比较它们，所以一个 triage knob 会改变每一次 serve 运行
的配置哈希，而 serve 行为零变化。

**在补丁变成 bundle 之前加一道应用内审批闸门。** 拒绝。`api/auth.py` 返回单一 principal，所以一次审批
谁也区分不了：谁碰到端口谁审批。一个审批者无法被指认的审批暂停，是一个被贴上「控制」标签的 UI 装饰。
steward 界面是一个队列加一个 diff；它的接受动作推动一行状态并产出一个 bundle，它不授权任何东西。
UI 文案必须这么说。

---

## Consequences

1. **两个新包，以及一个 `LAYERS` 决定。** `tools/check_imports.py::LAYERS` 必须穷举 `src/governed_bi`
   下的每个包，所以新增一个就强迫做一次定位。`feedback` 紧接在 `corpus` 之后 —— 它用与加载器相同的
   校验器判断一个补丁，且不得 import `serve`、`govern`、`api` 或 `eval`。**是一个包，不是两个：**
   `feedback` 放在了那里，而 `triage` 根本不在这张表上，因为流水线没有建。`LAYERS` 必须穷举每个包，
   正是将来真去建它的那一天会强迫做出那次定位的东西。
2. **`api/visibility.py::visible()` 覆盖不到新界面，而且覆盖不了。** 它收窄的是一个**语料投影**；
   一个 observation 的自由文本是一句人写的话，里面没有可收窄的东西。所以新路由需要一个与 `visible()`
   并列的第二个收窄函数，而那条自由文本豁免必须被声明并断言，而不是被撞见 —— 与 ADR 0012 §8.5 下
   `/audit/corpus` 的 `problems` 采取的是同一笔交易。建成
   `api/feedback_routes.py::_narrowed`，而且它是**白名单**而不是黑名单：一个路由没点名的字段到不了
   客户端，于是给存储加一列不会默认把它披露出去。它挡住的是基准的那几个字段 —— `gold_sql`、
   `gold_fingerprint`、`pred_fingerprint` —— 而这是本 ADR 没有预见到必须做的一次披露收拢。
3. **面向工程师的动词是新权限，它们以未挂载状态发布。** 藏在 `GOVERNED_BI_FEEDBACK_ADMIN` 之后，返回
   404 而不是 403 —— 403 会确认这个路由存在。一个分叉能开启的最便宜控制是**只**给这几个动词加一个
   token，而与 2026-08-13 那次回退不同，它不花任何代价：LangGraph Studio 从不调用它们。照原样上线了；
   而在单一 principal 下，那个环境变量开关就是控制的全部 —— 把这句话直说，好过把一个开关打扮成一道
   谁也区分不了的闸门。
4. **在唯一一个默认启用的动词上，本设计是按算术在收窄。** 一行一次、带配额、可清扫 —— 对比它取代的
   那种行：被重新序列化进该 thread 之后每一个 checkpoint、且存储没人清扫。提交路由返回 **201** 而不是
   200 也是同一个理由：它在一个存储里创建了一行，而客户端不该为了知道这件事去读 body。
5. **一个新的 register 字段。** 交付的 asset 集合无法从 turn 记录里恢复 —— `context_hash` 摘要了它，
   而摘要不可逆 —— 而复核界面和复现器都需要它。它在同一个 commit 上就获得具名消费者，因为一个没有
   读取方的字段正是 `tests/conformance/test_the_declared_but_unconsumed_set_does_not_grow.py` 会让
   构建失败的那个缺陷。**没有建**，而理由正是那条规则：它的消费者是流水线的复现器，流水线没有建，
   所以现在声明这个字段恰好就是那道闸门要拒绝的「声明了没人消费」。复核界面自己推导那一列，并在标题
   里说明。
6. **`corpus/snapshot.py` 拿到它的第一个调用者** —— T4 重放所依据的试验语料。随着 Adversary 被裁掉，
   那个调用者变成一个重放固定题集的确定性驱动，而不是一个自己挑选重放对象的模型，这严格更好：它可审计。
   Open questions 2 记录了在这个调用者存在之前必须修掉的 `snapshot` 安全缺陷。
   **这件事没有发生，而实际的答案比设计的那个更好。** T4 没有建，而免费阶梯是在**内存里**施加编辑、
   不是在一棵拷贝出来的树上，所以它根本不调用 `snapshot` —— 没有 scratch 目录，没有 `rmtree`，也没有
   可以指错的路径。`snapshot` 至今在自己的测试之外没有调用者。那个缺陷还是修了（`222d1bf`），因为一个
   数据丢失缺陷不会因为「还没有调用者」而变安全。
7. **一位读者的自由文本会在任何人看到它之前经过两次模型调用** —— 这是流水线的后果，所以还不是一个
   活着的后果。在已上线的东西里，这段自由文本经过的模型调用是**零**：
   `tools/reproduce_observation.py` 重跑的是这条 observation 的*问题*，从不是它的笔记，而这条笔记的
   第一个读者是 `/review` 里的 steward。下面这段是「一旦真去建流水线，它继承的残余风险」，原文未改。
   设计会议关于「暂存树在 `corpus_root` 之外」即结构性控制的说法**并不完整**。这段文本被 Diagnoser
   读到，并经由 diagnosis 转述给 Author；
   而且 —— 这是与那个说法矛盾的部分 —— 由它派生出的暂存 prose 会被 T4 的试验重放渲染进一个**真实**的
   prompt。真正约束住这件事的东西更弱，必须照实说：这段自由文本在每一个承载它的 prompt 里都被定界并
   框定为数据；试验语料是一份任何 serve 请求都到不了的拷贝，且在未配置 scratch 目录时是关闭的；暂存
   输出会被 `govern/guard.py::GUARD_RULES` 检查；而这个环不能写 `governance` 或 `provenance`。不存在
   对 prose 含义的内容扫描闸门，残余风险是一个被人批准了的投毒 asset。（裁掉 Adversary 去掉了四个读取
   点中的两个，这是一个支持裁掉它的安全论据，但不是裁掉它的理由。）
8. **`raised` 通道的行本该是被迁移而不是被遗弃，而它一行都没有。** 定下来的做法是 Decision 2 的那个：
   `tools/drain_raised.py` 加一个带具名终止条件的读取并集，代价是多一个构建步骤 —— 因为替代方案，也就是
   删掉并宣布那些行不可达，是一句得有人签字的话。然后在删除之前去数了这个通道：**checkpoint 存储、
   harness 存储和全部 23 个平台 thread 行里，一行都没有。** 所以没有人需要签什么字，也没有写任何东西。
   drain 工具不存在，并集不存在，而 `4a0d11a` 在一个 commit 里删掉了通道，用「写入方那道接缝已经没了」
   的断言代替一次迁移。所以本条描述的后果是**退役，不是交付** —— 记下来而不是删掉，因为下一次真要删一个
   通道时，读者需要的正是这段推理，而它的教益是：数一下比为「数出来可能是多少」做计划更便宜。

---

## 验收标准

可证伪，且每条都点明它必须挺过的那个变异。九条里有四条在本分支上由测试断言，四条等流水线，一条退役。
每条自己说明是哪种。

1. **serve 的处理身份不移动。** prompt 注册表拆分之后，默认变体下的 `prompt_set_hash()` 与
   `b1f9e4d7d230cb97` 逐字节相同 —— 在本树上实测于 2026-08-23。变异：编辑任意一个 triage prompt 的
   正文；`prompt_set_hash()` 不变，`triage_prompt_set_hash()` 移动。**等流水线，而且是以一个更平淡的
   理由半满足的：** 第 0-6 步一个 prompt 都没加，所以没有东西可拆分，而 `prompt_set_hash()` 今天重测
   仍是 `b1f9e4d7d230cb97`。不存在 `triage_prompt_set_hash()`，所以变异那一半没有被断言。
2. **一次完整的 triage 运行不碰语料。** `corpus_content_hash(corpus_root)` 前后相等，**并且**
   `store.load` 返回的 asset id 集合不变。变异：把暂存目录指到 `corpus_root` 里面；测试失败。
   **等流水线。** 上线的是更弱的那个说法，属于免费阶梯：它在内存里施加编辑、任何地方都不写文件，由
   `tests/conformance/test_the_ladder_checks_the_edit_and_not_the_file.py` 断言。
3. **Author 无法伪造 governance 或 provenance。** 一个脚本化模型发出
   `governance: {excluded: true, by: "human"}` 和 `provenance: {source: human, status: certified}`，
   产出的暂存 YAML 没有 `governance` 键、`provenance.status == proposed`，且记录上有一条排除请求。
   **等流水线**，没有 Author 可测。顶上位置的是结构性的东西：`corpus/patch.py` 拒绝把 `governance`、
   `provenance`、`audit` 和 `columns` 当作字段路径，所以即便调用方主动要求，这次伪造也没有路可走。
4. **每一个存储态都点明它的行动者。** 遍历转移表，对 `moved_by` 为空的存储态失败。这是把 Decision 3
   的规则变成机械的。**已满足**，在 `tests/feedback/test_every_stored_state_names_its_actor.py`。
5. **一致性检查抓住 M2 的全部四个破坏。** 今天漏掉的三个加上它抓住的那个，各作为一个合成 fixture 放进
   `tests/conformance/test_corpus_conformance_rules_fire.py`。**已满足**，分布在那个文件和
   `tests/conformance/test_the_whole_tree_rules_fire.py` 里 —— 全树那一半单独成文件，因为一条需要第二个
   asset 才会触发的规则无法表达成一个 fixture。
6. **新规则不靠豁免变绿。** 既有发现在语料仓库里**按名字**钉住，这个集合可以自由缩小、不可增长，而
   关闭其中一条会让构建像新增一条那样响亮地失败 —— 因为一个没人更新的缩小列表正是一个过期计数存活的
   方式。**已满足**，即 `tools/check_ratchet.py` 和
   `tests/conformance/test_the_ratchet_only_turns_one_way.py`。身份是「规则 + 文件与 asset」，不是那句
   消息，所以给一条发现换个措辞不会悄悄把它重新钉一遍。
7. **一个已关闭的 observation 离开队列，而一个 `superseded` 的补丁不读作 `handed_off`。**
   **已满足**，在 `tests/feedback/test_the_landing_states_are_derived_and_not_stored.py`。
8. **修订循环有界。** 一个其暂存 asset 永远通不过 `validate` 的脚本化模型，会在 `max_revisions` 处终止
   并撤回补丁，且恰好调用了那么多次 Author。**等流水线。** 没有修订循环，也没有 `max_revisions`。
9. **drain 有终止条件，并集有出口。** **退役**，而 Consequences 8 记录了原因：通道里一行都没有，所以
   `tools/drain_raised.py` 和读取并集从未建成，也没有终止条件可断言。这一条保留而不删掉，因为它点明了
   「分阶段删除」当初买来遮的那个风险，而它不再是风险的原因是一次实测，不是一次论证。

---

## Open questions

1. **一个模型到底能不能把缺陷定位到一个 asset 上？** Diagnoser 下游的一切都以此为条件，而最接近的一条
   实测令人沮丧：reflector 在「判断一条已执行语句是否回答了问题」这个**更容易**的任务上得 OOF AUC
   **0.597** —— 比数一数 agent 输出了多少 token 还差 —— 而且它的 `unsure` 桶与 `correct` 桶正确率一样
   （`open-work.md` §3.11）。如果一个模型分辨不出一个 turn**是否**错了，那么它能指出**哪一句话让它错了**
   的先验很差。这是缓解，不是回答：词汇表允许 Diagnoser 得出「没有 asset」，而第一个可发布模式是
   diagnosis-only，它不写任何 YAML。**如果 Diagnoser 是 reflector 那个水平，诚实的产品是一个不带撰写
   功能的分诊队列**，本 ADR 撰写那一半就是白写的。
2. **`corpus/snapshot.py::snapshot` 无守卫地删除它的目标目录。** `_refuse_nesting` 阻止嵌套；
   `_identify_corpus` 守的是 `restore`，不是 `snapshot`。实测：指向一个放着无关文件的临时目录，
   `shutil.rmtree` 把它们删掉了。所以快照路径绝不能从 caller 可影响的 id 派生，而 `snapshot` 也需要
   `restore` 有的那道守卫。这是既有代码里的一个缺陷，而本 ADR 的第一个调用者会把它武器化。
   **已关闭（`222d1bf`）。** 两个函数现在采用同一套识别，而 `snapshot` 多接受一种 `restore` 没有理由
   接受的情形 —— 一个**空**目录，它没有什么可失去的。那个本会把它武器化的调用者始终没有被建
   （Consequences 6），而这正是这次修复要紧的原因：一个还没有调用者的数据丢失缺陷，是一个在等它第一个
   调用者的缺陷。
3. **「被排除的列名出现在 `body` 里」这条规则应该拦截还是只报告？** 拦截需要一个已校准的误报率，而
   没人有；只报告则把这个发现放到本来就在读 diff 的那个人面前。但 M3 说当前姿态完全倚靠执行时拒绝 ——
   名字进 prompt，而点名它的查询被拒 —— 而一个企业分叉的 PII 故事不能只靠这一点。当前活跃对象为零，
   所以今天做这个决定很便宜，第一次有人排除一个列之后就很贵。
4. **机制读数没有实测 null，而一次夜间运行就能给它一个。** `run1`/`run2` —— 那个指定的重复实验 ——
   **ledger 行数为零**，所以磁盘上没有任何东西说明一个机制指标在配置相同的两次运行之间会动多少，而为它
   引用的每一个 MDE 都是从那一对自己的观测不一致率算出来的。事后计算，构造上如此，而
   `measure/stats.py::mde` 的 docstring 坚持这个读法。用当前 harness 重跑 `run1` 的配置就产出这个 null，
   让分层选择变成可预注册的，而且这是整份设计里最便宜的高价值实验。在它存在之前，分层是在看过 arm 之后
   选的 —— 那正是 `measure/signals.py` 的 docstring 花一整段警告的缺陷。

   **有一个数字禁止任何人引用：`BINDING/r_star_projection` 的 MDE = 1.12pp。** 它同时带三个独立缺陷 ——
   由该对自己的不一致率事后算出、没有 null 可对照、以及在其指标饱和之前只有 1.92 个可分辨档位。它看起来
   像仪器精度，实际上是一把只有两格量程的尺子。设计会议那张机制表还是在同一份设计所**禁止**的
   `False`-on-empty 约定下算的（1,351 对里有 12 对至少一侧 `attempts` 为空）；在规定的 `None` 约定下
   `mcnemar` 会正确地返回「未度量」，而限制到 1,339 个双侧对之后效应是 −1.94pp、p 值不变。**缺陷在数字
   的来源，不在数字本身** —— 而这正是为什么这个约定必须写在代码里，而不是留在习惯里。
5. **`tools/check_declared_is_consumed.py` 看不到本设计声明进去的四个命名空间中的四个。** 它的四条规则
   覆盖 knob、record 字段和 state 通道。`corpus_release` 是一个 knob，所以缺少读取方会按名字让构建失败；
   而 `ArmProfile.hypothesised_effect` 和 `.readout`、机制注册表的条目、存储的 SQLite 列、以及
   `Attribution` 的字段**不在**其中 —— 所以「第六条 finding 会让构建失败」这句话对本设计的一项声明成立、
   对其余不成立。补上它是再加一条同形状的规则；在那之前，这些新声明由评审而不是由 CI 守着。
6. **一个补丁落地之后，分析师的问题真的能被正确回答了吗？** 不，而且这里没有任何东西确立这一点。
   Decision 3 就是状态叫 `addressed`、$0 的升级叫 `retrieval_verified`、而没有任何状态叫 `resolved` 的
   原因。面向用户的字符串是一句重新提问的邀请（Decision 10），永远不是一个断言。仍然开放的是：实践中
   会不会有人接受这个区分，还是悄悄把 `addressed` 读成 `fixed`。
7. **投诉到底会不会聚类？** 本树上有零条。聚类键是一个猜测，而第一个月的分布就是那个实验。如果投诉大多
   是分散在不同表上的孤例，那这条批处理流水线就是一条穿着批处理外衣的逐事件流水线。
   **在真实的 73 条上得到了否定的回答。** 最大簇是 **3**，落在簇里的行占 **49%**。批处理论证撑不住，
   所以 `/review` 上线成一个列表加可选分组，而不是设计里规定的以簇为先的界面；而真去建流水线的话，它会
   是一条逐事件流水线。这是本 ADR 里唯一一个由实测而不是由建设关闭的开放问题，而它关闭的方向与设计相反。

---

## 本 ADR 不覆盖什么

- **认证。** 审计发现 A1 和 A7 是开放的，本 ADR 不关闭它们。它增加一道收窄接缝和一个默认未挂载的管理
  界面，并且明说：碰到端口仍然就够了。
- **谁拥有语料仓库的 CI。** 本设计规定了那个 CI 必须跑什么。它没说谁来写，而且没人写。自 `222d1bf`
  起服务的语料是一个 git 仓库（Context 5），而它根本没有 CI。那些 pin 存在它里面 ——
  `.conformance-pins.txt` —— 而 `tools/check_ratchet.py` 是从本仓库读它们的，也就是在合并的错误一侧；
  这一点被点明，而不是被算作 Decision 4 要的那道控制。
- **行级安全、多租户、或用户存储。** `docs/enterprise-fork.md` 不因本 ADR 而改变。特别地，存储不记录
  「谁提交了一条 observation」的身份，因为 `api/auth.py` 返回单一 principal，而在这里发明一个按用户的
  概念会是一个并不存在的边界。
- **让引擎变成策展人。** 流水线撰写的是**候选**。语料仍然由人拥有、在本仓库之外受版本控制、且无法从
  本仓库重建。
