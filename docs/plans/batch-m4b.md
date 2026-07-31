# 插入批次 · M4b 拆大文件（N18 / N19）

2026-07-31 立。分支从 `impl/rebuild-first-batch` 起。**插在 M4 与 M5 之间。**体例同 [batch-m2.md](batch-m2.md) 起的各批。

> **语言：简体中文，无英文孪生。**

## 为什么插在这里

这两项是 rebuild-checklist 的 **4.2 / 4.3**，也是**全案唯一真正回应「repo 里几千行的大文件都多的要死」的两条**。它们原本不在 [near-term-plan.md](near-term-plan.md) 里 —— 那是刻意的排除，理由是它们必须紧跟 5.3 的排序。四批做下来这个排除的代价已经可以量了：

| | main | 现在 |
|---|---|---|
| `src/` 里 >1000 行的文件 | 7 个 | **6 个** |
| `run_datalake.py` | 5371 | **5486** |
| `analyst/agent.py` | 1500 | **1534** |
| `src/` 净变化（扣掉被删的 `run_experiment.py`） | — | **+695 行** |

**唯一减少的那个是被删掉的，不是被拆开的。剩下六个净增长。**

两条硬时序，就是插在这里的理由：

- **N18 必须排在任何往 `agent.py` 加东西的工作之前** —— 而 M4 的 N12a / N14 已经往里加过了。再拖，那 1032 行只会更长。
- **N19 与 M5 的 N15 同域** —— 两者都要把 `run_datalake` 里的统计代码从头过一遍。分开做等于读两遍。

| 项 | 一句话 | 估工 | 花钱？ |
|---|---|---|---|
| **N18**（checklist 4.2） | 拆 `build_serve_rails`：1032 行单函数、17 个 kwarg、14 个嵌套 def | 3 人日 | 否 |
| **N19**（checklist 4.3） | 把 1138 行统计从 5486 行的 driver 里提出去 | 2 人日 | 否 |

**两项文件不重叠，可以并行。**基线：**1701 passed / 10 skipped / 1 xfailed**。

---

## 开工前：checklist 4.2 的数字全部为真

> **2026-07-31 撤回。**本节原先写着「**没有任何测试用 `inspect.getsource` 解析 `build_serve_rails`**」，并据此让 N18 不要把「消灭两个解析源码的测试」当收益。**那条更正是错的，checklist 是对的。**
>
> 错因：我那次 `grep -rn "inspect.getsource" tests/ | head` **被 `head` 截断在第 10 行**，而 `test_retrieval_index_cache.py` 按字母序正好排在第 11 位。
>
> 实际两处，逐字对上 checklist 的描述：
> - **`tests/test_retrieval_index_cache.py:333`** —— `inspect.getsource(agent_mod.build_serve_rails)` 之后 `src.split("shortlist_schemas(", 1)[1].split(")", 1)[0]`。
> - **`tests/test_retrieval_index_cache.py:534`** —— `inspect.getsource(build_serve_rails)` 之后**一个手写的括号配平循环**（`depth, end = 0, call_start` 逐字符数括号）。
>
> 「两个测试用 `inspect.getsource` **加手写括号匹配**解析它的源码文本」—— 一字不差。**这确实是全仓最刺眼的一处，而且它是 N18 的收益,不是 N19 的。**
>
> 教训按原样留在这里：一条 `| head` 截断过的 grep，被写成了一份工作单的头号更正。**这正是这几批文档反复在别人身上抓的那类错。**

checklist 4.2 的数字，用 AST 逐个复核，**全部为真**：1032 行（build-sequence 逐字写的就是 "1,032 lines"）、17 个 kwarg、13 个 depth-1 闭包 / 14 个全深度嵌套 def。**唯一偏差是构造点：checklist 说 6 个，实测 9 个**（3 个在 `src/`，6 个在 `tests/`）。

N19 那六处 `getsource` 仍然成立，见下 —— 两项各有各的源码解析债。

---

## N18 · 拆 `build_serve_rails`（checklist 4.2）

### 现状（AST 实测，不是 grep）

```
build_serve_rails   analyst/agent.py:408-1439   1032 行
  keyword-only 参数  17 个（无位置参数）
  嵌套 def          14 个（depth-1 有 13 个）
  文件总行数         1534  ← 这个函数占 67%
```

十三个 depth-1 闭包，每一个都是图上的一个节点或一个节点的内脏：

