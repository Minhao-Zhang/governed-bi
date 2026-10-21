"""``docs/openapi.json`` against the bodies the server actually sends.

**Nothing generated the spec's response shapes and nothing checked them.** Every handler under
``api/`` is annotated ``-> dict[str, Any]``, so FastAPI infers no response model and
``app.openapi()`` emits *zero* response schemas — all of the spec's components are hand-written.
An ``app.openapi()`` diff, the obvious check, therefore compares the routes (which were exact)
and is blind to the part that drifted: on 2026-08-22 an audit found ``/capabilities`` missing
the whole of ``models`` and ``connection``, the pending row missing ``interrupt_id`` and
``task_id``, and two fields declared non-nullable that the server sends as null.
A generated diff passes on every one of them.

So this compares the spec against **real payloads**: the real app (``routes.make_app``) over
in-memory dependencies through a ``TestClient``, each body checked against the component its own
operation declares. Three checks, and the second is the one that catches a spec falling behind a
handler — every ``required`` property present; every property the server sends *declared*; every
value matching its declared type, ``anyOf`` alternatives and ``enum``.

**The readers are the engine's.** The pending queue is the production ``PendingClarifications``
over a fake LangGraph client, so its rows are shaped by the code that ships.

**No ``jsonschema``.** It is not declared in ``pyproject.toml`` (it arrives transitively under
the LangGraph stack, which is not the same thing) and this file must not add a dependency. The
subset the spec uses — ``$ref``, ``anyOf``, ``allOf``, ``type``, ``properties``, ``required``,
``items``, ``enum``, ``additionalProperties`` — is walked below, which is also what lets a failure name the
route, the JSON path, the component and which side is missing the field.

**What this does NOT cover, and none of it is incidental:**

* **Request bodies and query parameters.** Nothing here validates a request body.
* **Most error paths.** ``/readyz`` 503 is checked. Other 404 and 422 responses are not.
* **Query-parameter variants.** Each route is called once, with its default window.
* **The streamed transport.** A turn is served over ``/runs/stream``, which this spec does not
  describe at all (ADR 0007 §7), as are the platform's own ``/threads`` and ``/runs``.
* **Declared-but-unsent optional fields.** A property the server never emits here is tolerated,
  not validated. ``connection.host``/``port``/``database`` and the pending row's
  ``interrupt_id``/``task_id`` are exercised on purpose for that reason; others are not.
* **Descriptions.** Prose is unchecked, and one of the six defects this file was written for was
  a description naming a setting that does not exist.
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


# ── the structural comparison ────────────────────────────────────────────────


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
    """The JSON type name of ``value``, so a message can say what arrived.

    ``bool`` is resolved before ``int`` deliberately: ``True`` is an ``int`` in Python and is not
    an integer on the wire, so a spec declaring ``integer`` for a flag is wrong and is told so.
    """
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
    """``allOf`` merged into one object schema.

    Needed because one component extends another with ``allOf``, and the
    alternative to merging is copying the base properties into a second component,
    where they would drift. The merge is shallow and one level deep, which is all the spec uses:
    ``properties`` union and ``required`` union, with the branch's own keys taking precedence.

    An extending schema must NOT re-declare a property its base declares differently. Nothing here
    detects that, and the failure would be silent, so it is a rule about writing the spec rather
    than one this walker enforces.
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
    """The response schema ``docs/openapi.json`` declares for one operation."""
    operation = (spec["paths"].get(path) or {}).get(method.lower())
    assert operation is not None, f"{method} {path} is not in docs/openapi.json"
    response = (operation.get("responses") or {}).get(status)
    assert response is not None, f"{method} {path} declares no {status} response"
    schema = ((response.get("content") or {}).get("application/json") or {}).get("schema")
    assert schema is not None, f"{method} {path} declares a {status} with no JSON schema"
    return schema


# ── the fixtures ─────────────────────────────────────────────────────────────


