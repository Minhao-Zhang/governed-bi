# 提示词变体实验：registry、选择与归因

_对应英文文档：[prompt-experiments.md](prompt-experiments.md)（该文档目前没有语言切换行，此处仅作对应标注）_

提示词以前是裸的模块级字符串，一个提示词唯一的身份标识就是它住在哪个文件
里，别无其他。这带来两个后果，都是测量上的失败：`baseline`/`curated`/
`curated_sme` 这条阶梯是一根**语料内容**轴，三个臂发出的提示词文本逐字节
相同，所以换臂从来不会改变提示词，也没有任何东西记录下这一点；而
`serve_config_hash` 里根本没有提示词文本的概念，于是"我们改了提示词、EX
变了"这句话事后根本无从证伪——两次用了不同提示词的运行，在记录里没有任何
区别，一个*被编辑过*的提示词，和它替换掉的那个提示词，在记录里也分不出来。

`src/governed_bi/prompts/registry.py` 把这两个问题都修好了：一个阶段映射
到一组具名的变体，一次运行为每个阶段解析出一个变体，这份映射会**按文本**
做哈希，并端到端地打上戳。这份文档就是操作手册——怎么新增一个变体、怎么选
一个、怎么读出打了什么戳，以及一个已测出的失败到底该换哪个变体。

> 实现：[`src/governed_bi/prompts/registry.py`](../src/governed_bi/prompts/registry.py)、
> [`src/governed_bi/prompts/__init__.py`](../src/governed_bi/prompts/__init__.py)。
> 测试：[`tests/test_prompt_registry.py`](../tests/test_prompt_registry.py)、
> [`tests/test_prompt_attribution.py`](../tests/test_prompt_attribution.py)、
> [`tests/test_prompt_attribution_gaps.py`](../tests/test_prompt_attribution_gaps.py)。

## Registry

`PromptVariant(stage, variant, text, rationale)` 是某一个阶段的一份具名
提示词文本。`rationale` 不是装饰性字段：dataclass 的 docstring 说得很
直白——"一个 rationale 说不出任何可观测失败模式的变体，只是个旋钮，不是
一次实验。"`REGISTRY`（`stage -> variant id -> PromptVariant`）在导入时
从一个扁平元组构建而成，遇到重复的 `(stage, variant)` 对会抛出
`RuntimeError`，所以一条新记录里的复制粘贴笔误会在导入时就报错，而不是
留到日后某次查找才现形。`DEFAULTS` 把每个阶段都映射到 `"v1"`。

目前登记了六个阶段，各自的 `v1` 都和 registry 出现之前这套系统发出的文本
逐字节相同：`agent_core`、`schema_pick`、`narrator`、`curator_phase_a`、
`curator_phase_b`、`sme_rules`。原来那些模块级常量（`analyst/agent.py`
里的 `SYSTEM_PROMPT`、`retrieval/schema_router.py` 里的
`SCHEMA_PICK_SYSTEM`、`analyst/narrate.py` 里的 `_NARRATOR_SYSTEM`、
`curator/prompts.py` 里的 `_PHASE_A_PROMPT`/`_PHASE_B_PROMPT`、
`curator/sme.py` 里的 `_SME_SYSTEM_RULES`）现在都是从 registry *派生*
出来的（`prompts.get(stage).text`），不再各自持有一份副本，于是调用点和
registry 不可能悄悄产生分歧——`test_prompt_registry.py` 为每个阶段的
`v1` 文本钉死了一个 sha256 摘要，作为额外的防线，防止一次就地编辑悄悄
改写了所有既有测量数字的基准。

几个核心函数：

- `get(stage, variant="v1")`——返回一个 `PromptVariant`，或者抛出一个
  点名了合法 id 的 `KeyError`。
- `resolve(overrides)`——一次运行完整的 `stage -> variant` 映射：
  `DEFAULTS` 加上覆盖项，每个阶段永远都在。一份局部映射、一份空映射，
  以及一份显式写全默认值的映射，解析结果完全相同——这份映射描述的是
  *实际发出了什么*，而不是调用方碰巧是怎么拼写的。
