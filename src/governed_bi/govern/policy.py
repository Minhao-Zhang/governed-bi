"""Defaults come from ``registry.settings``. ``guard_rules_enabled`` stays ``UNSET``. ``Unset.__bool__`` raises."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..registry.settings import Unset, setting_default
from .functions import PERMITTED_FUNCTIONS

__all__ = ["GovernancePolicy", "DEFAULT_DIALECT"]

#: Not a setting: the connector owns the real dialect. Postgres is the default because
#: the bypass list is Postgres-specific, so it is the stricter of the two.
DEFAULT_DIALECT = "postgres"


#: Names :func:`guard` dispatches. Predicates live in ``guard.py``; names live here because
#: legal keys belong with the field, and ``guard.py`` imports this module. Import asserts
#: the two sets match.
GUARD_RULE_IDS: frozenset[str] = frozenset({
    "g_encoding",
    "g_length",
    "g_instruction_override",
    "g_role_injection",
    "g_tool_forgery",
})

#: Not in :data:`GUARD_RULE_IDS`: it asks a model whether the question is a BI task, and
#: this package stays importable with no model. The check runs in ``serve/nodes/guard.py``.
#: Re-exported from ``guard.py`` so existing imports keep working.
BI_SCOPE_RULE_ID = "g_bi_scope"

#: Every id ``guard_rules_enabled`` may name. ``guard()`` and the scope check read disjoint
#: subsets; a key in neither dispatches nothing.
KNOWN_GUARD_RULE_IDS: frozenset[str] = GUARD_RULE_IDS | {BI_SCOPE_RULE_ID}


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    #: Canonical names. Defaults to :mod:`.functions`, whose digest is what the
    #: ``permitted_functions`` setting carries into the config hash.
    permitted_functions: frozenset[str] = field(default_factory=lambda: PERMITTED_FUNCTIONS)
    hard_block_suspect: bool = field(default_factory=lambda: setting_default("hard_block_suspect"))
    graded_delivery_enabled: bool = field(default_factory=lambda: setting_default("graded_delivery_enabled"))
    run_query_attempt_cap: int = field(default_factory=lambda: setting_default("run_query_attempt_cap"))
    max_rows: int = field(default_factory=lambda: setting_default("max_rows"))
    g_length_max_chars: int = field(default_factory=lambda: setting_default("g_length_max_chars"))
    #: ``UNSET`` until both calibration numbers exist per rule (ADR 0006 OQ3). ``guard``
    #: then refuses to run: all-on ships uncalibrated rules, all-off claims a guard that
    #: is not running.
    guard_rules_enabled: Mapping[str, bool] | Unset = field(
        default_factory=lambda: setting_default("guard_rules_enabled")
    )

    def __post_init__(self) -> None:
        """Unknown keys raise here. An empty mapping stays legal; shipped-policy coverage is a conformance assertion."""
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

    def guard_rule_enabled(self, rule_id: str) -> bool:
        """Raises while ``UNSET``. There is no default. See ADR 0006."""
        if isinstance(self.guard_rules_enabled, Unset):
            raise TypeError(
                "guard_rules_enabled is UNSET. ADR 0006 OQ3 requires red-team recall and "
                "benign firing rate per rule before it ships enabled, so there is no "
                "honest default: 'all on' ships uncalibrated rules and 'all off' ships "
                "no guard while claiming one. Pass an explicit per-rule mapping."
            )
        return bool(self.guard_rules_enabled.get(rule_id, False))


def _assert_policy_tracks_the_register() -> None:
    """``guard_rules_enabled`` stays uncalibrated until a caller sets it."""
    policy = GovernancePolicy()
    if not isinstance(policy.guard_rules_enabled, Unset):  # pragma: no cover
        raise AssertionError("guard_rules_enabled has acquired a default; see OQ3.")


_assert_policy_tracks_the_register()
