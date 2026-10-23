"""A setting the record publishes must be one the turn can be made to use. See ADR 0008.
"""

from __future__ import annotations

from typing import Any

import pytest

from governed_bi.serve.runtime import int_setting


def test_state_wins_then_settings_resolved_then_the_register() -> None:
    """The precedence, in one place. ``settings_resolved`` is the rung that was missing."""
    assert int_setting({"route_top_n": 7, "settings_resolved": {"route_top_n": 2}}, "route_top_n") == 7
    assert int_setting({"settings_resolved": {"route_top_n": 2}}, "route_top_n") == 2
    # The register's declared value, and *only* the register's -- there is no second copy
    # of it in `serve/` any more, so this cannot silently agree with a stale constant.
    from governed_bi.registry.settings import setting_default

    assert int_setting({}, "route_top_n") == int(setting_default("route_top_n")) == 3


def test_a_setting_that_cannot_be_read_raises_rather_than_substituting_a_value() -> None:
    """An unreadable setting raises. Substituting the default would hide what the caller asked for."""
    with pytest.raises(ValueError, match="not an integer"):
        int_setting({"route_top_n": "three"}, "route_top_n")

    # A setting that ships uncalibrated must not become a threshold nobody chose -- the same
    # refusal `corpus/validate.py` makes for the summary-length bounds.
    # And a typo'd setting name raises out of the register rather than resolving to a
    with pytest.raises(KeyError):
        int_setting({}, "route_top_nn")


def test_route_top_n_from_settings_resolved_changes_the_turn(
    two_schema_assets, guard_off_policy
) -> None:
    """The reachability claim, asserted on what the setting controls: how many schemas are shortlisted."""

    from governed_bi.serve.graph import compile_graph
    from governed_bi.serve.scripted_model import ScriptedChatModel, answer_call
    from governed_bi.serve.session import from_assets

    question = "customer account voltage reading device"

    def run(**overrides: Any) -> dict[str, Any]:
        model: Any = ScriptedChatModel(responses=[answer_call("one device")])
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
    # router's `recall@1` (0.442 on BIRD, against `recall@3` = 0.609) because picking is  [retired]
    # what throws the other candidates away. `retrieved_tables` is a table allowlist; a statement
    retrieved_tables_schemas = {t.split(".", 1)[0] for t in wide.get("retrieved_tables") or []}
    assert retrieved_tables_schemas == {"sales_a", "ops_b"}, (
        f"a shortlisted schema was dropped from licensing: {sorted(retrieved_tables_schemas)}"
    )

    # The setting set the way `Session` publishes it, and *not* in `state` -- which is the
    # only path that was ever wired.
    narrow = run(settings_resolved={**wide["settings_resolved"], "route_top_n": 1})

    # **Length, not identity.** This asserted `== ["ops_b"]`, and which of the two wins is not
    # a property anyone designed: the question ("customer account voltage reading device") is
    # built to match both, and the winner is decided by scores over a two-asset synthetic
    assert len(narrow.get("schemas") or []) == 1, (
        f"settings_resolved['route_top_n'] did not reach routing: {narrow.get('schemas')}. "
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
    """A ``a recorded setting`` setting that cannot change a result is a false claim about a run."""
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
    from governed_bi.registry.settings import setting_default

    assert channel_scale({}).lexical == float(setting_default("w_lexical"))
    assert channel_scale({}).semantic == float(setting_default("w_semantic"))
    moved = channel_scale({"w_lexical": 0.9, "w_semantic": 0.1, "semantic_scale_ceiling": 0.4})
    assert (moved.lexical, moved.semantic, moved.semantic_ceiling) == (0.9, 0.1, 0.4), (
        "a request cannot move the fusion settings, so publishing them in `settings_resolved` is a "
        "claim about a run that the run did not honour"
    )


def test_a_channel_weight_moves_the_ranking() -> None:
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
    """``lexical_saturation_k`` shipped ``UNSET`` while ``index.py`` ran the literal 1.2."""
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
    """``max_queries_per_channel`` bounded a list built as ``[question]``."""
    from governed_bi.registry.settings import setting_names
    from governed_bi.serve.nodes import channels

    assert "max_queries_per_channel" not in set(setting_names())
    assert not hasattr(channels, "_MAX_QUERIES"), "the duplicate local constant is still there"
