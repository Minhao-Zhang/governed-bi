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

### DeepSeek(curated 完成;seeded / baseline 作废)

```
arm         n      crash          EX     capped
curated   1351         0       48.70%      4.9%     <- 完整、干净
seeded    1351       637       16.02%      7.0%     <- 402 Insufficient Balance
baseline  1351      1351        0.00%      0.0%     <- 同上,全部崩溃
```

**账户余额在 seeded 中途耗尽**(`402 Insufficient Balance`),不是限流、不是并发、不是代码。

curated **先跑**是刻意的:arm 顺序按 CLI 给定(`serve_order = list(arms)`,无排序),这次指定的是 `curated,seeded,baseline`。按默认顺序,耗尽时拿到的会是 baseline —— 唯一有部署含义的那条臂反而拿不到。`run_datalake` 里 oracle rungs 排最后的注释讲的就是这个道理(「跑到一半死掉时,至少该把算结果的臂跑完」),这次它在一个没预料到的失败模式上生效了。

### 三方对照 · curated 臂 · 完整配对 1351 题

```
model                      EX    shortlist   pick   EX|pick  capped   lat      成本
deepseek-v4-flash/max    48.70%    95.3%    86.8%    55.8%    4.9%    24s     $3.80
gpt-5.6-luna/max         50.63%    95.2%    86.2%    58.4%    4.1%    38s    $12.17
Claude-Opus-4.8/high     56.25%    95.2%    87.3%    64.1%    0.6%    13s   $806.31
```

配对 McNemar(精确检验,双边):

```
比较                     delta      不一致对        p        判定
Opus  vs deepseek      +7.55pp     180 / 78     0.0000    检出
Opus  vs luna          +5.63pp     167 / 91     0.0000    检出
luna  vs deepseek      +1.92pp     127 / 101    0.0976    未检出
```

**luna 与 DeepSeek flash 之间测不出差别。**三条理由任一足够:p=0.098 不显著;1.92pp 低于在用的 2.5pp 噪声底;而那个噪声底本身还没被本仓库测过。

有信息量的是那 228 个不一致对(16.9%):127 对 101,几乎对消。**不是「luna 略强」,是两个模型在不同的题上各有胜负。**

这是**未检出**,不是「证明等效」。要说等效需要预注册一个等效界(如 ±2.5pp)并跑够样本。

成本上,Opus 那条**缓存命中 0%** —— 服务器侧代理没上报 `cache_read`,$806 是上界。DeepSeek 命中率最高(67.2%)。

⚠️ luna 行内落盘的 `cost_est_usd` 合计 **$133.59 是错的**(旧价格表:高 10 倍且不认缓存)。真值 $12.17,须从 `token_usage` 重算。DeepSeek 那条落盘即正确。

## 结论(第 4、5 条是对本文早先版本的更正)

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

### 4 · 瓶颈不是 pick 本身 —— 这一条我写反过,更正在此

三家 `shortlist` 都是 95.2-95.3%,`pick` 是 86.8 / 86.2 / 87.3% —— **相差 1.1pp,pick 对模型能力基本不敏感**。差距全在 `EX|pick`(55.8 / 58.4 / 64.1),即**选对 schema 之后的纯生成能力**。

本文档早先的版本引用过「DeepSeek pick 91.0%,高过 luna」—— 那是 n=234 的快照,受 schema 推进顺序影响。**满 1351 题之后三家几乎一样。**

更重要的更正:我曾多次把 pick 说成「把检索已经找到的答案扔掉」。算术上成立(106 题进了 shortlist 却被选错,完美 pick 值 +4.7pp),但漏了另一半:

```
纯取 shortlist rank-1 : 0.6921
实际 LLM pick         : 0.8734      净 +18.1pp
救回 266 / 弄丢 106  = 2.5 : 1
```

**没有 picker,系统会差 18 个点。**「给 pick 加置信度门」这个想法已被离线证伪(所有阈值净收益 ≤ 0;退化到 top-2/top-3 命中 0.790/0.850,都低于 picker 的 0.873,且破坏 D15 单 schema 不变量)。详见 `docs/plans/routing-redesign.md`。

