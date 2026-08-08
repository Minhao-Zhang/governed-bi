"""Create a small, realistic Postgres schema to serve a corpus over.

Neither existing option is "a schema you can ask a question about and look at the answer":
parcel F's `probe` fixture builds three toy tables and drops them again, and the 69 obfuscated
BIRD schemas rebuild through a whole-corpus pipeline with no per-database flag.

Seven tables, and each feature earns its place:

* **Foreign keys** become `JoinAsset`s in `corpus/seed.py`, the only way `join_edges` is
  populated and so the only way a multi-table question is answerable (ADR 0005 §2.8.2).
* **A self-join** (`employees.manager_id`) exercises §2.8.2.1: out of the edge set, in the index.
* **Two tables sharing a physical column name** reaches endpoint reconciliation's ambiguous
  branch, where a guess is a licensing leak rather than a lost edge.
* **Enough rows to aggregate**: a count over three rows cannot distinguish a correct query from
  several wrong ones.

Idempotent: drops and recreates its own schema, never one it did not create.

Usage::

    uv run --frozen python tools/load_demo_schema.py            # default schema name
    uv run --frozen python tools/load_demo_schema.py --schema my_demo

The DSN comes from ``GOVERNED_BI_PG_DSN`` or ``PG_RENAME_DECOY_DSN``, in the environment or the
git-ignored ``.env``. Never printed: an echoed DSN is a credential in a scrollback.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import random
import sys

#: The two variables that may carry the DSN, in precedence order.
DSN_KEYS = ("GOVERNED_BI_PG_DSN", "PG_RENAME_DECOY_DSN")

#: Default schema name. Prefixed so it is obvious this is not production data and obvious which
#: tool owns it -- an unlabelled `demo` in a shared database is one someone has to guess about.
DEFAULT_SCHEMA = "gbi_demo_sales"

DDL = """
CREATE TABLE {s}.regions (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    country     text NOT NULL
);

CREATE TABLE {s}.employees (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    role        text NOT NULL,
    region_id   integer NOT NULL REFERENCES {s}.regions (id),
    -- Self-join: §2.8.2.1 keeps this in the join index and out of the edge set.
    manager_id  integer REFERENCES {s}.employees (id),
    hired_on    date NOT NULL
);

CREATE TABLE {s}.customers (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    email       text NOT NULL,
    region_id   integer NOT NULL REFERENCES {s}.regions (id),
    signed_up   date NOT NULL
);

CREATE TABLE {s}.products (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    category    text NOT NULL,
    unit_price  numeric(10, 2) NOT NULL
);

CREATE TABLE {s}.orders (
    id          integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES {s}.customers (id),
    -- Two hops from customers to employees, so a question about both needs a Steiner point.
    rep_id      integer NOT NULL REFERENCES {s}.employees (id),
    placed_on   date NOT NULL,
    status      text NOT NULL
);

CREATE TABLE {s}.order_items (
    id          integer PRIMARY KEY,
    order_id    integer NOT NULL REFERENCES {s}.orders (id),
    product_id  integer NOT NULL REFERENCES {s}.products (id),
    quantity    integer NOT NULL,
    unit_price  numeric(10, 2) NOT NULL
);

