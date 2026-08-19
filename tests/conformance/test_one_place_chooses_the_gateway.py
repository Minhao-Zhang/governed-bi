"""No call site may name a provider itself. ``model/provider.py`` is the only one that does.

This is a regression test for a comment that went stale into a falsehood.
``serve/__main__.py`` carried its own copy of the OpenAI spelling under the note *"Kept
identical to api/graph_app.py: two entry points constructing a model differently are two
answers to 'what did this run use', on a comparability knob."* The moment Bedrock landed in
``graph_app.py`` and not in ``__main__.py``, the note was false and the two entry points did
disagree -- silently, because both still ran.

Four call sites had the string. A comment cannot hold that invariant; this can.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The one module allowed to name a gateway, plus the one that predates the abstraction.
#: ``proxy_gateway`` builds ``ChatOpenAI`` directly because the internal proxy needs a custom
#: http client and a minted bearer token, neither of which ``init_chat_model`` can carry.
ALLOWED = {
    Path("src/governed_bi/model/provider.py"),
    Path("src/governed_bi/model/proxy_gateway.py"),
}

#: ``model_provider=`` in any spelling: keyword argument or dict entry.
NAMES_A_PROVIDER = re.compile(r"""model_provider\s*[=:]\s*['"]""")


def _sources() -> list[Path]:
    files: list[Path] = []
    for base in ("src", "tools"):
        files.extend(p for p in (ROOT / base).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def test_only_the_provider_module_names_a_gateway() -> None:
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        if rel in ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NAMES_A_PROVIDER.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "these call sites choose a gateway themselves, so they cannot follow "
        "GOVERNED_BI_PROVIDER and will drift from the others exactly as serve/__main__.py "
        "did. Route them through governed_bi.model.provider.chat_model():\n  "
        + "\n  ".join(offenders)
    )


def test_no_call_site_constructs_a_concrete_embedder() -> None:
    """Same rule for the embedding surface, where the failure is quieter.

    A hardcoded ``OpenAIEmbedder()`` under ``GOVERNED_BI_PROVIDER=bedrock`` does not fail --
    it embeds with the wrong gateway and writes vectors under an ``openai:`` cache key, so
    the arm is mislabelled rather than broken.
    """
    concrete = re.compile(r"\b(OpenAIEmbedder|BedrockEmbedder|ProxyEmbedder)\s*\(")
    allowed = ALLOWED | {
        Path("src/governed_bi/model/__init__.py"),
        Path("src/governed_bi/model/openai_embedder.py"),
        Path("src/governed_bi/model/bedrock_embedder.py"),
        Path("src/governed_bi/model/proxy_embedder.py"),
    }
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        if rel in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if concrete.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "build the embedder with governed_bi.model.provider.embedder() so it follows the "
        "configured gateway:\n  " + "\n  ".join(offenders)
    )


#: A model id, in any vendor's spelling, written as a literal.
NAMES_A_MODEL = re.compile(
    r"""['"](?:gpt-[0-9a-z.-]+|claude-[a-z0-9.-]+|us\.anthropic\.[a-z0-9.-]+"""
    r"""|text-embedding-[a-z0-9.-]+|amazon\.titan-[a-z0-9.:-]+)['"]"""
)

#: Argument-parser defaults that still name a model. **A ratchet, not a waiver**: every entry is a
#: real instance of the defect below, may be deleted freely and may not be added to.
#:
#: Both are eval tools, and the fix there is not the fix ``serve/__main__.py`` got. Reading the
#: environment is right for the one-turn CLI and wrong for an arm: the model *is* the treatment, so
#: an arm that inherits an ambient variable makes two runs of different models record as one. What
#: these two want is a **required** ``--model``, which changes an interface every measurement
#: workflow calls, so it is its own change and not a rider on a bug fix.
LITERAL_MODEL_DEFAULTS: frozenset[str] = frozenset(
    {
        "tools/score_reflector.py",
        "tools/run_datalake_eval.py",
    }
)


def test_no_argument_default_names_a_model() -> None:
    """A model id in a ``default=`` is a second place deciding a comparability knob.

    ``serve/__main__.py`` had ``--model`` defaulting to ``gpt-4o-mini``, and the failure was not
    that it was redundant. Under ``GOVERNED_BI_PROVIDER=bedrock`` -- the configuration this
    repository's own ``.env`` carries -- it handed an OpenAI id to Bedrock, and the turn came back
    ``outcome: crashed`` with nothing in the record, the ledger or the printed summary pointing at
    the model. Measured 2026-08-19, on the entry point ``docs/usage.md`` tells a reader to run.

    The same failure as the provider tests above, one field along: the id and the gateway have to
    agree, and a literal in a call site cannot follow the variable that sets the other half.
    ``model/provider.py`` declares each surface's variable (``SURFACE_MODEL_VARS``); a caller reads
    that, or takes the id from its own caller, and never invents one.
    """
    default = re.compile(r"default\s*=\s*" + NAMES_A_MODEL.pattern)
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT)
        if rel.as_posix() in LITERAL_MODEL_DEFAULTS:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if default.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "these defaults name a model, so they cannot follow the variable that names the gateway "
        "and will send one vendor's id to another's endpoint. Read the surface's variable from "
        "governed_bi.model.provider.SURFACE_MODEL_VARS, or require the argument:\n  "
        + "\n  ".join(offenders)
    )


def test_the_literal_model_ratchet_still_describes_this_tree() -> None:
    """A pinned entry that stopped violating must be deleted, or the list outlives its findings.

    The same rule ``tests/conformance/test_register_closure.py`` applies to ``KNOWN_UNCONSUMED``,
    and for the same reason: a list nobody tightens reads as "these are fine".
    """
    default = re.compile(r"default\s*=\s*" + NAMES_A_MODEL.pattern)
    stale = [
        name
        for name in sorted(LITERAL_MODEL_DEFAULTS)
        if not any(
            default.search(line) and not line.lstrip().startswith("#")
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        )
    ]
    assert not stale, (
        f"{stale} no longer default to a literal model id. Delete them from "
        "LITERAL_MODEL_DEFAULTS so the ratchet tightens."
    )


@pytest.mark.parametrize(
    "module",
    [
        "src/governed_bi/api/graph_app.py",
        "src/governed_bi/serve/__main__.py",
        "tools/run_datalake_eval.py",
        "tools/routing_recall.py",
        "tools/query_summary_alignment.py",
        "tools/score_reflector.py",
    ],
)
def test_every_entry_point_reaches_the_provider_module(module: str) -> None:
    """Named one by one, so deleting the import from any of them fails here.

    A blanket "nobody hardcodes a provider" passes trivially on a file that stopped building
    a model at all, which is how the check above could rot without anyone noticing.
    """
    source = (ROOT / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_provider = any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith(("governed_bi.model", "model", "..model"))
        and any(alias.name == "provider" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imports_provider, (
        f"{module} builds a model or an embedder but does not import "
        "governed_bi.model.provider, so it cannot honour GOVERNED_BI_PROVIDER"
    )
