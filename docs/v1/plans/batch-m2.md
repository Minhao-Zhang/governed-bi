# 第二批工作单 · M2 零风险收敛（N5–N8）

2026-07-31 立。分支从 `impl/rebuild-first-batch` 起。上游是 [near-term-plan.md](near-term-plan.md) 的 M2 一节 —— **那一节只给了目标，这一份给做法**。

> **语言：简体中文，无英文孪生。**同 [near-term-plan.md](near-term-plan.md)，`.zh` 后缀宣称的是「某份英文文档的中文孪生」，这份没有英文源头。

## 这一批是什么

四项，**全部不花钱** —— 没有一项需要跑模型、连数据库或起 server。M1 那批要 Postgres 和真 Gateway，这批不用。整批的风险面只有一个:**改名和收敛会碰到很多文件**。

| 项 | 一句话 | 估工 | 会不会改行为 |
|---|---|---|---|
| **N5** | glossary 补齐：六条同音词陷阱 + 缺失的运维/实验词 | 1.5 人日 | 否，纯文档 |
| **N6** | 三个重名小函数各收敛成一份 | 0.5 人日 | **`_slug` 会 —— 而且是修 bug** |
| **N7** | `Corpus.table_by_name`：裸名歧义返回 `None` | 1.5 人日 | **会，27 个名字上** |
| **N8** | 锁死线协议依赖的版本范围 | 0.5 人日 | 否 |

**四项互不依赖，可以四个人同时开。但 N5 要先落地** —— 后面所有涉及术语的判断都要引用它，在一个自己都没定义清楚的术语表上改名是白改。

PR 规约、review 标准、「不许顺手改」那几条,全部沿用 [near-term-plan.md 的「交付规约」](near-term-plan.md)。基线：**1690 passed / 8 skipped / 1 xfailed**。

---

## 开工前：上游 spec 的六处更正

near-term-plan.md 的 M2 一节是从 rebuild-checklist 1.1 / 1.2 / 1.4.5 / 1.5 摘的，摘的时候没有逐行核代码。**2026-07-31 我核了，六处要改。照原文做会踩到其中至少三处。**

| # | 原文说 | 实际 | 影响 |
|---|---|---|---|
| 1 | 第三处裸名查找在 `agent.py:465` | **`agent.py:508`**，而且它不是函数,是一个内联 genexp。`:465` 是 router 的构造代码,毫不相干 | 按原文找会找不到 |
| 2 | 「`_render` 三处未必是同一件事,先读再决定」 | **三个完全不同的函数**，同名而已：渲染 prompt / 渲染查询结果摘要 / 渲染 diff 元素。**不要合** | 合了就是错的 |
| 3 | `_slug` 三处重复 | 两处**逐字节相同**，第三处（`profile.py`）**行为不同** —— 少一个 `or "x"` 兜底。需要判断的是 `_slug`,不是 `_render` | 盲合会改 id 生成 |
| 4 | 「需要一个 `Corpus.concat`，否则 pooled 路径上索引会过期」 | **只有做索引缓存时才需要。**这一批不做缓存 —— 所以 **`Corpus.concat` 这一批不做** | 省掉一整个构造器 |
| 5 | 「45 个高频承重术语一个都不在 glossary 里」 | 措辞不准。glossary 现有 **40 个词条,全部是产品词**（Governed dataset / Curator / Analyst / Reliability stamp…）。**一个运维词、一个实验词都没有** | 换个说法，N5 的形状就清楚了 |
| 6 | 「给 `langgraph` / `langgraph-api` / `langgraph-sdk` 三个包加上界」 | **后两个根本不是直接依赖**，`pyproject.toml` 里没有它们。它们经 `langgraph-cli[inmem]` → `langgraph-runtime-inmem` → `langgraph-api` → `langgraph-sdk` 传递进来 | 决定用什么机制加界，是 N8 的真问题 |

---

## N5 · glossary 补齐

### 目标

