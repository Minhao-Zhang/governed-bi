"""``GovernancePolicy``: the security configuration of one turn.

Mostly a typed view of ADR 0006 knobs (defaults from ``register.knobs``). Defaults are
read, never restated. ``UNSET`` knobs (``cost_budget``, ``guard_rules_enabled``) are
carried — ``Unset.__bool__`` raises; use the explicit accessors. ``hard_block_suspect``
differs between benchmark and production so the config hash can tell them apart.

``access_grant`` (ADR 0012) is the one field that is **not** a knob, and it is carried here
rather than added as a keyword to ``check()`` / ``prepare()`` for ADR 0008 D7's reason: an
optional control argument is a control that will be un-wired, and that is precisely what
happened to ``spellings``. Every production caller already threads a policy, so wiring
authorization is a change at the one place the policy is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..ports import OPEN_GRANT, Grant
from ..register.knobs import UNSET, Unset, knob_default
from .functions import PERMITTED_FUNCTIONS

__all__ = ["GovernancePolicy", "DEFAULT_DIALECT"]

#: The dialect assumed when a caller does not thread one from its connector.
#:
#: Not a knob: ``ports.Connector.dialect`` is where the real value comes from.
#: Postgres because the bypass list is Postgres-specific, so it is the stricter
#: default of the two.
DEFAULT_DIALECT = "postgres"


#: The five deterministic rule ids :mod:`.guard` dispatches, as **vocabulary**. The predicates
#: live in ``guard.py``; only the names live here, because the set of legal keys for
#: :attr:`GovernancePolicy.guard_rules_enabled` belongs with the field, and ``guard.py``
#: already imports this module. ``guard.py`` asserts at import that its mapping's keys are
#: exactly this set, so the two cannot drift.
GUARD_RULE_IDS: frozenset[str] = frozenset({
    "g_encoding",
    "g_length",
    "g_instruction_override",
    "g_role_injection",
    "g_tool_forgery",
})

#: The sixth rule id, which is **not** in :data:`GUARD_RULE_IDS` because it is not a
#: deterministic predicate: it asks a model whether the question is a BI task at all, and
#: ``govern/`` must stay importable with no model, no settings and no I/O. The check runs in
#: ``serve/nodes/guard.py``. Re-exported from ``guard.py``, where it used to live, so the
#: eight call sites that import it from there keep working.
BI_SCOPE_RULE_ID = "g_bi_scope"

#: Every id ``guard_rules_enabled`` may name. Both consumers read one mapping over two
#: disjoint namespaces — ``guard()`` iterates :data:`GUARD_RULE_IDS`, ``serve/nodes/guard.py``
#: reads :data:`BI_SCOPE_RULE_ID` — and until 2026-09-18 nothing checked the union.
KNOWN_GUARD_RULE_IDS: frozenset[str] = GUARD_RULE_IDS | {BI_SCOPE_RULE_ID}


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    """The security configuration of one turn. Immutable for the turn's duration."""

    #: The positive function allowlist, as canonical names. Defaults to the
    #: committed list in :mod:`.functions`, whose digest is what the
    #: ``permitted_functions`` knob carries into the config hash.
    permitted_functions: frozenset[str] = field(default_factory=lambda: PERMITTED_FUNCTIONS)
    hard_block_suspect: bool = field(default_factory=lambda: knob_default("hard_block_suspect"))
    graded_delivery_enabled: bool = field(default_factory=lambda: knob_default("graded_delivery_enabled"))
    run_query_attempt_cap: int = field(default_factory=lambda: knob_default("run_query_attempt_cap"))
    max_rows: int = field(default_factory=lambda: knob_default("max_rows"))
    g_length_max_chars: int = field(default_factory=lambda: knob_default("g_length_max_chars"))
    #: ``UNSET`` ⇒ the cost layer does not run, and says so by being absent from
    #: ``layers_evaluated`` rather than by passing.
    cost_budget: int | Unset = field(default_factory=lambda: knob_default("cost_budget"))
    #: ``UNSET`` ⇒ no rule has both of its numbers yet (ADR 0006 OQ3), so
    #: :func:`~governed_bi.govern.guard.guard` refuses to run rather than choosing
    #: between shipping uncalibrated rules and shipping no guard while claiming one.
    guard_rules_enabled: Mapping[str, bool] | Unset = field(
        default_factory=lambda: knob_default("guard_rules_enabled")
    )
    #: Who this turn is executed for, as authorization (ADR 0012). Defaults to
    #: :data:`~governed_bi.ports.OPEN_GRANT`, which authorizes every table, denies no column
    #: and declares no row predicate — so the TABLES and COLUMNS layers behave exactly as
    #: they did before the seam existed. **Not** ``None``-as-open: ADR 0006 G1's "absence is
    #: not permission" applies here too, so openness is a value with a name.
    access_grant: Grant = field(default_factory=lambda: OPEN_GRANT)

    def __post_init__(self) -> None:
        """Refuse a ``guard_rules_enabled`` key that names no rule.

        **The defect this closes.** ``api/graph_app.py`` shipped
        ``guard_rules_enabled={BI_SCOPE_RULE_ID: True}``, which reads as "the guard is on" and
        means "five of the six rules are off": :func:`~governed_bi.govern.guard.guard` iterates
        :data:`GUARD_RULE_IDS`, ``g_bi_scope`` is not a member, and
        :meth:`guard_rule_enabled`'s ``.get(rule_id, False)`` turns every absent id into a
        silent ``False``. So the served surface ran **zero** deterministic predicates from the
        day the scope gate was added, and the only test that read the shipped mapping pinned it
        with ``==`` — it would have failed on the fix and passed on the regression.

        Validating *keys* and not *coverage*: an empty mapping stays legal, because
        :func:`~governed_bi.govern.guard.guard` distinguishes "nothing enabled" from ``UNSET``
        and 44 test sites say the former deliberately. What is refused is a key that dispatches
        nothing — a typo, a retired id, or an id from the other namespace. Coverage is a
        deployment decision and is asserted against the *shipped* policy in
        ``tests/conformance/``, not here.

        Raises at construction rather than at ``guard()``, so all 71 construction sites in the
        tree — tests included — are checked, and a misconfiguration cannot wait for a turn.
        """
        if isinstance(self.guard_rules_enabled, Unset):
            return
        unknown = sorted(set(self.guard_rules_enabled) - KNOWN_GUARD_RULE_IDS)
        if unknown:
            raise ValueError(
                f"guard_rules_enabled names {unknown}, which no rule dispatches. Known ids are "
                f"{sorted(KNOWN_GUARD_RULE_IDS)}. A key that dispatches nothing is silently "
                "false, which is how the served surface came to run none of the five "
                "deterministic rules while reading as though the guard was on."
            )

    def cost_layer_enabled(self) -> bool:
        """Whether the cost layer has a bound to compare against.

        The gate for the cost layer's only caller: :func:`~governed_bi.govern.check._cost`
        reads ``cost_budget`` behind this and asserts on it rather than writing ``or 0``.
        Other readers exist and do not go through here — ``serve/session.py``'s
        ``_resolved_knobs`` records the knob's value, which is a different question from
        whether the layer runs.
        """
        return not isinstance(self.cost_budget, Unset)

    def guard_rule_enabled(self, rule_id: str) -> bool:
        """Whether ``rule_id`` is enabled. Raises while the knob is ``UNSET``."""
        if isinstance(self.guard_rules_enabled, Unset):
            raise TypeError(
                "guard_rules_enabled is UNSET. ADR 0006 OQ3 requires red-team recall and "
                "benign firing rate per rule before it ships enabled, so there is no "
                "honest default: 'all on' ships uncalibrated rules and 'all off' ships "
                "no guard while claiming one. Pass an explicit per-rule mapping."
            )
        return bool(self.guard_rules_enabled.get(rule_id, False))


