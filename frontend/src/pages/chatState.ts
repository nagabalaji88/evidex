import type { AnswerPayload, AskRequest, ErrorEvent, StageEvent, TraceStep } from "../api/types";

export type MessageStatus = "working" | "complete" | "error" | "lost";

export interface ChatMessage {
  id: string;
  request: AskRequest;
  status: MessageStatus;
  stage: string | null; // current running stage: searching | ranking | writing | checking
  steps: TraceStep[];
  answer?: AnswerPayload;
  error?: ErrorEvent;
  startedAt: number;
}

export interface ChatState {
  messages: ChatMessage[];
  selected: string | null; // message whose steps are shown on the right
}

export type ChatAction =
  | { type: "ask"; id: string; request: AskRequest; now: number }
  | { type: "stage"; id: string; event: StageEvent }
  | { type: "answer"; id: string; answer: AnswerPayload }
  | { type: "error"; id: string; error: ErrorEvent }
  | { type: "lost"; id: string }
  | { type: "retry"; id: string; now: number }
  | { type: "select"; id: string }
  | { type: "steps"; id: string; steps: TraceStep[] }
  | { type: "clear" };

export const initialChat: ChatState = { messages: [], selected: null };

function update(state: ChatState, id: string, fn: (m: ChatMessage) => ChatMessage): ChatState {
  return { ...state, messages: state.messages.map((m) => (m.id === id ? fn(m) : m)) };
}

export function chatReducer(state: ChatState, a: ChatAction): ChatState {
  switch (a.type) {
    case "ask":
      return {
        messages: [...state.messages, { id: a.id, request: a.request, status: "working", stage: "searching", steps: [],
          startedAt: a.now }],
        selected: a.id,
      };
    case "stage":
      return update(state, a.id, (m) => {
        if (m.status !== "working") return m;
        const step = (a.event.data as { step?: TraceStep } | null | undefined)?.step;
        if (a.event.status === "done" && step) return { ...m, steps: [...m.steps, step] };
        return { ...m, stage: a.event.stage };
      });
    case "answer":
      return update(state, a.id, (m) => ({ ...m, status: "complete", stage: null, answer: a.answer }));
    case "error":
      return update(state, a.id, (m) => ({ ...m, status: "error", stage: null, error: a.error }));
    case "lost":
      return update(state, a.id, (m) => (m.status === "working" ? { ...m, status: "lost", stage: null } : m));
    case "retry":
      return {
        ...update(state, a.id, (m) => ({ ...m, status: "working", stage: "searching", steps: [], error: undefined,
          answer: undefined, startedAt: a.now })),
        selected: a.id,
      };
    case "select":
      return { ...state, selected: a.id };
    case "steps":
      return update(state, a.id, (m) => ({ ...m, steps: a.steps }));
    case "clear":
      return initialChat;
  }
}

const TEXT_ORDER = ["Using the conversation", "Understanding the question", "Finding candidate passages",
  "Ranking by relevance", "Is the answer in the documents?", "Writing the answer", "Checking the evidence", "Answer ready"];
const CALC_ORDER = ["Understanding the question", "Picking the table", "Writing the query", "Safety checks",
  "Running the query", "Checking the number", "Answer ready"];

/** Title of the step now running, for the "working" card: the first one not yet finished. */
export function runningTitle(m: ChatMessage): string {
  const done = new Set(m.steps.map((s) => s.title));
  const calc = m.steps.some((s) => s.title === "Understanding the question" && s.detail?.type === "Calculation");
  const order = calc ? CALC_ORDER : TEXT_ORDER.slice(done.has("Using the conversation") ? 0 : 1);
  return order.find((t) => !done.has(t)) ?? "Answer ready";
}

/** 7 steps for a text or calculation question, 8 for a follow-up. */
export function totalSteps(m: ChatMessage): number {
  return m.steps[0]?.title === "Using the conversation" ? 8 : 7;
}
