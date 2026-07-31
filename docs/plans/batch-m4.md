# 第四批工作单 · M4 看得见与对得上（N11–N14）

2026-07-31 立。分支从 `impl/rebuild-first-batch` 起。上游是 [near-term-plan.md](near-term-plan.md) 的 M4 一节 —— **那一节只给了目标，这一份给做法**。体例同 [batch-m2.md](batch-m2.md) / [batch-m3.md](batch-m3.md)。

> **语言：简体中文，无英文孪生。**

## 这一批是什么

**这是第一批要花钱的。**M1–M3 全程离线；M4 的验收里有一次**真跑 5 题**，走 agent 路径、调真模型、写 Langfuse。委托人已确认预算。

| 项 | 一句话 | 估工 | 花钱？ |
|---|---|---|---|
| **N12a** | `RunContext` + `tracing_config` + `configure_logging`（代码） | 2 人日 | 否 |
| **N11** | 实时可观测：时间戳、每题进度与 ETA、巨型 JSON 出终端 | 1.5 人日 | 否 |
| **N13** | 可追溯：manifest 记 `dirty` / `diff_sha256` / 分支名 / main hash | 1.5 人日 | 否 |
| **N14** | 两条 serve 真缺陷（**实际是三条**，见下） | 1.5 人日 | 否 |
| **N12b** | **一次 5 题真跑**，一并验收上面四项 + M1 遗留 | 0.5 人日 | **是** |

**顺序改了，理由在下面「一次跑，验四件事」。**near-term-plan 把 N11–N14 平列，实际有依赖。

PR 规约沿用 [near-term-plan.md 的「交付规约」](near-term-plan.md)。基线：**1684 passed / 10 skipped / 1 xfailed**（M3 之后）。

---

## 模型：`gpt-5.6-luna`，已经是了 —— 但要在花钱之前确认

委托人要求这一批用 **`gpt-5.6-luna`**。现状我核过，**不用改配置**：

- `governed_bi.toml:46` —— `llm_model = "gpt-5.6-luna"`
- `governed_bi.local.toml` —— `[models]` **刻意不存在**（`:33` 注释写明「base governed_bi.toml defaults apply」），所以 pooled 配置不会覆盖它

**但这里有一个真实的坑，而且这一批没有它的机器守卫。**`--model` 参数是 checklist 2.3，**不在这一批**（rebuild-checklist 2.3 第 3 点：两个 driver 都没有 `--model`，模型只能从 TOML 读）。所以切模型靠改 TOML，**忘了改回来不会报错，只会在事后的 manifest 里留下一个你没注意的字段**。

**N12b 花钱之前必须做的一步（写进 PR）**：

```bash
uv run python -c "from governed_bi.config import Settings, Environment; print(Settings.for_env(Environment.dev).models.llm_model)"
```

输出必须是 `gpt-5.6-luna`。跑完之后再从 `manifest.json` 的 `model` 字段复核一次 —— **两头都对上才算数**，因为 `manifest_model()` 在无 fair 臂时会把它写成 `None`（M3 N10 之后是 `--oracle-only` 的推论）。

---

## 开工前：上游 spec 的七处更正

near-term-plan 的 M4 一节摘自 rebuild-checklist 3.1 / 5.2 / 5.4 / 6.1，摘时没有逐行核代码，而 M1–M3 又移动了不少行号。**2026-07-31 逐处核过：**

