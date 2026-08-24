# 回流路径 —— 工作参考

> ## 实际交付了什么，以及与本页的差异（2026-08-23）
>
> 第 **0-6 步已建成，在 `design/return-path` 分支上**。本页是当初商定的设计；有六处在实测后结果不同，
> 按本页而不是按本注记去做的读者，每一处都会做错。证据在 `docs/open-work.md` §3.10a-3.10c。
>
> 1. **`tools/check_closed_domains.py` 不存在，且 T2 不需要数据库。** §11 把 metric expression 解析器
>    放在活体 catalog 后面。它不需要：corpus 自己声明了表、列和 join，而 expression 必须与*那些*一致
>    —— 仓库（warehouse）是 serve 时 `govern/` 的事。T2 就是打过补丁的树上的一致性规则 **V17b**，离线且
>    免费。
> 2. **V18 砍掉了。** 五条新规则，不是六条。它没有活体样本、也没有校准过的误报率，上线只会是一条谁也
>    没法定量的规则。
> 3. **实测发现数，这也是这些规则与凭直觉写出来的规则的区别所在：** V17a **107 条，分布在 478 个
>    metric 中的 94 个**（设计里的 28 出自一个只做解析的原型 —— `DIVIDE(a, b)` 作为 SQL 能解析通过，
>    而它命名的函数任何 dialect 都没有，所以上线的规则还会去问
>    `govern/functions.py::PERMITTED_FUNCTIONS`）；V17b **17**；V19 **0**；V21 **1**，正是设计里点名的
>    那个文件；V23 **0**。棘轮（ratchet）钉住了 **101** 个身份。
> 4. **投诉的聚类很弱**，这以一个否定结果回答了 §12 的开放问题 7：最大的簇是 3，只有 49% 的行落在任何
>    一个簇里。设计里的批处理论证撑不住这个数字，所以 `/review` 是一个列表加可选分组。
> 5. **复现器必须带 `--embed` 跑。** §11 的 T3 建成了 `tools/reproduce_observation.py`，实际驱动它时
>    发现：只走 lexical 的复查会报出 2 张缺失的 gold 表，而该行记录的是 1 —— 一个假的「仍然复现」，读起来
>    和真发现一模一样。
> 6. **上报 UI 和 `/reports` 没有建，本轮也不打算建。** 这套部署上所有角色都由同一个人担任，所以通知
>    回路和按读者划分的报告列表没有服务对象。输入是 eval artifact：`tools/import_eval_failures.py`。
>    §15 里的上报界面（`raise-note.tsx` 重写、`category-picker.tsx`、`my-reports.ts`、
>    `report-status.tsx`）是给一个还不存在的第二类受众做的设计。
>
> 同样没有建、且在 §13 里就被列为后续步骤而非本轮的：agentic pipeline（`triage/`）、T4、T5，以及
> 超出 `corpus_release` 这个 knob 之外的任何 `CorpusRelease`。

读者与工程师的反馈如何变成一次语料变更。约束性决策在
[ADR 0015](adr/0015-the-return-path.zh.md)；本页是工程师照着实现的东西。
English: [The return path — working reference](return-path.md)。

**本页所有内容都还不存在。** 每一个路径、签名、路由和测试名都是设计。凡是描述代码树里已有代码的句子，
文中都会说明并点名文件。标注 **实测** 的数字取自 `governed-bi@464d1cb`，对
`../MS Fabric Facilities/corpus` 和 `../BIRD-corpus@74ff80c4`，测于 2026-08-22/23；其余数字都是估算，
且文中会说明。

---

## 0. 这个环，从头到尾

一条走查，好让本页余下部分读起来是一个功能，而不是一堆零件。例子取最常见的真实情形：分析师拿到一个
错的数字，原因是某个业务术语在这个数仓里是另一个意思。

**周一 09:14 —— 分析师。** Priya 问「上个月我们新增了多少活跃客户？」，拿到 4,102。她知道大概是 400。
她点答案卡上的 **This answer is wrong**，一个五行列表就地展开。她点了「我问题里的某个词在这里是别的
意思」。这一下**立即提交** —— 没有提交按钮，两次点击，零打字。回执出现在表单原来的位置，并给出两个
可选输入框；她填了一个：`expected: "about 400, not 4102"`。不再要求她做任何事，而回执说清了会发生
什么、不会发生什么：

> 已提交。数据管理员按最早优先复核这些。这台引擎不知道你是谁，所以不会有人给你发邮件 —— 到
> **My reports** 看结果。

一条 `Observation` 落进 `runs/feedback.sqlite`，带 `category: term_mismatch`、`state: open`，以及该
turn 的问题、SQL、许可表集合、outcome 和处理哈希的一份**拷贝**（§4）。

**周一 11:30 —— steward。** Dev 打开 `/review`。队列按最早优先，并做结构化分组：Priya 那一行落在一个
三条的 cluster 里，全是 `term_mismatch`，全在 `facilities.occupancy` 上。它上方的说明写着这个分组从未
读过那些问题。他选中这个 cluster，详情面板在决策条**上方**展示七个证据块（§15）：问了什么、回来了
什么；Priya 说了什么（她的 `expected` 被排版成它本来就是的那种引文）；SQL 和尝试 ledger，用的是她看到的
同一批组件；这个 turn 被允许读什么，附带路由器的 top-5 排名；以及哪些语料 asset 在 context 里 ——
并附上「rendered 这一列是派生的、不是记录下来的」这个注意事项。

第 5 块就是答案所在：*active customer* 这个 `term` asset 在 context 里，而它的 `summary` 对 `status`
列一个字都没说。引擎没有任何办法知道。他点 **Reproduce** —— 一次模型调用，而按钮就这么写着 ——
仍然返回 4,102。

他起草一处变更：一个字段，`term_active_customer.summary`，加上那个别名和那条规则。diff 按 register
声明的字段序逐字段渲染，带一个对着上限的实时字数统计 —— 因为一个 251 字符的 summary 如果是在导出
**之后**才发现，那就是一次白跑的往返。他把三条 observation 置为 `addressed`。

**周一 11:41 —— 阶梯。** T0 用生产加载器解析暂存文件。T1 对语料的一份**快照**（不是语料本身）跑全树
一致性、`build_structure` 和 `build_index`，按规则 id 报告无新增发现。T2 把这个 term 的 binding 对活体
catalog 解析。T3 成对重放检索、agent 模型关闭：三道受影响的问题上 gold 表保持被覆盖，其余没有一道
丢失 coverage。总挂钟时间约半分钟。总花费 **$0**（§11，M4）。

因为这个补丁改的是 `summary`，T3 在这里是一个真实的验证器。如果它只改了 `body`，补丁会带一条说明
「T3 看不见它，诚实的层级是 T4」—— 补丁触碰的字段决定它最便宜的诚实层级，而记录写明是哪一个。

**周一 11:45 —— 工程师。** Dev 跑一条命令：

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
```

拿到一个目录：外科式的 `changes.patch`（一行 diff，因为 `corpus/patch.py` 就地编辑字段而不是重新 dump
整个文件）、一份不含任何读者 prose 的生成 `COMMIT_MSG.txt`、post-state 文件全文、以及 `evidence/`
里每位读者原话（放在代码围栏里）。他在语料仓库里应用它：

```bash
cd ../BIRD-corpus && git checkout -b return/pat-… && git apply -p1 …/changes.patch
git commit -F …/COMMIT_MSG.txt
```

那次 commit 经过语料仓库自己的评审和 CI —— 一致性检查、棘轮，以及一个必须能起来的 `build_index`。
**这是整个环里对语料内容的唯一一次写入**，而且它是一个人做的。本仓库里没有任何东西能做这次写入。

**周二 —— 环靠「读」自己闭合。** 引擎重新加载语料。落地的那个 asset 在 `Provenance.source_refs` 里带
`obs:<observation_id>`，于是 `derived_state` 把 Priya 的 observation 与已加载语料对上，发现该 asset 在、
且其 `summary` 等于 bundle 的 post-state。她那一行现在读作 `landed_verified`。没有 webhook，没有回调：
**回执藏在内容里**，所以一条投诉只能被「变更真的在那里」标记为 addressed。

**周二 —— Priya 去看。** *My reports* 显示她那一行，带一个动作：**Re-ask**。它打开一个新 thread，
预填她原来的问题。她拿到 412。引擎**不**把这个和 4,102 做比较，也不因此标记任何东西为已解决：
配置相同的两次运行有 12.7% 的问题会翻转，所以一次重问不是证据。它是读者在看 —— 而那是唯一可得的
判断，也正是她周一请求的那个判断。

**这个环在每一步都拒绝声明什么。** 状态是 `addressed`，绝不是 `resolved` —— 在每一张 gold 表都被许可
的那些 turn 上，引擎的实测准确率是 0.7555，所以凭一次落地 commit 就关闭的投诉里大约每四个有一个仍然
是错的。唯一的免费升级是 `retrieval_verified`，而它只说明那些表可以取到了。

### 同一条走查，走歪的时候

四个分支，因为一个功能同样由这些定义：

| 发生了什么 | 去哪 |
|---|---|
| Dev 复现，发现现在答对了 | `declined` / `cannot_reproduce`，而 Priya 读到「在现在运行的语料上再问一遍，它答对了。如果你仍然能复现，请带上新答案重新提交一次。」 |
| 缺陷在引擎而不在语料 —— 比如 `r_star_projection` | `declined` / `engine_defect`。没有东西可打补丁。一条得不出这个结论的流水线一定会去打补丁，这就是词汇表里有这个词的原因 |
| `git apply` 冲突，或语料 CI 重排了文件 | `superseded`，在下一次读取时派生出来，并退回 steward。一个两状态模型会把它叫成「已交接，永远」—— 那正是今天关不掉的 `open: true` 在上一层的复现 |
| Dev 定不下 *active customer* 是什么意思，而没人可问 | `blocked_on_a_person`，附一行必填说明。Priya 读到「正在等一个人：<说明>。没有任何东西在自动推进这件事。」没有指派人下拉框，因为没有用户存储可以填充它 |

---

## 1. 词汇，以及它避开的那些冲突

这个代码库已经把大部分显而易见的词用掉了。下表每一个规范术语，都是对着一个「会把两个含义压到一个名词上」
的冲突挑出来的。

| 规范术语 | 是什么 | id | 被拒绝的名字，以及原因 |
|---|---|---|---|
| **Observation** | 读者或操作员看到的一件事，归属到恰好一个 turn | `obs-{yyyymmddThhmmssZ}-{8hex}` | *Signal* —— `measure/signals.py::Signal` 是选择性预测的排序特征。*Report* —— 与 `eval/report.py` 冲突，更糟的是它对每一个 BI 用户都意味着「报表」 |
| **Cluster** | 共享同一定位键的若干 observation。**派生，从不存储** | `cls-{16hex of the key}` | *Finding* —— `tools/check_corpus_conformance.py::Finding` 是一条一致性违规行 |
| **Patch** | 一个候选语料变更：一个或多个 asset 的创建/编辑，附带一个 intent | `pat-{obs or run id}-{6hex}` | *Proposal* —— `eval/projection.py` 已经用「proposal」指模型未受治理的 SQL，那东西在任何地方都没被执行。在一个专门审计这件事的仓库里，不能让一个词背两个意思 |
| **Triage run** | 流水线的一次执行，及其产出的证据 | `trg-{yyyymmddThhmmssZ}-{8hex}` | — |
| **Bundle** | 工程师在语料仓库里应用的那个目录 | `bnd-{patch id}` | *Handoff* 说的是它的用途，不是它是什么 |
| **Corpus release** | 语料仓库里的一个 tag 加上它的 `corpus_content_hash`。arm 钉住的东西 | — | *Corpus version* —— `.env` 里那个目录指针已经在被这么叫了，而它正是这东西要取代的 |
| **Category** | 读者对「哪里错了」的细化。字段名就是 `category` | — | *Signal* 又一次 —— 用一个本库已拥有的类型名去命名字段，是同一个冲突下移一层 |
| **Return path** | 整个环，作为对话里的一个名词 | — | *Feedback loop* 在 prose 里没问题；当包名太含糊 |

**两条命名规则，两条都在拦一个真实的树内歧义。** 生命周期字段永远叫 `state`，绝不叫 `status` ——
`status` 在这棵树里已经有三个意思（一次 run 的、一个列的 `reliability.status`、以及一个 provenance
的 `status`），而第四个意思正是读者读错一个的方式。以及**任何类型都不许叫 `Evidence`**：
`corpus/schema.py::Audit.evidence` 拥有这个词，而证据包叫 `Bundle`。

**绝不可重命名的 wire 值。** `kind ∈ {from_refusal, wrong_answer}` 已经由
`ui/components/answer/raise-note.tsx` 发出、由 `serve/raised.py::RAISED_KINDS` 校验、并由
`api/thread_turns.py::_open_raised_of` 的收窄逻辑读取。扩宽或重命名它会一次打断四个调用点，而且没有
收益。`wrong_answer` 有一个真实职责：「有问题但我说不清是什么」这个兜底桶。

**`report_id` 退役，而本页把它的代价算低了。** 一位批判者以最强的可用理由主张保留 *Report* 作为
规范名词：`report_id` 和 `rpt-` 前缀**已经**是 wire。它输在一点上 —— "report" 会成为这个系统里这个
词的**第三**个意思，排在 `eval/report.py` 和每个 BI 用户所指的那个东西之后，而一个词在一个系统里背
两个意思，正是本仓库专门审计自己的那个缺陷。`Observation` 与任何东西都不冲突，而且更诚实：读者说的
是他们看到了什么，不是什么错了。

**本页搞错的是代价。** 它写着「这是一次所有者已被删除的重命名，不是一次带 churn 的重命名」，理由是
`serve/raised.py::mint_report_id` 反正要删。所有者确实删了；**契约**没有。动手时实测到：
`docs/openapi.json` 用七个必填非空字段钉住了 `RaisedRowResponse`；`report_id` 被声明在待办队列的
`meta.columns` 里，**正因为客户端拿它做卡片的 key**；而 `tests/api/test_the_spec_matches_the_server.py`
在那个 operation 上有四条断言。这次重命名碰了以上全部，加 `ui/lib/schemas.ts` 和 `pending-queue.tsx`。
判断仍然是对的，而代价是大半天，不是本页声称的「不花什么」。

---

## 2. 代码放在哪

```
src/governed_bi/feedback/          # 存储与词汇。没有模型，没有 graph。
  __init__.py                      仅 docstring（包的房屋规则）
  events.py                        封闭词汇表 + Observation / Patch / Attribution
  validate.py                      problems_with(Observation) / problems_with(Patch) -> list[str]
  lifecycle.py                     TRANSITIONS, ACTORS, is_open(), derived_state()
  store.py                         FeedbackStore —— 深模块
  attribution.py                   attribution_from_turn(entry) -> Attribution
  cluster.py                       cluster_key(), clusters()

