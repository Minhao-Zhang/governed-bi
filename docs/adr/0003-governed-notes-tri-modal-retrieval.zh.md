# 0003: 受治理的笔记（`NoteAsset`）与三模态检索

_[English](0003-governed-notes-tri-modal-retrieval.md) · [简体中文](0003-governed-notes-tri-modal-retrieval.zh.md)_

- **状态：** Proposed（提议中） (2026-07-21)。设计已在一次多 agent 设计评审中
  达成一致（4 份独立提案、3 位不同的评审、以及一次对抗性红队评审；三位评审都
  各自将"泛化 `RuleAsset`"排在第一）。尚无代码，等待下面 5 个待定问题与
  Phase 1。
- **决策者：** 项目负责人 + 设计会议
- **相关文档：** [0002](0002-governed-agentic-serve-runtime.md)；
  [pipeline-design.md](../pipeline-design.md)；
  [design-decisions.zh.md](../design-decisions.zh.md)（D6 人工关口、D9 语料库
  文件结构、D10 提议者+对抗者、D15 多 schema、D16 agentic 核心）；
  [asset-schemas.zh.md](../asset-schemas.zh.md)；
  [plans/datalake-run.md](../plans/datalake-run.md)（路由相关数字）
- **取代：** 整个 `skill` 资产概念：`SkillFrontmatter` / `SkillKind`
  (`schemas.py:388-396,130-134`)、`corpus/<schema>/skills/*.md` 这个
  markdown 表面，以及（从未成立的）"`kind=routing` 的 skill 会影响 schema
  路由"这一说法。

## 背景

**数据湖场景（D15）。** 一个 Postgres 数据库里约 69 个 schema；一个路由器为
每个问题挑选 schema。两个已诊断出的缺口，加上一个治理漏洞，促成了这份 ADR。

**缺口 A：路由从不参考 skill。** Schema 路由（`agent.py::assemble`，大约
`~365-402` 行）是在 `schema_documents()`（`schema_router.py:86-112`）之上做
排序，而 `schema_documents()` 只把 Table / Metric / FewShot / Term 这几类文档
按 schema 分桶；skill 完全被排除在这次排序之外。skill *确实*会按 schema 过
滤，但只发生在 `filter_corpus_for_retrieval`（`schema_router.py:355-360`）内
部，而这份过滤后的语料库（`retrieval_corpus`）喂给的是 `retrieve()`
（`agent.py:410`），不是喂给 prompt。因此 `kind=routing` 的 skill 无法影响
schema 的选择结果；它只在检索评分时被过滤，却从不在 prompt 内容层面被过滤。
更糟的是，`assemble` 在构建 prompt 时调用的是
`assemble_context(corpus, retrieval, ...)`，传入的是**原始、未过滤的
`corpus`**（`agent.py:426`），而不是 `retrieval_corpus`，于是每个 schema 的
skill 都会被转成 `SkillView` 并渲染进每一次 prompt（`context.py:273-276`、
`context.py:403-408`），无论到底路由到了哪个 schema。在 69 个 schema 的规模
下，这是无条件的膨胀，不是优雅降级。

**缺口 B：没有任何东西会创建 skill。** 没有任何 curator 路径会构造一个
skill。`Skill`/`SkillFrontmatter` 不在 `Asset` 的判别联合类型（discriminated
union）里（`schemas.py:403-414`），因此它从不会被 `asset_document`
（`rvgd.py:81-106`，其中没有 `Skill` 分支）索引，从不会被 `validate_corpus`
校验，也从不会被对抗审查。`adversary.review()` 只检查 `TableAsset`
（`adversary.py:73-74`），而 `adversary.refute()`（本应审视一个 skill 主张的接口）目前是 `raise NotImplementedError(...)`（`adversary.py:104`）。磁盘上
唯一存在的那个 skill，`corpus/beer_factory/skills/routing.md`，是手写的，
标注为 `status: draft`（第 5 行），却从未有任何对抗者在它上面跑过，因为根本没有机制可以跑。

