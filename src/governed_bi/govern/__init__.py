"""Execution-time governance between agent SQL and the database. See ADR 0006. May import ``ports``, ``register``, and
``corpus`` only. A missing security argument refuses; the string checked is the string executed."""

from __future__ import annotations

from .binding import Bindings, ColumnBinding, LayerRefusal, bind
from .bounds import OUT_OF_SCOPE_MESSAGE, ToolBounds
from .check import GovernanceUsageError, check, graded_delivery_eligible
from .functions import (
    ADVERSARIAL_SET,
    PERMITTED_FUNCTIONS,
    canonical_function_name,
    permitted_functions_digest,
)
from .guard import GUARD_PUBLIC_MESSAGE, GuardResult, guard
from .identifiers import column_key, fold, is_valid_schema_id, table_key
from .layers import RULES, CheckResult, Layer
from .ledger import AttemptRecord, ExecutionRecord, attempt_record
from .pipeline import Prepared, prepare
from .policy import GovernancePolicy

__all__ = [
    "ADVERSARIAL_SET",
    "AttemptRecord",
    "Bindings",
    "CheckResult",
    "ColumnBinding",
    "ExecutionRecord",
    "GUARD_PUBLIC_MESSAGE",
    "GovernancePolicy",
    "GovernanceUsageError",
    "GuardResult",
    "Layer",
    "LayerRefusal",
    "OUT_OF_SCOPE_MESSAGE",
    "PERMITTED_FUNCTIONS",
    "Prepared",
    "RULES",
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
    "table_key",
]
