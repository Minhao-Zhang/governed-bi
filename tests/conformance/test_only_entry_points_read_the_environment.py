"""Only the entry points may read ``.env``.

``credentials`` reads ``.env`` and fills ``os.environ``. A library that does that decides its
own configuration behind its caller's back, so the rule is that only the two processes which
*are* the caller may import it: ``api/graph_app.py`` and ``serve/__main__.py``. Everything else
under ``src/`` takes a DSN or a client.

**This test exists because the previous way of expressing the rule was geography, and geography
did not hold.** ``credentials.py`` lived in ``tools/`` and its docstring said "Never for
``src/``" — while both entry points imported it anyway, by putting ``tools/`` on ``sys.path``
first. The rule was broken in the only two places it could be broken, and nothing said so. It
also put an unpackaged directory on an installed package's runtime path and made a module named
``credentials`` importable from anywhere in the process.

``tools/check_imports.py`` cannot express this. Layering answers "what may this module import";
the rule here is the other direction — who may import *it*.
"""

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
    """An allowlist naming a module that no longer imports it is an allowlist nobody prunes.

    Both halves matter. A stale entry silently widens the rule the day someone adds that import
    back for another reason.
    """
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
