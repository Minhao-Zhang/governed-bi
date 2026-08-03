# Spike: Should L3 narrow to routed schemas?

2026-07-31. Near-term plan **N4**. Conclusion only — no code.

Triggered by M1 / train_5163: under a pooled corpus, `column_allowlist(corpus)` walks every non-excluded table, so L3 is a lake-wide pass for any column that exists somewhere in the corpus. Table-scope is L4’s job. Graded delivery used to skip L4; **N2 closed that hole** by threading `allowed_tables` on recheck. This page asks whether L3 should *also* shrink to the turn’s routed schemas.

## Verdict

**Do not narrow L3 to routed schemas in this batch.** Keep the lake-wide allowlist. Authority for “which tables may execute” stays on L4 (now enforced on the graded-delivery path). Revisit only if a later measurement shows L3-as-lake-pass is causing a distinct, quotable failure mode that L4 cannot catch.

## 1. Tests that would go red under routed-schema L3

Assume production (and fixtures that mirror it) build the allowlist from `filter_corpus_for_retrieval(corpus, routed)` / an equivalent schema slice, not the full pooled corpus.

| Area | Disposition |
|------|-------------|
| Single-schema suites (`test_guardrails.py`, `test_graded_delivery_*.py`, most agent e2e) | Stay green — the allowlist already is one schema. |
| [`tests/test_multi_schema_guardrails.py`](../../tests/test_multi_schema_guardrails.py) | **Would change.** Cases that admit a non-routed schema’s columns at L3 and die at L4 (e.g. comments at ~249–251 assuming corpus-wide L3 keys) flip to `ast_column_allowlist`. Any assertion that out-of-routed columns clear L3 goes red. |
| Middleware / agent “unlicensed table → L4” with **same-schema** column refs | Stay green under *schema*-scoped narrowing; would go red only if L3 were narrowed to the *licensed table* set (different proposal — not this spike). |
| No existing graded-delivery scope e2e encodes “L3 admits foreign schema, L4 catches it” as a green path | N1’s new unit case is about L4 on recheck, not about changing L3’s builder. |

Practical takeaway: the unit suite mostly survives; **multi-schema / pooled expectations are the blast radius**.

## 2. Would pooled-eval EX drop?

**Unmeasurable on 20260730.** That run had exactly one `graded_delivery=True` row (train_5163) out of 5404 turns, so any EX movement from N2’s L4 recheck — and from a hypothetical L3 narrow that only affects the same class of turns — is at most 1/5404. Do not attribute any visible EX fluctuation to either change.

Separate the two mechanisms anyway (they matter on future runs):

1. **Silent wrongs that N2 already kills.** Rows like train_5163 (`graded_delivery=True`, out-of-routed tables, `correct=True`) become refuses under N2’s L4 recheck. That EX drop is intentional and already paid for by M1; L3 narrowing is not required for it.
2. **Extra hard refuses if L3 were narrowed.** Mid-loop, L3 is repairable (BLOCKED tool message). On final disposition, L3 is never graded-delivered. Schema-scoped L3 would turn some “cleared L3, failed L4, previously graded-delivered” turns into earlier L3 blocks. After N2 those turns already refuse on recheck, so the *deliver* path is gone either way. The residual EX risk is turns that today **succeed** after exploring within the lake-wide L3 pass in ways a routed allowlist would have blocked earlier — likely rare if `inspect_schema` is already routed-scoped, but not measured. Until a counterfactual attributes EX deltas to L3 vs L4, treat further EX drop from narrowing L3 as **not worth the test churn**.

## 3. Where “do not narrow” must live

| Home | What to write |
|------|----------------|
| **This page** | The decision and the test/EX reasoning (source of truth for N4). |
| [`gateway/guardrails.py`](../../src/governed_bi/gateway/guardrails.py) `column_allowlist` + L4 docstrings | Runtime asymmetry (“pooled L3 does not narrow to routed schemas”) — **already written by N2**; do not restate as a new code change. |
| [`docs/glossary.md`](../../glossary.md) | Under a future `column_allowlist` / L3 entry (N5’s glossary pass is the right vehicle): “lake-wide under pooled corpus; table scope is L4.” |
| [`docs/plans/rebuild-decisions.md`](rebuild-decisions.md) | Promote this spike to a numbered decision on the next decisions pass (“L3 stays lake-wide; L4 is the table gate”). Do not invent a parallel decision here. |

**Do not** fold this into ADR 0002 as an amendment unless the serve runtime contract itself changes — the asymmetry is an allowlist construction detail, not a new autonomy/authority spine.

## Out of scope

- Narrowing L3 to the **licensed table** set (stricter than routed schemas).
- Changing `grade_semantic_failures` defaults.
- Implementing the narrow — any such change is a later checklist item with its own red/green tests.