src/governed_bi/triage/            # 流水线。import feedback, corpus, retrieve, govern, serve, eval。
  __main__.py                      唯一入口点，也是 triage 唯一读 os.environ 的地方
  state.py  graph.py  wrap.py  scope.py  tools.py  stamp.py  trial.py  records.py
  nodes/{intake,reproduce,triangulate,diagnose,author,validate,arbitrate,assemble,close}.py

src/governed_bi/corpus/patch.py    # 新增，与 store.py 并列：外科式字段编辑（§6）
src/governed_bi/api/feedback_routes.py
src/governed_bi/api/triage_routes.py   # 只读。没有任何路由能启动一次 triage 运行（§10）

tools/export_bundle.py             # patch -> bundle
tools/check_landed.py              # 语料 source_refs -> 派生落地状态；--verify 重新核对
tools/drain_raised.py              # ServeState.raised -> 存储，并报告还剩多少
tools/check_proposal_fields_are_consumed.py
```

没有 `api/triage_app.py`，也没有 `ask_sme`/`refute` 节点：流水线不是一个被服务的 graph（§10），
而 Adversary 被裁掉了（ADR 0015 §5）。

### Import 分层

`tools/check_imports.py::LAYERS` 必须穷举 `src/governed_bi` 下的每个包 —— `undeclared()` 在它没穷举时
让整次运行失败，而一个被列表漏掉的包**完全没有**约束。插两处：

```python
LAYERS = (
    ("paths",), ("credentials",), ("ports",), ("register",), ("measure",),
    ("corpus",),
    ("feedback",),        # <- 新增：需要 register + corpus，不需要更上层
    ("retrieve",), ("govern",), ("datasource",), ("model",), ("serve",), ("eval",),
    ("triage",),          # <- 新增：需要 serve（reproduce）+ eval（replay）+ feedback
    ("api",),
)
```

`feedback` 紧接在 `corpus` 之后，理由值得写出来：它必须用**与加载器相同的校验器**
（`corpus/validate.py::problems_with`、`corpus/parse.py::from_mapping`）来判断一个补丁，而不是规则的
第二份拷贝。它不得 import `serve`、`govern`、`api` 或 `eval` —— 特别是不得 import
`api/visibility.py`；grant 收窄在 `api/` 里组装，因为 session 住在那儿。

`feedback` **不是** `STDLIB_ONLY`（它经由 `corpus` 触到 `yaml`）。`sqlite3` 是标准库，所以 `store.py`
不引入任何依赖。

---

## 3. observation 词汇表

封闭。挑选标准是让读者**不必懂 schema** 就能选一个。`kind` 是已有的两值 wire 字段；`category` 是新增的
可选细化。

已交付答案（`kind: "wrong_answer"`）上：

| 分析师读到的 | `category` | 典型的语料后果 |
|---|---|---|
| 这个数字不对 | `wrong_value` | **编辑** `metric.expression`；**编辑** `metric.summary`/`body`；有时**新建** `term` |
| 它用了错的数据 —— 错的表、错的过滤、错的日期 | `wrong_scope` | **编辑** `table.rules` / `join.on`；**新建** `join` |
| 它把记录数错了或拼错了 | `wrong_rows` | **编辑** `join.cardinality` 或 `join.on`；**编辑** `table.grain` |
| 它回答的不是我问的那个问题 | `misread_question` | 通常**都不是** —— 一个生成缺陷 |
| 我问题里的某个词在这里是别的意思 | `term_mismatch` | **编辑** `term.summary`（别名必须在 `summary` 里，ADR 0005 I1）；**新建** `term` |
| 我看不出这到底对不对 | `unverifiable` | 分诊前未知 |

拒答、`no_sql`、或一次被放弃的澄清（`kind: "from_refusal"`）上：

| 分析师读到的 | `category` | 典型的语料后果 |
|---|---|---|
| 这个数据确实存在 —— 它应该能回答 | `false_refusal` | **新建** `join` / `term` / `schema.rules`；或**都不是**（检索缺陷） |
| 它反问我的那个问题不成立 | `bad_clarification` | **都不是** —— 一个 prompt 或策略问题 |
| 它说不了是对的，但它该说明为什么 | `unclear_refusal` | **都不是** |

仅操作员可提交的，靠 `source` 而不是靠 `kind` 区分：

| `category` | `source` | 备注 |
|---|---|---|
| `column_suspect` | `operator` 或 `agent` | `Reliability.status` 可由 AI 撰写，所以 agent 也能提交 |
| `column_excluded` | 仅 `operator` | `Governance.excluded` 是 human-only。存储拒绝来自任何其他 `source` 的这个 `category` |
| `reusable_fact` | `operator` | 操作员对一次澄清的回答，被提升（§9） |

**`source` 是与 `category` 分开的列**，因为同一个 observation 来自三个人群（`reader`、`operator`、
`agent`），而队列对它们的排序不同。把它折进去会用十二个值回答九个问题。

### 哪些情况是新 asset、是编辑、还是都不是

这个区分重要，因为「都不是」是九个 category 里三个的众数结果，而一条**得不出「没有东西可打补丁」结论
的流水线一定会去打补丁**。

| 投诉 | 新建 / 编辑 / 都不是 |
|---|---|
| SQL 对，业务术语的定义错 | **编辑** `term` 或 `metric` |
| 定义对，join 粒度错 | **编辑** `join.cardinality`，或**新建** `join` |
| 本该成功的拒答，gold 表从未被许可 | **新建** `term`/`join` 让它可被检索到 —— 或者**都不是**，如果那张表其实被许可了而层栈是因为别的理由拒绝的 |
| 本该成功的拒答，`r_star_projection` | **都不是** —— 一个引擎缺陷 |
| 缺一个同义词 | **编辑**已有 `term` 的 `summary`，不是新建 asset。别名住在 `summary` 里，因为那才是检索通道（见 M3，ADR 0015） |
| metric expression 写错了 | **编辑**。而且这是唯一一个有**免费**验证器的类别：478 个 expression 里 28 个解析不了、23 个在任何地方都解析不到（**实测**） |
| 一个该标 `suspect` 的列 | **编辑** `column.reliability` |
| 一个该被 `excluded` 的列 | **从这个环的视角看是「都不是」** —— 它发出一条请求，由人手动编辑 |
| 一次澄清的回答其实是一个可复用事实 | **新建** `term` 或 `few_shot` —— 或者**都不是**，如果它只是一次性的过滤条件 |

---

## 4. 存储

### Schema

```sql
-- feedback/store.py::_SCHEMA。由 _migrate() 在一个事务里施加。
PRAGMA journal_mode = WAL;          -- 一写多读；操作员队列是读者

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  observation_id   TEXT PRIMARY KEY,
  filed_at         TEXT NOT NULL,          -- ISO-8601 UTC，秒
  source           TEXT NOT NULL,          -- reader | operator | agent
  kind             TEXT NOT NULL,          -- from_refusal | wrong_answer
  category         TEXT,                   -- §3，可空：第一次点击可能就是全部了
  note             TEXT NOT NULL DEFAULT '',   -- <= 4000 字符，已 strip
  expected         TEXT NOT NULL DEFAULT '',   -- <= 200 字符。价值最高的可选字段
  state            TEXT NOT NULL,          -- open | triaged | declined | duplicate | addressed
  decline_reason   TEXT,                   -- state = declined 时必填；§5
  duplicate_of     TEXT REFERENCES observation(observation_id),
  triaged_at       TEXT,
  -- 归属信息，是「拷贝」不是「join」（见下）
  turn_id          TEXT NOT NULL,
  thread_id        TEXT NOT NULL,
  run_id           TEXT,
  question         TEXT NOT NULL,
  outcome          TEXT,
  terminal_reason  TEXT,
  refused_by       TEXT,
  generated_sql    TEXT,
  licensed_json    TEXT NOT NULL DEFAULT '[]',
  rendered_json    TEXT NOT NULL DEFAULT '[]',   -- 需要那个新的 register 字段 `rendered_asset_ids`，§15.5
  schema_ranking_json TEXT NOT NULL DEFAULT '[]',
  corpus_content_hash TEXT,
  prompt_set_hash  TEXT,
  git_sha          TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_state  ON observation(state, filed_at);
CREATE INDEX IF NOT EXISTS ix_obs_turn   ON observation(turn_id);
CREATE INDEX IF NOT EXISTS ix_obs_category ON observation(category, state);

CREATE TABLE IF NOT EXISTS patch (
  patch_id         TEXT PRIMARY KEY,
  created_at       TEXT NOT NULL,
  author           TEXT NOT NULL,          -- operator | agent
  intent           TEXT NOT NULL,          -- new_asset | edit_asset | exclusion_request
                                           -- | shared_request | engine_defect | no_change
  state            TEXT NOT NULL,          -- draft | exported | withdrawn
  triage_run_id    TEXT,
  rationale        TEXT NOT NULL DEFAULT '',
  -- 改什么
  asset_type       TEXT,
  namespace        TEXT NOT NULL,
  asset_id         TEXT,                   -- new_asset 在 id 派生出来之前为 null
  field_path       TEXT,                   -- 例如 "summary"、"reliability.status"、"binding.target_id"
  was              TEXT,                   -- 起草时从活体语料读出
  becomes          TEXT,
  asset_yaml       TEXT,                   -- 整个文档，仅 new_asset
  -- 它是对着什么被验证的
  base_corpus_content_hash     TEXT NOT NULL,
  expected_corpus_content_hash TEXT,       -- bundle 构建之前为 null
  ladder_json      TEXT NOT NULL DEFAULT '{}'   -- tier -> GateResult
);
CREATE INDEX IF NOT EXISTS ix_patch_state ON patch(state, created_at);

