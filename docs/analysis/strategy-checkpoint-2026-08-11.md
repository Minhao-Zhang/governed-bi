# 临时策略检查点（2026-08-11）

**性质：临时。** 不是 ADR，不是产品承诺。目标只有一条：**本仓是作品集，作者会带着面试官走一遍。**
企业部署、试点交付、多租户明确出界；演示时假定有一个可连的库，编排不占本文件的预算。数字必须带
arm / corpus，否则不可引用。

过期条件：§4 队列走到 (3)（英文叙事页）为止，本文件即失效——届时整页替换或删除，不留「曾几何时」
附录。§1 事实页先于队列过期：任何一行与当前树不符，就改那一行。

---

## 1. 现在什么是真的（事实页）

### 产品形态

- 研究代码，无生产用户；API 与 corpus 格式仍会变（README Project status）。
- 引擎：自然语言 → 只读 SQL → 确定性 layer stack 先检再跑；模型**不持有**数据库句柄。治理边界是**没有某类工具**，不是「请模型守规矩」。
- 语义层（corpus）与数据湖（obfuscated lake）是**外部 sibling 仓库**；本仓只服务它们。`corpus_content_hash` 是测量身份；勿往 corpus checkout 写生成物。
- UI 已并入本仓 `ui/`（Next.js）；引擎与前端同仓，但只经 HTTP 相接，双向都没有 import。

### 三件套与本环境

