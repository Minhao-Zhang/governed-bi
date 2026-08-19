/**
 * Red/green check: ReAct tool frames must not become transcript bubbles.
 *
 *     npm run check:stream-messages
 *
 * Reproduces the screenshot failure mode: after a finished turn, tool outputs
 * like `{"columns":…,"rows":…}` were rendered as assistant text beside the
 * AnswerCard. This script fails if any mapped bubble still carries that spill.
 */

import {
  flattenContent,
  mapStreamToChatMessages,
  parseAnswer,
  turnAnswersByMessageId,
  turnFinalAiFrames,
  type StreamWireMessage,
} from "../lib/stream-messages.ts";
import type { AnswerView } from "../lib/types.ts";

const TOOL_JSON =
  '{"columns": ["city_alias", "population"], "rows": [], "truncated": false, "row_count": 0}';
const TOOL_JSON_2 =
  '{"columns": ["min_date", "max_date", "shipment_count", "shipments_in_2020"], "rows": [["2016-01-08 00:00:00", "2017-12-27 00:00:00", 960, 0]], "truncated": false, "row_count": 1}';

const channelAnswer = {
  outcome: "answered",
  text: null,
  answer_text: "No city population data for 2020 is available in the governed context.",
  record: {
    generated_sql: 'SELECT … FROM "shipping"."expedition"',
    execution: { attempts: [{}, {}, {}], terminal: "answered" },
  },
} as AnswerView;

const wire: StreamWireMessage[] = [
  { id: "h1", type: "human", content: "Provide the alias of the city with the highest population in year 2020." },
  { id: "a1", type: "ai", content: "" },
  { id: "t1", type: "tool", content: TOOL_JSON },
  {
    id: "t2",
    type: "tool",
    content: "That identifier is not available in this conversation. Work from the assets in the context you were given.",
  },
  { id: "a2", type: "ai", content: "" },
  { id: "t3", type: "tool", content: TOOL_JSON_2 },
  { id: "a3", type: "ai", content: channelAnswer.answer_text },
];

/**
 * The pre-fix mapper (last non-human wins; every other frame becomes text when
 * idle). Kept here so the check stays red-capable against the defect, not just
 * green against the fix.
 */
function mapBuggy(messages: StreamWireMessage[], answer: AnswerView | null) {
  let lastAssistantIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.type !== "human") {
      lastAssistantIndex = i;
      break;
    }
  }
  return messages
    .map((message, index) => {
      const id = message.id ?? `stream-${index}`;
      if (message.type === "human") {
        return { id, role: "user" as const, text: flattenContent(message.content) };
      }
      if (index === lastAssistantIndex && answer) {
        return { id, role: "assistant" as const, answer };
      }
      const governed = message.additional_kwargs?.governed_bi;
      if (governed != null) {
        const parsed = parseAnswer(governed);
        if (parsed) return { id, role: "assistant" as const, answer: parsed };
      }
      const text = flattenContent(message.content);
      if (text.trim() === "") return null;
      return { id, role: "assistant" as const, text };
    })
    .filter((m): m is NonNullable<typeof m> => m !== null);
}

function hasToolSpill(
  rows: Array<{ role: string; text?: string; answer?: AnswerView | null }>,
): boolean {
  return rows.some(
    (m) =>
      m.role === "assistant" &&
      typeof m.text === "string" &&
      (m.text.includes('"columns"') || m.text.includes("That identifier is not available")),
  );
}

let failed = false;
function check(cond: boolean, msg: string) {
  if (!cond) {
    failed = true;
    console.error(`FAIL: ${msg}`);
  } else {
    console.log(`ok: ${msg}`);
  }
}

// The defect: buggy mapper spills tool JSON into the transcript.
check(hasToolSpill(mapBuggy(wire, channelAnswer)), "buggy mapper still reproduces the screenshot spill");

const mapped = mapStreamToChatMessages({
  messages: wire,
  channelAnswer,
  isRunning: false,
  awaitingClarification: false,
  stepsFor: () => undefined,
});