CREATE TABLE IF NOT EXISTS observation_patch (
  observation_id TEXT NOT NULL REFERENCES observation(observation_id),
  patch_id       TEXT NOT NULL REFERENCES patch(patch_id),
  PRIMARY KEY (observation_id, patch_id)
);

CREATE TABLE IF NOT EXISTS transition (       -- 只追加。审计轨迹。
  rowid_           INTEGER PRIMARY KEY AUTOINCREMENT,
  at               TEXT NOT NULL,
  entity           TEXT NOT NULL,          -- observation | patch
  entity_id        TEXT NOT NULL,
  from_state       TEXT NOT NULL,
  to_state         TEXT NOT NULL,
  moved_by         TEXT NOT NULL,          -- 行动者，永不为空。§5
  detail           TEXT NOT NULL DEFAULT ''
);
```

**归属信息是拷贝的，不是 join 的。** turn 自己的记录是那个自然外键，而它是错的那个：
`MAX_TURNS_RETAINED = 25` 会把较旧的记录从 `ServeState.turns` 上省略掉，而 thread 索引是一个 pickle，
它的加载器在裸 `Exception` 上会删掉这个文件（`serve/checkpointer.py`）。一个 join 到会删行的存储里，
就是一个半年后什么都返回不了的 join —— 而半年后恰恰是 steward 想读这个队列的时候。拷贝的成本是每条
observation 约 2 KB，换来这一行自我描述。

### 接口

```python
# src/governed_bi/feedback/store.py
class FeedbackStore:
    """Observation、patch，以及它们状态之间的转移。

    刻意同步。`serve/checkpointer.py` 记录的每一个事件循环绑定陷阱，都来自存储与 graph 共用一个
    循环；这一个由同步 FastAPI handler 和 `tools/` 读写，从不碰那个循环。
    """

    def __init__(self, path: Path | str) -> None: ...          # _migrate() 在这里跑

    # 写
    def file(self, obs: Observation) -> str: ...               # -> observation_id
    def transition(self, entity: str, entity_id: str, *, to: str,
                   moved_by: str, detail: str = "",
                   decline_reason: str | None = None) -> None: ...
    def draft(self, patch: Patch, *, observations: Sequence[str]) -> str: ...
    def record_ladder(self, patch_id: str, tier: str, result: Mapping[str, Any]) -> None: ...

    # 读
    def get(self, observation_id: str) -> Observation | None: ...
    def queue(self, *, state: str | None = None, category: str | None = None,
              limit: int = 50, offset: int = 0) -> Page[Observation]: ...
    def patches_of(self, observation_id: str) -> list[Patch]: ...
    def observations_of(self, patch_id: str) -> list[Observation]: ...
    def history(self, entity_id: str) -> list[dict[str, Any]]: ...

    # 维护
    def sweep(self, *, older_than_days: int, dry_run: bool = True) -> SweepReport: ...
```

`sweep` 删除早于截止点的终态行，并对非终态行**只报告、不触碰** —— 后半句才是重要的那半，因为
「90 天里没人分诊过这一条」是操作员需要知道的事实，而一次删除会把它藏起来。

`serve/checkpointer.py` 的 `assert_not_a_warehouse` 原样复用在这个路径值上，理由与它在那里存在的理由相同。

### Knob

```
GOVERNED_BI_FEEDBACK_DB      默认 runs/feedback.sqlite，相对 REPO_ROOT 解析
GOVERNED_BI_FEEDBACK_ADMIN   未设置 -> 四个工程师动词根本不挂载
GOVERNED_BI_PROPOSAL_DIR     默认 .governed_bi/proposals
GOVERNED_BI_TRIAL_SCRATCH    未设置 -> 试验模式关闭，T4 拒绝运行
```

**这些一个都不许变成 `register/knobs.py` 的 knob。** `serve/session.py::_resolved_knobs` 把每一个已声明
knob 放到每一行 serve 记录上，而 `measure/gates.py::_knobs_resolved_gate` 会比较它们，所以在那里声明
一个，就为一个没有任何 turn 消费的值移动了每一个 arm 的配置哈希 —— 构造上就是 `expand_hops` 缺陷。
由 `tests/feedback/test_no_comparability_knob_names_the_feedback_store.py` 钉住。

---

## 5. 生命周期，以及每个状态的行动者

**规则：一个状态只有在存在具名行动者推动它时才存储，其余全部在读取时派生。** 一个没有行动者的存储态，
就是今天那个关不掉的 `open: true`。

### 存储 —— observation

| from → to | 行动者 | 前置条件 |
|---|---|---|
| — → `open` | `reader` \| `operator` \| `agent` | 该 turn 存在且已结束 |
| `open` → `triaged` | `steward` | — |
| `triaged` → `declined` | `steward` | `decline_reason` 已设置 |
| `triaged` → `duplicate` | `steward` | `duplicate_of` 指向一个 open 或 addressed 的 observation，**并且这条 observation 加入那一条的 patch 集合** —— 否则落地时受影响的 observation 会算成一条而不是两条 |
| `triaged` → `addressed` | `steward` | 至少有 1 个处于 `draft` 或 `exported` 的 patch |
| `triaged` → `blocked_on_a_person` | `steward` | 一行 `blocked_note` 已填。**不是路由动作** —— 没有可以升级给的人，所以这是一个有名字的状态而不是一个指派人。它的文案说明没有任何东西在推进它 |
| `blocked_on_a_person` → `triaged` \| `declined` \| `addressed` | `steward` | 阻塞解除 |
| `declined` → 任何状态 | **拒绝。** 重新打开是一条**新的** observation，因为原件的证据包挂在产生它的那个 turn 上 | |

### 存储 —— patch

`draft → exported →`（从存储视角看是终态）以及 `draft|exported → withdrawn`。
`exported` 的行动者是 `engineer`，`withdrawn` 的是 `steward`。

### 派生 —— 每次读取重新计算，从不存储

```python
# src/governed_bi/feedback/lifecycle.py
def derived_state(patch: Patch, *, loaded_corpus_hash: str,
                  asset_text_now: Mapping[str, tuple[str, str]]) -> str:
    """handed_off | landed_verified | landed_matched | superseded 之一。

    `asset_text_now` 把 asset_id 映射到 (summary, body)，取自 session 加载的那个语料。
    什么都不存：语料一变答案就变，而一份存下来的拷贝会成为「这个到底落地了没有」的第二个答案，
    并且可以与第一个不一致。
    """
```

| 状态 | 条件 | 分析师读到的 |
|---|---|---|
| `handed_off` | `loaded == patch.base` | 已交给一位工程师去提交。它还不在引擎里，而这里没人能说什么时候会在。 |
| `landed_verified` | `loaded == patch.expected` | 这个变更已在本服务器运行的语料里。再问一遍你的问题 —— 答案现在可能不一样了。 |
| `retrieval_verified` | `landed_*`，**并且**该 observation 的 T3 coverage fixture 重跑通过 | 回答这个问题所需的表现在可以取到了。这与答案正确不是一回事 —— 再问一遍看看。 |
| `landed_matched` | 哈希不同，但触碰过的每个 asset 都在，且其 `summary`/`body` 与 bundle 的 post-state 一致 | 这个变更已在本服务器运行的语料里，和同期落地的其他变更一起。再问一遍你的问题。 |
| `superseded` | 哈希已离开 base，而内容不在那里 | 语料变了，而这个变更不在里面 —— 它在路上被丢掉或被改写了。它回到评审者手里了。 |

`landed_matched` 是常见的真实情形：一周内落地两个 bundle，于是一个**确实**上线了的变更在精确哈希匹配上
失败。`superseded` 覆盖一次 `git apply` 冲突、一次语料 CI 重排、以及评审者在提交前改了补丁 —— 三者
都正常。

**注意这些文案从不说什么。** 它从不说这个问题现在能被正确回答了。落地确立的是语料变了，仅此而已；
配置相同的两次运行有 12.7% 的问题会翻转，所以即便一次重跑通过了也确立不了这件事。「再问一遍」是一句
邀请。

而存储态叫 `addressed`，绝不叫 `resolved`。在每一张 gold 表**都**被许可的那些 turn 上，引擎的实测准确率
是 **0.7555**，所以凭一次落地 commit 就关闭的投诉里，大约**每四个有一个**仍然是错的。
`retrieval_verified` 是免费阶梯唯一许可的升级，而它只说明那些表可以取到了。

### re-ask，以及为什么它不是可选项

```
ui/components/reports/re-ask-button.tsx     （新增，约 0.5 天）
```

每一个落地状态的文案都在叫读者再问一遍，而设计会议没有交付任何做这件事的途径。所以：
`landed_verified`、`landed_matched` 和 `retrieval_verified` 在 reports 页面上带一个 **Re-ask** 动作。
它在一个**新** thread 上打开聊天界面，预填存储早已从 turn 记录上抄下来的问题文本（§4）。

用新 thread 而不是原来那个：写进别人的 thread 正是 `api/raised_write.py` 长篇记录过「不要做」的事，
而在旧 thread 上再开一个 turn 会继承最多 `MAX_TURNS_RETAINED` 个 turn 的上下文，而那不该进入这次比较。

**它不给自己打分。** 引擎不比较新答案和旧答案，也不因为这次重问而推动任何状态。一次重问不是证据 ——
配置相同的两次运行有 12.7% 的问题会翻转 —— 它是读者在看，而这是唯一可得的判断，也正是一开始被请求的
那个判断。没有这个按钮，回流路径是一个队列；有了它，它闭合。

### 驳回词汇表，以及每一条的确切文案

理由**就是**通知。不存在一个没有句子的驳回徽章。

| `decline_reason` | 分析师读到的 |
|---|---|
| `working_as_intended` | 已复核并关闭：引擎是对的。这个答案与数据里的内容一致。 |
| `not_a_corpus_problem` | 已复核并关闭：是数据本身错了或缺失。语义层修不了这个，而这台引擎也不是它被修好的地方。 |
| `needs_a_schema_change` | 已复核并关闭：回答这个需要数仓里不存在的表或列。得有人先把它建出来。 |
| `engine_defect` | 已复核并关闭为一个引擎缺陷，不是语义层的问题。它已被记录在引擎缺陷该被记录的地方。 |
| `out_of_scope` | 已复核并关闭：这不是这台引擎该回答的问题。 |
| `cannot_reproduce` | 已复核并关闭：在现在运行的语料上再问一遍，它答对了。如果你仍然能复现，请带上新答案重新提交一次。 |
| `insufficient_detail` | 未做任何变更即关闭：这里的信息不足以行动。这台引擎不知道是谁提交了这条记录，所以没人能被追问。 |
| `wont_fix_cost` | 已复核并关闭：把这个真正修好的代价，比它当下的价值更大。它是一个真实的问题，而它不会被修。 |

最后两条才是这个项目的文案规则真正要求的东西。`insufficient_detail` 陈述的是没人跟进的**结构性**原因，
而不是暗示读者提供得不够。`wont_fix_cost` 说「我们不打算修这个」，不打折扣 —— 一个永不移动的
`deferred` 状态，和现在这个 pending 列表是同一个谎。

---

## 6. 写 YAML：`store.write` 负责创建，`corpus/patch.py` 负责编辑

**实测（M1）。** 加载一个 table asset、改 `summary`、再调 `corpus/store.py::write`，产生了
**第二个同 asset id 的文件**；`store.load` 返回 1,434 个 asset 且 **problems 为零**；随后 `build_index`
抛出 `ValueError: duplicate index id`。服务语料是 178 个文件里的 1,432 个 asset —— 一表加约 50 个内联列
每文件 —— 而 `write` 把一个 asset 放到 `<root>/<namespace>/<id>.yaml`，那不是它来源的那张表所在的位置。

而且 `write` 是整文件重排：`store.py:256` 是 `yaml.safe_dump(to_mapping(asset), sort_keys=False,
allow_unicode=True)`，没有 `width`，而 `parse.py::to_mapping` 「omits defaults」。所以在一个人工撰写的
文件上做一次往返会丢注释、把超过 80 列的字符串全部重排、丢掉任何显式写出的默认值，并把键按 dataclass
字段序重排。

```python
# src/governed_bi/corpus/patch.py  —— 新模块，与 store.py 同层
def locate(path: Path, *, asset_id: str, field_path: str) -> Span:
    """在声明了 `asset_id` 的那个文件里，某一字段值的字节区间。

    用 `yaml.compose` 的 node mark，不用正则也不重新 dump：一个内联列的 `summary` 嵌在它所属表的
    文档里两层深，只有 composer 知道位置。字段不存在、或该节点是 merge key 或 alias 时抛
    `FieldNotLocatable` —— 一个被 alias 的标量无法在一处编辑。
    """