| # | 原文说 | 实际 |
|---|---|---|
| 1 | tracing 有「八个调用点」，含 `eval/refuse_gate.py:71`、`api/graph_app.py:174`、`scripts/live_smoke.py` | **六个**，而且那三个**根本不调** `tracing_callbacks`。真正的六处：`analyst/agent.py:1493`、`curator/pipeline.py:769`、`curator/sme.py:472`、`eval/arms.py:473`、`eval/oracle.py:467`、**`llm/langchain_client.py:129`** —— 最后这个原文没列，而它是**通用客户端**，覆盖面最大 |
| 2 | 「30 个 `logger.` 调用全是死的」「105 个 `print()`」 | **24** 个 `logger.*`、**95** 个 `print(`。结论不变，数要改 |
| 3 | narrator token 互抢在 `agent.py:1396-1407` | **`agent.py:1412-1422`**（M1/M3 之后行号漂了） |
| 4 | N14 是「两条」serve 缺陷 | **三条。**`agent.py:700` 有**同一个缺陷的第二个实例**（`router_chat.last_usage_metadata`），原文完全没提，见 N14 |
| 5 | `on_result` 钩子在 `run_datalake.py:3966-3974` | **`:3954`**（`on_result=_persist`）。钩子本体在 `eval/parallel.py:181-182` |
| 6 | N13「只新增两个字段：分支名、对应的 main hash」当作要写新代码 | **两个都近乎免费**：`provenance.py:180` 已经在解析 `ref: refs/heads/<branch>` 然后把分支名**丢掉**。main hash 用同一个 reader 读 `.git/refs/heads/main`（含 packed-refs 回退，`:186` 起已经实现） |
| 7 | 「`src/` 里没有任何 `logging.basicConfig`」 | **这句是对的。**grep 会命中两处，但那两处是**注释在陈述这件事**（`analyst/agent.py:627`、`analyst/run_log.py:498`），而且它们各自记录了代价 —— 一次是 assemble 的异常只能靠 `print`，一次是 run-log 写失败被默默丢掉（AUDIT R4）。**这是 N12 最好的证据，不是反例** |

---

## N12a · `RunContext` + `tracing_config` + `configure_logging`

### 目标

`run_id` / `turn_id` / `corpus_pin` 三个字段**已经存在**，但**没有一个进入 Langfuse 或 LangSmith 的 trace**。服务器上跑完之后，trace 和 `stage_events.jsonl` 拼不回去。

### 做三件

**一 · `RunContext` 记录**，承载 `run_id` / `turn_id` / `corpus_pin` / `arm` / `schema` / `prompt_set_hash` / `identity`。

**二 · `tracing_config(ctx)`**，产出同时喂两个 tracer 的 metadata：LangSmith 读 `metadata` 与 `tags`，Langfuse 读 `langfuse_session_id` / `langfuse_user_id` / `langfuse_tags`。

现在的接缝是 `obs.py:145` 的 `tracing_callbacks(*, with_usage=False)`，六个调用点都走它。**扩这个函数，不要新起一条并行通道** —— 建议 `tracing_callbacks(*, with_usage=False, ctx: RunContext | None = None)`，`ctx=None` 时行为逐字节不变，这样六处可以分批接。

> **注意 `obs.py:81` 的 `_trace_mask`。**Langfuse v4 的 legacy `mask` 与 `mask_otel_spans` 是两回事，而这份 mask **不覆盖 callback data**（`framework-and-logging-audit.md` 的首条发现）。往 metadata 里塞 `identity` 之前先确认它会不会绕过 mask。**宁可先不塞 `identity`**，其余六个字段都不敏感。

**三 · `configure_logging()`**。`src/` 里没有 `logging.basicConfig`，入口也没有，所以 24 个 `logger.*` 调用全是死的，而 95 个必须被看见的诊断都写成了 `print()`。加一个 **ContextVar filter** 把 `run_id` / `turn_id` 注入每条记录 —— **不许改任何函数签名**。

`analyst/agent.py:627` 和 `analyst/run_log.py:498` 两处注释在这一项落地后**要跟着改**：它们现在说「因为没有 basicConfig 所以只能 print」，那个前提没了。**但不要在这一项里把 95 个 `print` 改成 `logger`** —— 那是 N11 的事，而且不该一次全改。

### 验收

- 单测：`tracing_config(ctx)` 的产物同时含 LangSmith 的 `metadata`/`tags` 与 Langfuse 的三个 `langfuse_*` 键。
- 单测：`configure_logging()` 之后一条 `logger.info` 能被 caplog 捕获，且记录上带 `run_id`。
- **端到端那条留给 N12b。**

