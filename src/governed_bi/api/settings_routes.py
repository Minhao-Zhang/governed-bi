"""Runtime-toggle admin routes: ``GET /settings/toggles`` and ``POST /settings/toggles/{name}``.

**Not a curation concern, even though it used to live in ``curation_routes.py``.** A toggle is an
operational knob over ``serve/runtime_overrides.py`` -- it has nothing to do with drafts,
conflicts, or the clarifications ledger. It was parked in ``curation_routes.py`` anyway because
that module already had the one thing a settings route needs (a ``make_..._router(session)``
factory mounted beside the others in ``routes.py``), and there was no reason yet to build a
second file just to hold two routes.

**Split out now because the file it was parked in ran out of room.** ``curation_routes.py`` was
984 lines against ADR 0005 §6's hard 1000-line cap (``tools/check_file_length.py``) -- the split
is forced by that cap, not by a belief that these routes were badly placed before. ``elicitation_
routes.py`` came out of the same file for the same reason, in the same commit series. Both are
factories over one ``session``, mirroring ``drafts_routes.py``'s own separate-``APIRouter``
module and the reasoning ``curation_routes.py::make_curation_router`` gives for why these are
factories rather than a module-level ``router``.

No behaviour changed: both routes keep their exact path, method, request/response shape, and
docstring.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

__all__ = ["make_settings_router"]


def make_settings_router(session: Any) -> APIRouter:
    """The two runtime-toggle routes this file declares, over one ``session``.

    A factory, not a module-level ``router`` -- see the module docstring, and
    ``browse_routes.make_router``'s identical reasoning for why.
    """
    router = APIRouter()

    @router.get("/settings/toggles")
    def list_toggles() -> list[dict[str, Any]]:
        """Every knob an operator may flip, its effective value, and **where that value came from**.

        The `source` field is the load-bearing one. Without it a client cannot tell an operator
        that a switch is pinned by an exported variable, and would render a control that silently
        does nothing — which is the class of defect this pair of routes exists to end. This fork
        shipped three such controls: `/settings/allow-user-clarification` (no route, on either
        branch), `can_edit` (hardcoded `False`, so what it gated could never appear), and
        `ui_display_mode` (declared in the client's schema, never populated).

        Deliberately **not** named after `allow_user_clarification`: that is a v1 name with no knob
        behind it — absent from `KNOB_REGISTER`, and the `[serve]` block in
        `governed_bi.local.toml` that sets it is read by nothing.
        """
        from governed_bi.serve.runtime_overrides import describe

        return describe()

    @router.post("/settings/toggles/{name}")
    def set_toggle(name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Set one knob, or clear it back to its default with ``{"value": null}``.

        **Not gated on a capability, and that is a decision rather than an oversight.** The obvious
        candidate is `can_edit`, which is hardcoded `False` — gating on it is precisely how this
        fork acquired a control that could never render; `/clarifications/{id}/answer` already
        records the same choice. The real boundary is the one `api/auth.py` describes: this engine
        binds to loopback and reaching the port is sufficient. The Settings UI shows these switches
        to the engineer tier only, which is an affordance, not a security boundary.

        404 on a name outside the allowlist — including `git_sha`, which is `operational` and would
        be writable under a role-based rule, and `enable_structured_percentage_check`, which is
        `comparability` and belongs in `arms.toml` rather than behind a switch. 422 on the wrong
        type, never a coercion: `bool("false")` is `True`, so a coerced string would switch a
        feature on and record it as off. 409 when the environment owns the value, because
        accepting a write the engine will not use is the same lie in a new place.
        """
        from fastapi import HTTPException

        from governed_bi.register.knobs import env_override
        from governed_bi.serve.runtime_overrides import (
            TOGGLEABLE,
            clear_override,
            describe,
            set_override,
        )

        if name not in TOGGLEABLE:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"knob {name!r} is not runtime-toggleable. Toggleable: {sorted(TOGGLEABLE)}. "
                    "Being `operational` is not enough — that role also carries the fields a "
                    "measurement's provenance is made of."
                ),
            )
        if env_override(name) is not None:
            from governed_bi.register.knobs import env_overrides

            var = env_overrides().get(name)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{var} pins {name!r} for this process, and an exported variable is how an "
                    "eval arm pins a run. Unset it to make this switch effective."
                ),
            )

        value = (body or {}).get("value")
        try:
            if value is None:
                clear_override(name)
            else:
                set_override(name, value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return next(row for row in describe() if row["name"] == name)

    return router