- `text(stage, variants_map)`——在那份映射下，`stage` 应该发出的文本。
- `prompt_set_hash(variants_map)`——对排序后的 `(stage, variant,
  sha256(text))` 三元组做 sha256。参与哈希的是**文本**摘要，不只是变体
  id，所以就地编辑 `v1` 会挪动这个哈希——这正是 `serve_config_hash` 那份
  手工维护的字段列表、在这个模块出现之前掉进去的那个坑。
- `parse_cli_overrides(items)`——把重复出现的 `--prompt stage=variant`
  字符串，转换成一份经过校验的覆盖映射。
- `stages()` / `variants(stage)`——已知的 id 列表，供报错信息与 CLI 帮助
  文本使用。

只有文本常量与纯函数：没有 I/O，不导入配置，不涉及模型。serve 路径与
curator 路径都会导入它，`provenance.py` 也从它这里取哈希，所以这个模块
一旦长出一个依赖环，就会同时打断两个方向（和 `governed_bi.stages` 是
同一种形状——见 [测量](measurement.zh.md)）。

## 三个真正存在的变体

`v1` 之外的每一个变体，都是因为 `eval.analysis` 或 `summary.json` 测出
了某个具体的失败模式、并把它点名出来才存在的。下面逐字引用 `registry.py`
里写的 rationale，这样即便 registry 继续长大，这些说法也不会失真：

| 阶段 | 变体 | Rationale |
|---|---|---|
| `schema_pick` | `v2` | “Forces one explicit rejection reason per candidate, turning a topical-similarity guess into a column-vocabulary check, and moves the answer onto a strict FINAL: line. Refuted if `pick_accuracy` in the `by_gold_rank['1']` bucket does not rise — no other bucket is its fault.” |
| `agent_core` | `v2` | “Makes the suspect/duplicate-copy check its own step with visible output, so a long context cannot bury it. Refuted if `n_selection_miss` does not fall with `n_retrieval_miss` flat (also watch `decoy_touch_rate` and `total_tokens`).” |
| `agent_core` | `v3` | “Commits to the output columns and grain before writing SQL, targeting the right-rows/wrong-projection class. Refuted if `n_wrong_but_nrows_match` does not fall, or falls without `ex_gradeable` rising by about the same count.” |

具体来说：`schema_pick@v2` 要求 picker 为每个候选写一行——要么是覆盖问题
每个部分的那些列，要么是它无法满足的第一个部分——然后再在一行
`FINAL: <schema 名称>` 上点名 schema（`v1` 只是让它自由推理、在最后一行
点名 schema）。`agent_core@v2` 把"选之前先读每张表的描述"变成了独立的
一个编号步骤，必须说明拒绝了哪张表、为什么拒绝。`agent_core@v3` 加了一个
第零步：在写 SQL *之前*先写清楚确切的输出列与粒度，然后拿最终的
`SELECT` 列表对照这句话检查，删掉不在这句话里的一切。

`narrator`、`curator_phase_a`、`curator_phase_b`、`sme_rules` 目前都只有
`v1`，各自的原因都写在它们自己的 rationale 里：narrator “runs after
grading and cannot move EX”（跑在打分之后，不可能撼动 EX——没有任何失败
模式可以用来衡量一个 narrator 变体）；`curator_phase_a`/`curator_phase_b`
的变体 “means rebuilding every corpus to test it”（意味着要重新构建整个
corpus 才能测试——没有针对一个已经建好的 corpus 做低成本 A/B 的办法）；
`sme_rules` 是 “the rules block inside the code-assembled SME brief (the
rest of that brief is data, not a prompt variant)”（代码拼装出的 SME
brief 里那一块规则文本——brief 的其余部分是数据，不是提示词变体）——brief
的大头是 BIRD 的列描述与训练集 evidence，这些不该由 registry 来管版本。

## 新增一个变体

往 `registry.py` 的 `_ALL` 元组里追加一个 `PromptVariant(stage=...,
variant=..., text=..., rationale=...)`，rationale 要点名能推翻它的那个
指标。除此之外不需要改任何东西它就能被选用：`get`/`resolve`/`text`/
`prompt_set_hash` 全都直接读 `REGISTRY`，`--prompt`/`[prompts]` 也都对着
`variants(stage)` 做校验。一个重复的 `(stage, variant)` id 会在导入时
抛出 `RuntimeError`，所以一个和已有 id 撞车的笔误会立刻报错，而不是悄悄
遮住原来那一条。

