#!/usr/bin/env python
"""Which corpus does this tool read, and how does it fail when nobody said.

Four tools ask the same question -- ``check_landed.py``, ``verify_patch.py``,
``export_bundle.py``, ``reproduce_observation.py`` -- and until 2026-08-24 each answered it with its
own copy of the same two mistakes.

**Mistake one: ``raise SystemExit("message")`` exits 1.** In three of those four, 1 already means
something: "the failure still reproduces", "a tier regressed", "the bundle was refused". So a
configuration error came back as a finding, and a caller reading the exit code recorded a verdict
the tool had never formed. :class:`Misconfigured` exists so ``main`` can map it to **2** in one
place, which is also what makes the code assertable without ``pytest.raises``.

**Mistake two: ``os.environ.get`` is not where this repository keeps configuration.**
``governed_bi.credentials`` has been its dotenv reader since 2026-08-03 -- ``secret()`` reads the
environment and then ``.env``. `reproduce_observation.py` asked ``credentials.secret`` for the DSN
and ``os.environ.get`` for the corpus, so one entry point gave two answers about where its own
configuration lives: ``.env`` set ``GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`` and the tool reported it
unset while the database beside it resolved fine.

**Why this lives in ``tools/`` and not in ``paths.py``.**
``tests/conformance/test_only_entry_points_read_the_environment.py`` permits exactly two modules
under ``src/governed_bi`` to import ``credentials``, and says adding a third "is a decision: it means
another place in the package reads ``.env``". ``paths.py`` is imported almost everywhere, so allowing
it would widen that surface to the whole package to save a small module here. ``tools/`` scripts are
entry points, which is who may read the file, and a sibling script imported by other scripts is the
arrangement ``conformance_findings.py`` already uses.
"""

from __future__ import annotations

from pathlib import Path

from governed_bi import credentials
from governed_bi.paths import REPO_ROOT

#: The variable every one of these tools reads, spelled once.
CORPUS_DIR_VAR = "GOVERNED_BI_CORPUS_DIR"


class Misconfigured(RuntimeError):
    """Nobody said which corpus, or which database. **Exit 2, never 1.**

    "I could not run" and "I ran and the answer is bad" are different sentences, and three of the
    four callers use 1 for the second one.
    """


def bridge_dotenv() -> int:
    """Put ``.env``'s values into the environment, and return how many landed.

    Bridged rather than only read through :func:`credentials.secret`, because not every reader goes
    through it: ``model/provider.py`` reads ``os.environ`` itself for the embedder key, so a run that
    embeds needs the file's values *in* the environment and not merely readable from it.
    """
    return credentials.load_into_environ()


def resolve_corpus_dir(explicit: str | None) -> Path:
    """``--corpus-dir``, then the environment, then ``.env``. In that order and no other.

    Relative paths resolve against the repository root rather than the working directory, because
    every documented invocation of these tools is written from the repository root and a tool that
    means something different when run from ``tools/`` is a tool nobody can paste a command for.
    """
    raw = explicit or credentials.secret(CORPUS_DIR_VAR)
    if not raw:
        raise Misconfigured(
            f"no corpus: pass --corpus-dir, or set {CORPUS_DIR_VAR} in the environment or in "
            f"{credentials.DOTENV.name} at the repository root."
        )
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path)
