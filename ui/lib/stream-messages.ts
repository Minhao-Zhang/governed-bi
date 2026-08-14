/**
 * Map LangGraph `stream.messages` into the transcript the chat UI renders.
 *
 * The serve agent streams its whole ReAct loop through `messages` (AI turns,
 * tool results, system frames). Those are node-local reasoning — the live
 * surface for them is the AgentTimeline (ADR 0001 / gotcha G2). This module is
 * the gate that keeps tool dumps like `{"columns":…,"rows":…}` out of the
 * bubble list once the turn settles.
 */

import { answerViewSchema } from "./schemas.ts";
import type { TimelineStep } from "./steps.ts";
import type { AnswerView } from "./types.ts";

/** The wire fields we read off each `stream.messages` entry. */
export interface StreamWireMessage {
  id?: string;
  type?: string;
  content?: unknown;
  additional_kwargs?: Record<string, unknown>;
}

/** Transcript row — mirrors `ChatMessage` in `hooks/use-chat` without importing it. */
export interface MappedChatMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  answer?: AnswerView;
  steps?: TimelineStep[];
}

export function flattenContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part === "object" && "text" in part) {
          return String((part as { text: unknown }).text ?? "");
        }
        return "";
      })
      .join("");
  }
  return "";
}

export function parseAnswer(raw: unknown): AnswerView | null {
  const parsed = answerViewSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/** Frames that are never a user-visible chat bubble. */
function isInternalFrame(type: string | undefined): boolean {
  return type === "tool" || type === "system" || type === "function" || type === "remove-ui" || type === "ui";
}

function isAiFrame(type: string | undefined): boolean {
  return type === "ai" || type === "AIMessageChunk" || type === "AIMessage";
}

/**
 * Is this AI frame the *last* one of its turn?
 *
 * A turn ends where the next visible frame is human (or where the transcript does). Needed
 * because `values.answer` only ever describes the newest turn — `PER_TURN_RESET` clears the
 * record channels every turn — so every earlier turn has to be recognised from the messages
 * alone, which are the only thing that accumulates.
 */
function lastAiIndexOf(messages: readonly StreamWireMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) if (isAiFrame(messages[i]?.type)) return i;
  return -1;
}

function lastHumanIndexOf(messages: readonly StreamWireMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i]?.type === "human") return i;
  return -1;
}

/** A frame that carries a turn's answer, and the 0-based turn it answers. */
export interface TurnFrame {
  id: string;
  turn: number;
}

/**
 * The frames that carry an answer, each tagged with the turn it answers.
 *
 * Exported for the hook, which uses it to attach the durable log's rows to message ids; it shares
 * {@link isTurnFinalAi} with the mapper so the two agree on what an answer frame is.
 *
 * Turns are counted in *questions* — the (N+1)th human frame opens turn N — and not in answers,
 * because not every turn leaves one: `decline_node` (retrieval matched no schema) returns a
 * terminal state and writes no message, so counting answers shifted every later turn's record by
 * one, and turn three's card showed turn two's SQL.
 */
export function turnFinalAiFrames(messages: readonly StreamWireMessage[]): TurnFrame[] {
  const out: TurnFrame[] = [];
  let asked = 0;
  messages.forEach((message, index) => {
    if (message?.type === "human") {
      asked += 1;
      return;
    }
    if (isTurnFinalAi(messages, index)) {
      out.push({ id: message.id ?? `stream-${index}`, turn: asked - 1 });
    }
  });
  return out;
}

function isTurnFinalAi(messages: readonly StreamWireMessage[], index: number): boolean {
  if (!isAiFrame(messages[index]?.type)) return false;
  for (let i = index + 1; i < messages.length; i++) {
    const type = messages[i]?.type;
    if (isInternalFrame(type)) continue;
    return type === "human";
  }
  return true;
}

/** One row of `GET /audit/turns`, reduced to the join key and the card it can render. */
export interface LoggedTurn {
  question: string;
  answer: AnswerView;
}

