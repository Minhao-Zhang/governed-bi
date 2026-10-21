"""Secrets: the environment, then ``.env``. Settings are not read here.

A ``.env`` key that is not a secret stops startup and names the key. Values are
never printed. Entry points call :func:`load_into_environ` once; libraries take a
DSN or a client.
"""

from __future__ import annotations

import os

from .paths import REPO_ROOT

#: The repository root, resolved from this file rather than the working directory, so a test
#: invoked from a subdirectory finds the same ``.env`` as one invoked from the root.
ROOT = REPO_ROOT

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


def is_secret(name: str) -> bool:
    """``OPENAI_API_KEY``, the DSN names, and ``LANGSMITH_*``. Nothing else."""
    return name in {*PG_DSN_NAMES, *OPENAI_KEY_NAMES} or name.startswith("LANGSMITH_")


def foreign_dotenv_keys() -> list[str]:
    """``.env`` keys that are set and are not secrets. Startup names these and stops."""
    return sorted(key for key, value in _dotenv().items() if value and not is_secret(key))


def load_into_environ(*, refuse: bool = True) -> int:
    """Copy unset secrets from ``.env`` into the environment. Returns how many were copied.

    Existing environment wins. With ``refuse``, a non-secret key raises and names the key.
    Third-party clients read the process environment, so the secret has to be there, not only in the file.
    """
    foreign = foreign_dotenv_keys()
    if refuse and foreign:
        shown = ", ".join(foreign)
        raise RuntimeError(
            f".env sets {shown}, which is not a secret. Settings live in "
            "register/knobs.py; a GOVERNED_BI_* override belongs in the environment."
        )
    filled = 0
    for key, value in _dotenv().items():
        if value and is_secret(key) and not os.environ.get(key):
            os.environ[key] = value
            filled += 1
    return filled


#: The Postgres DSN's names, in precedence order. Declared once rather than at each call site:
#: an alias list is the kind of thing that grows in one copy and not the other.
PG_DSN_NAMES = ("GOVERNED_BI_PG_DSN", "PG_RENAME_DECOY_DSN")

#: The OpenAI key's name. A tuple for symmetry with :data:`PG_DSN_NAMES`.
OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)
