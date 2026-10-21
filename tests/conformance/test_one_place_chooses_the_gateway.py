"""One module builds the chat model and the embedder. Call sites name a surface.

A second copy of the constructor is how two entry points come to disagree about
which client a run used. The model id's default lives in the settings module;
an argument parser does not invent another one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

NAMES_A_PROVIDER = re.compile(r"""model_provider\s*[=:]\s*['"]""")
NAMES_A_MODEL = re.compile(
    r"""['"](?:gpt-[0-9a-z.-]+|claude-[a-z0-9.-]+|us\.anthropic\.[a-z0-9.-]+"""
    r"""|text-embedding-[a-z0-9.-]+|amazon\.titan-[a-z0-9.:-]+)['"]"""
)


def _sources() -> list[Path]:
    files: list[Path] = []
    for base in ("src", "tools"):
        files.extend(p for p in (ROOT / base).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def test_only_the_provider_module_names_a_gateway() -> None:
    allowed = {Path("src/governed_bi/model/provider.py")}
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        if rel in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NAMES_A_PROVIDER.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "route these through governed_bi.model.provider.chat_model():\n  " + "\n  ".join(
        offenders
    )


def test_no_call_site_constructs_a_concrete_embedder() -> None:
    concrete = re.compile(r"\bOpenAIEmbedder\s*\(")
    allowed = {
        Path("src/governed_bi/model/provider.py"),
        Path("src/governed_bi/model/openai_embedder.py"),
    }
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        if rel in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if concrete.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "build the embedder with governed_bi.model.provider.embedder():\n  " + "\n  ".join(
        offenders
    )


def test_no_argument_default_names_a_model() -> None:
    """A model id in ``default=`` is a second place deciding the settings default."""
    default = re.compile(r"default\s*=\s*" + NAMES_A_MODEL.pattern)
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if default.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "these defaults name a model. Read it from the settings module, or require the argument:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "module",
    [
        "src/governed_bi/api/graph_app.py",
        "src/governed_bi/serve/__main__.py",
        "tools/run_datalake_eval.py",
    ],
)
def test_every_entry_point_reaches_the_provider_module(module: str) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_provider = any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith(("governed_bi.model", "model", "..model"))
        and any(alias.name == "provider" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imports_provider, f"{module} builds a model but does not import governed_bi.model.provider"
