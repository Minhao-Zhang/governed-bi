"""The TypeScript check's copy of the allowlist is the same allowlist.

``ui/scripts/check-observation-projections.ts`` asserts the client's zod schema parses both
shapes the engine can serve for an observation. It cannot import Python, so it carries its own
copy of :data:`PUBLIC_OBSERVATION_FIELDS` -- and a copy is only safe if something fails when the
two disagree. This is that something.

**The defect this pair exists for.** ``gold_sql``, ``gold_fingerprint`` and ``pred_fingerprint``
sit outside the allowlist, so an engine started without ``GOVERNED_BI_FEEDBACK_ADMIN`` omits them.
The client schema had them ``.nullable()`` without ``.optional()``, so a reader-mode response was
a hard parse failure: 73 rows became 219 zod issues, surfaced to the operator as "response did not
match the expected schema" with nothing naming the fields or the deployment flag behind them.
``docs/openapi.json`` had all three out of ``required`` from the start; the client was the half
that disagreed with the contract, and no gate compared them because ``npm run check:api`` needs a
live engine and never runs in CI.

Direction matters here. A field added to the allowlist and not to the TS list makes the TS check
*weaker* (it stops exercising the field) without failing; a field removed from the allowlist and
left in the TS list makes it claim a shape the engine no longer serves. Neither is visible from
one side, so this asserts set equality rather than a subset either way.
"""

from __future__ import annotations

import re
from pathlib import Path

from governed_bi.api.feedback_routes import PUBLIC_OBSERVATION_FIELDS

CHECK = Path(__file__).resolve().parents[2] / "ui" / "scripts" / "check-observation-projections.ts"


def _reader_fields_in_the_check() -> frozenset[str]:
    source = CHECK.read_text(encoding="utf-8")
    match = re.search(
        r"const READER_FIELDS: readonly string\[\] = \[(.*?)\];", source, re.DOTALL
    )
    assert match, (
        f"{CHECK.name} no longer declares `const READER_FIELDS: readonly string[] = [...]`. "
        "This test reads it by shape, so a rename here is a silent unpinning -- update both."
    )
    return frozenset(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_typescript_check_pins_the_same_allowlist() -> None:
    ts = _reader_fields_in_the_check()
    py = frozenset(PUBLIC_OBSERVATION_FIELDS)
    assert ts == py, (
        "the reader allowlist has drifted between the engine and the client's check.\n"
        f"  only in Python (the TS check stops exercising these): {sorted(py - ts)}\n"
        f"  only in TypeScript (the check claims a shape the engine does not serve): {sorted(ts - py)}"
    )


def test_the_check_still_has_something_to_prove() -> None:
    """If the allowlist ever became every field, the projection check would be vacuous.

    It would parse one shape twice and pass, which is the failure mode the check's own last
    assertion guards on the TypeScript side. Held here too, because this side is the one that
    knows what "every field" means.
    """
    from governed_bi.feedback.events import Observation

    every = {f.name for f in Observation.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    withheld = every - frozenset(PUBLIC_OBSERVATION_FIELDS)
    assert withheld, (
        "PUBLIC_OBSERVATION_FIELDS now names every field on Observation, so the reader and "
        "steward projections are the same shape and the client-side check proves nothing. "
        "That is a disclosure decision, not a refactor."
    )
