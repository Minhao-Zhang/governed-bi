"""Run N Cursor CLI agents in parallel, each in its own git worktree.

Headless ``-p`` needs ``--trust``. ``-w`` isolates trees (soft boundary under
``--yolo``). Worktree names carry a per-run token; :func:`_verify_base` fails
agents not on the expected commit; :func:`_dirty_paths` refuses dirty trees
unless ``--allow-dirty``. Delegate only machine-checkable work; run gates after.
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
