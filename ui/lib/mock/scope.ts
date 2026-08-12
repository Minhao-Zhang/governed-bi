/**
 * Re-exports graph/summary scoping helpers used by mock fixtures.
 * Implementation lives in `lib/graph-scope.ts` (also used as a live client
 * fallback when the engine ignores scope query params).
 */

export {
  annotateNodeSchemas,
  applyErGraphScope,
  applyKnowledgeGraphScope,
  engineScopeMatches,
  filterSummaryItems,
  withDefaultBudget,
  DEFAULT_ER_BUDGET,
  DEFAULT_KG_BUDGET,
} from "@/lib/graph-scope";
