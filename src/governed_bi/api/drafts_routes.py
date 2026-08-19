"""``GET /corpus/drafts``: the admin approval queue, read fresh off disk (trust-loop fix round).

**The bug this file fixes.** Task D's drafts panel was originally built on ``GET /corpus/assets``
(``api/routes.py::corpus_assets``), which reads ``session.assets_by_id`` -- a run constant frozen
at session-build time (ADR 0005; ``serve/session.py``'s ``Session`` is
``@dataclass(frozen=True, slots=True)``). So after ``POST /corpus/drafts/{id}/approve`` writes to
disk, that route kept reporting the same asset ``"proposed"`` until the process restarted -- a
hard refresh un-approved what the admin had just approved, on screen if not on disk.
``curation_routes.py::_reload_assets`` already exists to solve exactly this for
``/corpus/assumptions`` and ``/corpus/conflicts``; this route reads the corpus the same way.

**Split into its own file rather than added to ``curation_routes.py``.** That file is 965 lines
against ADR 0005 §6's hard 1000-line cap (``tools/check_file_length.py``); one more route written
in this project's docstring-heavy style would breach it. Mirrors ``browse_routes.py``'s own
separate-``APIRouter`` module, and takes ``curation_routes.py:163-171``'s reasoning for why these
are factories (``make_..._router(session)``) rather than a module-level ``router`` as given.

**``_reload_assets`` is imported from ``curation_routes.py``, not moved.** Moving it to a shared
module would be the cleaner factoring in isolation, but it is called from five places in that
965-line file already (``corpus_assumptions``, ``corpus_conflicts``, ``answer_clarification_route``,
``clarification_from_refusal_route``, ``elicitation_generate``) -- moving it means touching every
one of those call sites in a file already near its cap, for no gain ``tools/check_imports.py``
requires: that tool is AST-only and layers by *package*, not by module, so an import between two
files that are both in ``governed_bi.api`` carries no layer meaning at all (confirmed by running
it, not assumed -- its ``target == own`` branch short-circuits before the ordering check runs).
This module also already has the identical precedent to lean on: ``curation_routes.py::
approve_draft_route`` itself imports ``_provenance_status`` from ``api/routes.py``, a sibling
module in the same package, for the same reason (dodging a cycle at call time rather than at
import time). Importing one private helper the other direction is the same established pattern,
not a new one.

**Not narrowed by ``visible()`` (ADR 0012 §8.5), and that is not a new decision made here.**
``make_curation_router``'s own docstring already states this for the whole router it declares --
"these routes are not narrowed by the access grant, and the browse routes are" -- and
``/corpus/assumptions``/``/corpus/conflicts`` already ship that way today. This route is the same
kind of admin curation surface over the same kind of content, so it inherits that asymmetry
rather than opening a second, independent one. What *is* new, and worth recording precisely: task
D's original panel read ``GET /corpus/assets``, which **is** narrowed
(``routes.py::corpus_assets`` calls ``visible()``) -- so a grant-withheld asset that happened to
be ``proposed`` was invisible to the drafts queue before this fix, and is not after it, if a
deployment ever sets ``GOVERNED_BI_ACCESS_POLICY`` to something other than the open grant this
repository ships. Recorded rather than decided, per this router's own convention: whether
``visible()`` should also narrow the admin curation surface is a governance question the rest of
this router has already left open, and this route does not settle it either.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from governed_bi.api.curation_routes import _reload_assets

__all__ = ["make_drafts_router"]


def make_drafts_router(session: Any) -> APIRouter:
    """The one route this file declares, over one ``session``.

    A factory, not a module-level ``router`` -- see the module docstring, and
    ``browse_routes.make_router``'s identical reasoning for why.
    """
    router = APIRouter()

    @router.get("/corpus/drafts")
    def corpus_drafts() -> list[dict[str, Any]]:
        """Every ``proposed`` asset, read fresh off disk on every call -- the approval queue.

        Carries ``body``, unlike ``GET /corpus/assets`` (which does not declare the field at
        all): an admin approving a draft must be able to read what they are certifying, not
        only its possibly-truncated ``summary``. ``schema``/``excluded`` are not returned --
        certifying a draft is not a browsing action, and the brief this route fixes names
        exactly five fields an admin needs to judge one: id, type, summary, body, status.
        """
        from governed_bi.api.routes import _provenance_status

        rows = [
            {
                "id": asset.id,
                "asset_type": asset.asset_type.value,
                "summary": asset.summary,
                "body": asset.body,
                "provenance_status": status,
            }
            for asset in _reload_assets(session)
            if (status := _provenance_status(asset)) == "proposed"
        ]
        return sorted(rows, key=lambda r: r["id"])

    return router