**治理（P6）。** skill 是唯一一种游离在治理基座之外的标注：
`SkillFrontmatter`（`schemas.py:388-396`）带有 `provenance`，但没有
`Governance` 区块，没有分级（tier），也没有 provenance-aware 的检索。这不是
假设性的问题。`corpus/beer_factory/skills/routing.md:30` 在文字中点名了
`transaction.CreditCardNumber`（"is PII and is excluded; never select
it"），而那一列确实带有 `governance.excluded: true`
（`corpus/beer_factory/tables/tbl_beer_factory_transaction.yaml:82-84`）。这
一列本身被正确地隐藏于所有受治理工具之外，但 skill 文字中*点名*了它，却原样
注入进了 SQL prompt（`context.py:403-408`），这是一个活生生的 D6 排除性泄露，就藏在一条"贴心小贴士"里。

**术语陷阱。** "Skill" 这个词被三重重载：(1) 上面说的语料库 markdown 资产，
(2) Deep-Agents 的 `SKILL.md` 能力，(3) 泛指的 agent 工具。只有 (1) 真正上线
了，而且是作为无人问津的惰性数据上线的。

**关键洞察。** `RuleAsset`（`schemas.py:361-371`）已经是"一条可挂载到任意资产
上的受治理笔记"的约 90% 形态。它有 `kind` + `scope`（资产 id 列表；空列表 =
全局） + `statement` + `confidence` + `audit`；它**已经**在 `Asset` 联合类型
里（`schemas.py:403-414`）；它**已经**被索引（`asset_document` 对
`RuleAsset` 返回其 `.statement`，`rvgd.py:102-103`）；而且从"挂上
`Governance` 区块不需要改动任何管线"这个意义上说，它已经是治理就绪的。它是与
`TableAsset` 同处一个联合类型里的一等 Pydantic 模型，而不是像
`SkillFrontmatter` 那样另起一套 frontmatter 加正文的解析路径。`Skill` 其实
是同一个想法（挂在某个东西上的一条受治理标注），只是把上述每一项属性都剥掉了。

**当前的检索是双模态的。** BM25 词法检索（`rvgd.py`）加上一条可选的
embedding 余弦检索通道（`embedding.py`），经由 Reciprocal Rank Fusion 融合
（`embedding.py:53-79`）。目前没有正则/模式检索模式，也没有让 agent 直接读取
一条笔记文本的工具。路由探针（`docs/plans/datalake-run.md`）在 2030 个问题
的样本池上测得：仅用 embedding 的 recall@3 = 0.70，BM25 为 0.35，RRF 为 0.535
（`schema_router.py:143-145`）：把偏弱的词法通道和偏强的 embedding 通道融合
在一起，反而*拉低了* recall。而按 schema 拼接每个资产的文本构成的单一文档
（`schema_documents`，`schema_router.py:86-112`），每多折进一个资产，向量就
被稀释一分。

**`db` 和 `schema` 不是资产。** `schema` 只是 `TableAsset`
（`schemas.py:261`）上的一个 `SchemaName` 字段，不是独立的资产类型。目前没有
`DbAsset`/`SchemaAsset` 可以挂笔记。

**方向。** Skill 应该泛化为"关于任意资产（schema/db/table/column）的笔记"，
检索应该支持三种访问模式：语义相似度、正则/关键词模式匹配，以及 agent 直接
读取一小段文本。

## 决策

**删除 `skill`。将 `RuleAsset` 泛化为 `NoteAsset`**，即一种可挂载到任意资产
**或**命名空间上的受治理标注。"规则"（rule）就简化为一条
`enforcement=always` 的笔记。曾经考虑过另起一个全新的 `NoteAsset` 原语、让
`RuleAsset` 保持原样不动，但这个方案被否决了：它会把 `RuleAsset` 已经具备的
一切（类型化、纳入联合类型、被索引、可限定范围）重新推导一遍，却没有带来任何
超出"原地泛化"的好处。

