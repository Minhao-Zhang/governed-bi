"""Runtime knob overrides: what an admin may flip from the UI, and what they may not forge.

Before this, a knob resolved from its declared default and then from an environment variable, and
there was no third path — which is why three UI controls existed for settings nothing could
actually change (see ``docs/detentai-role-tiers-and-clarification-cancel.md`` on client-only halves).
This adds the write path, and almost all of this file is about keeping it narrow.

**The allowlist is not "operational knobs".** That was the first idea and it is wrong: the
operational role also carries ``git_sha``, ``working_tree_dirty`` and ``diff_sha256``, so a UI able
to write any operational knob could **forge the provenance of a measurement**. Toggleability is a
second, explicit decision per knob, which is what ``TOGGLEABLE`` is.

**And an override must be visible in the record.** ``measure/gates.py::_knobs_resolved_gate`` fails
an arm whose rows disagree on any key in ``resume_drift_keys()`` — which includes the operational
role — so flipping a toggle mid-run *should* make that run unquotable. That is the honest outcome
and these tests pin it: the override lands in ``knobs_resolved``, so the gate can see the drift.
Hiding it would let a run report a configuration it did not run under, which is the exact defect
``_resolved_knobs``'s own docstring was written about.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governed_bi.register.knobs import KNOB_REGISTER, Role, resume_drift_keys
from governed_bi.serve import runtime_overrides


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch) -> None:
    """Never write into the repository's own ``runs/`` — same precaution as ``TURN_LOG_DIR``'s."""
    monkeypatch.setattr(runtime_overrides, "OVERRIDE_PATH", tmp_path / "runtime-overrides.json")
    runtime_overrides.reload()


def test_every_toggleable_knob_is_declared_and_operational() -> None:
    """A typo in the allowlist would be a control that silently does nothing, and a comparability
    knob in it would let a UI click make two runs incomparable without saying so.
    """
    by_name = {k.name: k for k in KNOB_REGISTER}
    for name in runtime_overrides.TOGGLEABLE:
        assert name in by_name, f"{name!r} is not a declared knob"
        assert by_name[name].role is Role.operational, (
            f"{name!r} is {by_name[name].role.value}, not operational. A comparability knob "
            "changed from the UI would make two runs incomparable with nothing recording that "
            "a human did it; that belongs in arms.toml, not a switch."
        )


def test_provenance_knobs_are_not_toggleable() -> None:
    """The reason the allowlist is not simply "operational". These four are operational *and*
    are how a measurement says which code produced it.
    """
    for name in ("git_sha", "git_main_sha", "working_tree_dirty", "diff_sha256"):
        assert name not in runtime_overrides.TOGGLEABLE, (
            f"{name!r} became writable from the UI, which lets an operator forge the provenance "
            "of a run"
        )


def test_setting_and_reading_one_override() -> None:
    runtime_overrides.set_override("enable_clarification_to_draft", True)

    assert runtime_overrides.overrides() == {"enable_clarification_to_draft": True}


def test_an_override_survives_a_reload() -> None:
    """Persisted, because the alternative is a switch that silently reverts on restart and an
    admin who cannot tell whether it ever worked.
    """
    runtime_overrides.set_override("enable_mistake_memory_mining", True)
    runtime_overrides.reload()

    assert runtime_overrides.overrides()["enable_mistake_memory_mining"] is True


def test_clearing_an_override_returns_the_knob_to_its_default() -> None:
    runtime_overrides.set_override("enable_clarification_to_draft", True)
    runtime_overrides.clear_override("enable_clarification_to_draft")

    assert "enable_clarification_to_draft" not in runtime_overrides.overrides()


def test_a_knob_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(ValueError, match="not runtime-toggleable"):
        runtime_overrides.set_override("git_sha", "deadbeef")


def test_an_undeclared_knob_is_refused() -> None:
    with pytest.raises(ValueError, match="not runtime-toggleable"):
        runtime_overrides.set_override("enable_teleportation", True)


def test_a_value_of_the_wrong_type_is_refused() -> None:
    """The declared default decides the type, the same rule ``env_override`` uses. A knob that
    arrived as the string ``"false"`` would switch a feature **on** — ``bool("false")`` is
    ``True`` — and be recorded as off, which is the defect ``bool_knob``'s own docstring exists
    for.
    """
    with pytest.raises(ValueError, match="bool"):
        runtime_overrides.set_override("enable_clarification_to_draft", "false")


def test_the_resolved_base_never_carries_an_override() -> None:
    """``_resolved_knobs`` is the **clean base**, and keeping it clean is what makes clearing work.

    The first cut applied overrides here *as well as* at each reader. A session built while a
    switch was on then baked `True` into its cached mapping, so layering `{}` over it after the
    operator cleared the switch still resolved `True` — a switch that turned on and would not turn
    off. The two readers that mint a claim (``Session.turn``,
    ``api/routes.py::capabilities_for``) own the layering; this function owns the base.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.session import _resolved_knobs

    runtime_overrides.set_override("enable_clarification_to_draft", True)

    assert _resolved_knobs(GovernancePolicy(guard_rules_enabled={}))[
        "enable_clarification_to_draft"
    ] is False, "the base absorbed an override, so clearing it later cannot take effect"


