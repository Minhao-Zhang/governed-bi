# 对抗审计 2026-07-31 · `main` @ `83e131c`

八个独立视角的对抗 review,worktree 隔离、只读。**五个交了报告,三个撞上会话限额** —— 其中两个(eval integrity、test quality)已重跑,analysis correctness 未重跑。

**每一条都标了我的复核状态。**「我复现过」= 我自己跑出来的,不是读报告。分档标准是**证据强度**,不是严重度。

---

## 一 · 我亲自复现的(七条)

### 1 · CI 红了 43 小时,四道门在黑着 —— 而且我贡献了其中一次

```
2026-07-31T23:06  83e131ca  failure   ← 我推的
2026-07-31T13:26  214b6780  failure
2026-07-30T14:59  f55bde08  failure
2026-07-30T04:09  49536acd  failure
2026-07-30T03:07  9ac072bb  success   ← 最后一次绿
```

HEAD 那次的步骤明细:

```
 7 Lint (ruff)                                      failure
 8 Type-check (mypy, governance spine)              skipped
 9 Check generated docs are current (eval metrics)  skipped
10 Check the OpenAPI contract is current            skipped
11 Run tests                                        skipped
```

**九个 import 排序错误挡住了整个测试套 + mypy + 两道 drift 契约门。**其中 OpenAPI 那道,`ci.yml` 自己的注释说前端仓库照它构建、而且**已经用同样方式漂移过一次**。

外加:**80 次 workflow run 全是 `push`,`pull_request` 触发器从来没跑过。**`ci.yml` 第 3 行写着「Enforce the PR-gated governance narrative the docs describe」—— 没有分支保护,8 个 commit 落在红树上。**那不是门,是事后通知。**

**先别急着 `--fix`。**修掉之后就查不了「为什么 43 小时没人发现」了。

### 2 · `[routing]` 那张 TOML 表是死的

```python
run_datalake.py:3682  p.add_argument("--route-top-k", type=int, default=10, ...)
run_datalake.py:3692  p.add_argument("--no-llm-pick", action="store_true", ...)
                      ↓ 无条件覆盖 load_settings() 的结果
run_datalake.py:2758  schema_route_top_k=route_top_k,
run_datalake.py:2759  schema_route_llm_pick=route_llm_pick,
```

设 `[routing] top_k = 3` 对 driver **完全无效**,永远 10。

**紧挨着下面三行**,同一个 `replace()` 里的第三个 knob 用了 `None` 哨兵,注释逐字描述这个失效模式:「the knob was recorded in the manifest, guarded on resume, and used as a comparability key **while being permanently 12 — three guards on a value nothing could change**」。`--workers` 也用了哨兵。**就这两个漏了,而它们正是 `[routing]` 那张表当初要解决的。**

### 3 · 算不出 join 计划 → 盖成「join 完美」

`analyst/governance.py:832-835`:

```python
except ValueError:
    join_ids, min_confidence = [], 1.0
```

`plan_joins` 只在两种情况抛 —— 表不在 join 图上,或**要连的表分属不连通分量**。两种都是「不知道这些表怎么关联」。我实跑:

```
plan_joins raised: not table nodes in the join graph
  -> min_join_confidence = 1.0   (LOW_CONFIDENCE_JOIN = 0.7)
  -> low_confidence_join flag = False
```

**不是丢信号,是把信号反过来。**这个数进终端 `final` 事件、进 UI。`planner.py:5-6` 还写着「路径上最低的 join confidence 传播到 reliability stamp」。

同族:`JoinAsset.confidence = None`(未评分)在三个消费点各自倒向「没问题」—— planner 读成 1.0、`/health` 不计入、schema card 不加标注。

### 4 · `BUILD_COMPLETE.json` 盖到这次没构建的臂上

marker 自己的声明(`run_datalake.py:238-241`):

> Resume, staging seed, skip, and promote treat this — **not "any `*.yaml`"** — as the durable completeness contract. A kill mid-build leaves YAML without this marker; **that tree is debris**.

而 `:538` 就是用 `_has_yaml` 推导它。`:2698` 的 `roots` 覆盖全部四臂,不管 `--arms`。

