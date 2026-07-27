# 服务期澄清（HITL）—— 服务端 ↔ 前端 API 契约

_[English](hitl-clarification-contract.md) · [简体中文](hitl-clarification-contract.zh.md)_

_状态：**已达成一致；服务端已实现**（2026-07-14）。§11 的六项决策均已接受；引擎遵守本契约（见下面「服务端实现状态」）。本文是**前端契约**的权威来源；前端自身的构建状态见 [`governed-bi-ui`](https://github.com/Minhao-Zhang/governed-bi-ui)——不在这里跟踪。配套文档：[agent-step-visualization.md](agent-step-visualization.md)（本文所扩展的那条实时治理事件流，英文）与 [ADR 0002](../adr/0002-governed-agentic-serve-runtime.zh.md)（agentic 服务内核；`ask_user`→`interrupt` 是它 Phase 3 的 HITL 分支）。_

## 1. 范围与原则

- **是什么：** 当受治理的 agent 在一轮对话中途遇到真正的歧义时，它会**向用户问一个问题并等待**，而不是猜或者拒答。拿到回答后，它在同一轮里继续。
- **只在服务端。** HITL **只存在于部署出去的服务路径**（LangGraph Server 聊天图 → 内层 agent）。评测 / 离线 / 编程调用的 harness **从不中断**——那里没有人；它的行为与今天完全一致（要么继续，要么失败即拒）。`ask_user` 工具只注册在服务端的服务路径上。
- **扩展既有传输，不替换它。** 澄清与答案、以及修正 2 的治理事件流走同一条 `useStream` 连接。没有新端点，没有新 socket。
- **贴合主题：** 问出的问题和给出的回答都会成为 **ledger 记录**（不变量 #10）——澄清是一个受治理、被审计的动作，不是旁路通道。

## 2. 机制（钉在已发布的技术栈上）

已对着 `langgraph>=1.0`（服务端）与 `@langchain/langgraph-sdk@^1.9.25`（`useStream`）验证：

- **服务端抛出：** 在 `ask_user` 工具内部调用 `interrupt(request)`，其中 `request` 就是 `ClarificationRequest`（§3）。这会让图在服务端注入的 checkpointer 处暂停，并把这次中断流给客户端。
- **客户端读取：** `stream.interrupt` 变为非 null，它的 `.value` **就是** `ClarificationRequest`。这个 hook 的类型是 `useStream<ChatState, ClarificationRequest>`。
- **客户端回答：** 调用 `stream.respond(response)`，其中 `response` 是 `ClarificationResponse`（§4）。这会恢复这次运行（底层是 `Command({ resume: response })`）；服务端那次 `interrupt(...)` 调用返回 `response`，agent 继续往下走。
- **同一时刻只有一个待处理中断**（§6），所以客户端用不带目标的 `stream.respond(value)`（对准最新的那个中断）。v1 不做 `interruptId` / `namespace` 定向。

## 3. 服务端 → 客户端：`ClarificationRequest`（`interrupt()` 的值）

```jsonc
{
  "kind": "clarification",          // 判别字段；为未来的其他中断类型预留
  "clarification_id": "clar_ab12",  // 这个问题的稳定 id（ledger + provenance 的关联键）
  "question": "Which 'active' did you mean — logged in last 30 days, or account status = 'active'?",
  "why": "The corpus has two competing definitions of \"active\" and the question is ambiguous between them.",
  "choices": [                      // 可选。有 => 受限选择；没有 => 自由文本。
    { "id": "opt_login30", "label": "Logged in within 30 days" },
    { "id": "opt_status",  "label": "Account status = 'active'" }
  ],
  "allow_freeform": true,           // 当有 choices 时：是否也允许用户自己输入？
  "tier": "audit"                   // 这个问题的 provenance 层级（D12 澄清协议）
}
```

- `question` 与 `why` 始终存在（治理透明度：用户能看到自己*为什么*被问）。
- 没有 `choices` ⇒ 只能自由输入。有 `choices` ⇒ 渲染这些选项；`allow_freeform` 决定是否额外给一个文本框。
- `clarification_id` 是贯穿中断、恢复、时间线事件（§5）与最终 provenance（§7）的关联键。

## 4. 客户端 → 服务端：`ClarificationResponse`（`respond()` 的值）

```jsonc
// 已回答（自由文本）：
{ "clarification_id": "clar_ab12", "answer": "logged in last 30 days" }

// 已回答（选了一个选项）：
{ "clarification_id": "clar_ab12", "choice_id": "opt_login30" }

// 拒绝回答 / 取消：
{ "clarification_id": "clar_ab12", "declined": true }
```

- `answer` / `choice_id` / `declined:true` 三者恰好设置一个。
- **拒答语义（D3）：** agent **不猜**。它失败即拒——返回一次拒答（或者按 §6 的分级策略，返回一个 `semantic_assurance` 被下调后盖章的尽力而为答案）。v1 的安全默认是**拒答**，原因写 `clarification_declined`。
- 服务端会校验 `clarification_id` 与待处理的那个问题一致；不一致 ⇒ 忽略并重新发出中断（防御性措施；在只有一个待处理中断的前提下不应该发生）。

## 5. 与治理事件流的整合（修正 2）

澄清是一次**受治理的工具调用**，所以它会作为一个 `tool` 事件出现在时间线上（[agent-step-visualization.md](agent-step-visualization.md)），从而守住「实时步骤视图*就是* ledger」这条不变量：

| kind | step | status | detail |
|---|---|---|---|
| `tool` | `ask_user` | `start` | `{ clarification_id, question }`——时间线显示「正在提问…」 |
| `tool` | `ask_user` | `ok` | `{ clarification_id, answered_by }`——用户回答后落定 |
| `tool` | `ask_user` | `declined` | `{ clarification_id }`——用户取消 ⇒ 这一轮失败即拒 |

所以 UI 在同一个事件上有**两个互相配合的表面**：**时间线行**（被动，「正在提问…」）与**中断提示框**（主动，来自 `stream.interrupt.value` 的真实问题）。两者共享 `clarification_id`。

## 6. 生命周期与边界情况

- **只串行（v1）：** agent 同一时刻最多问一个问题；一轮里可以依次问好几个（中断 → 回答 → 恢复 → 可能再中断）。不做并行 / 批量提问。
- **持久化：** 被中断的这一轮活在线程 checkpoint 里。如果用户关掉标签页，线程保持暂停；重新打开线程会让 `stream.interrupt` 再次浮现。（取决于 checkpointer 的持久性——见 §8。）
- **取消 / 拒答：** 走 §4 的拒答路径 ⇒ 失败即拒的拒答。
- **无效 / 空回答：** 自由输入题给了空内容 ⇒ 客户端禁用提交；如果空内容真的送到了，服务端按拒答处理。
- **v1 服务端没有超时：** 服务端不会丢弃一个待处理的澄清；线程就一直等。

## 7. 最终答案的 provenance

最终的 `Answer`（以及它的 `answer_view`）会多出一个 `clarifications` 列表，让审计能看到问了什么、答了什么：

```jsonc
"provenance": {
  "clarifications": [
    { "clarification_id": "clar_ab12", "question": "…", "answer": "logged in last 30 days", "answered_by": "user" }
  ]
}
```

`answered_by:"user"` 用来把一次服务期的 HITL 回答与 curator 的 Simulated SME 回答区分开，这样两者在 ledger 里永远不会被混为一谈。

## 8. 能力位与特性开关

- `/capabilities` 增加 **`can_clarify: boolean`**。只有当 `can_clarify` 为真时，UI 才挂载中断提示框，这样一个没带 HITL 构建的服务端（或 REST / 离线剖面）能干净降级。
- `can_clarify` 只在流式服务路径上为真（它需要支持中断的传输层）。

## 9. TypeScript 类型（前端，对齐 `lib/steps.ts` 的风格）

```ts
export interface ClarificationChoice { id: string; label: string }

export interface ClarificationRequest {
  kind: "clarification";
  clarification_id: string;
  question: string;
  why: string;
  choices?: ClarificationChoice[];
  allow_freeform?: boolean;
  tier: "audit";
}

export type ClarificationResponse =
  | { clarification_id: string; answer: string }
  | { clarification_id: string; choice_id: string }
  | { clarification_id: string; declined: true };

// Hook: const stream = useStream<ChatStreamState, ClarificationRequest>(…)
// Render when stream.interrupt != null; resolve via stream.respond(response).
```

## 10. 待定问题——**仅服务端**（不属于本前端契约）

下面这些**不会**改动上面的线上契约；它们由引擎侧解决，列在这里只是让前端团队知道它们存在：

- **恢复时的重新执行。** 整条流水线跑在单个 `answer` 节点里，而这个节点编译时没带 checkpointer（由服务端注入）。恢复时这个节点会重跑；确定性的前缀（route / retrieve / assemble）会重新执行，内层 agent 则从 checkpoint 回放已完成的步骤。需要确认内层 `create_agent` 子图在嵌套的 `answer_question_agent` 调用中能否正确 checkpoint，以及前缀重跑是可以接受的，还是应该把流水线抬升成图节点。
- **checkpointer 的持久性。** `langgraph dev` 注入的是内存 saver，所以一个暂停的轮次会在服务端重启时死掉。持久的 HITL 需要 Postgres checkpointer（就是 ADR 里那个推迟项）。v1 可以先用内存 saver 发布。
- **`ask_user` 与 `recursion_limit`。** 一次中断是暂停，并不消耗一个超级步，但恢复后那次工具往返会消耗；需要确认上限的计账方式。
- **触发策略。** 是由 *agent* 自己决定调用 `ask_user`（自由裁量、由 prompt 驱动），还是要有一道确定性的歧义关口？草案假定是 **agent 驱动的工具**（它在推理中途自己决定）；确定性关口是另一个选项。

## 11. 决策（2026-07-14 达成一致）

1. **载荷形状**（§3/§4）：自由文本 + 可选的受限 `choices`，以 `clarification_id` 作为关联键。✅
2. **拒答 = 拒绝作答**（§4，D3）：一次被拒绝的澄清导致失败即拒。✅
3. **澄清是一个 ledger 的 `tool` 事件**（§5），并落进 provenance（§7），带 `answered_by:"user"`。✅
4. **`can_clarify` 能力位**（§8）作为 UI 的开关。✅
5. **串行、一次一个**（§6），v1 不做批量。✅
6. **agent 驱动的 `ask_user` 工具**（§10 的触发策略），不是确定性的歧义关口。✅

## 12. 服务端实现状态（2026-07-14）

引擎侧已经建好并在离线通过测试；剩下的工作是前端。

- **`ask_user` 工具**——在 `analyst/tools.py`（只在 `enable_clarify` 时加入），调用 `interrupt(clarification_request(...))`。载荷构造器与响应解析器在 `analyst/clarify.py`（就是 §3/§4 的形状；`clarification_id` 是由问题确定性推导出来的，所以重跑会推出同一个 id——不用时钟，也不用随机数）。
- **中断 / 恢复管路**——内层 `create_agent` 跑在一个按轮计的内存 checkpointer 上（`ServeStack.clarify_checkpointer`）；聊天图的 `answer` 节点（`api/graph_app.py`）检测到这次暂停后调用 `interrupt(request)`，从而让**外层**图暂停——也就是说 `stream.interrupt.value` == 那个 `ClarificationRequest`。`Command(resume=response)` 会一路送回内层 agent。由一次 spike 加 `tests/test_serve_clarify.py` 验证（中断能浮现、恢复 → 受治理答案 + provenance、拒答 → 拒绝作答，以及关闭特性时的行为一致性）。
- **能力位**——`/capabilities` 返回 `can_clarify`（只有在流式路径上且有线上模型时为真）；`openapi.json` 已重新生成。
- **v1 的限制（服务端侧，不影响线上契约）：** 澄清用的 checkpointer 是**内存的、按进程的**，所以一个暂停的轮次挺不过服务端重启（持久的 Postgres checkpointer 是推迟的后续项，§10）；一个被拒答的轮次会让内层线程在内存里一直暂停，直到 GC 或线程复用。单次澄清与一轮内依次多次澄清都有测试覆盖（`tests/test_serve_clarify.py`）；并行 / 批量澄清不在范围内（§6）。

### 对 LangGraph HITL 最佳实践的符合度

对照 `langgraph-human-in-the-loop` 的指引审过一遍：

- **checkpointer + thread id + 可 JSON 序列化的载荷**——内层 agent 跑在 stack 的 `InMemorySaver` 上，用按轮计的 `thread_id`（`{outer}:{human-turn}`，在多次恢复重跑之间保持稳定）；载荷是一个普通 dict。
- **「节点在恢复时会从头重跑；`interrupt` 之前的代码必须是幂等的。」** 已验证：护轨前缀（route / retrieve / assemble / 查缓存）都是纯读；`_working_memory_from` 是从历史重建一个新对象（不是持久地追加）；`ask_user` 工具在 `interrupt` 之前只做一次纯粹的 `clarification_request`。内层 agent 对**数据的触碰**（`run_query` / `sample_rows`）是**从内层 checkpointer 回放的，不会重新执行**——有一个测试断言恢复之后 ledger 里 `run_query` 恰好出现一次。`_finalize`（写缓存、narrate）只跑一次，且只在 agent 完成之后。
- **通过 `Command(resume=…)` 恢复**（作为输入时绝不用 `Command(update=…)`）；前端的 `stream.respond()` 映射到这个。
- **无害的重复发送：** 恢复时确定性前缀会把它的 `rail` 事件再发一遍，但前端 reducer 是按 `id` / `step:seq` 给行做键的，而前缀是确定性的，所以它们会折叠进同一批行（不会出现重复的时间线行）。这一点依赖那个确定性——特此记录给维护者。

**前端 TODO**（对照 §2/§3/§4/§9）：把 hook 类型写成 `useStream<ChatState, ClarificationRequest>`；在 `stream.interrupt != null` 时渲染提示框（问题 + 为什么 + 选项 / 自由输入）；通过 `stream.respond(response)` 恢复；用 `capabilities.can_clarify` 给 UI 加开关；在活动提示框旁边显示 `ask_user` 的时间线行（§5）。