/**
 * Pair each of a thread's questions with the logged turn that answered it.
 *
 * How the log gets attached to the ids {@link mapStreamToChatMessages} asks about, so it lives
 * beside it. The log's rows carry no message id and no `turn_index`, so the question text is the
 * only join key on offer — and a join is needed rather than a position because the log holds rows
 * this conversation never showed. Measured on thread `019fdc77`: two turns in `messages`, four
 * rows under that `thread_id`, two of them from an earlier run asking questions that appear
 * nowhere in the transcript. Indexing by position put one of those under turn one, which renders a
 * real question with a foreign turn's SQL, stamp and provenance beneath it.
 *
 * Matching walks forward and never re-claims a row, so a thread that asks the same question
 * twice attributes the two answers in the order they were given, and an unmatched row is simply
 * never used.
 *
 * @param frames  One entry per `stream.messages` frame: the question text for a human frame,
 *   `null` for anything else. Turn N is the (N+1)th non-null entry.
 * @param logged  The thread's log rows, oldest first.
 * @returns Answers indexed by 0-based turn number; `undefined` where no row matched.
 */
export function alignLogToQuestions(
  frames: readonly (string | null)[],
  logged: readonly LoggedTurn[],
): readonly (AnswerView | undefined)[] {
  const out: (AnswerView | undefined)[] = [];
  let from = 0;
  for (const question of frames) {
    if (question == null) continue;
    const at = logged.findIndex((row, i) => i >= from && row.question === question);
    out.push(at >= 0 ? logged[at]?.answer : undefined);
    if (at >= 0) from = at + 1;
  }
  return out;
}

/**
 * Fold `stream.messages` into transcript rows.
 *
 * - Human frames → user bubbles.
 * - Tool / system / function frames → dropped (timeline owns them).
 * - Settled turns (anything before the last human frame) → one card each, always, whatever the
 *   current run is doing.
 * - The newest turn, finished, with a channel `AnswerView` → a card on its AI frame, or appended
 *   when it left none.
 * - The newest turn, mid-run or suspended at a clarification → no bubble (ServeProgress is live).
 * - Finished turn without a channel answer → last AI text only, as a fallback.
 */