| 仓 | 角色 | 约定路径 / 获取 |
|---|---|---|
| [governed-bi](https://github.com/Minhao-Zhang/governed-bi) | 引擎 + Next.js 纯客户端 | 本仓；前端在 `ui/` |
| [BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus) | 语义层（无 README，按 schema 分目录） | `../BIRD-corpus`；钉 commit |
| [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) | 混淆湖 + `eval_dataset/` | 本地目录须为 `../BIRD-Data-Obfuscation`（**GitHub 名 ≠ 工具默认路径**） |

- 湖 dump 在 Hugging Face：`minhaozhang/BIRD_Obfuscation`（≈12–13 GB），sibling 仓已有 `docker compose` + `pg_restore`；本仓**没有**一键下载脚本。
- SQLite 只是 tests/CI 的离线基底（`datasource/sqlite.py` + 各 test 用 `sqlite3` 现建 `tmp_path` 库），**不是** LangGraph serve 数据源；serve 要 Postgres。本仓**没有**任何随仓提交的 `.sqlite` 文件——早先此处写的 `data/bird/beer_factory.sqlite` 在树里不存在。
- serve 要 `GOVERNED_BI_API_KEY`（`api/auth.py`）：未设不是「开放」，是每个请求 401；一把 key 即一个 principal。UI 侧 `NEXT_PUBLIC_GOVERNED_BI_API_KEY` 须与之相等（`docs/usage.md`）。

### 当前可引用测量（v4）

- Arm **v4**，corpus `BIRD-corpus` @ `30872d3`，1 351 题 / 57 schema：
  - 交付 EX **0.676**
  - 选择回答时准确率 **0.714**（n = 1 278）
  - 拒答 73（5.4%）；可计价 62 中 **77.4%** 若强答会错
- 失败互斥桶（438）：全覆盖仍答错 **257**；冻结字面量 gold 75；capped 49；覆盖不全答错 33；refused 20；clarification 4。
- 拒答几乎全是检索上下文不足（19/20 终局 `r_table_not_licensed`；4 次 clarification 零 licensed），**不是**「会校准难度的 ML 弃权」。
- WrenAI 对照：同一 73 题全答、准确率 56.2% vs v4 承诺集上 WrenAI 68.5%——说明拒答集大多**可答**；abstention 跟踪的是**本引擎本回合是否够上下文**，不是题难。诚实标题窄于「calibrated abstention」（[open-work](../open-work.md) §4.1）。
- 臂间比较用配对 McNemar / 不一致对，禁止用两个 EX 相减当结论；同配置不同 seed 可差 ~12.7% 题（SE(net) ≈ 1.0pp；`--replay-routing` 后约 0.83pp）。先定阈值再跑；先查机制再信分数。

### UI / HITL 现状（契约债）

- ADR 0007：答案卡用 `{outcome, text, …}`；**禁止**合成 `tier` / `safety_clearance` / `semantic_assurance`。
- `ui/` 的代码与文案现已一致（校对于 2026-08-12）：`lib/schemas.ts` 明写这三个字段不得回来，`ui/README.md` 同样写明「没有 reliability tier、没有 `safety_clearance` / `semantic_assurance` stamp」和「There is no SQLite deployment」。`ui/AGENTS.md` 与 `ui/DESIGN_QUESTIONS.md` 不存在，指向已删 `docs/ui-frontend-handoff.md` 的链接也已清干净。
- **Clarification HITL**（`ask_user`）已落地：进程内可用；`checkpoint_durable: false`、`hitl_survives_process_restart: false`（`api/routes.py`），进程重启丢 pause；`stream.submit(null, {command: {resume}})` 直达 `Command(resume=…)`，**不过** `resume_authorised`（ADR 0007 §6，明列为 out of scope）。
- **Post-answer 工程审阅 / 批准工作流不存在，也不建**（§2.11）。`reflect` 不挡交付；`ProvenanceStatus.certified` 是语料元数据，不是答案门闩。

### 已拍板的边界（对话共识）

| 议题 | 决定 |
|---|---|
| RLS / RBAC | **已修订 2026-08-12**：做**接缝**不做产品——port + 默认适配器 + 一个参考适配器 + `r_table_not_authorized`；RLS 谓词只声明、默认拒答，执行仍归 DB role。对外仍不说「企业权限产品」。原决定与理由见 §2.2（保留删除线） |
| PII | 假定入站数据已脱敏；**不做** SELECT 级混淆；corpus 仍应避免把原始样例值写进 `body`。**2026-08-12 增补**：新增列级拒绝 `r_column_not_authorized`——拒的是**引用**，不是改写值，所以这一行本身没变 |
| 高风险决策 | 不做校准 ML 弃权产品化；也不做没有审阅者的审阅门 |
| Demo | 一段录屏（一答一拒 + ledger 可见）；corpus / lake 继续外置 |
| 定位 | 跨平台 / 仓外 / 偏脏数据，对位 Cortex / Genie——讲强制预执行门禁，不讲仓内 ACL |
| 简历叙事 | 卖**判断力 + 治理拓扑**；成对报 precision@coverage；**EX 不当头条** |

**UI 并仓：已决，并入。** `506ad9b`（2026-08-11）把前端搬进 `ui/`，与 §1 事实页首段一致；本行此前写着「唯一明确未定」，是队列先于事实页过期的一例。它跟 §2.7 仍是两个问题，此前被混为一谈过一次：corpus / lake 外置是测量身份问题，已拍板且不重开；UI 并仓不碰 `corpus_content_hash`，是发现性与契约漂移问题——[open-work](../open-work.md) §5.3 记着那次分裂的代价。

### 引擎里真正值钱的性质

- 检索 miss → 可见拒答，而不是错表上的自信答案。
- 每次 turn 可审计：跑了什么、拒了什么、为什么。
- Deep Agents 已退役；`StateGraph` + `create_agent` 节点是既定形状。
- 护城河是「错答通道被结构切断 + 可复盘」，不是「更懂业务」或「行级权限」。

---

## 2. 我们选择不当什么（边界页）

1. **不当仓内 Copilot。** 不跟 Cortex / Genie 比「贴着 warehouse 的原生体验」；卖的是仓外、跨平台、脏数据上仍可治理的查询路径。
2. ~~**不当企业权限产品。** 不做 RLS/RBAC 栈；权限停在连接角色。把治理说成「行级安全」是定位污染。~~

   **已推翻并修订（2026-08-12，作者本人）。原文保留删除线，不覆盖——本仓的规矩是「翻案要记，不是抹掉」。**
   新边界一句话：**做接缝，不做产品**（[ADR 0012](../adr/0012-access-seam-principal-and-authorization.md)）。

   | 做 | 仍然不做 |
   |---|---|
   | `Principal`（一个 id + roles）、`AccessPolicy` port、一个「什么都不改」的默认适配器、一个参考适配器（roles→grant 读 TOML） | 租户模型、身份提供方、用户库、per-caller token、权限管理 UI、策略语言 |
   | Layer 6 拆成两条规则：`r_table_not_licensed`（检索没找到）与 `r_table_not_authorized`（你不能看） | 放宽 `licensed`；grant **永远只能收窄**，不能加表 |
   | 列级拒绝 `r_column_not_authorized`——PII 接缝 | SELECT 级脱敏 / 掩码 / 令牌化。§2.3「不当隐私清洗层」原样成立：拒绝的是**引用**，不是改写**值** |
   | 行级谓词**声明**，且默认 `refuse`——声明了却不能执行就拒答 | 把谓词注入语句。ADR 0012 §5：违反 0006 G4，且在 OUTER JOIN / UNION 分支 / 同名 CTE 下语义就是错的。RLS 归数据库角色 |

   **为什么翻。** 原文对「作品集」这个目标是对的，目标在 2026-08-12 改了：本仓要成为**可 fork 的企业底座**。
   但「有接缝」和「是权限产品」不是一回事，上表右列就是这条界线本身。三件事约束这次翻案不滑坡，
   其中两件当天就补上了：
   默认 grant 仍是**开放**的（两处 import 期断言：`govern/policy.py::_assert_policy_tracks_the_register`
   查默认 `access_grant.is_open`，`govern/access.py::_assert_the_default_adapter_is_inert` 查解析后的三个谓词恒定），
   95 个既有对抗用例逐字节判定不变（套件现为 115 例 = 62 attack / 53 benign，新增的四张虚构表只承载新维度）；
   接缝**已不止在 `govern/` 内生效**——ADR 0012 §8 欠的四根线于 2026-08-12 全部落地：
   `api/graph_app.py::resolve_access_grant`（组装根问一次策略）、
   `serve/delivery.py::tool_bounds_from_state`（把 grant 折进 `ToolBounds`）、
   `serve/session.py::_resolved_knobs`（§7 那根）、
   以及渲染器 `serve/context.py::withheld_by_grant`（同一集合既收窄 prompt 又从 `readable_assets` 里减去）；
   §8.5 当天复审后又补了 `api/visibility.py`，用**同一个** `withheld_by_grant` 收窄 browse 路由；
   grant 的 digest **已进 knob**——`serve/session.py::_resolved_knobs` 把 `policy.access_grant.digest()`
   写成 `access_grant`（`Role.comparability`，因此进配置哈希），所以「两个 arm 授权不同却哈希相同」这条已关（§7）。

   §2.6 的措辞纪律不变：Layer 6 归因臂**仍未跑**，对外不得升级为「治理层独立贡献了 X pp」。ADR 0012 只是把那个臂
   从「改代码」降成「换一个 `Grant`」。
3. **不当隐私清洗层。** 不做 SELECT obfuscation；PII 责任在入站。corpus `body` 里的字面量风险是合规债，不是当前卖点。
4. **不当校准弃权系统。** [open-work](../open-work.md) §4.1 已写明：拒答是「上下文不够」的机械后果，不是难度校准。
5. **不当 BIRD 刷榜机。** BIRD 是仪器不是产品。只抬 EX、却帮不了真实语义层客户的改动是缺陷。头条不是 EX。
6. **不当「假治理」叙事。** 禁止把 abstention precision 说成「知道哪些题难」。WrenAI 只能**框住**主张，不能**归因**；真正归因臂是放宽 Layer 6 allowlist、固定模型与 corpus——未做之前措辞保持窄。
7. **不当把 corpus/lake 吞进 monorepo「图省事」。** 测量身份与资产外置不变。
8. **不当第二套「谁算 attempt / 预算是多少」实现。** 一层 stack、一个 register、一本 ledger；双读是本仓反复付过的税。
9. **不当把 SQLite fixture 当公开 demo 湖。** SQLite 是 tests/CI 基底，serve 走 Postgres；两者不可混为一谈。（当初触发这条的 UI README「bundled SQLite」文案已于 2026-08-11 改掉。）
10. **不当 free-SQL 过滤器冒充 compile-through 语义层。** 全覆盖仍错 257 次是主桶；与 MetricFlow「能答则近确定」不是同一品类。
11. **不当没有审阅者的审阅门。** 交付前的人工批准工作流只有在有人真的审的时候才是治理；此处没有第二个人。给一个空门配上 interrupt 与状态机，就是 §5.6 那种置信度剧院，而且它会把「治理边界是缺一个工具」这句唯一有力的话稀释成「我们加了个流程」。

---

## 3. 缺陷摘要

### 会当场被自己的文档拆穿的

- 治理挡的是越权 / 未许可表，不是全覆盖下的语义答错（257）。
- 拒答 ≈ 检索 miss，不是难度校准（[open-work](../open-work.md) §4.1 + WrenAI 1.22×）。
- Corpus prose 不可从源重建；换客户是策展工作，不是接 DSN。
- 噪声地板高（12.7% discord）；&lt;~2pp 的 EX 波动通常不可测。
- 仪器自身有洞：D9 与「八个不会失败的测试」——见 §4 (4)(5)。这两条决定一个数字**是否可引用**，比任何新数字优先。

### 刻意不修：审计过、排过序的安全项

指针全在 [audit-2026-08-10](audit-2026-08-10.md)。排序按「若真要部署，先修哪个」：

| 序 | 项 | 事实 |
|---:|---|---|
| 1 | A6 | `/chat/resume` 是同线程校验而非同调用方（`_identity` 回落到 `{"token": thread_id}`），且 `/chat` 可覆写已存 identity |
| 2 | A5 | 流式传输完全没有 identity 绑定：该节点 2026-08-11 从 `api/graph_app.py::_accept_node` 搬到 `serve/accept.py::accept_node`，`identity` 仍然一个字都没传 |
| 3 | B1 | `get_state`、`values` 帧与 `POST /threads/search` 会回 `identity` 与 `delivery.context_block`；`output_schema` 只收窄 `invoke` |
| 4 | A9 | checkpoint 明文落 `.langgraph_api/*.pckl`，含 identity token 与渲染后的语料 |
| 5 | J3 | 规则 V5 禁 `summary` 写字面量，于是它们进了 `body`——而 `body` 每次命中都进 prompt，`summary` 从不进。无任何 gate 检查 `body` 的 PII |

**为什么不修。** 一把 API key 即一个 principal（`api/auth.py`），没有第二个调用方，因此 A5/A6/B1
是「未部署」的后果而不是疏忽；也无从验证——没有越权者可以拿来试。修掉它们要引入租户与 per-caller
token，那**仍然**是被 §2.2 划出界的产品：2026-08-12 的修订加的是授权**接缝**，右列（租户 / 用户库 /
per-caller token）一项没动，所以这四条的处置**不变**。留着的价值更高：**审出来、排了序、能说清每一条
为什么不修**，比悄悄补掉更能说明判断力。J3 是唯一会随语料变坏的一条——换成真实客户语料前必须重判。

**但触发条件变了，写下来。** 这四条现在有了明确的失效点：**一旦存在第二个 principal，A5/A6/B1 立刻从
「未部署的后果」变成活的漏洞**，且必须先于任何非开放 grant 修好——否则引擎能拒掉越权的表，却分不清
是谁在问。[docs/enterprise-fork.md](../enterprise-fork.md) 把这句话写进了 fork 指南的欠账清单。

---

## 4. 队列（单序，事前标准）

> 每条标准在动手**前**写下；数字出来后再改标准不算标准。

**(0) 修 UI 文案。已完成（2026-08-11）。** `ui/README.md` 里 two-axis stamp、bundled SQLite demo、
`uv run --extra agents --extra api` 和指向已删 `docs/ui-frontend-handoff.md` 的链接都已清掉；
陌生人照 UI README 走一遍，得到的心智模型与 `lib/schemas.ts` 一致。

**(1) README 头条改序。已完成（2026-08-11，`5c162c8`）。** 事前标准是：拒答与 precision@coverage 在前，
EX 在后且带口径；凡出现弃权数字处，必须同时出现「上下文不足」「priced subset 62」「非题难校准」；禁止
「calibrated abstention」单独作为能力出现。核对现表：0.714（n = 1,278，94.6% coverage）在第一行，
declined 73 在第二行，77.4%（48 of the 62）在第三行，unfiltered EX 0.676 在第四行；表下三条 caveat 写明
declines 不是难度估计、跟踪的是本回合上下文；「calibrated abstention」在 README 里一次都没有出现。

**(2) 录一段短 demo。** 一道答得出的题 + 一道拒答，**ledger 可见**：跑了什么、拒在哪一层、为什么。
成功 = 不加旁白也能看出「模型没有句柄」；失败 = 只看见一个答案卡。不为录屏改引擎行为（§5.1）。

**(3) 写一页英文叙事。** 结构固定：主张 → 对照臂 → 反驳 → 收窄后的主张。素材就是
[open-work](../open-work.md) §4.1：
77.4% 是主张，WrenAI 1.22× 是反驳，「拒答跟踪的是本回合上下文是否够，不是题难」是收窄结果。
成功 = 面试官读完能自己说出这个主张**不**成立的地方。

**(4) D9 —— 已完成（2026-08-11）。** `context_hash` 距离门曾在只差随机 seed 的 run1/run2 上以 **0.9993**
通过：它自认为在问「treatment 变了吗」，实际在测检索抖动，而后者恒为真。现已按审计 Phase 2 的处方降级为
**存在性检查**，treatment 判定移到声明字段上——`eval/report.py::knobs_comparable`，由调用方**声明** treatment，
声明不出来即 `cannot_evaluate`（这正是 run1/run2 的诚实判定）。`xfail(strict=True)` 那个 positive control 按 D9
行自己的警告**重新指向**而非删除。

顺带补上了一个更大的洞：`comparability_keys()` / `config_hash_keys()` **零生产调用者**，而且根本不存在
`config_hash`——record 只有 `context_hash` / `delivery_hash` / `corpus_content_hash` / `prompt_set_hash`。
所以两个 arm 在 `chat_model` 上不同也能被判为可引用。现已接线，9 处变异逐一验证。

**注意：这两处修复没有在真 artifact 上验证过。**`runs/` 是 gitignored，null pair 不在做修复的这台机器上，
两个 control 都 skip；验证走的是合成 fixture。真机上跑一遍仍是欠账。

配套落地了 `arms.toml`：arm 的 treatment 从此committed 且可 diff，不再只活在某台机器的 `.env` 里。

**(5) 测试完整性债。前半已完成（2026-08-11），后半仍在走。** [open-work](../open-work.md) §3.9：25 处变异里
**8 处**在全绿套件下存活，形状只有一种——**断言常量等于自己**。其中两处正是 `corpus_content_hash` /
`prompt_set_hash` 置 `None` 不被发觉（`test_a_measured_row_names_both_treatment_identities` 断的是
`"corpus_content_hash" in row`，`None` 满足），而这道门恰恰就是当时替 D9 遮丑的那道。
事前标准是「八处逐一变异 → 看红 → 还原；改不到会红的断言直接删」，已照此执行：八处全部以 `s39-` 前缀
声明进 `tools/mutation_catalogue.py`（现为 9 条，两处 routing_pinned 各占一条），2026-08-11 验证逐条被杀。
§3.10 是同一病的另一半，**还没走完**：声明了没人消费，一轮扫出 28 项，检查器现报 **6** 项（写这条时是
28 修掉 14）；`tools/check_declared_is_consumed.py` 仍刻意未入 CI，条件写在
`tests/conformance/test_register_closure.py`，其中 `test_the_declared_but_unconsumed_set_does_not_grow`
按**名字**钉住这 6 项，多一项与少一项都会红。剩下 6 项里 3 项要的是决策不是接线。
这是 §5 全部风险里唯一一条能被工具挡住的。

**(6) R2：抬高可答集质量——检索 / licensing（最大可赢桶）。**
事前标准（先机制）：目标桶是 [open-work](../open-work.md) §1.5——gold 表未全部 licensed（覆盖 0.936，
1 145/1 224），跨结果 73 例失败；不是盲目抬 EX。开跑前写下：arm 名、是否 `--replay-routing`、
McNemar 阈值。成功 = 该桶计数下降且 abstention 构成仍可解释为检索；失败 = EX 动了但 licensed 覆盖 /
拒答直方图说不清。成对报 precision@coverage。**排在 (4)(5) 之后不是礼貌**：在那两条落地前，本臂对比的
可引用性由一道已知会误判的门决定。

### 刻意后置

- 为抬整体 EX 大改 agent（MDE ≈ 2.3pp，不可测）。
- Layer 6 归因对照臂（放宽 allowlist、固定模型与语料）。要做需先出 ADR；**在它出数之前，对外话术不升级为「治理层独立贡献了 X pp」**（§2.6）。
- 再训 confidence / reflector 作弃答器（已测失败）。
- 全企业 RLS / SELECT 脱敏产品化；把 corpus / 湖并进 git。

---

## 5. 若腐蚀研究伦理：主要风险

1. **演示驱动改测量。** 为「录屏好看」放宽 Layer 6、吞掉拒答、或改评分口径 → EX 好看但「miss → 拒答」性质没了。
2. **把 [open-work](../open-work.md) §4.1 的窄事实卖成宽能力。** 写成「会弃权的智能」→ 归因臂未做时的过度宣称。
3. **演示或重跑污染 corpus 身份。** 生成 store、样例写进 corpus 树 → `corpus_content_hash` 漂移，此前每一个数字的身份跟着变。
4. **EX 成为隐形 KPI。** 成功标准事后被改写成「分涨了」。
5. **第二实现 / 旁路治理。** 热路径或 UI 另写 attempt / 预算语义。
6. **把 `reflect` 说成交付门。** 它不挡交付，也不是置信度分；说成门就是剧院。
7. **为好看藏 open-work / 合成 trust badge。** 直接违反 ADR 0007，且这是一份**以诚实为卖点**的作品集——藏一次，全部主张同时失效。

**护栏一句：** 对外只能**展示**已经测清的行为；任何「为了演示改一下引擎」的念头，先过测量教义
（机制 → 事前阈值 → 配对检验），再谈话术。

---

*作者自用。依据：本仓 README / open-work / failure-modes / audit-2026-08-10 / ADR 0006–0007 /
`tests/eval/test_the_delivery_gate_can_fail.py`；UI 仓 checkout（`README.md`、`lib/schemas.ts`）；
2026-08-11 目标重定向对话。*
