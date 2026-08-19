"""ADR 0012 §8.5: the HTTP corpus surface withholds what the served graph withholds.

**The gap this closes.** §8 opened "the seam is now enforced end to end" while every browse
route read ``session.assets_by_id`` directly and ``api/routes.py`` had no reference to the
access policy at all. A deployment that set ``GOVERNED_BI_ACCESS_POLICY`` to deny
``sales.customers.email`` withheld the column from the model's prompt and its tools, and served
it — name, type and ``sample_values`` — from ``GET /corpus/rows?type=column`` and
``GET /schema/{table_id}``. Two answers to "what may this principal see", one of them over HTTP.

**The method is a sweep, not a list of assertions.** Every corpus-projecting route is called
under a restrictive grant and its whole JSON body is searched, recursively, for any spelling of
the withheld table and column. A per-route assertion would only ever cover the routes whoever
wrote it thought of; the surface that produced this finding is exactly the one nobody thought
of. Each route is also called under the **open** grant and required to contain the identifier,
so a route that returns an empty body — or that a refactor stops mounting — cannot pass by
saying nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.corpus.schema import (
    AssetType,
    Binding,
    ColumnAsset,
    JoinAsset,
    MetricAsset,
    SchemaAsset,
    TableAsset,
    TermAsset,
)
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.ports import OPEN_GRANT, Grant, Reach

#: Denies one table outright and one column of an authorized table. Both halves of ADR 0012
#: §3/§4, because they are withheld by different branches of ``withheld_by_grant``.
RESTRICTIVE = Grant(
    reach=Reach.listed,
    tables=frozenset({"sales.customers"}),
    denied_columns=frozenset({"sales.customers.email"}),
)

#: What must not appear anywhere in a response body under :data:`RESTRICTIVE`. Every spelling:
#: the asset id, the physical name, and the prose that only that asset carries.
FORBIDDEN: tuple[str, ...] = (
    "sales.audit_log",
    "audit_log",
    "sales.customers.email",
    "email",
    "actor's name",
    "Contact address",
)

#: Every route that projects corpus assets. ``/audit/corpus`` is **not** here: it publishes
#: counts, hashes and problem strings and names no asset, so the open-grant control below would
#: fail on it for the right reason. Its own case is
#: :func:`test_the_one_unfiltered_field_is_declared`.
CORPUS_ROUTES: tuple[str, ...] = (
    "/corpus/assets",
    "/corpus/assets?type=column",
    "/corpus/rows?type=column",
    "/corpus/rows?type=table",
    "/corpus/rows?type=join",
    "/corpus/rows?type=metric",
    "/corpus/rows?type=term",
    "/schema/summary",
    "/schema/sales.audit_log",
    "/schema/sales.customers",
    "/columns/sales.customers.email/related",
    "/columns/sales.audit_log.note/related",
    "/graph",
    "/knowledge-graph",
)


def _assets() -> list[Any]:
    """Two tables, four columns, both join spellings, a metric and a term.

    Every asset type ``withheld_by_grant`` has a rule for is present, and the two joins differ
    only in whether their endpoints carry a schema — the spelling-dependent hole this suite also
    covers one layer down.
    """
    return [
        SchemaAsset(id="sales", name="sales", summary="sales audit log and customers"),
        TableAsset(
            id="sales.audit_log", schema="sales", physical_name="audit_log",
            summary="Audit log entries, one per change.",
            body="Every write to the sales schema lands here, with the actor's name.",
            columns=("sales.audit_log.note", "sales.audit_log.customer_id"),
        ),
        ColumnAsset(
            id="sales.audit_log.note", schema="sales", parent_table="sales.audit_log",
            physical_name="note", summary="Free text describing the change.",
        ),
        ColumnAsset(
            id="sales.audit_log.customer_id", schema="sales", parent_table="sales.audit_log",
            physical_name="customer_id", summary="Which customer the change was about.",
        ),
        TableAsset(
            id="sales.customers", schema="sales", physical_name="customers",
            summary="Customers of the shop.", body="One row per registered buyer.",
            columns=("sales.customers.email", "sales.customers.id"),
        ),
        ColumnAsset(
            id="sales.customers.email", schema="sales", parent_table="sales.customers",
            physical_name="email", summary="Contact address.",
            sample_values=("ada@example.com",),
        ),
        ColumnAsset(
            id="sales.customers.id", schema="sales", parent_table="sales.customers",
            physical_name="id", summary="Primary key.",
        ),
        JoinAsset(
            id="sales.join_bare_a1b2c3", left_table="customers", right_table="audit_log",
            on="customers.id = audit_log.customer_id",
            summary="Customers to their audit log entries, endpoints spelled bare.",
        ),
        JoinAsset(
            id="sales.join_qualified_d4e5f6",
            left_table="sales.customers", right_table="sales.audit_log",
            on="sales.customers.id = sales.audit_log.customer_id",
            summary="The same pair, endpoints spelled schema-qualified.",
        ),
        MetricAsset(
            id="sales.metric_writes", name="audit writes", base_table="sales.audit_log",
            expression="count(*)", summary="How many changes were logged.",
        ),
        TermAsset(
            id="term.buyer", name="buyer", summary="buyer, customer",
            binding=Binding(target_type=AssetType.column, target_id="sales.customers.id"),
        ),
        # Bound to the **denied** column. A term names no table, which is why the withholding
        # rules exempted the type — but `binding=<target id>` is rendered, and the target of a
        # binding is a column, so this one spells the denied column out under a business phrase.
        TermAsset(
            id="term.contact", name="contact address", summary="contact address, email address",
            binding=Binding(target_type=AssetType.column, target_id="sales.customers.email"),
        ),
    ]


def _client(grant: Grant) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api.routes import make_app
    from governed_bi.serve.session import from_assets

    session = from_assets(
        _assets(),
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}, access_grant=grant),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
    )
    assert not session.fatal_problems, [str(p) for p in session.fatal_problems]
    return TestClient(make_app(session, _TurnLog()))


class _TurnLog:
    def list_turns(self, **_: Any) -> list[Any]:
        return []


def _strings(payload: Any) -> list[str]:
    """Every string anywhere in a JSON body — keys and values, at any depth.

    Values *and* keys, because a projection keyed on an asset id (``joins_by_edge`` shaped
    output, a map of column id to something) discloses exactly as much as one that lists them.
    """
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        return [
            s
            for key, value in payload.items()
            for s in (*_strings(key), *_strings(value))
        ]
    if isinstance(payload, (list, tuple)):
        return [s for item in payload for s in _strings(item)]
    return []


def _body_minus_echo(payload: Any, route: str) -> str:
    """Every string in ``payload``, minus the caller's own path parameter echoed back.

    ``GET /columns/{id}/related`` returns ``{"column": {"id": <the id you asked for>}, ...,
    "meta": {"column_resolvable": false}}`` for an id that names nothing — ADR 0009's shape, and
    the same reply a withheld id now gets, which is
    :data:`~governed_bi.govern.bounds.OUT_OF_SCOPE_MESSAGE`'s "you may not and there is no such
    thing share a reply" at the HTTP layer. Repeating a string the caller supplied discloses
    nothing, so it is dropped by **exact match** — a substring rule would also swallow a real
    leak that merely contains it.
    """
    asked = route.strip("/").split("/")[1] if route.startswith("/columns/") else None
    return "\n".join(s for s in _strings(payload) if s != asked)


@pytest.mark.parametrize("route", CORPUS_ROUTES)
def test_the_open_grant_serves_the_identifier_so_the_sweep_is_not_vacuous(route: str) -> None:
    """The control. A route that returns nothing passes any "is it absent" test.

    ``/schema/sales.customers`` and ``/columns/.../related`` are the two that would otherwise
    slip through as 404s and empty payloads.
    """
    response = _client(OPEN_GRANT).get(route)
    assert response.status_code == 200, (route, response.status_code, response.text)
    body = _body_minus_echo(response.json(), route)
    assert "audit_log" in body or "email" in body, (
        f"{route} names neither withheld asset under the open grant, so its silence under the "
        f"restrictive one proves nothing:\n{body[:2000]}"
    )


@pytest.mark.parametrize("route", CORPUS_ROUTES)
def test_no_corpus_route_names_a_withheld_asset(route: str) -> None:
    """The finding. Under a restrictive grant, no spelling of the withheld assets survives.

    ``/schema/sales.audit_log`` is expected to 404: a withheld table is not in the map the
    handler reads, and the reply is the same one an id that names nothing gets — which is
    ``OUT_OF_SCOPE_MESSAGE``'s rule ("you may not" and "there is no such thing" share a reply)
    at the HTTP layer.
    """
    response = _client(RESTRICTIVE).get(route)
    if response.status_code == 404:
        assert route == "/schema/sales.audit_log", (route, response.text)
        return
    assert response.status_code == 200, (route, response.status_code, response.text)
    body = _body_minus_echo(response.json(), route)
    hits = sorted({needle for needle in FORBIDDEN if needle in body})
    assert not hits, f"{route} disclosed {hits} under a grant that withholds them:\n{body[:2000]}"


def test_the_authorized_half_of_the_corpus_still_reaches_the_browse_surface() -> None:
    """Both directions, or a filter that returned nothing would pass every test above."""
    client = _client(RESTRICTIVE)
    rows = client.get("/corpus/rows?type=table").json()
    assert [r["id"] for r in rows["rows"]] == ["sales.customers"], rows
    assert rows["total"] == 1

    detail = client.get("/schema/sales.customers").json()
    assert [c["id"] for c in detail["columns"]] == ["sales.customers.id"], detail
    assert detail["description"] == "One row per registered buyer."


def test_a_graph_edge_cannot_name_a_node_the_grant_removed() -> None:
    """Narrowing the asset map alone is not enough, and this is the surface that shows it.

    ``/graph``'s edges come from ``CorpusStructure.join_edges``, which is keyed on table asset
    ids and is not the asset map. Filter only the map and the withheld table loses its **node**
    while an edge keeps its id in ``target`` — the table's existence, in a different field of
    the same response.
    """
    wide = _client(OPEN_GRANT).get("/graph").json()
    assert any(
        "sales.audit_log" in (edge["source"], edge["target"]) for edge in wide["edges"]
    ), wide["edges"]

    narrow = _client(RESTRICTIVE).get("/graph").json()
    assert [n["id"] for n in narrow["nodes"]] == ["sales.customers"], narrow["nodes"]
    assert narrow["edges"] == [], narrow["edges"]


def test_the_open_grant_leaves_the_session_object_untouched() -> None:
    """Behaviour identity, at the object level rather than at the value level.

    Under the shipped grant :func:`~governed_bi.api.visibility.visible` returns *the session
    itself* — not an equal copy — so no browse response can differ, no projection is rebuilt,
    and the cost on the default path is one boolean.
    """
    from governed_bi.api.visibility import visible, withheld_for
    from governed_bi.serve.session import from_assets

    session = from_assets(
        _assets(),
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
    )
    assert withheld_for(session) == frozenset()
    assert visible(session) is session

    restricted = from_assets(
        _assets(),
        connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}, access_grant=RESTRICTIVE),
        db_id="sales",
        corpus_content_hash_="corpus-under-test",
    )
    assert visible(restricted) is not restricted
    assert withheld_for(restricted) == frozenset(
        {
            "sales.audit_log",
            "sales.audit_log.note",
            "sales.audit_log.customer_id",
            "sales.customers.email",
            "sales.join_bare_a1b2c3",
            "sales.join_qualified_d4e5f6",
            "sales.metric_writes",
            # The term bound to the denied column, and nothing else of that type: `term.buyer`
            # points at `sales.customers.id`, which this grant authorizes, and survives.
            "term.contact",
        }
    )


def test_the_one_unfiltered_field_is_declared() -> None:
    """``/audit/corpus``'s ``problems`` are carried verbatim, and that is a decision.

    ``servable`` is ``not fatal_problems``. Filtering a curation defect that happens to name a
    withheld table would let an unservable corpus read as servable — trading a health signal the
    operator needs for a narrow disclosure to the one principal this repository has. Asserted
    here so the exemption is a declaration rather than something a reader discovers.

    (This said the strings were "a disclosure they already have, since the same strings are on
    the server's stdout at startup". Only ``serve/__main__.py`` prints them; neither HTTP entry
    point does, so under ``langgraph dev`` this route is where they appear.)
    """
    body = _client(RESTRICTIVE).get("/audit/corpus").json()
    assert set(body["problems"]) == {"fatal", "degradations", "n_fatal", "n_degradations"}
    assert body["assets"]["total"] == 4, body["assets"]
    assert body["assets"]["by_type"] == {"column": 1, "schema": 1, "table": 1, "term": 1}, body
    assert body["structure"]["untagged_assets"] >= 0, body["structure"]