一句话概括现状:**glossary 定义的是产品，不是机器。**40 个词条全部在讲「这个系统对外是什么」—— Governed dataset、Curator、Analyst、Reliability stamp、四个臂的名字。而每天读 artifact、判断一个数能不能引用时用的那批词 —— `ledger`、`verdict`、`quotable`、`comparable`、`pooled`、`driver`、`solver`、`resume`、`outcome`、`crashed`、`headline`、`twin`、`replicate`、`claim_ready` —— **一个词条都没有**。

所以 N5 不是「补 45 个漏掉的词」，是**给 glossary 加第二个半区**：运维与实验词汇。

### 第一步：六条同音词陷阱

这六条单独成一节，放在词表**前面**，标题写清楚它们是陷阱不是定义。一条都不能少：

| 词 | 必须写清的事 |
|---|---|
| `graded_delivery` | 是 de**graded**（降级交付），不是「已打分」。`grade`/`grader`/`gradeable`/`hash_grade` 在 src 里全指「对着 gold 打分」。全仓库最糟的同音词 |
| `safety_clearance=False` | **不等于**不安全。只过了 L1–L3、栽在语义层的 SQL 也是 `False`（`analyst/answer.py:236`） |
| `semantic_assurance=unflagged` | **不等于**已验证正确。现有 `Reliability stamp` 词条已经在否定它,但没有自己的行,搜不到 |
| `ledger` | 四个互不相关的意思。这是**唯一一个既高频又零定义**的词 |
| `stamp` | 四个意思。只有「可靠性 stamp」有定义 |
| `scope` | 五个意思：note 附着范围 / L4 授权表集 / 图视窗 / 工具可调用范围 / 一次跑评了哪些题 |
| `tier` | 三个意思 |

（表里七行 —— 「六条」是 near-term-plan 的说法，按内容是七条。以这里为准。）

### 第二步：运维与实验词表

下面是我用**一条命令**数出来的，口径写死：

```bash
python -c "import subprocess,re,pathlib;fs=[f for f in subprocess.run(['git','ls-files','src','tests','docs'],capture_output=True,text=True).stdout.split() if f.endswith(('.py','.md','.toml'))];b='\n'.join(pathlib.Path(f).read_text(encoding='utf-8',errors='ignore') for f in fs);import sys;w=sys.argv[1] if len(sys.argv)>1 else 'ledger';print(len(re.findall(r'(?<![A-Za-z0-9_])'+re.escape(w)+r'(?![A-Za-z0-9_])',b,re.I)))"
```

**这批数和 rebuild-checklist 1.4 里的数对不上**（`ledger` 574 vs 527，`pooled` 257 vs 205）。原因是口径：这条命令含 `.toml`、文件清单用 `git ls-files src tests docs` 而不是 `'src/**'` 那种 pathspec。**两套都不算错，但一份文档里只能用一套。**这一项用上面这条。而且**次数不决定任何事** —— 它只用来说明「这个词承重」，不排优先级（rebuild-checklist 开头就写着按依赖链排，决定 7 专门否决了按影响面排）。

**没有词条的（按次数降序，全部要补）**

`ledger` 574 · `verdict` 412 · `block` 376 · `kind` 358 · `db_id` 357 · `resume` 330 · `budget` 307（**四种不同的 cap，必须分开写**）· `suspect` 306 · `outcome` 281 · `pooled` 257 · `licensed` 234 · `driver` 230 · `refuse` 209 · `quotable` 196 · `solver` 192 · `twin` 163 · `comparable` 154 · `headline` 152 · `crashed` 136 · `replicate` 132 · `fold` 130（**在 eval 语境里第一直觉是交叉验证的 fold —— 没有一处是那个意思**）· `shortlist` 125 · `graded_delivery` 64 · `routing_escaped` 28 · `promote` 25（**和现有 `Promotion loop` 词条正面冲突**：那里是「把发现的模式提炼成认证数据集」，代码里是「把构建产物从 staging 搬出来」）· `licensed_tables` 23 · `claim_ready` 20 · `hygiene_ok` 9

**有词条但不够用的（要补一行或改一行）**

