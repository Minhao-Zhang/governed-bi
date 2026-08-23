"""``docs/openapi.json`` against the bodies the server actually sends.

**Nothing generated the spec's response shapes and nothing checked them.** Every handler under
``api/`` is annotated ``-> dict[str, Any]``, so FastAPI infers no response model and
``app.openapi()`` emits *zero* response schemas — all of the spec's components are hand-written.
An ``app.openapi()`` diff, the obvious gate, therefore compares the routes (which were exact)
and is blind to the part that drifted: on 2026-08-22 an audit found ``/capabilities`` missing
the whole of ``models`` and ``connection``, the pending row missing ``interrupt_id`` and
``task_id``, two fields declared non-nullable that the server sends as null, and
``POST /turns/{id}/raised`` with no 200 schema at all while the handler returns
``{"ok": true, "row": {...}}``. A generated diff passes on every one of them.

So this compares the spec against **real payloads**: the real app (``routes.make_app``) over
in-memory dependencies through a ``TestClient``, each body checked against the component its own
operation declares. Three checks, and the second is the one that catches a spec falling behind a
handler — every ``required`` property present; every property the server sends *declared*; every
value matching its declared type, ``anyOf`` alternatives and ``enum``.

**The fixtures are the engine's, not hand-written JSON.** The turn envelope the audit surface
reads is produced by running the served graph and passing its answer through the production
writer (``graph_app.record_node``); the readers are the production ``ThreadTurnLog`` and
``PendingClarifications`` over a fake LangGraph client. So ``/audit/turns``, the trace and the
pending queue are shaped by the code that ships, not by a fake that agrees with the spec.

**No ``jsonschema``.** It is not declared in ``pyproject.toml`` (it arrives transitively under
the LangGraph stack, which is not the same thing) and this file must not add a dependency. The
subset the spec uses — ``$ref``, ``anyOf``, ``allOf``, ``type``, ``properties``, ``required``,
``items``, ``enum``, ``additionalProperties`` — is walked below, which is also what lets a failure name the
route, the JSON path, the component and which side is missing the field.

**What this does NOT cover, and none of it is incidental:**

* **Request bodies and query parameters.** Only ``POST /turns/{id}/raised`` declares a body, and
  nothing here validates it against what the handler accepts.
* **Most error paths.** The raised route's 404 / 409 / 422 are checked because the handler can be
  driven into all three from here. Every other operation's 404 and 422 are unchecked.
* **Query-parameter variants.** Each route is called once or twice, so a shape appearing only
  under some ``?focus=``/``?scope=`` combination is unreached; the bounded graphs have the most.
* **The streamed transport.** A turn is served over ``/runs/stream``, which this spec does not
  describe at all (ADR 0007 §7), as are the platform's own ``/threads`` and ``/runs``.
* **Declared-but-unsent optional fields.** A property the server never emits here is tolerated,
  not validated. ``connection.host``/``port``/``database`` and the pending row's
  ``interrupt_id``/``task_id`` are exercised on purpose for that reason; others are not.
* **Descriptions.** Prose is unchecked, and one of the six defects this file was written for was
  a description naming a knob that does not exist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"

#: The switch that mounts the steward's verbs. Off in production; on in this fixture, because an
#: operation the spec declares and the fixture cannot reach is an operation nothing validates.
ADMIN_SWITCH = "GOVERNED_BI_FEEDBACK_ADMIN"

#: A patch body the store accepts. The hash is the full 64 characters on purpose: a 16-character
#: prefix -- what every display shows -- is refused, and a fixture carrying one would exercise the
#: 422 instead of the 201.
_DRAFT: dict[str, Any] = {
    "intent": "edit_asset",
    "namespace": "beer",
    "asset_type": "table",
    "asset_id": "beer.brands",
    "field_path": "summary",
    "was": "one row per brand",
    "becomes": "one row per brand of beer",
    "base_corpus_content_hash": "d" * 64,
    "rationale": "the reference answer reads this table and retrieval did not license it",
}

#: A prompt the four-asset corpus below routes to `beer` on the lexical channel alone.
QUESTION = "how many root beer brands are there"
THREAD = "t-spec-conformance"


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

    Needed because ``ObservationDetailResponse`` is ``ObservationResponse`` plus two arrays, and the
    alternative to merging is copying twenty-eight property declarations into a second component --
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


def _envelope(session: Any) -> dict[str, Any]:
    """One turn envelope, **minted by the engine**: the served graph's answer through ``record_node``.

    Hand-writing it would make the audit surface's conformance a statement about the fixture.
    ``record_node`` is the only writer of a turn envelope in the system.
    """
    from langchain_core.messages import HumanMessage

    from governed_bi.api.graph_app import build_serve_graph, record_node
    from governed_bi.serve.graph import as_sync
    from governed_bi.serve.runtime import trust

    trust()
    try:
        out = as_sync(build_serve_graph(session)).invoke(
            {"messages": [HumanMessage(content=QUESTION)]},
            {"configurable": {"thread_id": THREAD}},
        )
    finally:
        trust()
    assert out["answer"]["outcome"] != "crashed", out["answer"]
    written = record_node()({"answer": out["answer"], "question": QUESTION})
    assert written.get("turns"), "record_node stored no envelope, so there is nothing to serve"
    return dict(written["turns"][0])


@pytest.fixture(scope="module")
def wire() -> dict[str, Any]:
    """The app, the spec and the turn id the tests address. Built once; the graph run is the cost.

    One thread carries the interrupt half of the pending queue — an unanswered ``ask_user``
    question, which contributes ``interrupt_id``/``task_id``. The observation half comes from the
    feedback store, which the filing test writes into: two stores, unioned by the route, so the
    fixture seeds one and the test seeds the other.

    A **second, degenerate thread** exists to make the server send ``null`` where it can: it has
    no ``thread_id`` and an interrupt with no ``question``, the two values the handler passes
    through with ``.get()``. Mutation-checked on 2026-08-22 — reverting ``question`` to
    non-nullable passed until this thread existed, because a nullability fix is unfalsifiable
    against a fixture that never produces the null.
    """
    from fastapi.testclient import TestClient

    from contracts import scratch_feedback_store
    from governed_bi.api.routes import make_app
    from governed_bi.api.thread_turns import PendingClarifications, ThreadTurnLog
    from governed_bi.datasource.postgres import PostgresConnector

    # A real connector, never connected: `endpoint` is pure DSN parsing, and it is the only way
    # `connection.host`/`port`/`database` reach the wire at all (SQLite carries none of them).
    session = _session(PostgresConnector("postgresql://u:p@warehouse.example:5432/facilities"))
    envelope = _envelope(session)
    turn_id = envelope["record"]["turn_id"]
    clarification_id = f"clar-{turn_id}-0123456789ab"  # `serve/tools.py`'s shape; joins back
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
            "turns": [envelope],
            "clarifications": [{**asked, "turn_id": turn_id, "answer": "the critic one"}],
        },
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

    # No `state_writer` and no "busy" client. Filing wrote graph state through one and 409'd on a
    # paused thread; ADR 0015 §2 replaced the channel with a table, so there is no interrupt to
    # consume and no 409 to declare -- and the reader whose turn is paused can file, which is what
    # `tests/api/test_an_observation_is_filed_on_a_turn.py` asserts.
    queue = PendingClarifications(client_factory=lambda: _Client())
    log = ThreadTurnLog(client_factory=lambda: _Client())
    store = scratch_feedback_store()

    # The steward's three verbs are mounted, because the spec declares them and an operation the
    # fixture cannot reach is an operation nothing validates. Set here rather than through
    # `monkeypatch`, which is function-scoped and this fixture is not; restored below so a later
    # module does not inherit an admin surface.
    had = os.environ.get(ADMIN_SWITCH)
    os.environ[ADMIN_SWITCH] = "1"
    try:
        app = make_app(session, log, queue, store)
    finally:
        if had is None:
            os.environ.pop(ADMIN_SWITCH, None)
        else:
            os.environ[ADMIN_SWITCH] = had

    return {
        "spec": json.loads(SPEC_PATH.read_text(encoding="utf-8")),
        "client": TestClient(app),
        "store": store,
        "turn_id": turn_id,
    }


#: Every operation these fixtures can drive, as (method, spec path, url). Coverage of the spec's
#: full operation set is asserted by :func:`test_every_operation_in_the_spec_is_exercised_here`.
#: Each entry is (spec path, url). The url defaults to the path when they are the same, which is
#: every route without a path parameter or a query string.
CASES: list[tuple[str, str, str]] = [
    ("GET", p, u)
    for p, u in (
        ("/livez", ""), ("/capabilities", ""),
        ("/corpus/assets", ""), ("/corpus/assets", "/corpus/assets?type=table"),
        ("/corpus/fields", ""), ("/corpus/fields", "/corpus/fields?type=table"),
        ("/corpus/rows", "/corpus/rows?type=table"),
        ("/corpus/rows", "/corpus/rows?type=column"),
        ("/schema/summary", ""), ("/schema/{table_id}", "/schema/beer.brands"),
        ("/columns/{column_id}/related", "/columns/beer.brands.name/related"),
        ("/columns/{column_id}/related", "/columns/nope/related"),
        ("/graph", ""), ("/graph", "/graph?schema=beer&focus=beer.brands&radius=1"),
        ("/knowledge-graph", ""), ("/audit/turns", ""), ("/audit/corpus", ""),
        ("/clarifications/pending", ""),
    )
    for u in (u or p,)
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


def test_the_spec_declares_the_trace_for_a_turn_that_exists_and_one_that_does_not(
    wire: dict[str, Any],
) -> None:
    """Both branches, because they are different shapes under one schema.

    A found turn carries fourteen keys including the whole record; a miss carries two. The spec
    requires only ``found`` and ``turn_id``, which is what lets one component describe both — so a
    test that only ever asked for a hit would not notice the miss growing a field.
    """
    schema = _declared(wire["spec"], "GET", "/audit/turns/{turn_id}/trace", "200")
    for turn_id, expected in ((wire["turn_id"], True), ("no-such-turn", False)):
        response = wire["client"].get(f"/audit/turns/{turn_id}/trace")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["found"] is expected, body
        problems = _violations(body, schema, wire["spec"], f"GET /audit/turns/{turn_id}/trace $")
        assert not problems, "\n".join(problems)


def test_the_spec_declares_the_envelope_the_filing_route_returns_and_not_the_row(
    wire: dict[str, Any],
) -> None:
    """The **201** is ``{"ok": true, "observation": {...}}``.

    Three changes from the version this replaces, all of them ADR 0015 §2's. The status is 201 and
    not 200, because the route creates a row in a store rather than appending to a channel, and a
    client should not have to read the body to learn which. The envelope's key is ``observation``
    rather than ``row``. And **there is no 409**: filing used to refuse on a paused thread because
    the write would consume the live ``ask_user`` interrupt, and nothing writes graph state now.

    The error codes ride along because they are the rest of the same operation's contract — and a
    422 here carries **two** different bodies, the handler's own ``HTTPError`` and FastAPI's
    ``HTTPValidationError`` for a body that is not a JSON object.
    """
    spec, client, turn_id = wire["spec"], wire["client"], wire["turn_id"]

    ok = client.post(f"/turns/{turn_id}/raised", json={"kind": "wrong_answer", "note": "off by one"})
    assert ok.status_code == 201, ok.text
    problems = _violations(
        ok.json(), _declared(spec, "POST", "/turns/{turn_id}/raised", "201"), spec,
        f"POST /turns/{turn_id}/raised $",
    )
    assert not problems, "\n".join(problems)
    assert "kind" not in ok.json(), (
        "the row reached the top level, so the envelope schema is the wrong one"
    )

    for status, url, body in (
        ("404", "/turns/no-such-turn/raised", {"kind": "from_refusal"}),
        ("422", f"/turns/{turn_id}/raised", {"kind": "not-a-kind"}),
        ("422", f"/turns/{turn_id}/raised", ["not", "an", "object"]),
    ):
        response = client.post(url, json=body)
        assert str(response.status_code) == status, f"{url} {body} -> {response.status_code}"
        problems = _violations(
            response.json(),
            _declared(spec, "POST", "/turns/{turn_id}/raised", status),
            spec,
            f"POST {url} ({status}) $",
        )
        assert not problems, "\n".join(problems)


def test_the_spec_declares_the_queue_in_both_of_its_shapes(wire: dict[str, Any]) -> None:
    """``rows`` by default and ``clusters`` under ``group=cluster``: two shapes, one operation.

    Both are exercised because neither is ``required`` in the component -- exactly one is present
    on any response -- so a test that only ever asked for the default would validate a schema half
    of which nothing reaches.
    """
    spec, client, turn_id = wire["spec"], wire["client"], wire["turn_id"]
    filed = client.post(
        f"/turns/{turn_id}/raised", json={"kind": "wrong_answer", "category": "wrong_value"}
    )
    assert filed.status_code == 201, filed.text
    schema = _declared(spec, "GET", "/observations", "200")

    for url, present, absent in (
        ("/observations?limit=5", "rows", "clusters"),
        ("/observations?group=cluster&state=open&limit=5", "clusters", "rows"),
    ):
        body = client.get(url).json()
        assert body[present], f"{url} returned no {present}, so its shape is unchecked"
        assert absent not in body, f"{url} returned both shapes"
        problems = _violations(body, schema, spec, f"GET {url} $")
        assert not problems, "\n".join(problems)

    problems = _violations(
        client.get("/observations?state=not-a-state").json(),
        _declared(spec, "GET", "/observations", "422"),
        spec,
        "GET /observations?state=not-a-state (422) $",
    )
    assert not problems, "\n".join(problems)


def test_the_spec_declares_one_observation_with_its_patches_and_history(
    wire: dict[str, Any],
) -> None:
    """The detail shape, with a patch attached so ``patches`` is not an empty array.

    An empty array satisfies any item schema, which is how ``PatchResponse`` would go unchecked
    while every assertion passed.
    """
    spec, client, turn_id = wire["spec"], wire["client"], wire["turn_id"]
    observation_id = client.post(f"/turns/{turn_id}/raised", json={"kind": "from_refusal"}).json()[
        "observation"
    ]["observation_id"]
    client.post("/patches", json={**_DRAFT, "observations": [observation_id]})

    body = client.get(f"/observations/{observation_id}").json()
    assert body["patches"], "no patch attached, so PatchResponse is unchecked here"
    problems = _violations(
        body,
        _declared(spec, "GET", "/observations/{observation_id}", "200"),
        spec,
        f"GET /observations/{observation_id} $",
    )
    assert not problems, "\n".join(problems)

    problems = _violations(
        client.get("/observations/obs-nope").json(),
        _declared(spec, "GET", "/observations/{observation_id}", "404"),
        spec,
        "GET /observations/obs-nope (404) $",
    )
    assert not problems, "\n".join(problems)


def test_the_spec_declares_the_amend_and_triage_envelopes(wire: dict[str, Any]) -> None:
    """Both return ``{"ok", "observation"}``, and both have an error the fixture can drive.

    ``PATCH`` then ``POST`` in that order on the same row on purpose: amending freezes at
    ``triaged``, so the 409 is only reachable after the triage, and driving it proves the freeze is
    real rather than declared.
    """
    spec, client, turn_id = wire["spec"], wire["client"], wire["turn_id"]
    observation_id = client.post(f"/turns/{turn_id}/raised", json={"kind": "wrong_answer"}).json()[
        "observation"
    ]["observation_id"]

    amended = client.patch(f"/observations/{observation_id}", json={"note": "it is about 400"})
    assert amended.status_code == 200, amended.text
    problems = _violations(
        amended.json(),
        _declared(spec, "PATCH", "/observations/{observation_id}", "200"),
        spec,
        "PATCH /observations/{id} $",
    )
    assert not problems, "\n".join(problems)

    triaged = client.post(f"/observations/{observation_id}/triage", json={"to": "triaged"})
    assert triaged.status_code == 200, triaged.text
    problems = _violations(
        triaged.json(),
        _declared(spec, "POST", "/observations/{observation_id}/triage", "200"),
        spec,
        "POST /observations/{id}/triage $",
    )
    assert not problems, "\n".join(problems)

    frozen = client.patch(f"/observations/{observation_id}", json={"note": "too late"})
    assert frozen.status_code == 409, frozen.text
    problems = _violations(
        frozen.json(),
        _declared(spec, "PATCH", "/observations/{observation_id}", "409"),
        spec,
        "PATCH /observations/{id} (409) $",
    )
    assert not problems, "\n".join(problems)

    undeclared = client.post(f"/observations/{observation_id}/triage", json={"to": "landed"})
    assert undeclared.status_code == 422, undeclared.text
    problems = _violations(
        undeclared.json(),
        _declared(spec, "POST", "/observations/{observation_id}/triage", "422"),
        spec,
        "POST /observations/{id}/triage (422) $",
    )
    assert not problems, "\n".join(problems)


def test_the_spec_declares_the_three_patch_operations(wire: dict[str, Any]) -> None:
    """Draft, list, withdraw. The 201 and the two 200s, plus the 409 the transition table raises.

    None of these writes to the corpus and none can: a patch records what a change *would* be, and
    the write is a human's ``git commit`` in a repository this process cannot reach.
    """
    spec, client = wire["spec"], wire["client"]

    drafted = client.post("/patches", json=_DRAFT)
    assert drafted.status_code == 201, drafted.text
    problems = _violations(
        drafted.json(), _declared(spec, "POST", "/patches", "201"), spec, "POST /patches $"
    )
    assert not problems, "\n".join(problems)
    patch_id = drafted.json()["patch"]["patch_id"]

    listed = client.get("/patches?limit=5")
    assert listed.status_code == 200, listed.text
    assert listed.json()["patches"], "the list is empty, so PatchResponse is unchecked here"
    problems = _violations(
        listed.json(), _declared(spec, "GET", "/patches", "200"), spec, "GET /patches $"
    )
    assert not problems, "\n".join(problems)

    withdrawn = client.post(
        f"/patches/{patch_id}/withdraw", json={"reason": "the router, not the summary"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    problems = _violations(
        withdrawn.json(),
        _declared(spec, "POST", "/patches/{patch_id}/withdraw", "200"),
        spec,
        "POST /patches/{id}/withdraw $",
    )
    assert not problems, "\n".join(problems)

    again = client.post(f"/patches/{patch_id}/withdraw", json={"reason": "again"})
    assert again.status_code == 409, again.text
    problems = _violations(
        again.json(),
        _declared(spec, "POST", "/patches/{patch_id}/withdraw", "409"),
        spec,
        "POST /patches/{id}/withdraw (409) $",
    )
    assert not problems, "\n".join(problems)

    unknown = client.get("/patches?state=landed")
    assert unknown.status_code == 422, unknown.text
    problems = _violations(
        unknown.json(),
        _declared(spec, "GET", "/patches", "422"),
        spec,
        "GET /patches?state=landed (422) $",
    )
    assert not problems, "\n".join(problems)


def test_the_pending_queue_carries_both_populations_so_neither_shape_goes_unchecked(
    wire: dict[str, Any],
) -> None:
    """A guard on the fixture, not on the server.

    The pending row's two hardest properties are ``interrupt_id`` and ``task_id``: present on an
    interrupt row, **absent** on an observation row, and declared in neither ``PENDING_FIELDS`` nor
    — until 2026-08-22 — the spec. The conformance test above reaches that asymmetry only if both
    kinds of row are in the response, and a fixture that quietly stopped producing one would make
    it pass while covering nothing. Same for the two nullable fields: a nullability declaration is
    unfalsifiable unless some row actually carries the null.

    **The two populations now come from two stores**, which is why this seeds one directly: the
    interrupt half is thread state and the observation half is ``runs/feedback.sqlite``, and the
    route is the only place that holds both.
    """
    wire["client"].post(
        f"/turns/{wire['turn_id']}/raised", json={"kind": "wrong_answer", "note": "off by one"}
    )
    rows = wire["client"].get("/clarifications/pending").json()["rows"]
    sources = {row["source"] for row in rows}
    assert "interrupt" in sources and sources - {"interrupt"}, (
        f"the queue returned only {sources or 'nothing'}; the spec's absent-vs-null rule for "
        "`interrupt_id`/`task_id` is then untested"
    )
    interrupt = next(row for row in rows if row["source"] == "interrupt")
    note = next(row for row in rows if row["source"] != "interrupt")
    assert "task_id" in interrupt and "interrupt_id" in interrupt, interrupt
    assert "task_id" not in note and "interrupt_id" not in note, (
        "an observation row grew the resume fields; the spec declares them absent here, not null"
    )
    for field in ("question", "thread_id"):
        assert any(row[field] is None for row in rows), (
            f"no row carries a null {field!r}, so the spec's nullability for it is untested and "
            "reverting it would fail nothing"
        )


def test_every_operation_in_the_spec_is_exercised_here() -> None:
    """The gate's own coverage, so a route added to the spec is not silently ungated.

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
    exercised = {(method, path) for method, path, _ in CASES} | {
        ("GET", "/audit/turns/{turn_id}/trace"),
        ("POST", "/turns/{turn_id}/raised"),
        ("GET", "/observations"),
        ("GET", "/observations/{observation_id}"),
        ("PATCH", "/observations/{observation_id}"),
        ("POST", "/observations/{observation_id}/triage"),
        ("POST", "/patches"),
        ("GET", "/patches"),
        ("POST", "/patches/{patch_id}/withdraw"),
    }
    assert not declared - exercised, (
        f"docs/openapi.json declares {sorted(declared - exercised)} and nothing here validates a "
        "response for it -- add a case to CASES, or say in the docstring why it is unreachable"
    )