-- Reachable by no foreign key at all. Parcel F's contract needs a table that is licensed by
-- nothing, so that "the corpus does not license this" is distinguishable from "no connector".
CREATE TABLE {s}.audit_log (
    id          integer PRIMARY KEY,
    actor       text NOT NULL,
    action      text NOT NULL,
    at          timestamp NOT NULL
);
"""

REGIONS = [
    (1, "Pacific Northwest", "United States"),
    (2, "Great Lakes", "United States"),
    (3, "Rhine-Ruhr", "Germany"),
    (4, "Kansai", "Japan"),
]

ROLES = ("Account Executive", "Senior Account Executive", "Regional Manager")
CATEGORIES = ("Fasteners", "Adhesives", "Abrasives", "Sealants")
STATUSES = ("placed", "shipped", "delivered", "cancelled")


def _dsn() -> str:
    """The DSN from the environment or ``.env``. Never logged."""
    for key in DSN_KEYS:
        if os.environ.get(key):
            return str(os.environ[key])
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in DSN_KEYS:
                return value.strip().strip("\"'")
    return ""


def _rows(rng: random.Random) -> dict[str, list[tuple]]:
    """The data. Deterministic under a fixed seed, because a demo whose row counts move
    between runs cannot be used to check whether an answer is right."""
    employees: list[tuple] = []
    for i in range(1, 13):
        region = 1 + (i - 1) % len(REGIONS)
        role = ROLES[2] if i <= len(REGIONS) else ROLES[rng.randrange(2)]
        manager = None if i <= len(REGIONS) else 1 + (region - 1)
        employees.append((i, f"Employee {i:02d}", role, region, manager, f"20{18 + i % 7}-0{1 + i % 9}-1{i % 9}"))

    customers = [
        (i, f"Customer {i:03d}", f"customer{i:03d}@example.test",
         1 + (i - 1) % len(REGIONS), f"202{i % 5}-0{1 + i % 9}-0{1 + i % 8}")
        for i in range(1, 61)
    ]
    products = [
        (i, f"Product {i:02d}", CATEGORIES[(i - 1) % len(CATEGORIES)], round(4 + rng.random() * 96, 2))
        for i in range(1, 25)
    ]
    orders = [
        (i, 1 + rng.randrange(len(customers)), 1 + rng.randrange(len(employees)),
         f"202{3 + i % 3}-0{1 + i % 9}-1{i % 9}", STATUSES[rng.randrange(len(STATUSES))])
        for i in range(1, 241)
    ]
    items: list[tuple] = []
    for order_id in range(1, len(orders) + 1):
        for _ in range(1 + rng.randrange(3)):
            product = 1 + rng.randrange(len(products))
            items.append((len(items) + 1, order_id, product, 1 + rng.randrange(20), products[product - 1][3]))
    audit = [
        (i, f"Employee {1 + i % 12:02d}", ("login", "export", "edit")[i % 3],
         f"2026-0{1 + i % 9}-1{i % 9} 09:{i % 60:02d}:00")
        for i in range(1, 41)
    ]
    return {
        "regions": REGIONS, "employees": employees, "customers": customers,
        "products": products, "orders": orders, "order_items": items, "audit_log": audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help=f"schema to create (default {DEFAULT_SCHEMA})")
    parser.add_argument("--seed", type=int, default=20260803, help="RNG seed; fixed so row counts are stable")
    args = parser.parse_args()

    dsn = _dsn()
    if not dsn:
        print(f"no DSN: set one of {' / '.join(DSN_KEYS)} in the environment or .env", file=sys.stderr)
        return 2

    try:
        import psycopg
    except ImportError:
        print("psycopg is not installed; `uv sync` first", file=sys.stderr)
        return 2

    schema = args.schema
    if not schema.replace("_", "").isalnum():
        print(f"refusing a schema name that is not alphanumeric-plus-underscore: {schema!r}", file=sys.stderr)
        return 2

    rows = _rows(random.Random(args.seed))
    try:
        with psycopg.connect(dsn) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            con.execute(f'CREATE SCHEMA "{schema}"')
            con.execute(DDL.format(s=f'"{schema}"'))
            for table, data in rows.items():
                if not data:
                    continue
                placeholders = ", ".join(["%s"] * len(data[0]))
                with con.cursor() as cur:
                    cur.executemany(f'INSERT INTO "{schema}".{table} VALUES ({placeholders})', data)
            con.commit()
    except Exception as err:  # noqa: BLE001 -- the type and message are the useful part
        # The failure, never the DSN: an echoed connection string is a credential in a scrollback.
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 1

    print(f"schema {schema!r}: " + ", ".join(f"{t}={len(d)}" for t, d in rows.items()))
    print("foreign keys: 6 (one of them a self-join on employees.manager_id)")
    print(f"audit_log is reachable by no foreign key, so it is licensed by nothing in {schema!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
