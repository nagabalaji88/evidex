import { describe, expect, it } from "vitest";
import type { AnswerPayload, TraceStep } from "../api/types";
import { railItems, railScore } from "../components/LiveRail";
import type { ChatMessage } from "../pages/chatState";

const step = (title: string, status: TraceStep["status"] = "done", detail: Record<string, unknown> = {}): TraceStep =>
  ({ title, tech: "", status, summary: `${title} summary`, detail, ms: 1 });

const steps: TraceStep[] = [
  step("Understanding the question"),
  step("Finding candidate passages"),
  step("Ranking by relevance"),
  step("Is the answer in the documents?", "done", { best_score: 0.81, threshold: 0.3 }),
  step("Writing the answer"),
  step("Checking the evidence", "done", { checks: [{ passed: true }, { passed: true }, { passed: true }] }),
  step("Answer ready", "done", { confidence: { level: "High", score: 0.77 } }),
];

const msg = (over: Partial<ChatMessage>): ChatMessage =>
  ({ id: "m1", request: { question: "q" }, status: "working", stage: null, steps: [], startedAt: 0, ...over }) as ChatMessage;

describe("live rail replay", () => {
  it("adds a glowing running step while the answer is still being worked out", () => {
    const { items, cur } = railItems(msg({ steps: steps.slice(0, 2) }), 2);
    expect(items.map((i) => i.status)).toEqual(["done", "done", "running"]);
    expect(items[2].title).toBe("Ranking by relevance");
    expect(cur).toBe(2);
  });

  it("replays finished steps one at a time, newest glowing, without waiting on them", () => {
    const m = msg({ status: "complete", steps });
    const { items, cur } = railItems(m, 3);
    expect(items).toHaveLength(3);
    expect(cur).toBe(2);
    expect(railItems(m, steps.length).cur).toBe(-1);
  });

  it("skips skipped steps", () => {
    const m = msg({ status: "complete", steps: [step("Understanding the question"), step("Writing the answer", "skipped")] });
    expect(railItems(m, 5).items.map((i) => i.title)).toEqual(["Understanding the question"]);
  });
});

describe("live rail score", () => {
  const answer = {
    kind: "answer", confidence: { level: "High", score: 0.77 }, evidence: [{}],
    checks: [{ passed: true }, { passed: true }, { passed: true }],
  } as unknown as AnswerPayload;

  it("shows nothing before the replay reaches a step that produced a number", () => {
    const s = railScore(msg({ status: "complete", steps, answer }), 3);
    expect(s).toMatchObject({ text: "—", label: "Waiting", checks: "—", sources: "—" });
  });

  it("shows the real relevance score once the gate step is replayed", () => {
    const s = railScore(msg({ status: "complete", steps, answer }), 4);
    expect(s).toMatchObject({ ring: 0.81, label: "Relevance", tone: "prov" });
  });

  it("fills in checks, then the final confidence and sources", () => {
    expect(railScore(msg({ status: "complete", steps, answer }), 6).checks).toBe("3/3");
    const s = railScore(msg({ status: "complete", steps, answer }), 7);
    expect(s).toMatchObject({ ring: 0.77, text: "77", label: "High confidence", tone: "good", sources: "1" });
  });

  it("marks a calculated answer as exact", () => {
    const calc = { kind: "calculation", sql: "SELECT 1", checks: [] } as unknown as AnswerPayload;
    const s = railScore(msg({ status: "complete", steps: [step("Answer ready")], answer: calc }), 1);
    expect(s).toMatchObject({ ring: 1, label: "Exact", tone: "good" });
  });
});