- `arm` 1333、`rung` 324 —— 四个臂的名字都有词条（`Baseline` / `Seeded arm` / `Curated arm` / `Curated+SME arm` / `Recoverable ceiling`），但**单位本身 `arm` 没有**，`rung` 只出现在「已退役词汇」那段里。整个实验设计的核心单位没有定义。
- `stage` 671、`scope` 668、`step` 435、`index` 392、`layer` 300、`tier` 280、`stamp` 262、`pin` 149、`harness` 197 —— 词在 glossary 里出现过，但都是**别的词条的正文提到它**，没有自己的行,`Ctrl+F` 搜不到定义。

### 怎么做

**只写现状，不做改名。**每个词条写「代码里它现在是什么意思」，一词多义的把几个意思都列出来并各给一个 `file:line`。改名归 rebuild-checklist 1.4，**不在这一批**。

`docs/glossary.md` 是一张 Markdown 表（77 行）。新词条加成第二张表 + 一节陷阱说明,不要塞进现有那张 —— 现有那张是产品词，混在一起两边都难读。

### 验收

**写一个测试。**这是这一项唯一的机器判据：

```python
# tests/test_glossary_covers_load_bearing_terms.py
REQUIRED = ("ledger", "verdict", "quotable", "comparable", ...)   # 硬编码在测试里
```

测试解析 `docs/glossary.md` 的表行,断言 `REQUIRED` 每一个都有自己的行。**`REQUIRED` 必须硬编码在测试文件里,绝对不许从 glossary 自己读出来** —— 那样测试恒真，是个假钉。

再加一条:七条陷阱每条都能 `grep` 到。

### 碰哪些文件

`docs/glossary.md`、新增 `tests/test_glossary_covers_load_bearing_terms.py`。

**`docs/glossary.zh.md` 本次不动** —— AGENTS.md 写着工作进行中只改英文，让孪生漂移。`tests/test_repo_contracts.py` 只查孪生文件**存不存在**，不查内容同不同步，所以不改它不会红。

### 禁止

- 不许改名。
- 不许顺手删「已退役词汇」那段 —— 那段在挡人重新引入退役词。
- 不许把 `arm` 的定义写成「等于 rung」。`rung` 是退役词，新词条不引用退役词。

---

## N6 · 三个重名小函数

三个名字的处置**各不相同**。逐个说。

### 6a · `_FROZEN_GOLD_RE` —— 真重复，直接收

三处**逐字节相同**：

```python
_FROZEN_GOLD_RE = re.compile(r"\bVALUES\s*\(", re.IGNORECASE)
```

`eval/analysis.py:50`、`eval/run_datalake.py:196`、`eval/sql_diff.py:195`。

**收敛目标已经存在**：`eval/sql_diff.py:198` 有 `is_frozen_constant(sql) -> bool`，正是那两个调用点在做的事。

**无环风险,我核过**：`sql_diff.py` 只 import 标准库（`re` / `collections` / `dataclasses` / `enum` / `typing`），一个包内模块都不 import；而 `analysis.py` 和 `run_datalake.py` 现在都还没 import 它。所以两边 `from .sql_diff import is_frozen_constant` 是安全的。

调用点：`analysis.py:346` 与 `:533`、`run_datalake.py:3811`。注意 `:533` 那处的参数是 `gold_sql.get(...) or ""`，换成 `is_frozen_constant(...)` 之后 `or ""` 可以去掉（函数自己吃 `None`）。

### 6b · `_slug` —— 两处相同、一处是 bug

```python
# asset_bag.py:38 与 seed.py:21 —— 逐字节相同
return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "x"

# profile.py:31 —— 少了 or "x"
return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
```

**`profile.py` 那个是缺陷，不是有意的变体。**它的返回值直接进 asset id：

```python
profile.py:85:  id=f"tbl_{_slug(schema)}_{_slug(name)}"
```

而 id 的文法是 `corpus/ids.py:17` 的 `_NAME = r"[a-z0-9]+(?:_[a-z0-9]+)*"` —— **段不能为空**。一个全是标点的表名或 schema 名会 slug 成 `""`，产出 `tbl__foo` 或 `tbl_foo_`，两个都过不了 `is_valid_id`。所以收敛到带 `or "x"` 的版本是**修 bug**，不是改行为。

