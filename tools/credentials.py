"""One reader for credentials, for tests and tools. Never for ``src/``.

On 2026-08-03 three copies of "read a secret from the environment or ``.env``" disagreed about
which sources they read, and parcel I's contract skipped its OpenAI half over a key that was
present in ``.env`` the whole time. The failure mode of a duplicated credential reader is a
quiet reduction in what ran, not a crash.

Not in ``src/``, deliberately: a library that reads ``.env`` decides its own configuration
behind its caller's back. Production code takes a DSN or a client.

:func:`secret` returns the value; :func:`have` answers yes/no without it, so a caller that only
needs presence cannot interpolate a credential into a skip message.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The repository root, resolved from this file rather than the working directory, so a test
#: invoked from a subdirectory finds the same ``.env`` as one invoked from the root.
ROOT = Path(__file__).resolve().parent.parent

#: The dotenv file. Git-ignored and the developer's to manage: nothing here writes to it.
DOTENV = ROOT / ".env"


def _dotenv() -> dict[str, str]:
    """Parse ``.env`` into a mapping. Absent file is an empty mapping, not an error.

    Deliberately minimal: ``KEY=value``, ``#`` comments, optional surrounding quotes. No
    interpolation, no ``export``, no multi-line values — a fuller parser would be a second
    implementation of python-dotenv.
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

    Environment first so a caller can override the file for one run without editing it. Several
    names because a secret has aliases, and the *order* is the precedence.

    Returns ``""``, not ``None``: a falsy string answers "is there one" without inviting a
    ``None`` check that passes for an empty value.
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

    For skip conditions and capability flags, so a caller that only needs presence cannot
    interpolate a credential into a CI log.
    """
    return bool(secret(*names))


def load_into_environ() -> int:
    """Fill **unset** environment variables from ``.env``. Returns how many were filled.

    For process entry points, and the only thing that works for third-party libraries: they read
    ``os.environ`` directly, so :func:`have` can report a key present and the library still not
    see it. That happened twice on 2026-08-03.

    Existing environment always wins, so ``OPENAI_API_KEY=... python -m ...`` overrides the file
    for one run. Nothing is printed — a loader that echoes what it found logs credentials.
    """
    for key, value in _dotenv().items():
        if value and not os.environ.get(key):
            os.environ[key] = value
    return sum(1 for k, v in _dotenv().items() if v and os.environ.get(k) == v)


#: The Postgres DSN's names, in precedence order. Declared once rather than at each call site:
#: an alias list is the kind of thing that grows in one copy and not the other.
PG_DSN_NAMES = ("GOVERNED_BI_PG_DSN", "PG_RENAME_DECOY_DSN")

#: The OpenAI key's name. A tuple for symmetry with :data:`PG_DSN_NAMES`.
OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)