### 禁止

- 不许新起第二条 tracing 通道，绕过 `obs.tracing_callbacks`。
- 不许在这一项里批量替换 `print`。
- 不许把 `identity` 塞进 trace metadata，除非先确认 mask 覆盖它。

---

## N11 · 实时可观测

### 现状（核过）

服务器上跑一个多小时看不见任何东西：

- serve 阶段**每题零输出**，每个臂静默 16–27 分钟，连续四次。`eval/parallel.py:181-182` 有 `on_result` 钩子，driver 只用它写盘（`run_datalake.py:3954` 的 `on_result=_persist`），**一个字不打**。
- stdout 上**没有任何一行带时间戳**。
- 构建阶段 20 个线程的日志交错、无 db 标签、会串行断行。
- 结束时终端一次性吐约 5 万行 JSON —— `run_datalake.py:5350` 的 `print(json.dumps(result["arms"], indent=2, ...))`，绝大部分是 `question_ids` 数组（`question_ids` 在这个文件里出现 8 次）。`:5389` 还有第二处。
- `run.console.log` 不是代码写的，靠操作员记得重定向。

### 做什么

排在 N12a 之后，**因为时间戳和 `run_id` 前缀由 `configure_logging()` 免费提供** —— 先做 N11 就要自己拼一遍前缀，然后 N12a 再拆掉。

1. 每行加时间戳（走 N12a 的 logging 配置）。
2. serve 每 N 题打一行进度和 ETA。**钩子已经在** —— `on_result` 就是那个位置，它已经在计算 per-row 完成，只是没打印。
3. 构建日志加 db 前缀。
4. 结构化日志自己写文件，不靠操作员重定向。
5. **巨型 JSON 从终端挪进文件。**`:5350` / `:5389` 两处改成写盘 + 打印一行摘要与路径。

### 验收

**跑一次 5 题的小跑（离线亦可，用 `--oracle-only`），全程 stdout 不超过 50 行，且每一行都能看出「现在在干什么、到哪了」。**

这条**可以在不花钱的前提下先验一遍**：M3 N10 给了 `--oracle-only`，它零模型调用。真花钱那次在 N12b 复验。

### 禁止

- 不许把进度打印塞进 `eval/parallel.py`。那是通用工具，`on_result` 存在的意义就是让调用方决定打什么。
- 不许删 `:5350` 的 JSON —— 是挪到文件，不是丢掉。

---

## N13 · 可追溯

### 先读这段，原计划错过一次

上次跑不可复现：manifest 记的 `git_sha 3f599b6` 在本地 `git cat-file -t` fatal。**原因不是缺字段，是分支没推加工作树脏。**

**已经存在、不要重复加**：`created_at_utc`、`completed_at_utc`（相减正是 1h45m32s）、`git_sha`（`provenance.py:170` 的 `corpus_release_hash()` 读 `.git/HEAD`，**它已经是服务器分支的 HEAD**）。

### 三件，难度差很多

**1 · 分支名 + 对应的 main hash —— 近乎免费，先做这个。**

`provenance.py:180` 已经在做 `if head.startswith("ref:")` 然后取 `ref[len("ref:"):]`，**分支名就在手里，被丢掉了**。main hash 用同一个 reader 读 `.git/refs/heads/main`，packed-refs 回退在 `:186` 起也已经写好。**两个字段，零新机制，不破 no-subprocess 约束。**

服务器上 internal proxy 代码在另一个分支，HEAD 永远不等于 main —— 两个都记是零成本的痕迹。

**2 · `dirty: bool` 与 `diff_sha256` —— 这是真活，别按小改动定价。**

`provenance.py:173-175` 的 docstring 明写「Reads `.git/HEAD` (and a loose/packed ref) under `repo_root` **without `subprocess`**」。加 `dirty` 要么**破这条约束**，要么**自己重实现 index 比对**。

**两个选项，PR 里写清楚选了哪个：**