export function mapStreamToChatMessages(args: {
  messages: readonly StreamWireMessage[];
  channelAnswer: AnswerView | null;
  /**
   * **The record for a finished turn, keyed on the id of the message that carries its answer.**
   *
   * Keyed on the message and not on a position, so it is an identity rather than a guess, and
   * therefore safe to ask about *any* frame — including the newest. That is what makes it
   * load-bearing rather than a cache. `submit()` flips `isLoading` true and then awaits the SSE
   * pump before appending the optimistic human frame
   * (`SubmitCoordinator.#waitForRootPumpReady`), so for one network round-trip a run is live and
   * the last AI frame is still the *previous* turn's answer, with no new human frame yet to put
   * it behind. Measured in the browser: ~700ms of the newest card blanking. A record that is
   * exact per message carries it through that window without having to ask whether the graph is
   * busy — which is unanswerable from the frames alone.
   *
   * The caller fills it from two places: the answer it saw when a turn finished in this session,
   * and the durable log joined onto message ids at fetch time — see `hooks/use-stream-chat`.
   */
  answerFor?: (id: string) => AnswerView | undefined;
  isRunning: boolean;
  awaitingClarification: boolean;
  stepsFor: (id: string, answer: AnswerView) => TimelineStep[] | undefined;
}): MappedChatMessage[] {
  const { messages, channelAnswer, answerFor, isRunning, awaitingClarification, stepsFor } = args;
  const idle = !isRunning && !awaitingClarification;

  // **The turn boundary is the last *human* frame, not the last AI frame.**
  //
  // That distinction is most of the fix for a follow-up question blanking the answer above it.
  // The new human frame is appended and `isLoading` flips, but the new turn has no AI frame yet —
  // the engine runs guard, the facet fan-out, routing and context assembly before the agent emits
  // a token, which is seconds of wall clock. For that whole window the last AI frame is still the
  // *previous* turn's answer, so treating "last AI frame" as "the newest turn" excluded it from
  // the settled-turn branch while `!idle` excluded it from the channel-answer branch, and it fell
  // through to `return null`: the card vanished from a live conversation and came back when the
  // new turn started streaming.
  //
  // Anything before the last human frame is settled history by construction, whatever the run is
  // doing. `newestAiIndex` is the current turn's AI frame only when one has actually arrived, so
  // nothing else can be mistaken for it.
  const lastHumanIndex = lastHumanIndexOf(messages);
  const lastAiIndex = lastAiIndexOf(messages);
  const newestAiIndex = lastAiIndex > lastHumanIndex ? lastAiIndex : -1;

  const out: MappedChatMessage[] = messages
    .map((message, index): MappedChatMessage | null => {
      const id = message.id ?? `stream-${index}`;
      const type = message.type;

      if (type === "human") {
        return { id, role: "user", text: flattenContent(message.content) };
      }

      // Tool results and other node-local frames never become bubbles.
      if (isInternalFrame(type)) return null;

      // Prefer the graph-state answer channel on the latest AI message once the
      // run finishes. `!awaitingClarification` is not redundant with `!isRunning`:
      // at an interrupt the SDK reports `isLoading: false`, so this is the one
      // moment a *suspended* turn could render a finished answer.
      if (index === newestAiIndex && channelAnswer && idle) {
        return { id, role: "assistant", answer: channelAnswer, steps: stepsFor(id, channelAnswer) };
      }

      const governed = message.additional_kwargs?.governed_bi;
      if (governed != null) {
        const parsed = parseAnswer(governed);
        if (parsed) return { id, role: "assistant", answer: parsed, steps: stepsFor(id, parsed) };
      }

      // **A turn that has already finished**, above the `!idle` guard on purpose so a running
      // turn does not blank the answers that came before it.
      //
      // `channelAnswer` describes the newest turn only: the record channels it comes from are
      // cleared every turn (`PER_TURN_RESET`), so there is no `AnswerView` to be had for turn
      // one — its answer exists solely as this AI frame's text. Dropping it is what made a
      // two-turn thread render two questions and one card, live and on reload both. Text is
      // not the full card (no SQL, no provenance — those need the turn's record), but it is
      // the answer, and losing it was the defect.
      if (isTurnFinalAi(messages, index)) {
        // Asked about *every* frame, the newest included: a record keyed on this message is an
        // identity, so it means "this turn finished" whatever the graph is doing now. That is
        // what carries the previous answer through the submit window — see `answerFor`.
        const record = answerFor?.(id);
        if (record) {
          return { id, role: "assistant", answer: record, steps: stepsFor(id, record) };
        }
        // No record, but the frame is behind the newest question, so its turn is over: its text
        // is still the answer, and showing it beats showing nothing, which is what used to
        // happen. Not the full card — no SQL, no provenance, those need the turn's record.
        if (index < lastHumanIndex) {
          const turnText = flattenContent(message.content);
          return turnText.trim() === "" ? null : { id, role: "assistant", text: turnText };
        }
      }

      // Mid-turn: the AgentTimeline is the live surface — don't spill ReAct
      // frames into the transcript.
      if (!idle) return null;

      // Intermediate AI frames inside a turn are chatter — tool-call shells, partial tokens —
      // not a second answer.
      if (channelAnswer) return null;

      // No channel answer at all: keep the last AI frame's text so an engine that predates
      // `values.answer` still shows something.
      if (index !== newestAiIndex) return null;
      const text = flattenContent(message.content);
      if (text.trim() === "") return null;
      return { id, role: "assistant", text };
    })
    .filter((message): message is MappedChatMessage => message !== null);

  // **Channel answer landed with no AI message frame to hang on — append one.**
  //
  // Conditioned on the *newest turn* having no frame (`newestAiIndex < 0`), not on the whole
  // transcript having no card. It used to be the latter, and that read as "nothing to do here"
  // the moment any earlier turn had a card — so on turn two onward a `decline` (which writes no
  // message) rendered no answer at all. `newestAiIndex < 0` is exactly the condition this
  // fallback describes: when a frame exists, the branch above has already carried the card.
  if (idle && channelAnswer && newestAiIndex < 0) {
    out.push({
      id: "channel-answer",
      role: "assistant",
      answer: channelAnswer,
      steps: stepsFor("channel-answer", channelAnswer),
    });
  }

  return out;
}