def apply_edit(path: Path, *, asset_id: str, field_path: str,
               was: str, becomes: str) -> str:
    """就地替换一个字段的值。返回新的文件文本。

    当前值不等于 `was` 时拒绝 —— 这是并发检查，也是补丁为什么要携带 `was` 的理由。保留原标量的
    block/引号风格，因为把一个没被碰过的邻居的 `>` 改成 `"`，是一处评审者不得不读的 diff。
    """

def apply_create(root: Path, *, asset_yaml: str, namespace: str) -> Path:
    """一个新 asset。它**就是** `store.write`，只是先经过 `from_mapping`，好让落盘的文件是加载器
    接受的那种。"""
```

`patch.py` 拒绝碰三样东西，而三者都是拒绝，不是 TODO：

- **`governance`。** ADR 0015 §8。没有任何代码路径写它，而复核界面也不渲染它 —— 一个能提议排除的屏幕
  **就是**那个「其缺席即控制」的工具。
- **对一张表内联列的结构性改动**（增、删、或重排 `columns`）。列 id 是派生的
  （`corpus/identity.py::derive_column_id`），所以一次结构性编辑会静默地给下游 asset 重新编键。这是一次
  由人通读整个文件的手工编辑。
- **把一个 `column` asset 建成独立文件。** 这是红队找到的那次服务中断，值得说清。服务语料把一张表的列
  放在内联位置，而 `corpus/store.py::_split_inline_columns` 在**加载时**把它们拆成各自的 asset。于是
  一个独立 `column` 文件，若其对应列已被所属表声明，就让加载器拿到同一个 asset id 两次 ——
  `store.load` 会带着 **problems 为零** 接受它（M1），随后 `build_index` 里抛出
  `ValueError: duplicate index id`，让每一次 `Session` 构建都失败。一次完整的服务中断，发生在 commit
  之后，绕过了一个看不见它的检查器。对一个列的 `summary` 或 `reliability` 的编辑，走**那张表的**文件上
  的 `locate`/`apply_edit`；而一个新列是数仓变更，不是语料变更。

---

## 7. HTTP 界面

从 `api/routes.py::app` 与 `make_clarification_router` 并列挂载。

### 默认启用 —— 读者的两个动词

| 方法 + 路径 | body | 状态码 | 披露什么 |
|---|---|---|---|
| `POST /turns/{turn_id}/raised` | `{kind, category?, note?, expected?}` | 201；404 turn 不存在；422 `kind`/`category` 非法或超长 | 除 id 外什么都不回 |
| `PATCH /observations/{id}` | `{note?, expected?}` —— 事后补充项 | 200；404；409 若不在 `open`；422 | 无 |

路径和 `kind` 的取值都不变，所以今天的 UI 继续可用。**暂停 thread 上的那个 409 消失了**，而这是好事：
再也没有东西写 graph state，所以没有活跃 interrupt 可被消耗，于是 turn 正在暂停中的读者可以提交。

**没有限流，而本页原先说有。** 它描述了 `GOVERNED_BI_FEEDBACK_RATE`、「每个 turn 每小时 5 条」，
并得出「一个 turn 无法被用来无界增长 store」的结论。那个变量在整棵树里不存在，那条不变量也不成立：写入动词
是未认证的，所以能连上端口的调用方可以把 `runs/feedback.sqlite` 涨到磁盘满。约束单行的是 `NOTE_MAX_CHARS`
（4,000）和 `QUESTION_MAX_CHARS`（8,000）；约束条数的没有。这是一个未完成项，记在
[open work](open-work.md) 里，而不是在这里当成已完成来描述。
一个 turn 不能被用来无界地把存储撑大。

### 默认启用 —— 读

| 方法 + 路径 | 备注 |
|---|---|
| `GET /observations?state=&category=&limit=&offset=` | 最早优先。`meta.truncated` 是承重的（ADR 0009） |
| `GET /observations/{id}` | 该行加上它的 patch，以及每个 patch 的派生状态 |
| `GET /clarifications/pending` | 形状不变。它的 note 那一半现在来自一次带索引的查询，而不是一次 40 往返的 thread 遍历，而且它经过收窄接缝 —— 今天它没有 |

### 仅当 `GOVERNED_BI_FEEDBACK_ADMIN` 已设置才挂载 —— steward 的四个动词

未挂载时返回 404，不是 403：403 会确认这个路由存在。

| 方法 + 路径 | body |
|---|---|
| `POST /observations/{id}/triage` | `{to: "declined" \| "duplicate" \| "addressed", decline_reason?, duplicate_of?}` |
| `POST /patches` | 一个 `Patch` 草稿 |
| `POST /patches/{id}/withdraw` | `{reason}` |
| `GET /patches?state=` | — |

**产出 bundle 是一个 CLI，不是一个路由**（§8）。一个写出目录、随后由工程师应用的路由，就是一个让任何
碰到端口的人都能暂存一次语料变更的路由。

### 实际收窄这些载荷的是什么

**`narrow_feedback_rows` 不存在，而本节原先当它存在来描述** —— 连签名、docstring 和测试文件名都写了，
而这三样在树里一个都没有。返回路径上**没有**基于 grant 的收窄。实际交付的更粗，值得精确说明，因为
「按 grant 收窄」和「按开关收窄」差别很大。

`api/feedback_routes.py` 从三份**白名单**投影 —— `PUBLIC_OBSERVATION_FIELDS`、`PUBLIC_PATCH_FIELDS`、
`PUBLIC_TRANSITION_FIELDS` —— 只在 `GOVERNED_BI_FEEDBACK_ADMIN` 置位时放宽到全部字段，而那是与挂载
steward 动词同一次读取。对未认证调用方扣留的：

| 扣留 | 原因 |
|---|---|
| `gold_sql`、`gold_fingerprint`、`pred_fingerprint` | **留出集**基准的参考答案。V12 阻止留出问题进入 corpus；通过 HTTP 发出答案是同一条污染通道、只是绕过了闸门 |
| patch 的 `was`、`becomes`、`rationale`、`base_corpus_content_hash` | steward 的工作草稿。`GET /patches` 在同一个开关下 404，而在修好之前详情路由照样发这些内容 |
| transition 的 `detail` | steward 打的任何字。append-only 轨迹的**形状**是公开的，句子不是 |

用**白名单而不是黑名单**，因为黑名单正是缺陷的来源：投影原本枚举 dataclass，所以往 `Observation` 上加一个
字段，下一次部署它就到了未认证路由上。`gold_sql` 就是这么来的。由
`tests/api/test_the_queue_does_not_serve_the_benchmark.py` 断言 —— 任何不在名单上的字段上线都会红。

**仍然披露、且确实没变的**：`question`、`generated_sql`、`licensed`、`missing_tables`。这些是让一行可
评审的东西，而 `/audit/turns/{id}/trace` 本来就对同一个调用方披露一个 turn 的 SQL —— 这是先于本界面就
被接受的立场。按 grant 的接缝是诚实的下一步，尚未建成。

---

## 8. bundle

```
bnd-pat-…/
  MANIFEST.yaml        补丁、它的 observation、阶梯结果、两个哈希、引擎 sha
  COMMIT_MSG.txt       生成的。首行 <= 72 字符。点名 observation id，不含 prose
  changes.patch        可 `git apply -p1`，针对 base_corpus_content_hash 产生
  after/               post-state 文件全文，好让评审者读结果而不是读 diff
  evidence/
    observations.md    每位读者说了什么，原文，放在代码围栏里
    turn-<id>.json     问题、SQL、ledger、licensed、rendered、schema_ranking
    ladder.json        每一层的 GateResult，包括没跑的那些以及为什么没跑
    reproduction.md    复现器发现了什么，或者说明它没跑
```

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
uv run --frozen python tools/export_bundle.py --patch pat-… --dry-run   # 打印 diff，不写任何东西
```

`COMMIT_MSG.txt` **不携带任何读者 prose**。commit message 由带类型的字段模板化或模型化生成；读者那句话
住在 `evidence/observations.md` 的代码围栏里，那里它不会变成某个别的工具日后未转义渲染的一行 commit log。

**应用它是手动的，而文档把整条命令写出来：**

```bash
cd ../BIRD-corpus && git checkout -b return/pat-… && git apply -p1 ../governed-bi/bundles/bnd-pat-…/changes.patch
git commit -F ../governed-bi/bundles/bnd-pat-…/COMMIT_MSG.txt
```

`export_bundle.py` 上没有 `--apply` 开关，而且不会有。写入是那个人的。

**没有人授权 bundle 的应用，而这必须说出来而不是留空。** 在这个部署上，一个 principal 起草补丁、接受它、
并应用它 —— 不存在职责分离，因为没有第二个身份可供分离。唯一真实的控制是语料仓库自己的评审：一个想要
两个人在环里的分叉，靠在那个仓库的 pull request 上要求一位评审者来得到它，那在这台引擎之外，而那正是
它该在的地方。

**bundle 会过期，而有一条命令管这件事。** 在导出和提交之间语料可能移动 —— 另一个 bundle 落地了，或者
有人手改了。`apply_edit` 在当前值不等于 `was` 时拒绝，所以一个过期的补丁会在 `git apply` 处响亮地失败，
而不是静默覆盖。在尝试之前检查：

```bash
uv run --frozen python tools/check_landed.py --verify --bundle ./bundles/bnd-pat-…
```

它报告三者之一：干净可应用；base 移动了但被触碰的字段未变（重新导出即可）；或某个被触碰字段在它底下
变了（回到 steward）。没有这条命令，工程师是从一次冲突里得知这件事的，而那是个更糟的知情场合。

---

## 9. 操作员的回答如何在不恢复任何人 thread 的前提下变成一个语料事实

`ui/components/clarifications/pending-queue.tsx` 按设计是只读的：在那里回答等于恢复一个「这位操作员并非
被问方」的 thread（ADR 0006 B9）。这个约束不变。

pending 队列新增**一个链接，不是一个按钮。** 这个链接打开 steward 界面，预填一条 `reusable_fact`
observation，携带那个暂停 turn 的问题和澄清文本。文案是明说的：

> 那个暂停的对话仍然保持暂停，而提问的人不会收到回复。你在这里写下的东西会变成对语义层的一个提议变更，
> 好让下一个问同样问题的人不必再被反问一次。

没有任何东西调用 `command.update`。没有任何东西调用 `POST /threads/{id}/state`。那个暂停的 thread
被读取，从不被写入。

---

## 10. triage 流水线

**不注册进 `langgraph.json`。** 它是一个由本地入口点编译并调起的 `StateGraph`：

```bash
uv run --frozen python -m governed_bi.triage --cluster cls-… --stop-after diagnose
```

**为什么不做成被服务的 graph。** `api/auth.py::_no_state_writes_on_a_new_run` 只拒绝 `command.update`
和 `command.goto`；一个 `{"assistant_id": "triage", "input": …}` 形状的载荷根本不带 `command`，于是
`_command_of` 返回 `None`，钩子一声不吭地返回。平台本来就允许匿名调用方在 `serve` 上花预算 ——
`api/routes.py` 原话如此 —— 但注册 `triage` 会把每次请求的上限从一个 turn（约 45k token）抬到一次只由
操作员设定的 cap 兜底的 fan-out（默认约 290k），而且是在那个还会写文件的 graph 上。`api/` 里任何地方都
没有限流器。一个本地入口点不花任何代价。

