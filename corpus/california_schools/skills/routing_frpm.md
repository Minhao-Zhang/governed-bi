---
skill_id: skill_california_schools_routing
db: california_schools
kind: routing
provenance: { source: curator, model: claude-opus-4-8, status: draft, source_refs: [q1032, q1044] }
---

# California Schools — routing & gotchas

## Scope
Answers about schools, districts, and free/reduced-price-meal eligibility.
Hub table: `tbl_california_schools_schools` — join everything via the CDS code.

## Routing triggers
- IF the question is about eligibility rate → use `metric_frpm_rate`; join
  `tbl_california_schools_frpm` → `tbl_california_schools_schools` via `join_frpm_schools`.
- DO NOT use `col_california_schools_frpm_lie_12` for counts — it is flagged
  unreliable (see its reliability caveat).

## Gotchas
- `academic_year` is the *starting* calendar year (see `rule_academic_year_format`).
- Several near-synonym columns are decoys; prefer the primary column named in each table asset.

## Query patterns
- Eligibility rate = free_count / enrollment, excluding enrollment = 0.
