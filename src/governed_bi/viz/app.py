"""Cockpit entry point (Streamlit).

Read-only audit cockpit over the corpus, plus a conversational front end:

- **Chat**: a multi-turn chat over the governed server flow. Each turn shows the
  answer, its two-axis stamp, the result table, the SQL, and the provenance trace;
  prior turns are fed back through the engine's working memory (D8), so a follow-up
  can resolve references (with a live model - the offline template generator
  answers each question independently).
- **Health / Tables / Assets / Skills**: the read-only audit views.

Editing and save-to-PR (D6) are out of scope for this repo: a correction is "edit
a file + PR" served by generic git/PR tooling + CI, or the enterprise app (see
docs/viz.md).

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


def _render_result_table(result) -> None:
    """Render the executed rows as a collapsible table under the answer text.

    Nothing is shown for a refusal (no result) or an empty grid; a scalar answer
    is already spelled out in the text, but the table still offers the raw cell.
    """
    if result is None or not result.rows:
        return
    plural = "s" if result.row_count != 1 else ""
    suffix = ", truncated" if result.truncated else ""
    with st.expander(f"result ({result.row_count} row{plural}{suffix})", expanded=False):
        st.dataframe(
            [dict(zip(result.columns, row)) for row in result.rows],
            hide_index=True,
        )


def _render_answer_view(view, *, provenance_expander: bool = False) -> None:
    """Render one server answer: the two-axis stamp, text, result table, SQL, and
    provenance (the Chat view's per-turn rendering)."""
    _TIER_RENDERER.get(view.tier, st.info)(f"tier: {view.tier}")
    # The two axes the tier collapses, shown side by side so neither is read as the
    # other: safety is a pass/fail gate; assurance is how well-grounded (not "right").
    axis_cols = st.columns(2)
    axis_cols[0].metric("safety clearance", "cleared" if view.safety_clearance else "not cleared")
    axis_cols[1].metric("semantic assurance", view.semantic_assurance)
    if view.text:
        st.write(view.text)
    _render_result_table(view.result)
    if view.sql:
        st.code(view.sql, language="sql")
    if view.escalation:
        st.warning(view.escalation)
    if provenance_expander:
        with st.expander("provenance"):
            st.json(view.provenance)
    else:
        st.json(view.provenance)


def _server_answer(corpus, sqlite_path: Path, question: str, *, session_id: str, generator, embedder, memory, narrator=None):
    """Run one question through the governed server flow against ``sqlite_path``.

    A fresh read-only connector per call (cheap for SQLite); the flow sees the
    ``for_server()`` view, not the full audit corpus.
    """
    from governed_bi.config import Environment, Settings
    from governed_bi.gateway import Gateway, Identity, SqliteConnector
    from governed_bi.server import answer_question

    connector = SqliteConnector(sqlite_path)
    try:
        return answer_question(
            question,
            Identity(user="viz", all_access=True),
            corpus=corpus.for_server(),
            gateway=Gateway(connector),
            settings=Settings.for_env(Environment.dev),
            session_id=session_id,
            sql_generator=generator,
            embedder=embedder,
            working_memory=memory,
            narrator=narrator,
        )
    finally:
        connector.close()


def _build_chat_generator():
    """Return ``(generator, embedder, narrator, mode_caption)`` for the chat view.

    Uses the live LangChain client (context-aware follow-ups via working memory,
    plus a natural-language narrator for the answer text) when a key + the
    ``agents`` extra are available; otherwise the deterministic offline template
    generator with no narrator (the compact render is used), which answers metric
    questions independently and ignores conversation history.
    """
    from governed_bi.server import TemplateSqlGenerator

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from governed_bi.config import load_settings
            from governed_bi.llm import LangChainChatClient, LangChainEmbedder
            from governed_bi.server import LlmAnswerNarrator, LlmSqlGenerator

            models = load_settings().models
            chat = LangChainChatClient.from_config(models)
            return (
                LlmSqlGenerator(chat, dialect="sqlite"),
                LangChainEmbedder.from_config(models),
                LlmAnswerNarrator(chat),
                f"Live model: {models.llm_model} — follow-ups resolve against the conversation.",
            )
        except Exception as err:  # missing agents extra, bad config, etc.
            return (
                TemplateSqlGenerator(), None, None,
                f"Template generator (live model unavailable: {err}).",
            )
    return (
        TemplateSqlGenerator(), None, None,
        "Offline template generator — answers metric questions independently. "
        "Set OPENAI_API_KEY (in the env or a repo-root .env) + the agents extra "
        "for context-aware follow-ups.",
    )


def _render_chat(corpus, sqlite_path: Path) -> None:
    st.subheader("Chat (governed server, multi-turn)")
    if not sqlite_path.exists():
        st.info(f"No database at {sqlite_path}; set GOVERNED_BI_SQLITE to enable chat.")
        return

    from governed_bi.memory import InMemoryWorkingMemory

    # Conversational state persisted across Streamlit reruns.
    if "chat" not in st.session_state:
        st.session_state.chat = {
            "transcript": [],  # [{"role": "user"|"assistant", "text"|"view"}]
            "memory": InMemoryWorkingMemory(),
            "session_id": "viz-chat",
            "generator": _build_chat_generator(),
        }
    chat = st.session_state.chat
    generator, embedder, narrator, mode = chat["generator"]

    top = st.container()
    with top:
        cols = st.columns([4, 1])
        cols[0].caption(mode)
        if cols[1].button("Clear"):
            chat["memory"].clear(chat["session_id"])
            chat["transcript"] = []
            st.rerun()

    # Replay the conversation so far (state survives reruns; new turns appended below).
    for msg in chat["transcript"]:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.write(msg["text"])
            else:
                _render_answer_view(msg["view"], provenance_expander=True)

    prompt = st.chat_input("Ask a question about the data…")
    if not prompt or not prompt.strip():
        return

    with st.chat_message("user"):
        st.write(prompt)
    chat["transcript"].append({"role": "user", "text": prompt})

    answer = _server_answer(
        corpus, sqlite_path, prompt,
        session_id=chat["session_id"], generator=generator, embedder=embedder,
        memory=chat["memory"], narrator=narrator,
    )
    view = presenter.answer_view(answer)
    with st.chat_message("assistant"):
        _render_answer_view(view, provenance_expander=True)
    chat["transcript"].append({"role": "assistant", "view": view})

    # Record the turn AFTER answering, so the current question is never injected as
    # prior history (the flow only reads working memory; the caller records).
    chat["memory"].append(chat["session_id"], "user", prompt)
    chat["memory"].append(chat["session_id"], "assistant", view.text or view.escalation or "(refused)")


def run(corpus_root: Path, *, db: str, sqlite_path: Path) -> None:
    """Render the cockpit over the corpus at ``corpus_root`` (the full audit view)."""
    st.set_page_config(page_title="governed-bi cockpit", layout="wide")
    st.title("governed-bi audit cockpit")
    st.caption(f"corpus: {corpus_root}/{db}")

    # The cockpit sees the FULL corpus (Facts + Inference + Audit + excluded).
    corpus = load_corpus(corpus_root, db=db)

    view = st.sidebar.radio("View", ["Chat", "Health", "Tables", "Assets", "Skills"])
    if view == "Chat":
        _render_chat(corpus, sqlite_path)
    elif view == "Health":
        _render_health(corpus)
    elif view == "Tables":
        _render_tables(corpus)
    elif view == "Assets":
        _render_assets(corpus)
    else:
        _render_skills(corpus)


if __name__ == "__main__":
    _root = Path(os.environ.get("GOVERNED_BI_CORPUS", "corpus"))
    _db = os.environ.get("GOVERNED_BI_DB", "beer_factory")
    _sqlite = Path(os.environ.get("GOVERNED_BI_SQLITE", "data/bird/beer_factory.sqlite"))
    run(_root, db=_db, sqlite_path=_sqlite)