- **A · 破约束，但只在新函数里破。**`corpus_release_hash` 保持 no-subprocess（它在 serve 热路径上被调用）；新开一个 `working_tree_state()` 用 `subprocess` 跑 `git status --porcelain` + `git diff`，**只在 eval driver 启动时调一次**。改 docstring 说明约束的边界在哪。
- **B · 自己比对 index。**不引 subprocess，但要解析 `.git/index` 二进制格式。**不推荐** —— 为了一个约束重写 git 的一部分。

**推荐 A**，并把「为什么 `corpus_release_hash` 仍然 no-subprocess」写进注释，否则下一个人会顺手把两者统一。

**3 · 零代码的那件：跑之前 `git push` 服务器分支。**落点是 runbook，不是代码。接受推论：**不推分支的那次跑不进 quotable 台账。**

### 验收

- `dirty=false` 的那次跑，拿**分支名 + hash** 能 checkout 出可运行的代码。
- `dirty=true` 的那次，`diff_sha256` 能对上落盘的 diff。
- 单测：在一个临时 git 仓库里造干净树与脏树，两种状态都断言。
- **manifest 多了四个字段 → `MANIFEST_SCHEMA_VERSION` 要不要 bump？**M3 N10 刚把它推到 2。这四个是 `MANIFEST_OPERATIONAL` 还是 `MANIFEST_KNOBS`？**如果进 `MANIFEST_KNOBS` 就必须 bump 到 3**，而且 M3 那个 bump 守卫（返工后的版本）会当场告诉你。**建议进 operational** —— 它们描述「这次跑是怎么产生的」，不是「测的是什么」，不该让两次跑因为分支名不同而不可比。

### 禁止

- 不许让 `corpus_release_hash` 变成 subprocess 调用。它在 serve 路径上。
- 不许把新字段塞进 `MANIFEST_KNOBS` 而不 bump 版本。

---

## N14 · serve 真缺陷 —— **三条，不是两条**

### 1 · 一个进程两份 `ServeStack`

`api/routes.py:28` 在 **import 时**调 `build_stack()`（`app = create_app(dataclasses.replace(build_stack(), can_stream=True))`），`api/graph_app.py:233` 又独立调一次，而 `api/stack.py:173` 的 `build_stack` **没有 `lru_cache`**（核过，那个文件里零命中）。

一个进程两份 corpus、两份 `index_cache`、两套 clarify checkpointer。连带后果：`POST /corpus/edit` 写盘成功后本进程读不到（`api/app.py` 写完直接返回，从不刷新 corpus），策展客户端看到「200 写成功 → 列表还是旧的 → 答案还是旧的」。

**注意 `build_stack(settings=None)` 有参数**，所以不能无脑 `@lru_cache` —— `Settings` 未必可哈希。做法是缓存**默认参数**那一条路径，或显式一个 `get_default_stack()`。

### 2 · narrator 的 token 归属互抢

`analyst/agent.py:1412-1422`：从 stack 级**共享**的 `narrator._chat.last_usage_metadata` 读，**然后置 `None`**。narrator 由 `build_stack()` 建一次。LangGraph Server 默认并发跑 run，**不需要开多标签页就能触发**：A 读到 B 的 usage，或 A 清空了 B 还没读的。

### 3 · **同一个缺陷的第二个实例，原计划没提**

`analyst/agent.py:700`：

```python
usage = getattr(router_chat, "last_usage_metadata", None)
```

`router_chat` 是 `build_serve_rails` 里的闭包（`agent.py` 约 :465 构造），**一张图服务并发 run 时同样共享**。而且它**读完不置 `None`** —— 所以它的失效方式和第 2 条相反：不是互相偷，是**重复计入**，同一份 usage 会被后续每个 run 再数一遍，直到下次调用覆盖它。

**两条一起改成从调用返回值取 usage**，不要只修 narrator 那一条 —— 修一半会让人以为这类问题解决了。

**改完顺手搜一遍还有没有第三个实例**：`grep -rn "last_usage_metadata" src/`。

### 验收

