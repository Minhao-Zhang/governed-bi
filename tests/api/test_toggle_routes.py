"""``GET /settings/toggles`` and ``POST /settings/toggles/{name}``.

**Not ``/settings/allow-user-clarification``.** That name has a full client half in this fork — a
zod schema, an `api-client` method, a rendered component — and has never had a route, because there
is no such knob: ``allow_user_clarification`` is a v1 name absent from ``KNOB_REGISTER`` entirely,
and ``governed_bi.local.toml``'s ``[serve]`` block that sets it is read by nothing. These routes
expose the knobs that exist.

**No capability gate, and that is deliberate rather than an omission.** The obvious candidate is
``can_edit``, which is hardcoded ``False`` (``routes.py``) — gating on it is how this fork got a
control that could never render. ``/clarifications/{id}/answer`` already documents the same choice.
The real boundary is the one ``api/auth.py`` describes: the engine is bound to loopback and
reaching the port is sufficient. The Settings UI shows this section to the engineer tier only,
which is an affordance, not a security boundary, and saying so here stops a reader mistaking it
for one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from contracts import needs
from governed_bi.serve import runtime_overrides

pytestmark = needs("D")


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_overrides, "OVERRIDE_PATH", tmp_path / "runtime-overrides.json")
    runtime_overrides.reload()


def _client(tmp_path: Path) -> Any:
    from fastapi.testclient import TestClient

    from governed_bi.api import routes, trace_store
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    session = Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}), corpus_content_hash="c",
        prompt_set_hash="p", knobs_resolved={}, db_id="app_store", run_id="r",
        corpus_root=tmp_path,
    )
    return TestClient(routes.make_app(session, None, trace_store))


def test_listing_reports_every_toggleable_knob_with_its_source(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/settings/toggles")

    assert response.status_code == 200, response.text
    rows = {r["name"]: r for r in response.json()}
    assert set(rows) == set(runtime_overrides.TOGGLEABLE)
    for row in rows.values():
        assert row["source"] == "default"
        assert row["value"] is False
        assert row["editable"] is True
        # The explanation travels with the switch. A control whose effect a reader has to guess at
        # is how the three client-only halves got built in the first place.
        assert row["why"].strip() != ""


def test_setting_a_toggle_changes_what_the_engine_reports(tmp_path: Path) -> None:
    """The assertion that matters is not the 200 — it is that a *reader* now sees it.

    Read through `/capabilities` rather than `_resolved_knobs`, which is deliberately the clean
    base: the two readers that mint a claim layer the override on, and the base does not, so that
    clearing a switch works. See
    `tests/serve/test_a_runtime_override_cannot_forge_a_configuration.py`.
    """
    client = _client(tmp_path)
    assert client.get("/capabilities").json()["enable_clarification_to_draft"] is False

    response = client.post("/settings/toggles/enable_clarification_to_draft", json={"value": True})

    assert response.status_code == 200, response.text
    assert response.json()["value"] is True
    assert response.json()["source"] == "override"
    assert client.get("/capabilities").json()["enable_clarification_to_draft"] is True

    # And back off again, which is the half that shipped broken the first time.
    client.post("/settings/toggles/enable_clarification_to_draft", json={"value": None})
    assert client.get("/capabilities").json()["enable_clarification_to_draft"] is False


def test_clearing_with_a_null_returns_the_knob_to_its_default(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/settings/toggles/enable_mistake_memory_mining", json={"value": True})

    response = client.post("/settings/toggles/enable_mistake_memory_mining", json={"value": None})

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "default"
    assert response.json()["value"] is False


def test_a_knob_outside_the_allowlist_is_404(tmp_path: Path) -> None:
    """`git_sha` is `operational` and would be writable under a role-based rule. It is not."""
    response = _client(tmp_path).post("/settings/toggles/git_sha", json={"value": "deadbeef"})

    assert response.status_code == 404, response.text
    assert "toggleable" in response.json()["detail"]


def test_a_comparability_knob_is_404(tmp_path: Path) -> None:
    """`enable_structured_percentage_check` is the near-neighbour that must stay out: changing it
    makes two runs incomparable, which belongs in `arms.toml` and not behind a switch.
    """
    response = _client(tmp_path).post(
        "/settings/toggles/enable_structured_percentage_check", json={"value": True}
    )

    assert response.status_code == 404, response.text


def test_the_wrong_type_is_422_not_a_silent_coercion(tmp_path: Path) -> None:
    """`bool("false")` is `True`, so a coerced string would switch a feature **on** and record it
    as off — the defect `bool_knob`'s own docstring exists for.
    """
    response = _client(tmp_path).post(
        "/settings/toggles/enable_clarification_to_draft", json={"value": "false"}
    )

    assert response.status_code == 422, response.text
    assert runtime_overrides.overrides() == {}


def test_a_knob_the_environment_pins_is_reported_and_refused(tmp_path: Path, monkeypatch) -> None:
    """An exported variable is how an eval arm pins a run, so a click must not quietly beat one.

    Both halves are asserted: the listing says the switch is not editable and names the variable,
    and the write is refused rather than accepted-and-ignored. Accepting it would leave the UI
    showing a value the engine does not use, which is the failure this whole round is about.
    """
    from governed_bi.register.knobs import env_overrides, knob_default

    name, var = next(iter(env_overrides().items()))
    monkeypatch.setitem(runtime_overrides.TOGGLEABLE, name, "under test only")
    monkeypatch.setenv(var, str(type(knob_default(name))(7)))
    client = _client(tmp_path)

    listed = {r["name"]: r for r in client.get("/settings/toggles").json()}[name]
    assert listed["source"] == "environment"
    assert listed["editable"] is False
    assert listed["env_var"] == var

    response = client.post(f"/settings/toggles/{name}", json={"value": knob_default(name)})
    assert response.status_code == 409, response.text
    assert var in response.json()["detail"]
