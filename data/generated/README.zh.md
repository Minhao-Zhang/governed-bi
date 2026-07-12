# data/generated/

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