check(mapped.filter((m) => m.role === "user").length === 1, "one user bubble");
check(mapped.filter((m) => m.answer != null).length === 1, "exactly one AnswerCard");
check(!hasToolSpill(mapped), "fixed mapper drops tool JSON from transcript text");
check(
  mapped.every((m) => m.role === "user" || m.answer != null),
  "no plain-text assistant bubbles when channel answer exists",
);

const midRun = mapStreamToChatMessages({
  messages: wire,
  channelAnswer: null,
  isRunning: true,
  awaitingClarification: false,
  stepsFor: () => undefined,
});
check(
  midRun.length === 1 && midRun[0]?.role === "user",
  "mid-run suppresses all assistant frames",
);

/**
 * **Asking a follow-up must not change how the turns above it render.**
 *
 * The defect: the mapper took "the last AI frame" to mean "the newest turn". `submit()` appends
 * the human frame and flips `isLoading` in one commit, and the engine then runs guard, the facet
 * fan-out, routing and assembly before the agent emits a token — so for seconds the last AI
 * frame was still the *previous* turn's answer. It was excluded from the prior-turn branch (by
 * `index !== lastAiIndex`) and from the channel-answer branch (by `!idle`), and fell through to
 * `return null`: the answer card disappeared from a live conversation and returned when the new
 * turn started streaming. Reproduced against thread 019fdc1c's real message list.
 */
const turn = (n: number, text: string): StreamWireMessage[] => [
  { id: `h${n}`, type: "human", content: `question ${n}` },
  { id: `a${n}tc`, type: "ai", content: "" },
  { id: `t${n}`, type: "tool", content: TOOL_JSON },
  { id: `a${n}`, type: "ai", content: text },
];
const settled = [...turn(1, "The alias is Katy."), ...turn(2, "The display name is whuber.")];
const recordFor = (id: string) =>
  ({ outcome: "answered", text: null, answer_text: `record for ${id}`, record: {} }) as AnswerView;

/**
 * @param known  Message ids we hold a record for — a turn captured live, or the log joined onto
 *   ids while the thread was idle. Defaults to both settled turns, which is the steady state.
 */
const phase = (
  messages: StreamWireMessage[],
  isRunning: boolean,
  answer: AnswerView | null,
  known: readonly string[] = ["a1", "a2"],
) =>
  mapStreamToChatMessages({
    messages,
    channelAnswer: answer,
    answerFor: (id) => (known.includes(id) ? recordFor(id) : undefined),
    isRunning,
    awaitingClarification: false,
    stepsFor: () => undefined,
  });

const cardsOf = (rows: ReturnType<typeof phase>) =>
  rows.filter((m) => m.answer != null).map((m) => m.id);

const before = phase(settled, false, channelAnswer);
// The follow-up is in flight and its first AI frame has not arrived.
const inFlight = phase([...settled, { id: "h3", type: "human", content: "question 3" }], true, channelAnswer);
// …and now it has.
const streaming = phase(
  [...settled, { id: "h3", type: "human", content: "question 3" }, { id: "a3tc", type: "ai", content: "" }],
  true,
  null,
);

check(cardsOf(before).join() === "a1,a2", "settled two-turn thread renders both cards");
check(
  cardsOf(inFlight).join() === "a1,a2",
  "submitting a follow-up leaves both earlier cards on screen",
);
check(
  cardsOf(streaming).join() === "a1,a2",
  "the follow-up's first AI frame does not disturb the earlier cards",
);
check(
  inFlight.filter((m) => m.role === "assistant" && m.answer == null).length === 0,
  "no earlier turn degrades to a bare-text bubble while a follow-up runs",
);

/**
 * **The submit window: a run is live and the new human frame has not landed yet.**
 *
 * `submit()` flips `isLoading` true, then awaits the SSE pump before appending the optimistic
 * human message (`SubmitCoordinator.#waitForRootPumpReady`). For that round-trip the last AI frame
 * is still the previous turn's answer with nothing after it — measured at ~700ms in the browser,
 * and it blanked the newest card even after the human-boundary fix, because a frame that is not
 * behind a later question cannot be recognised as settled from the frames alone. A record keyed on
 * the message is what carries it: an identity does not care whether the graph is busy.
 */
