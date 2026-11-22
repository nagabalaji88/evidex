import type { ErrorEvent, ProgressEvent, StageEvent, StreamEvent, UploadResult } from "../api/types";

export const UPLOAD_STAGES = ["validating", "extracting", "chunking", "embedding", "storing", "ready"] as const;
export type UploadStage = (typeof UPLOAD_STAGES)[number];

export interface StageState {
  status: "pending" | "active" | "done" | "failed" | "blocked" | "skipped";
  detail?: string;
  data?: Record<string, unknown> | null;
}

export interface UploadState {
  status: "idle" | "running" | "complete" | "error";
  filename: string;
  stages: Record<UploadStage, StageState>;
  progress: { done: number; total: number } | null;
  result: UploadResult | null;
  error: ErrorEvent | null;
}

const fresh = (): Record<UploadStage, StageState> =>
  Object.fromEntries(UPLOAD_STAGES.map((s) => [s, { status: "pending" }])) as Record<UploadStage, StageState>;

export const initialUpload: UploadState = { status: "idle", filename: "", stages: fresh(), progress: null, result: null, error: null };

export type UploadAction = { type: "start"; filename: string } | { type: "event"; event: StreamEvent } | { type: "lost" };

function current(stages: Record<UploadStage, StageState>): UploadStage | null {
  return UPLOAD_STAGES.find((s) => stages[s].status === "active") ?? null;
}

function finishRest(stages: Record<UploadStage, StageState>, status: "skipped"): Record<UploadStage, StageState> {
  const out = { ...stages };
  for (const s of UPLOAD_STAGES) if (out[s].status === "pending" || out[s].status === "active") out[s] = { status };
  return out;
}

export function uploadReducer(state: UploadState, a: UploadAction): UploadState {
  if (a.type === "start") return { ...initialUpload, stages: fresh(), status: "running", filename: a.filename };
  if (a.type === "lost") {
    const cur = current(state.stages);
    const stages = { ...state.stages };
    if (cur) stages[cur] = { ...stages[cur], status: "failed", detail: "Connection lost" };
    return { ...state, status: "error", stages: finishRest(stages, "skipped"),
      error: { code: "connection_lost", message: "Connection lost. Retry?" } };
  }
  const e = a.event;
  const stages = { ...state.stages };
  switch (e.event) {
    case "stage": {
      const d = e.data as StageEvent;
      const st = d.stage as UploadStage;
      if (!(st in stages)) return state;
      stages[st] = { status: d.status === "done" ? "done" : "active", detail: d.detail, data: d.data ?? stages[st].data };
      return { ...state, stages };
    }
    case "progress": {
      const d = e.data as ProgressEvent;
      return { ...state, progress: { done: d.done, total: d.total } };
    }
    case "result": {
      const r = e.data as UploadResult;
      if (r.status === "duplicate") {
        stages.validating = { status: "blocked", detail: "Already in the knowledge base, nothing re-processed" };
        return { ...state, status: "complete", result: r, stages: finishRest(stages, "skipped") };
      }
      for (const s of UPLOAD_STAGES) if (stages[s].status !== "done") stages[s] = { ...stages[s], status: "done" };
      stages.ready = { status: "done", detail: r.message };
      return { ...state, status: "complete", result: r, stages };
    }
    case "error": {
      const err = e.data as ErrorEvent;
      const cur = current(stages) ?? "validating";
      stages[cur] = { ...stages[cur], status: "failed", detail: err.code === "scanned_pdf" || err.code === "empty_file"
        || err.code === "unsupported_type" || err.code === "too_large" ? `Rejected: ${err.message}` : err.message };
      return { ...state, status: "error", error: err, stages: finishRest(stages, "skipped") };
    }
    default:
      return state;
  }
}

/** Rebuild the panel from a stored upload's events (Recent uploads → Steps). */
export function replayUpload(filename: string, events: StreamEvent[]): UploadState {
  return events.reduce((s, event) => uploadReducer(s, { type: "event", event }),
    uploadReducer(initialUpload, { type: "start", filename }));
}