序列:`--arms baseline,curated` 跑一半杀掉 → 跑 `--arms baseline` → 第二次把 `corpus_curated/X` **盖成完成** → 之后 `--arms curated --resume` 跳过重建,**拿半份被杀死的语料当治疗组打分**。付费跑路径,污染 treatment identity 本身。

### 5 · 代码做了它自己注释说不要做的事

`stages.py:174-181`:

```python
if refused_by is None:
    # Nothing produced and nothing recorded about why. Genuinely unknown, and
    # worth surfacing as such rather than defaulting into `refused` — a
    # silent no-op is a different bug from a considered refusal.
    ...
    return Outcome.refused, None, True     # ← 就是 default 成了 refused
```

`Outcome` 没有 `unknown` 成员。而这个枚举的 docstring 讲的正是同一类事故(crash 被 `refusal_rate` 吸收)。**同一个家族,上次没覆盖到的那一处。**

### 6 · `index_cache` 声称按内容 key,实际按 asset id

```
asset edited: tbl_beer_factory_customers
  description: 'One row per customer...' -> 'COMPLETELY DIFFERENT TEXT'
  cache key identical: True
```

`corpus_index_key` 返回 `tuple(sorted(a.id ...))`。改文本不改 id → 缓存命中 → BM25 分词、schema doc、embedding **全是编辑前的,直到进程重启**。

而 `reload_corpus` 自己的 docstring 把这个缓存当成安全理由:「Index cache entries are **content-keyed, so stale embeddings are not reused for edited assets**」。**「写后立刻可见」这条承诺,检索那一半不成立。**

测试没抓到是因为:一个比**两份完全相同**的 corpus,另一个变的是 schema 名(**也就是 id**)。**没有一个测试改文本而不改 id** —— 正好是 `/corpus/edit` 那个 case。

### 7 · graded delivery 能执行未声明的跨 schema join,D15 的承诺不成立

`middleware.py:451-453` 逐字写着:

> an undeclared cross-schema join is **never executed nor graded-delivered** (D15 refuses + escalates), so it cannot be self-authorized.

我实跑同一条 SQL:

```
D15 detector: undeclared cross-schema join?  True
guardrails.check() on the SAME sql: passed=True  failed_layer=None
```

**D15 规则只存在于 `wrap_tool_call`,`check()` 里零命中。**而 graded delivery 的复检**只跑 `check()`** —— `governance.py` 自己的注释还说「Graded delivery executes **outside wrap_tool_call**」。

同一条 SQL,在一个执行点被硬拦,在另一个执行点被执行。

**触发条件**:`grade_semantic_failures=True` **且**多 schema 进 shortlist。默认 pooled 跑有 `route_llm_pick=True` 会收敛到单 schema,**所以默认配置不暴露**;`--no-llm-pick` 或 local toml 开 `grade_semantic_failures` 就暴露。


### 8 · headline 率和它的显著性检验算在**两个不同的总体**上,artifact 自己和自己矛盾

**这一条最直接威胁那次付费跑。**

- 每臂的 headline `ex_no_twin`(`statistics.py:1257`)分母是 **`n_no_twin_gradeable` = 1085**
- 而 `comparisons[].no_twin`(`statistics.py:533`)建 `twin_free` 时**没有 `is_gradeable_eval_row` 过滤**,`n_shared` = **1236**

实测:

```
twin-free (all rows): 1236   twin-free AND gradeable: 1085   difference: 151

  curated      vs curated_sme  discordant=115  of which ungradeable=  7
  baseline     vs curated      discordant=354  of which ungradeable= 25
```

那 151 行是 frozen-constant(125)加 order-sensitive(26)的 gold —— **生成器永远赢不了的题**,项目正是为此把它们排除在 `gradeable` 外。但它们**在显著性检验的分母里**,并且贡献了 7–25 个不一致对。

**同一份 `summary.json` 里,同一个预登记量有两个数:**

```
curated  ex_no_twin=0.59078  →  curated_sme  ex_no_twin=0.59447   (1085 行, +4 题, +0.37pp)
对应的 no_twin 比较块:        net_questions=1, net_rate=0.00081    (1236 行, +0.081pp)
```

**带 p 值的那一个,算在被排除的总体上。**

在花 2 亿 token 之前必须修 —— 否则产出的 artifact 会像 20260730 这份一样,对自己的头号指标给出两个互相矛盾的数。