给一个*已有*阶段加变体（比如 `agent_core@v4`）不需要别的东西。加一个
*新阶段*则还需要：为它登记一个 `v1` 基线，让调用点从
`prompts.get("new_stage").text` 派生自己的常量、而不是各自持有一份副本，
再在 `test_prompt_registry.py` 的 `V1_DIGESTS` 里加一条钉死的摘要——
`test_every_registered_stage_has_a_pinned_v1_digest` 会断言
`set(prompts.stages()) == set(V1_DIGESTS)`，不满足就失败。

具体到 `curator_phase_a` / `curator_phase_b` / `sme_rules`：没有低成本的
办法能在一个已经建好的 corpus 上试一个新变体。要测试它就得在这个变体下
重新构建 `curated` / `curated_sme`，这正是这三个阶段目前只有 `v1` 的
原因。

## 选择一个变体

有两套各自独立的机制，它们的组合方式可能和你预想的不一样。

**`governed_bi.toml` 里的 `[prompts]`**（`Settings.prompt_variants`）是
*线上 serve 技术栈*会读的那一份——`api.stack.build_stack` 只调用
`load_settings()`，不做别的，所以这张 TOML 表是一次部署能跑在非默认提示
词上的唯一途径。它在加载时就会被校验：`load_settings()` 会拿 `[prompts]`
表去调 `prompts.resolve()`，把一个错误的阶段或变体重新抛成一个点名了
配置路径的 `ValueError`，所以一个笔误会在启动时就拖垮整个进程，而不是
让文件里写着 `v9`、实际却悄悄服务着 `v1`。

```toml
[prompts]
schema_pick = "v2"
agent_core = "v3"
```

**`--prompt STAGE=VARIANT`**（可重复传入）是 `eval/run_datalake.py` 与
`eval/run_experiment.py` 都有的一个 CLI 参数，由 `parse_cli_overrides()`
解析，并且在连接 Postgres 或调用模型*之前*就完成解析——一个错误的
`--prompt` 会以 `parser.error()` 的用法错误退出，而不是跑到一半才崩溃。

对 eval 驱动器来说，这两套机制不会合并。`run_datalake()` 与
`run_experiment()` 都是从 `Settings.for_env(Environment.dev,
models=base_settings.models, ...)` 各自构建 `Settings`——从
`load_settings()` 带过来的只有 `.models`，`prompt_variants` 是单独从
`resolve_prompts(prompt_variants)` 设置的，这里的 `prompt_variants` 就是
`--prompt` 产出的那份（没传就是空）。**在 `governed_bi.toml` 里设置
`[prompts]` 对任何一个 eval 驱动器都不起作用**——对一次实验来说，
`--prompt` 是唯一的杠杆。

## 每一跳打的戳

1. `Settings.prompt_variants`——一份局部或完整的 `stage -> variant` 映射。
2. `build_serve_rails` **每次构建技术栈只解析一次**，不是每轮都解析：
   `prompt_variants = prompts.resolve(settings.prompt_variants)`，然后
   `agent_core_prompt` / `schema_pick_prompt` 只计算一次，被图的各个节点
   闭包捕获。`agent_core_node` 会在变体文本*之后*追加
   `## Governed context` 和 `## Current time`——变体替换的是指令块本身，
   绝不会替换已组装好的上下文。
3. `serve_config_hash(settings)` 无条件地把
   `prompt_set_hash(settings.prompt_variants)` 折进去。哪怕一次运行什么
   变体都没选，也会去哈希 `v1` 的文本，所以就地编辑 `v1` 会挪动每一次
   默认运行的 `serve_config_hash`，不只是那些主动选用了某个变体的运行。
4. `Answer.provenance` 带着 `prompt_variants`（完整的解析结果映射）和
   `prompt_set_hash`，由 `finalize_and_log` / `emit_run_record` 打戳。
   这两个键都列在 `METADATA_PROVENANCE_KEYS`
   （`src/governed_bi/analyst/run_log.py`）里——这是每一个终态 `Answer`
   都必须携带的字段集合，和 `turn_id`、`run_id`、`corpus_release_hash`
   等字段并列。
