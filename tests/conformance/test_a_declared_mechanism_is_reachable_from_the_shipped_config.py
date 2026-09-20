"""Every closed vocabulary must say whether the shipped configuration reaches it.

**The class, named after the defect that made it visible.** ``api/graph_app.py`` shipped
``guard_rules_enabled={BI_SCOPE_RULE_ID: True}``. That reads as "the guard is on" and meant
five of six rules were off: ``g_bi_scope`` is not a member of ``GUARD_RULES``,
``guard()`` iterates that mapping, and ``guard_rule_enabled``'s ``.get(rule_id, False)`` turns
every absent id into a silent ``False``. All five rules were 100% line-covered throughout, by
a fixture that built its own all-on policy.

``tools/check_declared_is_consumed.py`` gates the adjacent class — declared → *consumed by
code* — over knobs, record fields and state channels. It could not catch this, and correctly:
the five rules **are** consumed by ``guard()``. They are never *enabled*. That is a property
of a constructed object, not of the source, so it cannot be an AST gate.

Two mechanisms hold it instead. ``GovernancePolicy.__post_init__`` refuses a key naming no
rule, which catches a typo at all 71 construction sites including tests. And this file, which
is the **inventory**: one entry per closed vocabulary dispatched through an open mapping,
saying where its shipped reachability is asserted. A vocabulary added without an entry fails
:func:`test_every_closed_vocabulary_is_in_the_inventory`, which is the only part of this that
scales — the individual assertions are cheap, remembering to write one is not.

Not every entry has to be *reached*. ``COST`` ships off and ``policy.py`` asserts it stays
off; that is a decision with a guard behind it, which is the opposite of an accident. What is
refused is a vocabulary whose reachability nobody wrote down either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

#: ``vocabulary -> where its shipped reachability is established``.
#:
#: The value is prose read by a person; the key is what the test below enumerates. A new
#: closed vocabulary that dispatches through a mapping keyed by id belongs here on the day it
#: is written, which is the day somebody still knows whether the shipped config reaches it.
INVENTORY: dict[str, str] = {
    "GUARD_RULE_IDS": (
        "serve_policy enables every member; asserted behaviourally by "
        "tests/api/test_the_served_surface_is_not_the_benchmark_arm.py and as a key-space "
        "invariant by GovernancePolicy.__post_init__"
    ),
    "BI_SCOPE_RULE_ID": (
        "enabled by serve_policy with the five above. Not in GUARD_RULES on purpose — it is a "
        "model call and govern/ must import with no model — which is exactly why the two "
        "namespaces sharing one mapping needed a validator"
    ),
    "Layer": (
        "COST ships disabled (cost_budget is UNSET) and policy.py's import-time guard asserts "
        "it stays that way, so its unreachability is a decision rather than an omission. Every "
        "other layer owns at least one adversarial attack, asserted by "
        "tests/govern/test_adversarial_suite.py::test_every_layer_that_owns_an_attack_catches_all_of_them"
    ),
    "CASE_FAMILIES": (
        "every family must hold cases, asserted by "
        "tests/govern/test_adversarial_suite.py::test_the_suite_declares_both_halves_and_every_family"
    ),
    "AssetType": (
        "SUMMARY_CAP is built as a comprehension over the enum, so closure is a property of "
        "construction. conform/rules_asset.py::_closed asserted it anyway and was deleted "
        "2026-09-18: it could not fail, and nothing called it"
    ),
    "REFUSED_BY_TO_STAGE": (
        "every value needs a reader-facing sentence, asserted by "
        "tests/api/test_the_refusal_phrasing_covers_the_vocabulary.py"
    ),
}


def test_the_served_policy_reaches_every_deterministic_guard_rule() -> None:
    """The instance the class is named after, asserted against the object the server builds.

    Not against a literal: comparing the shipped mapping to a dict is what the suite did
    before, and it could not tell a fix from a regression.
    """
    from governed_bi.api.graph_app import serve_policy
    from governed_bi.govern.policy import KNOWN_GUARD_RULE_IDS

    policy = serve_policy(ROOT)
    unreachable = sorted(
        rule for rule in KNOWN_GUARD_RULE_IDS if not policy.guard_rule_enabled(rule)
    )
    assert not unreachable, (
        f"the served policy runs none of {unreachable}. An id absent from "
        "guard_rules_enabled is silently False, which is how five rules that were fully "
        "line-covered ran on no served turn at all"
    )


def test_an_access_policy_no_principal_can_hold_is_refused_at_startup() -> None:
    """A role nobody holds is a deployment that starts, looks configured, and refuses all.

    ``StaticRoleAccessPolicy.grant_for`` returns ``Grant()`` — ``reach=listed``, no tables —
    for a principal holding none of the declared roles. Fail-closed, and silently: every
    query refuses and nothing names the mismatch. ``authenticated_principal`` returns one
    constant holding one role, so "a role nobody holds" is every role except that one.

    Refused rather than warned for ``_require_keys``' reason: a policy file is read once and
    enforced thousands of times, and there is no working deployment on the other side of the
    warning.
    """
    import tempfile

    from governed_bi.api.graph_app import ACCESS_POLICY_VAR, resolve_access_grant

    directory = Path(tempfile.mkdtemp(prefix="reachable-role-"))
    unreachable = directory / "unreachable.toml"
    unreachable.write_text(
        'version="1"\n[role.analyst]\ntables=["sales.orders"]\n', encoding="utf-8"
    )
    reachable = directory / "reachable.toml"
    reachable.write_text(
        'version="1"\n[role.local]\ntables=["sales.orders"]\n', encoding="utf-8"
    )

    import os

    previous = os.environ.get(ACCESS_POLICY_VAR)
    try:
        os.environ[ACCESS_POLICY_VAR] = str(unreachable)
        with pytest.raises(RuntimeError, match="No declared role is reachable"):
            resolve_access_grant(ROOT)

        os.environ[ACCESS_POLICY_VAR] = str(reachable)
        grant = resolve_access_grant(ROOT)
        assert "sales.orders" in grant.tables, (
            "a role the principal does hold must still load; the check is about the "
            "intersection being empty, not about roles being unusual"
        )
    finally:
        if previous is None:
            os.environ.pop(ACCESS_POLICY_VAR, None)
        else:
            os.environ[ACCESS_POLICY_VAR] = previous


def test_every_closed_vocabulary_is_in_the_inventory() -> None:
    """The part that scales. A new vocabulary with no entry fails here.

    Enumerated from the modules that own them rather than hardcoded, so adding a member to
    one is free and adding a *vocabulary* is not. The individual assertions above are cheap;
    what is expensive is remembering that a new closed set needs one at all.
    """
    from governed_bi.govern.adversarial import CASE_FAMILIES
    from governed_bi.govern.layers import Layer
    from governed_bi.govern.policy import BI_SCOPE_RULE_ID, GUARD_RULE_IDS
    from governed_bi.register.assets import AssetType
    from governed_bi.register.stages import REFUSED_BY_TO_STAGE

    live = {
        "GUARD_RULE_IDS": GUARD_RULE_IDS,
        "BI_SCOPE_RULE_ID": {BI_SCOPE_RULE_ID},
        "Layer": {layer.name for layer in Layer},
        "CASE_FAMILIES": CASE_FAMILIES,
        "AssetType": {member.value for member in AssetType},
        "REFUSED_BY_TO_STAGE": set(REFUSED_BY_TO_STAGE),
    }

    assert set(live) == set(INVENTORY), (
        f"closed vocabularies with no inventory entry: {sorted(set(live) - set(INVENTORY))}; "
        f"entries naming nothing: {sorted(set(INVENTORY) - set(live))}. Each entry says where "
        "the shipped configuration's reach over that vocabulary is established — which is the "
        "question nothing asked when five guard rules shipped disabled and fully covered"
    )
    for name, members in live.items():
        assert members, f"{name} is empty, so its inventory entry describes nothing"
        assert INVENTORY[name].strip(), f"{name}'s entry says nothing"
