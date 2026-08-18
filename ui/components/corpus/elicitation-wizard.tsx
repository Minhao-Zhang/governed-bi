"use client";

/**
 * Phase 1 elicitation wizard — proactive admin onboarding. Unlike
 * `ClarificationsPanel`'s reactive queue (fed by curator review + live-chat
 * `ask_user` interrupts), this walks the admin through AI-proposed candidate
 * questions BEFORE any business user asks a live query.
 *
 * Grouped **audience tab → severity tier**, per utku-ai-setup-wizard-gap-model.md
 * — replacing the fixed A > C > E > B > D category order this used to group by.
 * Two reasons the axes swapped:
 *
 * - Audience is the outer axis because it decides *who is in the room*. Kindling's
 *   restaurant owner can say what "an active customer" means but has never seen a
 *   column name; Power Kiosk has a DBA who can say how two tables join but must
 *   guess at business intent. Neither pilot can fill both tabs, so each has to be
 *   independently useful.
 * - Severity is next because it is what an unanswered gap *costs* (a silently
 *   wrong number vs a refusal), which is the only axis an admin with 30 minutes
 *   can triage on. Category is a knowledge *type*, not a cost, so it demotes to a
 *   small label on the card: three levels of nesting is more than a
 *   non-technical admin should have to navigate, and the category is context for a
 *   question you are already reading rather than a reason to group.
 *
 * Category survives as the *within-tier* sort (`CATEGORY_ORDER`), which is exactly
 * the role the gap model leaves for Experiment 003's per-category accuracy prior:
 * a tie-break inside a tier, never the primary key.
 *
 * Answers reuse the exact same `POST /clarifications/{id}/answer` endpoint and
 * fold pipeline as the reactive queue — this component only decides how to
 * render each category's UI modality (column-picker, required-numeric,
 * exclusion-checkbox, value-checklist) and groups/orders the result.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  ListChecks,
  Lock,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api-client";
import { canCurateCorpus } from "@/lib/capabilities";
import type {
  ClarificationRecord,
  ElicitationAudience,
  ElicitationCategory,
  ElicitationSeverity,
  ScanReport,
} from "@/lib/types";
import { useCapabilities, useElicitationCandidates } from "@/hooks/queries";
import { ClarificationAnswerForm } from "@/components/common/clarification-answer-form";
import { ElicitationChecklistForm } from "@/components/corpus/elicitation-checklist-form";
import { QueryState } from "@/components/common/query-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Within-tier sort only — see the module docstring. */
const CATEGORY_ORDER: ElicitationCategory[] = ["A", "C", "E", "B", "D"];

const CATEGORY_LABEL: Record<ElicitationCategory, string> = {
  A: "Source-of-truth mapping",
  C: "Business rules",
  E: "Default exclusions",
  B: "Value mapping",
  // Not "(follow-up)" any more. D used to mean exactly one thing — the join question
  // auto-minted after an A answer landed on an unexpected table. Since the backend wired
  // `curator/gaps.py` into `POST /elicitation/generate`, the structural near-duplicate
  // cluster question ("which of these two look-alike columns is authoritative?") is also a D
  // ("D, not a sixth letter" — a disagreeing identity-ish pair within one table is the gap
  // model's D row seen from the column side). Seen live on app_store, the wizard's five
  // highest-severity T1 cards all read "Join paths (follow-up)", which is neither a join nor
  // a follow-up.
  D: "Join paths & duplicate columns",
};

const AUDIENCE_ORDER: ElicitationAudience[] = ["business", "data"];

const AUDIENCE_LABEL: Record<ElicitationAudience, string> = {
  business: "Business owner",
  data: "Data engineer",
};

const AUDIENCE_BLURB: Record<ElicitationAudience, string> = {
  business: "What things mean in your business. No column names, no SQL.",
  data: "Which column or join actually holds it. Needs someone who knows the database.",
};

/** `null` is the trailing bucket: a wizard record generated before the backend
 * classified severity. Kept visible rather than dropped — an unclassified question
 * is still a question someone can answer. */