5. 可移植运行记录（`load_run_record`）带着同样这两个键，所以在 eval 之外
   查一轮——不管是从 `runs/` 还是从持久日志里——依然能说出是哪套提示词
   集合产出了它。
6. `eval.arms.agent_solver` 把这份戳从 `Answer.provenance` 转接到求解器
   逐问题的元数据里。一轮**没打戳**的记录，两个键都会转接成 `None`，绝
   不会是 `v1` 默认值——"没记录跑的是哪套提示词"和"跑的是 `v1`"是两件
   不同的事实，只有后者才能打印成 `v1`。
7. `generations.<arm>.jsonl` 里打过分的行带着 `prompt_variants` /
   `prompt_set_hash`（`run_experiment.py` 里的 `_run_arm_generations`，
   `run_datalake.py` 里的 `_run_pool_arm`）。
8. `manifest.json`——现在**两个**驱动器都是——带着解析出的映射与哈希。
   在 `run_datalake.py` 里，这个哈希同时也是一条 `_RESUME_KNOBS` 记录
   （见下文"失败即拒"）。
9. `eval.index.COMPARABILITY_KEYS` 包含 `prompt_set_hash`，所以
   `runs/index.jsonl` 的 `comparable(a, b)` 会把两次运行之间的提示词
   集合差异按名字标出来。

## 为什么 curator 与 SME 的生产者不能再重新推导 Settings

`build_curated_corpus`、`build_curated_corpus_with_sme`、`SimulatedSme`
现在都接收一个 `settings` 参数，并从它——经由
`pipeline._settings_or_load(settings)` 与 `SimulatedSme._resolved_settings()`
——来给自己的运行记录打戳（`emit_run_record`），而不再重新调用一次
`load_settings()`。这两个 helper **只有**在拿到 `None` 时才会回落到重新
加载（对应独立 CLI 用法、没有调用方已解析好的配置的情况）。

这件事之所以要紧，是因为 registry 落地之后，一次对抗性评审发现了一个
缺口（见 `tests/test_prompt_attribution_gaps.py`）：一个用
`--prompt curator_phase_a=v2` 构建出来的 corpus，它的 curator agent
确实跑在 `v2` 文本上（通过 `system_prompt=prompt_text("curator_phase_a",
...)` 显式传入），但如果运行记录的戳来自一次*重新*调用的
`load_settings()`，而不是调用方已经解析好的那份 `Settings`，那条记录
读到的就会是 `governed_bi.toml` 的 `[prompts]` 写着什么——默认就是
`v1`，因为（如上一节所说）eval 驱动器根本不读 `[prompts]`。实际后果是：
查日志找"提示词集合 X 下产出的每一轮"，会查到 serve 那些轮次（戳打得
没错，因为 `answer_question_agent` 始终能看到调用方的 `settings`），却
会悄悄漏掉构建出那些 serve 轮次所查询 corpus 的 curator/SME 轮次——一个
实验的两个半边，一半归因对了，另一半没对，而且没有任何信号说明出了问题。

## 失败即拒：每一处该报错而不是回退的地方

一个未知的阶段或变体绝不能解析成 `v1`——追踪提示词身份这件事的全部意义，
就在于一次运行要报告它实际发出的那个变体。具体来说，来自测试套件的例子：

- `prompts.get("agent_core", "v9")` / `prompts.get("sqlgen", "v1")`——
  `KeyError`，点名合法的变体或已知的阶段。
- `prompts.resolve({"agent_core": "v9"})`——`KeyError`，不会为那一个阶段
  悄悄回退到 `v1`。
- `--prompt schema_pick` / `--prompt =v2` / `--prompt schema_pick=` /
  `--prompt ""` / `--prompt "schema_pick:v2"`——`parse_cli_overrides`
  抛出的 `ValueError`，在 CLI 碰到 Postgres 或模型之前，就以
  `parser.error()` 的用法错误呈现出来。