`_column_count` · `_timed` · `ingest` · `refuse_gate` · `after_refuse` · `assemble` · `_assemble_inner` · `after_assemble` · `_tool_start_detail` · `_resolve_tool` · `_stream_agent` · `agent_core_node` · `narrate_node`

**外部代码引用不到任何一个**，所以测不了、也换不了。`_assemble_inner` 一个人从 `:651` 到 `:925`，**274 行**。

十七个 kwarg：`corpus` · `gateway` · `settings` · `identity` · `model` · `embedder` · `working_memory` · `narrator` · `on_event` · `session_id` · `clarify_checkpointer` · `clarify_thread` · `clarify_resume` · `run_id` · `n_human` · `index_cache` · `schema_vectors`

**九个构造点**（checklist 说 6 个，实测 9）：
- `src/`：`analyst/agent.py:1479`、`eval/arms.py:460`、`eval/oracle.py:365`
- `tests/`：`test_eval_run_log_turns.py:42`、`test_prompt_attribution.py:174`、`test_retrieval_index_cache.py:155/215/364/581`

### 做什么

一个 `ServeDeployment` 承载那 17 个 kwarg，加**模块级**的 rails 节点。三样症状一次消除：kwarg 穿层没了、构造点收成一处、闭包变成可寻址的函数。

**建议的落地顺序**（每一步都可单独发、单独绿）：

1. **先只做 `ServeDeployment` 数据类 + 一个 `build_serve_rails(deployment)` 重载**，旧签名保留成一层薄壳转发。九个构造点一个不动。这一步纯加法，行为零变化。
2. **把最外围、依赖最少的闭包提到模块级**，一次一个，从 `_column_count`（M4 已经把它的主体抽成了 `_column_count_for`，是现成的样板）和 `_tool_start_detail` 开始。
3. **`_assemble_inner`（274 行）单独一笔。**它是最大的一块，也是最值得的一块。
4. **最后收构造点**，删薄壳。

**不要一笔梭。**1032 行一次搬完的 diff 没人能 review，而这一项的全部价值在于之后有人能读懂它。

### 安全网

**18 个测试文件用 `FakeToolModel` 驱动完整的 governed turn** —— 离线、无模型、无 Postgres。这是这一项的主网，比任何新写的测试都强，因为它们是既有的、独立于这次改动的。

每一步之后跑全套。**不许在中途留一个红的中间状态过夜。**

### 验收

- `analyst/agent.py` 里最大函数的行数**降到三位数以内**。
- `build_serve_rails` 的 kwarg 数从 17 降到 1（或 2：deployment + 少数每轮变量）。
- 至少 8 个原闭包变成模块级、可 import、可单测的函数。
- `pytest tests/` 全绿，**测试数只许增不许减**。
- 九个构造点收敛到「构造一个 `ServeDeployment`」加「用它建图」两步。

### 禁止

- **不许改 `index_cache=` 这个参数名。**`tests/test_retrieval_index_cache.py:333/534` 按字符串盯着它。改名归 X.5.5，不在这一批。
- 不许顺手改任何节点的行为。这一项是**纯搬运** —— 有任何一处你觉得「顺便修一下」的，记进 `docs/open-work.md`。
- 不许在这一项里动 `run_datalake.py`（那是 N19）。

---

## N19 · 把统计从 `run_datalake` 提出去（checklist 4.3）

### 现状（AST 实测）

```
run_datalake.py   5486 行   57 个顶层函数
```

统计簇 **8 个函数 / 1138 行**：

| 行数 | 函数 | 位置 |
|---|---|---|
| **629** | `_summarise_rows` | `2293-2921` |
| 230 | `_compare_arms` | `1741-1970` |
| 206 | `ladder_deltas` | `1533-1738` |
| 41 | `_routing_escaped` | `2081-2121` |
| 12 | `_bool_rate` | `2147-2158` |
| 9 | `_fmt_rate` | `1304-1312` |
| 8 | `_mean` | `1315-1322` |
| 3 | `_rate_over` | `2161-2163` |

**`_summarise_rows` 一个函数 629 行** —— 比这个仓库里大多数文件都长。

### 它已经是一个事实上的库了

checklist 说「6 个测试文件通过下划线名 import 它们」。**实测 19 个测试文件、181 处引用**，`_summarise_rows` 22 次、`_compare_arms` 17 次。

