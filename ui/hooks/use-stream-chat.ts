"use client";

/**
 * useStreamChat — the live streaming transport.
 *
 * Backs onto the engine's shipped LangGraph runtime via the LangChain
 * `useStream` hook (`@langchain/langgraph-sdk/react`) pointed at LANGGRAPH_URL +
 * ASSISTANT_ID. Only mounted when `/capabilities` reports `can_stream: true`.
 *
 * The serve graph is the governed agentic core (ADR 0002): it streams a
 * governance ledger live as custom `GovEvent`s (`rail` / `tool` / `final`, the
 * first tagged `serve_path: "agent"`). `reduceSteps` folds that ordered stream
 * into the append-only agent timeline shown in <ServeProgress/>, and the same
 * rows are rebuilt from the completed ledger via `buildStepsFromLedger`
 * (live == audit). The terminal AnswerView prefers `stream.values.answer`
 * (handoff §3); per-message `additional_kwargs.governed_bi` remains a fallback
 * for older stamps.
 *
 * Everything that makes the stream arrive at all is in `SUBMIT_OPTIONS` below —
 * read it before changing a `submit` call.
 *
 * Transcript rows are built by `mapStreamToChatMessages` (`lib/stream-messages.ts`):
 * tool/system frames and intermediate ReAct AI text never become bubbles — the
 * AgentTimeline is the live surface for those (gotcha G2).
 */

import { useEffect, useRef, useState } from "react";
import type { StreamMode } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import { toast } from "sonner";

import type { ChatTransport } from "@/hooks/use-chat";
import { useRestChat } from "@/hooks/use-rest-chat";
import {
  parseClarification,
  type ClarificationRequest,
  type ClarificationResponse,
} from "@/lib/clarification";
import { api } from "@/lib/api-client";
import { ASSISTANT_ID, LANGGRAPH_URL } from "@/lib/env";
import {
  buildStepsFromLedger,
  defaultLabel,
  reduceSteps,
  type GovEvent,
  type StepStatus,
  type TimelineStep,
} from "@/lib/steps";
import {
  alignLogToQuestions,
  flattenContent,
  mapStreamToChatMessages,
  parseAnswer,
  turnFinalAiFrames,
  type LoggedTurn,
} from "@/lib/stream-messages";
import type { AnswerView } from "@/lib/types";

/**
 * The options every run on this transport is submitted with.
 *
 * **`streamSubgraphs: true` is load-bearing, not a tuning knob.** The serve graph runs the
 * model and all five governed tools inside a nested `create_agent` graph invoked from its
 * `agent_core` node, so every token and every tool event is emitted under a subgraph
 * namespace. Measured against `langgraph-api` 0.11.0 on one identical question: omitted →
 * **0** `messages/partial` frames and **0** subgraph updates; `true` → 321 and 12. Without
 * it the UI shows a live-looking run with an empty timeline and no text ever appearing.
 *
 * The spelling matters and the server will not tell you when it is wrong: the wire field is
 * `stream_subgraphs`, the SDK field is `streamSubgraphs` (declared on `SubmitOptions` and
 * forwarded by `useStream`'s `submit` → `client.runs.stream` → `stream_subgraphs`), and a
 * request that sends `subgraphs` instead gets HTTP 200 and is silently ignored.
 *
 * Shared by the initial send **and** the clarification resume rather than written twice: a
 * resume that omitted it would stream nothing for the remainder of the turn, and the half of
 * a HITL turn that happens after the human answers is the half worth watching.
 */
const SUBMIT_OPTIONS: { streamMode: StreamMode[]; streamSubgraphs: boolean } = {
  streamMode: ["values", "messages", "custom"],
  streamSubgraphs: true,
};

/**
 * The slice of graph state we care about. `messages` is loosely typed so the
 * `submit` payload below (a bare human turn) type-checks; the rendered messages
 * come from `stream.messages`, which the SDK types on its own.
 */
