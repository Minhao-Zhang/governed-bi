"""``GovernancePolicy``: typed view of ADR 0006 knobs (defaults from ``register.knobs``).

Defaults are read, never restated. ``UNSET`` knobs (``cost_budget``,
``guard_rules_enabled``) are carried — ``Unset.__bool__`` raises; use the
explicit accessors. ``hard_block_suspect`` differs between benchmark and
production so the config hash can tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..register.knobs import UNSET, Unset, knob_default
from .functions import PERMITTED_FUNCTIONS

__all__ = ["GovernancePolicy", "DEFAULT_DIALECT"]

#: The dialect assumed when a caller does not thread one from its connector.
#:
#: Not a knob: it is a fact about the datasource, and ``ports.Connector.dialect``
#: is where the real value comes from. Postgres because the obfuscated BIRD
#: databases are Postgres-only, and because the bypass list is Postgres-specific —
#: a stricter default than SQLite in the one dimension that matters.
DEFAULT_DIALECT = "postgres"


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

    def cost_layer_enabled(self) -> bool:
        """Whether the cost layer has a bound to compare against.

        The only reader of ``cost_budget``, so ``UNSET`` is handled in exactly one
        place instead of at every branch that might have written ``or 0``.
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

    Asserted as an **effect** on a constructed policy, not by comparing this module
    against its own constant: a default that silently became a number is the failure
    this catches, and it would read as a perfectly ordinary integer here.
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


_assert_policy_tracks_the_register()
