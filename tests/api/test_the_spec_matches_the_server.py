"""Real payloads against the hand-written ``docs/openapi.json`` components.

Handlers return ``dict[str, Any]``, so ``app.openapi()`` emits no response schemas and a generated
diff cannot see a drifted component. No ``jsonschema`` dependency: the subset the spec uses is
walked here. Request bodies, most errors, and ``/runs/stream`` are out of scope (ADR 0007).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"

THREAD = "t-spec-conformance"
#: A hex turn id, the shape ``serve/tools.py`` mints, so the clarification id joins back.
TURN_ID = "a1b2c3d4e5f60718"


def _resolve(schema: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Follow ``$ref`` until the schema is inline. Local refs only, which is all this spec has."""
    for _ in range(50):
        if not (isinstance(schema, dict) and "$ref" in schema):
            break
        pointer = schema["$ref"]
        assert pointer.startswith("#/"), f"only local refs are supported, not {pointer!r}"
        node: Any = spec
        for part in pointer[2:].split("/"):
            node = node[part]
        schema = node
    return schema if isinstance(schema, dict) else {}


def _kind(value: Any) -> str:
    """``bool`` before ``int``: ``True`` is an ``int`` in Python and is not an integer on the wire."""
    for name, kinds in (("null", type(None)), ("boolean", bool), ("integer", int),
                        ("number", float), ("string", str), ("object", dict), ("array", list)):
        if isinstance(value, kinds):
            return name
    return type(value).__name__


def _matches(value: Any, name: str) -> bool:
    """Whether ``value`` satisfies the JSON Schema type ``name``. Unknown names constrain nothing."""
    if name == "number":  # an integer is a valid `number`; the reverse is not true
        return _kind(value) in {"integer", "number"}
    known = {"null", "boolean", "integer", "number", "string", "object", "array"}
    return _kind(value) == name if name in known else True