**所以没有 `interrupt()`，也没有 HITL 暂停。** 当 Diagnoser 无法定夺一个语义问题时，这次运行**结束**，
并向存储写入一条 `category: needs_sme` 的 observation；由 steward 在复核界面上回答，那个动作起草补丁。
这从设计里删掉了 `serve/resume.py::authorise_resume`，连带删掉了它解决不了的那个问题：在单一 principal 下，
这道闸门比较的是批次**发起者**和恢复者，而不是投诉的那位读者，所以它谁也区分不了。

节点：

```
START -> intake
intake --(Send x K)--> reproduce_one -> triangulate
intake --(没有可复现的)--> triangulate
triangulate --> {diagnose, close}
diagnose    --> {author, close}                # locus 为 no_asset_* 或 needs_sme 时走 close
author      --> validate                       # 阶梯 T0-T2
validate    --> {refute, arbitrate}
refute      --> arbitrate                      # 阶梯 T3（启用时含 T4）
arbitrate   --> {author, assemble, withdraw}   # 有界：revision < max_revisions
assemble    --> close
withdraw    --> close
close       --> END
```

Reducer：`reproductions`、`critiques`、`usage`、`sme_answers` 上用 `operator.add`。`diagnosis` 和
`patch` 上**不用 reducer** —— 修订循环覆写它们，而在那里用 `operator.add` 会让「那个补丁」变成一个
list，而每一个下游节点都得记得取最后一个元素。

`arbitrate` 返回 `Command[Literal["author", "assemble", "withdraw"]]`，而且它出边上**不能**有
`add_edge`，只能有 `add_conditional_edges` —— 否则每个目标都会跑。

### 按角色划分的工具面

读：`read_asset`（去掉 `audit`、去掉 `governance`）、`list_assets`、`retrieval_trace`、`sample_column`、
`probe_query`、`read_diagnosis`。**没有任何工具重放问题** —— 试验语料属于阶梯（§11 T4），不属于 agent。
写：**一个**，`stage_asset`，外加 `stage_exclusion_request`、`stage_shared_request`、`withdraw_staged`。

`stage_asset` 按这个顺序做六件事，而顺序本身就是控制：

1. 经 `corpus/store.py::_loader_class()` 做 `yaml.load` —— 同一个加载器，所以 YAML 1.1 的 `on:` 别名
   和 utf-8-sig 的行为与生产一致。
2. `triage/stamp.py::restamp_model_authored` **丢掉** `governance`，**覆写** `provenance` 为
   `source: curator, status: proposed`。
3. `corpus/parse.py::from_mapping`，然后 `corpus/validate.py::problems_with`。problems 作为
   **模型可以据此行动的工具回复**返回，不抛异常 —— 好让它能自己修 summary 长度。
4. `corpus/identity.py::validate_asset_id` 和 `validate_path_component(namespace)`。
5. 写入 `<proposal dir>/<id>/assets/<namespace>/<id>.yaml`。
6. 把暂存行记到 state 上。

**asset id 是派生的，绝不从模型那里取**（ADR 0008 §1.2）。模型提供的 `id` 是一个 problem，不是一个覆盖值。

### `audit` 层从不到达模型

`corpus/schema.py::Audit`：「Never enters the analyst context.」一个 triage agent 不是分析师，但把这条
规则外推是便宜的选择，而日后拿着证据推翻它是某人可以做的决定。`governance` 被扣住的理由不同：一个能读到
它的 Author 就能对它做模式匹配，而这里的边界是它不能**写**一个；让它看到形状，是教它伪造的前半步。

### Prompt

在 `register/prompts.py` 里，放在一个**第二注册表**中，带自己的摘要：

```python
TRIAGE_PROMPT_REGISTRY: Mapping[str, Prompt] = {...}
def triage_prompt_set_hash(overrides=None) -> str: ...

def _assert_the_two_registries_partition_this_module() -> None:
    """模块作用域里每一个 `Prompt` 恰好在一个注册表里。

    `prompt_set_hash` 是 serve arm 的处理身份，而它完整摘要 PROMPT_REGISTRY，所以一个 triage
    prompt 放进去，会在一次不改变任何 serve 行为的编辑上移动每个 serve arm 的身份。两个摘要是
    解法；代价是一个 prompt 现在可能**两边都不在**，那是一个没有任何哈希覆盖的 prompt ——
    比原问题严格更糟。所以有这条断言。
    """
```

### 试验语料

`triage/trial.py::corpus_under_trial(...)` —— **T4 的**设施，也是暂存 prose 唯一被渲染进真实 prompt 的
地方。它原本被设计成一个 Adversary 的 agent 工具，而那个角色现在被裁掉了（ADR 0015 §5）；把它移进阶梯
严格更好，因为一个重放固定题集的确定性驱动，其可审计性是一个自己挑选重放对象的模型所不具备的。这也是
`corpus/snapshot.py` 终于拿到调用者的原因。

- `mode="off"` —— `GOVERNED_BI_TRIAL_SCRATCH` 未设置时的默认。T4 拒绝运行。fail closed，因为一次静默
  改动活体语料的试验，是这个包里可能出现的最昂贵的失败。
- `mode="copy"` —— `corpus/snapshot.py::snapshot(corpus_root, scratch)`，然后把暂存树盖到拷贝上，然后
  在拷贝上建一个 `Session`。`corpus_root` 从不被碰。
- `mode="in_place"` —— 需显式开启，由一个独占的 `<corpus_root>.trial.lock` 守护，而这个锁
  **拒绝而不等待**（持锁者可能是一次 1,351 题的 arm），并且总是 `restore`，附带一个
  `not drifted(corpus_root, expected)` 的后置条件。后置条件失败是一次 crash，不是一个警告。

> **在写这个调用者之前先修 `snapshot`。** `corpus/snapshot.py:83` 是
> `if dest.exists(): shutil.rmtree(dest)`，只由 `_refuse_nesting` 守护；而 `_identify_corpus` ——
> 那个「这到底是不是一个语料」检查 —— 只守 `restore`。**实测：** 指向一个放着无关文件的临时目录，
> 那些文件被删掉了。红队给出的实例：在今天的 `.env` 路径下，
> `GOVERNED_BI_TRIAL_SCRATCH=C:\Users\zhang\Code\governed-bi` 能通过对
> `../MS Fabric Facilities/corpus` 的嵌套检查，并删掉工作树。
>
> 三处修复，全部必需：`dest` 存在时 `snapshot` 对它施加 `_identify_corpus`，于是它只会替换一个本来
> 就是语料的东西；`corpus_under_trial` 要求 scratch 路径**不存在、或是一个可识别的语料**，否则拒绝；
> 以及 scratch 路径组合为 `<GOVERNED_BI_TRIAL_SCRATCH>/<run id>`，其中 run id 由进程铸造，于是没有
> 任何 caller 提供的字符串会到达 `rmtree`。

### 成本

估算，来自 cap 结构而不是来自一次度量，而且整张表对第一行高度敏感。一个交付的 context block 由一致性
规则 V16 限制在 20,000 渲染字符，所以每次 agent 调用约 5k token。

| | 模型调用 | token（估算） |
|---|---:|---:|
| 一个 serve turn（基本单位） | 约 8 | **约 45k** |
| `reproduce_one` × 3 | 3 个 serve turn | 135k |
| `diagnose` | 约 7 | 27k |
| `author` | 约 5 | 18k |
| **默认 cluster** | 约 12 + 3 个 serve turn | **约 180k** |

**账单的大头是那几个 serve turn。** 这个设计里所有便宜的东西，之所以便宜，都是因为它们不跑引擎。

一个 cluster 里多一条 observation 的边际成本：**+45k**，直到 reproduce cap，之后为**零**。这就是批处理
论证的量化形式。

应该发布的便宜路径，按顺序：

1. `stop_after="diagnose"`、`reproduce_mode="from_record"` —— **约 30k，十分之一。** 产出是一个已定位
   的发现，完全不写 YAML。**先发这个。**
2. `reproduce_cap=1` —— 约 80k。补丁记录 `assurance: unrefuted`，而 steward 会读到那个词。
3. `reproduce_mode="from_record"` 但开启撰写 —— 约 45k。记录里写 `reproduced: null`，所以没人会以为
   投诉被重新核对过。

`reproduce_workers = 1`（串行）为默认。LangGraph 会并发跑一个 super-step 的每一个 `Send`，且不提供
按 fan-out 的并发上限，所以串行化的做法是一次只 fan out N 个再重入路由；N=1 时那就是一条链。项目经验：
一个策展人规模的 turn 约占本地 TPM 配额的 60%，付费工作在服务器上跑。

---

## 11. 验证阶梯

每一层都是**增量闸门**。服务语料本身就产生 361 个 `build_structure` problem（**实测**），所以一个
「零 problem」的闸门会拒掉生产、会被豁免，而豁免正是一个真实发现变绿的方式。

| 层 | 命令 | 成本（标注处为**实测**） | 通过条件 |
|---|---|---|---|
| **T0** | 对暂存树跑 `tools/check_corpus_conformance.py` | 约 1.6 秒 | 文件能 parse、`from_mapping` 接受它、`problems_with` 为空、id 通过校验 |
| **T1** | 全树一致性检查 + `build_structure` + `build_index` + `tools/govern_bench.py` | 3.4 秒（facilities）/ 26 秒（BIRD）**实测**；索引词法 0.03 秒、暖态语义 0.27 秒**实测**；govern_bench 1.7 秒**实测** | 按规则 id 没有**新增**发现；`build_index` 不抛异常；`build_structure` 的 problem 计数不上升 |
| **T2** | `tools/check_closed_domains.py` + metric expression 解析器，对活体 catalog | 数秒，需要数据库，无模型 | metric `expression` 里每一个裸标识符都能在 `base_table` 上、或经一条已声明 join 解析到 |
| **T3** | `tools/routing_recall.py --baseline`，成对，agent 模型关闭 | 数分钟，**约 $0** —— 向量缓存 100% 暖，且加一个 asset 只花 **2** 次 embed 调用**实测** | **逐题，不按比率**：没有任何一道题丢失 gold 表 coverage。并报告哪些题变好了。**不适用于只改 `body` 的补丁** —— 见下 |
| **T4** | 对该 cluster 的问题做定向重放 | 数十次付费调用 | 那个具体机制变了 —— 见下 |
| **T5** | 一对成对 arm | 在 `workers=10` 下约 52 分钟挂钟、约 74M 输入 token，**实测自 `runs/eval/driver_v4.log`** | 一个 **release** 闸门。绝不是一个补丁闸门 |

**`tools/govern_bench.py` 在 T1 里，但它不是一个补丁闸门。** 它跑的是 `govern/adversarial.toml` 自己
声明的虚构世界（`open-work.md` §3.11 就这么说，而一份原型确认了在语料打补丁前后它的输出逐字节相同）。
它在那里是为了抓住同一个 commit 里搭车的**代码**变更，而设计把这一点说出来，而不是让某人以为这个套件
在盯着语料。

### 按 category 的读数

除 T5 之外，这个清单上没有 EX。`docs/open-work.md` §3.12 给了理由：MDE 约 2.3pp，而 §1.5 最大的单个
coverage 桶是 7 道题 —— 0.52pp。

| category | 主读数 | 层 | 分辨率 |
|---|---|---|---|
| `false_refusal` | 该 turn 的 `terminal_reason` 不再是 `r_table_not_licensed`，且 coverage 变为真 | T3 | 一道题 |
| `wrong_scope`（coverage） | 逐题的 `all_gold_tables_licensed`；`pulled_in.n_connect` | T3 | 一道题 |
| 许可集合内选错了表 | `licensed` 集合 diff + `schema_ranking` 的 gold 排名 | T3 报告，T4 确认答案翻转 | 精确 |
| `wrong_value`（定义） | metric 解析器通过，随后 T4 的 `generated_sql` 绑定到预期列 | T2 + T4 | 精确 |
| 答案形状（projection、DISTINCT） | `BINDING/r_star_projection` 的 turn 命中计数，在指标上做 McNemar | T5 | 约 1.1pp |
| `bad_clarification` | `outcome == clarification` 与 `licensed == ∅` 的计数 | T4 | 逐题 |
| 一个良性语句被拒 | 对抗套件的良性一半 | T1 | 零噪声 |
| 进入 prompt 的 prose | 新增的内容规则 | T0/T1 | 精确 |

