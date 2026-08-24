"""A tool that could not run must not exit with the code it uses for a bad answer.

`raise SystemExit("message")` exits **1**. In `reproduce_observation.py` 1 means "the failure still
reproduces"; in `verify_patch.py` it means "a tier regressed"; in `export_bundle.py` it means "the
bundle was refused". So for three of the four tools that read a corpus, forgetting `--corpus-dir`
produced a verdict the tool had never formed — and a caller reading the exit code could not tell.

Found by running one: `reproduce_observation.py` reported `no corpus: pass --corpus-dir or set
GOVERNED_BI_CORPUS_DIR` and exited 1 while `.env:92` set exactly that variable, because the tool
asked `os.environ.get` for the corpus and `credentials.secret` for the database beside it.

These tests are on the **shape**, not on the three sites. A fourth tool copying the pattern is the
failure mode; naming the three that had it would not catch the fourth.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"

#: Tools that resolve a corpus directory and therefore must fail the same way.
CORPUS_READERS = (
    "check_landed.py",
    "export_bundle.py",
    "reproduce_observation.py",
    "verify_patch.py",
)


def _string_valued_system_exits(path: Path) -> list[int]:
    """Line numbers of `raise SystemExit(<a string>)` — the shape that silently exits 1."""
    out: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "SystemExit"):
            continue
        if not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, int):
            continue  # `SystemExit(2)` is a code, and a code is the point
        out.append(node.lineno)
    return out


@pytest.mark.parametrize("name", CORPUS_READERS)
def test_a_tool_with_a_meaningful_one_does_not_exit_one_by_raising_a_message(name: str) -> None:
    """`SystemExit("...")` exits 1. Raise the exception the tool maps to a code instead.

    **Scoped to the tools where 1 already means something**, and the first version of this test was
    not: it ran over every `tools/*.py` and failed 27 of them. That is not 27 defects. For a tool
    whose only failure is "something is wrong", `SystemExit("message")` is idiomatic Python and the
    message goes to stderr with a non-zero code, which is the whole contract. The defect is a
    *collision*: these four document 1 as a verdict — "still reproduces", "a tier regressed", "the
    bundle was refused" — so a configuration error arriving as 1 is a verdict the tool never formed.

    Widening it back means first giving the other tools a documented second code to collide with.
    """
    lines = _string_valued_system_exits(TOOLS / name)
    assert not lines, (
        f"tools/{name} raises SystemExit with a message at line(s) {lines}, which exits 1. If 1 "
        "means something in this tool, a configuration error reported that way is a verdict it "
        "never formed. Raise `corpus_target.Misconfigured` (or a local equivalent) and let `main` "
        "choose 2."
    )


@pytest.mark.parametrize("name", CORPUS_READERS)
def test_a_corpus_reader_resolves_through_the_shared_target(name: str) -> None:
    """One answer to "which corpus", so the four cannot drift.

    Each of these had its own copy, and the copies disagreed about whether `.env` counts.
    """
    source = (TOOLS / name).read_text(encoding="utf-8")
    assert "corpus_target" in source, (
        f"tools/{name} reads a corpus directory without going through tools/corpus_target.py; that "
        "is the fifth copy of the question"
    )
    assert "os.environ.get(\"GOVERNED_BI_CORPUS_DIR\"" not in source, (
        f"tools/{name} asks os.environ for the corpus dir. `.env` sets it, and asking the "
        "environment alone is how one entry point came to give two answers about its own config"
    )


def test_the_shared_resolver_raises_the_type_the_entry_points_map(monkeypatch) -> None:
    """The exception type, asserted directly, because the subprocess test could not reach it.

    The first version of this drove each tool from a temp directory with
    `GOVERNED_BI_CORPUS_DIR` cleared and asserted the exit code was not 1. **It passed while
    asserting nothing.** `credentials.DOTENV` is `ROOT / ".env"` -- anchored to the repository,
    not the working directory -- so `.env:92` supplied the corpus from any cwd and the missing
    branch never ran. Mutating `Misconfigured` back to `SystemExit` left all twelve green, which
    is how the vacuity surfaced.
    """
    import corpus_target

    monkeypatch.setattr(corpus_target.credentials, "secret", lambda *names: "")
    with pytest.raises(corpus_target.Misconfigured):
        corpus_target.resolve_corpus_dir(None)


@pytest.mark.parametrize("name", CORPUS_READERS)
def test_each_entry_point_maps_that_type_to_two(name: str, monkeypatch) -> None:
    """Raising the right type buys nothing until somebody catches it.

    `Misconfigured` is a `RuntimeError`. Three of these four had no handler when the type was
    introduced, so the exception escaped as a traceback and **still exited 1** -- the same defect
    with a worse face. Driven through the module's real entry point with the resolver stubbed, so
    the mapping is exercised and not read.
    """
    import importlib.util
    import sys as _sys

    import corpus_target

    spec = importlib.util.spec_from_file_location(f"_t_{name[:-3]}", TOOLS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def _refuse(_explicit):
        raise corpus_target.Misconfigured("no corpus, for the test")

    monkeypatch.setattr(corpus_target, "resolve_corpus_dir", _refuse)
    # `main(argv) -> int` is the one contract all four share, and the only place that owns a
    # code. An earlier version of this test looked for a `_run` wrapper, which is a shape three
    # of them happened to have and the fourth used for something else entirely -- so it failed
    # on the one tool that was already correct.
    assert module.main(["--patch", "pat-does-not-exist"]) == 2
