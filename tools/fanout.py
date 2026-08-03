"""Run N Cursor CLI agents in parallel, each in its own git worktree.

**There is no "multi-task mode" in the Cursor CLI.** Cursor's parallel agents are a
GUI feature (the Agents Window); the CLI gives you one agent per process. So
parallelism here is *this script*: launch N processes, each with ``-w`` so they get an
isolated worktree and cannot fight over one working tree.

Verified against ``cursor-agent`` 2026.07.01 on 2026-08-03:

* ``cursor-grok-4.5-high`` is in ``--list-models`` and the CLI is authenticated.
* ``-p`` is headless. In headless mode ``--trust`` is **required** or the run exits 1
  on a workspace-trust prompt with nothing to answer it.
* ``--plan`` is read-only. ``-f``/``--yolo`` allows writes and shell without asking.
* ``-w NAME`` puts the agent in ``~/.cursor/worktrees/<repo>/NAME``.

**Why ``-w`` is not optional here, and it is not about merge conflicts.** ``.env`` is
gitignored and untracked and there is no ``.cursor/worktrees.json`` setup script, so a
fresh worktree contains **no credentials**. An agent running with ``--yolo`` in a
worktree therefore cannot read this project's API keys. That is a real boundary and it
is the reason ``--yolo`` is tolerable at all.

It is a **soft** boundary: ``--yolo`` grants shell, and shell is not confined to the
worktree. An agent that runs ``cat ../../Code/governed-bi/.env`` gets the keys, and one
that runs ``git push`` pushes. So the worktree bounds *accidents*, not intent. Nothing
here should be pointed at a prompt from an untrusted source.

**``-w NAME`` REUSES an existing worktree of that name, and does not reliably rebase
it onto current HEAD.** Found 2026-08-03 the first time this launched real work: three
parcels went out, and one landed in a worktree left over from an earlier smoke test,
**two commits behind**. That agent's tree contained no ``measure/``, no lint gates, and
no acceptance-test file — it was asked to satisfy a contract that did not exist in the
only tree it could see.

Index-derived names (``fanout-0``, ``fanout-1``, ...) collide across runs *by
construction*, so this was guaranteed rather than unlucky. Two defences, because the
first is a convention and the second is a check:

* Names carry a per-run token, so a name is never reused.
* :func:`_verify_base` reads each worktree's ``HEAD`` after launch and **fails the
  agent's result** if it is not the commit that was expected. "Commit before fanning
  out" was the mitigation this file previously documented, and it is not sufficient on
  its own: committing does nothing if the agent is looking at a different commit.

**A worktree is based on HEAD, so uncommitted work is invisible to the agent** — and
this is the trap that matters, because it fails *silently and plausibly*. The first
real fan-out asked two agents factual questions about this repo. One was right. The
other was asked how many ``.py`` files are under ``src/`` and answered **9**; the
true answer was **15**, because six files were uncommitted. The agent was not wrong
about the tree it was in. It was answering a different question than the one intended,
and ``9`` is exactly the kind of number that survives review.

So :func:`_dirty_paths` refuses the run when the working tree is dirty and ``-w`` is in
use. ``--allow-dirty`` overrides it, for the case where the agents genuinely should see
only committed state. A checkable trap gets checked; the alternative is a docstring
nobody reads at the moment it matters.

**What this is for, and what it must not be used for.** Work is being parcelled to
agents, and the rule that came out of that (see
``docs/plans/v2-layer-handoffs.md`` §9) is that an agent writes tests which pass
against the implementation it just produced — v1's gold-gate test re-derived the
condition it was checking, so deleting the gate, flipping the comparison and reversing
the denominator all passed. So:

* **Delegate** work whose acceptance criterion already exists and is machine-checkable:
  renames, link fixes, moving files, filling a table from a declared source, running a
  measurement and reporting the number, mechanical sweeps across many files.
* **Do not delegate** the acceptance criteria themselves, the lint gates, or anything
  where "looks right" and "is right" come apart — the absence semantics in
  ``register/quantity.py`` being the clearest example.

After any fan-out, run the five gates and read the diff. The gates are the reason
delegating is safe at all, so skipping them removes the whole basis for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

#: Resolved rather than assumed on PATH: the installer does not add it, and a
#: ``FileNotFoundError`` three layers into a thread pool is a bad error message.
CLI = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / "cursor-agent.cmd"

DEFAULT_MODEL = "cursor-grok-4.5-high"

#: Cursor's own guidance is that concurrency past this stops paying: merge-conflict
#: cost scales worse than linearly with agent count. Not a technical limit.
SANE_MAX = 4


def _head() -> str:
    """The commit the agents are expected to see."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _verify_base(worktree: Path, expected: str) -> str | None:
    """``None`` when ``worktree`` sits on ``expected``, else a description of the drift.

    Checked rather than trusted: ``-w`` reuses a worktree of the same name, so a
    leftover from an earlier run silently supplies an older commit. An agent working
    from the wrong commit produces confident, plausible, useless output -- the first
    real fan-out lost a third of its work this way.
    """
    if not worktree.exists():
        return f"worktree {worktree} was never created"
    got = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        return f"cannot read HEAD in {worktree}"
    actual = got.stdout.strip()
    if actual != expected:
        return (
            f"worktree is at {actual[:8]}, expected {expected[:8]} -- the agent could "
            "not see the contract it was asked to satisfy"
        )
    return None


