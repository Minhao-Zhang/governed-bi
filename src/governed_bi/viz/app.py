"""Cockpit entry point (Streamlit).

Read-only audit cockpit over the corpus: corpus health, the table/tier view, the
non-table asset listing, skills, and an "ask" panel that runs the server flow and
shows the reliability stamp + guardrail trace. Editing and save-to-PR (D6) are a
planned follow-up; the view models already carry everything a form would edit.

This is the **only** Streamlit-specific module. All display data comes from
``governed_bi.viz.presenter`` (no UI dependency), so swapping Streamlit for a more
mature frontend means rewriting this file alone. Streamlit is an optional extra
(``pip install 'governed-bi[viz]'``); this module imports it at load time and is
never imported by the rest of the package or the tests.

Run it with::

    uv run --extra viz streamlit run src/governed_bi/viz/app.py

Point it at a different corpus / DB via the ``GOVERNED_BI_CORPUS``,
``GOVERNED_BI_DB``, and ``GOVERNED_BI_SQLITE`` environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

# Absolute imports: Streamlit executes this file as a top-level script (not as a
# package submodule), so relative imports would fail. The package is installed,
# so ``governed_bi.*`` resolves regardless of how the script is launched.
from governed_bi.corpus import load_corpus
from governed_bi.viz import presenter

_TIER_RENDERER = {
    "governed": st.success,
    "lineage": st.warning,
    "fenced_raw": st.warning,
    "refused": st.error,
}


def _render_health(corpus) -> None:
    health = presenter.corpus_health(corpus)
    st.subheader("Corpus health")
    if health.ci_green:
        st.success("CI green: all references resolve.")
    else:
        st.error(f"CI has {len(health.findings)} finding(s).")
        for message in health.findings:
            st.text(message)

    cols = st.columns(4)
    cols[0].metric("Assets", sum(health.counts.values()))
    cols[1].metric("Suspect columns", health.n_suspect_columns)
    cols[2].metric("Excluded assets", health.n_excluded)
    cols[3].metric("Low-confidence joins", health.n_low_confidence_joins)
    st.caption(f"Skills: {health.n_skills}")
    st.write({k: v for k, v in sorted(health.counts.items())})


def _render_tables(corpus) -> None:
    st.subheader("Tables (Facts + Inference)")
    for table in presenter.table_views(corpus):
        with st.expander(f"{table.physical_name}  ({table.id})", expanded=False):
            if table.excluded:
                st.error(f"EXCLUDED: {table.excluded_reason or 'no reason given'}")
            meta = f"rows: {table.row_count} | grain: {table.grain or 'n/a'}"
            if table.provenance_status:
                meta += f" | provenance: {table.provenance_status}"
            st.caption(meta)
            if table.description:
                st.write(table.description)
            rows = [
                {
                    "column": col.physical_name,
                    "type": col.logical_type,
                    "role": col.role or "",
                    "description": col.description or "",
                    "references": col.references or "",
                    "flag": "excluded" if col.excluded else ("suspect" if col.reliability == "suspect" else ""),
                }
                for col in table.columns
            ]
            st.dataframe(rows, hide_index=True)
            for col in table.columns:
                if col.reliability == "suspect":
                    st.warning(f"suspect: {col.physical_name} - {col.reliability_note or 'AI-flagged'}")
                if col.excluded:
                    st.error(f"excluded: {col.physical_name} - {col.excluded_reason or 'governance'}")


def _render_assets(corpus) -> None:
    st.subheader("Assets")
    rows = presenter.asset_rows(corpus)
    types = sorted({r.asset_type for r in rows})
    chosen = st.multiselect("Types", types, default=types)
    for row in rows:
        if row.asset_type not in chosen:
            continue
        badge = " [EXCLUDED]" if row.excluded else ""
        status = f" ({row.provenance_status})" if row.provenance_status else ""
        st.markdown(f"**{row.id}**{badge}{status}  \n`{row.asset_type}` - {row.summary}")


def _render_skills(corpus) -> None:
    st.subheader("Skills")
    for skill in presenter.skill_views(corpus):
        with st.expander(f"{skill.skill_id}  ({skill.kind})", expanded=False):
            st.markdown(skill.body)


def _render_ask(corpus, sqlite_path: Path) -> None:
    st.subheader("Ask (server flow)")
    if not sqlite_path.exists():
        st.info(f"No database at {sqlite_path}; set GOVERNED_BI_SQLITE to enable the ask panel.")
        return

    from governed_bi.config import Environment, Settings
    from governed_bi.gateway import Gateway, Identity, SqliteConnector
    from governed_bi.server import answer_question

    question = st.text_input("Question", value="What is the total revenue?")
    if not st.button("Ask") or not question.strip():
        return

    server_corpus = corpus.for_server()  # the flow sees the governed view, not the audit view
    connector = SqliteConnector(sqlite_path)
    try:
        gateway = Gateway(connector)
        answer = answer_question(
            question,
            Identity(user="viz", all_access=True),
            corpus=server_corpus,
            gateway=gateway,
            settings=Settings.for_env(Environment.dev),
            session_id="viz",
        )
    finally:
        connector.close()

    view = presenter.answer_view(answer)
    _TIER_RENDERER.get(view.tier, st.info)(f"tier: {view.tier}")
    if view.text:
        st.write(view.text)
    if view.sql:
        st.code(view.sql, language="sql")
    if view.escalation:
        st.warning(view.escalation)
    st.json(view.provenance)


def run(corpus_root: Path, *, db: str, sqlite_path: Path) -> None:
    """Render the cockpit over the corpus at ``corpus_root`` (the full audit view)."""
    st.set_page_config(page_title="governed-bi cockpit", layout="wide")
    st.title("governed-bi audit cockpit")
    st.caption(f"corpus: {corpus_root}/{db}")

    # The cockpit sees the FULL corpus (Facts + Inference + Audit + excluded).
    corpus = load_corpus(corpus_root, db=db)

    view = st.sidebar.radio("View", ["Health", "Tables", "Assets", "Skills", "Ask"])
    if view == "Health":
        _render_health(corpus)
    elif view == "Tables":
        _render_tables(corpus)
    elif view == "Assets":
        _render_assets(corpus)
    elif view == "Skills":
        _render_skills(corpus)
    else:
        _render_ask(corpus, sqlite_path)


if __name__ == "__main__":
    _root = Path(os.environ.get("GOVERNED_BI_CORPUS", "corpus"))
    _db = os.environ.get("GOVERNED_BI_DB", "beer_factory")
    _sqlite = Path(os.environ.get("GOVERNED_BI_SQLITE", "data/bird/beer_factory.sqlite"))
    run(_root, db=_db, sqlite_path=_sqlite)
