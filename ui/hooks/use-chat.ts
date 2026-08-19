"use client";

/**
 * useChat — the chat cockpit's state machine.
 *
 * ── MOCK TRANSPORT (current) ────────────────────────────────────────────────
 * No LangGraph Server is attached yet (USE_MOCKS is the default), so this hook
 * FAKES the governed agentic core (ADR 0002): on send() it replays the scripted
 * `MOCK_AGENT_EVENTS` governance trajectory as a live agent timeline (folded
 * through `reduceSteps` on a ~250 ms timer), then resolves to a synthetic
 * AnswerView from the fixtures — MOCK_REFUSAL when the question trips the
 * restricted-content pattern (mirroring the engine's fail-closed negative-example
 * / excluded-field gates), MOCK_GRADED_ANSWER / MOCK_AGENT_ANSWER / MOCK_ANSWER
 * otherwise. A separate clarify path stands in for the `ask_user` HITL interrupt.
 *
 * ── REAL PATH (later) ───────────────────────────────────────────────────────
 * When capabilities.can_stream is true, swap this mock for the LangChain
 * `useStream` hook (`@langchain/langgraph-sdk/react`) pointed at
 * LANGGRAPH_URL + ASSISTANT_ID (@/lib/env). The serve graph streams `GovEvent`s
 * that `reduceSteps` folds into the same timeline, and the assistant AnswerView
 * comes from the streamed graph state. The { messages, send, isRunning, steps,
 * reset } shape below is intentionally transport-neutral so the UI layer
 * (MessageList / AgentTimeline / Composer) never changes.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ClarificationRequest, ClarificationResponse } from "@/lib/clarification";
import {
  MOCK_AGENT_ANSWER,
  MOCK_AGENT_EVENTS,
  MOCK_ANSWER,
  MOCK_CLARIFICATION,
  MOCK_CLARIFIED_ANSWER,
  MOCK_GRADED_ANSWER,
  MOCK_REFUSAL,
} from "@/lib/mock/fixtures";
import { reduceSteps, type GovEvent, type TimelineStep } from "@/lib/steps";
import type { AnswerView } from "@/lib/types";

export type ChatRole = "user" | "assistant";

/** One turn in the transcript. Assistants carry a full AnswerView; users, text. */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  text?: string;
  answer?: AnswerView;
  /** The stage-event trace captured live, kept on the finished turn so the
   * timeline persists after the answer lands. There is no second source: the v2
   * record carries `execution` (an attempt ledger with no stage names), not the
   * stage events, so a turn whose live trace was missed has no trace at all —
   * see the note in <ProvenanceDrawer/>. */
  steps?: TimelineStep[];
}

/**
 * The transport-neutral shape every chat hook exposes to the shared conversation
 * UI. Both the mock and the streaming transport satisfy this, so the parent can
 * swap containers without the UI layer ever changing.
 */
export interface ChatTransport {
  messages: ChatMessage[];
  send: (question: string) => void;
  isRunning: boolean;
  /** Agent live timeline (§ agent-step-visualization); optional so a transport with no
   * governance stream can omit it and the renderer falls back to a plain spinner. */
  steps?: TimelineStep[];
  /** Cancel the in-flight turn. Optional: a transport that cannot abort omits it and the
   * composer hides the Stop button. */
  stop?: () => void;
  /** The pending serve-time clarification (HITL): non-null while the agent has
   * interrupted mid-turn to ask a question and is waiting on the answer. Only
   * transports over an interrupt-capable connection set it; others omit it. */
  clarification?: ClarificationRequest | null;
  /** Resume the interrupted turn with the user's answer/decline (contract §4). */
  respondClarification?: (response: ClarificationResponse) => void;
}

export interface UseChatResult extends ChatTransport {
  reset: () => void;
}

/**
 * Questions matching this route to a refusal in the mock transport, standing in
 * for the engine's negative-example / excluded-field fail-closed behavior.
 */
const REFUSAL_PATTERN = /restrict|exclud|pii|card|secret|password/i;