那张表里每一个零都经由 `measure/stats.py::rule_of_three` 报告，于是 `0/53` 渲染成「≤ 5.7%」，无法被
引用成「0% 误拒」。那个函数已经存在。

### T4/T5 的读数：机制选出分层，分层上的 EX 才是判决

这是批判轮之后存活下来的更正，而推理比配方更重要，因为前两次尝试都错了。

**尝试 1 —— 在整个 arm 上读 EX。** 在 v3_fold → v4 这一对上、同样 1,351 题，EX 是 +1.18pp，126 个
不一致，对上 MDE 2.33pp。不决定性，而且它精确复现 `open-work.md` §3.1 —— 这一点正是下面一切的许可证。

**尝试 2 —— 退役 EX，改读一个机制指标**，理由是 `BINDING/r_star_projection` 走 −1.94pp、29 个不一致
对、MDE 1.12pp，所以更稀有的事件在同样的 n 上分辨得更好。**撤回：这是单位错误。** MDE 以全体人群的
百分点计量，而两个读数的基线率差两个数量级。那个指标的**最大可能**效应是 2.15pp，所以它在饱和之前只有
**1.92 个可分辨档位**，而 EX 有 28.5 个。`COLUMNS/r_column_not_allowed` 是 1.16 倍 —— 已经饱和 ——
而那张表的初稿把它标成了 decisive。

**站得住的是尝试 3。** 限制到两个 arm 中任一命中该机制的 30 个 turn 上，**EX 走 +23.33pp，9 个不一致
对，精确 McNemar p = 0.0391。** 显著。初稿判它「not decisive」是因为 23.33pp 低于该分层自己的事后 MDE
28.02pp —— 而事后 MDE 不是显著性阈值；`measure/stats.py::mde` 自己的 docstring 就这么说。

所以流程是：**用机制计数选出补丁可能触碰的人群，再在那个人群上用精确 McNemar 读 EX。** 两个仪器，
两个职责。

三条限制，都是承重的：

1. **机制计数没有实测 null。** `run1`/`run2` —— 那个指定的重复实验 —— **ledger 行数为零**，所以磁盘上
   没有任何东西说明一个指标在配置相同的两次运行之间会动多少，因而分层是在看过 arm 之后选的。用当前
   harness 重跑一次 `run1` 的配置就能修好这一点，而且这是整份设计里最便宜的高价值实验。
2. **`mechanism_indicator` 在 ledger 为空时必须返回 `None`，不是 `False`。** 那张表的初稿是按
   `False`-on-empty 算的，而 1,351 对里有 12 对至少一侧 `attempts` 为空。在规定的约定下 `mcnemar` 会
   正确地报告未度量；限制到 1,339 个双侧对之后效应是 −1.94pp、p 值不变。**缺陷在数字的来源，不在数字
   本身** —— 而这正是为什么这个约定要写在代码里、带一个已声明的变异，而不是留在习惯里。
3. **有一个数字被禁。** `BINDING/r_star_projection` 的 MDE **1.12pp** 不得被引用：它由该对自己的
   不一致率事后算出、没有 null 可对照、而且只比它自己能表达的最大效应小 1.9 倍。它看起来像仪器精度，
   实际上是一把只有两格量程的尺子。

### 新增一致性规则

id 沿用 `tools/check_corpus_conformance.py` 的 `RULES` 表。六条里有四条**今天就有实测的活跃对象**，
这一点把它们与凭直觉写下的规则区分开。

| 规则 | 谓词 | 活跃发现 |
|---|---|---|
| **V17a** | 一个 metric `expression` 在引擎的 dialect 下能解析为 SQL | BIRD 上 **478 个里 28 个**：`DIVIDE(…)`、`COUNT(x WHERE y)`、`<condition>` |
| **V17b** | metric `expression` 里每一个裸标识符都能在 `base_table` 上解析到，或在经一条**已声明** join 可达的表上解析到 | **23 个 metric / 28 个列引用**；10 个只能经 join 可达，18 个在任何地方都不可达 |
| **V18** | 一个封闭域断言（"one of"、"always"、"only"）在 `audit.evidence` 里携带一条观察 | 未测 |
| **V19** | 任何模型可见的 **`body`** 不许点名一个 `governance.excluded` 的列或 asset。**是 `body`，不是 `summary`** —— `summary` 从不进入 prompt（`serve/context.py`），它进的是检索索引 | **零**，因为两个语料里被排除的 asset 数都是零。加上它是免费的；不可能造成回退 |
| **V21** | 模型可见文本通过 `govern/guard.py::GUARD_RULES` —— 复用它们，不是把它们重述一遍 | **一条**：`public_review_platform/few-shots/fs_public_review_platform_0012.yaml` 携带两个 `U+200B` |
| **V23** | asset id 在全树唯一 | **今天为零**，而这条规则存在的理由是：一个重复 id 能通过一致性检查，然后在 `build_index` 里抛 `ValueError: duplicate index id`（**实测**） |

**V10 和 V12 不是披露规则，不得被当作既有控制来引用。** V10 是「no text discloses how an unreliable
column was made」—— 它为 BIRD 的混淆诱饵而存在 —— 而 V12 管 held-out 问题泄漏。两条都在管基准完整性。
在一个生产语料上它们什么也不管，所以 V19 是这一类里的**第一条**控制，不是对既有控制的加固。

**棘轮。** 既有发现在语料仓库里**按名字**钉住。这个集合可以自由缩小、不可增长，而关闭其中一条会让构建
像新增一条那样响亮地失败 —— 是名字而不是计数，因为 28 条发现和 28 条**不同的**发现是同一个整数。

### 可比性

两个阻碍，都是**实测**：

1. `comparability_keys()` 是 50 个名字，**没有一个含 "corpus"**，所以一个处理变量就是语料的 arm 无法
   声明它，而 `register/arm_profiles.py` 会把它判为 `cannot_evaluate`。
2. `corpus_content_hash('../BIRD-corpus')` 在 HEAD 上是 `6e5c7b4be83d5682…`；`arms.toml` 在四个 arm 上
   都声明 `86ed1dbf…`。中间那两个 commit 只加了 `LICENSE` 和 `README.md` —— 没有任何 asset 变化 ——
   摘要还是动了。**所以今天用 `--arm v4` 跑当前 checkout 会被拒绝。**

所以：一个新的可比性 knob `corpus_release`，命名一个 **tag** 而不是一个目录。补丁持续落地；arm 钉住
release。再加 `ArmProfile` 上的 `hypothesised_effect` 和 `readout`，它们终于给
`eval/power.py::require_power` 提供了 `open-work.md` §3.10 记录它缺失的那个调用者 —— 到那时，一个探测
不到自己假设的 arm 会在花掉任何东西之前失败。

**但不要围绕一对成对 arm 来规划 release。** 约束节奏的是可探测效应的存量，而它快见底了。T3 能看见的
全部就是 coverage 欠账 —— 79 道 gold 表从未被许可的题 —— 最多值 +5.85pp，按实测 EX 折算 +3.98pp，对上
EX 的 MDE 2.33pp：**整个欠账里只有 1.7 个可探测的 release。** 而且每个 release 需要**两条**新 arm，
不是一条，因为磁盘上没有任何一对能通过 `knobs_comparable`（上面第 1 条阻碍就是原因），所以第一个 release
得自己买一条控制组：约 150M 输入 token、约 104 分钟。

因此 **release 的头条读数是 T3 的逐题 coverage delta** —— 分辨率一道题（0.08pp），成本约 $0 —— 而一对
成对 arm 是**代码**变更需要定价时才买的东西。一条 release arm 本会花掉的 token，更该花在上面那个读数
目前缺失的 null 上。`ArmProfile.hypothesised_effect` 存在的部分目的就是让这个拒绝自动化：一个声明
+0.5pp 假设的 release arm 会在花掉任何东西之前被 `require_power` 拒绝。

**本设计的声明里，只有两项真正被 CI 抓住。** `tools/check_declared_is_consumed.py` 有四条规则，覆盖
knob、record 字段和 state 通道。`corpus_release` 是一个 knob，所以缺少读取方会按名字让构建失败。
`ArmProfile.hypothesised_effect`、`.readout`、机制注册表的条目、存储的 SQLite 列、以及 `Attribution`
的字段住在那四条规则都不遍历的命名空间里 —— 所以对它们而言，「声明了但没有读取方」是由评审而不是由 CI
守着的。补上它是再加一条同形状的规则；在那之前，这一段就是那个控制。

---

## 12. CI

### 引擎仓库 —— `.github/workflows/ci.yml` 的 `test` job

```bash
uv run --frozen python tools/check_imports.py                    # LAYERS 里有 feedback 和 triage
uv run --frozen python tools/check_proposal_fields_are_consumed.py
uv run --frozen pytest tests/feedback tests/triage -rs
```

夜间的 `mutate` job 加上回流路径已声明的那些变异（§13）。

### 语料仓库

这是工程师的 commit 要经过的那个 CI，之所以在这里规定它，是因为检查器住在引擎里。它**不得**需要模型
凭据或数据库。

```bash
uv run --frozen python ../governed-bi/tools/check_corpus_conformance.py --corpus-dir .
uv run --frozen python ../governed-bi/tools/check_ratchet.py --pins .conformance-pins.txt
uv run --frozen python -c "from governed_bi.retrieve import build_index; ..."   # T1：它必须能起来
```

### 两边都不跑什么

T4 和 T5。它们花钱，所以由一个已经决定要花这笔钱的人来启动，而产出的 artifact 记录它花了多少。

---

## 13. 构建顺序

第 0–4 步不花任何钱，而且各自独立有用。第 6 步是这份设计第一次可能以「花钱」的方式出错的地方。人日估算
按一位熟悉这棵树的工程师计。

| # | 做什么 | 人日 | 为什么在这个位置 |
|---|---|---:|---|
| **0** | **给服务语料 `git init`**、首次提交，并修掉 `corpus/snapshot.py::snapshot` 那个无守卫的 `rmtree` | 0.5 | 落地那一半没有东西可落，而第一个 `snapshot` 调用者会把一个真实缺陷武器化 |
| 1 | `feedback/{events,validate,lifecycle,cluster}.py` + `store.py` + `attribution.py`；`LAYERS`；两个读者动词写入存储；读取方并集存储与通道 | 4 | 在没有任何模型的情况下关掉「没有东西关闭一个 open 行」。**回答第一个真问题：投诉到底会不会聚类？** |
| 2 | 分析师捕获 UX（§15.2）+ `/observations` 读接口 + `/reports`（§15.3）+ **re-ask 按钮** + `review-copy.ts` 及其检查脚本 | 3.5 | 文案不再是一个小谎，而读者可以自己验证 |
| 3 | `corpus/patch.py` + `tools/export_bundle.py` + `tools/check_landed.py`（含 `--verify`）+ `/review`（§15.4–15.8）+ 四个 admin 动词 | 5 | **一个不含任何 agent 的完整闭环。** steward 今天就能交给工程师一个 bundle |
| 4 | 阶梯 T0–T2、六条新规则、棘轮、`corpus_release`、`ArmProfile.hypothesised_effect` | 4 | 免费闸门与可比性修复，两者都不依赖流水线 |
| 5 | `tools/drain_raised.py`，然后删除 `serve/raised.py`、`api/raised_write.py`、`ThreadTurnLog.append_raised`/`raised_of` 以及读取并集 | 1.5 | **在** drain 报告为零并保持之后。通道删除单独成一步，因为它的风险完全是迁移风险 |
| 6 | prompt 注册表拆分 + `triage/` 骨架 + `stop_after="diagnose"` 的 `diagnose` | 4 | **第一次花 token。** 先发布并度量 Diagnoser，再在它上面建东西 |
| 7 | `replay` 模式的 `reproduce`；把 T3 接成闸门 | 3 | |
| 8 | `stamp.py`、`stage_asset`、`author`、`assemble` | 4 | |
| 9 | `trial.py`（含 `snapshot` 修复）与 T4；`arbitrate`、有界修订循环 | 3 | 试验语料是一个阶梯设施，所以即便第 6 步杀掉了流水线，这一步仍然有用 |