interface ChatStreamState {
  messages: Array<{
    id?: string;
    type?: string;
    content?: unknown;
    additional_kwargs?: Record<string, unknown>;
  }>;
  answer: unknown;
}

/**
 * What the client knows about one clarification and the wire does not.
 *
 * The engine's `ask_user` event carries **only** `clarification_id` (ADR 0010's
 * step table; `serve/tools.py` passes `detail=started` on both emissions). The
 * question, the reason and the answer live in the interrupt payload and in the
 * resume, because the stream keeps a closed vocabulary and free text is not part
 * of it. Without joining them back on, the one row the user personally acted on
 * would read "Asked a question" and show nothing.
 */
interface ClarificationFact {
  clarification_id: string;
  question: string;
  why: string;
  /** Absent while the graph is still waiting on the human. */
  resolution?: { declined: boolean; answer?: string };
}

function clarificationFact(
  request: ClarificationRequest,
  resolution?: { declined: boolean; answer?: string },
): ClarificationFact {
  return {
    clarification_id: request.clarification_id,
    question: request.question,
    why: request.why,
    resolution,
  };
}

/**
 * Join the client-held clarification facts onto the event-folded rows, at render
 * time. A projection, deliberately — **nothing synthesised here is ever committed
 * to the list `reduceSteps` folds into.**
 *
 * That is a fix, not a style choice. The earlier version spliced a synthesised row
 * into the live trace when the user answered before any `ask_user` event had
 * arrived — reachable, because a reload while a clarification is pending leaves the
 * SDK deriving `stream.interrupt` from the thread's tasks with no events replayed.
 * The synthesised row was keyed on `clarification_id` and the engine keys its rows
 * on `tool_call_id` (ADR 0010: "`id` is keyed on `turn_id` for rails and on
 * `tool_call_id` for tools"), two identifiers the interrupt payload gives no way to
 * relate. So `reduceSteps` could not merge them and the resume's events appended a
 * second row: the user saw their clarification twice. Keeping the synthesis out of
 * the folded list makes that unrepresentable rather than guarded against.
 *
 * Merge is by `clarification_id`, so a turn that asks twice cannot attribute one
 * answer to the other question, and a *resolved* fact never appends — it can only
 * land on a row that exists. A resolved fact whose row never streamed therefore
 * shows nothing, which costs nothing: if the events did not arrive, there is no
 * timeline for it to be missing from.
 */
function withClarifications(
  steps: TimelineStep[],
  facts: readonly ClarificationFact[],
): TimelineStep[] {
  if (facts.length === 0) return steps;
  let out = steps;
  for (const fact of facts) {
    const at = out.findIndex(
      (s) => s.step === "ask_user" && s.detail?.clarification_id === fact.clarification_id,
    );
    if (at >= 0) {
      out = out.map((s, i) => (i === at ? mergedClarificationRow(s, fact) : s));
      continue;
    }
    // Only a *pending* question may invent a row, and only for the live turn: it
    // is the row the user is being asked to act on, and a prompt on screen with
    // nothing beside it in the trace reads as "nothing happened".
    if (fact.resolution !== undefined) continue;
    out = [...out, synthesisedClarificationRow(out, fact)];
  }
  return out;
}

function clarificationDetail(row: TimelineStep | null, fact: ClarificationFact) {
  const resolved = fact.resolution;
  return {
    ...(row?.detail ?? {}),
    clarification_id: fact.clarification_id,
    question: fact.question,
    why: fact.why,
    ...(resolved ? (resolved.declined ? { declined: true } : { answer: resolved.answer }) : {}),
  };
}

function mergedClarificationRow(row: TimelineStep, fact: ClarificationFact): TimelineStep {
  const detail = clarificationDetail(row, fact);
  // The engine's settled status wins; ours only fills the gap between the click
  // and the resume's resolve event — including the moment the replayed `ask_user`
  // re-emits `start` and the engine's own row flips back to `running`.
  const status: StepStatus =
    row.status === "running" && fact.resolution
      ? fact.resolution.declined
        ? "declined"
        : "ok"
      : row.status;
  return { ...row, status, label: defaultLabel({ step: row.step, status, detail }), detail };
}

