# data/

_[English](README.md) · [简体中文](README.zh.md)_

一个小型的真实 BIRD SQLite 数据库，随附于仓库中，供开发和测试使用。

## 目录内容

`bird/beer_factory.sqlite`：来自 BIRD 基准测试的 `beer_factory` 数据库
（0.95 MB，7 张表）。**未经修改**收录，基于 CC BY-SA 4.0 许可；归属信息和许可
条款见 [`bird/NOTICE`](bird/NOTICE)。它**不**受本仓库 MIT 许可证的约束。

刻意排除的内容：BIRD 的 `database_description/` CSV 文件（人工撰写的列
说明）以及其余所有 BIRD 数据库。这些说明是刻意省略的，因为推断字段含义正是
curator 的工作。

这是**未混淆（base）**版本的数据库，使用真实的表名 / 列名，这正是构建和验证
引擎（目录内省、Facts 层画像、物理存在性检查、真实查询执行）所需要的。它并不
考验 curator 的核心工作（为晦涩命名推断含义），因此护城河评估后续仍需要来自
BIRD-Obfuscation 的混淆版 `rename_decoy` 变体及其 manifest。

## 后续新增数据库

保持约 5 MB 的软上限：低于该值，直接提交 `.sqlite` 文件；超过该值，使用
Git LFS 或抓取脚本。为每个新文件在 `NOTICE` 中添加归属信息，并只提交实际会
用到的数据库（而非完整的 BIRD 开发集）。

## 生成的 corpus 输出

对数据库进行画像（以及后续运行 curator）会按照约定将 corpus YAML 写入
[`generated/`](generated/) 目录，例如
`data/generated/beer_factory/tables/*.yaml`。该目录是可重建的暂存区，已被
gitignore；经过整理、人工审核的 corpus 位于 `corpus/<db>/`。参见
[`generated/README.md`](generated/README.zh.md)。

## 使用方式

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/beer_factory.sqlite")   # opens read-only
facts = profile_database(conn, db="beer_factory")          # Facts-tier table assets
gw = Gateway(conn)
rows = gw.execute("SELECT COUNT(*) FROM customers", Identity(user="dev", all_access=True))
```

connector、gateway 和 profiler 都有各自的单元测试，会构建自己的临时 SQLite
（`tests/test_connector.py`）；当 `beer_factory.sqlite` 存在时，这些测试还会
针对它运行一次集成检查。
