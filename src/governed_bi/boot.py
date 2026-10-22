from __future__ import annotations

from typing import Any

from . import credentials
from .registry.settings import bind_settings
from .registry.settings import resolve_settings as resolve_from_environ

__all__ = ["resolve_settings"]


def resolve_settings() -> dict[str, Any]:
    credentials.load_into_environ()
    resolved = resolve_settings_from_environ()
    return bind_settings(resolved)


def resolve_settings_from_environ() -> dict[str, Any]:
    return resolve_from_environ()
