"""Only listed modules may import ``credentials``, which reads ``.env``. The rule is who may import it."""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "governed_bi"

#: Every module permitted to import ``credentials``, relative to ``src/governed_bi``.
#: Adding one is a decision: it means another place in the package reads ``.env``.
ALLOWED: frozenset[str] = frozenset(
    {
        "api/graph_app.py",
        "serve/__main__.py",
        "boot.py",
        "model/provider.py",
        "model/openai_embedder.py",
    }
)


def _imports_credentials(tree: ast.AST) -> bool:
    """``import governed_bi.credentials``, ``from . import credentials``, ``from .credentials``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "credentials" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "credentials":
                return True
            if any(a.name == "credentials" for a in node.names):
                return True
    return False


def _package_modules():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "credentials.py":
            continue
        yield rel, path


def test_no_module_outside_the_entry_points_imports_credentials():
    offenders = [
        rel
        for rel, path in _package_modules()
        if rel not in ALLOWED
        and _imports_credentials(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], (
        "these modules read .env and are not entry points: "
        + ", ".join(offenders)
        + ". Take a DSN or a client instead, or add the module to ALLOWED and say why."
    )


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_each_allowed_entry_point_exists_and_still_imports_it(rel):
    """A listed module that does not import ``credentials`` is an entry nobody prunes."""
    path = SRC / rel
    assert path.exists(), f"{rel} is on the allowlist and does not exist"
    assert _imports_credentials(
        ast.parse(path.read_text(encoding="utf-8"))
    ), f"{rel} is on the allowlist and no longer imports credentials; remove it"


def test_the_package_does_not_put_tools_on_sys_path():
    """The mechanism that hid the rule-break. It has no other user, so it should not come back."""
    offenders = [
        rel
        for rel, path in _package_modules()
        if 'REPO_ROOT / "tools"' in (text := path.read_text(encoding="utf-8"))
        or "TOOLS_DIR" in text
    ]
    assert offenders == [], (
        "package code is reaching into tools/ at runtime: " + ", ".join(offenders)
    )