> **2026-07-31 撤回第二条。**本节原先还写着「**三个 `src/` 模块也在 import：`eval/analysis.py`、`eval/harness.py`、`eval/leakage.py`**」。**假的 —— 没有任何 `src/` 模块 import `run_datalake`**（`grep -rn "import run_datalake" src/` 零命中）。那三个文件里只是**散文里提到了这些名字**（docstring 与注释）。
>
> 错因和本文档头号更正那条同源：我拿 `grep -rln` 的**文件名清单**当成了代码依赖的证据。**`-l` 只告诉你「这个文件里出现过这个字符串」,不告诉你它是不是 import。**一份工作单里同一类错误犯两次。

所以这不是「把私有代码搬出去」，是**承认它早就是测试的公共 API 了，只是藏在一个 driver 里、用下划线名假装私有**。

### `inspect.getsource` 那条摩擦，实测不成立

原先本节写「搬模块会打断那六处 `getsource`，这是最大的摩擦」。**两层都错：**

1. **`inspect.getsource` 通过函数自己的 code object 解析，所以一个普通别名会透明转发。**搬模块本身不打断任何东西。
2. **我列的六处里有五处根本不解析被搬走的代码** —— 它们解析的是 `run_datalake` 这个模块或 `run_datalake()` 这个函数、以及 `_build_db_corpora`，三者都没搬。

真正受影响的只有两处，而**其中一处不在我的清单里**：

| 站点 | 解析什么 | 结果 |
|---|---|---|
| `test_eval_metrics.py:790` | `_summarise_rows` 本体 | 受影响，改指向 `eval.statistics` |
| **`test_ladder_design.py:842`** | **`ladder_deltas` 本体** | **受影响 —— 我漏了这处** |
| 其余五处（`test_build_isolation.py:602/751`、`test_datalake_routing.py:618`、`test_hash_grade.py:582/611`、`test_ladder_design.py:94`） | 未搬走的东西 | 不受影响，未改动 |

**只按我给的六处清单做，会漏掉唯一真正需要改的另一处。**

### 安全网：这一项可以做到逐字节可证

checklist 的验证条写的是「X.5.4 的 9 个基线数不变」。**那 9 个基线不存在** —— X.5 整块不在近期计划里，X.5.4 从没做过。

**但这一项有个更好的网，而且现成：**

```
拿 runs/datalake/20260730T034522Z-test-ladder-fixed2 的 generations.*.jsonl
→ 搬之前跑一遍 _summarise_rows / _compare_arms / ladder_deltas，存下输出
→ 搬之后再跑一遍
→ 两份 JSON 必须逐字节相同
```

离线、无模型、无 Postgres、分钟级。**先建这个网,再动刀** —— 这是这一项的第一笔 commit，不是最后一笔。

### 做什么

新建 `src/governed_bi/eval/statistics.py`（名字随你，但别叫 `utils`）。八个函数搬过去，`run_datalake` 从它 import。

**下划线名怎么办**：搬过去时把真正对外的几个去掉下划线（`summarise_rows` / `compare_arms` / `routing_escaped`），在 `run_datalake` 里保留下划线别名转发**一个 release**，让 181 处引用可以分批迁。别名要在注释里写死「什么时候删」。

> 这不违反决定 12 的「不给操作员建护栏」—— 那条管的是**拦操作员手滑的闸门**，不是**代码内部的迁移期别名**。M1 的 4.1 已经辨析过同一件事。

### 验收

- 逐字节 golden：搬前搬后三个函数在 20260730 数据上的输出完全相同。
- `run_datalake.py` 行数从 5486 **显著下降**（预期 ~4350）。
- 那六处 `inspect.getsource` 要么指向新模块、要么改成真断言 —— **在 PR 里逐条说明每一处怎么处理的**。
- `pytest tests/` 全绿，测试数不减。

### 禁止

- **不许顺手改任何统计口径。**这一项是搬运。任何一个数变了都是 bug，golden 会抓到。
- 不许把 eval driver 合并（那件事已推迟，见非目标）。
- 不许在这一项里动 `analyst/agent.py`（那是 N18）。

---

## 顺序

```
N18 ──►         （硬时序：任何再往 agent.py 加东西之前）
N19 ──►  N15    （N19 必须在 M5 的 N15 之前，两者同域）
```

N18 与 N19 **文件不重叠，可以两个人同时开**。

## review 会挂在哪里

