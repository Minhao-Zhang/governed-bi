"""One startup read of settings and secrets, shared by every entry point.

Settings come from :mod:`governed_bi.registry.settings` (code defaults, then
``GOVERNED_BI_*`` in the process environment). Secrets come from the environment,
then ``.env``. A ``.env`` key that is not a secret stops startup and names the key.
"""

from __future__ import annotations

from typing import Any

from . import credentials
from .registry.settings import bind_settings
from .registry.settings import resolve_settings as resolve_from_environ

__all__ = ["resolve_settings"]


def resolve_settings() -> dict[str, Any]:
    """Load secrets, refuse foreign ``.env`` keys, and bind the settings for this process."""
    credentials.load_into_environ()
    resolved = resolve_settings_from_environ()
    return bind_settings(resolved)


def resolve_settings_from_environ() -> dict[str, Any]:
    """Settings from the process environment. Does not touch ``.env`` or the binding."""
    return resolve_from_environ()