const submitWindow = phase(settled, true, null);
check(
  cardsOf(submitWindow).join() === "a1,a2",
  `a live run with no new human frame yet keeps both cards (got ${cardsOf(submitWindow).join()})`,
);
// …and the turn actually in flight must not be given a card. Nothing is keyed to its message, so
// there is nothing for it to claim — which is why the log is joined onto ids while idle rather
// than consulted per render: it can hold a row for the very question now being asked.
const liveTurn = phase(
  [...settled, { id: "h3", type: "human", content: "question 3" }, { id: "a3", type: "ai", content: "partial…" }],
  true,
  null,
);
check(
  cardsOf(liveTurn).join() === "a1,a2",
  `the in-flight turn renders no card of its own (got ${cardsOf(liveTurn).join()})`,
);

/**
 * **A turn that writes no message still gets its card, and does not steal anyone else's.**
 *
 * `decline_node` (retrieval matched no schema) returns a terminal state with no `messages` write,
 * so the turn has a record and no AI frame. Two defects followed: the append-a-card fallback was
 * gated on the *whole transcript* having no card, so from turn two on the decline rendered
 * nothing at all; and the log was keyed by counting AI frames, so a declined turn shifted every
 * later turn's record by one.
 */
const declined = { outcome: "refused", text: null, answer_text: "No schema matched.", record: {} } as AnswerView;
const afterDecline = phase([...settled, { id: "h3", type: "human", content: "question 3" }], false, declined);
check(
  cardsOf(afterDecline).join() === "a1,a2,channel-answer",
  "a declined newest turn still renders its card below the earlier ones",
);

// …and the turn it left frameless must not shift which record the later turns claim. Turn 2 here
// declined, so turn 3's answer frame belongs to turn *2* by index and turn 3 by question count.
const withDeclineInMiddle: StreamWireMessage[] = [
  ...turn(1, "The alias is Katy."),
  { id: "h2", type: "human", content: "question 2" }, // declined: no AI frame
  ...turn(3, "The display name is whuber."),
];
const frames = turnFinalAiFrames(withDeclineInMiddle);
check(
  frames.map((f) => `${f.id}@${f.turn}`).join() === "a1@0,a3@2",
  `a declined turn does not shift later turns' records (got ${frames.map((f) => `${f.id}@${f.turn}`).join()}, want a1@0,a3@2)`,
);
check(
  turnFinalAiFrames(settled).map((f) => `${f.id}@${f.turn}`).join() === "a1@0,a2@1",
  "tool-call shells and intermediate frames are not turn-final",
);

/**
 * **Records come off the thread's own `turns` channel, keyed to message ids.**
 *
 * This replaced a fetch of `GET /audit/turns` matched to the transcript by question *text*.
 * The text match existed because the log is a file queried by `thread_id` and holds rows this
 * conversation never showed (measured on thread `019fdc77`: four rows for a two-turn
 * transcript), so neither a position nor a string was an identity. `turns` is the same store
 * the messages come from — `record_node` appends one row per recorded turn of this thread, and
 * refuses a row with no `turn_id` — so the checks below are about placing rows, not matching
 * them.
 */
const turnRow = (opts: {
  turnId: string;
  question: string;
  answerText: string;
  outcome?: string | null;
  /** The 1-based question number the record states, via its usage rows (see `stamp`). */
  turnIndex?: number;
}) => ({
  asked_at: "2026-08-18T00:00:00+00:00",
  question: opts.question,
  answer_text: opts.answerText,
  outcome: opts.outcome ?? "answered",
  record: {
    turn_id: opts.turnId,
    thread_id: "019fdc77",
    ...(opts.turnIndex === undefined
      ? {}
      : { usage: [{ turn_index: opts.turnIndex, stage: "guard", model: "m" }] }),
  },
});

