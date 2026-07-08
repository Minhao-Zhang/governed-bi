"""Cockpit entry point.

Views (``docs/viz.md``): corpus health · table view · FK graph · audit trail
(centerpiece) · gold-diff (BIRD, read-only) · skills · search. Schema-driven
forms (it knows the asset schemas from ``governed_bi.corpus.schemas``); read-only
views computed from the corpus, editable views write back to git.

Framework TBD (the ``.gitignore`` anticipates Streamlit / Marimo). Kept simple:
local app, git integration for save/PR, no SaaS, no multi-tenant.
"""

from __future__ import annotations

from pathlib import Path


def run(corpus_root: Path) -> None:
    """Launch the local audit/edit cockpit over the corpus at ``corpus_root``."""
    raise NotImplementedError("viz cockpit pending; framework TBD (Streamlit/Marimo)")
