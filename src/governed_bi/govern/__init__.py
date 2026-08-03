"""Execution-time governance: everything between "the agent produced a string" and
"the database saw a statement" (ADR 0006).

Layer 6. May import ``ports`` and ``register``; imports nothing later, and imports no
model, no connector and no corpus — which is what makes the whole layer testable with
a SQL string and nothing else.

The four invariants, because every module here is shaped by one of them:

* **G1. Absence refuses.** Every security parameter is required. There is no code path
  where a missing argument means "skip". A function that cannot evaluate its own
  precondition returns ``blocked``.
* **G2. Every executor is enumerated, passes ``check()``, and writes a ledger entry.**
  Four of them (:data:`~governed_bi.govern.ledger.EXECUTOR_PATHS`), not "one choke
  point" — that was aspirational, and the ADR's own first draft contradicted it in its
  own tool table.
* **G3. Permission is proven, never inferred.** ``failed_layer=None`` never means
  safe. Graded delivery keys on a positively established verdict field.
* **G4. The string checked is the string executed.** :mod:`.pipeline` fixes the order
  of the three transformations and the ledger hashes the exact executed text.

Module map:

===================  ==========================================================
:mod:`.layers`       the ``Layer`` enum, ``CheckVerdict``, the rule → layer table
:mod:`.policy`       ``GovernancePolicy``: ADR 0006's knobs, read from the register
:mod:`.functions`    the positive function allowlist, keyed on sqlglot classes
:mod:`.identifiers`  path validation (B8), folding (B5), the two key shapes
:mod:`.scopes`       one per-scope traversal, shared by two layers
:mod:`.binding`      the one positive binding rule (B6 and its siblings)
:mod:`.check`        the seven layers in order, and the exception-to-block wrapper
:mod:`.guard`        the deterministic input gate
:mod:`.pipeline`     normalise → canonicalise → check → limit
:mod:`.bounds`       tool bounds and the licensed set (B7, B9)
:mod:`.ledger`       the durable projection, and what measurement must see
===================  ==========================================================
"""

from __future__ import annotations

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
from .ledger import AttemptRecord, ExecutionRecord, attempt_record, ledger_entry
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
    "ledger_entry",
    "permitted_functions_digest",
    "prepare",
    "resume_authorised",
    "table_key",
]
