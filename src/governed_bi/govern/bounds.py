"""Tool bounds and the licensed set (ADR 0006 §8). Closes the govern half of B7 and B9.

> **A tool that grants privilege must have a bound the model cannot widen.**

**B7 was ``inspect_schema`` writing straight into the licensed set**, so inspecting
anything authorised it — in a pooled corpus, that reaches into unrelated schemas. The
structural fix is that the set is an **explicit output of the ``connect`` node**,
closed for the turn, and that no tool has any way to add to it. :class:`ToolBounds` is
frozen and has no widening method; that is the whole mechanism, and the reason it is a
type rather than a convention is B10 — the routing index embedded governance-excluded
PII columns while the picker summary filtered them, because the caller contract was a
docstring.

**Out-of-scope and non-existent return the identical message.** Two different messages
are an existence oracle: a model that can distinguish "not licensed" from "no such
table" can enumerate the lake one guess at a time.

**B9: a ``thread_id`` is not a capability.** A guessable id was a handle on another
caller's paused clarification, which embeds their question. :func:`resume_authorised`
is the primitive; namespacing and hashing an id is a mitigation, not authentication.

What this module does **not** do: it does not construct ``licensed``. That is ADR
0005's ``connect``, whose accounting includes the Steiner points — a Steiner point
must be licensed or every multi-hop query refuses at the table layer, which is the
thing ``connect`` exists to prevent — and few-shot SQL closure, which pulls in every
table a gold statement touches and is therefore an unaudited licensing expansion
unless it is counted.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

__all__ = ["OUT_OF_SCOPE_MESSAGE", "ToolBounds", "resume_authorised"]

#: The single reply for "you may not" **and** for "there is no such thing".
OUT_OF_SCOPE_MESSAGE = (
    "That identifier is not available in this conversation. Work from the assets in "
    "the context you were given."
)


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """What this turn's tools may touch. Closed at ``connect``, never widened.

    Frozen, and deliberately without an ``add``/``extend``/``with_`` method: the v1
    defect was not that widening was *allowed*, it was that widening was *reachable* —
    ``licensed`` was a mutable set on a shared object and one tool wrote to it. A
    resume from a clarification interrupt continues from the interrupt point and so
    cannot widen it either.
    """

    #: Table keys (``{schema}.{physical_name}``) this turn licenses.
    licensed: frozenset[str] = frozenset()
    #: Asset ids in this turn's ``hits ∪ pulled_in``. ``read_body``'s bound.
    readable_assets: frozenset[str] = frozenset()

    def may_read_body(self, asset_id: str) -> bool:
        return asset_id in self.readable_assets

    def may_inspect_schema(self, table_key: str) -> bool:
        return table_key in self.licensed

    def may_sample(self, column_id: str) -> bool:
        """``sample_rows`` takes a **column id**, not a name (§7).

        v1's signature took a model-supplied ``column`` string and hand-built
        ``SELECT {column} FROM {table}`` — identifiers cannot be bound as parameters,
        so that was a direct injection surface with no parse layer, no function layer,
        no column layer and no ledger entry. The bound is the column's *table*: a
        column id whose table this turn does not license is out of scope.
        """
        table = column_id.rsplit(".", 1)[0]
        return bool(table) and table != column_id and table in self.licensed


def resume_authorised(*, stored_identity: str | None, caller_identity: str | None) -> bool:
    """Whether ``caller_identity`` may resume a run paused by ``stored_identity``.

    Both ``None`` is **not** authorised: an unauthenticated deployment must not get
    cross-caller resume for free, and ``None == None`` is exactly the comparison that
    made v1's ``corpus_content_hash == "unknown"`` pass a gate. Compared with
    :func:`hmac.compare_digest` because the identity is a secret-shaped value and a
    short-circuiting comparison leaks its prefix.
    """
    if not stored_identity or not caller_identity:
        return False
    return hmac.compare_digest(stored_identity, caller_identity)
