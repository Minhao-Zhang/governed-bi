"""Execution-time governance between agent SQL and the database (ADR 0006).

May import ``ports``, ``register``, ``corpus``; no model, no connector.

* G1 Absence refuses — missing security args never mean "skip".
* G2 Every executor in :data:`~.ledger.EXECUTOR_PATHS` passes ``check()`` and ledgers.
* G3 Permission is proven — ``failed_layer=None`` never means safe.
* G4 The string checked is the string executed (:mod:`.pipeline` + ledger hash).
"""

from __future__ import annotations

from .access import (
    LOCAL_PRINCIPAL,
    OPEN_RESOLVED,
    OpenAccessPolicy,
    ResolvedGrant,
    StaticRoleAccessPolicy,
    resolve_grant,
)
from .binding import Bindings, ColumnBinding, LayerRefusal, bind
from .bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds, resume_authorised
from .check import GovernanceUsageError, check, graded_delivery_eligible
from .functions import (
    ADVERSARIAL_SET,
    PERMITTED_FUNCTIONS,
    canonical_function_name,
    permitted_functions_digest,
)
from .guard import GUARD_PUBLIC_MESSAGE, GuardVerdict, guard
from .identifiers import column_key, fold, is_valid_schema_id, table_key
from .layers import RULES, CheckVerdict, Layer
from .ledger import AttemptRecord, ExecutionRecord, attempt_record
from .pipeline import Prepared, prepare
from .policy import GovernancePolicy

__all__ = [
    "ADVERSARIAL_SET",
    "AttemptRecord",
    "Bindings",
    "CheckVerdict",
    "ColumnBinding",
    "ExecutionRecord",
    "GUARD_PUBLIC_MESSAGE",
    "GovernancePolicy",
    "GovernanceUsageError",
    "GuardVerdict",
    "LOCAL_PRINCIPAL",
    "Layer",
    "LayerRefusal",
    "OPEN_RESOLVED",
    "OUT_OF_SCOPE_MESSAGE",
    "OpenAccessPolicy",
    "PERMITTED_FUNCTIONS",
    "Prepared",
    "RULES",
    "ResolvedGrant",
    "StaticRoleAccessPolicy",
    "ToolBounds",
    "attempt_record",
    "bind",
    "canonical_function_name",
    "check",
    "column_key",
    "fold",
    "graded_delivery_eligible",
    "guard",
    "is_valid_schema_id",
    "permitted_functions_digest",
    "prepare",
    "resolve_grant",
    "resume_authorised",
    "table_key",
]
