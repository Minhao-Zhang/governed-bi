from __future__ import annotations

import os

from .paths import REPO_ROOT

ROOT = REPO_ROOT

DOTENV = ROOT / ".env"


def _dotenv() -> dict[str, str]:
    """``KEY=value`` and ``#`` comments only. No interpolation: that would be a second dotenv."""
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
    """Environment first, then ``.env``. ``""`` rather than ``None``, so empty is not missing."""
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
    return bool(secret(*names))


def is_secret(name: str) -> bool:
    return name in {*PG_DSN_NAMES, *OPENAI_KEY_NAMES} or name.startswith("LANGSMITH_")


def foreign_dotenv_keys() -> list[str]:
    return sorted(key for key, value in _dotenv().items() if value and not is_secret(key))


def load_into_environ(*, refuse: bool = True) -> int:
    """Existing environment wins. A non-secret key raises and names the key."""
    foreign = foreign_dotenv_keys()
    if refuse and foreign:
        shown = ", ".join(foreign)
        raise RuntimeError(
            f".env sets {shown}, which is not a secret. Settings live in "
            "registry/settings.py; a GOVERNED_BI_* override belongs in the environment."
        )
    filled = 0
    for key, value in _dotenv().items():
        if value and is_secret(key) and not os.environ.get(key):
            os.environ[key] = value
            filled += 1
    return filled


PG_DSN_NAMES = ("GOVERNED_BI_PG_DSN",)

OPENAI_KEY_NAMES = ("OPENAI_API_KEY",)
