# 回流路径 —— 工作参考

读者与工程师的反馈如何变成一次语料变更。约束性决策在
[ADR 0015](adr/0015-the-return-path.zh.md)；本页描述的是 `design/return-path` 上的代码。设计与实测
不一致的地方，写在这里的是代码的行为；被砍掉的设计已经从本页删除，而不是被标注。
English: [The return path — working reference](return-path.md)。

有三个界面在下文被描述、但**没有建**，而且各自在出现的地方就说明了：分析师上报 UI（§12.2）、
`/reports`（§12.3）、以及 re-ask 按钮（§5）。这套部署上所有角色都由同一个人担任，所以通知回路和按读者
划分的报告列表没有服务对象 —— 输入是 eval artifact：`tools/import_eval_failures.py`。

标注 **实测** 的数字取自 `../MS Fabric Facilities/corpus` 与 `../BIRD-corpus`，测于 2026-08-22/23；
其余数字都是估算，且文中会说明。

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
读过那些问题。他选中这个 cluster，详情面板在决策条**上方**展示证据（§12）：问了什么、回来了
什么；Priya 说了什么（她的 `expected` 被排版成它本来就是的那种引文）；SQL 和尝试 ledger，用的是她看到的
同一批组件；这个 turn 被允许读什么，附带路由器的 top-5 排名；以及哪些语料 asset 在 context 里 ——
并附上「rendered 这一列是派生的、不是记录下来的」这个注意事项。

第 5 块就是答案所在：*active customer* 这个 `term` asset 在 context 里，而它的 `summary` 对 `status`
列一个字都没说。引擎没有任何办法知道。他跑面板给出的那条复现命令 —— 它不花钱 —— 而它仍然返回 4,102。

他起草一处变更：一个字段，`term_active_customer.summary`，加上那个别名和那条规则。diff 就这一个字段
按词渲染，并标出是哪个字段 —— 因为改 `summary` 改的是「什么会被找到」，改 `body` 改的是「模型读到
什么」。他把三条 observation 置为 `addressed`。

**周一 11:41 —— 阶梯。** 一条命令，`tools/verify_patch.py`。T0 用生产加载器解析被编辑的那个 asset。
T1 在内存里把这次编辑替换进整棵树，跑全树一致性、`build_structure` 和 `build_index`，按规则 id 报告
无新增发现。T2 把这个 term 的 binding 对语料自己声明的表和 join 解析。T3 ——
`tools/reproduce_observation.py --embed` —— 在 agent 模型关闭的情况下重放检索：三道受影响的问题上
gold 表保持被覆盖，其余没有一道丢失 coverage。总挂钟时间约半分钟。总花费 **$0**（§10）。

因为这个补丁改的是 `summary`，T3 在这里是一个真实的验证器。如果它只改了 `body`，T3 根本看不见它 ——
`body` 从不进检索索引 —— 而记录会这么写，不会报一个通过。补丁触碰的字段决定有没有任何免费层级能检查它。

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
  events.py                        封闭词汇表 + Observation / Patch
  validate.py                      faults_with(Observation) / faults_with(Patch) -> list[str]
  lifecycle.py                     TRANSITIONS, PATCH_TRANSITIONS, is_open(), derived_state()
  store.py                         FeedbackStore —— 深模块
  cluster.py                       cluster_key(), clusters()

src/governed_bi/corpus/patch.py    # 与 store.py 并列：外科式字段编辑（§6）
src/governed_bi/api/feedback_routes.py

