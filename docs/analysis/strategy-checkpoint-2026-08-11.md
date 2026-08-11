# 临时策略检查点（2026-08-11）

**性质：临时。** 不是 ADR，不是产品承诺。供作者（Minhao）在「研究仪器 + 可演示产品」双轨上对齐判断。事实以当前树与已引用 arm 为准；数字必须带 arm / corpus，否则不可引用。

过期条件：下一次正式 ADR 或 open-work 大改覆盖本节主张时，删除或整页替换本文件，不留「曾几何时」附录。

---

## 1. 现在什么是真的（事实页）

### 产品形态

- 研究代码，无生产用户；API 与 corpus 格式仍会变（README Project status）。
- 引擎：自然语言 → 只读 SQL → 确定性 layer stack 先检再跑；模型**不持有**数据库句柄。治理边界是**没有某类工具**，不是「请模型守规矩」。
- 语义层（corpus）与数据湖（obfuscated lake）是**外部 sibling 仓库**；本仓只服务它们。`corpus_content_hash` 是测量身份；勿往 corpus checkout 写生成物。
- UI 在独立仓 [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui)；本仓是引擎。

### 四件套与本环境

| 仓 | 角色 | 约定路径 / 获取 |
|---|---|---|
| [governed-bi](https://github.com/Minhao-Zhang/governed-bi) | 引擎 | 本仓 |
| [governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) | Next.js 纯客户端 | `../governed-bi-ui` |
| [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) | 语义层（无 README，按 schema 分目录） | `../BIRD-corpus`；钉 commit |
| [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) | 混淆湖 + `eval_dataset/` | 本地目录须为 `../BIRD-Data-Obfuscation`（**GitHub 名 ≠ 工具默认路径**） |

- 湖 dump 在 Hugging Face：`minhaozhang/BIRD_Obfuscation`（≈12–13 GB），sibling 仓已有 `docker compose` + `pg_restore`；本仓**没有**一键下载脚本。
- 本仓 `data/bird/beer_factory.sqlite`（~1 MB）只供 tests/CI，**不是** LangGraph serve 数据源；serve 要 Postgres。
- README「三项 env」不够：serve 还要 `GOVERNED_BI_API_KEY`；UI 要 `NEXT_PUBLIC_GOVERNED_BI_API_KEY`（或等价）与之对齐。

### 当前可引用测量（v4）

- Arm **v4**，corpus `BIRD-corpus` @ `30872d3`，1 351 题 / 57 schema：
  - 交付 EX **0.676**
  - 选择回答时准确率 **0.714**（n = 1 278）
  - 拒答 73（5.4%）；可计价 62 中 **77.4%** 若强答会错
- 失败互斥桶（438）：全覆盖仍答错 **257**；冻结字面量 gold 75；capped 49；覆盖不全答错 33；refused 20；clarification 4。
- 拒答几乎全是检索上下文不足（19/20 终局 `r_table_not_licensed`；4 次 clarification 零 licensed），**不是**「会校准难度的 ML 弃权」。
- WrenAI 对照：同一 73 题全答、准确率 56.2% vs v4 承诺集上 WrenAI 68.5%——说明拒答集大多**可答**；abstention 跟踪的是**本引擎本回合是否够上下文**，不是题难。诚实标题窄于「calibrated abstention」（[open-work](../open-work.md) §4.1）。
- 臂间比较用配对 McNemar / 不一致对，禁止用两个 EX 相减当结论；同配置不同 seed 可差 ~12.7% 题（SE(net) ≈ 1.0pp；`--replay-routing` 后约 0.83pp）。先定阈值再跑；先查机制再信分数。

### UI / HITL 现状（契约债）

- ADR 0007：答案卡用 `{outcome, text, …}`；**禁止**合成 `tier` / `safety_clearance` / `semantic_assurance`。
- UI README 仍卖 dual-axis stamp，并指向已删的 `docs/ui-frontend-handoff.md`、过时的 `uv run --extra agents --extra api`——接 live 时易「跑完无答案卡」或澄清死锁。
- **Clarification HITL**（`ask_user`）已落地：进程内可用；`checkpoint_durable: false`，进程重启丢 pause；stream `command.resume` 不走 `resume_authorised`。
- **Post-answer 工程审阅 / 批准工作流不存在。** `reflect` 不挡交付；`ProvenanceStatus.certified` 是语料元数据，不是答案门闩。

### 已拍板的产品边界（对话共识）

| 议题 | 决定 |
|---|---|
| RLS / RBAC | **不做进产品**；依赖 DB role；对外不说「企业权限产品」 |
| PII | 假定入站数据已脱敏；**暂不做** SELECT 级混淆；客户 corpus 仍应避免把原始样例值写进 `body` |
| 高风险决策 | **HITL + 工程审阅**，不做校准 ML 弃权产品化 |
| Demo | 改善可下载 DB 工作流；引擎+UI 可 monorepo / compose；corpus/lake **继续外置** |
| 定位 | 跨平台 / 仓外 / 偏脏数据，对位 Cortex / Genie——卖强制预执行门禁，不卖仓内 ACL |
| 简历叙事 | Minhao；卖**判断力 + 治理拓扑**；成对报 precision@coverage；**EX 不当头条** |

### 引擎里真正值钱的性质

- 检索 miss → 可见拒答，而不是错表上的自信答案。
- 每次 turn 可审计：跑了什么、拒了什么、为什么。
- Deep Agents 已退役；`StateGraph` + `create_agent` 节点是既定形状。
- 护城河是「错答通道被结构切断 + 可复盘」，不是「更懂业务」或「行级权限」。

---

## 2. 我们选择不当什么（边界页）

1. **不当仓内 Copilot。** 不跟 Cortex / Genie 比「贴着 warehouse 的原生体验」；卖的是仓外、跨平台、脏数据上仍可治理的查询路径。
2. **不当企业权限产品。** 不做 RLS/RBAC 栈；权限停在连接角色。把治理说成「行级安全」是定位污染。
3. **不当隐私清洗层。** 不做 SELECT obfuscation；PII 责任在入站。corpus `body` 里的字面量风险是合规债，不是当前卖点。
4. **不当校准弃权系统。** §4.1 已写明：拒答是「上下文不够」的机械后果，不是难度校准。高风险靠 HITL + 人审，不靠阈值分数演戏。
5. **不当 BIRD 刷榜机。** BIRD 是仪器不是产品。只抬 EX、却帮不了真实语义层客户的改动是缺陷。简历头条不是 EX。
6. **不当「假治理」叙事。** 禁止把 abstention precision 说成「知道哪些题难」。WrenAI 只能**框住**主张，不能**归因**；真正归因臂是放宽 Layer 6 allowlist、固定模型与 corpus（§4.2）——未做之前措辞保持窄。
7. **不当把 corpus/lake 吞进 monorepo「图省事」。** Demo 可 compose、可下载 DB；测量身份与资产外置不变。
8. **不当第二套「谁算 attempt / 预算是多少」实现。** 一层 stack、一个 register、一本 ledger；双读是本仓反复付过的税。
9. **不当把 SQLite fixture 当公开 demo 湖。** UI README「bundled SQLite」与引擎 serve 路径矛盾。
10. **不当 free-SQL 过滤器冒充 compile-through 语义层。** 全覆盖仍错 257 次是主桶；与 MetricFlow「能答则近确定」不是同一品类。

---

## 3. 双轨缺陷摘要（探索结论）

### 共享

- 治理挡的是越权/未许可表，不是全覆盖下的语义答错（257）。
- 拒答 ≈ 检索 miss，不是难度校准（§4.1 + WrenAI 1.22×）。
- Corpus prose 不可从源重建；客户冷启动是策展，不是接 DSN。
- 噪声地板高（12.7% discord）；&lt;~2pp 的 EX 波动通常不可测。

### 公司试点特有

- 安全：A5/A6（流式 identity / resume 同线程非同调用方）、B1 状态泄露、A9 checkpoint 明文、J3 body PII；OpenAPI 仍写「无认证」与实现不一致。
- 无租户；共享 API key；无 durable HITL。
- 语义层交付成本 ≫ 引擎调参；薄语料 → 硬拒或错 schema。

### 简历特有

- Metric theater：单报 EX / 宽口径弃权会被自己的 open-work 拆穿。
- 无托管 demo；三仓 + HF + 密钥摩擦大。
- 强项是方法与边界，不是「生产级 AI 产品」证据。

### 双轨张力

研究诚实（拒答、低 EX、open-work）与销售抛光、BIRD 仪器与客户语料、单实现大胆改与企业稳定 API——目标函数冲突。窄兼容点：治理拓扑 + 外置语义层 + 可下载 demo 湖 + **草案须人审**。

---

## 4. 接下来的工作（两条并行序 + 事前成功标准）

> 每条标准在动手**前**写下；数字出来后再改标准不算标准。双轨默认：**研究轨保仪器诚实，产品轨只包装已诚实的行为。**

产品装配轨与研究叙事轨**并行**，不要互相替代。

### 产品装配轨（让人能跑、敢信）

#### P1 — UI ↔ 引擎契约对齐（含 clarification 字段）

**事前成功标准：**

- UI 不再渲染 / 依赖 `safety_clearance` / `semantic_assurance` / `tier`。
- Live 模式：一问可见 `outcome` + SQL 或拒答理由；澄清 interrupt 含 `kind` / `clarification_id` / `why`，不出现静默死锁。
- UI README / 启动命令与本仓 `docs/usage.md` 一致（含 API key 对齐）。

#### P2 — Demo 编排：可下载 DB + 路径别名（corpus/lake 外置）

**事前成功标准：**

- 文档或脚本：clone corpus、clone Obfuscation **到 `BIRD-Data-Obfuscation`**、`hf download`、compose restore（日常可只起 `pg_rename_decoy`）、写 `.env`（含 `GOVERNED_BI_API_KEY`）。
- 陌生人按文档跑通：CLI 或 UI 一问一拒（对齐 README airline / Clothing 类样例）。
- 不把 dump / 生成物写入本仓或 corpus 树。

#### P3 — Review HITL（交付门，不同于 clarification）

**事前成功标准：**

- `answered` 后可进入 `review_status: pending|approved|rejected`（或等价）；业务默认只消费 approved。
- Interrupt `kind: "review"`（勿复用 clarification prompt）；落盘 durable（不能只靠内存 pause）。
- **不**引入 ML 置信度门；与 attempt ledger 分开——review 是交付门，不是 layer。
- 诚实标注：在 durable checkpointer 落地前，`hitl_survives_process_restart` 仍为 false。

#### P4 — Demo / pilot 前信任硬伤

**事前成功标准（机制）：**

| 优先级 | 项 | 说明 |
|---|---|---|
| MUST | A6 / A5 | resume 鉴权与流式 identity；否则 HITL 叙事是假的 |
| MUST | open-work §1.7 | 禁止对外交付「`answered` 且无 SQL」 |
| 视语料 | §3.2a `r_ambiguous_fold` | 当前语料未触发；客户/重建语料前升 MUST |
| 不挡近端 | C6（已 accepted） | stamp 未 wrap |

OpenAPI 描述与「已有 API key」对齐，避免文档宣称无认证。

#### P5 —（可选）Monorepo：引擎根 + `apps/ui` + compose

**事前成功标准：**

- compose 起 api + ui + **demo/空 Postgres**；完整湖仍外置挂载或远端 DSN。
- 不挪 `REPO_ROOT` 语义除非显式改所有 `../BIRD-*` 默认；CI path-filter 拆开 UI。
- 成功 = 启停编排与契约同仓；**不**宣称 `compose up` = 可引用测量或产品就绪。

### 研究 / 叙事轨（保持仪器诚实）

#### R1 — 对外叙事锁死 §4.1 窄表述

**事前成功标准：**

- 凡出现拒答/弃权数字处，必须同时出现：「上下文不足」「priced subset 62」「非题难校准」。
- 禁止单独出现「calibrated abstention」作产品能力。
- README「How well it works」与简历一句，都能用同一窄句复述且不互相打架。

#### R2 — 抬高可答集质量：检索 / licensing（最大可赢桶）

**事前成功标准（先机制）：**

- 目标桶是 open-work §1.5：gold 表未全部 licensed 的失败（跨结果 73），不是盲目抬 EX。
- 开跑前写下：arm 名、是否 `--replay-routing`、McNemar 阈值。
- 成功 = 该桶计数下降，且 abstention 构成仍可解释为检索；失败 = EX 动了但 licensed 覆盖/拒答直方图说不清。
- 成对报 precision@coverage；勿单报「准确率涨了」。

#### R3 —（可选）Layer 6 归因对照臂，先 ADR 后动手

**事前成功标准：**

- 书面 ADR：`licensed` = 检索预算 vs allowlist 是否解耦；对比臂只动 allowlist 宽度。
- 开跑前：样本、配对方法、接受/拒绝阈值写死。
- 在归因臂出数之前，对外话术**不升级**为「治理层独立贡献了 X pp」。

#### R4 — 刻意后置

- 为抬整体 EX 大改 agent（MDE ≈ 2.3pp）。
- 再训 confidence / reflector 作弃答器（已测失败）。
- 全企业 RLS / SELECT 脱敏产品化。
- 把 corpus / 湖并进 git。

---

## 5. 双轨若腐蚀研究伦理：主要风险

1. **Demo 驱动改测量。** 为「演示好看」放宽 Layer 6、吞掉拒答、或改评分口径 → EX 好看但「miss → 拒答」性质没了。
2. **把 §4.1 窄事实卖成宽能力。** 写成「会弃权的智能」→ 对照臂与归因未完成时的过度宣称。
3. **Monorepo / compose 污染 corpus 身份。** 生成 store、样例写入 corpus 树 → `corpus_content_hash` 漂移。
4. **EX 成为隐形 KPI。** 成功标准事后改写成「分涨了」。
5. **第二实现 / 旁路治理。** Demo 热路径或 UI 另写 attempt/预算语义。
6. **权限/PII 叙事回潮。** 销售随口承诺 RLS 或 SELECT 脱敏。
7. **HITL 被做成置信度剧院。** 用未校准分数触发「人审」。
8. **为试点藏 open-work / 合成 trust badge。** 直接违反 ADR 0007，简历诚信蒸发。

**护栏一句：** 产品轨只能**展示**研究轨已测清的行为；任何改引擎行为的 demo 需求，先过测量教义（机制 → 事前阈值 → 配对检验），再谈发布话术。

---

## 6. 建议的「此刻默认下一步」

1. **P1** UI 契约对齐（含 clarification）——否则 demo/简历当场拆穿。  
2. **P2** 把 Obfuscation 已有 HF+compose 接到本仓 README/编排脚本（路径别名 + API key）。  
3. **P4** §1.7 + A5/A6（与 HITL 演示绑定）。  
4. **P3** Review HITL 最小态（兑现 high-stakes）。  
5. **R1/R2** 与装配并行：叙事锁死 + licensing 桶（不刷 EX）。  
6. **P5** Monorepo 跟在能跑通之后。

---

*作者自用。探索依据：本仓 README / open-work / failure-modes / ADR 0006–0007 / audit-2026-08-10；兄弟仓 GitHub README；2026-08-11 双轨对话与并行审阅。*
