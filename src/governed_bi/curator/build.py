"""Generate a corpus layer by layer (``docs/curator.md``).

The semantic layer grows in layers, and only the first needs no AI:

- **Layer 1 - Facts (this module).** Read straight from the database catalog and
  data by :func:`profile.profile_database`: column names, physical dtypes, a
  normalized logical type, nullability, uniqueness, sample values, row counts.
  Deterministic, no LLM. This is the ``facts-only`` corpus (D14) - the cold
  starting point before any curation.
- **Layer 2 - Inference.** The proposer + adversary (D10, ``loop.py``) add
  descriptions, roles, joins, reliability caveats on top.
- **Layer 3 - Clarifications.** Gaps the curator cannot fill become SME questions
  (``clarifications.py``) that a human answers, growing the layer over time.

This module builds layer 1 and writes it to a corpus root. The output goes
wherever the caller points it: ``data/generated/<schema>/`` (rebuildable, gitignored)
by default, or the separate corpus repo (D13) via ``--out ../BIRD-corpus``.

CLI::

    python -m governed_bi.curator.build --db beer_factory \\
        --sqlite data/bird/beer_factory.sqlite --out ../BIRD-corpus
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import load_settings
from ..corpus.serialize import write_corpus
from .profile import profile_database

if TYPE_CHECKING:
    from ..gateway.connectors.base import Connector


def build_facts_corpus(connector: "Connector", schema: str, root: Path | str) -> list[Path]:
    """Profile ``schema`` through ``connector`` and write the bare-minimum facts layer.

    Layer 1 of the corpus: :func:`profile_database` emits Facts-only
    :class:`~governed_bi.corpus.schemas.TableAsset` s (name/dtype/nullable/PK +
    samples, no full scans; no Inference tier), and
    :func:`~governed_bi.corpus.serialize.write_corpus` writes them under
    ``root/<schema>/``. No LLM, no network beyond the database. Returns the written
    paths. Connector-agnostic; the CLI wires the SQLite one.
    """
    tables = profile_database(connector, schema)
    return write_corpus(root, schema, tables)


def build_facts_all_schemas(
    datasource, root: Path | str, *, connector_factory=None
) -> dict[str, int]:
    """Profile EVERY schema (one db_id each) into ``root/<schema>/``.

    We assume full read access: connecting yields every schema and every table
    within it. Lists schemas once, then profiles each into its own subtree,
    reusing ``datasource`` with ``schema``/``db`` set per schema. Postgres/Redshift
    only (SQLite has no schema level). Returns ``{schema: n_asset_files_written}``.
    ``connector_factory`` is injectable for testing; it defaults to
    :func:`governed_bi.gateway.build_connector`.
    """
    if connector_factory is None:
        from ..gateway import build_connector

        connector_factory = build_connector

    lister = connector_factory(datasource)
    try:
        # list_schemas() is on the Connector ABC now, but only schema-bearing
        # engines have real schemas to iterate; SQLite reports a single logical
        # namespace, which is not what all-schemas mode is for.
        if datasource.kind.lower() not in ("postgres", "redshift"):
            raise ValueError(
                f"datasource kind={datasource.kind!r} has no schemas to iterate; "
                "all-schemas mode needs postgres/redshift"
            )
        schemas = lister.list_schemas()
    finally:
        lister.close()

    written: dict[str, int] = {}
    for schema in schemas:
        # Pin each schema (multi_schema=False): the span-all datasource above is only
        # for the single list_schemas() enumeration; profiling one schema needs a
        # connector pinned to it, or build_connector leaves schema=None -> "public"
        # and every schema is silently profiled as public.
        connector = connector_factory(
            replace(datasource, schema=schema, db=schema, multi_schema=False)
        )
        try:
            written[schema] = len(build_facts_corpus(connector, schema, root))
        finally:
            connector.close()
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI: build the facts-only corpus layer from a configured database.

    The data source comes from ``[datasource]`` in ``governed_bi.toml`` (and
    optional ``governed_bi.local.toml``); the flags below override individual
    fields for a one-off run without editing those files.
    """
    parser = argparse.ArgumentParser(
        prog="python -m governed_bi.curator.build",
        description="Generate the facts-only corpus layer (no AI) from a configured database.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="governed_bi.toml (default: auto-locate; local overlay still applies)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/generated"),
        help=(
            "corpus root to write into (writes <out>/<schema>/...); default data/generated. "
            "Use ../BIRD-corpus for the D13 benchmark corpus repo."
        ),
    )
    parser.add_argument(
        "--all-schemas",
        dest="all_schemas",
        action="store_true",
        help="profile every schema (one db_id each) into <out>/<schema>/; postgres/redshift only",
    )
    # Per-field overrides of [datasource]; omit to use the config file.
    parser.add_argument("--kind", choices=["sqlite", "postgres", "redshift"], help="datasource kind")
    parser.add_argument("--db", help="db_id / corpus namespace, e.g. beer_factory")
    parser.add_argument("--sqlite", type=Path, help="SQLite file (kind=sqlite)")
    parser.add_argument("--dsn", help="libpq DSN (kind=postgres/redshift; prefer --dsn-env)")
    parser.add_argument("--dsn-env", dest="dsn_env", help="env var holding the DSN")
    parser.add_argument("--schema", help="postgres/redshift schema")
    args = parser.parse_args(argv)

    datasource = load_settings(args.config).datasource
    overrides = {
        k: v
        for k, v in (
            ("kind", args.kind),
            ("db", args.db),
            ("sqlite_path", str(args.sqlite) if args.sqlite else None),
            ("dsn", args.dsn),
            ("dsn_env", args.dsn_env),
            ("schema", args.schema),
        )
        if v is not None
    }
    if overrides:
        datasource = replace(datasource, **overrides)

    if args.all_schemas:
        counts = build_facts_all_schemas(datasource, args.out)
        nonempty = {s: n for s, n in counts.items() if n}
        total = sum(counts.values())
        print(
            f"[{datasource.kind}] profiled {len(nonempty)} schema(s) "
            f"({len(counts)} seen) -> {total} facts-only asset file(s) under {args.out}"
        )
        return 0

    from ..gateway import build_connector

    connector = build_connector(datasource)
    try:
        written = build_facts_corpus(connector, datasource.db, args.out)
    finally:
        connector.close()

    print(
        f"[{datasource.kind}] profiled {datasource.db} -> wrote {len(written)} "
        f"facts-only asset file(s) to {args.out / datasource.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