function synthesisedClarificationRow(
  steps: TimelineStep[],
  fact: ClarificationFact,
): TimelineStep {
  const detail = clarificationDetail(null, fact);
  // `seq` is arrival order, not the wire's counter — the same `max + 1` rule
  // `reduceSteps` applies — so the question lands after everything already held.
  // The key is prefixed `clarification:` so it cannot collide with an engine id
  // (always `ask_user:<tool_call_id>`) even by accident.
  return {
    key: `clarification:${fact.clarification_id}`,
    seq: steps.reduce((max, s) => Math.max(max, s.seq), 0) + 1,
    kind: "tool",
    step: "ask_user",
    status: "running",
    label: defaultLabel({ step: "ask_user", status: "running", detail }),
    detail,
  };
}

/**
 * @param threadId  The conversation to open, or `null` for a new one. Passing it is what makes
 *   the transcript survive a reload: `useStream` fetches that thread's history and current
 *   values on mount. The engine has always persisted them — `langgraph dev` checkpoints to
 *   `.langgraph_api/*.pckl`, 16 threads were sitting on disk when this was wired up — so the
 *   conversation was never lost on the server, only unreachable from here.
 * @param onThreadId  Called once, when the first submit mints a thread. The caller records it
 *   (in the URL) so the next load can pass it back.
 */
