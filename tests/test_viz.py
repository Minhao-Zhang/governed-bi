"""Tests for the UI-agnostic viz presenter.

These import only ``governed_bi.viz`` / ``.presenter`` (no Streamlit), which also
demonstrates the swap seam: the cockpit's view models carry no UI dependency, so
the suite runs without the optional ``viz`` extra installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.corpus import load_corpus
from governed_bi.viz import presenter
from governed_bi.server.answer import Answer, ReliabilityTier

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"


@pytest.fixture
def corpus():
    # The cockpit reads the FULL corpus (Audit + excluded assets), not for_server.
    return load_corpus(CORPUS_ROOT, db="beer_factory")


def test_corpus_health(corpus):
    health = presenter.corpus_health(corpus)
    assert health.ci_green
    assert health.findings == []
    assert health.counts["table"] == 5
    assert health.n_suspect_columns == 1  # customers.ZipCode
    assert health.n_excluded == 1  # transaction.CreditCardNumber
    assert health.n_low_confidence_joins == 0  # beer_factory joins are 0.95
    assert health.n_skills == 1


def test_table_views_expose_tiers_and_governance(corpus):
    views = {t.id: t for t in presenter.table_views(corpus)}
    assert len(views) == 5

    tx = views["tbl_beer_factory_transaction"]
    ccn = next(c for c in tx.columns if c.physical_name == "CreditCardNumber")
    assert ccn.excluded  # governance.excluded PII column is visible in the audit view
    # Facts + Inference both present on a normal column.
    price = next(c for c in tx.columns if c.physical_name == "PurchasePrice")
    assert price.logical_type and price.description

    customers = views["tbl_beer_factory_customers"]
    zip_col = next(c for c in customers.columns if c.physical_name == "ZipCode")
    assert zip_col.reliability == "suspect"


def test_asset_rows_filterable(corpus):
    rows = presenter.asset_rows(corpus)
    assert all(r.asset_type != "table" for r in rows)  # tables have their own view
    types = {r.asset_type for r in rows}
    assert {"join", "metric", "term"} <= types
    metrics = presenter.asset_rows(corpus, asset_types={"metric"})
    assert metrics and all(r.asset_type == "metric" for r in metrics)


def test_skill_views(corpus):
    skills = presenter.skill_views(corpus)
    assert len(skills) == 1
    assert skills[0].body.strip()


def test_answer_view_maps_stamp_and_trace():
    answer = Answer(
        tier=ReliabilityTier.governed,
        text="total_revenue = 18496.0",
        sql='SELECT SUM(PurchasePrice) AS total_revenue FROM "transaction"',
        provenance={"route": "kpi_lookup", "metric_id": "metric_revenue"},
    )
    view = presenter.answer_view(answer)
    assert view.tier == "governed"
    assert "SUM(PurchasePrice)" in view.sql
    assert view.provenance["metric_id"] == "metric_revenue"
    assert view.escalation is None


# --------------------------------------------------------------------------- #
# Streamlit app smoke test (only when the optional `viz` extra is installed)
# --------------------------------------------------------------------------- #

APP = Path(__file__).resolve().parents[1] / "src" / "governed_bi" / "viz" / "app.py"


@pytest.mark.parametrize("view", ["Chat", "Ask", "Health", "Tables", "Assets", "Skills"])
def test_streamlit_app_renders_every_view(view):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, list(at.exception)
    at.sidebar.radio[0].set_value(view).run()
    assert not at.exception, list(at.exception)


def test_streamlit_chat_answers_a_question():
    # Drive the Chat view end-to-end: submit a question through the real UI and
    # confirm the governed answer renders (UI -> server flow -> SQLite -> stamp).
    # Uses the offline template generator (no key), so it runs in CI.
    pytest.importorskip("streamlit")
    if not (Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite").exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("Chat").run()
    at.chat_input[0].set_value("What is the total revenue?").run()
    assert not at.exception, list(at.exception)
    # A governed answer stamps its tier via st.success ("tier: governed").
    assert any("tier:" in block.value for block in at.success)
