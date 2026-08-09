"""Every prompt the engine sends must be resolved at the variant the run selected.

The failure this refuses is **silent and total**: ``prompt_text("analyst")`` with no variants
argument returns the default, the turn still records the overriding ``prompt_set_hash``, and the
artifact names a treatment the model never received. Nothing raises, nothing looks wrong, and a
paired A/B measures the difference between a prompt and itself.

That was the state of the tree until prompt-variant selection was wired: ``select(overrides)``,
``prompt_set_hash(overrides)`` and ``prompt_text(name, variants)`` had all carried the parameter
since the registry was written, and **no caller in ``src/`` passed one**. Two of the five call
sites were worse than unwired — ``SYSTEM_PROMPT`` and ``REFLECT_PROMPT`` resolved at *import*,
so no amount of threading at the call site would have reached them.

A source-level check rather than a behavioural one, because the property is about every call
site including ones nobody has written yet. A new node that sends a registered prompt is
exactly the case a behavioural test cannot cover.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVE = Path(__file__).resolve().parents[2] / "src" / "governed_bi" / "serve"

#: Functions that resolve a prompt and must therefore be told the run's selection.
RESOLVERS = frozenset({"prompt_text"})


def _call_sites() -> list[tuple[Path, ast.Call]]:
    out: list[tuple[Path, ast.Call]] = []
    for path in sorted(SERVE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name in RESOLVERS:
                out.append((path, node))
    return out


def test_the_check_has_something_to_check() -> None:
    """A zero-site sweep passes vacuously; that has happened here before."""
    sites = _call_sites()
    assert len(sites) >= 4, f"expected the known prompt call sites, found {len(sites)}"


def test_no_prompt_is_resolved_without_the_run_s_variant_selection() -> None:
    """``prompt_text(name)`` in ``serve/`` ignores the selection the run recorded."""
    bad = [
        f"{path.relative_to(SERVE.parent.parent.parent)}:{call.lineno}"
        for path, call in _call_sites()
        if len(call.args) < 2 and not any(kw.arg == "variants" for kw in call.keywords)
    ]
    assert not bad, (
        "these call sites resolve a prompt without the run's variant selection, so a run that "
        "selects one records its hash and sends the default: " + ", ".join(bad)
    )


def test_no_prompt_is_resolved_at_import_time() -> None:
    """A module-level constant cannot see a per-run selection, whatever the call site passes.

    Checked as *module scope*, not as "is it uppercase": the defect is where the call happens,
    and a lowercase module-level name has exactly the same problem.
    """
    def executes_at_import(node: ast.AST):
        """Sub-nodes of ``node`` evaluated while the module is being imported.

        A ``def``'s *body* runs later, so it is skipped — but its decorators and its argument
        defaults are evaluated at import, and a prompt resolved in either is just as stuck.
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from node.decorator_list
            yield from (d for d in node.args.defaults if d is not None)
            yield from (d for d in node.args.kw_defaults if d is not None)
            return
        if isinstance(node, ast.ClassDef):
            yield from node.decorator_list
            for stmt in node.body:
                yield from executes_at_import(stmt)
            return
        yield node

    bad: list[str] = []
    for path in sorted(SERVE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for stmt in tree.body:
            for top in executes_at_import(stmt):
                for inner in ast.walk(top):
                    if not isinstance(inner, ast.Call):
                        continue
                    fn = inner.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                    if name in RESOLVERS:
                        bad.append(f"{path.name}:{inner.lineno}")
    assert not bad, (
        "these resolve a prompt at import, so the variant a run selects can never reach them: "
        + ", ".join(bad)
    )


@pytest.mark.parametrize("name", ["analyst", "bi_scope", "narrate", "reflect"])
def test_every_registered_prompt_the_engine_sends_is_reachable_by_name(name: str) -> None:
    """The registry is what ``prompt_set_hash`` digests; a prompt sent from outside it is a
    treatment the hash does not cover."""
    from governed_bi.register.prompts import PROMPT_REGISTRY

    assert name in PROMPT_REGISTRY