```python
class NoteKind(str, Enum):
    # from RuleKind: default enforcement = always
    business_rule = "business_rule"
    constraint = "constraint"
    context = "context"
    # from SkillKind: default enforcement = on_match
    routing = "routing"
    gotchas = "gotchas"
    domain_overview = "domain_overview"
    pattern = "pattern"


class Trigger(_Strict):
    kind: Literal["keyword", "regex"]
    value: str


class NoteAsset(_Strict):
    asset_type: Literal["note"] = "note"
    id: str

    # ── Inference (curator writes / gold fills) ──
    kind: NoteKind
    scope: list[str] = Field(default_factory=list)  # asset/namespace ids; empty = global
    title: str | None = None
    statement: str
    triggers: list[Trigger] = Field(default_factory=list)
    enforcement: Literal["always", "on_match"]  # kind sets the default; a validator hard-enforces it
    confidence: Confidence | None = None
    related_notes: list[str] = Field(default_factory=list)

    # ── Governance: NEW vs. RuleAsset, closes a latent D6 gap ──
    governance: Governance | None = None

    audit: Audit | None = None
```

### 范围（scope）模型：命名空间 vs 资产的曲折之处

`db`/`schema` 今天还不是资产，这份 ADR 也不打算把它们提升为资产，因为那会牵动
`corpus/schemas.py`、loader、以及路由器的一大片改动，而笔记功能本身并不需要
这些。取而代之的做法是：用前缀来给 scope 条目定类型；资产 id 永远不含 `:`，
所以这个 sentinel（哨兵前缀）空间是空闲可用的。

| scope entry | resolves against | meaning |
|---|---|---|
| `tbl_…` / `col_…` / `metric_…` / `join_…` | asset ids (+ derived column ids) | 资产引用 |
| `schema:beer_factory` | `list_schemas(corpus)` (`schema_router.py:36-38`) | 命名空间引用 |
| `db:main` | the whole (single-DB) lake | 整个数据湖的所有 schema |
| `[]` (empty) | n/a | 全局（GLOBAL） |

这些 sentinel 前缀之后可以升级为一个结构化的 `ScopeTarget`（`asset` /
`schema` / `db` / `global` 的判别联合类型），且不需要任何数据迁移：字符串编码
本来就是结构化形式所能表达内容的一个严格子集。

### 三模态检索与"PIN，绝不 blend"的契约

| Mode | Purpose | Wiring point (file:function) | Fusion rule |
|---|---|---|---|
| **语义（自有向量）** | *检索*阶段（路由之后）的 recall 驱动力 | `asset_document(NoteAsset)` 返回 `title + statement`，在 RVGD 检索索引里按资产逐条 embedding（`embedding.py:45-50`），在 RRF 后仍受笔记预算约束 | 正常**blend** 进 RRF。这是 schema 路由*之后*才构建的 `retrieve()` 索引（`agent.py:410`），所以它提升的是范围内召回，而不是 schema 选择。每条笔记都是自己独立的向量，不会稀释某张表的向量；笔记正文完全不进入路由用的 `schema_documents` 信号（见下文"这解决了什么"里的缺口 A 说明）。 |
| **正则/关键词触发器** | 针对已命名的漏检问题做确定性修补 | 新增 `retrieval/triggers.py::fire_triggers(corpus, q)`，被合并进 `selected`（`rvgd.py:354-372`）以及 `shortlist_schemas`（`schema_router.py:130-180`） | **PIN，绝不 blend。** 词法触发分数永远不进入 RRF，这是在尊重上文 RRF-拖累-recall 的发现。有上限（≤3），以 certified > draft > confidence 作为平局判定规则。 |
| **Agent 直读** | "agent 直接读取一小段文本" | 新增只读、无需授权（licensing）的工具 `read_notes(target)` / `grep_notes(pattern)`，加入 `make_tools` 返回的工具列表（`tools.py:289`） | **两者都不适用。** 不加入 `_GOVERNED_TOOLS`（`middleware.py:40`），因此 `wrap_tool_call` 的分发逻辑（`middleware.py:219-222`，`if name not in _GOVERNED_TOOLS: return handler(request)`）会原样放行、不做处理。两个工具都会通过 `_is_excluded`（`tools.py:33-35`）遵守 `governance.excluded`；读到一条点名了表 X 的笔记，仍然需要先执行 `inspect_schema(X)` 来为 `run_query` 授权 X。安全性来自拓扑结构，而不是工具自身的自觉。 |

### 这解决了什么

