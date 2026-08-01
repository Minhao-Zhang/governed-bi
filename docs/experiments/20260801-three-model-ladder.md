# 20260801 · 三模型梯子(luna / DeepSeek / Opus 参照)

一天之内跑的三条梯子,**共用同一份逐字节相同的 Opus 策展语料**,唯一变量是生成模型。
`shortlist_recall` 三家分毫不差(95.7% 于同一批题上),这是设计干净的证明,不是巧合。

## 配置

| | luna | DeepSeek | Opus(参照,20260731) |
|---|---|---|---|
| 生成 | `gpt-5.6-luna` / effort=max | `deepseek-v4-flash` / effort=max | `Claude-Opus-4.8` / high |
| 端点 | api.openai.com | api.deepseek.com | 服务器侧代理 |
| embedding | `text-embedding-3-large` @ OpenAI | **同左**(刻意) | `text-embedding-3-large`(未记录) |
| 语料 | Opus 建的三臂,逐字节复制 | 同左 | 自建 |
| 并发 | 6 | 6 | 20 |

`route_top_k=10`,`llm_pick=true`,`pick_max_columns=12`,`grade_semantic_failures=true`。

## 结果

### luna(完成)

```
arm         n      crash      EX     ex_no_twin   shortlist    pick
baseline  1351   0.0000    30.57%     30.88%       94.15%    83.42%
seeded    1351   0.0000    39.38%     40.55%       93.19%    82.38%
curated   1351   0.0015    50.63%     51.61%       95.18%    86.14%
```

花费 **$32.63**(缓存命中 55.4%)。

### 三方对照(curated 臂,同一批 234 题,DeepSeek 跑到该处时的快照)

```
model                        EX   shortlist    pick   EX|pick  capped   lat
deepseek-v4-flash/max     49.1%     95.7%     91.0%    54.0%    3.8%    16s
gpt-5.6-luna/max          51.7%     95.7%     88.0%    58.7%    3.8%    28s
Claude-Opus-4.8/high      60.3%     95.7%     91.0%    66.2%    0.4%    12s
```

## 五条结论

### 1 · 梯子形状跨模型复现

```
luna   30.6 → 39.4 → 50.6   (+8.8 / +11.3pp)
Opus   41.7 → 48.0 → 56.3   (+6.3 /  +8.3pp)
```

「seeded 有用、curated 更有用」在第三个模型上成立,**而且弱模型受益更大** —— 与此前 Sonnet-5 的观察同向。这是本次最结实的一条。

### 2 · 治理把模型间差距压小了

DeepSeek flash 在 curated 上只落后 luna **2.6pp**,而价格是它的**八分之一**(curated 整臂 $3.58 vs 约 $14)。一个 flash 档模型打到 49.1%。

### 3 · 策展顺带治好了「乱转」—— 这是个此前没测过的收益

```
capped 率(luna):  baseline 23.5%  →  seeded 12.4%  →  curated 2.6%
```

不是 EX 指标,是**行为**指标。baseline 上模型因为没有上下文只能反复摸索,把 40 步预算烧光:

```
每题工具调用    capped(n=317)   answered(n=1027)
search_corpus        4.2             1.2
sample_rows          4.0             1.1
run_query            2.9             1.8
中位总调用数          13               3
```

耗尽的**不是查询次数**(`RUN_QUERY_CAP=3` 把 `run_query` 卡在 2.9),而是**步数** —— `search_corpus` / `sample_rows` 完全没有上限,吃光了 `AGENT_RECURSION_LIMIT=40`。

**推论:提高步数上限这个干预的价值被高估了。**它要修的 23.5% 只出现在 baseline,而 curated 上只剩 2.6%。它应该被当作机制验证,不是提升手段。

### 4 · 瓶颈在 pick,而且「想更久」在这一步是负收益

三家 `shortlist` 都是 95.7%,`pick` 却分别是 91.0 / 88.0 / 91.0。**luna 在 `schema_pick` 上花了 17 秒中位耗时(Opus 是 2.3 秒),买到的是三家里最差的准确率。**

机制在 `schema_router.py:44`:`SCHEMA_PICK_MAX_TABLES = 15`,**按字母序**截断。`works_cycles` 73 张表,picker 看到 `Address…Department`,`SalesOrderHeader` / `Product` / `Employee` 全在一行 `… (58 more tables)` 里。

### 5 · 差距在生成,不在路由

DeepSeek 的 pick 和 Opus 一样好(91.0%),但 `EX|pick` 是 54.0% vs 66.2%。**选对 schema 之后写不对 SQL** —— 那 12pp 是纯生成能力。

## 事故与更正(必须读)

### 我把 OpenAI 的 embedding 配额打爆了,污染了两条跑

DeepSeek 那条第一次跑时开了 24 并发。**它的 embedding 刻意留在 OpenAI**(为了让检索层可比),而每个 worker 都要给路由语料建索引:

```
openai.RateLimitError: Rate limit reached for text-embedding-3-large
  on tokens per min (TPM): Limit 1000000, Used 1000000
```

后果:DeepSeek baseline 655 崩 / seeded 1351 全崩(**该目录整体作废**,保留在 `20260801T-ladder` 作证据);**luna 的 seeded 和 curated 也被带崩 39 行**,后经低并发 resume 重服干净(最终 crash 0.15%)。

**教训:两条跑的 embedding 都走 OpenAI,配额是共享的。它们必须串行。**

### 一条被撤回的数字

事故期间测到「DeepSeek pick = 69.9%」。**那是假的** —— 当时 shortlist 本身因配额耗尽而残缺。独占配额后重测是 **91.0%**。

### 价格表错了一个数量级

`_PRICE_PER_1M` 里 `gpt-5.6-luna: (2.0, 8.0)`,既不是现价($0.20/$1.20,2026-07-30 降 80%)也不是降价前的 $1/$6。叠加「不认缓存」,把一个 **$32.63** 的跑报成了「已花 $129 / 预计 $302」。已修:价格表改为三元组带缓存档,并补齐 DeepSeek / Claude 条目。

## 顺带修掉的两个真 bug

**孤儿 `function_call`。** G1 只执行第一个工具调用,`_coerce_single_tool_call` 截断了 `tool_calls[:1]` 但保留了 `content` —— 而 Responses API 下 `content` 的块列表里就含着两个 `function_call`。发出去的对话自相矛盾:两个调用、一个输出。OpenAI 容忍,DeepSeek 直接 400(`No tool output found for tool call ...`),4/6 题崩溃。**不是 DeepSeek 的问题,是我们一直在发不自洽的对话。**

**`ModelConfig` 没有 endpoint 字段。** 唯一到达 OpenAI 兼容厂商的方法是环境变量 `OPENAI_BASE_URL`,而 chat 客户端和 embedder **都读它** —— 指向 DeepSeek 会把 embedding 也送过去。加了 `base_url` / `embedding_base_url` / `embedding_api_key_env`,这才让「只换生成模型、检索层原封不动」成为可能。

## 待办

- DeepSeek seeded / baseline(curated 之后,顺序刻意为 curated → seeded → baseline)
- `recursion80` 干预(已建 worktree,与 luna 代码只差一行),**价值已按第 3 条重估**
- DeepSeek 的 `cost_est_usd` 落盘时是旧口径(表里没有条目 → null),需从 `token_usage` 重算 —— 原始 token 与 `cache_read` 都在行里,不必重跑
- **噪声底仍未测**。三条梯子都没跑 `--replicate`,所有显著性都带着 `no noise floor measured for this run`
