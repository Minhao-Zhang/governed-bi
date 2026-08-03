# v1 documentation (archive)

Everything in this directory describes the v1 implementation of governed-bi, which was
deleted in commit `2347ae3`. None of it describes code that exists today. It is kept as
history: it is the project's record of what was built, what was measured, and why each
thing was tried.

## The numbers in `experiments/` are retired

An experiment record stating what was measured on a date is a true statement, so those
files have been left unedited. But several of those measurements were later found to have
been produced by defective instrumentation — a crash counted as a refusal, a `NameError`
inside the notes tools, unbuilt schemas competing in the router, and a routing-recall
figure that was wrong by 2.4x. The records are faithful; what they were measuring was not
what we believed at the time.

So do not quote a number out of this directory without checking it first.
[`docs/lessons-from-v1.md`](../lessons-from-v1.md) is the authoritative account of which
claims survived and which did not, and
[`src/governed_bi/register/citations.py`](../../src/governed_bi/register/citations.py)
holds the machine-readable list of retired figures, each with the pattern that detects its
reappearance.

## The plans in `plans/` are not a guide to the current system

They reason from those figures toward designs for code that no longer exists. The
arguments may still be worth reading, but the thing they were arguing about is gone.
[`docs/adr/0005`](../adr/0005-v2-memory-layer-and-faceted-retrieval.md) and
[`docs/adr/0006`](../adr/0006-execution-time-governance.md) are what describes the system
now.

## What is still live

- [`docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md`](../adr/0005-v2-memory-layer-and-faceted-retrieval.md)
- [`docs/adr/0006-execution-time-governance.md`](../adr/0006-execution-time-governance.md)
- [`docs/lessons-from-v1.md`](../lessons-from-v1.md)
- [`docs/plans/v2-implementation-decisions.md`](../plans/v2-implementation-decisions.md)