const SEVERITY_ORDER: (ElicitationSeverity | null)[] = ["T1", "T2", "T3", "T4", null];

/** Each tier's copy claims exactly what its evidence supports, and no more.
 *
 * The previous wording was written before any detector existed and told the admin, flatly,
 * that a T1 gap "is wrong for everything that touches this table". At the 73% precision the
 * near-duplicate detector shipped with, that sentence was actively harmful: one card in four
 * was two columns that had always been different, and being confidently wrong in that register
 * is how an admin learns to distrust the whole tiering. Measured after `731892c`, precision
 * against BIRD-Obfuscation's own decoy manifest is 26/27 across three schemas, with one known
 * false positive that survives and is pinned. So T1 now says what a measurement says — these
 * two columns disagree on real rows — and admits the residual instead of hiding it.
 *
 * T2 covers two different things and has to say so: the keyword categories at their per-category
 * floor (a real wrong answer, scoped to one term), and a near-duplicate pair *demoted* out of T1
 * because a third column wears the same naming frame, where the detector no longer believes
 * either column is a decoy at all.
 *
 * T3's claim is definitional rather than measured — the tier *is* "unanswered means a refusal"
 * (utku-ai-setup-wizard-gap-model.md § "Tier structure") — so it keeps its absolute. T4 is the
 * same, and its copy now also says why so many of them are here at once. */
const SEVERITY_LABEL: Record<ElicitationSeverity, string> = {
  T1: "Wrong answers, spreading",
  T2: "Wrong answers, contained",
  T3: "Refusals, never wrong answers",
  T4: "Polish",
};

const SEVERITY_BLURB: Record<ElicitationSeverity, string> = {
  T1: "Two things that should agree do not, measured on real rows — so an answer can look right and be wrong, for everything that reads this data. A few will turn out to be fields that were always different; the numbers on each card are what we actually counted. Answer these first.",
  T2: "A wrong answer is possible here, but it stays inside one term, column or metric. Some of these are findings we are only half sure of — where that is so, the question says what made us unsure.",
  T3: "Left unanswered, the worst case is that we decline to answer or ask you mid-question. Never a wrong number.",
  T4: "Nothing here can make an answer wrong. Answering improves how well we find the right data and how the answers read. There are usually a lot of these, because nothing in a freshly-loaded database describes itself.",
};

/** What the bare `T1`–`T4` badge means, for the "?" beside it in `TierSection`. Readers who have
 * not memorised the tiering see only the code, not `SEVERITY_LABEL`/`SEVERITY_BLURB`'s framing
 * (which claims exactly what the detectors measured — see the block comment above them), so this
 * exists to answer a plainer question in the words `utku-ai-setup-wizard-gap-model.md` §
 * "Tier structure" uses to define the tiers at all: not how much accuracy a category bought, but
 * what happens if the gap is left unanswered.
 *
 * T1 and T2's copy both say "silently wrong" on purpose — the doc's own point is that the tiers
 * split on how far the damage spreads, not on how bad it is, and letting T1 sound like the
 * "dangerous" one and T2 the "safe" one would misstate the doc it is quoting. */
const SEVERITY_EXPLANATION: Record<ElicitationSeverity, { name: string; body: string }> = {
  T1: {
    name: "Poison",
    body: "If you leave this unanswered, the answer looks right but is silently wrong — and because it sits on a name or ID used to match rows between tables, the mistake spreads to every question about this table. You would not be able to tell. (T2 is just as silently wrong; the only difference is how far it spreads.)",
  },
  T2: {
    name: "Silent-wrong, local",
    body: "If you leave this unanswered, the answer can also look right but be silently wrong — but the damage stays inside the one term, metric or column this question is about. It does not spread to anything else.",
  },
  T3: {
    name: "Safe failure",
    body: "If you leave this unanswered, you will never get a wrong number. Instead the system may say it can't answer, ask you a follow-up question mid-conversation, or give a narrower answer than you wanted.",
  },
  T4: {
    name: "Polish",
    body: "If you leave this unanswered, nothing can come out wrong. Answers just may be a little harder to find or more clumsily worded.",
  },
};

