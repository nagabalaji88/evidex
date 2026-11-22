import { describe, expect, it } from "vitest";
import { modelLabel } from "../app/format";
import { parseFrames } from "../api/stream";
import type { StreamEvent } from "../api/types";
import { chatReducer, initialChat, runningTitle, totalSteps } from "../pages/chatState";
import { validate, yamlSet } from "../pages/settingsModel";
import { initialUpload, replayUpload, uploadReducer } from "../pages/uploadState";

describe("SSE framing", () => {
  it("parses complete frames and keeps the partial rest", () => {
    const [ev, rest] = parseFrames('event: stage\ndata: {"stage":"searching","detail":"x","status":"running"}\n\nevent: done\ndata: {"elapsed_ms":5}\n\nevent: pro');
    expect(ev.map((e) => e.event)).toEqual(["stage", "done"]);
    expect(rest).toBe("event: pro");
  });
  it("handles CRLF and ignores broken JSON", () => {
    const [ev] = parseFrames('event: error\r\ndata: {"code":"x","message":"y"}\r\n\r\nevent: x\ndata: {nope\n\n');
    expect(ev).toEqual([{ event: "error", data: { code: "x", message: "y" } }]);
  });
});

const stage = (stage: string, status: "running" | "done", extra = {}) =>
  ({ event: "stage", data: { stage, detail: `${stage} ${status}`, status, ...extra } }) as StreamEvent;

describe("upload state", () => {
  it("goes idle -> stages -> complete with progress", () => {
    const events: StreamEvent[] = [
      stage("validating", "running"), stage("validating", "done"), stage("extracting", "running"), stage("extracting", "done"),
      stage("chunking", "running"), stage("chunking", "done"), stage("embedding", "running"),
      { event: "progress", data: { stage: "embedding", done: 18, total: 31 } } as StreamEvent,
    ];
    let s = replayUpload("a.pdf", events);
    expect(s.status).toBe("running");
    expect(s.stages.embedding.status).toBe("active");
    expect(s.progress).toEqual({ done: 18, total: 31 });
    s = uploadReducer(s, { type: "event", event: { event: "result", data: { status: "indexed", message: "A v1 indexed: 4 pages, 9 chunks.", document_id: 1, version_id: 1, version: 1, doc_name: "a.pdf", elapsed_ms: 1 } } as StreamEvent });
    expect(s.status).toBe("complete");
    expect(Object.values(s.stages).every((x) => x.status === "done")).toBe(true);
    expect(s.stages.ready.detail).toBe("A v1 indexed: 4 pages, 9 chunks.");
  });
  it("marks a duplicate as blocked and skips the rest", () => {
    const s = replayUpload("a.pdf", [stage("validating", "running"),
      { event: "result", data: { status: "duplicate", message: "Duplicate, skipped: identical to a.pdf v1.", document_id: 1, version_id: 1, version: 1, doc_name: "a.pdf", elapsed_ms: 1 } } as StreamEvent]);
    expect(s.stages.validating.status).toBe("blocked");
    expect(s.stages.extracting.status).toBe("skipped");
  });
  it("marks a scanned PDF as rejected at the current step", () => {
    const s = replayUpload("scan.pdf", [stage("validating", "running"), stage("validating", "done"), stage("extracting", "running"),
      { event: "error", data: { code: "scanned_pdf", message: "This PDF appears to be scanned and has no text layer." } } as StreamEvent]);
    expect(s.status).toBe("error");
    expect(s.stages.extracting.status).toBe("failed");
    expect(s.stages.extracting.detail).toMatch(/^Rejected: /);
    expect(s.stages.chunking.status).toBe("skipped");
  });
  it("a dropped stream is a connection-lost error", () => {
    const s = uploadReducer(uploadReducer(initialUpload, { type: "start", filename: "a.pdf" }), { type: "lost" });
    expect(s.error?.message).toBe("Connection lost. Retry?");
  });
});

describe("chat state", () => {
  const req = { question: "q", session_id: "s", document_ids: null, model: null };
  it("collects finished steps and ends with the answer", () => {
    let s = chatReducer(initialChat, { type: "ask", id: "1", request: req, now: 0 });
    expect(s.messages[0].status).toBe("working");
    expect(runningTitle(s.messages[0])).toBe("Understanding the question");
    s = chatReducer(s, { type: "stage", id: "1", event: { stage: "searching", detail: "", status: "done",
      data: { step: { title: "Understanding the question", tech: "", status: "done", summary: "", detail: { type: "Lookup or explanation" }, ms: 1 } } } });
    expect(s.messages[0].steps).toHaveLength(1);
    expect(runningTitle(s.messages[0])).toBe("Finding candidate passages");
    expect(totalSteps(s.messages[0])).toBe(7);
    s = chatReducer(s, { type: "lost", id: "1" });
    expect(s.messages[0].status).toBe("lost");
    s = chatReducer(s, { type: "retry", id: "1", now: 1 });
    expect(s.messages[0].status).toBe("working");
    expect(s.messages[0].steps).toHaveLength(0);
  });
  it("a calculation follows the table steps", () => {
    let s = chatReducer(initialChat, { type: "ask", id: "1", request: req, now: 0 });
    s = chatReducer(s, { type: "stage", id: "1", event: { stage: "searching", detail: "", status: "done",
      data: { step: { title: "Understanding the question", tech: "", status: "done", summary: "", detail: { type: "Calculation" }, ms: 1 } } } });
    expect(runningTitle(s.messages[0])).toBe("Picking the table");
  });
});

describe("settings", () => {
  const yaml = "retrieval:\n  candidate_k: 10\n  relevance_threshold: 0.30   # placeholder, set by evaluation\n\nreranker:\n  enabled: true            # evaluation compares on/off\n";
  it("patches config.yaml and keeps comments", () => {
    const out = yamlSet(yamlSet(yaml, "retrieval.relevance_threshold", 0.35), "reranker.enabled", false);
    expect(out).toContain("relevance_threshold: 0.35   # placeholder, set by evaluation");
    expect(out).toContain("enabled: false            # evaluation compares on/off");
  });
  it("refuses invalid combinations", () => {
    const ok = { "ollama.num_predict": 400, "retrieval.candidate_k": 10, "retrieval.history_turns": 5, "ingestion.chunk_size_tokens": 500,
      "ingestion.chunk_overlap_tokens": 50, "ingestion.max_file_mb": 25, "retrieval.final_k": 3, "retrieval.relevance_threshold": 0.3,
      "confidence.medium_from": 0.45, "confidence.high_from": 0.7 };
    expect(validate(ok)).toEqual([]);
    expect(validate({ ...ok, "ingestion.chunk_overlap_tokens": 600 }).join()).toMatch(/overlap/);
    expect(validate({ ...ok, "confidence.medium_from": 0.9 }).join()).toMatch(/Thresholds/);
    expect(validate({ ...ok, "retrieval.candidate_k": 2 }).join()).toMatch(/Passages sent/);
  });
});

describe("format", () => {
  it("labels models", () => {
    expect(modelLabel("qwen2.5:7b-instruct")).toBe("Qwen 2.5 7B");
    expect(modelLabel("llama3.1:8b")).toBe("Llama 3.1 8B");
    expect(modelLabel("nomic-embed-text")).toBe("Nomic Embed Text");
  });
});
