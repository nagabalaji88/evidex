// Aliases over the generated OpenAPI types (src/types/api.ts, from `npm run gen:types`).
// Never hand-write API types: re-run gen:types after a backend model changes.
import type { components } from "../types/api";

type S = components["schemas"];
export type AnswerPayload = S["AnswerPayload"];
export type TraceStep = S["TraceStep"];
export type TraceResponse = S["TraceResponse"];
export type StageEvent = S["StageEvent"];
export type ProgressEvent = S["ProgressEvent"];
export type ErrorEvent = S["ErrorEvent"];
export type DoneEvent = S["DoneEvent"];
export type UploadResult = S["UploadResult"];
export type HealthResponse = S["HealthResponse"];
export type DocumentSummary = S["DocumentSummary"];
export type DocumentList = S["DocumentList"];
export type DocumentDetail = S["DocumentDetail"];
export type VersionInfo = S["VersionInfo"];
export type UploadRow = S["UploadRow"];
export type UploadList = S["UploadList"];
export type UploadSteps = S["UploadSteps"];
export type Stats = S["Stats"];
export type ActivityRow = S["ActivityRow"];
export type ActivityList = S["ActivityList"];
export type SettingsResponse = S["SettingsResponse"];
export type SettingsSaved = S["SettingsSaved"];
export type ModelInfo = S["ModelInfo"];
export type ModelList = S["ModelList"];
export type EvalReport = S["EvalReport"];
export type EvalRun = S["EvalRun"];
export type EvalCase = S["EvalCase"];
export type ActionResult = S["ActionResult"];
export type AskRequest = S["AskRequest"];

/** Every stream event (spec section 8.1), keyed by event name. */
export type StreamEvents = {
  stage: StageEvent;
  progress: ProgressEvent;
  answer: AnswerPayload;
  result: UploadResult;
  error: ErrorEvent;
  done: DoneEvent;
};
export type StreamEvent = { [K in keyof StreamEvents]: { event: K; data: StreamEvents[K] } }[keyof StreamEvents];