/** T4 opens collapsed. It is the only tier that is routinely large — a freshly-seeded
 * `beer_factory` produces 18 "describe this" cards against 27 T1 findings — and a tier whose own
 * copy says it cannot make an answer wrong should not be the thing an admin scrolls past to
 * reach the ones that can. Collapsed, not truncated and not dropped: the owner's decision is
 * "list ALL gaps", the count is on the header, and one click shows every one of them. */
const COLLAPSED_BY_DEFAULT: (ElicitationSeverity | null)[] = ["T4"];

export function ElicitationWizard() {
  const candidates = useElicitationCandidates();
  const { data: caps } = useCapabilities();
  // Same fix as clarifications-panel.tsx/conflicts-panel.tsx (ef52743): neither
  // POST /elicitation/generate nor POST /clarifications/{id}/answer is gated on
  // can_edit on the backend (can_edit is a hard-coded false /capabilities report
  // for the unrelated free-form corpus editor) -- gating this wizard on canEdit
  // left every button permanently disabled the moment this tab went live.
  const editable = canCurateCorpus(caps);
  const queryClient = useQueryClient();
  const [generating, setGenerating] = useState(false);
  const [report, setReport] = useState<ScanReport | null>(null);

  async function generate() {
    if (generating) return;
    setGenerating(true);
    try {
      const response = await api.elicitationGenerate();
      await queryClient.invalidateQueries({ queryKey: ["elicitation-candidates"] });
      setReport(response.report);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to generate candidates.";
      toast.error(message);
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          A small set of AI-proposed questions about your schema, answered once before any
          business user asks a live query.
        </p>
        <Button type="button" size="sm" variant="outline" disabled={!editable || generating} onClick={() => void generate()}>
          <Sparkles className="size-3.5" />
          {generating ? "Generating…" : "Generate candidates"}
        </Button>
      </div>

      {report && <ScanReportLine report={report} />}

      <QueryState
        query={candidates}
        isEmpty={(data) => data.length === 0}
        emptyMessage={'No candidate questions yet — click "Generate candidates" to scan the schema.'}
      >
        {(data) => <GroupedCandidates records={data} editable={editable} />}
      </QueryState>
    </div>
  );
}

/** What the last re-run changed, in words. A status line, not a dashboard.
 *
 * The owner's third standing decision (utku-ai-setup-wizard-gap-model.md § "Three owner
 * decisions") is that a re-run diffs against already-confirmed content and **says so** when
 * nothing is new. What this replaces was a toast reading "No new questions to propose — the
 * schema is already covered", which was wrong twice over: it vanished after four seconds, and
 * "already covered" is a claim nothing measured (it is equally the answer for a schema where
 * every detector is structurally blind — the exact failure `curator/gaps.DetectorCoverage`
 * exists to close on the other half of the same sentence).
 *
 * The sentence is composed on the backend and rendered verbatim; see `scanReportSchema`. This
 * component adds placement and one thing the sentence cannot carry: an icon that distinguishes
 * "nothing changed" from "here is what changed" before the admin has read a word.
 *
 * Persistent rather than a toast because it is the answer to the question the button asks, and
 * an admin who clicks Generate and looks away has otherwise learned nothing. It clears on the
 * next click, never on a timer.
 */
function ScanReportLine({ report }: { report: ScanReport }) {
  return (
    <div className="flex items-start gap-2 rounded-md border bg-muted/40 p-3">
      {report.nothing_new ? (
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      ) : (
        <Sparkles className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      )}
      <p className="text-sm leading-relaxed">{report.summary}</p>
    </div>
  );
}

function GroupedCandidates({
  records,
  editable,
}: {
  records: ClarificationRecord[];
  editable: boolean;
}) {
  // Every wizard record, keyed by id, so a blocked card can name the question it is
  // waiting for instead of just its id.
  const byId = new Map(records.map((rec) => [rec.id, rec]));

  // Both tabs always render, even empty: "nothing for the business owner yet" is
  // itself worth seeing, and it is the honest report for a schema whose detectors
  // only fired on one side.
  const byAudience = new Map<ElicitationAudience, ClarificationRecord[]>(
    AUDIENCE_ORDER.map((audience) => [audience, []]),
  );
  for (const rec of records) {
    if (!rec.category) continue;
    // No audience recorded (a record generated before the backend classified them)
    // falls to the data tab: every question this generator can produce is answerable
    // by someone who can read a schema, and an extra question costs a DBA less than
    // showing a business owner a raw column name costs the business owner.
    byAudience.get(rec.audience ?? "data")!.push(rec);
  }
  const firstNonEmpty = AUDIENCE_ORDER.find((a) => byAudience.get(a)!.length > 0);

  return (
    <Tabs defaultValue={firstNonEmpty ?? "business"}>
      <TabsList>
        {AUDIENCE_ORDER.map((audience) => (
          <TabsTrigger key={audience} value={audience} className="gap-1.5">
            {AUDIENCE_LABEL[audience]}
            <span className="text-xs text-muted-foreground">
              {byAudience.get(audience)!.length}
            </span>
          </TabsTrigger>
        ))}
      </TabsList>
      {AUDIENCE_ORDER.map((audience) => (
        <TabsContent key={audience} value={audience} className="space-y-6 pt-2">
          <p className="text-xs text-muted-foreground">{AUDIENCE_BLURB[audience]}</p>
          <TierSections
            records={byAudience.get(audience)!}
            byId={byId}
            editable={editable}
          />
        </TabsContent>
      ))}
    </Tabs>
  );
}

/** One audience's questions, split into severity tiers, worst-consequence first. */
function TierSections({
  records,
  byId,
  editable,
}: {
  records: ClarificationRecord[];
  byId: Map<string, ClarificationRecord>;
  editable: boolean;
}) {
  if (records.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No questions for this audience — nothing here needs their input yet.
      </p>
    );
  }

  const byTier = new Map<ElicitationSeverity | null, ClarificationRecord[]>();
  for (const rec of records) {
    const tier = rec.severity ?? null;
    const group = byTier.get(tier) ?? [];
    group.push(rec);
    byTier.set(tier, group);
  }

  return (
    <>
      {SEVERITY_ORDER.filter((tier) => (byTier.get(tier)?.length ?? 0) > 0).map((tier) => (
        <TierSection
          key={tier ?? "unclassified"}
          tier={tier}
          records={byTier.get(tier)!}
          byId={byId}
          editable={editable}
        />
      ))}
    </>
  );
}

