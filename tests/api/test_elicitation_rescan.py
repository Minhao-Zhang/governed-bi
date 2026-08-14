"""``POST /elicitation/generate`` re-run against a ledger that already has history.

``tests/api/test_elicitation_routes.py`` covers what a scan *finds*; this covers what a re-run
*says about the last one*. Split rather than appended because that file is at 911 of ADR 0005
§6's 1 000-line hard cap and ``tests/curator/test_elicitation.py`` already had to be split once
for the same reason. Its fixture is imported rather than copied: a second schema fixture would be
a second answer to "what does the wizard find on this schema", which is the thing every count
below is relative to.

The assertions are about the **account**, not the totals. The owner's third standing decision
(``utku-ai-setup-wizard-gap-model.md`` § "Three owner decisions") is that a re-run diffs against
already-confirmed content and says so in words when nothing is new — so "``n_generated == 0``"
is precisely the answer that is *not* good enough here, and none of these tests accept it alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_elicitation_routes import (  # noqa: E402 - sibling fixture, as tests/curator/ does
    _by_scope,
    _client,
    _session_with_schema,
)

from contracts import needs  # noqa: E402

pytestmark = needs("D")


def _report(client) -> dict:
    response = client.post("/elicitation/generate")
    assert response.status_code == 200, response.text
    return response.json()["report"]


# ── (i) a first scan ────────────────────────────────────────────────────────────────────────


def test_a_first_scan_says_there_was_nothing_to_compare_against(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    report = _report(client)

    assert report["nothing_new"] is False
    assert report["new"]["count"] > 0
    assert report["still_open"]["count"] == 0
    assert report["settled"]["count"] == 0
    assert report["stranded"]["count"] == 0
    assert report["summary"].endswith("Nothing was on file before this scan to compare against.")


# ── (ii) a re-run with nothing changed ──────────────────────────────────────────────────────


def test_a_re_run_with_nothing_changed_says_nothing_is_new(monkeypatch, tmp_path: Path) -> None:
    """The decision's headline case. Every candidate is re-derived, every one is already on the
    ledger, and what the admin reads is a sentence rather than a zero."""
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = _report(client)
    second = _report(client)

    assert second["nothing_new"] is True
    assert second["new"]["count"] == 0
    assert second["still_open"]["count"] == first["new"]["count"]
    assert second["still_open"]["scopes"] == first["new"]["scopes"]
    assert second["summary"].startswith("No new gaps found. ")
    assert "still unanswered" in second["summary"]


def test_a_re_run_appends_nothing_to_the_ledger(monkeypatch, tmp_path: Path) -> None:
    """The report is new; the idempotency it reports on is ``b587358``'s and must not have moved
    when the scope filter did."""
    from governed_bi.curator.clarifications import load_clarifications

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    before = load_clarifications(tmp_path)
    client.post("/elicitation/generate")

    assert load_clarifications(tmp_path) == before


# ── (iii) answering one question ────────────────────────────────────────────────────────────


def test_answering_one_question_moves_exactly_that_one_to_settled(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = _report(client)
    answered = _by_scope(
        client.get("/elicitation/candidates").json(), "elicitation:exclusion:orders.review_status"
    )
    client.post(f"/clarifications/{answered['id']}/answer", json={"choice_id": "exclude"})

    second = _report(client)
    assert second["settled"]["scopes"] == [answered["scope"]]
    assert answered["scope"] not in second["still_open"]["scopes"]
    assert second["still_open"]["count"] == first["new"]["count"] - 1
    assert second["nothing_new"] is True
    assert "1 question was already answered and was not asked again." in second["summary"]


def test_everything_else_stays_still_open(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = _report(client)
    answered = _by_scope(
        client.get("/elicitation/candidates").json(), "elicitation:exclusion:orders.review_status"
    )
    client.post(f"/clarifications/{answered['id']}/answer", json={"choice_id": "exclude"})

    second = _report(client)
    carried = set(first["new"]["scopes"]) - {answered["scope"]}
    assert set(second["still_open"]["scopes"]) == carried


# ── the diff key: scope, not question text ──────────────────────────────────────────────────


def test_a_reworded_question_is_not_a_new_gap(monkeypatch, tmp_path: Path) -> None:
    """**The key choice this module makes, pinned.** ``clarification.<schema>.<sha256(question)>``
    is a hash of the *text*, so the corpus dedup cannot survive a rewording — and the phrasing
    pass immediately before this work rewrote most of the wizard's question text. The diff keys
    on ``scope`` instead, which both generators derive from schema objects, so a question whose
    words changed on either side is still the same gap.
    """
    from governed_bi.curator.clarifications import load_clarifications, restate_question

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    first = _report(client)
    target = next(
        r for r in load_clarifications(tmp_path) if r.scope == "elicitation:valuemap:orders.country_code"
    )
    restate_question(tmp_path, target.id, "Utterly different words, same gap.")

    second = _report(client)
    assert second["nothing_new"] is True
    assert target.scope in second["still_open"]["scopes"]
    assert second["still_open"]["count"] == first["new"]["count"]


def test_a_rewording_survives_the_ledger_but_not_its_loss(monkeypatch, tmp_path: Path) -> None:
    """The residual, stated rather than hidden. With the ledger intact, scope settles it. With
    the ledger **gone** — a rebuilt corpus root, a second deployment — the only surviving key is
    the corpus asset's question hash, and a fold that happened under different words does not
    match it. That candidate then reports as new, which is honest for a system that has no record
    of it, and it is the one case where a rewording inflates the count.

    Pinned so the next change to either key is visible. Closing it means teaching the corpus the
    scope an answer settles (``enhancer.apply`` would have to carry an ``extra`` its signature
    does not accept today), which is a change to the shared fold path and not to the diff.

    The subject is the ``review_status`` exclusion and not the ``country_code`` value map, which
    is the more obvious pick and would test something else entirely: ``country_code`` is one half
    of this fixture's decoy pair, so it is ``blocked_by`` the cluster question and answering it
    lands ``stranded``, not ``settled``.
    """
    from governed_bi.curator.clarifications import load_clarifications, restate_question

    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    target = next(
        r
        for r in load_clarifications(tmp_path)
        if r.scope == "elicitation:exclusion:orders.review_status"
    )
    restate_question(tmp_path, target.id, "Utterly different words, same gap.")
    client.post(f"/clarifications/{target.id}/answer", json={"choice_id": "exclude"})

    with_ledger = _report(client)
    assert target.scope in with_ledger["settled"]["scopes"], "the ledger remembers the scope"

    (tmp_path / "clarifications.jsonl").unlink()
    without_ledger = _report(client)
    assert target.scope in without_ledger["new"]["scopes"], "the corpus only remembers the words"


# ── (iv) an answer given without its prerequisite ───────────────────────────────────────────


def test_an_answer_given_without_its_prerequisite_is_stranded_not_settled(
    monkeypatch, tmp_path: Path
) -> None:
    """``f718365`` lands that answer at ``draft``; ``approve_draft`` accepts ``proposed`` only and
    nothing promotes a ``draft`` back, so the fact is permanently uncertifiable and the wizard has
    no re-answer path (``utku-ai-design-gaps`` #4). Before this bucket existed a re-run counted it
    as settled, which told an admin a question was closed when it was stuck."""
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    eng = _by_scope(client.get("/elicitation/candidates").json(), "elicitation:termcolumn:amount")
    assert eng["blocked"] is True, "the business half is still open, which is the whole setup"
    client.post(f"/clarifications/{eng['id']}/answer", json={"choice_id": "orders.total_amount"})

    report = _report(client)
    assert report["stranded"]["scopes"] == [eng["scope"]]
    assert eng["scope"] not in report["settled"]["scopes"]
    assert eng["scope"] not in report["still_open"]["scopes"]
    assert "cannot yet reopen it" in report["summary"]


def test_the_same_answer_given_with_its_prerequisite_is_simply_settled(
    monkeypatch, tmp_path: Path
) -> None:
    """The control. Answer the business half first and the engineering half carries its warrant,
    folds ``proposed``, and is settled like any other question — so ``stranded`` is reporting the
    missing warrant and not merely the presence of a dependency."""
    client = _client(monkeypatch, _session_with_schema(tmp_path))
    client.post("/elicitation/generate")
    rows = client.get("/elicitation/candidates").json()
    biz = _by_scope(rows, "elicitation:term:amount")
    eng = _by_scope(rows, "elicitation:termcolumn:amount")
    client.post(f"/clarifications/{biz['id']}/answer", json={"choice_id": "orders.total_amount"})
    client.post(f"/clarifications/{eng['id']}/answer", json={"choice_id": "orders.total_amount"})

    report = _report(client)
    assert report["stranded"]["count"] == 0
    assert {biz["scope"], eng["scope"]} <= set(report["settled"]["scopes"])
