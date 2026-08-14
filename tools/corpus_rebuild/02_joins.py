"""Harvest join edges from train gold SQL, and from the few foreign keys that exist.

The instance carries **7 foreign-key constraints in the whole database**, so introspection is
not a source of edges here; the gold queries are. Every edge a real question needed is an edge
some train query took.

Edges matter more than they look: ``connect`` is a Steiner walk over the declared edge set and
**declines** when the terminals sit in different components, and a decline means the gold table
is never licensed. 38 real tables in the corpus being replaced carry no edge at all.

``on`` is schema-qualified (``address.CBSA.CBSA = address.zip_data.zip_code``). The unqualified
form is what the last corpus revision was fixing, and ``join_id`` embeds ``on_digest``, so the
two spellings mint different ids for one relationship.

``cardinality`` is left unset. It is not derivable from a SELECT, and guessing it would be a
claim the source did not make; the agent fills it in phase 3.

    uv run python tools/corpus_rebuild/02_joins.py --out ../BIRD-corpus
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C  # noqa: E402

from governed_bi.corpus.identity import join_id, slug, table_id  # noqa: E402

_BARE = re.compile(r"\A[A-Za-z0-9_]+\Z")


def _q(part: str) -> str:
    """Quote an identifier only when it needs it.

    ``on_digest`` parses the clause, so ``airline.Air Carriers.Code`` is a syntax error and the
    whole schema fails. Quoting only the awkward ones keeps every ordinary edge byte-identical
    to the unquoted spelling — verified: ``address.CBSA.CBSA = address.zip_data.CBSA`` mints the
    same digest here as in the corpus this replaces.
    """
    return part if _BARE.match(part) else '"' + part.replace('"', '""') + '"'


def _clause(schema: str, left: str, lcol: str, right: str, rcol: str) -> str:
    return (
        f"{_q(schema)}.{_q(left)}.{_q(lcol)} = {_q(schema)}.{_q(right)}.{_q(rcol)}"
    )


def _table_scopes(tree: exp.Expression) -> dict[str, tuple[str, str]]:
    """``alias or name -> (schema, table)`` for every table named in the query."""
    scopes: dict[str, tuple[str, str]] = {}
    for node in tree.find_all(exp.Table):
        schema, name = node.text("db"), node.name
        if not schema:
            continue
        scopes[name] = (schema, name)
        if node.alias:
            scopes[node.alias] = (schema, name)
    return scopes


def _edges(sql: str) -> set[tuple[str, str, str, str, str]]:
    """``(schema, left, right, left_col, right_col)`` for each equality across two tables.

    Read off every ``=`` in the query, not only the ones inside an ON clause: BIRD gold mixes
    ``JOIN ... ON`` with comma joins filtered in the WHERE, and taking only the former loses
    edges that questions actually depend on.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:
        return set()
    scopes = _table_scopes(tree)
    found: set[tuple[str, str, str, str, str]] = set()
    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        lt, rt = scopes.get(left.table), scopes.get(right.table)
        if not lt or not rt or lt == rt:
            continue
        if lt[0] != rt[0]:
            continue  # cross-schema: no gold query has one, and an edge needs one namespace
        # Order the pair so a JOIN written either way round yields one edge.
        (ls, lname, lcol), (rs, rname, rcol) = (
            ((*lt, left.name), (*rt, right.name))
            if (lt[1], left.name) <= (rt[1], right.name)
            else ((*rt, right.name), (*lt, left.name))
        )
        found.add((ls, lname, rname, lcol, rcol))
    return found


def _from_foreign_keys(schemas: set[str]) -> set[tuple[str, str, str, str, str]]:
    """Declared foreign keys, **mapped through the rename map**.

    ``*_tables.json`` is upstream BIRD, so it names ``beer_factory.customers`` while the
    instance under test calls it something else. Emitting the original spelling produced 691
    join endpoints resolving to no asset -- edges that exist in the corpus, are counted as
    coverage, and can never license a table.
    """
    import json

    rename = C.rename_map()
    found: set[tuple[str, str, str, str, str]] = set()
    for path in sorted(C.DATASET.glob("data/*/**/*_tables.json")):
        for db in json.loads(path.read_text(encoding="utf-8")):
            name = db.get("db_id")
            if name not in schemas:
                continue
            tables = db.get("table_names_original") or []
            cols = db.get("column_names_original") or []
            for left, right in db.get("foreign_keys") or []:
                try:
                    lt, lcol = cols[left]
                    rt, rcol = cols[right]
                except (IndexError, TypeError, ValueError):
                    continue
                if lt < 0 or rt < 0 or lt == rt:
                    continue
                m = rename.get(name, {})
                pair = ((m.get(tables[lt], tables[lt]), m.get(lcol, lcol)),
                        (m.get(tables[rt], tables[rt]), m.get(rcol, rcol)))
                (a, acol), (b, bcol) = sorted(pair)
                found.add((name, a, b, acol, bcol))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.DEFAULT_CORPUS)
    ap.add_argument("--schemas", nargs="*", default=None)
    args = ap.parse_args(argv)

    wanted = set(args.schemas) if args.schemas else set(C.evaluated_schemas())
    edges: set[tuple[str, str, str, str, str]] = set()
    unparsed = 0
    for row in C.train_rows():
        if row.get("db_id") not in wanted:
            continue
        sql = row.get("sql_rename") or ""
        found = _edges(sql)
        # A query that joins and yields nothing is a parse or alias-resolution failure. Count
        # *that*, not "added no new edge" -- most queries repeat an edge already harvested, so
        # the naive counter reports a fleet of failures that are duplicates.
        if not found and " join " in sql.lower():
            unparsed += 1
        edges |= found
    from_sql = len(edges)
    edges |= _from_foreign_keys(wanted)

    per_schema: dict[str, int] = defaultdict(int)
    for schema, left, right, lcol, rcol in sorted(edges):
        lid, rid = table_id(schema, left), table_id(schema, right)
        on = _clause(schema, left, lcol, right, rcol)
        jid = join_id(schema, lid, rid, on)
        C.write_asset(
            args.out, schema, "joins", jid,
            {
                "asset_type": "join",
                "id": jid,
                "left_table": lid,
                "right_table": rid,
                "on": on,
                # The identifier rule reads the last component of `left_table`, which is the
                # *slug* -- `Air_Carriers_66c534`, not `Air Carriers`. A sentinel spelling the
                # physical name fails both V3 and the loader.
                "summary": f"{C.SENTINEL} {slug(left)} {slug(right)}",
                "body": "",
                "audit": C.provenance(
                    "gold", "scaffold-1", ["train_final.jsonl", "*_tables.json"],
                    f"equality between {left} and {right} in train gold SQL",
                ),
            },
        )
        per_schema[schema] += 1

    covered = len(per_schema)
    print(
        f"{len(edges)} edges into {args.out} across {covered} schemas "
        f"({from_sql} from gold SQL, {len(edges) - from_sql} added by declared foreign keys); "
        f"{unparsed} gold queries mentioned a join and yielded none."
    )
    thin = sorted(s for s in wanted if per_schema[s] == 0)
    if thin:
        print(f"  no edge at all for: {thin}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
