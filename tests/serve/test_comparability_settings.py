"""A setting the record publishes must be a setting the turn can be made to use. ADR 0008 D7.

``route_top_n``, ``max_steiner_points`` and ``max_crossings`` are declared
``a recorded setting`` — they go into ``knobs_resolved``, which is what a quotable run
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

from governed_bi.serve.runtime import int_setting


def test_state_wins_then_knobs_resolved_then_the_register() -> None:
    """The precedence, in one place. ``knobs_resolved`` is the rung that was missing."""
    assert int_setting({"route_top_n": 7, "knobs_resolved": {"route_top_n": 2}}, "route_top_n") == 7
    assert int_setting({"knobs_resolved": {"route_top_n": 2}}, "route_top_n") == 2
    # The register's declared value, and *only* the register's -- there is no second copy
    # of it in `serve/` any more, so this cannot silently agree with a stale constant.
    from governed_bi.registry.settings import setting_default

    assert int_setting({}, "route_top_n") == int(setting_default("route_top_n")) == 3


def test_a_setting_that_cannot_be_read_raises_rather_than_substituting_a_value() -> None:
    """Substituting the register default for a setting the caller set to something unusable
    is the comparability lie in a smaller costume: the turn runs at 3, the record says 3,
    and the caller asked for something else entirely.

    ``candidate_depth`` used to swallow exactly this and return its local constant.
    """
    with pytest.raises(ValueError, match="not an integer"):
        int_setting({"route_top_n": "three"}, "route_top_n")

    # A setting that ships uncalibrated must not become a threshold nobody chose -- the same
    # refusal `corpus/validate.py` makes for the summary-length bounds.
    # And a typo'd setting name raises out of the register rather than resolving to a
    # plausible literal no setting backs, which would leave the value outside the
    # comparability hash entirely.
    with pytest.raises(KeyError):
        int_setting({}, "route_top_nn")


def test_route_top_n_from_knobs_resolved_changes_the_turn(
    two_schema_assets, guard_off_policy
) -> None:
    """The reachability claim, asserted on what the setting controls: how many schemas are
    shortlisted.

    This test first asserted the *outcome* — the question matches both schemas, the two
    share no join edge, so at the default of 3 ``connect`` declined and at 1 it answered.
    That stopped being true the same day, because ``connect_node`` now keeps one
    :func:`~governed_bi.retrieve.connect.components` group and both settings answer. The
    surviving assertion is the honest one: the setting decides the shortlist, and a shortlist
    of two is observable in ``schemas`` whether or not it changes the result.
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
    # **Both** components stay retrieved_tables, and that is the design rather than laxity.
    # `connect_node` used to keep one, which was measured to cap reachability at the
    # router's `recall@1` (0.442 on BIRD, against `recall@3` = 0.609) because picking is  [retired]
    # what throws the other candidates away. `retrieved_tables` is a table allowlist; a statement
    # can only reach a table it names, and `connect` guarantees a join path *per component*.
    retrieved_tables_schemas = {t.split(".", 1)[0] for t in wide.get("retrieved_tables") or []}
    assert retrieved_tables_schemas == {"sales_a", "ops_b"}, (
        f"a shortlisted schema was dropped from licensing: {sorted(retrieved_tables_schemas)}"
    )

    # The setting set the way `Session` publishes it, and *not* in `state` -- which is the
    # only path that was ever wired.
    narrow = run(knobs_resolved={**wide["knobs_resolved"], "route_top_n": 1})

    # **Length, not identity.** This asserted `== ["ops_b"]`, and which of the two wins is not
    # a property anyone designed: the question ("customer account voltage reading device") is
    # built to match both, and the winner is decided by scores over a two-asset synthetic
    # corpus. It flipped to `sales_a` when `channel_schema` stopped rewriting, failing a test
    # whose own docstring says it gave up asserting the outcome. The claim is that the setting
    # reaches routing, which is a count.
    assert len(narrow.get("schemas") or []) == 1, (
        f"knobs_resolved['route_top_n'] did not reach routing: {narrow.get('schemas')}. "
        "The record would still publish 1 while the turn routed on 3."
    )
    assert set(narrow.get("schemas") or ()) <= set(wide.get("schemas") or ()), (
        "the survivor must be one of the schemas the wider run scored, or truncation is not "
        f"what happened: narrow={narrow.get('schemas')} wide={wide.get('schemas')}"
    )
    assert narrow.get("path_kind") == "answered", (
        f"path_kind={narrow.get('path_kind')} terminal_reason={narrow.get('terminal_reason')!r}"
    )

    # And the two ways of setting it agree, so there is one behaviour and not two.
    via_state = run(route_top_n=1)
    assert via_state.get("schemas") == narrow.get("schemas")