def _flattened(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """``allOf`` is merged so an extension does not copy properties that would drift.

    Shallow, one level: that is all the spec uses. A re-declared property that disagrees with its base is not detected.
    """
    if "allOf" not in schema:
        return schema
    properties: dict[str, Any] = {}
    required: list[str] = []
    for branch in schema["allOf"]:
        resolved = _resolve(branch, spec)
        properties.update(resolved.get("properties") or {})
        required += list(resolved.get("required") or ())
    merged = {k: v for k, v in schema.items() if k != "allOf"}
    merged["properties"] = {**properties, **(schema.get("properties") or {})}
    merged["required"] = sorted({*required, *(schema.get("required") or ())})
    merged.setdefault("type", "object")
    return merged


def _violations(value: Any, schema: Any, spec: dict[str, Any], at: str) -> list[str]:
    """Every way ``value`` disagrees with ``schema``, each naming ``at`` and which side is wrong."""
    schema = _flattened(_resolve(schema, spec), spec)
    title = schema.get("title") or "(inline)"
    out: list[str] = []

    if "anyOf" in schema:
        branches = [_violations(value, b, spec, at) for b in schema["anyOf"]]
        if any(not b for b in branches):
            return []
        alts = " | ".join(
            _resolve(b, spec).get("title") or str(_resolve(b, spec).get("type") or "?")
            for b in schema["anyOf"]
        )
        return [
            f"{at}: the server sent {_kind(value)} and no declared alternative accepts it "
            f"(spec declares {alts} in {title})"
        ]

    declared = schema.get("type")
    names = [declared] if isinstance(declared, str) else list(declared or ())
    if names and not any(_matches(value, n) for n in names):
        return [
            f"{at}: the server sent {_kind(value)}, the spec declares {'/'.join(names)} "
            f"(in {title}) -- the spec and the handler disagree on the type"
        ]

    if "enum" in schema and value not in schema["enum"]:
        out.append(
            f"{at}: the server sent {value!r}, not one of the spec's {schema['enum']!r} (in {title})"
        )

    properties = schema.get("properties")
    if isinstance(value, dict) and (properties is not None or "object" in names):
        properties = properties or {}
        for name in schema.get("required") or ():
            if name not in value:
                out.append(
                    f"{at}.{name}: the spec declares it REQUIRED in {title} and the server did "
                    "not send it -- the spec is ahead of the handler"
                )
        if properties and schema.get("additionalProperties") is not True:
            out += [
                f"{at}.{name}: the SERVER sends it and {title} does not declare it "
                "-- the spec is behind the handler"
                for name in value
                if name not in properties
            ]
        for name, sub in properties.items():
            if name in value:
                out += _violations(value[name], sub, spec, f"{at}.{name}")

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += _violations(item, schema["items"], spec, f"{at}[{i}]")
    return out


def _declared(spec: dict[str, Any], method: str, path: str, status: str) -> Any:
    operation = (spec["paths"].get(path) or {}).get(method.lower())
    assert operation is not None, f"{method} {path} is not in docs/openapi.json"
    response = (operation.get("responses") or {}).get(status)
    assert response is not None, f"{method} {path} declares no {status} response"
    schema = ((response.get("content") or {}).get("application/json") or {}).get("schema")
    assert schema is not None, f"{method} {path} declares a {status} with no JSON schema"
    return schema


class _FakeThreads:
    """``select`` and ``extract`` are applied. Whole ``values`` would hide a reader that stopped projecting."""

    def __init__(self, threads: list[dict[str, Any]]) -> None:
        self._threads = threads

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        ids, status = kw.get("ids"), kw.get("status")
        select, extract = kw.get("select"), kw.get("extract")
        offset, limit = kw.get("offset", 0), kw.get("limit", 10)
        rows = list(self._threads)
        if ids:
            rows = [t for t in rows if t.get("thread_id") in set(ids)]
        if status is not None:
            rows = [t for t in rows if t.get("status") == status]
        if kw.get("sort_by") == "updated_at":
            rows.sort(key=lambda t: t["updated_at"], reverse=(kw.get("sort_order") != "asc"))
        out = []
        for thread in rows[offset : offset + limit]:
            projected = {k: v for k, v in thread.items() if select is None or k in select}
            if extract:
                current: dict[str, Any] = {}
                for alias, path in extract.items():
                    node: Any = thread
                    for part in str(path).split("."):
                        node = node.get(part) if isinstance(node, dict) else None
                    current[alias] = node
                projected["extracted"] = current
            out.append(projected)
        return out


def _session(connector: Any) -> Any:
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        ColumnAsset,
        FewShotAsset,
        SchemaAsset,
        TableAsset,
        TermAsset,
    )
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import from_assets

    session = from_assets(
        [
            SchemaAsset(id="beer", name="beer", summary="beer brands and sales"),
            TableAsset(id="beer.brands", schema="beer", physical_name="brands",
                       summary="Brands of root beer.", columns=("beer.brands.name",)),
            ColumnAsset(id="beer.brands.name", schema="beer", parent_table="beer.brands",
                        physical_name="name", summary="Brand name."),
            TermAsset(id="term.root_beer", name="root beer", summary="root beer, sarsaparilla",
                      binding=Binding(target_type=AssetType.column, target_id="beer.brands.name")),
            FewShotAsset(
                id="fs_beer_0000",
                schema="beer",
                sql="SELECT count(*) FROM beer.brands",
                summary="How many root beer brands are there?",
            ),
        ],
        connector=connector,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="beer",
        corpus_content_hash_="corpus-under-test",
        agent_model=None,
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return session


@pytest.fixture(scope="module")
def wire() -> dict[str, Any]:
    """Built once. A second thread omits ``question``.

    Nullability is unfalsifiable against a fixture that never sends null.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.api.thread_turns import ThreadReader
    from governed_bi.datasource.postgres import PostgresConnector

    # A real connector, never connected: `endpoint` is pure DSN parsing, and it is the only way
    # `connection.host`/`port`/`database` reach the wire at all (SQLite carries none of them).
    session = _session(PostgresConnector("postgresql://u:p@warehouse.example:5432/facilities"))
    clarification_id = f"clar-{TURN_ID}-0123456789ab"  # `serve/tools.py`'s shape; joins back
    asked = {
        "kind": "clarification", "clarification_id": clarification_id,
        "question": "which rating did you mean?", "why": "two columns match",
        "basis": "data_definition",
    }
    thread = {
        "thread_id": THREAD,
        "updated_at": "2026-08-22T10:00:00Z",
        "status": "interrupted",
        "metadata": {"graph_id": "serve"},
        "interrupts": {"task-spec-1": [{"id": "int-spec-1", "value": asked}]},
        "values": {
            "question": "how many brands use cane sugar?",
            "turns": [
                {"asked_at": "2026-08-22T09:59:00Z", "question": "how many brands?",
                 "outcome": "answered", "record": {"turn_id": TURN_ID}},
            ],
        },
    }
    # Nothing asked yet: `ending`, `question` and `asked_at` are null on the wire.
    empty = {"thread_id": "t-spec-empty", "updated_at": "2026-08-22T08:00:00Z", "status": "idle",
             "values": {"turns": [{"asked_at": None, "question": None, "outcome": None}]}}
    unstarted = {"thread_id": "t-spec-new", "updated_at": "2026-08-22T07:00:00Z", "status": "idle"}
    degenerate = {
        "updated_at": "2026-08-22T09:00:00Z",
        "status": "interrupted",
        "metadata": {"graph_id": "serve"},
        "interrupts": {"task-spec-2": [{"id": None, "value": {"kind": "clarification"}}]},
        "values": {},
    }

    class _Client:
        threads = _FakeThreads([thread, degenerate, empty, unstarted])

    queue = ThreadReader(client_factory=lambda: _Client())
    app = make_app(session, queue)

    return {
        "spec": json.loads(SPEC_PATH.read_text(encoding="utf-8")),
        "client": TestClient(app),
    }


#: Every operation, as (method, spec path, url). Coverage of the spec's full operation set is
#: asserted by :func:`test_every_operation_in_the_spec_is_exercised_here`.
CASES: list[tuple[str, str, str]] = [
    ("GET", p, p)
    for p in (
        "/livez", "/readyz", "/capabilities", "/clarifications/pending", "/conversations",
        "/examples", "/layer",
    )
]

#: Operations whose body is not JSON, each checked by its own test below.
NOT_JSON: set[tuple[str, str]] = {("GET", "/layer/{schema}")}


@pytest.mark.parametrize(("method", "path", "url"), CASES, ids=[c[2] for c in CASES])
def test_the_spec_declares_every_field_the_server_sends(
    wire: dict[str, Any], method: str, path: str, url: str
) -> None:
    """An undeclared property still has every required field present and correctly typed."""
    response = wire["client"].request(method, url)
    assert response.status_code == 200, f"{method} {url} -> {response.status_code}: {response.text}"
    schema = _declared(wire["spec"], method, path, "200")
    problems = _violations(response.json(), schema, wire["spec"], f"{method} {url} $")
    assert not problems, "\n".join(problems)


def test_the_pending_queue_carries_interrupt_ids_and_a_null_question(wire: dict[str, Any]) -> None:
    """A nullability declaration is unfalsifiable unless some row carries the null."""
    rows = wire["client"].get("/clarifications/pending").json()["rows"]
    assert rows, "the fixture queued no interrupt"
    assert all(row["source"] == "interrupt" for row in rows)
    full = next(row for row in rows if row.get("question"))
    assert full["interrupt_id"] and full["task_id"]
    assert any(row["question"] is None for row in rows)
    assert any(row["thread_id"] is None for row in rows)


def test_the_spec_declares_the_not_ready_body(wire: dict[str, Any]) -> None:
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import _build_app

    def missing_dsn() -> Any:
        raise RuntimeError("no database: set GOVERNED_BI_PG_DSN")

    response = TestClient(_build_app(missing_dsn, object())).get("/readyz")
    assert response.status_code == 503
    schema = _declared(wire["spec"], "GET", "/readyz", "503")
    problems = _violations(response.json(), schema, wire["spec"], "GET /readyz $")
    assert not problems, "\n".join(problems)


def test_every_operation_in_the_spec_is_exercised_here() -> None:
    """Adding an operation to the spec with no case here would leave that route unchecked."""
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    declared = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    }
    exercised = {(method, path) for method, path, _ in CASES} | NOT_JSON
    assert not declared - exercised, (
        f"docs/openapi.json declares {sorted(declared - exercised)} and nothing here validates a "
        "response for it -- add a case to CASES, or say in the docstring why it is unreachable"
    )


def test_the_layer_serves_a_listed_model_byte_for_byte_and_nothing_else(tmp_path) -> None:
    """The viewer opens the file as it is on disk; a name outside the listing is a 404, not a path."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.corpus.schema import SchemaAsset, TableAsset
    from governed_bi.corpus.store import write

    path = write(
        tmp_path,
        "beer",
        [
            SchemaAsset(id="beer", name="beer", summary="beer: brands of root beer and their sales"),
            TableAsset(id="beer.brands", schema="beer", physical_name="brands", summary="brands: one row per brand"),
        ],
    )
    client = TestClient(make_app(SimpleNamespace(corpus_root=tmp_path), object()))
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    listing = client.get("/layer")
    assert listing.json() == {"schemas": ["beer"]}
    assert not _violations(listing.json(), _declared(spec, "GET", "/layer", "200"), spec, "GET /layer $")

    served = client.get("/layer/beer")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/yaml")
    assert served.content == path.read_bytes()

    for name in ("absent", "..", "beer.yaml"):
        assert client.get(f"/layer/{name}").status_code == 404