/** Questions matching this route to a graded-delivery fixture (§13). */
const GRADED_PATTERN = /graded|unverified|fenced/i;

/**
 * Every turn now replays the agent timeline; this pattern only picks the richer
 * MOCK_AGENT_ANSWER (whose trace carries a repair loop) over the plain
 * MOCK_ANSWER — it selects the answer fixture, not the serve path.
 */
const AGENT_PATTERN = /agent|reason|corpus|repair|inspect|step/i;

/**
 * Questions matching this replay the serve-time clarification (HITL) flow: the
 * agent interrupts mid-turn to ask one question and waits, then resumes on the
 * answer — a faithful offline stand-in for the server's `ask_user` interrupt
 * (docs/plans/hitl-clarification-contract.md). Refusal still takes priority.
 */
const CLARIFY_PATTERN = /clarif|ambiguous|which .*mean|did you mean|active/i;

/** Refusal takes priority; the agent replay only fires for non-refused questions. */
function isAgentQuestion(question: string): boolean {
  return AGENT_PATTERN.test(question) && !REFUSAL_PATTERN.test(question);
}

/** Clarification outranks the plain agent replay, but never a refusal. */
function isClarifyQuestion(question: string): boolean {
  return CLARIFY_PATTERN.test(question) && !REFUSAL_PATTERN.test(question);
}

function mockAnswerFor(question: string): AnswerView {
  if (REFUSAL_PATTERN.test(question)) return MOCK_REFUSAL;
  if (GRADED_PATTERN.test(question)) return MOCK_GRADED_ANSWER;
  if (isAgentQuestion(question)) return MOCK_AGENT_ANSWER;
  return MOCK_ANSWER;
}

/** Milliseconds between successive timeline events in the mock replay. */
const STEP_INTERVAL_MS = 250;

// Module-level fallback counter so ids stay unique without an external dep.
let idCounter = 0;

function nextId(): string {
  // Prefer the platform UUID; fall back to a counter in older/SSR environments.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  idCounter += 1;
  return `msg-${idCounter}`;
}