const twoTurns = [
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy", turnIndex: 1 }),
  turnRow({ turnId: "t2", question: "question 2", answerText: "whuber", turnIndex: 2 }),
];
const placed = (rows: unknown[], messages: StreamWireMessage[]) =>
  [...turnAnswersByMessageId(rows, messages).entries()]
    .map(([id, a]) => `${id}=${a.answer_text}`)
    .join(",");

check(
  placed(twoTurns, settled) === "a1=Katy,a2=whuber",
  `each turn's record lands on its own answer frame (got ${placed(twoTurns, settled)})`,
);

// **A declined turn holds its slot.** `decline_node` writes no message, so turn 2 here has a
// row and no frame: turn 3's record must still land on turn 3's frame. Reading the rows by
// arrival order against the *frames* would put turn 2's decline under turn 3's answer.
const withDecline = [
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy", turnIndex: 1 }),
  turnRow({
    turnId: "t2",
    question: "question 2",
    answerText: "No schema matched.",
    outcome: "refused",
    turnIndex: 2,
  }),
  turnRow({ turnId: "t3", question: "question 3", answerText: "whuber", turnIndex: 3 }),
];
check(
  placed(withDecline, withDeclineInMiddle) === "a1=Katy,a3=whuber",
  `a declined turn claims no later turn's frame (got ${placed(withDecline, withDeclineInMiddle)})`,
);

// **A question that left no row at all.** A run killed before `record` appends nothing, so
// arrival order under-counts every row after it — the stated question number is what carries
// them. Here turn 1 left no row and turn 2's answer frame is the only one.
const gap = [turnRow({ turnId: "t2", question: "question 2", answerText: "whuber", turnIndex: 2 })];
check(
  placed(gap, settled) === "a2=whuber",
  `a row states its own question number rather than inheriting a position (got ${placed(gap, settled)})`,
);

// …and with no stated number (a turn that made no model call records an empty `usage`), arrival
// order is the fallback — sound here because the rows cannot come from another conversation.
const unstated = [
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy" }),
  turnRow({ turnId: "t2", question: "question 2", answerText: "whuber" }),
];
check(
  placed(unstated, settled) === "a1=Katy,a2=whuber",
  `rows with no stated number fall back to arrival order (got ${placed(unstated, settled)})`,
);

// A row whose record carries no `turn_id` is not a recorded turn — `record_node` will not append
// one — and must not take a turn's slot from the row that follows it.
const forged = [
  { asked_at: null, question: "junk", answer_text: "junk", outcome: "answered", record: {} },
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy" }),
];
check(
  placed(forged, settled) === "a1=Katy",
  `a row with no turn_id is skipped without consuming a slot (got ${placed(forged, settled)})`,
);

// The same turn appearing twice must not be placed twice, and must not shift the cursor.
const duplicated = [
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy" }),
  turnRow({ turnId: "t1", question: "question 1", answerText: "Katy again" }),
  turnRow({ turnId: "t2", question: "question 2", answerText: "whuber" }),
];
check(
  placed(duplicated, settled) === "a1=Katy,a2=whuber",
  `a duplicated turn_id is placed once (got ${placed(duplicated, settled)})`,
);

// An unparseable outcome is dropped rather than rendered with a made-up one, and a row for a
// turn with no answer frame claims nothing.
check(
  placed([turnRow({ turnId: "t1", question: "q", answerText: "x", outcome: "nonsense" })], settled)
    === "",
  "a row whose outcome is outside the engine's vocabulary is dropped",
);
check(
  turnAnswersByMessageId(twoTurns, turn(1, "The alias is Katy.")).size === 1,
  "a row whose turn left no frame claims nothing",
);
check(turnAnswersByMessageId(undefined, settled).size === 0, "no turns channel yields no records");

if (failed) {
  console.error("\nmapped messages:", JSON.stringify(mapped, null, 2));
  process.exit(1);
}
console.log("\nall checks passed");
