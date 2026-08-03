"""One reader for credentials, for tests and tools. Never for ``src/``.

**Why this exists.** On 2026-08-03 there were three copies of "read a secret from the
environment or ``.env``" and they did not agree: `tests/serve/turn_contract_fixtures.py`
read both sources, `tests/embedders.py` read only `os.environ`, and
`tools/load_demo_schema.py` read both again. The consequence was not a crash. It was that
parcel I's contract **skipped its OpenAI half over a key that was present in `.env` the whole
time**, and said in capitals that it had only exercised one of two adapters — a true
statement about a false situation.

That is the shape `tools/check_one_implementation.py` exists to catch: two implementations of
one concept, differing in a way nobody reads until it costs coverage. A credential reader is
an unusually bad place for it, because the failure mode is a *quiet reduction in what ran*.

**Not in `src/`, deliberately.** `tools/check_imports.py` keeps the library layered, and a
library that reads `.env` decides its own configuration behind its caller's back. Production
code takes a DSN or a client; only the test suite and the developer-facing tools go looking
for one.

**Values are never returned to a log.** :func:`secret` returns the value to its caller and
:func:`have` answers yes/no without it, so a caller that only needs presence — a skip
condition, a capability flag — cannot accidentally interpolate a credential into a message.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The repository root, resolved from this file rather than the working directory, so a test
#: invoked from a subdirectory finds the same ``.env`` as one invoked from the root.
ROOT = Path(__file__).resolve().parent.parent

#: The dotenv file. Git-ignored, and its contents are the developer's to manage: nothing here
#: writes to it.
DOTENV = ROOT / ".env"


def _dotenv() -> dict[str, str]:
    """Parse ``.env`` into a mapping. Absent file is an empty mapping, not an error.

    Deliberately minimal: ``KEY=value``, ``#`` comments, optional surrounding quotes. No
    interpolation, no ``export``, no multi-line values. A fuller parser would be a second
    implementation of python-dotenv, and the failure mode of guessing wrong here is a
    credential that silently reads as absent — which is the defect this module was written to
    remove.
    """
    if not DOTENV.exists():
        return {}
    out: dict[str, str] = {}
    for line in DOTENV.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def secret(*names: str) -> str:
    """The first non-empty value among ``names``, from the environment then ``.env``.

    Environment first so a caller can override the file for one run without editing it.
    Several names because a secret legitimately has aliases — the Postgres DSN is
    ``GOVERNED_BI_PG_DSN`` or ``PG_RENAME_DECOY_DSN`` — and the *order* is the precedence.

    Returns ``""`` when nothing is set. Not ``None``: every caller here immediately asks
    "is there one", and a falsy string answers that without inviting a `None` check that
    passes for an empty value.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return str(value)
    parsed = _dotenv()
    for name in names:
        value = parsed.get(name)
        if value:
            return value
    return ""


def have(*names: str) -> bool:
    """Whether :func:`secret` would find one — without handing the value to the caller.

    For skip conditions and capability flags. The point is that a caller which only needs
    presence cannot interpolate a credential into a skip message by accident, which is how a
    secret reaches a terminal scrollback or a CI log.
    """
    return bool(secret(*names))


#: The Postgres DSN's names, in precedence order. Declared here rather than repeated at each
#: call site: an alias list is exactly the kind of thing that grows in one copy and not the
#: other, which is the bug this module exists for.
PG_DSN_NAMES = ("GOVERNED_BI_PG_DSN", "PG_RENAME_DECOY_DSN")

#: The OpenAI key's name. A tuple for symmetry with :data:`PG_DSN_NAMES`, so callers use one
#: spelling of the idiom.
OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)
