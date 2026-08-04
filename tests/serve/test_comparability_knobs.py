"""A knob the record publishes must be a knob the turn can be made to use. ADR 0008 D7.

``route_top_n``, ``max_steiner_points`` and ``max_crossings`` are declared
``Role.comparability`` — they go into ``knobs_resolved``, which is what a quotable run
compares against another run. All three read per-turn ``state`` *only*, with a
module-level constant beside them for the default, and **no production entry point writes
those state keys**: only ``eval/harness.py`` and test fixtures do. So the record published
``route_top_n: 3`` and routing genuinely used 3, but only because
``_DEFAULT_TOP_N = 3`` happened to equal the register's default. Move either one and the
record reports a value the turn did not use.

That is why the test below is about ``knobs_resolved`` rather than about ``state``: the
state path already worked, and testing it is what let the gap survive.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.serve.runtime import int_knob


def test_state_wins_then_knobs_resolved_then_the_register() -> None:
    """The precedence, in one place. ``knobs_resolved`` is the rung that was missing."""
    assert int_knob({"route_top_n": 7, "knobs_resolved": {"route_top_n": 2}}, "route_top_n") == 7
    assert int_knob({"knobs_resolved": {"route_top_n": 2}}, "route_top_n") == 2
    # The register's declared value, and *only* the register's -- there is no second copy
    # of it in `serve/` any more, so this cannot silently agree with a stale constant.
    from governed_bi.register.knobs import knob_default

    assert int_knob({}, "route_top_n") == int(knob_default("route_top_n")) == 3


def test_a_knob_that_cannot_be_read_raises_rather_than_substituting_a_value() -> None:
    """Substituting the register default for a knob the caller set to something unusable
    is the comparability lie in a smaller costume: the turn runs at 3, the record says 3,
    and the caller asked for something else entirely.

    ``candidate_depth`` used to swallow exactly this and return its local constant.
    """
    with pytest.raises(ValueError, match="not an integer"):
        int_knob({"route_top_n": "three"}, "route_top_n")

    # A knob that ships uncalibrated must not become a threshold nobody chose -- the same
    # refusal `corpus/validate.py` makes for the summary-length bounds.
    with pytest.raises(ValueError, match="UNSET"):
        int_knob({}, "cost_budget")

    # And a typo'd knob name raises out of the register rather than resolving to a
    # plausible literal no knob backs, which would leave the value outside the
    # comparability hash entirely.
    with pytest.raises(KeyError):
        int_knob({}, "route_top_nn")


def test_route_top_n_from_knobs_resolved_changes_the_turn(
    two_schema_assets, guard_off_policy
) -> None:
    """The reachability claim, asserted on what the knob controls: how many schemas are
    shortlisted.

    This test first asserted the *outcome* — the question matches both schemas, the two
    share no join edge, so at the default of 3 ``connect`` declined and at 1 it answered.
    That stopped being true the same day, because ``connect_node`` now keeps one
    :func:`~governed_bi.retrieve.connect.components` group and both settings answer. The
    surviving assertion is the honest one: the knob decides the shortlist, and a shortlist
    of two is observable in ``schemas`` whether or not it changes the verdict.
    """
    from langchain_core.messages import AIMessage

    from governed_bi.serve.graph import compile_graph
    from governed_bi.serve.scripted_model import ScriptedChatModel
    from governed_bi.serve.session import from_assets

    question = "customer account voltage reading device"

    def run(**overrides: Any) -> dict[str, Any]:
        model: Any = ScriptedChatModel(responses=[AIMessage(content="one device")])
        session = from_assets(
            list(two_schema_assets.values()),
            connector=None,
            policy=guard_off_policy,
            db_id="ops_b",
            corpus_content_hash_="c",
            agent_model=model,
        )
        config = session.configurable()
        config["configurable"]["thread_id"] = f"t-{sorted(overrides)}-{len(overrides)}"
        state = {**session.turn(question), **overrides}
        return compile_graph().invoke(state, config)

    wide = run()
    assert len(wide.get("schemas") or []) == 2, (
        f"the test is vacuous unless the register default shortlists both schemas: "
        f"schemas={wide.get('schemas')}"
    )
    assert wide.get("path_kind") == "answered", (
        f"two schemas sharing no join edge must not decline -- each component is connected "
        f"on its own: path_kind={wide.get('path_kind')} reason={wide.get('terminal_reason')!r}"
    )
    # **Both** components stay licensed, and that is the design rather than laxity.
    # `connect_node` used to keep one, which was measured to cap reachability at the
    # router's `recall@1` (0.442 on BIRD, against `recall@3` = 0.609) because picking is
    # what throws the other candidates away. `licensed` is a table allowlist; a statement
    # can only reach a table it names, and `connect` guarantees a join path *per component*.
    licensed_schemas = {t.split(".", 1)[0] for t in wide.get("licensed") or []}
    assert licensed_schemas == {"sales_a", "ops_b"}, (
        f"a shortlisted schema was dropped from licensing: {sorted(licensed_schemas)}"
    )

    # The knob set the way `Session` publishes it, and *not* in `state` -- which is the
    # only path that was ever wired.
    narrow = run(knobs_resolved={**wide["knobs_resolved"], "route_top_n": 1})

    assert narrow.get("schemas") == ["ops_b"], (
        f"knobs_resolved['route_top_n'] did not reach routing: {narrow.get('schemas')}. "
        "The record would still publish 1 while the turn routed on 3."
    )
    assert narrow.get("path_kind") == "answered", (
        f"path_kind={narrow.get('path_kind')} terminal_reason={narrow.get('terminal_reason')!r}"
    )

    # And the two ways of setting it agree, so there is one behaviour and not two.
    via_state = run(route_top_n=1)
    assert via_state.get("schemas") == narrow.get("schemas")