**第 0–3 步就是最小可行闭环：约 13 人日，而且其中不含任何一次模型调用。** 从第 6 步起的一切都以第 6 步
的度量为条件。没有 `ask_sme` 那一步，也没有 Adversary 那一步：两者都被裁掉了（ADR 0015 §5）。

### 设计无法知道的三件事，以及各自最便宜的实验

| 未知 | 实验 | 什么时候 |
|---|---|---|
| 投诉会聚类吗？ | 第 1 步交付 `cluster_key`，`GET /observations` 报告规模分布。零成本 | 约 30 条真实 observation 之后 |
| 一个模型能把缺陷定位到一个 asset 上吗？ | 第 6 步的 diagnosis-only 模式跑 20 条 observation，约 600k token。与一位 steward 自己的定位对分 | 第 8 步之前 |
| 分析师会用那个选择器吗？ | 第 2 步把 `category` 做成可选。度量携带它的 observation 占比 | 约 30 条之后 |

如果 Diagnoser 是 reflector 那个水平（在更容易的任务上 OOF AUC 0.597），**停在第 7 步**，而诚实的产品
是一个不带撰写功能的分诊队列。

---

## 14. 测试名

按「什么会坏」分组。名字是句子，遵循本库惯例。

**存储与生命周期**
- `tests/feedback/test_every_stored_state_names_its_actor.py` —— 遍历 `TRANSITIONS`，对行动者为空的存储态失败
- `tests/feedback/test_a_declined_observation_cannot_be_reopened.py`
- `tests/feedback/test_a_duplicate_joins_the_patch_set_of_its_original.py` —— 原型发现落地时受影响 observation 算成了 1 而不是 2
- `tests/feedback/test_a_note_can_be_filed_on_a_paused_thread.py` —— 那个消失了的 409
- `tests/feedback/test_no_comparability_knob_names_the_feedback_store.py`
- `tests/feedback/test_the_derived_landing_states_are_not_stored.py`
- `tests/feedback/test_a_superseded_patch_does_not_read_as_handed_off.py`

**语料写入**
- `tests/corpus/test_an_edit_does_not_create_a_second_file_with_the_same_id.py` —— M1，作为回归
- `tests/corpus/test_a_one_word_summary_edit_is_a_one_line_diff.py`
- `tests/corpus/test_an_edit_refuses_when_the_current_value_is_not_was.py`
- `tests/corpus/test_patch_refuses_a_governance_field.py`
- `tests/corpus/test_snapshot_refuses_a_destination_that_is_not_a_corpus.py` —— 那个 rmtree 发现

**流水线**
- `tests/triage/test_a_full_run_leaves_corpus_content_hash_unmoved.py` —— 以及 asset id 集合不变
- `tests/triage/test_the_author_cannot_write_a_governance_block.py`
- `tests/triage/test_source_human_status_certified_is_restamped_curator_proposed.py`
- `tests/triage/test_the_reproduction_rate_never_lands_in_confidence.py`
- `tests/triage/test_the_revision_loop_is_bounded.py` —— 一个其补丁永远通不过 `validate` 的脚本化模型
- `tests/triage/test_a_trial_replay_leaves_the_corpus_root_byte_identical.py`
- `tests/triage/test_an_in_place_trial_restores_and_asserts_no_drift.py`

**身份与可比性**
- `tests/conformance/test_the_two_prompt_registries_are_disjoint.py::test_prompt_set_hash_is_unmoved_by_a_triage_prompt_edit` —— 断言 `b1f9e4d7d230cb97`
- `tests/conformance/test_corpus_conformance_rules_fire.py` —— 扩展到 M2 的全部四个破坏
- `tests/eval/test_a_corpus_release_is_a_declarable_treatment.py`
- `tests/api/test_the_return_path_respects_the_grant.py::test_the_reader_note_is_a_declared_exemption`

### 已声明的变异

放在 `tools/mutation_catalogue_data_2.py` 里，用 `rp-` 前缀，因为 `open-work.md` §3.9 讲的正是「不可能
失败的测试」：

| id | 变异 | 必须被谁抓住 |
|---|---|---|
| `rp-1` | `restamp_model_authored` 原样返回输入 | restamp 测试 |
| `rp-2` | `stage_asset` 写进 `corpus_root` | `test_a_full_run_leaves_corpus_content_hash_unmoved` |
| `rp-3` | `derived_state` 永远返回 `handed_off` | `test_a_superseded_patch_does_not_read_as_handed_off` |
| `rp-4` | `apply_edit` 去掉 `was` 检查 | `test_an_edit_refuses_when_the_current_value_is_not_was` |
| `rp-5` | V19 的谓词不返回任何发现 | 一致性 fixture |
| `rp-6` | V23 的谓词不返回任何发现 | 重复 id fixture |
| `rp-7` | `narrow_feedback_rows` 原样返回输入 | grant 测试 |
| `rp-8` | admin router 无条件挂载 | 未设置环境变量时的 404 断言 |
| `rp-9` | `sweep` 删除非终态行 | sweep 测试 |
| `rp-10` | `mechanism_indicator` 在 ledger 为空时返回 `False` 而不是 `None` | 一个断言「`mcnemar` 在 `attempts` 为空的对上报告未度量」的测试 —— 这个约定曾让一个正确的数字带上错误的来源，所以它被钉住而不是被记住 |
| `rp-11` | `derived_state` 不重跑 fixture 就升级到 `retrieval_verified` | 一个断言升级需要一次通过的 T3 重跑的测试 |
| `rp-12` | `check_landed.py` 把一个匹配不上的 `source_refs` id 当作匹配 | 一个带故意拼错 `obs:` ref 的测试，断言它被报告为 dangling |

---

## 15. 界面

三个角色，三块屏幕，以及一个拥有全部文案的模块。

### 15.1 新增与改动的文件

| 路径 | 做什么 |
|---|---|
| `ui/app/reports/page.tsx` | 新路由，面向分析师 |
| `ui/app/review/page.tsx` | 新路由，面向 steward |
| `ui/components/answer/raise-note.tsx` | 重写（§15.2） |
| `ui/components/answer/category-picker.tsx` | 新增 |
| `ui/components/reports/report-list.tsx` | 新增 |
| `ui/components/reports/report-status.tsx` | 新增 —— 状态 chip **及其句子**，一个组件，好让 §5 只有一个渲染者 |
| `ui/components/reports/re-ask-button.tsx` | 新增（§5） |
| `ui/components/review/review-surface.tsx` | 新增 —— 双栏外壳 |
| `ui/components/review/review-queue.tsx` | 新增 |
| `ui/components/review/cluster-panel.tsx` | 新增 |
| `ui/components/review/evidence-bundle.tsx` | 新增 |
| `ui/components/review/reproducer.tsx` | 新增 |
| `ui/components/review/asset-diff.tsx` | 新增 |
| `ui/components/review/decision-bar.tsx` | 新增 |
| `ui/components/review/handoff-panel.tsx` | 新增 —— 导出后的 bundle 下载与 manifest |
| `ui/components/clarifications/pending-queue.tsx` | 一个链接加两段文案（§9） |
| `ui/lib/category-taxonomy.ts` | 新增 —— `category` → 标签。唯一的映射 |
| `ui/lib/review-copy.ts` | 新增 —— §3、§5、§15 里**每一句**面向用户的文案 |
| `ui/lib/my-reports.ts` | 新增 —— `localStorage` 存储 |
| `ui/lib/schemas.ts`、`types.ts`、`api-client.ts`、`hooks/queries.ts` | zod schema、`z.infer` 类型、9 个 client 方法、6 个 hook |
| `ui/components/layout/nav.tsx` | 两个 `LINKS` 条目 |

**`ui/lib/review-copy.ts` 是把「诚实文案」规则变成机械的。** 每一句都住在那里、按状态索引，而
`ui/scripts/check-review-copy.ts` 和其他几个 `check-*.ts` 一样跟着 `npm run lint` 跑。它断言两件事：
observation / patch / decline 三个状态联合的每一个成员都有一句文案；以及没有任何一句命中禁用词表 ——
`robust`、`seamless`、`comprehensive`，以及这个项目最在意的两个：**`automatically`** 和
**`will be fixed`**（除否定用法外）。两项检查在文案内联写在组件里时都不可能做，而这正是这个模块存在的
全部理由。

### 15.2 分析师：两次点击完成捕获

三个状态，而分析师在第一个之后就可以停。

**状态 1 —— 触发器。** 答案卡上一个 `variant="outline" size="sm"` 按钮，位置与今天相同、标签也相同
（`"This answer is wrong"` / `"This refusal looks wrong"`）。它已经好用，而且它是读者认得的那一句。

**状态 2 —— 选择器。** 点击后**就地**展开 —— 没有对话框、不跳转，因为分析师正要指着那个答案 ——
展开成一个五行（已交付）或三行（被拒）的竖排列表，取自 §3。**每一行一次点击并立即提交。没有提交
按钮。** 中位交互是两次点击、零打字，对比今天的两次点击加一个空文本框。

**状态 3 —— 补充项，在提交成功**之后**展示。** 回执上两个可选单行输入框，各自可独立保存：

- `expected` —— `"If you know it: what should the answer have been?"`，200 字符。**steward 能拿到的
  单一最高价值字段**，因为它是这一页上唯一可证伪的断言，而且它不需要任何 schema 知识（一个数字、
  一个名字、「大概 400，不是 40」）。
- `note` —— 已有的自由文本，上限不变仍是 `RAISED_NOTE_MAX_CHARS = 4000`，标签改为
  `"Anything else that would help (optional)"`。

**这个倒置就是重点。** 今天是 note 卡着提交，所以一个不想写字的分析师什么都不给你。这里提交已经完成，
补充项是额外的 —— 而那是它们唯一会被填的安排。

**选择器刻意不做什么。** 它绝不点名任何表、列、metric 或 term。不是因为分析师不能从下拉框里挑一个，
而是因为一个有 13,281 个 asset 的下拉框会把两次点击的动作变成一次搜索任务，而一次**错误的**选择比不选
更糟：它把 steward 送到错的 asset 上，还带着一个看起来很确定的指针。`term_mismatch` 是这个界面能靠到
最近的地方，而它点名的是一**类**对象，绝不是一个实例。定位 asset 是 steward 的工作，§15.4 给他们机械。

**回执文案原文** —— 它移除了产品里今天就存在的一个谎（`"Filed. It is on the pending list."`，而那个
列表从不被清空）：

> 已提交。数据管理员按最早优先复核这些。这台引擎不知道你是谁，所以不会有人给你发邮件 —— 到
> **My reports** 看结果。

### 15.3 `/reports`：分析师之后看到什么

`GET /observations`，按 `localStorage` 里的 id 过滤。**`ui/lib/my-reports.ts` 是浏览器记忆，而页面
就这么说** —— 只有一个 principal、没有用户存储，所以在这里发明一个按用户的概念会是一个并不存在的边界：

> 这个列表由这个浏览器记住，不由你的账号记住。这台引擎不知道你是谁，所以换一个浏览器会看到不同的列表。

每一行：问题、提交时间、category 标签，以及一个状态 chip，其句子就是 §5 里该状态对应的那一句。
`landed_verified`、`landed_matched` 和 `retrieval_verified` 带 **Re-ask** 动作（§5）。

### 15.4 `/review`：steward 的屏幕，钱在这里

一个新路由，导航条目放在 **Pending** 和 **Settings** 之间。**不是 `/audit` 上的第三栏**，理由是
`pending-queue.tsx` 自己陈述过的那条再往前推一步：`/audit` 是最新优先、涵盖每一个 turn；这里是最早
优先、只涵盖有人投诉过的，而把两个滚动方向放在一块屏幕上会让两者都更差。

