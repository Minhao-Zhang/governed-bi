# data/generated/

> **本文档描述的是 v1,已在 commit `2347ae3` 中删除。** 保留在原路径是因为它是仓库的入口之一,
> 目前正依据 [ADR 0005](../../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md) 与
> [ADR 0006](../../docs/adr/0006-execution-time-governance.md) 重写。在重写完成之前,
> 请把本文中所有具体的说法 —— 模块名、文件路径、工具名、实测数字 —— 都当作历史记录,
> 而不是对当前系统的描述。v1 的其余文档在 [`docs/v1/`](../../docs/v1/),
> 哪些实测结论经复核后仍然成立、哪些已作废,记在 [`lessons-from-v1.md`](../../docs/lessons-from-v1.md)。

_[English](README.md) · [简体中文](README.zh.md)_

机器生成的 corpus 输出的默认落地目录：目前是经过 profiling 的 Facts 层资产，日后会是 curator 生成的草稿。由 `governed_bi.corpus.write_corpus(...)` 写入。

这里是一个中转区(staging area)，不是权威数据源(source of truth)。它被 gitignore 排除，因为它可以从数据库重新构建出来(`profile_database` 是确定性的)。经人工审核并被人接受的、经过整理的(curated) corpus 存放在 `corpus/<db>/`（D15：`<db>` corpus 命名空间已更名为 `<schema>`；已决定，尚未落地）下，并提交到该目录(D9)。

对某个数据库完成 profiling 之后，典型的目录结构如下：

```
data/generated/beer_factory/
  tables/tbl_beer_factory_customers.yaml
  tables/tbl_beer_factory_transaction.yaml
  ...
```

可以随时重新生成：

```python
from governed_bi.gateway import SqliteConnector
from governed_bi.curator.profile import profile_database
from governed_bi.corpus import write_corpus

conn = SqliteConnector("data/bird/beer_factory.sqlite")
write_corpus("data/generated", "beer_factory", profile_database(conn, "beer_factory"))
```