function TierSection({
  tier,
  records,
  byId,
  editable,
}: {
  tier: ElicitationSeverity | null;
  records: ClarificationRecord[];
  byId: Map<string, ClarificationRecord>;
  editable: boolean;
}) {
  const [open, setOpen] = useState(!COLLAPSED_BY_DEFAULT.includes(tier));
  const unanswered = records.filter((rec) => rec.status !== "answered").length;

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Badge variant={tier === "T1" ? "destructive" : "secondary"}>{tier ?? "?"}</Badge>
          {tier && <SeverityHelp tier={tier} />}
          <h3 className="text-sm font-medium">{tier ? SEVERITY_LABEL[tier] : "Unclassified"}</h3>
          <span className="text-xs text-muted-foreground">
            {unanswered === records.length
              ? records.length
              : `${unanswered} of ${records.length} left`}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 gap-1 px-2 text-xs font-normal text-muted-foreground"
            aria-expanded={open}
            onClick={() => setOpen((was) => !was)}
          >
            {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            {open ? "Hide" : "Show"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {tier
            ? SEVERITY_BLURB[tier]
            : "Proposed before this wizard recorded what an unanswered gap costs."}
        </p>
      </div>
      {open &&
        sortWithinTier(records).map((rec) => (
          <ElicitationCard key={rec.id} record={rec} byId={byId} editable={editable} />
        ))}
    </div>
  );
}

/** The "?" beside a tier badge. A button rather than a bare icon so it is a real focus target —
 * Radix's tooltip trigger opens on keyboard focus as well as hover, which a `<span>` cannot
 * receive at all: a hover-only affordance would be invisible to anyone not using a mouse. */
function SeverityHelp({ tier }: { tier: ElicitationSeverity }) {
  const { name, body } = SEVERITY_EXPLANATION[tier];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="flex size-4 shrink-0 items-center justify-center rounded-full text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
          aria-label={`What ${tier} means`}
        >
          <HelpCircle className="size-3.5" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent className="flex-col items-start gap-1 py-2 text-left" sideOffset={4}>
        <p className="font-medium">
          {tier} — {name}
        </p>
        <p className="font-normal">{body}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/** Answerable questions before blocked ones (the gap model's dependency rule is a hard
 * constraint on sequencing, not a preference), then the category prior as the tie-break. */
function sortWithinTier(records: ClarificationRecord[]): ClarificationRecord[] {
  return [...records].sort((a, b) => {
    const blockedDelta = Number(a.blocked ?? false) - Number(b.blocked ?? false);
    if (blockedDelta !== 0) return blockedDelta;
    return (
      CATEGORY_ORDER.indexOf(a.category as ElicitationCategory) -
      CATEGORY_ORDER.indexOf(b.category as ElicitationCategory)
    );
  });
}

function ElicitationCard({
  record,
  byId,
  editable,
}: {
  record: ClarificationRecord;
  byId: Map<string, ClarificationRecord>;
  editable: boolean;
}) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function submit(body: { choiceId?: string; choiceIds?: string[]; answer?: string }) {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.answerClarification(record.id, body);
      toast.success(`Answered ${record.id}`);
      await queryClient.invalidateQueries({ queryKey: ["elicitation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["assumptions"] });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to submit the answer.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  // Trusted from the server (GET /elicitation/candidates' derived field), never
  // recomputed here — one definition of "may this be answered yet", on the side that
  // also owns the ledger.
  const blocked = record.blocked === true;
  // Answered before the question it depends on. `answer_clarification` stamps which
  // prerequisites were still open at that moment, and `fold_ledger_answer_into_corpus`
  // reads the stamp and lands the corpus fact `draft` instead of `proposed` — a status
  // `approve_draft` refuses, with nothing anywhere that promotes it back. Until now the
  // card was indistinguishable from a fully-warranted answer, which made the strongest
  // claim the wizard can make ("Answered") about its weakest state.
  const stranded =
    record.status === "answered" && (record.unmet_prerequisites_at_answer ?? []).length > 0;

  return (
    <Card className={blocked ? "border-dashed opacity-80" : undefined}>
      <CardHeader>
        <div className="flex items-start gap-2">
          {blocked ? (
            <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          ) : (
            <ListChecks className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          )}
          <div className="flex-1 space-y-1">
            <CardTitle className="text-sm leading-snug font-medium">{record.question}</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              {record.category && (
                // Category as a label, not a grouping level — see the module docstring.
                <Badge variant="outline" className="gap-1 font-normal">
                  <span className="font-medium">{record.category}</span>
                  <span className="text-muted-foreground">
                    {CATEGORY_LABEL[record.category]}
                  </span>
                </Badge>
              )}
              {/* Data tab only. This line put `mobile_app_market.content_rating` under every
                  business-audience card no matter how the question was worded, which is the
                  same leak the backend's `find_schema_leak` guard blocks in the question text
                  (`curator/elicitation.py::enforce_audience_language`) — a guard the card was
                  quietly working around. A business question now names its table and field in
                  plain words in the question itself, which is where a non-technical reader is
                  looking anyway; a DBA still gets the exact identifier here. */}
              {record.audience !== "business" && record.target_table && (
                <span className="font-mono text-xs text-muted-foreground">
                  {record.target_table}
                  {record.target_column ? `.${record.target_column}` : ""}
                </span>
              )}
            </div>
          </div>
          {record.status === "answered" && (
            <Badge
              variant={stranded ? "outline" : "secondary"}
              className="shrink-0 gap-1 text-muted-foreground"
            >
              {stranded ? <TriangleAlert className="size-3" /> : <CheckCircle2 className="size-3" />}
              {stranded ? "Answered early" : "Answered"}
            </Badge>
          )}
          {blocked && record.status !== "answered" && (
            <Badge variant="outline" className="shrink-0 text-muted-foreground">
              Waiting
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {record.status === "answered" ? (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">{record.answer}</p>
            {stranded && (
              // Stated on the card and not only in the scan report, because this is where an
              // admin looks to find out what happened to a question they answered. It says what
              // is true and stops: there is no revise path in this wizard (utku-ai-design-gaps
              // #4), so promising that answering the prerequisite now would fix it would be a
              // second wrong claim on top of the one this line exists to correct.
              <p className="rounded-md border border-dashed p-2 text-xs text-muted-foreground">
                Answered before the question it depends on, so this is on file as a draft that
                cannot be approved. There is no way to revise it here yet.
              </p>
            )}
          </div>
        ) : blocked ? (
          <BlockedNotice record={record} byId={byId} />
        ) : (
          <AnswerWidget record={record} editable={editable} submitting={submitting} onSubmit={submit} />
        )}
      </CardContent>
    </Card>
  );
}

/** Why a card is waiting, in place of its answer form. Shown rather than hidden so the
 * admin can see the order the wizard is imposing and go answer the blocker: certifying a
 * value mapping on a column that turns out to be a near-duplicate decoy makes the wrong
 * column authoritative, and nobody looking at a value checklist can tell which it is.
 *
 * The backend deliberately still accepts a POST for a blocked record (a DBA with no
 * business counterpart has to be able to answer the engineering half standalone) — it
 * stamps the answer as unwarranted instead of refusing it. This surface is the one that
 * imposes the order, which is why the form is absent rather than merely disabled. */
function BlockedNotice({
  record,
  byId,
}: {
  record: ClarificationRecord;
  byId: Map<string, ClarificationRecord>;
}) {
  return (
    <div className="space-y-2 rounded-md border border-dashed p-3 text-sm text-muted-foreground">
      <p>Answer this first:</p>
      <ul className="list-disc space-y-1 pl-5">
        {(record.blocked_by ?? []).map((id) => {
          const prerequisite = byId.get(id);
          return (
            <li key={id}>
              {prerequisite ? (
                <>
                  {prerequisite.question}
                  {prerequisite.status === "answered" && " (answered)"}
                </>
              ) : (
                <>
                  a question that is not in this ledger —{" "}
                  <code className="font-mono text-xs">{id}</code>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AnswerWidget({
  record,
  editable,
  submitting,
  onSubmit,
}: {
  record: ClarificationRecord;
  editable: boolean;
  submitting: boolean;
  onSubmit: (body: { choiceId?: string; choiceIds?: string[]; answer?: string }) => void;
}) {
  if (record.ui_modality === "checklist") {
    return (
      <ElicitationChecklistForm
        choices={record.choices ?? []}
        disabled={!editable}
        submitting={submitting}
        onSubmit={(answer) => onSubmit(answer)}
      />
    );
  }

  return (
    <ClarificationAnswerForm
      choices={record.choices}
      allowFreeform={record.allow_freeform}
      disabled={!editable}
      submitting={submitting}
      inputType={record.ui_modality === "numeric" ? "number" : "text"}
      freeformPlaceholder={record.ui_modality === "numeric" ? "e.g. 1" : undefined}
      freeformAriaLabel={`Answer for ${record.id}`}
      onSubmit={(answer) => onSubmit({ choiceId: answer.choiceId, answer: answer.answer })}
    />
  );
}