### 9 · 脏工作树在 resume 上没有守卫

```
  git_sha                in RESUME_DRIFT_KEYS? True
  dirty                  in RESUME_DRIFT_KEYS? False
  diff_sha256            in RESUME_DRIFT_KEYS? False
```

M4 的 N13 加了 `dirty` / `diff_sha256`,但它们进的是 `MANIFEST_OPERATIONAL`,**不在 resume drift 键里**。

所以:改一行代码不提交 → `--resume` → `git_sha` 没变 → `run_datalake.py:1394` 那道**致命**守卫不触发 → 「两个 harness 版本的行被平均进同一个臂」,**正是那道守卫点名要防的事**,而且不留任何痕迹。

**在 57 schema / 4 臂 / 2 小时的跑上,中途改点东西再 resume 是最可能发生的操作员动作。**

---

## 二 · 机制成立、我未逐条复现(挑重要的)

| findings | 为什么可信 |
|---|---|
| **`turn_id` = 客户端给的 `session_id` + history 长度,run log 按它 UPSERT** | 两个并发 `/chat` 同 session 同长度 → **两次执行一条审计记录**。对照:`graph_app.py:136-150` 早就给 clarify key 做了加盐哈希(AUDIT S7),run-log key 没有 |
| **`ServeRuntime` 被逐轮改写** | 今天不可达(serve 每轮重建 rails),**但修「每轮重复 embed」最省事的做法就是把图提到 stack 上,那一笔正好让它变活**。两条不能分开修 |
| **每轮 chat 重新 embed 全部 schema doc + 深拷贝整个 corpus** | 实测 5 轮 = 5 次。`embed_schema_documents` 绕过了 `RetrievalIndexCache.schema_docs`,还要付那个自称「serve 非模型 CPU 的 55%」的深拷贝 |
| **`token_sum` 把 `{0,0,0}` 当测量值** | 而紧挨着的 `cost_est_usd` 正确返回 `None`,**同一行记录里两个字段对「测量发生了吗」给出相反答案** |
| **`governance_ledger: []` 兼表「审计过,零动作」和「台账丢了」** | 三行之上的 `strip_stage_events_for_log` 明确拒绝这种处理(`None` in `None` out) |
| **`question_pool_hash` 的 `"empty"` 哨兵** | 和 `corpus_content_hash` 的 `"unknown"` 同形状 —— **后者被 `comparable()` 明确拒了,前者没有** |
| **`decoy_touch` 把「没测」塌进通过值** | 邻居 `routed_hit`/`routing_escaped`/`pick_hit` 全是三态,`SUMMARY_COUNTS` 里其他五个都有 `*_observed`,**唯独它没有**。而这是治理主张所依赖的那个数 |
| **AUDIT S5 的 prompt-injection 消毒只在 assemble 那条路上** | `read_notes` / `grep_notes` / `search_corpus` 三个工具直出原始 corpus 散文,**而且不限长**。`sanitize_note_text` 自己的 docstring 声称它是「the one place」 |
| **`search_corpus` 的非表资产不受 routed 边界约束** | few-shot 的 gold SQL、metric 表达式、note 散文跨 schema 返回;`grep_notes(".")` 是全湖 note dump |
| **C5 excluded-identifier 在 serve 上结构性失效** | 三个工具从 `for_analyst()` 后的 corpus 算排除词集 —— 而那个视图**已经把 excluded 资产删了**,所以词集恒空 |
| **无信号问题上 routed 范围 fail-open 到全湖** | `shortlist_schemas` 无命中时返回全部 schema(对 router 合理),**而同一个值被当作授权边界复用** |
| **`ci.yml:29` 跑裸 `uv sync`,会静默重新解析并重写 lock** | 版本**边界**有守卫(我自己证伪过 constraint 确实绑),**确切解析结果没有**。`--locked` 才是 |
| **一批 docstring 承诺不成立** | `atomic.py` 声称四个 artifact 都走原子写 —— `summary.json` 是裸 `write_text`,`generations.*.jsonl` 是 append+flush(它自己的 docstring 说「`flush()` without `fsync()` is deliberate」,**两处直接打架**);`build_manifest` 说 knob 参数一律必填 —— `grade_semantic_failures` 有默认值;`metrics.py` 说三个 artifact 都有「emitted-but-undeclared」测试 —— **row 那个不存在**;四个安装 extra(`api`/`agents`/`openai`/`tracing`)**全都不存在**,而 `api/__init__.py` 自己说「there is no `api` extra and never was」 |