export function useStreamChat(
  threadId: string | null,
  onThreadId: (threadId: string) => void,
): ChatTransport {
  // Agent live timeline, folded from the custom `GovEvent` stream.
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  // Completed traces, kept by assistant-message id so each finished turn keeps
  // its timeline after the run ends (the live `steps` state resets next send).
  // State, not a ref, so the mapping below can read it during render.
  const [completedSteps, setCompletedSteps] = useState<Map<string, TimelineStep[]>>(new Map());
  // **Finished turns' answers, by the same key, for the same reason.**
  //
  // `values.answer` describes the newest turn only (`PER_TURN_RESET`), so the moment turn two
  // starts, turn one's record is gone from state and its card had to be rebuilt from the audit log
  // by *position*, on every render. That cost the card twice: it degraded to bare text for as long
  // as the fetch took, and a position is not a fact about a turn, so it could name the wrong one.
  //
  // Keyed on the message that carries the answer instead, and filled from both sources — the
  // record seen when a turn finished here (`onFinish`), and the log joined onto ids once, below.
  // A finished turn's card is then a lookup that cannot go stale or shift.
  const [completedAnswers, setCompletedAnswers] = useState<Map<string, AnswerView>>(new Map());
  // Latest live trace, mirrored in a ref so the `onFinish` event handler can read
  // it (event handlers may touch refs; render may not) to snapshot the finished
  // turn's timeline — no ref reads during render, no setState inside an effect.
  // It holds **only** what the engine emitted: nothing derived and nothing
  // synthesised, so a row here always has the engine's `id` as its key.
  const stepsRef = useRef<TimelineStep[]>([]);
  // What the user was asked and what they answered, by `clarification_id`. State
  // rather than a ref because it is read during render (the projection), and the
  // rule in this file is that render may not touch refs.
  const [clarified, setClarified] = useState<ReadonlyMap<string, ClarificationFact>>(new Map());

  // Graceful degradation: if the streaming run errors (e.g. the LangGraph server
  // can't execute the graph), fall back to the non-streaming POST /chat transport
  // and replay the pending question there, so the user still gets an answer.
  //
  // It is a *degradation*, and a sharper one than it looks: the two transports do
  // not share a thread. The engine's REST route and its streaming route are built
  // on different checkpointers, and `useRestChat` starts with an empty transcript,
  // so the replayed question is answered with no memory of the conversation that
  // preceded it. The client cannot bridge that — it can only say so, which the
  // toast in `onError` does.
  //
  // `degradedRef`, not the `degraded` state, is the re-entrancy guard: state lands
  // a render later and `onError` can fire again before it does.
  const rest = useRestChat();
  const [degraded, setDegraded] = useState(false);
  const degradedRef = useRef(false);
  const pendingRef = useRef("");

  const stream = useStream<ChatStreamState>({
    apiUrl: LANGGRAPH_URL,
    // No `apiKey`: the engine reads no credential off the wire, so there is none to send.
    assistantId: ASSISTANT_ID,
    messagesKey: "messages",
    threadId,
    onThreadId,
    // Reload *during* a run and rejoin it, rather than watching a finished-looking page while
    // the graph is still working. Worth having here specifically because a turn is 30–120
    // seconds: the window in which a reload lands mid-run is most of the turn, not an edge case.
    reconnectOnMount: true,
    onCustomEvent: (data) => {
      // The serve graph streams the governance ledger as `GovEvent`s — each has
      // `kind` + a numeric `seq`; `reduceSteps` folds them into the timeline.
      const ev = data as Partial<GovEvent> | null | undefined;
      if (typeof ev?.kind === "string" && typeof ev.seq === "number") {
        const next = reduceSteps(stepsRef.current, ev as GovEvent);
        stepsRef.current = next;
        setSteps(next);
      }
    },
    onFinish: (state) => {
      // Run finished: snapshot the trace *and* the answer under the completed assistant
      // message's id, so this turn keeps both once the next turn clears the record channels
      // and resets the live timeline.
      const captured = stepsRef.current;
      const values = state?.values as ChatStreamState | undefined;
      const answer = parseAnswer(values?.answer);
      if (captured.length === 0 && answer == null) return;
      const msgs = values?.messages ?? [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i]?.type !== "human") {
          const id = msgs[i]?.id ?? `stream-${i}`;
          if (captured.length > 0) setCompletedSteps((prev) => new Map(prev).set(id, captured));
          if (answer != null) setCompletedAnswers((prev) => new Map(prev).set(id, answer));
          break;
        }
      }
    },
    onError: () => {
      // Fires at most once per transport: `degradedRef` (not the `degraded` state,
      // which lands a render later) is what stops a second stream error — or a
      // failed clarification resume — from replaying the question a second time.
      if (degradedRef.current) return;
      degradedRef.current = true;
      setDegraded(true);
      // Honest about what is lost, not just about what changed. The engine's REST
      // route and its streaming route use **different checkpointers**, so the
      // thread the stream was building is not readable from `POST /chat`: the
      // replayed question is answered on its own, with the earlier turns of this
      // conversation gone. Nothing on the client can bridge that.
      toast.error(
        "Live streaming failed — answering without live progress, and without the earlier turns of this conversation.",
      );
      // `pendingRef` is the question that was in flight. Cleared as it is handed
      // over so a later error can never replay the same turn twice.
      const pending = pendingRef.current;
      pendingRef.current = "";
      if (pending) rest.send(pending);
    },
  });

  const isRunning = stream.isLoading;

  // Channel answer (handoff §3) — used for the latest assistant turn when present.
  const channelAnswer = parseAnswer(stream.values?.answer);

  // HITL: when the graph pauses at `interrupt()` inside its `ask_user` tool, the
  // interrupt value IS the ClarificationRequest. Parse fail-loud (it arrives as
  // `unknown`); a malformed interrupt is dropped rather than half-rendered. The
  // engine's payload is `{kind, clarification_id, question, why}` with no
  // `choices`, so <ClarificationPrompt/> renders freeform-only — which is why the
  // textarea path, not the option buttons, is the one that has to work.
  const clarification = parseClarification(stream.interrupt?.value);
  // A pending clarification means the turn is *suspended*, not finished:
  // `stream.isLoading` is false while the graph waits at `interrupt()`, but the
  // progress timeline (and the "Asked a question…" row) must stay on screen
  // beside the prompt, so treat this as still-in-progress for display.
  const awaitingClarification = clarification != null;

  // Everything the user has answered on this thread, keyed by `clarification_id`.
  // A side table joined onto the rows at render time, never folded into them —
  // see `withClarifications` for why that distinction is the bug fix.
  const resolvedFacts = [...clarified.values()];
  // Plus the question currently on screen, which is the only fact allowed to
  // invent a row.
  const liveFacts =
    clarification && !clarified.has(clarification.clarification_id)
      ? [...resolvedFacts, clarificationFact(clarification)]
      : resolvedFacts;

  // Answer/decline the pending clarification by resuming the run. On the shipped
  // SDK this is `submit(null, { command: { resume } })` — equivalent to the
  // contract's `stream.respond(response)` (contract §2). The engine accepts either
  // a bare string or this structured reply, and reads `declined` before
  // `answer`/`choice_id` (`serve/tools.py::_clarification_answer`).
  const respondClarification = (response: ClarificationResponse) => {
    // Record what was asked and what the user said. The engine's resolve event
    // carries neither, so this is the only place either exists on the client.
    if (clarification) {
      const declined = "declined" in response && response.declined === true;
      const answered =
        "choice_id" in response
          ? (clarification.choices?.find((c) => c.id === response.choice_id)?.label ?? response.choice_id)
          : "answer" in response
            ? response.answer
            : undefined;
      const fact = clarificationFact(clarification, { declined, answer: answered });
      setClarified((prev) => new Map(prev).set(fact.clarification_id, fact));
    }
    // `SUBMIT_OPTIONS` spread first: the resume needs the same stream modes and
    // the same `streamSubgraphs` as the send, or the rest of the turn — the tool
    // calls the answer unblocks — runs blind.
    void stream.submit(null, { ...SUBMIT_OPTIONS, command: { resume: response } });
  };

  // The persistent trace for a finished assistant turn: the captured live trace
  // if we have it, else rebuilt from the answer's ledger (live == audit).
  const stepsFor = (id: string, answer: AnswerView): TimelineStep[] | undefined => {
    const captured = completedSteps.get(id);
    const rows =
      captured && captured.length > 0 ? captured : buildStepsFromLedger(answer.record?.execution);
    if (rows.length === 0) return undefined;
    // `resolvedFacts`, not `liveFacts`: a finished turn's card must not grow a
    // *pending* row, and since every resolved fact can only merge onto a matching
    // `clarification_id`, another turn's answer can never appear on this one.
    return withClarifications(rows, resolvedFacts);
  };

  // **Earlier turns' records, from the durable turn log — the reload path.**
  //
  // `stream.values.answer` describes the newest turn only: the engine clears the record channels
  // every turn (`PER_TURN_RESET`), so a two-turn thread has one record in state. `onFinish` above
  // keeps the turns that finished while this component was mounted; the log is what covers the
  // ones that did not, which after a reload is all of them.
  //
  // **The rows are attached to message ids here, in the fetch's own callback, rather than read
  // positionally at render time.** Two reasons, and the second is the load-bearing one:
  //
  //  1. The rows carry neither a message id nor a `turn_index`, so they can only be joined on the
  //     question — and that is a text match, not an identity. It has to be done somewhere it
  //     cannot go wrong. Here, the frames present are exactly the turns that have already
  //     answered: the run this fetch was triggered by has not produced its frame yet, so no row
  //     can be attached to a question still being worked on. That matters because the log really
  //     does hold rows this conversation never showed — thread `019fdc77` had four under one
  //     `thread_id` for a two-turn transcript, two of them from an earlier run, one repeating a
  //     question asked again later.
  //  2. Once resolved to an id, a finished turn's record no longer depends on run state at all.
  //     That is what closes the last blank window: `submit()` flips `isLoading` before appending
  //     the human frame, and during that gap the previous turn's answer is the newest frame with
  //     nothing behind it — recognisable as settled only by identity. Measured: ~700ms.
  //
  // Refetched when the turn count changes rather than on every render. Never cleared, and it does
  // not need to be: message ids are unique, so a row resolved on another thread cannot match a
  // frame on this one. Failures are swallowed on purpose — a missing log costs an earlier turn its
  // SQL panel, and must not cost the live turn its answer.
  //
  // Fetched for a *one*-turn thread too, though it has no earlier turns to rebuild. Reason (2)
  // above is why: reload a one-turn conversation and ask a follow-up, and that turn's card has to
  // survive the submit window on a record keyed to its message — which it only has if the log was
  // already read. Waiting until there were two turns raced the request against the run.
  const turnCount = stream.messages.filter((m) => m.type === "human").length;
  useEffect(() => {
    if (!threadId || turnCount === 0) return;
    // Captured before the request so the join reads the frames as they were when this turn count
    // was reached. They are the settled turns' frames, which do not change while the fetch is in
    // flight; adding `stream.messages` to the deps would refetch the log on every streamed token.
    const questions = stream.messages.map((m) =>
      m.type === "human" ? flattenContent(m.content) : null,
    );
    const frames = turnFinalAiFrames(stream.messages);
    let live = true;
    void api
      .auditTurns(200, threadId)
      .then(({ turns }) => {
        if (!live) return;
        const logged: LoggedTurn[] = [...turns].reverse().map((turn) => ({
          question: typeof turn.question === "string" ? turn.question : "",
          answer: {
            outcome: (turn.outcome ?? "answered") as AnswerView["outcome"],
            text: null,
            answer_text: turn.answer_text ?? null,
            record: turn as unknown as Record<string, unknown>,
          },
        }));
        const byTurn = alignLogToQuestions(questions, logged);
        // Merged, never replaced: a record captured live is first-hand, so it outranks the log for
        // the same message.
        setCompletedAnswers((prev) => {
          let next: Map<string, AnswerView> | null = null;
          for (const frame of frames) {
            const answer = byTurn[frame.turn];
            if (!answer || prev.has(frame.id)) continue;
            next ??= new Map(prev);
            next.set(frame.id, answer);
          }
          return next ?? prev;
        });
      })
      .catch(() => {
        // Nothing to undo: the map only ever gained entries.
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see the capture note above.
  }, [threadId, turnCount]);

  /** The record for the turn this message answered, if we have one. */
  const answerFor = (id: string): AnswerView | undefined => completedAnswers.get(id);

  // Drop tool/system frames and intermediate ReAct AI text — see mapStreamToChatMessages.
  const messages = mapStreamToChatMessages({
    messages: stream.messages,
    channelAnswer,
    answerFor,
    isRunning,
    awaitingClarification,
    stepsFor,
  });

  const send = (question: string) => {
    if (degradedRef.current) {
      rest.send(question);
      return;
    }
    const trimmed = question.trim();
    // A suspended turn is not a finished one: `isLoading` is false at an
    // interrupt, so without `awaitingClarification` the composer could start a
    // second run on a thread that is waiting for an answer to its first.
    if (!trimmed || isRunning || awaitingClarification) return;
    pendingRef.current = trimmed;
    // Reset the agent timeline for the new turn; events refill it as they arrive.
    stepsRef.current = [];
    setSteps([]);
    void stream.submit({ messages: [{ type: "human", content: trimmed }] }, SUBMIT_OPTIONS);
  };

  // Once degraded, the REST transport owns the whole transcript + sends.
  if (degraded) return rest;

  return {
    messages,
    send,
    isRunning,
    // Progress state only means anything mid-run; clear it when idle. (The
    // completed trace re-renders on the answer card, so nothing is lost.) While
    // the graph waits at `interrupt()` the timeline carries the synthesised
    // `ask_user` row, so the question the user is answering is also *on* the
    // trace, not only in the prompt below it.
    steps: isRunning || awaitingClarification ? withClarifications(steps, liveFacts) : [],
    stop: () => stream.stop(),
    clarification,
    respondClarification,
  };
}