缺口 A（schema 限定的笔记变得能被路由信号触达，但**只通过 trigger-PIN 模式与
推迟到 Phase 6 的 max-pool 向量，而不是语义模式**：路由发生在 `retrieve()`
*之前*（先 `agent.py:402` 再 `:410`），排序用的是 `schema_documents`，其中从不
包含笔记，所以 Phase 1-3 让笔记受治理、在 prompt 里可见，却还不改变 schema
选择）、缺口 B（笔记成为一种 curator 能产出、adversary 最终能审查的受治理资产）、
P6（笔记继承完整的治理基座），以及未过滤语料库这个 bug 导致的每次 prompt 都膨胀
的问题。附带收益：今天 `RuleAsset` / `NegativeExampleAsset` 都没有 `Governance`
区块，而且两者都是 `_Strict`（`extra="forbid"`，`schemas.py:146-149`），所以带
`governance:` 键会在解析时*被直接拒绝*，而不是被无声忽略，因此 D6 排除对一条
rule 根本无法书写；给 `NoteAsset` 加上 `governance` 才让它生效。

**关于 PII 泄露修复的边界。** 一个 `Governance` 区块只能*整条*排除一条笔记。
`governance.excluded` 是资产级的（`_is_excluded`，`tools.py:33-35`），没有任何
东西会扫描笔记 `statement` 的正文，所以一条在正文里*点名*了被排除列的笔记（就像
`routing.md:30` 点名 `CreditCardNumber` 那样）在结构上并没有被拦住；而 Mode-C 的
`read_notes` / `grep_notes` 工具还会直接返回 statement 文本，新增了一个泄露面。
`CreditCardNumber` 这个具体例子只能靠在迁移时删掉那一行来关闭；要有结构性保证，
需要一个扫描正文的校验器。

### 诚实的局限（来自红队）

1. **对问题本身做正则触发是最弱的模式。** 它是词法性的，因此继承了 BM25
   0.35 recall 那种词汇不匹配的问题；它只能修补*已命名*的漏检（这本身有过拟
   合风险，因为每加一条触发器，都意味着有人已经先看到过一次失败）；而且它不
   会抬升未见问题上的 0.70 recall 天花板；手写触发器也无法扩展到 69 个
   schema。正则真正的价值在于对资产**文本**做 `grep_notes`，而不是对输入的
   问题做匹配。默认只用关键词触发器；对问题本身做正则匹配则推迟（它需要引入
   `regex`/RE2 依赖并加上单次匹配超时，因为 Python 标准库的 `re` 没有 ReDoS
   超时保护）。
2. **未经认证（uncertified）的笔记必须在路由排序上拿到零话事权。** 单条错误
   的笔记只要抬高了某个 schema 的分数，就可能把正确的 schema 挤出
   `top_k=3`（`DEFAULT_SCHEMA_TOP_K`，`schema_router.py:33`）。这一点必须被
   证明，而不是被假设：在把 PIN 交付生产之前，先跑一次对抗性错误笔记测试，
   证明 recall@3 不会因此退化。
3. **`on_match`/被检索到的笔记今天没有注入路径。** `assemble_context` 从不
   读取一份"被*触发*（而非仅仅是范围匹配且已授权）"的笔记 id 列表（类似
   `retrieval.rule_ids`）注入渲染后的 prompt（`context.py:290-297` 只按
   已授权的 scope 匹配来注入）。如果在这条路径打通之前，就把常驻的 skill 文字
   直接迁移成 `enforcement=on_match`，其内容会悄无声息地不再传达给模型。这是一次退化，不是中性的无操作。
4. **scope 注入解析器目前只匹配 `licensed_table_ids`**
   （`context.py:290-297`）。它必须扩展到 `schema:` / `metric_` / `join_` /
   `col_` 这些 scope；照现在的写法，一条以 column 为 scope 的笔记今天永远不会
   被注入，schema 限定或 metric 限定的笔记同样如此。
5. **`adversary.refute()` 目前是 `NotImplementedError`**
   （`adversary.py:104`）。在 LLM 反驳（refutation）这个接口真正落地之前，
   一条被认证（certified）的 PIN 只能依赖结构性检查（`review()`，
   `adversary.py:52-93`）。目前唯一真实的关口是"有人签字确认了"（D6），而不是
   "一个对抗者尝试攻破但没能成功"。

## 影响