```tsx
// ui/components/review/review-surface.tsx
export function ReviewSurface(): JSX.Element {
  // URL 里的 `?cluster=`，不是 useState：steward 在这里的整个工作就是把一个决定交给别人，
  // 而「看这个」必须是一个链接。
  const [cluster, setCluster] = useQueryParam("cluster");
  return (
    <div className="flex h-full min-h-0 flex-col gap-6">
      <ReviewQueue selected={cluster} onSelect={setCluster} />
      {cluster && <ClusterPanel clusterId={cluster} />}
    </div>
  );
}
```

`PageShell` 的 description，常驻在页面上 —— 一句话说清产品边界：

> 人们标记出来的答案和拒答，按看起来是同一个问题分组。最早优先。在这里做决定会**起草**一处对语义层的
> 变更 —— 它不会应用任何变更。

**队列。** `GET /observations?state=open,triaged&group=cluster`，cluster 内联其成员（3–20 条短行；
每次点击为此多一次往返毫无意义）。每一行：`n` 条 observation · category 标签 · schema · 最早的
`filed_at` · 两三个表名 · 一个显示**不同问题数**的 badge —— 那个数字才说明这是一个人点了两次，还是
五个人撞在同一面墙上。

**按 cluster 最早成员的时间排序，不按规模。** 今早的五条 cluster 并不比等了一个月的一条更紧急，而按
规模排序会让长尾永久不可见。

cluster 标题下的说明常驻，因为这个分组是结构化的：

> 按被报告的问题类型、以及那些 turn 被允许读的表分组。这里没有任何东西读过那些问题并判定它们是同一个
> 意思 —— 在把它们当作一个问题处理之前，先看看这些行。

**空状态：** `"Nothing to review. Every observation filed on this server has been triaged."` ——
一句与 `/reports` 的空状态**不同**的话，因为「没人提交过任何东西」和「全部已分诊」是两个不同的事实，
而把其中一个读成另一个，正是一个队列被弃用的方式。

**刻意不在队列里的：** SQL、ledger、record。全都只有一次点击之遥。一个展示证据的队列是一个没人扫的队列。

### 15.5 证据包：七个块，全部在决策之上

`ui/components/review/evidence-bundle.tsx`。每选中一个 cluster 取一次。

1. **问了什么、回来了什么。** 问题原文；然后 `outcome`，非 `answered` 的 turn 再加 `terminal_reason`
   和 `refused_by`，经由已有的 `lib/answer-delivery.ts::terminalLabel` 渲染 —— **好让 steward 读到
   分析师读到的同一句话**；然后 `answer_text`。
2. **读者说了什么。** category 标签、`expected`、`note`。`expected` 被排版成引文，并在这一块里获得最大
   视觉权重，因为它是这一页上唯一可证伪的断言。
3. **那条语句。** `generated_sql` 放在已有的只读 `<SqlBlock/>` 里，加上经
   `<AgentTimeline/>` / `buildStepsFromLedger(execution)` 渲染的尝试 ledger —— 与答案卡用的是同一批
   组件。**与决策在同一块屏幕上，不在一个 tab 后面。** 一个必须跳走才能读 SQL 的 steward 会在不读它的
   情况下做决定。
4. **这个 turn 被允许读什么。** `licensed`（Layer 6 据以执行的允许清单）和 `schemas`（路由器的选择），
   旁边是 `schema_ranking` 的 top 5 及分数 —— 因为「gold schema 排第 4」和「它从来不是候选」是两个
   修法相反的问题，而那个 register 字段的存在就是为了区分它们。
5. **哪些语料 asset 在 context 里。** 关键所在，三列，每个 asset 都链进 `/corpus`：
   - **Found** —— 每个 `facet_hits` 条目一行，带 `asset_type`、找到它的 facet，以及它的
     `lexical`/`semantic` 分数。
   - **Reachable** —— `pulled_in`（`asset_id → resolve|connect`），合并并标记。
   - **Rendered** —— *派生*：found ∪ pulled_in，减去 `budget_dropped`，减去 `evicted.dropped_ids`。

   那条说明，也就是诚实的那部分，属于面板而不属于文档：

   > 「Rendered」是派生的，不是记录下来的。没有任何 register 字段列出真正在模型读到的那个 block 里的
   > asset id —— `context_hash` 是一个摘要，而 `evicted` 只点名了预算丢掉的那些。这一列是
   > *found 减去上限和预算移除的部分*，而它与真实集合相同 —— 除非在检索和渲染之间有什么东西移除了一个
   > asset 却没有说。

   **那个一字段修复，好让这条说明是一个决定而不是一次摊手：把 `rendered_asset_ids` 加进
   `RECORD_REGISTER` 的 `Stage.assemble`。** 一个 `Tier.treatment` 字段，其消费者就是这个面板，把一次
   派生变成一次观察。它必须**与**这个面板一起落地、不能更早 ——
   `tools/check_declared_is_consumed.py` 和 `test_the_declared_but_unconsumed_set_does_not_grow`
   就是理由，而它们是对的。
6. **复现器**（§15.6）。
7. **完整 record**，折叠，仅 `atLeast(mode, "engineer")` —— 就是 `/audit` 的 `TracePanel` 已经渲染的那
   份 `GET /audit/turns/{id}/trace` 载荷，复用而不是重新实现。若 `incomplete_fields > 0` 则它**不**
   折叠，并携带：「这个 turn 的 record 缺 N 个必填字段。不要据此起草变更 —— 关于这个 turn 有些东西没被
   记录下来。」

**它刻意不展示什么：结果行。** `result_table` 按 ADR 0006 §11 只在实时时存在、不在 record 里，所以没有
东西可展示，而一个为它留了位置的面板会读作「那些行没被保存」而不是「那些行不保留」。

**披露情况，因为 ADR 0012 §8.7 要求说明。** 这个界面读 `turn_log`，它不是 grant-aware，所以它披露的
恰好就是 `GET /audit/turns/{id}/trace` 已经向同一个未认证调用方披露的东西。**它不扩宽任何东西，也不
收窄任何东西。** 而第 5 块里语料 asset 那一半**是**可收窄的、因此**就是**被收窄的：那些 asset 经由
`visible(get_session())` 读取，所以一个被 withheld 的 asset 会像在 `/corpus/assets` 里那样被省略。
这种不对称是唯一可用的那一种 —— 在隔壁路由照样提供 SQL 的情况下拒绝展示 SQL 是演戏，而在 `visible()`
存在的情况下在这里扣住一个 asset 是一个洞。

### 15.6 复现器

steward 需要一个 record 给不了的事实：*这件事现在还发生吗？* `cannot_reproduce` 是一个驳回理由，所以
它必须可核。**它是一个按钮，它花一次模型调用，而按钮就这么写着。** 它开一个**新**对话（绝不是投诉者的
thread），而它的结果记在 observation 上，不记在语料上。

### 15.7 diff：逐字段，绝不是文本 diff

```tsx
export type FieldEdit = {
  path: string;             // "summary" | "body" | "reliability.note" | "columns[betrieb_id].body"
  before: string | null;    // create 时为 null
  after: string | null;     // 删除时为 null
  kind: "scalar" | "block" | "list";
};
export function AssetDiff({ edit, fieldOrder }: {
  edit: AssetEdit;
  /** 该类型在引擎里声明的字段序，来自 `GET /corpus/fields?type=`。 */
  fieldOrder: CorpusField[];
}): JSX.Element;
```

**不是对 YAML 做文本 diff，而这不可商量** —— 它由 M1 推出。`to_mapping` 省略默认值，所以 `governance`
和 `reliability` 在取默认值时根本不在文件里，于是设置其中一个时文本 diff 会显示一处**虚假的新增**；
而 PyYAML 在 80 列处重排，所以对一个词的 `summary` 改动做文本 diff 会变成整段 diff。字段序从
`GET /corpus/fields?type=` 读取，所以往 `corpus/schema.py` 加一个字段，它会出现在这里而这个组件不用改。

- **`scalar`**（`summary`、`reliability.note`）—— 单行，行内**按词**diff，新增和删除都可见，加一个
  对着上限的实时字数统计。一个 251 字符的 summary 如果是在导出**之后**才发现，那就是一次白跑的往返。
- **`block`**（`body`）—— 双栏，**按行**。8,000 字符上做按词 diff 无法阅读，而 `body` 是真正到达 prompt
  的那个字段，所以它在屏幕上占最大空间。
- **`list`**（`synonyms`、`rules`、`source_refs`）—— 新增项和删除项做成 chip，**绝不做重排后的文本
  diff**。YAML 序列顺序对这几个都不具语义，而把一次重排渲染成一次变更会训练评审者去略读。
- **未改动字段折叠**而不是隐藏，藏在「显示这次没有改动的 9 个字段」后面 —— 否则一个不在 diff 里的字段
  和一个不在 asset 里的字段看起来一模一样。

**这个组件拒绝渲染两样东西**，而两者都是拒绝而不是缺口：任何形式的 `governance`（一个能提议排除的屏幕
**就是**那个「其缺席即控制」的工具 —— ADR 0015 §8），以及对一张表内联 `columns` 的任何结构性改动（§6）。

### 15.8 决策条

吸底在详情栏底部，好让它在任何滚动位置都与证据同屏。这是这一页上最重要的布局决定，也是为什么这一栏
内部滚动而不是让整页变长。

```tsx
export function DecisionBar({ cluster, patch, blocked }: {
  cluster: ObservationCluster;
  patch: PatchDraft | null;
  /** 非空则禁用 Draft/Export，并原文渲染：一致性 + 内容 + governance。 */
  blocked: readonly string[];
}): JSX.Element;
```

四个动作，而第四个是大多数复核工具都省掉的那个：

- **Draft a change** → §15.7 允许的字段集的编辑器，然后 `POST /patches`。
- **Decline** → 一个覆盖八个 `decline_reason` 成员的 `Select`，每一项把它在 §5 的那句话渲染成
  **该选项的描述**，好让 steward 在选之前就读到分析师将要读到的话。不接受纯自由文本驳回：一个没人能
  聚合的理由是一个没人复核的理由。
- **Fold into another observation** → `duplicate`，并加入那一条的 patch 集合（§5 —— 否则落地时受影响的
  observation 会算成一条而不是两条）。
- **Escalate。** 没有可以升级**给**的人 —— 一个 principal，没有指派人。所以它不是一个路由动作，它是
  **一个有名字的状态**：`blocked_on_a_person`，加一行必填说明。面向分析师的文案：「正在等一个人：
  <说明>。没有任何东西在自动推进这件事。」指派人下拉框被拒绝了：没有用户存储可以填充它，而一个只有
  一项的下拉框是对工作流的一个谎。

### 15.9 显示模式

仅工程师可见的那些块（§15.5 第 7 块、`schema_ranking` 分数、阶梯细节）藏在已有的
`ui/lib/display-mode.ts::atLeast(mode, "engineer")` 后面。不发明任何新东西：那个模块本来就带着「显示
模式不是安全边界」的警告，而这份设计不把它变成一个。

---

## 16. 这份设计不做什么

- **它不认证任何人。** 单一 principal，而碰到端口仍然就够了。admin 动词以未挂载状态发布；那是一个部署
  开关，不是一个身份。
- **它不知道是谁提交了一条 observation。** 没有 `filed_by`，因为 `api/auth.py` 返回单一 principal，
  而在这里加一个按用户的字段会是一个并不存在的边界。reports 页面记住**这个浏览器**提交过什么，存在
  `localStorage` 里，并标注为浏览器记忆。
- **它不宣称一个落地的补丁修好了那个问题。** 见 §5。
- **它不把 prose 注入扫描当作闸门。** V21 复用 `GUARD_RULES`，V19 覆盖一个具名披露。除此之外，姿态是
  ADR 0006 的：名字可以到达 prompt，而点名它的查询被拒。一个企业分叉必须自己决定这够不够。
- **它不让本仓库变成策展人。** 流水线撰写候选；语料由人拥有、在本仓库之外受版本控制、且无法从本仓库重建。