def test_every_declared_ranking_setting_has_a_reader() -> None:
    """A ``a recorded setting`` setting that cannot change a result is a false claim about a run.

    Four of them shipped that way. ``channel_weight_schema`` and ``channel_weight_other`` were
    declared while ``retrieve/route.route`` took no weights at all; ``w_lexical`` and
    ``w_semantic`` were declared while ``serve/runtime.FUSE_WEIGHTS`` was a hardcoded literal.
    All four entered ``setting_names()`` and ``knobs_resolved``, so a run could publish
    ``w_lexical: 0.9``, move its config hash, and behave identically — the inverse of the
    defect ``registry/settings.py`` opens by describing.
    """
    import inspect

    from governed_bi.registry.settings import setting_names
    from governed_bi.retrieve.route import route
    from governed_bi.serve.runtime import channel_scale

    declared = set(setting_names())
    assert {"channel_weight_schema", "channel_weight_other", "w_lexical", "w_semantic"} <= declared

    # `route` must be able to express a per-channel multiplier at all.
    assert "weights" in inspect.signature(route).parameters, (
        "facet_weight_* are declared and route cannot apply them"
    )
    # **The setting must be readable from the run, not only from the register** (audit I10). This
    # asserted `FUSE_WEIGHTS["lexical"] == setting_default("w_lexical")` against a module constant
    # built at import, which is true of a value no request can move — the same false claim about a
    # run, one level up. `channel_scale` resolves through `float_setting`, so state wins.
    from governed_bi.registry.settings import setting_default

    assert channel_scale({}).lexical == float(setting_default("w_lexical"))
    assert channel_scale({}).semantic == float(setting_default("w_semantic"))
    moved = channel_scale({"w_lexical": 0.9, "w_semantic": 0.1, "semantic_scale_ceiling": 0.4})
    assert (moved.lexical, moved.semantic, moved.semantic_ceiling) == (0.9, 0.1, 0.4), (
        "a request cannot move the fusion settings, so publishing them in `knobs_resolved` is a "
        "claim about a run that the run did not honour"
    )


def test_a_channel_weight_actually_moves_the_ranking() -> None:
    """Behaviour-preserving at 1.0, and *effective* off it. Both halves matter."""
    from governed_bi.retrieve.route import route

    hits = [
        ("channel_schema", "sales", 0.4),
        ("channel_term", "ops", 0.5),
    ]
    unweighted = dict(route(hits))
    assert unweighted == dict(route(hits, weights={"channel_schema": 1.0, "channel_term": 1.0})), (
        "the shipped 1.0/1.0 must be exactly the previous behaviour"
    )
    assert max(unweighted, key=lambda s: unweighted[s]) == "ops"
    boosted = dict(route(hits, weights={"channel_schema": 2.0}))
    assert max(boosted, key=lambda s: boosted[s]) == "sales", (
        "doubling the schema retrieval channel's vote changed nothing, so the weight is not applied"
    )


def test_the_saturation_constant_is_declared_where_it_is_read() -> None:
    """``lexical_saturation_k`` shipped ``UNSET`` while ``index.py`` ran the literal 1.2.

    The register therefore said nobody had chosen the constant that sets where the lexical
    scale sits, and the code chose it anyway. They agreed at 1.2, which is exactly why it
    survived — ``session._resolved_settings`` dropped the setting from the record while BM25 used a
    value the record did not mention. Declaring what runs is not the same as fitting it, and
    the register's note now says so out loud.
    """
    from governed_bi.registry.settings import Unset, setting_default
    from governed_bi.retrieve.index import IndexEntry, build_index

    value = setting_default("lexical_saturation_k")
    assert not isinstance(value, Unset), "the code runs a value; the register must name it"
    from governed_bi.registry.assets import AssetType

    index = build_index(
        [IndexEntry(id="a", summary="customers orders", asset_type=AssetType.table)]
    )
    assert index.lexical.k == float(value), (
        f"BM25 runs k={index.lexical.k} while the register declares {value}"
    )


def test_a_setting_describing_a_feature_that_does_not_exist_is_gone() -> None:
    """``max_queries_per_facet`` bounded a list built as ``[question]``.

    So the limit could never fire, and it was ``a recorded setting`` — every run published a
    bound on a per-channel fan-out the code does not have. Deleted rather than wired: wiring it
    would hand a setting to a feature that does not exist.
    """
    from governed_bi.registry.settings import setting_names
    from governed_bi.serve.nodes import channels

    assert "max_queries_per_channel" not in set(setting_names())
    assert not hasattr(channels, "_MAX_QUERIES"), "the duplicate local constant is still there"