- `--prompt agent_core=v2 --prompt agent_core=v3`——`ValueError`（提示
  "twice"）；重复传*同一个*值两次（`agent_core=v2 --prompt
  agent_core=v2`）则无害、依然合法，因为它没有说任何自相矛盾的话。
- `governed_bi.toml` 里的 `[prompts]` 点名了一个未知的阶段或变体——
  `load_settings()` 抛出的 `ValueError`，点名配置路径，所以一个笔误会
  在启动时就拖垮整个进程。
- **在不同的 `prompt_set_hash` 下 `--resume-from` 是致命错误**
  （`RuntimeError`，消息里包含"prompt set"），这和其他每一条
  `_RESUME_KNOBS`（`model`、`route_top_k`、`route_llm_pick`、
  `schema_pick_max_columns`、`use_embedder`、`skip_agent`、`git_sha`）都
  不一样——那些只会打印一条 `*** WARNING: resuming ... with changed
  knobs ***` 然后继续跑。这一条是评审之后才从警告升级成硬错误的：
  `_merge_resume_manifest` 会保留*原始* manifest 的顶层旋钮，把一次恢复
  尝试的取值归档到 `resumes` 里，而 `eval/index.py` 的 `record_for_run`
  只读顶层。这样一来，一个一半在 `v1`、一半在 `v2` 下打分的目录，就会把
  自己呈现成一次干净的 `v1` 运行，还会被拿去和别的 `v1` 运行比较——对于
  其他旋钮，读者至少还能在 manifest 里看到、自己判断要不要紧；混杂的
  提示词集合事后则完全无法判断，因为下游没有任何东西能分清哪些行是哪个
  版本。

## 该试哪个变体，由测量决定，不是靠品味

`eval.analysis.table_selection_report()` 把一次"schema 选对了但还是错"
的失败拆成 `n_retrieval_miss`（gold 表根本没被展示给模型）和
`n_selection_miss`（展示了但没用上）两种；`rank_report()` 的
`by_gold_rank` 分桶则把一次 shortlist 漏检和一次 picker 出错分开。这里
面只有一部分是提示词的问题：

| 信号 | 在哪 | 怎么解读 | 该试什么 |
|---|---|---|---|
| `by_gold_rank["miss"]` 偏大 | `summary.json` → `arms.<arm>.by_gold_rank` | 检索确实跑过，却始终没能把 gold schema 呈现出来 | **不是提示词能修的**——放宽 `schema_route_top_k`，或改进 embedder/shortlist |
| `by_gold_rank["no_shortlist"]` 偏大 | `summary.json` → `arms.<arm>.by_gold_rank` | 没有记录到任何 shortlist——一档 oracle，或者一轮在检索之前就已经结束。不是一次检索失败，以前会被并进 `miss` 里 | 什么都不用做；先确认这个臂是不是你以为的那个 |
| gold schema 排在第 1 位，但 `pick_hit` 是 false | `summary.json` → `arms.<arm>.by_gold_rank["1"].pick_accuracy` | picker 看到了正确的 schema，但选错了 | `schema_pick@v2` |
| `n_selection_miss` > `n_retrieval_miss` | `analysis.json`（`table_selection_report`） | agent core 被展示了 gold 表却没用 | `agent_core@v2` |
| `n_wrong_but_nrows_match` 偏大 | `summary.json`（按臂） | 行数对了，投影或排序错了 | `agent_core@v3` |

提示词没法选一个它压根没看到的东西，所以一次 shortlist 漏检需要的是
检索层面的修复，不管其他数字看起来如何。

`by_gold_rank` 在 `summary.json` 里，每次运行都会写。`table_selection_report`
只在 `analysis.json` 里，而这个文件没有任何流程会自动写出来，得自己跑
`uv run python -m governed_bi.eval.analysis <run_dir>`。

**不要在两半各自单独跑过之前，就去跑一个组合变体。**
配对 McNemar 检验（`eval.power`，导出名 `paired_mcnemar`）只在两次
运行都打过分的*同一个*共享问题池上起作用。引用 delta 时该用这一个，
而不是 `eval.analysis.mcnemar`：只有 `eval.power` 会把这次运行能分辨
到的程度一并报出来。如果 `(schema_pick=v2, agent_core=v2)` 作为一个
组合臂整体去对 `v1` 基线跑，那么在这个问题池上算出的 McNemar 差值
没法把变化归因到任一
半——因为没有"只试过另一半"的运行跑过同一个问题池可供配对，事后这两个
效应（以及它们之间任何交互）都分不开。