**正面**
- 用一个原语同时关掉缺口 A 和缺口 B，而不用做两套并行的修复。
- 治理升级：`NoteAsset`（连带每一条 rule）都获得了真正的 `Governance` 区块，
  因此一整条笔记可以被 D6 排除（今天做不到，因为 `extra="forbid"` 会拒绝
  `RuleAsset` 上的 `governance` 键）。但这本身并不能阻止被排除的标识符在笔记
  正文里*被点名*（见上文缺口 A 的边界说明）；那需要一个扫描正文的校验器，或
  手动删除。
- 删除了整个未受治理的表面（`SkillFrontmatter`、
  `corpus/<schema>/skills/*.md` 这一约定、loader 里独立的 `skills` 通配符），
  而不是在原地给它硬套一层治理。
- 一次性交付了三种被要求的检索模式，外加"笔记可挂载到任意资产*或*命名空间"
  这一能力；schema/db 级别的指引在此之前根本无法表达。

**负面 / 成本**
- 重命名带来的改动是实打实的，不是表面功夫：本仓库自己的后端 `/skills` 路由
  加 presenter（`api/app.py:296-299`、`viz/presenter.py`），以及姊妹仓库
  `governed-bi-ui` 的 `/skills` 表面，都要同步迁移，否则会产生分歧。
- 注入解析器的扩展（诚实局限 #4）和 `on_match` 的接线（#3）是承重的，不是
  可有可无的小事。跳过任何一个，Phase 1 迁移过去的内容都会从 prompt 里悄悄
  消失。
- 如果以后真的要做对问题本身的正则匹配（按 #1 已推迟），它会打开一个 ReDoS
  攻击面，上线前必须先做好预算（RE2 或超时机制）。
- 对抗者接口（#5）仍未建成；对一条笔记来说，"certified"目前仍然只意味着
  "有人看过"，不意味着"一个独立模型尝试攻破过而没有成功"。

## 考虑过的替代方案

- **在 `RuleAsset` 保持不动的前提下，另起一个一等公民的 `NoteAsset`。**
  已否决；它会把 `RuleAsset` 已经具备的一切重新推导一遍。支持二者分离的唯一
  论据是"一个类型无法同时承载常驻注入和触发式注入这两种语义"，可一旦
  `enforcement` 变成一个字段而不是类型区分，这个论据就不成立了。
- **以检索索引为中心的设计（一个独立于资产检索之外、专门的标注索引）。** 没有
  被整体采纳，但它对 pin-vs-blend 的严格要求（绝不让词法触发分数进入 RRF）
  被嫁接进了上面的"决策"部分。
- **只做 agent 工具为中心的设计（Mode C 工具，没有语义/正则模式）。** 它的
  工具被嫁接进来成为 Mode C，但作为*唯一*方案被否决：它唯一的路由杠杆在
  schema 挑选阶段，无法撬动数据湖探针所暴露出来的那道 recall 天花板。
- **保持 `skill` 原样，只是把它接入路由。** 已否决；这会让这个资产永远处于
  无治理、curator 也无法创建的状态，而这正是缺口 B，不只是缺口 A。
- **"笔记不过是现有资产上多加的几个描述字段。"** 已否决；一个无类型的字段
  无法像一等资产那样被独立治理、排除、触发，也无法限定到某个命名空间。

## 迁移（分阶段；每个阶段都可独立发布）