class _FakeThreads:
    """Enough of ``ThreadsClient.search`` for the production readers, honouring their projections.

    ``ids``, ``status``, ``select`` and ``extract`` are applied rather than accepted and ignored.
    ``extract`` in particular is what puts a channel under ``extracted``, and a fake returning
    ``values`` whole would hide a reader that stopped projecting.
    """

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
    """Four assets over one schema, with a real index and structure. As ``test_http_contract``'s."""
    from governed_bi.corpus.schema import (
        AssetType,
        Binding,
        ColumnAsset,
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
    """The app, the spec and the turn id the tests address. Built once; the graph run is the cost.

    One thread carries an unanswered ``ask_user`` question, which contributes
    ``interrupt_id`` and ``task_id``.

    A **second, degenerate thread** exists to make the server send ``null`` where it can: it has
    no ``thread_id`` and an interrupt with no ``question``, the two values the handler passes
    through with ``.get()``. Mutation-checked on 2026-08-22 — reverting ``question`` to
    non-nullable passed until this thread existed, because a nullability fix is unfalsifiable
    against a fixture that never produces the null.
    """
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.api.thread_turns import PendingClarifications
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
    }
    degenerate = {
        "updated_at": "2026-08-22T09:00:00Z",
        "status": "interrupted",
        "metadata": {"graph_id": "serve"},
        "interrupts": {"task-spec-2": [{"id": None, "value": {"kind": "clarification"}}]},
        "values": {},
    }

    class _Client:
        threads = _FakeThreads([thread, degenerate])

    queue = PendingClarifications(client_factory=lambda: _Client())
    app = make_app(session, queue)

    return {
        "spec": json.loads(SPEC_PATH.read_text(encoding="utf-8")),
        "client": TestClient(app),
    }


#: Every operation, as (method, spec path, url). Coverage of the spec's full operation set is
#: asserted by :func:`test_every_operation_in_the_spec_is_exercised_here`.
CASES: list[tuple[str, str, str]] = [
    ("GET", p, p)
    for p in ("/livez", "/readyz", "/capabilities", "/clarifications/pending")
]


@pytest.mark.parametrize(("method", "path", "url"), CASES, ids=[c[2] for c in CASES])
def test_the_spec_declares_every_field_the_server_sends(
    wire: dict[str, Any], method: str, path: str, url: str
) -> None:
    """One route's real 200 body against the component its own operation declares.

    The *undeclared property* direction is the one that matters most. A hand-written spec drifts
    by the handler growing a field, and every other check passes while it does — the required
    properties are all still there and all still the right type. That is how ``/capabilities``
    lost ``models`` and ``connection``, the newest and largest addition to the route.
    """
    response = wire["client"].request(method, url)
    assert response.status_code == 200, f"{method} {url} -> {response.status_code}: {response.text}"
    schema = _declared(wire["spec"], method, path, "200")
    problems = _violations(response.json(), schema, wire["spec"], f"{method} {url} $")
    assert not problems, "\n".join(problems)


def test_the_pending_queue_carries_interrupt_ids_and_a_null_question(wire: dict[str, Any]) -> None:
    """interrupt_id and task_id are on the wire, and a payload can omit question.

    A nullability declaration is unfalsifiable unless some row actually carries the null.
    The degenerate thread in the fixture is that row.
    """
    rows = wire["client"].get("/clarifications/pending").json()["rows"]
    assert rows, "the fixture queued no interrupt"
    assert all(row["source"] == "interrupt" for row in rows)
    full = next(row for row in rows if row.get("question"))
    assert full["interrupt_id"] and full["task_id"]
    assert any(row["question"] is None for row in rows)
    assert any(row["thread_id"] is None for row in rows)


def test_the_spec_declares_the_not_ready_body(wire: dict[str, Any]) -> None:
    """``/readyz``'s 503, driven by a session that cannot be built."""
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
    """The check's own coverage, so a route added to the spec is not silently ungated.

    Without this, the way to make every assertion above pass is to add an operation to
    ``docs/openapi.json`` and no case to :data:`CASES` — the failure mode this file exists to
    close, one level up.
    """
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    declared = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    }
    exercised = {(method, path) for method, path, _ in CASES}
    assert not declared - exercised, (
        f"docs/openapi.json declares {sorted(declared - exercised)} and nothing here validates a "
        "response for it -- add a case to CASES, or say in the docstring why it is unreachable"
    )