def _assert_policy_tracks_the_register() -> None:
    """Import-time guard: the two knobs that must stay uncalibrated still are.

    Asserted as an effect on a constructed policy, not against this module's own
    constants: the failure being caught is a default that became a number, which
    would read as an ordinary integer here.
    """
    policy = GovernancePolicy()
    if policy.cost_layer_enabled():  # pragma: no cover - import-time guard
        raise AssertionError(
            "cost_budget has acquired a default. ADR 0006 §13 ships the cost layer "
            "disabled rather than guessed, and a guessed bound is a gate whose "
            "false-refusal rate nobody has measured."
        )
    if not isinstance(policy.guard_rules_enabled, Unset):  # pragma: no cover
        raise AssertionError("guard_rules_enabled has acquired a default; see OQ3.")
    if UNSET is not knob_default("cost_budget"):  # pragma: no cover - import-time guard
        raise AssertionError("register.knobs no longer ships cost_budget as UNSET")
    if not policy.access_grant.is_open:  # pragma: no cover - import-time guard
        raise AssertionError(
            "the default access grant is no longer open. ADR 0012 ships the seam with an "
            "adapter that changes nothing, and a restrictive default would silently move "
            "every measured number in runs/ — including the v4 arm, which is the control. "
            "A deployment that wants one constructs it, and the grant's digest then reaches "
            "knobs_resolved through serve/session.py::_resolved_knobs (ADR 0012 §7), so two "
            "runs under different authorization cannot hash identically."
        )


_assert_policy_tracks_the_register()