1. **先只做改名，不引入新的检索行为。** `RuleAsset` → `NoteAsset`
   （`asset_type: rule → note`，id 前缀 `rule_` → `note_`，目录 `rules/` →
   `notes/`）；加上 `Governance` 区块（单独就能关掉 rule 这边的 D6 缺口）和
   `enforcement`，配一个校验器强制约束 `kind → enforcement`。删除整条 skill
   路径：`SkillFrontmatter`、`SkillKind`、frontmatter 解析器、loader 里的
   skills 通配符和 `Corpus.skills`（`loader.py:87`）、`SkillView` 及其渲染
   代码块（`context.py:273-276,403-408`）、`dump_skill`、`schema_router` 的
   skills 过滤器（`schema_router.py:355-360`）、CLI 的 `n_skills` 计数、
   `ids.py` 里 skill 的 id 匹配模式（`ids.py:30,43`）、`serialize` 的 skills
   写入分支（`serialize.py:70,95-99`）、`corpus`/`viz` 的 `__init__` 里 skill
   相关再导出，以及（这些都在*本*仓库里，不只是 UI）后端 HTTP 面：
   `GET /skills` + `SkillResponse`（`api/app.py:296-299`、`api/schemas.py:281`）、
   `HealthResponse.n_skills`（`api/schemas.py:45`）、`AssetTypeFilter`
   （`api/schemas.py:270`，`'rule'` → `'note'`），以及 `presenter.SkillView` /
   `skill_views`（`viz/presenter.py:121-131,378,519-527`）。把唯一真实
   存在的那个 skill（`routing.md`）迁移成若干条粒度更细的笔记；其中大部分
   内容去重后落进 `rule_boolean_flags`、一个 `Column.reliability.note`，以及
   `governance.excluded`（`CreditCardNumber` 那一行整条消失，因为排除机制
   已经覆盖了它）。
2. **把注入真正接上。** 把 scope 注入解析器（`context.py:290-297`）扩展到
   `schema:` / `metric_` / `join_` / `col_`；把被触发的/`on_match` 的笔记渲染
   进 prompt。在依赖 Phase 1 迁移过去的任何内容之前，先加一条"无 EX 退化"的
   eval 分支和一个 prompt 体积的 CI 上限。
3. **Agent 直读工具。** 把 `read_notes` / `grep_notes` 加入 `make_tools`
   （`tools.py:289`）；这一步就能交付原始需求里"正则 + agent 直接读文本"这
   一半，完全不触碰评分路径。
4. **触发器 PIN。** 加上 `Trigger` + `retrieval/triggers.py` +
   shortlist 层面的触发器 PIN（仅关键词、有上限、在 RRF 之外）；在拿去信任
   全部 69 个 schema 之前，先在一个留出的（held-out）切分上测量触发器覆盖率。
5. **认证（certified）门控 PIN。** 把 dev 阶段的晋级机制（draft 在 dev 里
   可用，certified 在 prod 里必需）作为独立的、可对比的 eval 分支接上。
6. **第二个按 schema 的向量，仅在确有必要时才做。** 如果 Phase 1-4 之后
   schema 路由的 recall 仍然是 EX 的瓶颈，就加一个 max-pooled 的第二个按
   schema 的笔记向量，并配上计数偏差的缓解手段；无论如何，笔记都不参与
   `schema_documents` 的拼接。
7. **为笔记落地 `adversary.refute()`。** 一条笔记就是一个主张（claim），
   是那个尚未建成的反驳接口（`adversary.py:96-104`）最自然的第一个客户端。
   在把"certified"这个 PIN 权限交付生产信任之前，先把它落地。

Phase 1-3 交付的是这个功能最核心的部分（可治理、可创建、支持三模态检索、可挂到
任意资产或命名空间上的笔记）；Phase 4-7 是加固与规模验证。

## 待定问题（留给维护者）

1. **重命名的改动量大 vs 改动量小。** 完整的 `rule` → `note` 重命名（会破坏
   UI 那个 `/skills` 表面，强制两边同步迁移）vs 保持 `asset_type`/目录仍为
   `rule`，只在词汇/文档层面改名。*建议：直接大刀阔斧改名，因为这是一个没有
   用户的绿地项目（AGENTS.md）。*
2. **前缀 sentinel vs 现在就上结构化的 `ScopeTarget`。** *建议：现在先用
   sentinel；以后再升级，且不需要任何迁移。*
3. **要不要做对问题本身的正则匹配，还是推迟。** *建议：推迟。只用关键词
   触发器；`grep_notes` 已经覆盖了对文本做正则匹配这块需求，不需要为此背上
   ReDoS 依赖这个问题。*
4. **全局常驻笔记的预算。** 需要给 `scope=[]` 的笔记加一个 CI 上限
   （最大条数 + 最大字符数），否则这份 ADR 想要修复的"每次 prompt 都膨胀"问题
   会卷土重来，只是从 skill 换了个马甲变成 rule。
5. **PIN 权限的门控。** 在 Phase 5 上线之前，先确认"draft 用于 dev / certified
   用于 prod"这个默认策略。
