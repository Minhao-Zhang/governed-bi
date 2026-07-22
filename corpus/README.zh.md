# corpus/

_[English](README.md) · [简体中文](README.zh.md)_

**语义层（semantic layer）**是护城河。基于 Git 追踪的纯 Markdown + YAML 类型化
资产，由 curator 编写 / 人工审核（D9）。**Git 是唯一的事实来源。**其他所有存储
（内存图、向量、BM25、Postgres）都是 `_generated/` 目录下的派生、可重建投影，
绝不直接编写。

完整规范参见[`docs/asset-schemas.md`](../docs/asset-schemas.zh.md)。

## 目录结构

```
corpus/
  <schema>/
    tables/      tbl_<schema>_<name>.yaml      # columns inline
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    notes/       note_<name>.yaml            # governed annotations (D17)
    negatives/   neg_<schema>_<n>.yaml
  _generated/    # search index, embeddings, compiled graph (gitignored)
```

> **D15：**`<schema>` 层级是**schema**命名空间，而非数据库（一次运行所连接的
> 数据库是 connection-config 里的常量，不是语料建模的一个层级，可以容纳多个
> schema）。磁盘 YAML 与 load/write API 使用字段/参数名 `schema`（相对 `db`
> 的硬切断）。资产 ID 不变。

`beer_factory/` 是**完整参考示例**，基于真实的 BIRD `beer_factory` 数据库
（`data/bird/beer_factory.sqlite`）编写而成。它覆盖了每一种资产类型，并针对该
数据库进行校验（物理存在性）。可将其作为编写自有资产时的参考。

## 字段档位

每个资产都拆分为 **Facts**（目录事实，绝不推断）、**Inference**（curator 编写的语义层）与
**Audit**（说明原因，绝不注入 Analyst 上下文）三个层级。
此外还有仅供人工使用的 **Governance** 覆盖项。

## 校验

```bash
uv run python -m governed_bi.corpus.cli corpus/beer_factory
```

校验全部通过（即 ID 命名规范与引用完整性均满足）是 curator 用来判断"完成得
足够好"的、可由机器检查的信号。