**做法**：一个函数进 `corpus/ids.py`（那里已经是 id 文法的家）：

```python
def slug(name: str, *, fallback: str = "x") -> str:
```

三处都调它。`fallback` 参数保留是为了让「万一 profile 真的想要空串」这件事变成调用点上的一个显式选择，而不是三份实现之间的沉默分歧 —— 但**默认值就是 `"x"`，profile 不传**。

**先跑一遍确认影响面**：拿 `corpus/` 和 `../BIRD-corpus` 里所有 schema 名与表名过一遍 `_slug`，看有没有真的 slug 成空的。有就在 PR 描述里列出来（那说明现在的 corpus 里已经有非法 id）；没有就写「零命中，本次是防御性修复」。

### 6c · `_render` —— **不要合**

三个同名函数，**没有一个和另一个是同一件事**：

| 位置 | 签名 | 干什么 |
|---|---|---|
| `analyst/context.py:363` | `_render(ctx: PromptContext) -> str` | 把整个 prompt context 渲染成给模型的文本 |
| `analyst/governance.py:259` | `_render(result, generated) -> str` | 把一次查询结果压成一句话（`"x = 3"` / `"no rows"` / `"12 row(s) over [...]"`） |
| `eval/sql_diff.py:446` | `_render(value) -> str` | 把一个 diff 元素（含 `frozenset` join key）转成稳定文本 |

**处置：不合，改名。**`_render_prompt` / `_render_result_summary` / `_render_diff_value`。这是命名冲突，不是重复代码 —— 归 rebuild-checklist 1.4.4 那一类，但既然已经打开了这三个文件，顺手改掉比留着强。

**如果你决定连名都不改**（也是合格交付），就在 PR 描述里写一句「三者无关，仅同名，不合并」。**不许合。**

### 验收

- `grep -rn "_FROZEN_GOLD_RE" src/` 只剩 `sql_diff.py` 一处。
- `grep -rn "def _slug\|def slug" src/` 只剩 `corpus/ids.py` 一处。
- `_render` 三处要么改成三个不同的名字，要么 PR 描述里有那句「不合」。
- `pytest tests/` 全绿，**测试数不减**。

### 禁止

- 不许把 `is_frozen_constant` 挪到别的模块 —— 它在 `sql_diff.py` 里，而 `sql_diff.py` 是个零包内依赖的叶子模块，这个性质要保住。
- 不许在这一项里动 `_table_by_id`（那是 N7）。

---

## N7 · `Corpus.table_by_name`

### 现状（逐处核过）

「先按 id 查，查不到按物理名查」有**三份拷贝，全部取第一个匹配**：

| 位置 | 形态 |
|---|---|
| `analyst/tools.py:38` | `def _table_by_id(corpus, table_id)`，配一个 `_is_excluded` helper |
| `analyst/middleware.py:118` | `def _table_by_id(corpus, table_id)`，exclusion 检查内联写死 |
| `analyst/agent.py:508` | **不是函数**，是一个内联 genexp：`next((a for a in corpus.assets if isinstance(a, TableAsset) and a.physical_name == table_id), ...)` |

第四处 `retrieval/rvgd.py:530-538` **做对了**：

```python
_bare_seen[bare] = _bare_seen.get(bare, 0) + 1
phys_to_table[bare] = a.id if _bare_seen[bare] == 1 else None
```

注释写明了理由 ——「an ambiguous bare name maps to None and grounds nothing, rather than to whichever table happened to be loaded last」。

**注意：rvgd 那处不是一个可复用的函数**，是一个大函数内部的循环。收敛不是「改成调用它」，是**把这个语义提取出来**。

### 为什么值得做

BIRD-corpus 上实测 **731 个表资产里 67 张卷进裸名歧义，涉及 27 个歧义名字**（`pais` 五个、`kunden` 四个）。命中时 agent 收到「`tbl_beer_factory_kunden`: not licensed this turn」—— 泄露一个它从没提过、且在其路由范围之外的表名，还可能死循环到步数上限，最后记成 agent 失败。

