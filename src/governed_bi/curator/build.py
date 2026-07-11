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
  (``clarify_loop.py``) that a human answers, growing the layer over time.

This module builds layer 1 and writes it to a corpus root. The output goes
wherever the caller points it: ``data/generated/<db>/`` (rebuildable, gitignored)
by default, or the separate corpus repo (D13) via ``--out ../BIRD-corpus``.

CLI::

    python -m governed_bi.curator.build --db beer_factory \\
        --sqlite data/bird/beer_factory.sqlite --out ../BIRD-corpus
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from ..corpus.serialize import write_corpus
from .profile import profile_database

if TYPE_CHECKING:
    from ..gateway.connectors.base import Connector


def build_facts_corpus(connector: "Connector", db: str, root: Path | str) -> list[Path]:
    """Profile ``db`` through ``connector`` and write the facts-only layer.

    Layer 1 of the corpus: :func:`profile_database` emits Facts-only
    :class:`~governed_bi.corpus.schemas.TableAsset` s (no Inference tier), and
    :func:`~governed_bi.corpus.serialize.write_corpus` writes them under
    ``root/<db>/``. No LLM, no network beyond the database. Returns the written
    paths. Connector-agnostic; the CLI wires the SQLite one.
    """
    tables = profile_database(connector, db)
    return write_corpus(root, db, tables)


def main(argv: list[str] | None = None) -> int:
    """CLI: build the facts-only corpus layer from a SQLite database."""
    parser = argparse.ArgumentParser(
        prog="python -m governed_bi.curator.build",
        description="Generate the facts-only corpus layer (no AI) from a database catalog.",
    )
    parser.add_argument("--db", required=True, help="db_id / corpus namespace, e.g. beer_factory")
    parser.add_argument(
        "--sqlite", required=True, type=Path, help="path to the SQLite database file"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/generated"),
        help=(
            "corpus root to write into (writes <out>/<db>/...); "
            "default data/generated. Use ../BIRD-corpus for the benchmark corpus repo."
        ),
    )
    args = parser.parse_args(argv)

    from ..gateway.connectors.sqlite import SqliteConnector

    connector = SqliteConnector(args.sqlite)
    try:
        written = build_facts_corpus(connector, args.db, args.out)
    finally:
        connector.close()

    print(f"Wrote {len(written)} facts-only asset file(s) to {args.out / args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
