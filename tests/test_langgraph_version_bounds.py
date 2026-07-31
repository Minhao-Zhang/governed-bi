"""Declared LangGraph wire-protocol ranges must match docs/architecture.md §9."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"

# Hardcoded expected ranges — the contract this test pins.
EXPECTED_DIRECT = {
    "langgraph": (">=1.0", "<2"),
    "langgraph-cli": (">=0.4", "<0.5"),
}
EXPECTED_CONSTRAINTS = {
    "langgraph-api": (">=0.11", "<0.12"),
    "langgraph-sdk": (">=0.4.2", "<0.5"),
}


def _dep_name(spec: str) -> str:
    return re.split(r"[<>=!\[]", spec, maxsplit=1)[0].strip()


def _has_bound(spec: str, lower: str, upper: str) -> bool:
    return lower in spec and upper in spec


def test_pyproject_declares_langgraph_wire_protocol_bounds():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = {_dep_name(d): d for d in data["project"]["dependencies"]}
    for name, (lo, hi) in EXPECTED_DIRECT.items():
        # langgraph-cli is declared as langgraph-cli[inmem]...
        key = next((k for k in deps if k == name or k.startswith(f"{name}[")), None)
        assert key is not None, f"missing direct dep {name}"
        assert _has_bound(deps[key], lo, hi), deps[key]

    constraints = {
        _dep_name(c): c
        for c in data.get("tool", {}).get("uv", {}).get("constraint-dependencies", [])
    }
    for name, (lo, hi) in EXPECTED_CONSTRAINTS.items():
        assert name in constraints, f"missing constraint {name}"
        assert _has_bound(constraints[name], lo, hi), constraints[name]


def test_architecture_documents_the_same_wire_protocol_ranges():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    assert "Declared LangGraph SDK / wire-protocol ranges" in text
    for name, (lo, hi) in {**EXPECTED_DIRECT, **EXPECTED_CONSTRAINTS}.items():
        assert f"`{name}`" in text, name
        assert f"`{lo},{hi}`" in text or f"{lo},{hi}" in text, (
            f"{name} combined range {lo},{hi} missing from architecture.md"
        )