### 做法

在 `corpus/loader.py` 的 `Corpus` 类上加一个方法，签名：

```python
def table_by_name(self, name: str) -> TableAsset | None:
    """Resolve a physical table name. Qualified ``schema.table`` always resolves;
    a bare name resolves only when exactly one table corpus-wide carries it."""
```

四条语义，逐条都要有测试：

1. **限定名 `schema.table` 永远能解析**（大小写不敏感 —— rvgd 是 `.lower()` 的，跟它一致）。
2. **裸名唯一时解析**。
3. **裸名歧义时返回 `None`** —— 不是第一个匹配，不是抛异常。
4. **不做 exclusion 过滤。**`Corpus` 是原始容器,`by_id()` 也不过滤,过滤是 `for_analyst()` 的事。三个调用点各自的 `_is_excluded` 检查**原样保留**。

**三条明确不做：**

- **不要把 id 查找折进去。**调用点的顺序是「先 `by_id`,查不到再按名字」,那个顺序留在调用点。`table_by_name` 只管名字。
- **不做索引缓存。**`Corpus.assets` 是一个可变 list（`loader.py:132` 就在 `.append()`），任何 memo 都会过期。线性扫描不比现状差 —— 现在 `_table_by_id` 已经是 `by_id`（O(n)）加第二遍 O(n) 扫描了。
- **因此 `Corpus.concat` 这一批不做**（上游 spec 更正 #4）。它只在做缓存时才是必需品。

### 这是行为变更，要写进 PR

27 个名字上，「返回第一个匹配」变成「返回 `None`」。原先拿到一张**错的**表的调用，现在拿到「找不到」。这会改 eval 结果 —— 方向是好的（错答变拒答或变正确重试），但**它会动数字**，PR 描述里要单独一段说明。

### 验收

- **合成 corpus 的单测**：造两个 `TableAsset`，不同 `schema`、同 `physical_name`。断言裸名 → `None`，两个限定名 → 各自解析。构造要按**那 27 个歧义名字的形状**（一名对多表），不是按 67 张表 —— 67 是被影响的表数，按表数构造是在重复测同一条路径。
- **三个调用点各一条**：断言歧义裸名下返回 `None` 而不是第一个匹配。
- **一条跳过式的真语料测试**（照 M1 里 `BIRD_DB.exists()` 那个写法）：`../BIRD-corpus` 在就加载，断言歧义名字数 ≥ 1 且 `table_by_name` 对它们返回 `None`；不在就 `pytest.skip`。
- 关掉 `docs/open-work.md` 的 C13。
- `pytest tests/` 全绿。

---

## N8 · 锁死线协议依赖的版本范围

### 现状（实测）

`pyproject.toml` 只声明这些：

```toml
"langgraph>=1.0",
"langgraph-cli[inmem]>=0.2",
```

而 `uv.lock` 实际锁的是：

| 包 | 锁定版本 | 直接依赖？ |
|---|---|---|
| `langgraph` | 1.2.8 | 是（`>=1.0`） |
| `langgraph-cli` | 0.4.30 | 是（`>=0.2` —— 声明下界 0.2，实际 0.4.30） |
| **`langgraph-api`** | **0.11.0** | **否** |
| **`langgraph-sdk`** | **0.4.2** | **否** |
| `langgraph-runtime-inmem` | 0.31.0 | 否 |
| `langgraph-checkpoint` | 4.1.1 | 否 |

**主通道的线协议由后两个拥有** —— `/threads`、`/runs/stream`、`stream_subgraphs` 的行为在 `langgraph-api` 和 `langgraph-sdk` 里，而它们**在本仓一个字都没声明**。一次 `uv sync -U` 就能换掉，而本仓 diff 里什么都看不到。任何只管 `openapi info.version` 的版本化策略都管不到这条。

### 真问题：用什么机制加界

两个选项，**选一个，在 PR 描述里写为什么**：