- `build_stack()` 两次调用返回同一对象。
- `POST /corpus/edit` 之后 `GET /schema` 立刻可见。
- **并发两个 run，两边的 `token_usage` 之和等于实际消耗** —— 第 2 条测「不偷」，第 3 条测「不重复」，两个方向都要断言。
- `grep -rn "last_usage_metadata" src/` 的每一处都从返回值取，或有注释说明为什么不用。

---

## 一次跑，验四件事（N12b）

**顺序**：`N12a → N11 → N13 → N14 → N12b`

N12b 是**唯一花钱的一步**，一次 5 题真跑同时结清四条验收：

| 验的是谁 | 判据 |
|---|---|
| **N11** | 全程 stdout ≤ 50 行，每行带时间戳，能看出进度与 ETA |
| **N12a** | **同一个 `run_id` 能在 Langfuse trace、`stage_events.jsonl`、日志文件里查到同一批记录** —— 三个 sink 的联合验收，缺一个不算 |
| **N13** | manifest 里 `dirty` / `diff_sha256` / 分支名 / main hash 四个字段都在，且和实际工作树状态一致 |
| **M1 遗留** | `generations.*.jsonl` 里读得到**投影后的逐层判决列表**（`action` / `verdict` / `layer` / `sql` / `allowed` / `row_count`，无 `result`），且整行 `json.dumps` 不抛 —— 见 [near-term-plan.md](near-term-plan.md) N3 那条处置 |

**花钱之前的三步检查清单（写进 PR）：**

1. 模型确认 —— 上面那条命令输出 `gpt-5.6-luna`。
2. `git push` 当前分支（N13 第 3 件）—— 否则这次跑的 hash checkout 不出来。
3. 工作树干净，或明确接受 `dirty=true` 并留下 diff。

跑完从 `manifest.json` 复核 `model`、`git_sha`、`dirty`、分支名四个字段。

**只截一张 Langfuse 截图不算通过 N12a。**要的是同一个 `run_id` 在三处都查得到 —— 这一条我会具体问。

---

## review 会挂在哪里

按会退回的概率排：

1. **N12a 新起了第二条 tracing 通道**，绕过 `obs.tracing_callbacks`。六个调用点会分裂成两套。
2. **N14 只修了 narrator，没修 `agent.py:700` 的 router。**修一半比不修更糟 —— 它会让人以为这类问题清干净了。
3. **N12b 只交了一张 Langfuse 截图。**三个 sink 的联合查询才是判据。
4. **N13 把 `corpus_release_hash` 改成 subprocess。**它在 serve 热路径上。
5. **N13 的四个新字段进了 `MANIFEST_KNOBS` 而没 bump 到 3。**M3 那个守卫（返工后的版本）应该当场红 —— 如果它没红，说明守卫还没修好。
6. **N11 在 `eval/parallel.py` 里打印。**那是通用工具。
7. **N12a 顺手把 95 个 `print` 全换成 `logger`。**不在这一项，而且一次全换没人能 review。
8. **花钱之前没做那三步检查。**模型跑错了要重花一次。

## 这一批做完之后

M4 出口判据（[near-term-plan.md](near-term-plan.md) 那张表）：5 题小跑 stdout ≤ 50 行且每行带时间戳；同一个 `run_id` 在三个 sink 里都查得到；`build_stack()` 两次调用返回同一对象。**外加本文档新增的两条**：N13 四字段一致、M1 逐层判决端到端。

下一批是 **M5（N15–N17）**，其中：

- **N15（分析工具 CLI 化）对手上那份 1351×4 数据立刻生效，不用再跑一次** —— 但 **`runs/` 在 N15 做完之前不许删**（服务器有备份，本地删了就得拉回来）。
- **N17 的跑不外派** —— 交付命令、零题守卫、MDE 预登记三件，不交付「跑完了」。
- N17 那句必须进 runbook 的话：**在 9.03% 的噪声底线下，没有任何负担得起的 N 能分辨 0.2pp 的 SME 步长。**`--replicate` 只让你有资格说「未检出」。