---

## 三 · 有价值的否定结果

对抗 review 最容易变成「一堆听起来可怕但站不住的东西」。这几条是**查过、没问题**,记下来免得重复挖:

- **秘密:干净。**历史里从没提交过 `.env` / `.local.toml` / 密钥;`sk-` / `AKIA` / `ghp_` 全域零命中;`runs/` 和 `docs/experiments/` 里的 `password` 是 AdventureWorks 的**列名**。DSN 那个「默认带密码」的老问题已修。
- **L4 绕过:40 个构造全部拦住。**相关子查询、`EXISTS`/`IN`/`ANY`、CTE(含嵌套与遮蔽)、`UNION ALL`、`LATERAL`、`TABLESAMPLE`、带点的引号表名、表值函数、`pragma_table_info`、`sqlite_master`、`pg_catalog` —— 每一个越界表引用都在 L3 或 L4 被拦。
- **ContextVar 无泄漏。**五对 `bind`/`reset` 都在 `finally` 里,LangGraph executor 会 `copy_context()`。
- **`eval/parallel.py` 的 per-worker 隔离是真的。**每个 worker 自己的 connector / gateway / solver / 编译图 / session_id;共享的都是只读。
- **约 60 条不变量经查成立** —— 原子写本体、`corpus_release_hash` 的 no-subprocess、`COMPARABILITY_KEYS` 确实派生自 `MANIFEST_KNOBS`、`arithmetic_floor_for_arms` 三个值我重算过、`quotable` 在未知上确实 fail-closed、`summarise_rows` 确实返回 87 个字段且与 `SUMMARY_FIELDS` 相等。

---

## 四 · 一条贯穿性的观察

**几乎每一条发现,旁边三五行就有一段注释准确描述了这个失效模式。**

- `_BUILD_COMPLETE_MARKER` 的声明禁止「any `*.yaml`」,下面就用 `_has_yaml` 推导它
- `schema_pick_max_columns` 的 None 哨兵注释讲透了「三道守卫守着一个改不动的常数」,隔壁两个 knob 就是那样
- `stages.py` 的注释说「不要 default 成 refused」,下一行就 default 成了 refused
- `strip_stage_events_for_log` 说「`None` in `None` out」,三行上面的 ledger 版本返回 `[]`
- `comparable()` 明确拒绝 `"unknown"` 哨兵,同一个函数放行 `"empty"` 哨兵

**这个仓库非常清楚自己会怎么坏。**问题不是认知,是**修的时候只修了手头那一处,没有把同一条规则推到相邻的位置**。

这比任何单条 bug 都更值得作为一条工作方法记下来:**下次修一个 fail-open,先 grep 同一个形状的其他实例。**

---

## 五 · 还没做完的

- **analysis correctness** 那个视角撞限额后没重跑 —— `bird_basis.py` / `statistics.py` 的算术还没有被独立对抗过(M5 review 覆盖了一部分)。
- **eval integrity** 与 **test quality** 已重跑,结果未回。test quality 那个做的是**变异测试**(改 12 个承重函数看测试红不红),它的结果会直接告诉我们这 1740 个测试里有多少是摆设。

---

## 六 · 变异测试:24 个变异,8 个存活

最后一个视角(test quality)交了。做法是**改 12+ 个承重函数的语义,跑全套,看有没有测试变红** —— 活下来的就是洞。

**我亲自复现了最重的两个。**

### M-1 · L2 函数黑名单可以静默缩水(我复现了)

删掉 `readfile` / `writefile` / `load_extension` / `system` 四个:

```
after deleting them: readfile blocked=False
1740 passed, 10 skipped, 1 xfailed
```

**`SELECT readfile('/etc/passwd')` 从被拦变成放行,全套 1740 个测试没有一个发现。**