**A. `[tool.uv] constraint-dependencies`**（uv 0.12.0，本机已装）。给传递依赖加约束而**不把它们变成直接依赖**。语义上更准 —— 我们确实不直接 import `langgraph_api`，只是依赖它的 wire 行为。

**B. 加成直接依赖。**在 `dependencies` 里写 `langgraph-api>=0.11,<0.12` 和 `langgraph-sdk>=0.4.2,<0.5`。更直白，任何读 `pyproject.toml` 的人一眼看到；代价是声明了一个我们不 import 的包。

**我倾向 A**，但**没有实跑验证过 uv 0.12 在这个项目里会不会真的应用它** —— 所以验收是经验性的（下面那条 `uv sync -U`），机制选错了验收会自己红。别照抄我的倾向，跑一遍再定。

### 建议的界

| 包 | 建议 | 理由 |
|---|---|---|
| `langgraph-api` | `>=0.11,<0.12` | 0.x，minor 就可能动 wire。收紧 |
| `langgraph-sdk` | `>=0.4.2,<0.5` | 同上 |
| `langgraph` | `>=1.0,<2` | 1.x 已发布正式版，semver 可信度高一些 |
| `langgraph-cli` | `>=0.4,<0.5` | 现在下界写 `0.2` 而实际跑 `0.4.30`,那个下界是假的 |

### 验收

三条，全部机器可查：

1. `uv sync -U` 之后 `uv.lock` 里四个包仍在声明范围内。**跑完记得 `git checkout uv.lock`** —— `-U` 会改写它，别把一次探测提交进去。
2. 一个测试解析 `pyproject.toml`（连同 `[tool.uv]` 如果走 A）里的范围，和文档里那一节比对，不一致就红。
3. **文档要有一处写着「本仓声明兼容的 SDK 版本范围」** —— 只改 `pyproject.toml` 不算做完。落点建议 `docs/architecture.md` 的 §9 Environments（它是英文源，有中文孪生，按 AGENTS.md 这次只改英文）。

### 禁止

- 不许升级任何包。这一项是**声明现状的边界**，不是动版本。`uv.lock` 除了探测之外不该出现在这一批的 diff 里。

---

## 交付顺序

N5 先落地（后面的术语判断都引用它）。N6 / N7 / N8 可以并行。

四个 PR，一项一个。跨项的顺手改会被退回 —— 尤其是 N6 和 N7 都碰 `analyst/` 下的文件，**N6 不许动 `_table_by_id`，N7 不许动 `_render`**。

## review 会挂在哪里

按会退回的概率排：

1. **N6 把 `_render` 合了。**三个不同的函数，合了就是错的。
2. **N5 的测试从 glossary 自己读词表。**恒真的假钉。
3. **N7 加了索引缓存。**`Corpus.assets` 可变，缓存会过期,而这一批没有 `concat` 兜底。
4. **N7 的测试按 67 张表构造而不是 27 个名字。**重复测同一条路径。
5. **N8 把 `uv.lock` 的升级结果提交了。**这一项不动版本。
6. **N5 顺手改名。**这一项是描述性的,改名归 1.4，不在这一批。

## 这一批做完之后

M2 的出口判据（[near-term-plan.md](near-term-plan.md) 里那张表）：三个重复名字各只剩一处定义 —— 注意按本文档，**`_render` 是例外，它不是重复**；歧义裸名返回 `None`；`uv sync -U` 后线协议包仍在声明范围内；glossary 覆盖运维与实验词。

下一批是 **M3（N9 → N10 → N10a）**。N9/N10 **有严格顺序**：`skip_agent` 在 `src/` 里 75 处 / 8 个文件，其中 11 处在 `run_experiment.py` 里 —— 先做 N9 删掉那个 driver，N10 就少改 11 处。反过来做等于白改。**N10a**（rvgd ↔ `table_by_name` 歧义一致性测试）是 M2 遗留：热路径故意不合成一份，用测试钉住两份不漂移；与 N9/N10 无文件冲突，见 [near-term-plan.md](near-term-plan.md) M3。
