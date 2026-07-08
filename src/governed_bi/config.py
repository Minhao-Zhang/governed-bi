"""Environment toggles + reusable numbers.

Environments are **toggles, not architecture forks** (Architecture §9). The same
code runs in both; only these switches differ. Bake the abstractions in now so
prod is a config flip, not a rewrite.

The numeric defaults are the "reusable numbers" starting points (Architecture
§7); tune against the BIRD-Obfuscation eval before trusting them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Environment(str, Enum):
    """Dev/test runs on BIRD; prod runs at enterprise scale. See Architecture §9."""

    dev = "dev"  # BIRD: auto-accept corpus, single all-access identity, files + SQLite
    prod = "prod"  # enterprise: PR + owner + CI, real user + RLS, service fleet


@dataclass(frozen=True)
class MemoryBudget:
    """Per-route memory injection budget (Profile / Episodic / Correction)."""

    profile: int
    episodic: int
    correction: int


# Architecture §7 route memory budgets.
ROUTE_MEMORY_BUDGETS: dict[str, MemoryBudget] = {
    "nl2sql": MemoryBudget(5, 2, 5),
    "kpi_lookup": MemoryBudget(2, 0, 1),
    "knowledge_qa": MemoryBudget(3, 1, 1),
    "deep_analysis": MemoryBudget(8, 8, 4),
}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Construct via ``Settings.for_env(...)``."""

    environment: Environment

    # ── D6 human gate ──
    auto_accept_corpus: bool  # dev: True (adversary is sole reviewer); prod: False (PR + owner + CI)

    # ── D7 identity / RLS ──
    single_all_access_identity: bool  # dev: True; prod: False (real user + gateway RLS)

    # ── Server: suspect-column enforcement (Server §"three points" #1) ──
    hard_block_suspect_columns: bool  # dev/BIRD: True; prod/enterprise: soft-warn + drop reliability tier

    # ── Memory (D8) — working always on; episodic/correction off until eval earns it ──
    working_memory: bool = True
    episodic_memory: bool = False
    correction_memory: bool = False

    # ── Reusable numbers (Architecture §7; tune on BIRD first) ──
    profile_ttl_days: int = 365
    episodic_ttl_days: int = 90
    episodic_decay_per_day: float = 0.02
    correction_ttl_days: int = 180
    sql_cache_ttl_minutes: int = 15
    cache_hit_cosine_gate: float = 0.92
    few_shot_recall_cosine_gate: float = 0.95
    few_shot_recall_confidence_gate: float = 0.90
    few_shot_recall_max_fail_count: int = 3

    route_memory_budgets: dict[str, MemoryBudget] = field(
        default_factory=lambda: dict(ROUTE_MEMORY_BUDGETS)
    )

    @classmethod
    def for_env(cls, environment: Environment | str) -> "Settings":
        env = Environment(environment)
        if env is Environment.dev:
            return cls(
                environment=env,
                auto_accept_corpus=True,
                single_all_access_identity=True,
                hard_block_suspect_columns=True,
            )
        return cls(
            environment=env,
            auto_accept_corpus=False,
            single_all_access_identity=False,
            hard_block_suspect_columns=False,
        )