### 5 · 检索也没有饱和 —— 只是当前这个通道到顶了

我说过「@10 已 0.953,检索饱和」。**`tblmax`(每张表单独 embed,schema 取其最佳表)在每一个 k 上都赢**,而且少 26% token:

```
channel          @1      @3      @5     @10     @20
emb_large     0.694   0.850   0.906   0.952   0.979   <- 现在用的
tblmax_large  0.730   0.893   0.939   0.973   0.991
```

成因:把 73 张表揉成一个平均向量,`works_cycles` 被稀释;按表 embed 没有这个问题。这是目前证据最硬的可上线项 —— 目标指标就是召回,而召回被直接测量。

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

## 当天新增的两件事故与更正

### DeepSeek 第二次中断:402,不是我的错这次

第一次是我把 OpenAI 的 embedding 配额打爆(24 并发 × 每 worker 重复 embed 57 个 schema 文档)。**第二次是 DeepSeek 账户余额耗尽**,与并发和代码无关。两次的现象都是「大批 crash」,但原因完全不同 —— `error_type` 分别是 `RateLimitError` 和 `APIStatusError`,而 402 的正文只在 `run.log` 里,行内不存。

### 限流比我说的严重十倍,而我的 grep 骗了我

```
我用的固定串 grep "Retrying request to /responses in 6.6 seconds"  ->  0 命中
正则 "in [0-9.]+ seconds"                                          ->  7,025
429 响应 / 总请求                                                  ->  6,639 / 18,422 = 36%
```

**退避延迟是每次不同的浮点数**,固定串一个都匹配不上。我据此说过「6 并发只有 6 次重试」。真实是:**luna 那条跑全程三分之一以上的请求被 429,累计 12,930 秒在退避里**,而那只是 6 并发。

后果:我把 DeepSeek 从 6 提到 16 导致崩溃,当时归因为「16 太多」—— 实际是**账号在 6 就已经饱和**。

## 已修(全部经证伪:故意改坏、看测试变红)

| | |
|---|---|
| 资产 embedding 逐 worker 重复 | 一条梯子 994 次构建 / 171 个不同语料,发 1.21M token、真需 212k;**纯重复 1.0M token、823 次调用、约 1.7GB 内存** |
| 每轮把问题 embed 两次 | 占全部 embedding 流量的一半 |
| 中止根本不中止 | `pool.map` 全量提交 + `shutdown(wait=True)`;第 5 题中止,400 题照跑完 |
| serve 阶段无熔断 | Page's CUSUM 变点检测(今天的故障是「600 行干净后突然 48%」,第 610 行触发) |
| 降级不挡 quotable | 阈值 2% 由 e1 实测推导;**它拒绝的第一个对象就是 20260731 那条参照跑**(四臂各路由 1351 题、零 channel 记录) |
| MDE 在错误的总体上求值 | 全量复制臂时重合、省钱时发散**且朝危险方向**:300 题复制臂给出 15.3 题阈值,诚实值 32.6 |
| `LOW_CONFIDENCE_JOIN` 两处定义、运算符不同 | curator 默认置信度恰是 0.7,**每条默认 join 在产物里「正常」、在 UI 里「低置信」** |
| 四个被当作测量结果引用的已证伪数字 | 含我当天早上亲手写进 `governed_bi.toml` 的那个 |

## 待办

- **DeepSeek seeded / baseline 需要重跑**(充值后)。curated 完整可用,不受影响
- `recursion80` 干预仍暂停。价值已按第 3 条重估:capped 沿梯子是 23.5% → 12.4% → 2.6%,**策展本身就把「乱转」治好了**,提高步数上限在 curated 上最多值零点几个点
- **噪声底仍未测。** MDE bug 修掉之后,300 题的复制臂是安全的买点(约一条梯子的 5.5%),但 `run_datalake` 缺一个 `--replicate-limit`
- `tblmax`:证据最硬的可上线项
- luna 行内 `cost_est_usd` 需从 `token_usage` 重算(旧价格表)
- **自动定并发做不到**:OpenAI 每个响应都返回 `x-ratelimit-remaining-tokens`,仓库一行没读;而且额度是组织级共享的,任何静态并发数都会错
