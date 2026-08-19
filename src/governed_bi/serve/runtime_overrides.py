"""The third resolution path for a knob: an operator's own switch, written at runtime.

A knob resolved from its declared default and then from an environment variable, and there was no
way for a running engine to be told otherwise. That absence is why this fork carried three UI
controls for settings nothing could change — a schema field, an `api-client` method and a rendered
component, over a route that did not exist and a knob that could not be written
(``docs/detentai-role-tiers-and-clarification-cancel.md`` § client-only halves). This is the write
path those controls needed.

**Almost everything here is about keeping it narrow, because the danger is not the writing.**

*The allowlist is not "operational knobs".* That was the first design and it is wrong. The
operational role also carries ``git_sha``, ``git_main_sha``, ``working_tree_dirty`` and
``diff_sha256`` — the fields by which a measurement says *which code produced it*. A UI able to
write any operational knob could forge the provenance of a run. Being toggleable is a second,
deliberate decision per knob, and :data:`TOGGLEABLE` is where it is made and argued.

*Comparability knobs are excluded by construction.* ``enable_structured_percentage_check`` looks
like a natural neighbour of the two below and is deliberately absent: it is declared
``Role.comparability`` because it changes what the model sees, so "a run with it on is not
comparable to one without". Changing that from a switch would make two runs incomparable with
nothing recording that a human did it. Comparability belongs in ``register/arms.toml``, which
exists to name such a change and reconcile it against an artifact.

*An override is recorded, never hidden.* ``session._resolved_knobs`` applies it, so it lands in
every turn's ``knobs_resolved`` — which means ``measure/gates.py::_knobs_resolved_gate`` sees a
mid-run flip as configuration drift and fails that arm. **That is the correct outcome, not a
limitation to route around:** ``enable_clarification_to_draft``'s own declaration says it "changes
the corpus on disk between two turns of the SAME run". A run that flipped it and still reported one
configuration would be reporting a rate over a population that does not exist — the defect
``_resolved_knobs``'s own docstring was written about.

*The environment still wins.* Precedence is default → policy → resolvers → **this** → environment.
An exported variable is how an eval arm pins a run, and a switch that silently overrode one would
make the artifact lie about the run it came from. Neither knob below declares an ``env_var`` today,
so this ordering is about the mechanism rather than about them; :func:`describe` reports the source
so a UI can say "pinned by the environment" instead of offering a control that does nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..paths import REPO_ROOT

__all__ = [
    "TOGGLEABLE",
    "OVERRIDE_PATH",
    "overrides",
    "set_override",
    "clear_override",
    "reload",
    "describe",
]

#: Knobs an operator may flip from the running engine, and why each is safe to expose.
#:
#: Both are ``Role.operational``, which is necessary but not sufficient — see this module's
#: docstring for what else is operational and must never be here.
#: ``tests/serve/test_a_runtime_override_cannot_forge_a_configuration.py`` asserts every entry is
#: declared and operational, and that the provenance fields are absent.
TOGGLEABLE: dict[str, str] = {
    "enable_clarification_to_draft": (
        "Turns an answered live clarification into a corpus draft. Writes only `proposed` assets, "
        "which are invisible to retrieval until an admin approves them, so the blast radius of "
        "having it on is a review queue rather than a changed answer."
    ),
    "enable_mistake_memory_mining": (
        "Turns a turn that self-corrected a failing query into a few-shot draft. Same containment "
        "as the row above: `proposed`, invisible until approved."
    ),
}

#: Where the overrides live. Under ``runs/`` because they are the engine's own scratch state, not
#: the corpus's — a corpus is a portable artifact and an operator's switch is not part of it.
#: Overridable so a test never writes into the repository's own ``runs/``, the same precaution
#: ``api/trace_store.py``'s ``TURN_LOG_DIR`` takes.
OVERRIDE_PATH = Path(
    os.environ.get("GOVERNED_BI_RUNTIME_OVERRIDES") or (REPO_ROOT / "runs" / "runtime-overrides.json")
)

_cache: dict[str, Any] | None = None


def _read() -> dict[str, Any]:
    """Load from disk, dropping anything no longer toggleable.

    A name that has since left :data:`TOGGLEABLE` is dropped rather than raising: the file outlives
    the code that wrote it, and an engine that refused to boot because of a stale switch would be
    worse than one that ignores it. Same for a malformed file — an operator's convenience must not
    be able to stop the engine serving.
    """
    try:
        raw = json.loads(OVERRIDE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in TOGGLEABLE}


def reload() -> None:
    """Drop the cache so the next read hits disk. For tests, and for a second process."""
    global _cache
    _cache = None


def overrides() -> Mapping[str, Any]:
    """Every override currently in force. Cached; :func:`reload` clears it."""
    global _cache
    if _cache is None:
        _cache = _read()
    return dict(_cache)


def _validate(name: str, value: Any) -> None:
    """Refuse anything outside the allowlist, or of the wrong type.

    **The declared default decides the type**, the same rule
    ``register/knobs.py::env_override`` uses, and for the same reason its docstring gives: a knob
    that arrived as the string ``"false"`` would switch a feature **on** (``bool("false")`` is
    ``True``) and be recorded as off. A wrong type is refused here, at the write, rather than
    coerced somewhere downstream.
    """
    from ..register.knobs import KNOB_REGISTER

    if name not in TOGGLEABLE:
        raise ValueError(
            f"knob {name!r} is not runtime-toggleable. Toggleable knobs are "
            f"{sorted(TOGGLEABLE)}; being `operational` is not enough, because that role also "
            "carries the fields a measurement's provenance is made of."
        )
    declared = next((k for k in KNOB_REGISTER if k.name == name), None)
    if declared is None:  # pragma: no cover - the allowlist test makes this unreachable
        raise ValueError(f"knob {name!r} is not declared in the register")
    expected = type(declared.default)
    if not isinstance(value, expected) or isinstance(value, bool) is not (expected is bool):
        raise ValueError(
            f"knob {name!r} is declared {expected.__name__} and was given "
            f"{type(value).__name__} ({value!r}). The declared default decides the type."
        )


def set_override(name: str, value: Any) -> None:
    """Put one knob under an operator's control until it is cleared."""
    _validate(name, value)
    current = dict(overrides())
    current[name] = value
    _write(current)


def clear_override(name: str) -> None:
    """Return one knob to whatever the register and the environment say."""
    current = dict(overrides())
    current.pop(name, None)
    _write(current)


def _write(values: dict[str, Any]) -> None:
    global _cache
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_PATH.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    _cache = dict(values)


def describe() -> list[dict[str, Any]]:
    """Every toggleable knob, its effective value, and **where that value came from**.

    The source is the load-bearing field. Without it a UI cannot tell an operator that a switch is
    pinned by an exported variable, and would render a control that silently does nothing — which
    is the whole class of defect this module exists to end.
    """
    from ..register.knobs import KNOB_REGISTER, env_override, knob_default

    live = overrides()
    declared = {k.name: k for k in KNOB_REGISTER}
    rows: list[dict[str, Any]] = []
    for name, why in TOGGLEABLE.items():
        knob = declared[name]
        from_env = env_override(name)
        if from_env is not None:
            value, source = from_env, "environment"
        elif name in live:
            value, source = live[name], "override"
        else:
            value, source = knob_default(name), "default"
        rows.append(
            {
                "name": name,
                "value": value,
                "source": source,
                "default": knob.default,
                "why": why,
                "editable": from_env is None,
                "env_var": knob.env_var,
            }
        )
    return rows