export function useChat(): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  // The pending HITL clarification; non-null pauses the turn until answered.
  const [clarification, setClarification] = useState<ClarificationRequest | null>(null);

  // Holds the running stage interval so we can tear it down on reset / unmount.
  const timerRef = useRef<number | null>(null);
  // While a clarification is pending, the turn's accumulated timeline, so
  // `respondClarification` can resume the trace where send() left off.
  const clarifyStepsRef = useRef<TimelineStep[]>([]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Cancel any in-flight pipeline when the consuming component unmounts.
  useEffect(() => clearTimer, [clearTimer]);

  // Append the assistant turn (with its finished trace) and leave the run idle.
  const resolve = useCallback(
    (answer: AnswerView, finalSteps?: TimelineStep[]) => {
      clearTimer();
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "assistant", answer, steps: finalSteps },
      ]);
      setIsRunning(false);
    },
    [clearTimer],
  );

  const send = useCallback(
    (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || isRunning) return;

      // 1) Push the user's turn immediately.
      setMessages((prev) => [...prev, { id: nextId(), role: "user", text: trimmed }]);

      // 2) Enter the running state; reset the timeline.
      setIsRunning(true);
      setSteps([]);
      setClarification(null);
      clearTimer();

      // 3a) Clarification path (HITL): walk a couple of agent steps, then
      // interrupt with a question and PAUSE — the run stays "running" (waiting on
      // the user) until respondClarification resumes it. Mirrors the server
      // raising `interrupt()` inside its `ask_user` tool (contract §2).
      if (isClarifyQuestion(trimmed)) {
        // Stage names and detail keys are the engine's (ADR 0010). `ask_user`'s
        // event carries **only** `clarification_id` — the question and the answer
        // reach the timeline from the interrupt payload, folded in by
        // `respondClarification` below, exactly as on the streaming transport.
        const preamble: GovEvent[] = [
          { seq: 1, id: "accept:mock", kind: "rail", step: "accept", status: "ok", serve_path: "agent", detail: { turn_index: 1 } },
          { seq: 2, id: "assemble:mock", kind: "rail", step: "assemble", status: "ok", detail: { n_chars: 4820 } },
          { seq: 3, id: "agent_core:mock", kind: "rail", step: "agent_core", status: "start" },
          { seq: 4, id: "ask_user:call_1", kind: "tool", step: "ask_user", status: "start",
            detail: { clarification_id: MOCK_CLARIFICATION.clarification_id } },
        ];
        let acc: TimelineStep[] = [];
        let i = 0;
        timerRef.current = window.setInterval(() => {
          if (i < preamble.length) {
            acc = reduceSteps(acc, preamble[i]);
            setSteps(acc);
            i += 1;
            return;
          }
          // Preamble done → raise the interrupt and hold. Keep the trace so the
          // "Asked a question…" row stays visible beside the prompt.
          clearTimer();
          clarifyStepsRef.current = acc;
          setClarification(MOCK_CLARIFICATION);
        }, STEP_INTERVAL_MS);
        return;
      }

      // 3b) Default (the only serve path now, ADR 0002): replay the scripted
      // governance trajectory as a live agent timeline, folding each event
      // through the same reducer the stream uses. The resolved answer still
      // varies by question (refusal / graded / agent / plain) via mockAnswerFor.
      let acc: TimelineStep[] = [];
      let evIndex = 0;
      timerRef.current = window.setInterval(() => {
        if (evIndex < MOCK_AGENT_EVENTS.length) {
          acc = reduceSteps(acc, MOCK_AGENT_EVENTS[evIndex]);
          setSteps(acc);
          evIndex += 1;
          return;
        }
        // Keep the completed trace on the finished turn so it doesn't vanish.
        resolve(mockAnswerFor(trimmed), acc);
      }, STEP_INTERVAL_MS);
    },
    [clearTimer, isRunning, resolve],
  );

  // Resume the paused turn once the user answers (or declines) the clarification.
  // A decline fails closed to a refusal (contract §4 / D3); an answer folds the
  // `ask_user` resolution into the trace and continues to the answer.
  const respondClarification = useCallback(
    (response: ClarificationResponse) => {
      if (!clarification) return;
      const declined = "declined" in response && response.declined === true;
      // Resolve the "Asked a question" row into the recorded interaction: the
      // question, the WHY, and what the user actually answered (a chosen option's
      // label, or their freeform text). reduceSteps deep-merges this onto the
      // start row so the finished trace shows the whole Q&A.
      const answered =
        "choice_id" in response
          ? (clarification.choices?.find((c) => c.id === response.choice_id)?.label ?? response.choice_id)
          : "answer" in response
            ? response.answer
            : undefined;
      const resolution: GovEvent = {
        seq: 5,
        id: "ask_user:call_1",
        kind: "tool",
        step: "ask_user",
        status: declined ? "declined" : "ok",
        detail: {
          clarification_id: clarification.clarification_id,
          question: clarification.question,
          why: clarification.why,
          ...(declined ? { declined: true } : { answer: answered }),
        },
      };
      const trace = reduceSteps(clarifyStepsRef.current, resolution);
      setSteps(trace);
      setClarification(null);
      resolve(declined ? MOCK_REFUSAL : MOCK_CLARIFIED_ANSWER, trace);
    },
    [clarification, resolve],
  );

  // Abort the running turn, leaving the user's question in the transcript with no
  // answer. The synthetic pipeline is a timer, so tearing it down is enough.
  const stop = useCallback(() => {
    clearTimer();
    setIsRunning(false);
    setSteps([]);
    // A turn aborted while suspended at a clarification must take the pending
    // question with it — a prompt whose turn no longer exists cannot be answered.
    setClarification(null);
  }, [clearTimer]);

  const reset = useCallback(() => {
    clearTimer();
    setMessages([]);
    setIsRunning(false);
    setSteps([]);
    setClarification(null);
  }, [clearTimer]);

  return {
    messages,
    send,
    isRunning,
    // Progress state only means anything mid-run; clear it when idle.
    steps: isRunning ? steps : [],
    stop,
    clarification,
    respondClarification,
    reset,
  };
}
