"""Untrusted mapping ↔ typed asset.

Unknown keys error (except :class:`~.schema.Audit`). No validation here —
:mod:`.validate` judges constructed assets. Defaults omitted on write so round
trips stay equal.
"""


from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import fields
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from .schema import Asset, class_for

__all__ = ["from_mapping", "to_mapping"]

#: ``type(None)``, bound once so the union walker below reads as prose.
_NONE = type(None)



def _coerce(value: Any, annotation: Any, *, where: str) -> Any:
    """One value into its declared type, or ``ValueError``.

    Generic over the annotation rather than a per-class conversion table, because a
    per-class table is a second declaration of the field list and would have to
    agree with the dataclass.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        if value is None:
            return None
        inner = [a for a in get_args(annotation) if a is not _NONE]
        return _coerce(value, inner[0], where=where)
    if origin is tuple:
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError(f"{where}: expected a list, got {type(value).__name__}")
        item = get_args(annotation)[0]
        return tuple(_coerce(v, item, where=f"{where}[]") for v in value)
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise ValueError(f"{where}: expected a mapping, got {type(value).__name__}")
        return dict(value)
    if isinstance(annotation, type):
        if dataclasses.is_dataclass(annotation):
            if not isinstance(value, Mapping):
                raise ValueError(f"{where}: expected a mapping, got {type(value).__name__}")
            return _build(annotation, value, where=where)
        if issubclass(annotation, Enum):
            try:
                return annotation(value)
            except ValueError:
                allowed = ", ".join(str(m.value) for m in annotation)
                raise ValueError(f"{where}: {value!r} is not one of: {allowed}") from None
        if annotation is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{where}: expected true/false, got {value!r}")
            return value
        if annotation is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{where}: expected an integer, got {value!r}")
            return value
        if annotation is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{where}: expected a number, got {value!r}")
            return float(value)
        if annotation is str:
            if not isinstance(value, str):
                raise ValueError(f"{where}: expected a string, got {type(value).__name__}")
            return value
    return value


def _build(cls: type, raw: Mapping[str, Any], *, where: str = "") -> Any:
    """Construct ``cls`` from ``raw``. Unknown keys are an error.

    Rejecting unknown keys is v1's ``extra="forbid"`` and it stays: a mistyped
    field name that parses is a field nobody writes and nothing reads. The one
    exception is a class declaring a field literally named ``extra`` -- see
    :class:`Audit`.
    """
    hints = get_type_hints(cls)
    declared = {f.name: f for f in fields(cls)}
    prefix = f"{where}." if where else ""
    extra_sink = "extra" if "extra" in declared else None

    unknown = sorted(set(raw) - set(declared) - {"asset_type"})
    if unknown and extra_sink is None:
        raise ValueError(
            f"{cls.__name__}: unknown field(s) {unknown}. A mistyped field name that "
            "parses is a field nobody writes and nothing reads."
        )

    kwargs: dict[str, Any] = {}
    for name, spec in declared.items():
        if name == extra_sink:
            kwargs[name] = {k: raw[k] for k in unknown}
            continue
        if name not in raw or raw[name] is None:
            has_default = (
                spec.default is not dataclasses.MISSING
                or spec.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            )
            if not has_default:
                raise ValueError(f"{cls.__name__}: missing required field {prefix}{name!r}")
            continue
        kwargs[name] = _coerce(raw[name], hints[name], where=f"{prefix}{name}")
    return cls(**kwargs)


def from_mapping(raw: Mapping[str, Any]) -> Asset:
    """A raw mapping into the typed asset its ``asset_type`` names.

    Raises ``ValueError`` for anything it cannot build. It **does not validate**:
    the rules live in :mod:`.validate`, so that a constructed-but-wrong asset is
    representable and ``problems_with`` has something to find.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"expected a mapping, got {type(raw).__name__}")
    if "asset_type" not in raw:
        raise ValueError("no asset_type: the discriminator decides which of the eight this is")
    return _build(class_for(raw["asset_type"]), raw)


def to_mapping(asset: Asset) -> dict[str, Any]:
    """An asset back to a YAML-ready mapping, with defaults omitted.

    Defaults are omitted so a written file shows what was decided rather than the
    whole schema, and so a round trip through :func:`from_mapping` returns an equal
    asset.
    """
    out: dict[str, Any] = {"asset_type": asset.asset_type.value}
    out.update(_unbuild(asset))
    return out


def _unbuild(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec in fields(obj):
        value = getattr(obj, spec.name)
        default = (
            spec.default_factory()  # type: ignore[misc]
            if spec.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            else spec.default
        )
        if default is not dataclasses.MISSING and value == default:
            continue
        if spec.name == "extra" and isinstance(value, Mapping):
            out.update(value)
            continue
        out[spec.name] = _plain(value)
    return out


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _unbuild(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    return value