def _dirty_paths() -> list[str]:
    """Tracked-or-untracked paths that a HEAD-based worktree will not contain.

    Excludes the ignored set, since ``.env`` being absent from the worktree is the
    property this script relies on rather than a problem to report.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def run_one(
    index: int,
    prompt: str,
    *,
    run_token: str,
    expect_head: str,
    model: str,
    write: bool,
    worktree: bool,
    timeout: int,
) -> dict[str, object]:
    """One agent. Returns a result row; never raises for a failed agent."""
    name = f"fanout-{run_token}-{index}"
    cmd: list[str] = [
        str(CLI),
        "-p",
        "--trust",  # mandatory in headless; without it the run exits 1 unanswered
        "--model",
        model,
        "--output-format",
        "text",
    ]
    cmd += ["--force"] if write else ["--plan"]
    if worktree:
        cmd += ["-w", name]
    cmd.append(prompt)

    started = time.time()
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=Path.cwd()
        )
        status, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        status, out, err = 124, "", f"timed out after {timeout}s"
    except FileNotFoundError:
        status, out, err = 127, "", f"no cursor-agent at {CLI}"

    drift = (
        _verify_base(Path.home() / ".cursor" / "worktrees" / "governed-bi" / name, expect_head)
        if worktree
        else None
    )
    if drift and status == 0:
        # Exit 0 from an agent that could not see its contract is the worst case: it
        # reports success and the result is unusable. Overwrite the status.
        status = 125
        err = (err + "\nBASE MISMATCH: " + drift).strip()

    return {
        "index": index,
        "name": name,
        "base_ok": drift is None,
        "prompt": prompt[:120],
        "exit": status,
        "seconds": round(time.time() - started, 1),
        "worktree": f"~/.cursor/worktrees/governed-bi/{name}" if worktree else "(main tree)",
        "output": out.strip(),
        "error": err.strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prompts", nargs="*", help="one prompt per agent")
    ap.add_argument("--from-file", type=Path, help="file with one prompt per line")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument(
        "--write",
        action="store_true",
        help="allow writes and shell (--force). Default is --plan, read-only.",
    )
    ap.add_argument(
        "--no-worktree",
        action="store_true",
        help="run in the main tree. Removes the credential boundary; say why in the PR.",
    )
    ap.add_argument(
        "--allow-dirty",
        action="store_true",
        help="fan out despite uncommitted changes. The agents will see HEAD, not your "
        "tree -- correct only when that is what you want.",
    )
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--json", action="store_true", help="machine-readable result rows")
    args = ap.parse_args()

    prompts = list(args.prompts)
    if args.from_file:
        prompts += [
            line.strip()
            for line in args.from_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if not prompts:
        ap.error("no prompts given")

    if args.write and args.no_worktree:
        print(
            "refusing: --write with --no-worktree gives an unsupervised agent write "
            "access to the tree that holds .env. Drop one of the two flags.",
            file=sys.stderr,
        )
        return 2

    dirty = _dirty_paths() if not args.no_worktree else []
    if dirty and not args.allow_dirty:
        print(
            f"refusing: {len(dirty)} uncommitted path(s), and -w bases each worktree on "
            "HEAD. The agents would answer about a tree that is not yours, and answer "
            "it plausibly -- the first real fan-out reported 9 .py files under src/ "
            "when there were 15, because six were uncommitted.\n",
            file=sys.stderr,
        )
        for path in dirty[:10]:
            print(f"  {path}", file=sys.stderr)
        if len(dirty) > 10:
            print(f"  ... and {len(dirty) - 10} more", file=sys.stderr)
        print(
            "\nCommit or stash first, or pass --allow-dirty if HEAD is genuinely what "
            "the agents should see.",
            file=sys.stderr,
        )
        return 2

    if len(prompts) > SANE_MAX:
        print(
            f"note: {len(prompts)} agents requested; past {SANE_MAX} the merge cost "
            "grows faster than the throughput. Proceeding.",
            file=sys.stderr,
        )

    head = _head()
    # A per-run token so a worktree name is never reused. Derived from the base commit
    # plus the prompt set, so re-running the same fan-out against the same commit reuses
    # its own worktrees (resumable) while a different run cannot collide with it.
    run_token = hashlib.sha256((head + "\x00".join(prompts)).encode()).hexdigest()[:8]

    mode = "WRITE + shell (--force)" if args.write else "read-only (--plan)"
    print(
        f"{len(prompts)} agent(s), model={args.model}, {mode}\n"
        f"base {head[:8]}, worktrees fanout-{run_token}-N",
        file=sys.stderr,
    )

    # as_completed, NOT pool.map. map() is a barrier: it yields nothing until every
    # agent has finished, so a caller cannot tell "one done, two running" from "all
    # three hung" -- and on the first real fan-out that is exactly the question that
    # mattered, because one agent had silently landed on the wrong commit. Progress that
    # only arrives at the end is not progress reporting.
    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(len(prompts), SANE_MAX)) as pool:
        futures = {
            pool.submit(
                run_one,
                i,
                prompt,
                run_token=run_token,
                expect_head=head,
                model=args.model,
                write=args.write,
                worktree=not args.no_worktree,
                timeout=args.timeout,
            ): i
            for i, prompt in enumerate(prompts)
        }
        for done in as_completed(futures):
            row = done.result()
            results.append(row)
            state = "ok" if row["exit"] == 0 else f"exit {row['exit']}"
            print(
                f"[{len(results)}/{len(prompts)}] {row['name']} finished: {state} "
                f"in {row['seconds']}s",
                file=sys.stderr,
                flush=True,
            )
    results.sort(key=lambda r: r["index"])

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            tag = "ok " if r["exit"] == 0 else f"EXIT {r['exit']}"
            base = "" if r.get("base_ok", True) else "  !! WRONG BASE COMMIT"
            print(f"-- [{tag}] {r['name']}  {r['seconds']}s  {r['worktree']}{base}")
            print(f"   {r['prompt']}")
            if r["output"]:
                print("   " + str(r["output"]).replace("\n", "\n   "))
            if r["error"]:
                print("   stderr: " + str(r["error"]).replace("\n", "\n   "))
            print()

    failed = [r for r in results if r["exit"] != 0]
    if failed:
        print(f"{len(failed)}/{len(results)} agent(s) failed", file=sys.stderr)
        return 1
    if args.write:
        print(
            "Now run the gates and read the diff: check_imports, check_citations, "
            "check_file_length, check_one_implementation, check_measurement_locality, "
            "and the test suite. Delegating is safe *because* of them.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
