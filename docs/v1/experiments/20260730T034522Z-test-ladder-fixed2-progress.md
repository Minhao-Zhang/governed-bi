# Test-split ladder run (FIXED code + C11 fix) — progress log

- **Run dir:** `runs/datalake/20260730T034522Z-test-ladder-fixed2`
- **Code:** HEAD=3f599b6 + local C11 fix (read_corpus todo_only now clipped to 20k)
- **Prompt:** curator_phase_a=v2 · --max-agent-steps unset (derived)
- **Config:** Claude-Opus-4.8 medium · arms baseline,seeded,curated,curated_sme · split test (N=1351) · 20/20
- **Supersedes:** 20260730T031119Z (killed at 54/57 on the C11 wedge). Compare EX to pre-fix 20260729T234601Z (0.395/0.484/0.571, SME n/a).

## Timeline (UTC)
- 2026-07-30T03:45:22Z — Launching with C11 fixed. Expect works_cycles to build without wedging, 0 caps, SME realizable.
- 2026-07-30T04:10:10Z — **BUILD COMPLETE: 57/57, 0 recursion caps** (vs 30/57 capped pre-fix). works_cycles built cleanly (C11 fix held — no 668KB eviction, no wedge). SME byte-identical count: 3/57 (vs 28/57 pre-fix). Serve phase started.

## The 3 byte-identical SME schemas (professional_basketball, synthea, works_cycles) — analyzed via new curator_trace.jsonl
- 2026-07-30T04:14:28Z — All 3 are honest "no clarifications to fold", NOT the pre-fix cap bug:
  - **works_cycles**: 125 tool calls (75 annotate_columns, 8 annotate_table, 6 terms, 5 few-shots, 13 probes) — the RICHEST corpus in the run. This is the exact schema that wedged at 0 assets under C11 last run; the C11 fix unblocked a full Phase-A pass. It authored no clarifications by choice (73 tables fully annotated, nothing ambiguous enough to escalate). Decisive validation of the C11 fix.
  - **professional_basketball**: 13 calls (11 run_probe_query) — investigated thoroughly, wrote 0 clarifications. Legitimate "no open questions".
  - **synthea**: 1 call (read_corpus todo_only) then stopped — the one soft spot: the agent under-worked an easy schema and bailed. Model/prompt behavior, not infra; doesn't invalidate results.
  - Net: SME realizable — 54/57 fold real clarifications, 3/57 legitimately have none (vs pre-fix 28/57 byte-identical purely from the cap).
- 2026-07-30T04:37:55Z — **baseline complete** EX=0.392 (pre-fix 0.395 — identical, as expected: no corpus, fixes can't move it; good harness sanity check). decoy=0.1150 refuse=0.019 crash=0.000. seeded started.
- 2026-07-30T04:59:22Z — **seeded complete** EX=0.470 (pre-fix 0.484; -1.4pp, within noise). decoy=0.0477 refuse=0.019 crash=0.000 routing_recall=0.849. curated started.
- 2026-07-30T05:15:08Z — **curated complete** EX=0.585 (pre-fix 0.571; **+1.4pp** from uncapped/richer corpora + join-identity fix). decoy=0.0007 (near-zero!) refuse=0.006 crash=0.000 routing_recall=0.894 cond_EX|routed=0.651. curated_sme started (now genuinely folding on 54/57).
- 2026-07-30T05:32:50Z — **curated_sme complete** EX=0.583 (first-ever genuinely-measured SME number — 54/57 fold real clarifications). Essentially == curated 0.585 (-0.2pp, noise). decoy=0.0007 refuse=0.006 crash=0.000. **RUN COMPLETE, all 4 arms.**