九个条目在**任何测试文件里零命中**:`current_user` `current_database` `current_schema` `session_user` `set_config` `load_extension` `readfile` `writefile` `system`(我逐个确认它们**今天确实被拦**,所以这是覆盖洞不是活漏洞)。`test_guardrail_function_denylist.py` 只盖了 `pg_*` / `lo_*` / `dblink` 前缀那几族,而且**没有任何契约测试钉住 `_FORBIDDEN_FUNCTION_NAMES` 的内容**。

按模块自己的注释,**L2 是唯一看得见这些的层** —— 一条不引用任何列的 `SELECT fn(...)` 对 L3/L4/L5 都是隐形的。

### M-2 · `quotable()` 唯一挡「好看结果」的那道门,不可能被触发(我复现了)

把阈值抬到够不着 → **0 红**。原因是结构性的,`tests/test_eval_index.py:101-104`:

```python
for _arm, _s in (summary.get("arms") or {}).items():
    for _k, _v in _MEASURED_FREE_PASSES.items():
        _s.setdefault(_k, _v)          # 三个计数器全是 0
```

每个 fixture 的每个臂都被塞进全零计数器,所以 `worst / n` 恒等于 0,**永远 > 0.10 不了**。

fixture 的意图是对的(注释写着「a fixture standing in for a real run carries what a real run always writes」),**副作用是那道门在这个文件里不可能被测到**。

而 agent 指出的不对称才是要害:**`quotable()` 的其他每一道门都有触发测试** —— `crash_rate=0.04`、`n_re_served=12`、低于下限的 `n_questions`、未测的 `crash_rate`、不可读的 manifest。**唯独这一道没有,而它正是挡「结果好看得可疑」的那一道**(AUDIT E2/C8 加它就是为了终结这种不对称)。

### 其余六个存活的

| # | 变异 | 后果 |
|---|---|---|
| **H3** | 不再抹掉客户端可见 ledger 的 `reason` | libpq 会把出错语句原样嵌进来(`LINE 1: SELECT ...`),可能回显问题字面量与 PII;唯一的测试 fixture **两条都不带 `reason` 键**,分支进不去 |
| **H4** | `Gateway.execute` 的 `max_rows × 10` | 行上限只在 connector 层被断言,**没有任何测试在 `Gateway` 这个接缝上验截断** —— 而类 docstring 称这里是「every query flows through」 |
| **H5** | always-note 字符预算完全不生效 | 两个测试都传 `char_max=10_000`,**永远不 binding**。兄弟项 `global_max` 是有覆盖的 |
| **H6** | `funnel_stage` 的 `or` 改 `and` | 测试 fixture 里 `routed_hit` 和 `pick_hit` **永远取同一个值**,析取从没被行使。同一个谓词的另外两份拷贝都有覆盖 —— **只有这一份漂了** |
| **H7** | `schema_route_degraded` 硬编码 `False` | 全仓测试**零命中这个字符串**。它存在的意义(AUDIT R8)就是让 embedding 端点挂掉可见 —— 而 embedding recall@3 是 0.70,BM25 是 0.35,**静默降级会腰斩路由召回** |
| **H8** | 四个「一条测试之差」的薄边 | L4 空 `allowed_tables`、`top_k`、graded-delivery 的两处 —— 都只有一条测试拦着 |

### 还有三处「函数有测试、调用点没有」

- **`eval/bird_basis.py:223 schema_misroute_report`** —— 在 `__all__` 里、有测试、**被 `m5-delivery-evidence.md` 当证据引用**,而 `bird_basis_report()` **根本不调它**。**那张误路由表只由测试产出。**
- `eval/treatment.py:281 treatment_reasons` —— 有测试、有导出、`src/` 里零调用;台账用自己的 `_undelivered()`。**一份被测试的平行实现,可以随意漂离生产路径。**
- `analyst/answer.py:165 reliability_tier` —— 生产直接查 `_ASSURANCE_TO_TIER`,从不调它。

### skip 掉的测试:CI 里 15 个

**8 个是 `requires_live_serve`** —— `/chat` 的治理答案、拒答、多轮历史重放,**主用户路径,没有 key 就永不运行**。
**4 个依赖 gitignore 掉的 `runs/` 数据**(`test_bird_basis_report.py` 三个 + `test_corpus.py` 一个)—— 在你机器上过,**在 CI 或新克隆上永远 skip**,而且路径是相对的,从别的目录跑 pytest 也会 skip。
2 个可选依赖,1 个要活 Postgres。