tools/import_eval_failures.py      # 一份 eval artifact 的失败 -> observation
tools/verify_patch.py              # 免费阶梯，T0-T2（§10）
tools/reproduce_observation.py     # T3：这个失败现在还发生吗？（§10）
tools/export_bundle.py             # patch -> bundle
tools/check_landed.py              # 语料 source_refs -> 派生落地状态；--verify 重新核对
```

**没有流水线这个包。** agentic triage 那套设计 —— 一个 Diagnoser、一个 Author、一个带自己入口点的
`triage/` graph —— 在本轮被砍掉，它的文件一个都不存在。steward 拿到的是复核界面（§12）、免费阶梯
（§10），以及他自己的判断。也没有 `attribution.py`：一个 turn 贡献的那些字段就是 observation 行上的列
（§4），一个单独携带它们的类型会是它们住的第二个地方。

### Import 分层

`tools/check_imports.py::LAYERS` 必须穷举 `src/governed_bi` 下的每个包 —— `undeclared()` 在它没穷举时
让整次运行失败，而一个被列表漏掉的包**完全没有**约束。插一处：

```python
LAYERS = (
    ("paths",), ("credentials",), ("ports",), ("register",), ("measure",),
    ("corpus",),
    ("feedback",),        # <- 新增：需要 register + corpus，不需要更上层
    ("retrieve",), ("govern",), ("datasource",), ("model",), ("serve",), ("eval",),
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
| 它试了又试，始终没到 | `attempt_capped` | 通常**都不是**。它自成一个成员而不是归入 `unverifiable`：「我看不出」是关于读者的陈述，这一条是关于引擎的 |

仅操作员可提交的，靠 `source` 而不是靠 `kind` 区分：

| `category` | `source` | 备注 |
|---|---|---|
| `column_suspect` | `operator` 或 `agent` | `Reliability.status` 可由 AI 撰写，所以 agent 也能提交 |
| `column_excluded` | 仅 `operator` | `Governance.excluded` 是 human-only。存储拒绝来自任何其他 `source` 的这个 `category` |
| `reusable_fact` | `operator` | 操作员对一次澄清的回答，被提升（§9） |

**`source` 是与 `category` 分开的列**，因为同一个 observation 来自三个人群（`reader`、`operator`、
`agent`），而队列对它们的排序不同。把它折进去会用十三个值回答十个问题。

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
| metric expression 写错了 | **编辑**。而且这是唯一一个有**免费**验证器的类别：478 个 expression 里 85 个解析不了（共 107 条发现），另有 17 条点名了在任何地方都解析不到的标识符（**实测**） |
| 一个该标 `suspect` 的列 | **编辑** `column.reliability` |
| 一个该被 `excluded` 的列 | **从这个环的视角看是「都不是」** —— 它发出一条请求，由人手动编辑 |
| 一次澄清的回答其实是一个可复用事实 | **新建** `term` 或 `few_shot` —— 或者**都不是**，如果它只是一次性的过滤条件 |

---

## 4. 存储

### Schema

```sql
-- feedback/store.py::_SCHEMA，由 _migrate() 施加。`PRAGMA journal_mode = WAL` 在事务之外设置，
-- 因为它是数据库级属性，不是一次变更。

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  observation_id      TEXT PRIMARY KEY,
  filed_at            TEXT NOT NULL,          -- ISO-8601 UTC，秒
  source              TEXT NOT NULL,          -- reader | operator | agent
  kind                TEXT NOT NULL,          -- from_refusal | wrong_answer
  category            TEXT,                   -- §3，可为空：第一下点击可能就是全部
  note                TEXT NOT NULL DEFAULT '',   -- <= 4000 字符，已 strip
  state               TEXT NOT NULL,          -- open | triaged | declined | duplicate | addressed
                                              -- | blocked_on_a_person
  decline_reason      TEXT,                   -- state = declined 时必填；§5
  duplicate_of        TEXT REFERENCES observation(observation_id),
  blocked_note        TEXT NOT NULL DEFAULT '',   -- blocked_on_a_person 时必填；§5
  triaged_at          TEXT,
  -- attribution，是拷贝而不是 join（见下）
  turn_id             TEXT,
  thread_id           TEXT,
  question            TEXT NOT NULL DEFAULT '',
  outcome             TEXT,
  refused_by          TEXT,
  generated_sql       TEXT,
  licensed_json       TEXT NOT NULL DEFAULT '[]',
  schemas_json        TEXT NOT NULL DEFAULT '[]',
  missing_tables_json TEXT NOT NULL DEFAULT '[]',
  -- 一条导入失败的基准那一半。对未认证调用方由 §7 的允许清单扣住。
  gold_sql            TEXT,
  gold_fingerprint    TEXT,
  pred_fingerprint    TEXT,
  quality_flags_json  TEXT NOT NULL DEFAULT '[]',
  corpus_content_hash TEXT,
  prompt_set_hash     TEXT,
  git_sha             TEXT,
  arm                 TEXT,
  question_id         TEXT,
  db_id               TEXT,
  external_key        TEXT UNIQUE             -- 导入方重读同一份 artifact 是幂等的
);
CREATE INDEX IF NOT EXISTS ix_obs_state    ON observation(state, filed_at);
CREATE INDEX IF NOT EXISTS ix_obs_turn     ON observation(turn_id);
CREATE INDEX IF NOT EXISTS ix_obs_category ON observation(category, state);
CREATE INDEX IF NOT EXISTS ix_obs_cluster  ON observation(db_id, category);

CREATE TABLE IF NOT EXISTS patch (
  patch_id                     TEXT PRIMARY KEY,
  created_at                   TEXT NOT NULL,
  author                       TEXT NOT NULL,   -- operator | agent
  intent                       TEXT NOT NULL,   -- new_asset | edit_asset | exclusion_request
                                                -- | engine_defect
  state                        TEXT NOT NULL,   -- draft | exported | withdrawn
  namespace                    TEXT NOT NULL,
  rationale                    TEXT NOT NULL DEFAULT '',
  -- 改了什么
  asset_type                   TEXT,
  asset_id                     TEXT,            -- new_asset 在 id 派生出来之前为 null
  field_path                   TEXT,            -- 只能是 "summary" 或 "body"（§6）
  was                          TEXT,            -- 起草时从活体语料读出
  becomes                      TEXT,
  asset_yaml                   TEXT,            -- 整份文档，仅 new_asset
  -- 它是对着什么被验证的
  base_corpus_content_hash     TEXT NOT NULL DEFAULT '',
  expected_corpus_content_hash TEXT,            -- bundle 建成之前为 null
  ladder_json                  TEXT NOT NULL DEFAULT '{}',  -- 层 -> GateResult
  withdrawn_reason             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_patch_state ON patch(state, created_at);

CREATE TABLE IF NOT EXISTS observation_patch (
  observation_id TEXT NOT NULL REFERENCES observation(observation_id),
  patch_id       TEXT NOT NULL REFERENCES patch(patch_id),
  PRIMARY KEY (observation_id, patch_id)
);

CREATE TABLE IF NOT EXISTS transition (       -- 只追加。审计轨迹。
  rowid_     INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  entity     TEXT NOT NULL,                   -- observation | patch
  entity_id  TEXT NOT NULL,
  from_state TEXT,
  to_state   TEXT NOT NULL,
  moved_by   TEXT NOT NULL,                   -- 那个行动者，绝不为空。§5
  detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_transition_entity ON transition(entity, entity_id, rowid_);
```

**`expected` 不是一个列。** 提交路由接受它、上限 200 字符，并把它作为一行 `expected: …` 前置到 `note`
里（§7）。为读者自己的一行文字单独开一列不值一次迁移，而复核界面就连着 note 的其余部分一起读它。

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
    def move(self, observation_id: str, *, to: ObservationState,
             moved_by: Actor | None = None, detail: str = "",
             decline_reason: DeclineReason | None = None,
             duplicate_of: str | None = None,
             blocked_note: str = "") -> Observation: ...
    def move_patch(self, patch_id: str, *, to: PatchState,
                   moved_by: Actor | None = None, detail: str = "",
                   withdrawn_reason: str = "",
                   expected_corpus_content_hash: str | None = None) -> Patch: ...
    def draft(self, patch: Patch, *, observations: Sequence[str]) -> str: ...
    def amend_note(self, observation_id: str, note: str) -> None: ...
    def record_ladder(self, patch_id: str, tier: str, result: Mapping[str, Any]) -> None: ...

    # 读
    def get(self, observation_id: str) -> Observation | None: ...
    def get_patch(self, patch_id: str) -> Patch | None: ...
    def queue(self, *, states: Sequence[ObservationState] | None = None,
              category: Category | None = None,
              limit: int = 50, offset: int = 0) -> Page: ...
    def patches(self, *, states: Sequence[PatchState] | None = None,
                limit: int = 50, offset: int = 0) -> Page: ...
    def observations_for_turn(self, turn_id: str) -> tuple[Observation, ...]: ...
    def patches_of(self, observation_id: str) -> tuple[Patch, ...]: ...
    def observations_of(self, patch_id: str) -> tuple[Observation, ...]: ...
    def history(self, entity_id: str) -> tuple[dict[str, Any], ...]: ...
    def counts_by(self, column: str) -> dict[str, int]: ...
```

`move` 和 `move_patch` 在同一个 `BEGIN IMMEDIATE` 事务里、带 `AND state = ?` 守卫，把新状态**和**它的
转移行一起写下，所以两个 steward 同时决定同一行不会把审计轨迹弄断链。没有保留期清扫：行会累积，没有
任何东西删它们。

`paths.py` 的 `assert_not_a_warehouse` 施加在这个路径值上，理由与它存在的理由相同。

### Knob

```
GOVERNED_BI_FEEDBACK_DB      默认 runs/feedback.sqlite，相对 REPO_ROOT 解析
GOVERNED_BI_FEEDBACK_ADMIN   未设置 -> steward 的四个动词根本不挂载
```

**这两个都不许变成 `register/knobs.py` 的 knob。** `serve/session.py::_resolved_knobs` 把每一个已声明
knob 放到每一行 serve 记录上，而 `measure/gates.py::_knobs_resolved_gate` 会比较它们，所以在那里声明
一个，就为一个没有任何 turn 消费的值移动了每一个 arm 的配置哈希 —— 构造上就是 `expand_hops` 缺陷。
由 `tests/feedback/test_the_feedback_store_is_not_a_comparability_knob.py` 钉住。

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

**没有建。** `ui/components/reports/re-ask-button.tsx` 不存在，它要落脚的那个 reports 页面也不存在。
写下它是因为它留下的缺口是真的。

每一个落地状态的文案都在叫读者再问一遍，而没有任何东西交付了做这件事的途径。所以：
`landed_verified`、`landed_matched` 和 `retrieval_verified` 应当在 reports 页面上带一个 **Re-ask** 动作。
它在一个**新** thread 上打开聊天界面，预填存储早已从 turn 记录上抄下来的问题文本（§4）。

用新 thread 而不是原来那个：写进别人的 thread 正是已删除的 `api/raised_write.py` 长篇记录过「不要做」的事，
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

## 6. 写 YAML：`corpus/patch.py` 就地替换一个字段

**实测（M1）。** 加载一个 table asset、改 `summary`、再调 `corpus/store.py::write`，产生了
**第二个同 asset id 的文件**；`store.load` 返回 1,434 个 asset 且 **problems 为零**；随后 `build_index`
抛出 `ValueError: duplicate index id`。服务语料是 178 个文件里的 1,432 个 asset —— 一表加约 50 个内联列
每文件 —— 而 `write` 把一个 asset 放到 `<root>/<namespace>/<id>.yaml`，那不是它来源的那张表所在的位置。

而且 `write` 是整文件重排：`store.py:256` 是 `yaml.safe_dump(to_mapping(asset), sort_keys=False,
allow_unicode=True)`，没有 `width`，而 `parse.py::to_mapping` 「omits defaults」。所以在一个人工撰写的
文件上做一次往返会丢注释、把超过 80 列的字符串全部重排、丢掉任何显式写出的默认值，并把键按 dataclass
字段序重排。

```python
# src/governed_bi/corpus/patch.py  —— 与 store.py 同层
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

```

除这两个之外还有：`read_field`、`Span`，以及作为异常类型的那几条拒绝 —— `FieldNotLocatable`、
`StaleValue`、`UnwritableValue`。**没有创建原语。** `new_asset` 是一个可声明的 patch intent，
`asset_yaml` 也会被校验，但没有任何工具导出它：`export_bundle.py` 拒绝除 `edit_asset` 以外的每一种
intent，因为只有一次编辑才产生工程师能应用的 diff。一个全新的 asset 是一个手写的文件。

**只有两个字段路径，再无其他。** `patch.py::EDITABLE` 是 `{summary, body}` —— 刻意与
`feedback/validate.py::EDITABLE_FIELD_PATHS` 允许的那一组相同，并有一个 import 期守卫在两者不一致时
失败。理由是 `lifecycle.derived_state`：它靠比较 `summary`/`body` 文本来确认落地，所以一个改了它读不到
的字段的补丁会落地、然后永远读作 `superseded`。`reliability`、`binding` 和 `rules` 是手工编辑。另外有
四个根键无论调用方怎么要求都不可触及：`governance`、`provenance`、`audit`、`columns`。

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
  之后，绕过了一个看不见它的检查器。对一个列的 `summary` 的编辑，走**那张表的**文件上的
  `locate`/`apply_edit`；而一个新列是数仓变更，不是语料变更。

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
  MANIFEST.yaml        补丁、它的 observation 与 question id、阶梯结果、base 哈希
  COMMIT_MSG.txt       生成的。首行 <= 72 字符。点名 observation id，不含 prose
  changes.patch        可 `git apply -p1`，针对 base_corpus_content_hash 产生
  after/               post-state 文件全文，好让评审者读结果而不是读 diff
  evidence/
    observations.md    每位读者说了什么，原文，放在代码围栏里
    ladder.json        每一层跑过的 GateResult
```

```bash
uv run --frozen python tools/export_bundle.py --patch pat-… --out ./bundles
uv run --frozen python tools/export_bundle.py --patch pat-… --dry-run   # 打印 diff，不写任何东西
```

`MANIFEST.yaml` 刻意不放 `expected_corpus_content_hash`。它是一棵还没人写出来的树的摘要，而一个
谁也无法比较的、长得像哈希的字符串比一处缺失更糟；`tools/check_landed.py` 在 commit 之后算它。

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

**没有建。** `ui/components/clarifications/pending-queue.tsx` 上没有任何通往复核界面的链接，而
`Category.reusable_fact` 没有任何生产者。词汇在，界面不在。

`pending-queue.tsx` 按设计是只读的：在那里回答等于恢复一个「这位操作员并非被问方」的
thread（ADR 0006 B9）。这个约束不变。

它需要的是**一个链接，不是一个按钮。** 这个链接打开 steward 界面，预填一条 `reusable_fact`
observation，携带那个暂停 turn 的问题和澄清文本。文案是明说的：

> 那个暂停的对话仍然保持暂停，而提问的人不会收到回复。你在这里写下的东西会变成对语义层的一个提议变更，
> 好让下一个问同样问题的人不必再被反问一次。

没有任何东西调用 `command.update`。没有任何东西调用 `POST /threads/{id}/state`。那个暂停的 thread
被读取，从不被写入。

---

## 10. 验证阶梯

每一层都是**增量闸门**。服务语料本身就产生 361 个 `build_structure` problem（**实测**），所以一个
「零 problem」的闸门会拒掉生产、会被豁免，而豁免正是一个真实发现变绿的方式。每一层问的都是：**这个
补丁**有没有把事情弄得更糟。

T0 到 T2 是一条命令，不花任何钱：

```bash
uv run --frozen python tools/verify_patch.py --patch pat-…             # 一直跑到 T2
uv run --frozen python tools/verify_patch.py --patch pat-… --tier T0   # 最快的有用答案
```

**没有任何东西被暂存到磁盘。** `corpus/patch.py::apply_edit` 返回新文本、不写任何文件，而全树检查是在
把那一个文件的 mapping 替换进去之后的已解析树上跑的。所以既不需要每次运行拷贝一棵 7,357 个文件的树，
更重要的是也不存在任何可供删除的目标目录：这个阶梯从不碰 `corpus/snapshot.py`，而它的 `rmtree` 曾被
实测删掉一个装着无关文件的临时目录。

| 层 | 跑什么 | 成本（标注处为**实测**） | 通过条件 |
|---|---|---|---|
| **T0** | 只看被编辑的那个 asset | 约 1.6 秒 | 文件能 parse、`from_mapping` 接受它、`problems_with` 为空、id 通过校验 |
| **T1** | 全树一致性检查 + `build_structure` + `build_index` | 3.4 秒（facilities）/ 26 秒（BIRD）**实测**；索引词法 0.03 秒、暖态语义 0.27 秒**实测** | 按规则 id 没有**新增**发现；`build_index` 不抛异常；`build_structure` 的 problem 计数不上升 |
| **T2** | 在打过补丁的树上跑 metric expression 解析器 | 离线、免费、不需要数据库 | metric `expression` 里每一个裸标识符都能在 `base_table` 上、或经一条已声明 join 解析到 |
| **T3** | `tools/reproduce_observation.py --embed`，agent 模型关闭 | 数分钟，**约 $0** —— 向量缓存 100% 暖，且加一个 asset 只花 **2** 次 embed 调用**实测** | **逐题，不按比率**：没有任何一道题丢失 gold 表 coverage。并报告哪些题变好了。**不适用于只改 `body` 的补丁** —— 见下 |

**T2 不需要活体 catalog，而这是对设计的一处更正。** ADR 0015 把这个解析器放在数据库后面，理由是解析一个
标识符需要数仓。它不需要：语料自己声明了表、列和 join，而 expression 必须与*那些*一致 —— 数仓是 serve
时 `govern/` 的事。`check_closed_domains.py` 是设计给这一层起的名字，而这个文件不存在。

**T3 必须带 `--embed` 跑。** 不带它这次复查只走 lexical，而那些 arm 是带 embedder 测出来的。实际两种
方式各驱动一次同一条 observation：该行记录的是 **1** 张缺失的 gold 表，而只走 lexical 的复查报出 **2**
—— 一个假的「仍然复现」，读起来和真发现一模一样。通道名在每次运行的输出里，而走 lexical 的那次会告警。

**T3 回答的问题比其他几层都窄**，而它的输出每次都说明是哪一个：参考答案要读的那些表又能取到了。不是
说答案对了。在每一张 gold 表**都**被许可的那些 turn 上，实测准确率是 0.7555。

**T3 之上没有任何层。** 对一个 cluster 的问题做定向付费重放、以及一对成对 arm，都是由某个人决定花钱才
启动的东西，两者都没有建。所以一个只改 `body` 的补丁根本没有验证器：`body` 不进检索索引，T3 看不见这次
变更，而记录会点名三种理由里的哪一种适用，而不是报一个通过。

### 按 category 的读数

这个清单上没有 EX。`docs/open-work.md` §3.12 给了理由：MDE 约 2.3pp，而 §1.5 最大的单个 coverage 桶是
7 道题 —— 0.52pp。

| category | 主读数 | 层 | 分辨率 |
|---|---|---|---|
| `false_refusal` | 该 turn 的 `terminal_reason` 不再是 `r_table_not_licensed`，且 coverage 变为真 | T3 | 一道题 |
| `wrong_scope`（coverage） | 逐题的 `all_gold_tables_licensed`；`pulled_in.n_connect` | T3 | 一道题 |
| 许可集合内选错了表 | `licensed` 集合 diff，以及哪些 gold 表缺失 | T3 | 精确 |
| `wrong_value`（定义） | metric 解析器通过 | T2 | 精确 |
| 进入 prompt 的 prose | 新增的内容规则 | T0/T1 | 精确 |

那张表里每一个零都经由 `measure/stats.py::rule_of_three` 报告，于是 `0/53` 渲染成「≤ 5.7%」，无法被
引用成「0% 误拒」。那个函数已经存在。

**没有任何一层读的是答案。** 一条 gold 表全都被许可、答案却仍然错的投诉是语义缺陷，而免费阶梯看不见
它。那种情况下面板会这么说，而不是报一个通过。

### 新增一致性规则

id 沿用 `tools/check_corpus_conformance.py` 的 `RULES` 表。五条里有三条**今天就有非空的实测对象**，
这一点把它们与凭直觉写下的规则区分开。

| 规则 | 谓词 | 活跃发现 |
|---|---|---|
| **V17a** | 一个 metric `expression` 在引擎的 dialect 下能解析为 SQL | BIRD 上 **107 条，分布在 478 个 metric 中的 85 个**：`DIVIDE(…)`、`COUNT(x WHERE y)`、`<condition>` |
| **V17b** | metric `expression` 里每一个裸标识符都能在 `base_table` 上解析到，或在经一条**已声明** join 可达的表上解析到 | **17** |
| **V19** | 任何模型可见的 **`body`** 不许点名一个 `governance.excluded` 的列或 asset。**是 `body`，不是 `summary`** —— `summary` 从不进入 prompt（`serve/context.py`），它进的是检索索引 | **零**，因为两个语料里被排除的 asset 数都是零。加上它是免费的；不可能造成回退 |
| **V21** | 模型可见文本通过 `govern/guard.py::GUARD_RULES` —— 复用它们，不是把它们重述一遍 | **一条**：`public_review_platform/few-shots/fs_public_review_platform_0012.yaml` 携带两个 `U+200B` |
| **V23** | asset id 在全树唯一 | **今天为零**，而这条规则存在的理由是：一个重复 id 能通过一致性检查，然后在 `build_index` 里抛 `ValueError: duplicate index id`（**实测**） |

设计里给 V17a 的计数是 **28**，出自一个只做解析的原型：`DIVIDE(a, b)` 作为 SQL 能解析通过，而它命名的
函数任何 dialect 都没有，所以上线的规则还会去问 `govern/functions.py::PERMITTED_FUNCTIONS`。还设计过
第六条并砍掉了 —— 一个封闭域断言要在 `audit.evidence` 里携带一条观察 —— 因为它没有活体样本、也没有
校准过的误报率，上线只会是一条谁也没法定量的规则。

**V10 和 V12 不是披露规则，不得被当作既有控制来引用。** V10 是「no text discloses how an unreliable
column was made」—— 它为 BIRD 的混淆诱饵而存在 —— 而 V12 管 held-out 问题泄漏。两条都在管基准完整性。
在一个生产语料上它们什么也不管，所以 V19 是这一类里的**第一条**控制，不是对既有控制的加固。

**棘轮。** 既有发现在语料仓库里**按名字**钉住。这个集合可以自由缩小、不可增长，而关闭其中一条会让构建
像新增一条那样响亮地失败 —— 是名字而不是计数，因为 28 条发现和 28 条**不同的**发现是同一个整数。
**在 `../BIRD-corpus` 上实测：**125 条发现，收敛成 **101** 个形如 `(rule, file:asset)` 的钉住身份 ——
其中 24 条发现与另一条共享同一个身份。

### 可比性

两个阻碍，都是**实测**：

1. `comparability_keys()` 是 50 个名字，**没有一个含 "corpus"**，所以一个处理变量就是语料的 arm 无法
   声明它，而 `register/arm_profiles.py` 会把它判为 `cannot_evaluate`。
2. `corpus_content_hash('../BIRD-corpus')` 在 HEAD 上是 `6e5c7b4be83d5682…`；`arms.toml` 在四个 arm 上
   都声明 `86ed1dbf…`。中间那两个 commit 只加了 `LICENSE` 和 `README.md` —— 没有任何 asset 变化 ——
   摘要还是动了。**所以今天用 `--arm v4` 跑当前 checkout 会被拒绝。**

所以：一个可比性 knob `corpus_release`，命名一个 **tag** 而不是一个目录。补丁持续落地；arm 钉住
release。再加 `ArmProfile` 上的 `hypothesised_effect` 和 `readout`，它们给
`eval/power.py::require_power` 提供了 `open-work.md` §3.10 记录它缺失的那个调用者 —— 到那时，一个探测
不到自己假设的 arm 会在花掉任何东西之前失败。

**但不要围绕一对成对 arm 来规划 release。** 约束节奏的是可探测效应的存量，而它快见底了。T3 能看见的
全部就是 coverage 欠账 —— 79 道 gold 表从未被许可的题 —— 最多值 +5.85pp，按实测 EX 折算 +3.98pp，对上
EX 的 MDE 2.33pp：**整个欠账里只有 1.7 个可探测的 release。** 而且每个 release 需要**两条**新 arm，
不是一条，因为磁盘上没有任何一对能通过 `knobs_comparable`（上面第 1 条阻碍就是原因），所以第一个 release
得自己买一条控制组：约 150M 输入 token、约 104 分钟。

因此 **release 的头条读数是 T3 的逐题 coverage delta** —— 分辨率一道题（0.08pp），成本约 $0 —— 而一对
成对 arm 是**代码**变更需要定价时才买的东西。`ArmProfile.hypothesised_effect` 存在的部分目的就是让
这个拒绝自动化：一个声明 +0.5pp 假设的 release arm 会在花掉任何东西之前被 `require_power` 拒绝。

**这些声明里只有一项真正被 CI 抓住。** `tools/check_declared_is_consumed.py` 有四条规则，覆盖 knob、
record 字段和 state 通道。`corpus_release` 是一个 knob，所以缺少读取方会按名字让构建失败。
`ArmProfile.hypothesised_effect`、`.readout`、以及存储的 SQLite 列住在那四条规则都不遍历的命名空间里
—— 所以对它们而言，「声明了但没有读取方」是由评审而不是由 CI 守着的。补上它是再加一条同形状的规则；
在那之前，这一段就是那个控制。

---

## 11. CI

### 引擎仓库 —— `.github/workflows/ci.yml` 的 `test` job

```bash
uv run --frozen python tools/check_imports.py    # LAYERS 里有 feedback
uv run --frozen pytest -q -rs                    # 其中包含 tests/feedback 与 tests/corpus
```

回流路径没有任何属于自己的 CI 步骤，也不需要。`check_imports.py::undeclared` 会在 `LAYERS` 漏掉一个包
时失败，这就覆盖了新增的那一层；其余是全套 `pytest`，而它是好几个 `tools/` 检查唯一的调用者，所以其中
一个坏掉时它会响亮地失败。

### 语料仓库

这是工程师的 commit 要经过的那个 CI，之所以在这里规定它，是因为检查器住在引擎里。它**不得**需要模型
凭据或数据库。

```bash
uv run --frozen python ../governed-bi/tools/check_corpus_conformance.py --corpus-dir .
uv run --frozen python ../governed-bi/tools/check_ratchet.py --pins .conformance/pins.txt
uv run --frozen python -c "from governed_bi.retrieve import build_index; ..."   # T1：它必须能起来
```

### 两边都不跑什么

T3，以及任何要花钱的东西。T3 需要一条携带 gold 语句的 observation —— 那是操作员存储里的一行，不是本仓库
里的一个 fixture —— 还需要一个暖的向量缓存。它跑起来免费，而它是手动跑的。

---

## 12. 界面

交付了一块屏幕，没交付两块，以及一个拥有全部文案的模块。

### 12.1 新增与改动的文件

| 路径 | 做什么 |
|---|---|
| `ui/app/review/page.tsx` | steward 的路由 |
| `ui/components/review/review-surface.tsx` | 双栏外壳 |
| `ui/components/review/review-queue.tsx` | 队列（§12.4） |
| `ui/components/review/cluster-panel.tsx` | 详情面板 |
| `ui/components/review/evidence-bundle.tsx` | 证据（§12.5） |
| `ui/components/review/reproduce-panel.tsx` | 第 6 块（§12.6） |
| `ui/components/review/asset-diff.tsx`、`ui/lib/asset-diff.ts` | 单字段 diff，以及它背后的词级 diff（§12.7） |
| `ui/components/review/decision-bar.tsx` | 四个动作（§12.8） |
| `ui/components/review/handoff-panel.tsx` | 导出后的 bundle 命令与 manifest |
| `ui/lib/review-copy.ts` | §3、§5、§12 里**每一句**面向用户的文案 |
| `ui/lib/schemas.ts`、`types.ts`、`api-client.ts`、`hooks/queries.ts` | zod schema、`z.infer` 类型、client 方法与 hook |
| `ui/scripts/check-asset-diff.ts` | diff 的最小性，密闭运行 —— `npm run check:asset-diff` |
| `ui/components/layout/nav.tsx` | 一个 `LINKS` 条目，**Review** |

**没有建。** 下面每一个路径在代码树里都不存在：`ui/app/reports/page.tsx`、
`ui/components/answer/category-picker.tsx`、`ui/components/reports/report-list.tsx`、
`report-status.tsx`、`re-ask-button.tsx`、`ui/lib/category-taxonomy.ts`、`ui/lib/my-reports.ts`、
`ui/scripts/check-review-copy.ts`。`ui/components/answer/raise-note.tsx` 在，而且没有被重写。

**`ui/lib/review-copy.ts` 本该把「诚实文案」规则变成机械的，而它只做了一半。** 每一句都住在那里、按状态
索引，这正是让这条规则可检查的前提 —— 但检查没写。`ui/scripts/check-review-copy.ts` 不存在，所以没有
任何东西断言 observation / patch / decline 三个状态联合的每一个成员都有一句文案，也没有任何东西禁掉
`robust`、`seamless`、`comprehensive`，以及这个项目最在意的两个：**`automatically`** 和
**`will be fixed`**（除否定用法外）。这个模块让检查成为可能；今天靠评审来执行它。

### 12.2 分析师：两次点击完成捕获

**没有建。** `raise-note.tsx` 仍然是通过一个文本框提交 note，下面这三个状态一个都不存在。写下它是因为
这一轮真正使用的输入 —— 一份 eval artifact —— 里根本没有分析师，而它将来需要的那个形状最容易被做错。

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
最近的地方，而它点名的是一**类**对象，绝不是一个实例。定位 asset 是 steward 的工作，§12.4 给他们机械。

**回执文案原文** —— 它移除了产品里今天就存在的一个谎（`"Filed. It is on the pending list."`，而那个
列表从不被清空）：

> 已提交。数据管理员按最早优先复核这些。这台引擎不知道你是谁，所以不会有人给你发邮件 —— 到
> **My reports** 看结果。

### 12.3 `/reports`：分析师之后看到什么

**没有建。** 不存在 `/reports` 路由。只有一个 principal，也就没有第二个读者需要一个按读者划分的列表。

`GET /observations`，按 `localStorage` 里的 id 过滤。**`ui/lib/my-reports.ts` 是浏览器记忆，而页面
就这么说** —— 只有一个 principal、没有用户存储，所以在这里发明一个按用户的概念会是一个并不存在的边界：

> 这个列表由这个浏览器记住，不由你的账号记住。这台引擎不知道你是谁，所以换一个浏览器会看到不同的列表。

每一行：问题、提交时间、category 标签，以及一个状态 chip，其句子就是 §5 里该状态对应的那一句。
`landed_verified`、`landed_matched` 和 `retrieval_verified` 带 **Re-ask** 动作（§5）。

### 12.4 `/review`：steward 的屏幕，钱在这里

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

**按 cluster 最早成员的时间排序，不按规模。** 今早的三条 cluster 并不比等了一个月的一条更紧急，而按
规模排序会让长尾永久不可见。

cluster 标题下的说明常驻，因为这个分组是结构化的 —— 键就是 `(category, schema)`，再无其他，没有
embedding、没有模型、没有成本：

> 按被报告的问题类型、以及那些 turn 被允许读的表分组。这里没有任何东西读过那些问题并判定它们是同一个
> 意思 —— 在把它们当作一个问题处理之前，先看看这些行。

**而实测的弱点就写在它下面。** 在导入的那批失败上，最大的簇是 **3**，而只有 **49%** 的行落在任何一个簇
里。设计当初是靠「簇会很大」来论证批处理的；它们不大，所以这是一个带可选分组的列表，绝不是一条批处理
流水线。

**空状态：** `"Nothing to review. Every observation filed on this server has been triaged."` ——
一句与「没人提交过任何东西」**不同**的话，因为那一句和「全部已分诊」是两个不同的事实，
而把其中一个读成另一个，正是一个队列被弃用的方式。

**刻意不在队列里的：** SQL、ledger、record。全都只有一次点击之遥。一个展示证据的队列是一个没人扫的队列。

### 12.5 证据包：六个块，全部在决策之上

`ui/components/review/evidence-bundle.tsx`。每选中一个 cluster 取一次。**设计规定了七个块，交付了
六个**，因为一份 evaluation artifact 不记录其中两个要展示的东西。

在一切之上、当这一行携带 held-out 问题时：一张警告卡，不是一句说明。问题文本来自 held-out 划分，而一个
据它写语料 prose 的人会不可见地污染基准。一致性规则 V12 抓得住逐字引用；改写则完全无法被检测，所以最后
一道防线是一个知道自己在读什么的读者。

1. **问了什么、回来了什么。** 问题原文；`outcome` 和 `refused_by`；该状态的标签及其 §5 句子；有驳回
   理由或阻塞备注时一并给出。
2. **grader 说了什么。** category、quality flag、note —— 以及并排的参考指纹与产出指纹。这是设计里
   「读者说了什么」那一块，被换成了一个可证伪的东西：一条导入的行没有读者，而一次指纹不匹配不是意见。
3. **那条语句。** `generated_sql` 放在已有的只读 `<SqlBlock/>` 里，而当这一行携带参考语句时，**参考语句
   就并排在旁边**。读者没有 gold 答案；一条基准行有，这让它成为这一页上最强的证据。一个没跑任何语句的
   turn 会这么说 —— 那是它自己的一个缺陷类别，不是一个缺失字段。
4. **参考答案需要而没拿到什么。** `missing_tables`，也就是那个最直接的陈述；`schema_ranking` 不在
   artifact 里，所以设计里「gold schema 排第 4」和「它从来不是候选」在这里分不开。`licensed` 和被路由到
   的 schema 藏在 `atLeast(mode, "engineer")` 后面。空列表才是有意思的那种情况，而它会这么说：参考答案
   要读的每一张表都能取到，而答案仍然是错的 —— 那是免费阶梯看不见的语义问题。
5. **哪些语料 asset 在 context 里** —— 这就是在这里无法存在、并且直说这一点的那一块。在 v4 arm 上，
   `facet_hits`、`pulled_in` 和 `turn_id` 在 **1,351 行里有 0 行**（**实测**），所以这个位置放的是一句
   话而不是一张表。一个渲染成空的块读起来是「我们没费这个心」而不是「没有这个数据」，而这就是这个位置
   要留着的理由。
6. **复现器**（§12.6）。

仅工程师可见、放在最下面的：**provenance** —— arm、question id、语料内容哈希，以及提交时间。设计里的第
七块，即完整的 `GET /audit/turns/{id}/trace` 载荷，因为第 5 块同样的理由而缺席：一条导入的行没有
`turn_id`，也就没有 trace 可取。

**它刻意不展示什么：结果行。** `result_table` 按 ADR 0006 §11 只在实时时存在、不在 record 里，所以没有
东西可展示，而一个为它留了位置的面板会读作「那些行没被保存」而不是「那些行不保留」。

**披露情况。** 这个界面只读反馈存储，别的都不读，所以它能披露的恰好就是 §7 的允许清单放行的那些，而
steward 更宽的视野就是挂载 steward 动词的那同一个 `GOVERNED_BI_FEEDBACK_ADMIN` 开关。这块屏幕上没有
按 grant 的收窄。设计里有过一个 —— 第 5 块里经 `visible(get_session())` 读取语料 asset —— 而第 5 块不
存在，所以那个收窄也不存在。

### 12.6 复现器

steward 需要一个存储给不了的事实：*这件事现在还发生吗？* `cannot_reproduce` 是一个驳回理由，所以它必须
可核。

**它是一条命令，不是一个按钮，而这才是诚实的形状。** 这次复查会在 agent 模型关闭的情况下把问题重新走一
遍路由，那需要一个数仓连接和一个暖的向量缓存 —— 浏览器两样都没有，而服务器两样都得先配好。刻意没有对应
的 HTTP 动词：一个在多数部署上会 404 的按钮，比一行能被人复制的命令更糟。`--embed` 就在被复制的那条命令
里，而且不是可选项（§10）。

**它不花钱**，而这是对设计的一处更正：对一条导入的失败，「这件事现在还发生吗」是一次 answering 模型关闭
的 coverage 复查，不是一次模型调用。

一个绿色结果许可了什么，常驻在面板上 —— 参考答案要读的那些表又能取到了，而不是答案对了 —— 而这次复查
根本无法回答的那三种情况被点名而不是被藏起来，因为一个提供了无法回答的命令的面板，正是有人据此断定工具
坏了的方式。

### 12.7 diff：一个字段，逐词，绝不是文本 diff

```tsx
export function AssetDiff({ assetId, fieldPath, was, becomes }: {
  assetId: string;
  /** "summary" 或 "body" —— 一个补丁能携带的仅此两条路径（§6）。 */
  fieldPath: string;
  was: string;
  becomes: string;
}): React.JSX.Element;
```

**不是对 YAML 做文本 diff，而这不可商量** —— 它由 M1 推出。`to_mapping` 省略默认值，所以 `governance`
和 `reliability` 在取默认值时根本不在文件里，于是设置其中一个时文本 diff 会显示一处**虚假的新增**；
而 PyYAML 在 80 列处重排，所以对一个词的 `summary` 改动做文本 diff 会变成整段 diff。一个补丁携带的是
一条字段路径加两个字符串，所以 diff 就在那上面做，别的都不碰。

**是哪个字段写在那一行上，因为这两个字段做的事不同。** `summary` 喂检索索引，`body` 喂模型的 prompt
—— 改 `summary` 改的是*什么会被找到*，改 `body` 改的是*模型读到什么*。一个要判断某次编辑是否修好了一次
coverage miss 的评审者必须知道自己在看哪一个，而只显示词的 diff 会让他去猜。

**颜色不是唯一的信号。** 每一段变化还带一个 `+`/`−` 标记。红绿 diff 对色盲评审者不可读，而这正是做决定
的那块屏幕。

**「+0 −0 词」是两种情况，给两句不同的话。** 替换值可以就是那里已有的文本，也可以只在空白上不同。两者都
算零个词；只有第二种是 steward 真的敲进去、而且提交不了的值，`classifyEdit` 点名是哪一种。

`ui/scripts/check-asset-diff.ts` 钉住的性质是**最小性**，不是「它产出了 span」。一个「一个词移动就把整句
标成改过」的贪心走法照样能渲染、照样看起来像 diff，却悄悄让评审者看不见那处编辑。这个检查是密闭的 ——
它 import `lib/asset-diff.ts`，不需要引擎、不需要语料、不需要网络。

**这个组件渲染不到两样东西**，而两者都是拒绝而不是缺口：任何形式的 `governance`（一个能提议排除的屏幕
**就是**那个「其缺席即控制」的工具 —— ADR 0015 §8），以及对一张表内联 `columns` 的任何结构性改动（§6）。
两者都不是可编辑的字段路径，所以都到不了它这里。

### 12.8 决策条

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

- **Draft a change** → §12.7 允许的字段集的编辑器，然后 `POST /patches`。
- **Decline** → 一个覆盖八个 `decline_reason` 成员的 `Select`，每一项把它在 §5 的那句话渲染成
  **该选项的描述**，好让 steward 在选之前就读到分析师将要读到的话。不接受纯自由文本驳回：一个没人能
  聚合的理由是一个没人复核的理由。
- **Fold into another observation** → `duplicate`，并加入那一条的 patch 集合（§5 —— 否则落地时受影响的
  observation 会算成一条而不是两条）。
- **Escalate。** 没有可以升级**给**的人 —— 一个 principal，没有指派人。所以它不是一个路由动作，它是
  **一个有名字的状态**：`blocked_on_a_person`，加一行必填说明。面向分析师的文案：「正在等一个人：
  <说明>。没有任何东西在自动推进这件事。」指派人下拉框被拒绝了：没有用户存储可以填充它，而一个只有
  一项的下拉框是对工作流的一个谎。

### 12.9 显示模式

仅工程师可见的那些部分 —— §12.5 的 provenance 块，以及它那行 `licensed`/被路由到的 schema ——
藏在已有的 `ui/lib/display-mode.ts::atLeast(mode, "engineer")` 后面。不发明任何新东西：那个模块本来就
带着「显示模式不是安全边界」的警告，而这里不把它变成一个。

---

## 13. 回流路径不做什么

- **它不认证任何人。** 单一 principal，而碰到端口仍然就够了。admin 动词以未挂载状态发布；那是一个部署
  开关，不是一个身份。
- **它不知道是谁提交了一条 observation。** 没有 `filed_by`，因为 `api/auth.py` 返回单一 principal，
  而在这里加一个按用户的字段会是一个并不存在的边界。也没有任何东西告诉读者他那条投诉后来怎么了，因为
  根本没有面向读者的界面（§12.3）。
- **它不宣称一个落地的补丁修好了那个问题。** 见 §5。
- **它不把 prose 注入扫描当作闸门。** V21 复用 `GUARD_RULES`，V19 覆盖一个具名披露。除此之外，姿态是
  ADR 0006 的：名字可以到达 prompt，而点名它的查询被拒。一个企业分叉必须自己决定这够不够。
- **它不自己撰写候选变更。** 补丁由 steward 起草、由阶梯检查；本仓库里没有任何东西决定语料该说什么。
  本该做这件事的 agentic 流水线被砍掉了（§2）。
- **它不让本仓库变成策展人。** 语料由人拥有、在本仓库之外受版本控制、且无法从本仓库重建。整个环里对语料
  内容唯一的一次写入，是某个人在那个仓库里的 `git commit`（§8）。