1. **N18 一笔梭。**1032 行一次搬完的 diff 没人能 review，而这一项的全部价值是之后有人能读懂它。
2. **N19 没有先建 golden 就动刀。**搬完再补 golden 等于用搬完的结果证明搬对了。
3. **顺手改行为。**两项都是纯搬运。看见坑记 `open-work.md`。
4. **N18 改了 `index_cache=`。**两个测试按字符串盯着它。
5. **N19 直接删下划线别名**，让 181 处引用一次性全改 —— 那个 diff 会淹掉真正的改动。
6. **N18 把 `test_retrieval_index_cache.py:333` / `:534` 那两个源码解析测试删掉而不是改指向。**它们守的是「图自己的那份 index cache 按名字传进去了」，不是「有个 `index_cache=` 参数」—— 注释里写明了宽松的子串检查曾经放过 `index_cache=None`。改指向可以，删不行。

## 这两项做完之后

> **2026-07-31 · N18 已完成（`f752fc9` / `82ef4a9` / `975b7e5` / `181880b`），下面这条预测错了一半。**
>
> `build_serve_rails` **1032 → 25 行**，17 个 kwarg → 1 个位置参数，14 个嵌套 def → **0**，最大函数 156 行。但 **`agent.py` 从 1534 涨到 1764**。
>
> 预测「约 500」的隐含前提是**有东西离开这个文件**，而这一项**什么都没搬出去** —— 它把闭包变成了同一个文件里的顶层函数。多出的 230 行是可寻址性的价码：12 个新顶层函数各自的 `def` 行与 docstring，加上 `ServeRuntime` 类的 190 行（其中约 36 行是显式 passthrough property）。
>
> **所以「拆函数」和「缩文件」是两件事，这一项只做了前者。**要让 `agent.py` 进 1000 以内，下一步是把 rails 提到 `analyst/rails.py` —— 现在这一步很便宜，因为那些节点已经是彼此独立的函数了。**没有并进这一批**：它不在条目里，而且会让第 4 笔 commit 不可 review。

> **2026-07-31 · N19 也已完成（`79ed49d` golden / `8785155` 搬运）。**
>
> `run_datalake.py` **5486 → 3919 行**（57 → 34 个顶层函数），新 `eval/statistics.py` **1666 行 / 23 个函数**。比预期的 ~4350 更低，因为实际搬了 **23 个函数不是 8 个** —— 另外 15 个（`price_verdict` 140 行等）只被这个簇和它自己用，留在原地会造成 `statistics` ↔ `run_datalake` 循环 import。
>
> **golden 我自己复跑过**：在 `cac0163` 的 worktree 里生成一份、在搬完的代码上生成一份，两份 **3,759,346 字节、SHA256 完全相同**（`8570aac3...`）。「after」那份的解析日志显示九个统计全部来自 `governed_bi.eval.statistics`，所以它跑的是搬过去的代码,不是 driver。

### 净效果：>1000 行的文件从 6 个变成 **7 个**

| 文件 | M4b 前 | 现在 |
|---|---|---|
| `eval/run_datalake.py` | 5486 | **3919** |
| `analyst/agent.py` | 1534 | **1764** |
| `curator/pipeline.py` | 1668 | 1668 |
| **`eval/statistics.py`** | — | **1666**（新） |
| `eval/index.py` | 1409 | 1409 |
| `curator/asset_bag.py` | 1259 | 1259 |
| `analyst/run_log.py` | 1066 | 1066 |

**这两项修好的是「不可寻址」，不是「文件大」。**`run_datalake` 少的 1567 行几乎原样变成了 `statistics.py` 的 1666 行，`agent.py` 还涨了 230 行。总量基本持平。

这不是失败，是**范围本来如此** —— 4.2 / 4.3 在 checklist 里的定义就是「让这些东西可寻址、可单测」，而它们做到了：`build_serve_rails` 从 1032 行降到 25 行、17 个 kwarg 降到 1 个、14 个嵌套 def 降到 0；1666 行统计有了逐字节 golden 和自己的模块。

**但如果目标是「文件不要几千行」，还差一步**，而且这一步现在便宜：把 rails 提进 `analyst/rails.py`，把 `statistics.py` 按 summarise / compare / price 再分。**便宜的原因正是这两项做完了** —— 那些单元已经彼此独立，搬运不再需要理解它们。要不要走这一步是单独的决定。

**剩下四个大文件**：`run_datalake` ~4350、`pipeline` 1668、`index` 1409、`asset_bag` 1259、`run_log` 1066。checklist 里 `asset_bag` 有 X.1、其余三个都没有对应条目 —— **B 轴还没有走完，这两项只是第一步**。

然后回 [batch-m5.md](batch-m5.md) 做 N15–N17。
