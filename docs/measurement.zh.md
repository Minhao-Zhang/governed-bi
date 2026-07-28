# 我们量什么，以及失败会在哪里显形

_[English](measurement.md) · [简体中文](measurement.zh.md)_

曾经有一次三臂数据湖跑批，最后不得不整批作废——因为 harness 只能说出一轮失败
了，却说不出*失败在哪*。求解器崩溃与蓄意拒答都统一落地成 `error="refusal"`，
于是 `refusal_rate` 把崩溃计数一并吞了进去，EX 也跟着吞下这份损失——而且各个
臂吞下的量还不一样，因为它们崩溃的比例本就不同。serve 路径某个工具 helper
里的一个 `NameError`，最终只体现为 `refused_by="model_error"`，和模型本身
偶尔抽风没有任何区别。这份文档要解决的问题是：当一个数字看着不对劲时，去哪儿
查——测量本身落在哪里，哪个文件负责把哪一类失败定位出来。那次作废的记录本身
见 [`plans/datalake-run.md`](plans/datalake-run.md)。

> **这一页上数字的出处。** 每一个「最近一次完整基准测试」的数字（45.8%、
> 2030 中的 135、61%、73.7%……）都来自 2026-07-25 之前的分析跑批，和那套
> 仪器产出的其余东西一样已经**作废**——见
> [`plans/datalake-run.md`](plans/datalake-run.md#status)。留着它们，是因为
> 它们所演示的*算术*（多类别错误不能直接相加；问题层面的波动会淹没掉小
> 差值）才是要点，而一个带着真实量值的示例，比一个凭空编的示例更能说清楚
> 这一点。它们**不是当前的结果**，`plans/experiment-runbook.md` 说得对：这
> 个仓库从来没有带模型跑完过完整的切分。引用其中任何一个之前，请重新推算。

## 两根轴，刻意分开

`src/governed_bi/stages.py` 是 serve 路径与 eval harness 共用的一套词汇表，
两边都从这里导入。它只包含文本常量与纯函数——没有 I/O、没有配置、没有模型——
所以对它的修改不可能在构建侧与评测侧之间悄悄产生分歧。

**`Outcome`**——这一轮到底发生了什么：`answered`、`refused`、
`clarification`、`capped`、`crashed`。`crashed` 正是旧 harness 表达不出来
的那个成员，也是上面那次跑批必须作废的原因：崩溃是我们自己的 bug，拒答是
产品在正常工作，而一个把两者加在一起的指标，是在测量两件事却只报告一个数字。

**`Stage`**——发生在流水线的哪个环节：图自身的轨道（`route`、
`refuse_gate`、`cache`、`assemble`、`agent_core`、`narrate`、
`finalize`），外加 `assemble` 内部的子阶段（`shortlist`、`schema_pick`、
`retrieve`、`license`）与 `agent_core` 内部的子阶段（`search_corpus`、
`inspect_schema`、`sample_rows`、`guardrail`、`execute`、`repair`）。一次
schema-pick 没选中和一次护栏拦截，在图看来都只是"assemble/agent_core 失败
了"；把两者分清楚，就是修检索还是修生成的区别。

`classify_outcome()` 依据一轮的原始信号来判定这两者，判定顺序如下：
`exception` 永远优先（一轮抛过异常，就不算拒答，不管它的元数据里还写了别的
什么）；其次，出现 `generated_sql` 就判为 `answered`；再往后才会去查
`refused_by` 在 `REFUSED_BY_TO_STAGE` 里对应到什么。`refused_by="model_error"`
映射到 `Outcome.crashed`、`Stage.agent_core`——serve 路径在捕获自己内部抛出
的异常、降级成一次拒答以求失败即拒时，打上的就是这个值。失败即拒本身没有
问题；把它算成一次拒答才是问题。`refused_by="exhausted"`（修复循环的次数
上限）映射到 `Outcome.capped`，而不是 `refused`——循环不是主动拒绝，而是
尝试次数耗尽了。`refused_by` 是自由文本，没有中心化的声明表，所以一个不在
`REFUSED_BY_TO_STAGE` 里的取值会被诚实地计数，而不是被悄悄归进某个桶：这种
情况下 `classify_outcome()` 返回的第三个元素是 `False`，每一处逐行调用方
（`run_datalake.py` 里的 `_grade_one`）都会打印一条警告，并把它计入
`n_unmapped_refused_by`，而不是去猜一个阶段。`classify_row()` 是把同一套
分类逻辑用在已打分的行上：如果某一行已经带有一个打好的 `outcome`/
`failed_stage`，就优先沿用它，这样一行被更新的分类器打过分后，就不会被更旧
的分类器悄悄重新推导一遍。

可评分性（gradeability）在这里被刻意排除在第三种取值之外。一个 gold hash
能不能用来比对，和 outcome 是两件正交的事：一个没有 gold hash 的问题，该是
answered 还是 refused 并不会因此改变；把它并进 outcome 词汇表里，正是评分
缺口开始被读成模型失败的起点。一行本已 answered 的记录，即便带有
`error="missing_gold_hash"` / `"gold_unusable:..."`，依然分类为
`Outcome.answered`——见
`tests/test_stages.py::test_grader_gradeability_errors_are_not_crashes`。

## 埋点：每一轮都记录了什么

`StageRecorder`（`src/governed_bi/analyst/governance.py`）是一个按轮次计
的累加器：每轮一个实例，归该轮的 `GovEventStream` 所有，因此两者在同一个
边界上重置。这种归属关系是正确性要求，不是偏好问题。当 `serve_workers > 1`
时，eval harness 会同时服务好几张图，一个模块级全局累加器会把两轮的数据
交织进一条读不懂的记录里。

- `stage(name, **detail)`：以上下文管理器的形式为一个阶段计时。出现异常时，
  记录会被打上 `status="error"`，随后异常会被**重新抛出**——如果吞掉它，
  一个死掉的阶段看起来就会和一个从未运行过的阶段一模一样。
- `skipped(name, **detail)`：记录一个刻意没有运行的阶段（比如单 schema
  corpus 上不存在 LLM schema pick）。`ms` 是 `None`，不是 `0`：一个从未
  运行的阶段并不是耗时零毫秒，而一条缺失的记录则会被读成"这个 build 根本
  测不了这个阶段"。
- `count_tool_call(name)`：按名称统计每一次工具调用，独立于治理账本（账本
  只记录 `run_query`/`sample_rows`——把它扩大，就等于扩大了"受治理"这个
  说法本身的边界）。在这之前，`search_corpus`/`inspect_schema`——一轮里
  占大头的那部分工具调用——在任何地方都不留下持久痕迹。
- `guardrail_layer(layer, passed)`：统计某一层护栏的裁决。跑过但什么都没
  拦下的层，键值记为 `0`；这一轮根本没跑过的层（比如没有检索 scope 时 L4
  term-semantics 会被跳过）则**缺失**这个键。把这两种情况混为一谈，就会
  给一个根本没执行过的层报出一个自信满满的零。
- `provenance()` 返回 `{"stage_events": [...], "n_tool_calls": {...},
  "by_guardrail_layer": {...}}`——后面整条链路都按名字读取这三个键。

护栏层计数器由传入 `gateway.guardrails.check(..., on_layer=...)` 的一个
observer 驱动。它在结构上就是只读观察：`_observe()` 内部的任何东西都无法
影响裁决结果，且 observer 抛出的异常会被吞掉——治理逻辑在这里出问题，要比
少一个指标严重得多。如果 observer 曾经抛出过异常，它会按进程只警告一次
（而不是按查询逐次警告），因为一个在第一次调用就悄无声息挂掉的计数器，会
让这一层的直方图永久留空，同时每次跑批看起来都很健康——这正是这套埋点存在
的目的所要终结的那种失败形态。端到端契约见 `tests/test_stage_metrics.py`
里 `check()` 的观察测试与 `tests/test_stage_metrics_seam.py`（一轮真实
运行，不打桩 provenance——链路上任何一处键名重命名都会在这里失败）。

`GovEventStream.final()` 会在可移植追加记录写入*之前*，把 recorder 的
`provenance()` 打到答案上，所以持久运行日志里也带着这一轮自己的耗时与
计数，而不只是 eval harness 才有。哪些 provenance 键必须留存到每一个终态
`Answer` 上，由 `METADATA_PROVENANCE_KEYS`（`src/governed_bi/analyst/run_log.py`）
钉死，其中包括 `stage_events`、`n_tool_calls`、`by_guardrail_layer`、
`cache_hit`、`attempts`（这些属于 `_INSTRUMENTATION_KEYS` 那一块），外加
运行身份字段（`turn_id`、`run_id`、`outcome`、`model`……）。一个没有测到
值的埋点字段会被写成 `None`，绝不是 `0` 或 `{}`——一个什么都没测到的生产者
必须说清楚这一点，否则缺失的键就和"这个 build 根本记录不了这个指标"分辨
不出来。`strip_stage_events_for_log()` 会保留 `stage_events` 的数值形状
（`stage`/`status`/`ms`），但在写入持久存储之前丢掉所有字符串取值的
`detail` 键：`detail` 在源头就是自由格式的，所以持久化的投影不能按键名
信任它——不然日后一个 `detail["query"]` 就会把用户自己的原话写进一份本该
只含元数据的日志里（ADR 0004 H11 Tier A）。

## 从一个问题，到一次运行，再到账本

**逐问题。** `generations.<arm>.jsonl` 里每一条打分记录都带着 `outcome`、
`failed_stage`、`refused_by`、`n_tool_calls`、`by_guardrail_layer`（由
`eval/run_datalake.py` 里的 `_grade_one` 计算——它给这一行分类的那一刻，
正是求解器异常还能和其他种种 `error` 字符串区分开的唯一时机）。同一轮的
逐阶段耗时会被 `_stage_event_rows()` 摊平写入 `<run_dir>/stage_events.jsonl`
——每个问题的每个阶段一行 JSON，打上 `question_id`/`arm`/`db_id` 标签。
**在 `--resume` 时被重放的一行，不会贡献任何 `stage_events` 记录**：它没有
新鲜的耗时数据，而如果去合成一条——或者把整行的总耗时抄到某个阶段头上——就是
在这个专门负责归因耗时的文件里塞进一个编造出来的数字。所以在一次恢复过的
运行里，`stage_events.jsonl` 是 `generations.<arm>.jsonl` 的一个子集，可以
按 `(question_id, arm)` 关联；一个在写入中断后被重新服务过的问题，可能在
里面出现不止一次——真正的权威始终是那份行文件本身记录的打分结果。

**逐次运行。** `_summarise_rows()`（`run_datalake.py`）是从**落盘的行**
里聚合，不是从运行中的结果里聚合，这正是一次恢复过的运行能和一次不间断的
运行汇总出一样结果的原因——被重放的行和新打分的行走的是完全同一个函数。
`run_experiment.py` 里的 `ArmSummary` 用同样的方式聚合单库阶梯驱动器的行，
现在也带上了同一套崩溃字段——这个对齐是最近才补上的：一次对抗性评审发现
单库驱动器仍然把崩溃计成拒答，正是逼着上面那次池化驱动器作废的同一个缺陷，
只是原封不动地待在另一个驱动器里。两者现在都会写 `by_outcome`（完整的
outcome 分区，可以拿 `n_answered`/`n_refused`/`n_crashed` 去对照 `n`）、
`by_failed_stage`（只有真被观测到才会出现某个桶）、`n_unmapped_refused_by`，
以及与 `refusal_rate`（现在只剩真正的拒答）分开的 `crash_rate`。两个驱动器
现在都把崩溃打成 `f"{type(err).__name__}: {err}"`，绝不是裸的 `str(err)`：
`str(KeyError("schema"))` 只会得到 `'schema'`，既说不出失败的种类，也说
不出是哪个调用帧抛出来的，而这个字符串是这次崩溃唯一留存下来的记录。

**只要分母为空，任何比率都是 `None`，绝不是 `0.0`**——`ex_lenient`、
`ex_strict`、`ex_gradeable`、`refusal_rate`、`crash_rate`、`routing_recall`、
`cond_ex_given_routing`、`decoy_touch_rate`、`conditional_ex_lenient` 和
`share_with_a_note` 现在全都遵守这条规则。一个打了零行分的臂，什么都没测到；
`0.0` 反而会被读成"测了个遍、一个都没对"，而运行台账的可引用性检查恰恰就是
靠这个区分来判断的（一个 `crash_rate` 从未被记录过——即 `None`——的臂，
并没有证明自己没崩溃过，因此会被当成不可引用；见下文"逐个项目"）。
`share_with_a_note` 只在那些确实记录了笔记注入情况的行上计算，而不是对每一行
都算，所以一次早于这个字段出现的运行会报 `None`，而不会像 `0.0` 那样谎称
"交付失败"。

`eval/analysis.py` 的 `gradeable_report()` 对它和 summary 共用的三个名字
（`ex_lenient`、`ex_gradeable`、`decoy_touch_rate`）遵循同一条规则。它们
同时出现在 `summary.json` 和 `analysis.json` 里，如果两边对"边界情况该
算什么"的理解不一致，就会让两份文件对同一次运行给出互相矛盾的说法。

有一个后果值得记住，因为它确实咬过人一次：任何要把这些数字格式化到控制台
上的代码，都必须能容忍 `None`。`run_datalake` 里按臂输出的进度行会经过
`_fmt_rate()`，把它渲染成 `n/a`；如果直接对它套用格式说明符，会在整个
serve 循环跑完、`summary.json` 写入之前抛出 `TypeError`。

随崩溃/拒答拆分一起落地到 `_summarise_rows()` 的，还有两处修正，形状和它
一样：一个指标不能悄悄吞掉另一个指标的失败。

- **`routing_recall` 的分母现在排除了崩溃的轮次和被绕过的轮次。** 一个
  崩溃的轮次根本不返回任何 meta，所以不管路由器那一轮到底跑没跑过，它的
  行都会记成 `routed_hit=False`；把它算进分母，就是在把崩溃的账算到路由
  器头上。*被绕过*的轮次是另一面镜像：当 corpus 只有一个 schema 时，根本
  没有路由决策可打分，serve 路径会明确说明这一点（`routing_bypassed`）。
  把这些轮次算成漏选，会给一个无可路由的池子报出 `0.0` 的 recall；把它们
  算成命中，则报出 `1.0`，这等于给一个从未运行过的路由器记一次功——而在
  oracle 档上，schema 本来就是*直接发放*的，那更是让这一档给自己发的礼物
  打分。两者都被排除在外，所以这个指标会读作 `None`（没有测量），而不是
  上面两种谎言里的任何一种，`n_routing_bypassed` 说明这样的行有多少。

  还有第三种形状：一轮*完全没有*记录任何路由决策——它在 `assemble` 运行
  之前就结束了。`routed_hit` 在这里是 `None`，不是 `False`：分母依据的是
  正面证据（一次被记录下来的决策），而不是"没有绕过标记"。
  `n_routing_unrecorded` 说明被这样排除掉的行有多少。少了这一项，那次覆盖
  全 split 的 `--skip-agent` 上限跑批就会在每一行上报出
  `routing_recall: 0.0`，旁边的 `n_routing_bypassed` 却是 `0`——而那个
  路由器根本没被调用过。
- **`cond_ex_given_routing` 现在的两项都取自已路由的行。** 它以前是拿每
  一条正确的行（不论是否路由过）去除以已路由的行数，只要有一个问题是在
  路由器漏选的 schema 上蒙对了答案，这个比值就能读出超过 `1.0`。因此
  正确答案要按**五**路来分，被排除在路由分母之外的每一类各占一项：

  ```
  n_correct == n_correct_routed
             + n_correct_unrouted
             + n_correct_bypassed
             + n_correct_routing_unrecorded
             + n_correct_routing_crashed
  ```

  五项缺一不可，五项都是直接数出来的、不靠相减，`n_correct_unaccounted` 把
  余数也写进产物，这样再多出第六类排除时，它会显示成一个数字，而不是某个桶
  悄悄算错。这里以前写的那个三项版本，其实早就已经不成立了：一轮没有记录任何
  路由决策、答案却是对的，三项里哪一项都装不下它。`EX` 是在*每一行*上算出来
  的，而路由相关的这几项不是，所以一旦存在被排除的行，`EX == routing_recall ×
  cond_ex_given_routing` 这条恒等式就不再成立，而 `n_correct_unrouted`——
  以前充当逃生舱口的那个字段——却仍然读作 `0`。
- **路由器不是一道门，所以这条恒等式原本就不该成立。** `n_correct_unrouted`
  曾被描述成“通常是 0”；从结构上看，它并不是。`agent_core_node` 是拿**池化**
  corpus 去组装 agent 的，而不是 `assemble` 用过的那份已路由的
  `retrieval_corpus`——所以 agent 的 `search_corpus` 工具检索的是全部
  schema，不管路由器选中了什么。这一点已经直接验证过：路由器只选中了
  `address`，但对池化 corpus 的检索仍然返回了 `airline` 的表，而同一个查询
  对已路由的 corpus 检索则什么都不返回。

  这对 EX 来说未必是坏事（agent 可以从一次路由漏选里恢复过来，而不是直接
  拒答），但这也意味着，路由这几项指标描述的是*一个答案可以自由无视的排序
  步骤*，而不是一道它必须通过的过滤器。所以 `routing_recall` 和
  `cond_ex_given_routing` 是两项各自有用的测量，而不是 EX 的两个因子；一个
  只挪动其中一项的差值，并不能像这一对指标看起来暗示的那样，定位出某个臂到底
  是在哪里起了作用。

  这种情况发生的频率，现在是测出来的，而不是假定出来的：`routing_escape_rate`
  （分母是 `n_routing_escape_observed`）和 `n_correct_via_routing_escape`——
  用到了路由器已经排除掉的某个 schema 的正确答案，这些胜利不是路由器促成的。

  这个裁定是从 `tables_used` 算出来的——从实际交付出去的 SQL 里解析出的表，
  再经由这个臂自己的 corpus 解析回 schema（资产 id 长得像
  `tbl_<schema>_<name>`，但 schema 名字本身也含下划线，所以靠切字符串去猜会
  猜错）。**不是**从 `licensed_tables` 算的：那是 assemble 阶段的种子许可，
  是从*已路由*的 corpus 算出来的，而且从不会被修订，所以不管 agent 实际走到
  多远，它都不可能包含一个路由之外的 schema。这个指标的第一版用的正是
  `licensed_tables`，结果把一次已经证实发生过的越界判成了合规（那次越界是：
  `search_corpus`，接着 `inspect_schema` 给一张路由之外的表发了许可，再接着
  护栏放行了它）。到目前为止构建出来的每一份 corpus 里，跨 schema 的 `JoinAsset` 都是
  零，所以那一版指标永远只能返回 `False` 或 `None`。

  解析不到的资产 id 不再被静默丢掉。每一行会记下
  `tables_used_unresolved` / `n_tables_used_unresolved`。约定：只要有*已解析*
  的 schema 落在路由集合之外，`routing_escaped` 就是 `True`（已知越界）。若只剩
  未解析 id、又证明不了已解析越界，则 `routing_escaped` 为 `None` 且
  `routing_escape_unknown=True`——未知，不是“未观测 / 没什么可判”。真正空或
  缺失的 `tables_used` 仍算未观测（`routing_escaped=None` 且无 unknown 旗标）。
  越界**率**的分母（`n_routing_escape_observed`）只计明确的 True/False 行；
  `n_routing_escape_unknown` 另列，欠计可见。

  如果 `routing_escape_rate` 测出来很高，读这条阶梯里跟路由有关的那一半时就
  要留着这个心眼：一个臂可以只提升 `routing_recall` 而不提升 EX，因为 agent
  早就已经能绕过路由器自己够到答案；出于同样的原因，它也可以只提升 EX 而不
  提升 `routing_recall`。

`_summarise_rows()` 还会写 `tool_calls` 与 `by_guardrail_layer`（跨行
求和）、`n_with_difficulty`（BIRD 有约 85% 的行没有难度标签，所以
`by_difficulty` 会全部塌缩进一个 `"unknown"` 桶；没有这个计数，它读起来
就会像一个均匀分布，而不是一次空测量）、`n_gold_unusable`（存在 gold
hash，但打分产物把它记成不可用——这些行仍然按 `correct=False` 计分，也
仍然落在每一个 EX 分母里，没有这个计数，这种低估就无从命名）；以及在
运行层面（而非按臂）写进 `summary.json` 的 `decoy_manifest_missing_dbs`
（一个没加载陷阱 manifest 的 db 算不出有意义的诱饵触碰率；把它点名出来，
能防止那里的 `0.0` 被读成"干净"，而它实际的意思是"没测过"——这一项只在
datalake 模式下存在：`run_experiment.py` 也以同样方式为它服务的那一个
db 加载陷阱列，但不追踪也不报告 manifest 是否存在）。以上全部落在
`summary.json` 里。

**逐个项目。** `runs/index.jsonl`（`src/governed_bi/eval/index.py`）是一
份扁平台账，每次运行一条记录，在 `run_datalake()` 结束时自动追加，也可以
单独用 `uv run python -m governed_bi.eval.index --add runs/datalake/<ts>`
来生成。每条记录都回答两个以前只存在于某个人记忆里的问题：

- **`quotable` / `ledger_ok` / `hygiene_ok`**——这次运行的*产物卫生*够不够拿去
  考虑引用？只要有任意一个臂崩溃过（`crash_rate` 为真值）、任意一个臂的
  `crash_rate` 根本没被记录过、有 db 构建失败、curator 构建时的错误被吞掉了、
  这次运行打分的是 `train` split（curator 读过这些问题，所以这只是一次诊断，
  不是一个结果），或者 `n_questions` 低于该臂家族的 Holm 算术下限
  （`arithmetic_floor_for_arms`；默认四臂下限是 8，五臂要 9）——满足其中任何
  一条，这次运行就*不* ledger-ok。`crash_rate` 未记录这一种情况是刻意失败即拒
  的：一次早于崩溃/拒答拆分的运行，并没有证明自己没有崩溃过，因为它的
  `refusal_rate` 和 EX 无论如何都会把崩溃吞进去。`not_quotable_because` /
  `not_ledger_ok_because` 会把每一条理由都列出来，不只是第一条。
  **`quotable: true` 不等于“可以发表”。** 统计上的声明就绪（replicate、MDE、
  Holm、cluster、single-variable、twin）在实验运行手册清单里；台账里的
  `claim_ready` 恒为 `false`，并列着 `claim_ready_requires`，不会假装去评这些
  条件。
- **`comparable(a, b)`**——两次运行能不能放进同一句话里比较？只有当
  `split`、`model`、`prompt_set_hash`、`route_top_k`、`route_llm_pick`、
  `schema_pick_max_columns`、`use_embedder` 全部一致时才算数。一个旋钮在
  两边都缺失，算作匹配（两次都早于这个旋钮出现的运行，在这一项上谈不上
  有差异）；一个旋钮只在一边被记录，则算作真实差异，因为另一边的取值是
  未知的。在旋钮已经变了、却没注意到的情况下去比较两次运行，正是那批被
  作废的数字当初是怎么产生的。

这两项检查都不会阻止运行本身。它们做的是把理由写进产物、写进渲染出来的
表格里，所以要引用一个数字，就得先把这些理由读过一遍。`tests/test_eval_index.py`
把这两条规则钉死，针对的正是已经让一整套结果作废的那两个错误。

## `eval/analysis.py`：跑批之后的归因

`analyse_run()`（`src/governed_bi/eval/analysis.py`）在一次运行结束后
读取 `generations.<arm>.jsonl`，计算那些运行本身算不出来的东西。伴随分类
体系那次改动，下面这几处修正一起落地：

- **`incomplete_arms` 以前的逻辑是反的。** 它原来是拿最短的那个臂做基准，
  把*更完整*的那个臂标记出来。现在它把每个臂的问题 id 集合去和所有臂 id
  的**并集**做比较，于是一个臂被判定为不完整，恰好就是它缺了某个别的臂
  打过分的问题——这也能抓住"两个臂大小相同但覆盖的问题不同"这种情况，一
  个只看长度最大值的规则会漏掉它。
- **split 绝不靠猜。** 对于早于 `split` 字段出现的那些行，`analyse_run()`
  会直接报错，而不是自己挑一个 gold 文件——因为选错 gold 文件会让每个
  问题都匹配不上，这种情况以前读起来像是干净的"没有 gold 可比"，而不是
  真正的缺陷所在。跑一份老运行时，要显式传 `--split`。
- **拒答会被排除在选表归因之外。** 一行没有 `generated_sql` 的记录，
  根本没做过任何选表动作；把它的空表集合算成"所有 gold 表都缺失"，等于
  凭空从一次拒答里制造出一次选表失败。`table_selection_report()` 现在把
  这些行归进 `n_no_sql`，从比较中剔除，而当没有任何东西可比较时，
  `table_mismatch_rate` / `mean_table_recall` / `mean_table_precision`
  都是 `None`——不是 `0.0`——因为零次比较得出的零不匹配率，读起来像
  "选表全都没问题"，而实情恰恰相反。
- **它的两两检验也做了校正，并且会说清楚自己捆绑了什么。** `analyse_run()`
  把每个臂和其他每个臂两两配对，于是四个臂就是六个检验，而六个都按名义
  α=.05 单独判定，整个检验家族里至少出现一次假阳性的概率大约是 26%。它会
  在原始 `p_value` 旁边报出 `p_value_holm` 和 `n_family`，而且只统计那些
  确实产出了 p 值的配对——出错的配对会被排除在这个家族之外，因为为一个从未
  真正跑过的检验去收紧其他检验的判定标准，纯粹是白白浪费显著性。每一对还
  带着 `single_variable`，复合情形下还带着 `bundles` 列表，用的是驱动器
  同一个 `arms.skipped_rungs`：一对臂哪怕在实际跑过的那些臂里算是相邻，也
  仍然可能捆绑了不止一件事——默认臂集合里 `curated → curated_sme` 跳过了
  `curated_sme_blind`，正是这种情况。这份报告做不到的是给出分辨率——它没有
  replicate 臂——所以它带着 `mcnemar_caveats.no_noise_floor` 指向
  `summary.json`，而不是让一个很小的 p 值暗示这次运行本可以分辨出它背后的
  那个差值。

## 错误分类体系：按阶段与按类别

上文 `stages.py` 的 `Outcome`/`Stage` 词汇表，只覆盖到拒答、被打满上限、或
崩溃的那些轮次。这样一来，一次基准测试跑批里数量最大的那部分——干净地跑完
全程、生成了 SQL、执行了它、却返回了错误行——就一直没有归因。在上一次完整
基准测试里，这部分占到全部问题的 45.8%，而它们全都被塞进了同一个叫"schema
选对了、SQL 写错了"的桶里——这个桶太大了，没法据此采取行动，早先对"修好
它值多少"的估计也因此横跨了一个数量级都定不下来。
`src/governed_bi/eval/sql_diff.py` 与 `src/governed_bi/eval/error_taxonomy.py`
把这个桶从两个维度拆开，拆分的依据都是拿 `generated_sql` 去和 gold 做
diff——不碰数据库、不碰模型，在一份归档的 `generations.*.jsonl` 上重跑一遍，
和在一行实时数据上跑一遍一样容易。

**按阶段**分类时，归因是一条从外到内的严格级联，所以各个桶互斥、可以直接
相加：每一个错误答案都恰好被记到一个阶段头上，即出错链条里
最外层的那一环。顺序是：`embedding_wall` → `wrong_schema` →
`execution_error` → `unparseable_sql` → `wrong_table` → 一组 SQL 构造类别（连接图、连接键、
连接类型、聚合、分组、过滤列、过滤字面量、投影、投影顺序、distinct、
排序/限制、集合运算） → `value_level`。`attribute_row()` 沿着这个列表走，
在第一个命中的类别处停下；一个问题如果 schema 就选错了，那不管它的 SQL
还有什么别的问题，它都是一次路由失败——因为这段 SQL 本来就是照着模型不该
看到的表写的，把它算成一次连接 bug，就是在为一个生成阶段根本没有机会避免
的错误去怪罪生成阶段。`execution_error` 记的是语句能解析、但打分器执行时抛错
（类型错误、未知列、除零）：它没有返回行，没法比任何结构维度，也绝不能并进
`value_level`。只有 schema 选对了，选错表才算一次选表失败；只有表
也选对了，别的问题才算一次 SQL 构造失败。`gold_unusable` 完全在这条级联
之外：一条把结果硬编码进去的 gold 语句（`is_frozen_constant`）或者干脆
解析不了，任何模型都不可能从 schema 出发写出它，所以它不会被记到任何阶段
头上，也会被从 `gradeable` 里剔除。

两种路由失败被分开看待，而不是并成一种。`embedding_wall` 指 gold schema
根本没能进入候选名单——在任何挑选器运行之前，检索就已经漏掉了它。
`wrong_schema` 指候选名单里明明有正确的 schema，挑选器却选了别的——这是
提示词的问题，不是检索的问题。`attribute_row()` 依据 `gold_in_shortlist`
（由该行记录下来的 `shortlisted_schemas` 推出）在两者之间做判断；如果
这个值从未被记录过，该行就归到 `wrong_schema`——这是更保守的读法，把
责任记在确实被观测到的那个组件头上，而不是那个根本看不见的组件头上。

**按类别**分类时，报告的是每一行错误、可评分记录上"有差异的那些*维度*"
（`sql_diff.Dimension`）构成的一个集合，而不是像 `stage` 那样折叠成单一
标签。一条写错的查询，往往在不止一个维度上同时出错——实测的分布里，一条
查询最多可以同时命中十一个类别——所以 `error_taxonomy` 统计的是类别的
出现次数，并在每一行记录 `n_classes`，让这种重叠可见，而不是被默认忽略。
所以按类别的计数不能直接加总成一个可提升空间的估计值：在上一次
基准测试里，61% 的错误答案是多类别的，所以"N 个问题选错了表"并不等于修好
选表之后就有 N 个问题会变对——它们中的大多数在别的地方仍然是错的。
`summarise_attributions()` 把 `multi_class_share` 记进产物，正是为了这个
原因，这样读者不用先自己重新发现这种不可加性，就会把 `error_class_incidence`
里的各行当成互相独立的杠杆去读。曾经有一份报告，在没有这个数字的情况下
发布了按类别的点估计，得出"还有 46 个百分点可以拿"，随后又把它改成"3 到
5 个百分点"，中间没有任何东西能证明这两个数字里哪一个成立，也没有一个能
从它所依据的产物里重新推导出来。

这套级联给出的是互斥的阶段分桶，给不出因果意义上的可提升空间。"修好选表
能换来多少 EX？"是一个反事实问题，而对一套承认多类别并存的分类体系做计数
加总，只会给出一个高估的答案。诚实的答案来自在某一阶段替换成 gold、再
重新测量——见[oracle 阶梯](oracle-ladder.zh.md)。

两个分块都是在跑批汇总阶段、按臂计算一次，而不是逐行打戳：
`summarise_attributions(attribute_rows(rows, gold, shortlists=...))` 在
`run_datalake.py` 与 `run_experiment.py` 里都会跑，其输出落在
`summary.json` 的 `arms.<arm>.errors` 下——`n`、`n_wrong`、
`n_wrong_gradeable`、`n_gold_unusable`、`by_error_stage`、
`by_error_primary`、`error_class_incidence`、`classes_per_query`、
`multi_class_share`。离线分类体系故意用 **`by_error_stage`**，不用 live serve
汇总里的 `by_failed_stage`（`classify_row` 的 Outcome/Stage）：两个含义以前共用
一个名字，调试时容易搅在一起。当这次运行没有提供 gold 时，它是 `None`，不是一个
空字典：空字典等于在断言"没有任何东西被错误分类"，而 `None` 说的是"这个
问题根本没有被问过"。

**维度**（`sql_diff.Dimension`）——被比较的句法事实，大致按从外到内的顺序
排列：

| 维度 | 比较的是什么 |
|---|---|
| `schema_set` | 语句里的表分别属于哪些 schema（`db` 限定符） |
| `table_set` | 语句读取了哪些物理表 |
| `join_graph` | 哪些表和哪些表连接在一起，作为一个无序边集（不区分连接顺序） |
| `join_keys` | 用作连接等值条件的确切 `table.column` 对 |
| `join_type` | 出现的连接种类（`INNER`/`LEFT`/……）及各自的数量 |
| `projection` | 选取的列/表达式，按顺序比较（`order_only` 标记"元素集合对、顺序错"的情形） |
| `filter_columns` | `WHERE`/`HAVING` 里引用的列 |
| `filter_literals` | `WHERE`/`HAVING` 里比较用的字面量（大小写已折叠，所以大小写差异算值错误，不算结构错误） |
| `aggregation` | 聚合函数（`SUM`/`AVG`/`MIN`/`MAX`/`COUNT`）及其包裹的列 |
| `group_by` | `GROUP BY` 的列 |
| `order_limit` | `ORDER BY` 的键加上 `LIMIT`，顺序敏感 |
| `distinct` | 是否出现 `SELECT DISTINCT` |
| `set_ops` | `UNION`/`INTERSECT`/`EXCEPT` 的使用及次数 |

**错误类别**（`error_taxonomy.ErrorClass`）——每一种维度不匹配（或维度
缺失本身）叫什么：

| 类别 | 含义 |
|---|---|
| `embedding_wall` | gold schema 根本没能进入候选名单——在任何挑选器运行之前，检索就已经漏掉了它 |
| `wrong_schema` | 候选名单里有 gold schema，挑选器却选了别的 |
| `execution_error` | 语句能解析，打分器执行时抛错——记到 `execute`；不是 harness 崩溃，也不是结构/值类别 |
| `unparseable_sql` | 生成的文本根本不能解析成 SQL |
| `gold_unusable` | gold 语句是一个冻结常量，或者解析不了——不是模型的失败，被排除在级联之外，也被排除在 `gradeable` 之外 |
| `wrong_table` | `table_set` 不匹配 |
| `wrong_join_graph` | `join_graph` 不匹配 |
| `wrong_join_key` | `join_keys` 不匹配 |
| `wrong_join_type` | `join_type` 不匹配 |
| `wrong_projection` | `projection` 不匹配——选取的列/表达式错了 |
| `projection_order` | `projection` 的元素是对的，顺序错了 |
| `wrong_filter_column` | `filter_columns` 不匹配 |
| `wrong_filter_literal` | `filter_literals` 不匹配 |
| `wrong_aggregation` | `aggregation` 不匹配 |
| `wrong_group_by` | `group_by` 不匹配 |
| `wrong_order_limit` | `order_limit` 不匹配 |
| `wrong_distinct` | `distinct` 不匹配 |
| `wrong_set_op` | `set_ops` 不匹配 |
| `value_level` | 每个维度都解析成功且互相匹配，结果却还是错的——差异出在某个值上。记到 `sql_generate` 头上：语句本身执行正常，返回的正是它自己要求的那些行，所以缺陷出在生成器写了什么，而不是执行器 |
| `unresolved_diff` | 至少有一个维度解析结果是 `unknown`（别名/作用域解析失败），所以"没找到不匹配"不等于"全都匹配"。不记到任何阶段头上，计入 `n_unattributed`，让这个空缺有一个可见的大小 |

每一行错误记录还带着一个 `result_shape`——`both_empty` / `empty_result` /
`row_count_differs` / `same_row_count`——由打分器早就记录下来的 `pred_nrows`
和 `gold_nrows` 推导得出。它只是描述性的，从不用来判定阶段：知道一条查询
返回了空结果，说明的是一个错误的字面量造成了什么后果，而不是说明另一个
组件出了问题。它不需要额外的查询，这也是为什么早先那个"对每一行错误重新
执行一遍 gold 和生成的 SQL"的设计被撤掉了，而不是接上去用：那两次额外的
往返，买来的不过是打分早就付过钱的一个区分，`Stage.execute` 仍然只留给
那些真正执行失败的语句（由 `refused_by="execution"` 实时打上戳）。

## 处理验证：干预到底有没有送达模型

一次实验要比较的是理应存在差异的几个臂。`eval/treatment.py` 之所以存在，
是因为在这个项目里，臂有时候根本没有任何差异，而且这一点直到一个建立在
"零效应"之上的结论已经发布出去之后才被人发现。这个模块强制执行的规则是：
一项干预的效果，必须先证明这项干预确实被应用过，才能报告。硬盘上有一份
corpus、一个跑过的臂、跑出来的若干行结果——这些只能证明没有任何东西崩溃，
不能证明任何东西真的被送达过。

两起事故把这条规则逼进了代码里。Simulated-SME 臂读取澄清账本时，用的是
一个构建步骤早就已经挪走的路径；它什么都没折叠进去，每次跑批产出的
corpus 都和它本该改进的那个臂逐字节相同，而"SME 不提升准确率"这个结论，
在这个账本 bug 被发现之前，已经报告了好几个星期。那份"oracle"语料
库——为了确立"任何语义层最多值多少"这个上限而构建的 9154 条 gold 业务
规则——把每条笔记的 scope 都写成了 `scope: ['<schema>']`。而 scope 匹配
要的是 `schema:<name>`、一个 `db:` 前缀、或者一个裸的资产 id；一个裸的
schema 名字这三种都不匹配。全部 9154 条笔记都悄悄匹配失败，一条都没有
送进提示词，按问题计算，提示词变化量的中位数只有一个 token。由此得到的
"多答对 5 题，不显著"被当作"丰富语义层这条路已经走到头了"的证据发表了
出来，后面还有一份路线图是建立在它之上的。这两次失败都是无声的，因为
流水线里没有任何东西去断言处理确实已经送达——所有存在过的检查全都通过了。

现在，交付情况是从一次运行本就会产出的那些行里测出来的，分两个层面。

**按臂**，`fingerprint_arm()` 从一个臂的生成记录行里构建出一个
`ArmTreatment`：有多少行记录了任何交付字段（`n_rows_observed`——这里的
缺失代表"未验证"，不是零）、有多少行带着被注入的笔记
（`n_rows_with_notes`、`n_notes_injected`、`distinct_note_ids`）、又有
多少行带着 `context_hash`（`n_rows_with_context_hash`、
`distinct_context_hashes`、`mean_context_chars`）。只有至少一行记录了
什么，`observed` 才是 `True`；什么都没观测到时，`note_injection_rate`
是 `None`，不是 `0.0`——遵循的是本文其余部分同一条"分母为空"的规则。
这会按臂落进 `summary.json` 的 `arms.<arm>.treatment` 里。

**按配对**，`compare_arms()` 从两个臂在它们共同拥有的那些问题上的
`context_hash` 值构建出一个 `PairDivergence`。`context_hash` 是对一个
问题实际组装出的提示词内容的指纹——corpus 笔记、few-shot、schema 上下文
——所以两个臂在同一个问题上交给模型相同的 hash，不管它们各自硬盘上的
corpus 写着什么，交给模型的就是同一份提示词。只有*两边*都记录了 hash 的
问题才会计入 `n_comparable`；任何一边缺 hash 的问题会被排除，单独计入
`reasons`，这样一次早于 hash 记录出现的运行，读起来是"未验证"，而不是
"一致"。`divergence` 是 `n_different / n_comparable`，`delivered` 只有
在它越过 `DEFAULT_MIN_DIVERGENCE`（0.05）之后才是 `True`——而不是 1.0，
因为两份 corpus 完全可能在"谁都没有额外可说的话"的问题上合理地保持
一致，而在一个覆盖面很宽的基准测试里，大多数问题也确实只涉及少数几张表。
但如果几乎每个问题都一致，那就说明这两个臂其实是同一个实验跑了两遍：
前面那次 oracle 故障的 divergence 恰好卡在 0.0，而一个真正生效的处理，
在同一个基准测试上几乎挪动了每一行。这会落进 `summary.json` 的
`treatment_divergence` 里，每对臂一条记录，由 `divergence_table()` 渲染
到控制台。

所以，缺失的 `context_hash` 读作**未验证**，绝不读作**已交付**：只要
`n_comparable` 是 `0`，`PairDivergence.delivered` 就是 `False`，它的
`reasons` 会明确说明"这两个臂是否有差异"这件事本身是未验证的，而不是
"已验证过、确认相等"。这和本文别处 `crash_rate` 遵循的失败即拒规则是
同一条规则：一次缺失的测量，不是一次干净的测量。

这决定了一次运行自己报出的数字能不能被引用。`eval/index.py` 的
`record_for_run()` 会从 `summary.json` 里把 `treatment_divergence` 和
每个臂的 `treatment` 块读回来，把任何"未交付"的情形折进
`treatment_not_delivered`，`quotable()` 会把它追加进自己的理由列表——
一次臂与臂之间实际上从未分化过的运行，或者一次 corpus 明明持有笔记却
一条都没注入的运行，在台账里都不可引用，不需要任何人凭记忆去想起 SME
或 oracle 那两次事故才知道要检查这个。`treatment.treatment_reasons()`
是同一套检查作为一个独立函数、面向一组指纹与 divergence 提供的版本
（`tests/test_failure_attribution.py` 直接跑它）；台账里的
`record_for_run()` 是通过它自己的 `_undelivered()` helper 读取同样的
产物，而不是直接调用它，所以两者检测到的东西是等价的，调用路径却不是
同一条。

## 噪声下限与最小可探测效应

serve 路径不是确定性的，也没法把它钉死：模型背后架着一个代理，会丢弃
`temperature` 参数，所以不管存在多少采样噪声，都是这套配置固有的成本，
不是这个项目能拧下去的一个旋钮。`eval/power.py` 存在的原因是，这份噪声
此前从未被测量过，而这正是一次本该"无法判定"的比较，被报告成"确实为零"
的原因所在。

**下限是测出来的，不是假定的。** `run_datalake.py` 上的 `--replicate
ARM` 会把某个臂的 corpus 用 `ARM__replicate` 这个名字再服务一遍，追加
在服务顺序的最后，这样一次中途挂掉的运行，真正的臂仍然能被打上分。
`measure_floor()` 接着比较两次运行的 `correct_by_question()` 映射，数
它们有多少次意见不一致——在上一次完整基准测试里，把同一个臂拿去和它
自己重跑一遍，2030 个问题里有 135 个的对错判断变了，尽管头条 EX 数字
几乎没动。`NoiseFloor.suspect` 是对这次复制本身的一次合理性检查：如果
分歧的*净值*相对于它的离散程度显得过大（`abs(net) > 2 *
sqrt(n_discordant)`），那这两次运行其实并不是同一套配置，从它们身上量
出来的下限也就不是一个下限。

**最小可探测效应由这个下限推出。** `minimum_detectable_effect()` 把
测得的分歧率和问题总数，换算成这次运行上的 McNemar 检验在
`alpha=0.05`、`power=0.80` 下能判定为显著的最小真实差异。以 2030 个
问题里 135 个分歧为例，换算出来大约是 33 题，合大约 1.6 EX 点。这个
数字会在展示任何差值*之前*先报出来，因为读者需要先知道这次运行本身能
看清多细的差异，再去看它实际看到了什么。这也让此前那个错误变得具体
可查：一份曾经发表过的"多答对 5 题，不显著"的结果，比这次运行本身能
分辨的最小效应还要小了大约 6.5 倍——这不是"干预毫无作用"的证据，而是
"这个实验本来就分辨不出差异是不是噪声"的证据。

**每一次比较都是配对比较**，绝不是拿边际比率去做差。`mcnemar()` 只在
两个臂都回答过的问题上计算精确的双侧 p 值，用的是精确二项分布尾部，
而不是卡方近似，因为这里的分歧计数经常小到会让近似产生误导
（`_binomial_two_sided` 只有过了 `_EXACT_LIMIT`——4000 对分歧样本——
才会退回正态近似，而到了这个规模，正态近似的精度早就远超所报告的
位数了）。配对之所以重要，是因为它抵消了问题难度——这是一个问题难度
从平凡到根本答不出来的基准测试里，方差的最大来源：拿边际比率去做差，
会把检验力花在重新发现"有些问题就是难"这件事上，而配对检验不需要这么
做，这正是它能挽回大部分因温度参数钉不死而损失掉的分辨率的原因。

`comparison_report()` 把一个 `McNemarResult` 和这次运行的
`NoiseFloor`、`DetectableEffect` 打包进同一个 dict——`net_questions`
在 `summary.json` 里绝不会脱离 `detectable` 和 `reading` 单独出现，
所以读一个差值，就必须同时读到这次运行到底有没有能力分辨出它。没有
replicate 时，`reading` 会直说："这次运行没有测出噪声下限——报告的
显著性，是在不知道这次运行能分辨到什么程度的情况下给出的。"这是一个
按运行计算的测量值，不是一个常数，目前也只有 `run_datalake.py` 会算
它——`run_experiment.py` 的单库阶梯完全没有接入 `--replicate` 或
`power.py`，所以一次单库运行报出的差值不带 McNemar 检验，也不带任何
标明的分辨率。

**零次观测到的分歧不等于零噪声。** `minimum_detectable_effect()` 以前在
一次 replicate 恰好和自己处处一致时，会返回 `0.0` 题——这会让
`resolves()` 对*任何*效应都判为真，包括零效应本身。这与这个模块本来的
目的正好相反，而且在小规模运行上咬得最狠，因为在小规模下零分歧本来就
平平无奇，谈不上有信息量。`n` 次试验里出现零事件，能把发生率约束在大约
`3/n`（三法则）以内，所以现在下限会退回到三对分歧，并把自己标记为
`from_zero_discordance`——这是一个界，不是一次测量。如果完全没有可配对
的问题，结果就是 `measured=False`，`resolves()` 答 `False`，`reading`
会说分辨率未知；"我们判断不出来"绝不能被读成"是"。

## 两件问题级别检验做不到的事

**多重比较。** 一次四臂运行会产生六个两两配对的 McNemar 检验。每一个都
按名义上的 `alpha=0.05` 单独判定时，整个检验家族里至少出现一次假阳性的
概率大约是 26%，而以前每一个检验都是当作独立结果单独报告的。
`holm_adjust()` 在整个家族上应用 Holm–Bonferroni 逐步递减校正，每次比较
除了原始的 `p_value`，还带上 `p_value_holm`、`family_size` 和
`significant_holm`。

这个家族只包含“确实在检验某个本次运行真的在问的假设”的那些配对，它比
“磁盘上的所有配对”要窄，窄在三处。诊断性质的 **oracle** 档不算进去。
**`--replicate`** 那个臂以及它参与的每一对也不算：它存在的意义是测量噪声
下限，而它组成的每一对，都只是它的源臂已经组成的那一对的重复——否则一次
四臂运行加一个 replicate，就会在十个检验上做校正，而真正被问出来的只有
六个不同的问题。**完全没有共享问题**的配对也不算，它的 `p_value = 1.0`
来自一个空的不一致计数——那是“没有东西可比”这件事的算术结果，不是一次
测量。这三处排除的理由是同一个：把检验力花在没人问过的检验上，会让每一个
真实比较都更难判定为显著，这正是校正在跟自己的目的对着干。被排除的配对
仍然会带着它原始的 `p_value` 被报告出来，只是不带校正后的那个。

这里用 Holm 而不是朴素的 Bonferroni，是因为它的检验力一致更高，也不需要
独立性假设——这一点很要紧，因为六个共享着同样几个臂的比较，怎么看都算
不上互相独立。

**聚类。** 配对检验把每个问题都当成一个独立观测。但它们并不独立：这些
问题嵌套在大约 69 个难度和 schema 形态差异很大的数据库里，所以一次恰好
适合其中五个数据库的 corpus 改动，就能产生上百个相关联的"胜出"，得出的
p 值会以一个未知的倍数偏向乐观。`cluster_sign_test()` 把分析单位挪高
一级——按每个数据库上每个臂答对了多少题给数据库打分，然后统计有多少个
数据库变好、多少个变差，做一次精确的符号检验。它的检验力被刻意做得比
问题级别的检验低；问题级别检验看起来多出来的那部分检验力，很大程度上是
靠一个数据并不支持的独立性假设借来的。每次比较都带着一个 `cluster` 块。
两者都要看：两者一致会让人安心；而一次问题级别的胜出，如果聚类检验看不
见，就说明这个结果其实只压在少数几个 schema 上，这一点最好在它变成一个
结论之前就弄清楚。

## 阶梯：每一步只变一件事

差值只在*相邻*两档之间报告，而这些档位的排列顺序保证了每一步只改变一
件事。`ladder_steps()` 是从一次运行实际打过分的那些臂里推导出这些档位，
而不是套用一个固定列表，所以一次只跑了部分 `--arms` 的运行，也能把手头
有的档位串起来；`skipped_rungs()` 会点名任何非相邻步骤到底捆绑了什么，
驱动器既会把它记进 `deltas.*_bundles`，也会把它打印出来。

`deltas.*_correct_answers` 是**配对**净增益，只在两个臂的问题 id 集合相同
时才有（和计价同一题池）。N 相等但题池不同、缺 id、或 N 不等时，该字段为
`null`，原因写在 `*_correct_answers_unmeasured_because`。原始的 `n_correct`
相减若还出现，只会叫 `*_unpaired_n_correct_delta`——这个名字是故意的，不能
当成“多答对了几题”来引用。

| 步骤 | 新增了什么 | 构建成本 |
|---|---|---|
| `baseline → seeded` | 训练集 SQL 推出的连接与指标，诱饵 / 负空间标记；去掉 baseline 的 FK 名猜测。**没有 few-shot。** | 无——不调用模型 |
| `seeded → curated` | curator LLM agent（含 few-shot），作用在同一份种子之上 | 每个 db 一次 curator 跑批 |
| `curated → curated_sme_blind` | SME 澄清协议 | 每个 db 一轮 SME |
| `curated_sme_blind → curated_sme` | BIRD 人工撰写的列文档，写进 SME 的任务简报里 | 每个 db 一轮 SME |

其中两档之所以存在，是因为它们上面那一档比较原本是复合的。

`baseline → curated` 把 `build_curated_corpus` 的*机械*那一半（训练集
SQL 推出的连接与指标，加上把 gold 里没出现过的列标记成诱饵 / 负空间）和
*LLM* 那一半（撰写 few-shot 以及其余 Inference 层）捆在了一起。两者以前总是一起跑，所以这个差值既可以解释成
来自那趟免费、多机制的确定性种子——本身也不是“单靠解析”——也可以解释成来自 curator
agent，说不清是哪个。`seeded` 就是同一条代码路径，只是把
`run_agent=False`：只有连接与指标，没有 few-shot，构建不花模型调用。不要把
`baseline → seeded` 当成 few-shot 提升，也不要当成单机制的解析效应。

`curated → curated_sme` 把澄清协议和一个新的信息来源捆在了一起。
Simulated SME 的任务简报是基于 BIRD 的 `database_description/*.csv`——
人工撰写的列与取值说明——构建的，而 Phase A 从来拿不到这个目录。
`curated_sme_blind` 跑的是完全相同的一轮，但简报只基于训练问题和
evidence 构建，这些 curator 本来就有。它是可选项，因为每个数据库都要
多花一整轮 SME 的成本；当它被省略时，那个复合步骤会如实标注自己捆绑了
什么，而不是冒充成单变量的一步。

## 并发，以及它被允许改变什么

两个各自独立的旋钮，因为它们耗尽的是不同的资源。`--workers` 把按问题的
serve 循环并行开来，受限于 Postgres 的 `max_connections`；
`--build-workers` 把整个 curator 构建并行开来，受限于模型供应商的速率
限制，因为每个构建都同时占着一个数据库连接*和*一段 deep-agent 对话。

两者都不是恢复用的旋钮。它们改变的只是一次运行要花多久，从不改变一行
打分记录的含义；按构建隔离也让"换一个并发度去恢复"这件事是安全的——所以
它们会被记进 manifest，但被刻意排除在 `_RESUME_KNOBS` 之外。

构建循环到目前为止一直是串行的，而让它必须串行的不是这些数据库本身
（它们彼此独立，各自有自己的 schema 和连接器），是*文件系统*：curator
把它的五个 sidecar 都写在**臂的根目录**下，而
不是按 schema 分开，也把 deep agent 的 `FilesystemBackend` 指向了同一个
根目录。两个并发的构建会把写入交织进同一份 `clarifications.jsonl`，对
SME 臂来说，这意味着一个 schema 的澄清文本会混进另一个 schema 的
corpus 里。所以现在每个构建都跑在自己私有的暂存根目录下，成功后才被
提升为正式产物，这样一次构建*内部*的所有路径关系，都和串行时逐字节
相同。这是刻意的：曾经有一次这些路径被重新指向过别处，SME 臂就从一个
被构建步骤挪走的目录里读它的账本，好几个星期里所有 SME 数字都成了
空跑。

暂存目录会在每次构建开始时清空；只有带 `BUILD_COMPLETE.json` 的构建才算完成，
半成品 YAML 不会被后来的恢复误读成"已经构建过了"。

一次速率限制风暴是可读的，不是无声的：那些轮次会被分类为崩溃（而不是
拒答），这会阻断可引用性，`arms.<arm>.by_error_type` 会说明这些崩溃到底
是 `RateLimitError`——该缩窄范围重跑——还是别的什么问题。

## 症状 → 字段 → 文件

| 你观察到什么 | 该看哪个字段 | 它落在哪个文件 |
|---|---|---|
| EX 掉了，分不清是 bug 还是真的拒答 | `outcome`（`crashed` 还是 `refused`）、`failed_stage` | `generations.<arm>.jsonl`；`governed_bi.stages.classify_row` |
| `refusal_rate` 变了，不知道是哪一层做的判定 | `by_failed_stage`、`by_guardrail_layer` | `summary.json` |
| 单库阶梯运行的崩溃计数，和它的拒答分开看 | `ArmSummary` 上的 `crash_rate`、`n_crashed`、`by_outcome` | `summary.json`（`run_experiment.py`） |
| serve 路径里有什么东西抛了异常，需要知道在哪 | `status=="error"` 且带 `detail.error_type` 的 `stage_events` 记录 | `stage_events.jsonl` |
| 一个你没见过的 `refused_by` 取值 | `n_unmapped_refused_by`；打印出来的 `*** WARNING: unrecognised refused_by=...` | `summary.json` / 驱动器 stdout；`stages.py` 里的 `REFUSED_BY_TO_STAGE` |
| `routing_recall` 高得或低得不合常理 | `n_routing_observed`（排除崩溃*和*被绕过的轮次） | `summary.json` |
| 明明跑过的臂上 `routing_recall` 却是 `null` | `n_routing_bypassed`——只有一个 schema 的池子，或者一档 oracle，根本没有路由决策可打分，"没测量"比 0.0 和一个自我表扬式的 1.0 都更诚实 | `summary.json` |
| `routing_recall` 是 `null`，而且也没有任何行被绕过 | `n_routing_unrecorded`——这些轮次在 `assemble` 运行之前就结束了，根本没有决策可打分。在一个正常跑着的臂上如果这个数不是零，说明 serve 路径正在丢失溯源信息 | `summary.json` |
| `cond_ex_given_routing` 和 EX / routing_recall 对不上 | 五个 `n_correct_*` 桶——EX 是在每一行上算的，路由相关的几项不是，所以恒等式是 `n_correct == routed + unrouted + bypassed + routing_unrecorded + routing_crashed`，`n_correct_unaccounted` 是那道核对 | `summary.json` |
| 一个比率读数是 `0.0`，分不清到底测没测过 | 该字段的分母计数（`n`、`n_produced`、`n_routing_observed`……）——`None` 表示没测过，`0.0` 表示测了且为零 | `summary.json` |
| EX 分母看着被一堆没有可用 gold 的行注了水 | `n_gold_unusable`（与 `n_missing_gold` 并列） | `summary.json` |
| 某个 db 的诱饵触碰率干净得可疑 | `decoy_manifest_missing_dbs` | `summary.json`（`run_datalake.py`） |
| "这个臂到底探索了多少？" | `n_tool_calls`（按行）、`tool_calls`（求和） | `generations.<arm>.jsonl`；`summary.json` |
| 这次运行的 EX 能不能安全引用 | `ledger_ok` / `quotable`（仅卫生），再走运行手册声明清单；不要单靠台账里的 `claim_ready` | `runs/index.jsonl` |
| 两次运行是不是真的同一个实验 | `comparable(a, b)` 的差异列表 | `runs/index.jsonl`，经 `eval.index` CLI |
| 答案错了但 schema 选对了——是检索问题还是生成问题？ | `table_selection_report()`：`n_retrieval_miss` 对比 `n_selection_miss` | `analysis.json`，经 `eval.analysis` |
| 一个错误答案的阶段和种类，而不只是"schema 选对了、SQL 写错了" | `by_error_stage`、`by_error_primary`、`error_class_incidence`、`n_classes` | `summary.json`（`arms.<arm>.errors`）；`governed_bi.eval.error_taxonomy` |
| 按类别的计数看着可以直接加总，让人很想拿它算一个可提升空间的数字 | `multi_class_share` | `summary.json`（`arms.<arm>.errors`） |
| 一个阶段的失败到底值多少，而不只是出现了多少次 | oracle 档（`oracle_sql`/`oracle_schema`/`oracle_tables`） | `governed_bi.eval.oracle`；见[oracle 阶梯](oracle-ladder.zh.md) |
| 两个臂可能其实是同一个实验跑了两遍 | `treatment`（按臂）、`treatment_divergence`（按配对） | `summary.json`；`governed_bi.eval.treatment` |
| 一堆崩溃摆在眼前，分不清是速率限制还是 bug | `by_error_type`（按臂）、`error_type`（按行） | `summary.json`；`generations.<arm>.jsonl` |
| 一个错误答案是什么都没返回，还是返回了和 gold 行数相同的结果 | `by_result_shape`（按臂）、`result_shape`（按归因） | `summary.json`（`arms.<arm>.errors`） |
| 错误答案完全没有被记到任何阶段头上 | `n_unattributed`——不可用的 gold，加上 differ 解析不出来的行（`unresolved_diff`） | `summary.json`（`arms.<arm>.errors`） |
| 一次四臂运行里，某个差值看着很显著 | `p_value_holm`、`family_size`——六个两两检验各自按名义 0.05 判定，整个家族的错误率大约有 26% | `summary.json`（`comparisons[]`） |
| 怀疑一次问题级别的胜出其实是靠少数几个 schema 撑起来的 | `cluster`——一个以数据库而非问题为单位的符号检验 | `summary.json`（`comparisons[]`） |
| 两个臂之间的差值，而它们并不是相邻的两档 | `deltas.<hi>_minus_<lo>_bundles`——这一步跳过了哪些档位，也就是它同时改变了不止一件事 | `summary.json` |
| 每多答对一题的成本 / 一档买到了多少正确答案 | `deltas.*_correct_answers`（仅问题 id 集合相同的配对）、`*_usd_per_added_correct`；题池不同时 `*_correct_answers` 为 `null` 并带 `*_correct_answers_unmeasured_because`，原始计数差只出现在 `*_unpaired_n_correct_delta` | `summary.json` |
| 一次运行的 EX 因为处理从未真正送达而不可引用 | `treatment_not_delivered` | `runs/index.jsonl` |
| "多答对 N 题"这样的差值，可能只是采样噪声 | `comparisons[].detectable`、`comparisons[].noise_floor`、`comparisons[].reading` | `summary.json`（`run_datalake.py`，仅在使用了 `--replicate` 时） |
| `routing_recall` 是 0.0，而且每个错误答案都怪到选择器头上 | `routing_bypassed`——为真表示路由器压根没启动（被钉死的 oracle corpus，或者池子里只有一个 schema），那它就不可能"选错" | `generations.<arm>.jsonl` |
| 一个数字到底出自公平臂，还是出自读了答案的档 | `oracle_rung`（公平臂上一律是 `None`）、`arms_run` 与 `fair_arms` 的差异 | `generations.<arm>.jsonl`；`summary.json` |
| 配对比较里某个臂看着少了几条 | `question_coverage.incomplete_arms` | `analysis.json` |
| eval 之外，想看某一轮自己的耗时/工具调用/护栏历史 | `load_run_record(turn_id, settings)` | 可移植运行日志（ADR 0004），不只是 eval 产物 |

## 提示词归因

`prompt_set_hash` 是 `comparable()` 用到的键之一，现在两个驱动器都会写
它：`run_datalake.py` 的池化 manifest 与 `run_experiment.py` 的单库
manifest 都会打上 `prompt_variants` + `prompt_set_hash`，所以两次用了
不同提示词变体的单库运行，会像两次池化运行一样被标记为不可比。完整的
归因链路——从 registry 到打好戳的行，再到 manifest，再到台账，外加失败
即拒的契约，以及"一个已测出的失败到底该换哪个变体"的判断表——都在
[提示词变体实验](prompt-experiments.zh.md)里。