## 比较两次运行

**`eval.index` 的可比性检查**（`runs/index.jsonl`、`COMPARABILITY_KEYS`）
会核对 `split`、`model`、`prompt_set_hash`、`route_top_k`、
`route_llm_pick`、`schema_pick_max_columns`、`use_embedder`。两次运行
只在 `--prompt` 上不同，除非其他每一个旋钮都一致，否则会被判定为**不可
比**，报出来的差异会直接点名：`prompt set: '<hash a>' vs '<hash b>'`。

**配对 McNemar 检验**（以 `question_id` 为键）才是两套提示词集合之间
真正的显著性检验，前提是 `eval.index` 已经确认它们在其他方面是可比的。
用 `eval.power` 的那一个——导出名 `paired_mcnemar`，也是驱动器写进
`summary.json` 的那一个。它会把这次运行的噪声下限和最小可探测效应和
p 值并排报出来，所以读一个 delta 时没法绕开"这次运行到底分不分辨得出
它"这个问题。`eval.analysis.mcnemar(rows_a, rows_b)` 是 `analysis.json`
背后的离线版本：同一个精确检验，同样的 p 值，签名不同，但不给出分辨率。
要引用就引用前者。

**在没配对的运行之间比点估计，代替不了上面任何一种检验。** serve 端的
解码并没有钉死，所以*完全同一个*臂、同一套提示词的两次运行，在不小
一部分问题上给出的答案也会不一样（见 [`datalake-run.md`](plans/datalake-run.md)
里那段"数字已作废"的提醒）——一个旧的默认运行和一个新的 `agent_core@v2`
运行之间的原始 EX 差值，完全可能只是解码噪声，跟提示词无关。McNemar 把
不一致的那些配对单独挑出来，只有这部分数据才真正携带"哪个变体更好"的
信息。

## CLI 速查

对 `v2` schema picker 做一次性的单 schema 实验：

```bash
uv run python -m governed_bi.eval.run_experiment --db beer_factory --prompt schema_pick=v2
```

在 `agent_core@v2` 上做一次数据湖试跑，五个 db，输出到自己的目录
（另外跑一次默认调用作为对比用的基线）：

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt agent_core=v2 --out runs/datalake/
```

一次组合改动的两半，在合并之前分别单独试跑、单独测量（是两次调用，不是
在一次调用里传两个 `--prompt`）：

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt schema_pick=v2 --out runs/datalake/
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --prompt agent_core=v2 --out runs/datalake/
```

把两次运行都建入索引，并渲染两两之间的可比性：

```bash
uv run python -m governed_bi.eval.index --add runs/datalake/<ts-schema-pick-v2>
uv run python -m governed_bi.eval.index --add runs/datalake/<ts-agent-core-v2>
uv run python -m governed_bi.eval.index
```

对一次已完成的运行做离线归因（不涉及模型、数据库，也不产生 API 费用）：

```bash
uv run python -m governed_bi.eval.analysis runs/datalake/<timestamp> --bird-dir ../BIRD-Data-Obfuscation
```

一个格式错误或未知的 `--prompt`，会在连接 Postgres 或调用模型之前就
报错：

```bash
uv run python -m governed_bi.eval.run_datalake --prompt sqlgen=v9 --out runs/datalake/
# usage error: unknown prompt stage 'sqlgen'; known stages: agent_core, curator_phase_a, ...
```

**另见：** [测量](measurement.zh.md)了解归因链路的其余部分（outcome/stage
分类体系、运行台账）；[Data-lake run](plans/datalake-run.md) 了解这套
CLI 接线所接入的完整池化实验操作手册；[Analyst LLM-call walkthrough](analyst-llm-call.zh.md)
与 [Curator LLM-call walkthrough](curator-llm-call.zh.md) 了解每个阶段
的提示词在各自调用点里到底做了什么。