def test_an_environment_variable_still_wins(monkeypatch) -> None:
    """Env last, so a run pinned by an exported variable — an eval arm — cannot be quietly
    changed by someone clicking a switch. The route reports the source for exactly this case, so
    the UI can say "pinned by the environment" rather than offering a control that does nothing.

    Driven through a knob that *has* an env var, since neither toggleable knob does today; what is
    under test is the order in ``_resolved_knobs``, not this particular knob.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.register.knobs import env_overrides, knob_default
    from governed_bi.serve.session import _resolved_knobs

    name, var = next(iter(env_overrides().items()))
    # The declared default decides the type on both paths, so build both values from it rather than
    # writing literals -- an `int` for a `float` knob is refused, which is the strictness under
    # test two functions up.
    declared = type(knob_default(name))
    monkeypatch.setenv(var, "7")
    monkeypatch.setitem(runtime_overrides.TOGGLEABLE, name, "under test only")
    runtime_overrides.set_override(name, declared(3))

    assert _resolved_knobs(GovernancePolicy(guard_rules_enabled={}))[name] == declared(7)


def test_a_toggleable_knob_is_in_the_drift_set() -> None:
    """So flipping one mid-run makes that run unquotable rather than silently mixed.

    Not a limitation to route around: ``enable_clarification_to_draft``'s own declaration says it
    "changes the corpus on disk between two turns of the SAME run". A gate that failed to notice
    would be reporting a rate over a population that does not exist.
    """
    drift = resume_drift_keys()
    for name in runtime_overrides.TOGGLEABLE:
        assert name in drift, (
            f"{name!r} is toggleable but outside resume_drift_keys(), so flipping it mid-run "
            "would pass the configuration-drift gate"
        )


def test_an_override_set_after_boot_reaches_a_turn() -> None:
    """The defect live verification found, and the one this whole module exists to prevent.

    ``_resolved_knobs`` runs once, when the session is built, and ``Session.turn`` copies the
    mapping it produced. So the first cut of this feature wrote the override, reported success, and
    changed nothing: ``serve/nodes/mine_corpus.py`` reads the knob off the turn's state, and the
    turn carried the boot-time value. A switch that says "on" over an engine still doing the old
    thing is exactly the class of control this round was written to end -- it had simply been built
    in reverse, with the working half on the server.

    Asserted on the turn rather than on the session, because the turn is what a node reads.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session

    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    session = Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=GovernancePolicy(guard_rules_enabled={}),
        corpus_content_hash="c", prompt_set_hash="p",
        knobs_resolved={"enable_clarification_to_draft": False},
        db_id="app_store", run_id="r",
    )
    assert session.turn("q")["knobs_resolved"]["enable_clarification_to_draft"] is False

    runtime_overrides.set_override("enable_clarification_to_draft", True)

    assert session.turn("q")["knobs_resolved"]["enable_clarification_to_draft"] is True, (
        "a turn minted after the switch was flipped still carries the boot-time value, so the "
        "node that reads this knob never sees the change"
    )


def test_clearing_an_override_also_reaches_a_turn() -> None:
    """The other half, and the half that was broken.

    Found live: setting a switch took effect immediately, and clearing it did not. The override was
    being applied *twice* — once in ``_resolved_knobs``, which bakes it into the session's cached
    mapping, and again at each read. Layering `{}` over a base that already carried `True` leaves
    `True`. So the base has to stay clean: ``_resolved_knobs`` no longer applies overrides, and the
    two readers that mint a claim — ``Session.turn`` and ``capabilities_for`` — layer them on.

    A switch that turns on but not off is worse than one that does neither, because the operator
    has no way to tell which state the engine is in.
    """
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.retrieve.structure import CorpusStructure
    from governed_bi.serve.session import Session, _resolved_knobs

    runtime_overrides.set_override("enable_clarification_to_draft", True)
    policy = GovernancePolicy(guard_rules_enabled={})

    # The session is built *while the override is on* -- the case that broke.
    structure = CorpusStructure(
        join_edges=frozenset(), references={}, asset_types={}, table_schemas={},
        schema_tags={}, joins_by_edge={},
    )
    session = Session(
        index=None, structure=structure, assets_by_id={}, corpus=None, connector=None,
        policy=policy, corpus_content_hash="c", prompt_set_hash="p",
        knobs_resolved=_resolved_knobs(policy), db_id="app_store", run_id="r",
    )
    assert session.turn("q")["knobs_resolved"]["enable_clarification_to_draft"] is True

    runtime_overrides.clear_override("enable_clarification_to_draft")

    assert session.turn("q")["knobs_resolved"]["enable_clarification_to_draft"] is False, (
        "clearing the switch left the boot-time value behind, so the engine keeps doing the thing "
        "the operator just turned off"
    )
