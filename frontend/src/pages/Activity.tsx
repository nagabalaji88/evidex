import { useEffect, useRef, useState } from "react";
import { timeTxt } from "../app/format";
import { getJSON } from "../api/client";
import type { ActivityList, ActivityRow, StreamEvent, TraceResponse, UploadSteps } from "../api/types";
import { AnswerDetails } from "../components/AnswerCard";
import { Trace, questionSteps, type UiStep } from "../components/Trace";
import { replayUpload } from "./uploadState";

const FILTERS = [["all", "All"], ["answered", "Answered"], ["refused", "Not in documents"], ["error", "Errors"],
  ["upload", "Uploads"]] as const;
const LABEL: Record<string, string> = {
  answered: "Answered", refused: "Not in documents", error: "Error", indexed: "Indexed", duplicate: "Duplicate, skipped",
  rejected: "Rejected", failed: "Failed",
};
const PILL: Record<string, string> = {
  answered: "answered", refused: "refused", error: "error", indexed: "answered", duplicate: "skipped", rejected: "rejected",
  failed: "error",
};

export default function Activity() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");
  const [rows, setRows] = useState<ActivityRow[] | null>(null);
  const [open, setOpen] = useState<{ title: string; steps: UiStep[]; kind: "question" | "upload"; trace?: TraceResponse } | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setRows(null);
    getJSON<ActivityList>(`/api/activity?result=${filter}`).then((a) => setRows(a.rows)).catch(() => setRows([]));
  }, [filter]);

  async function show(r: ActivityRow) {
    try {
      if (r.kind === "question" && r.trace_id) {
        const t = await getJSON<TraceResponse>(`/api/traces/${r.trace_id}`);
        setOpen({ title: r.detail, steps: questionSteps(t.steps), kind: "question", trace: t });
      } else if (r.kind === "upload") {
        const u = await getJSON<UploadSteps>(`/api/uploads/${r.id}`);
        const s = replayUpload(u.filename, u.events as StreamEvent[]);
        const names: Record<string, string> = { validating: "Checking the file", extracting: "Reading the text",
          chunking: "Splitting into chunks", embedding: "Making it searchable", storing: "Switching to the new version",
          ready: "Ready for questions" };
        setOpen({ title: u.filename, kind: "upload", steps: Object.entries(s.stages).map(([k, v]) => ({
          key: k, title: names[k], tech: "", status: v.status, summary: v.detail })) });
      }
      setTimeout(() => panel.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch {
      setOpen(null);
    }
  }

  return (
    <>
      <div className="ph"><div><h1>Activity log</h1></div></div>
      <section className="card">
        <div className="pills" role="group" aria-label="Filter">
          {FILTERS.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
          ))}
        </div>
        <div className="tw">
          <table>
            <thead><tr><th>When</th><th>Type</th><th>Detail</th><th>Result</th><th className="num">Confidence</th>
              <th>Source</th><th className="num">Time</th></tr></thead>
            <tbody>
              {(rows ?? []).map((r) => (
                <tr key={`${r.kind}${r.id}`} className="clickable" tabIndex={0} onClick={() => void show(r)}
                  onKeyDown={(e) => { if (e.key === "Enter") void show(r); }}>
                  <td style={{ whiteSpace: "nowrap" }}>{timeTxt(r.when)}</td>
                  <td>{r.kind === "question" ? "Question" : "Upload"}</td>
                  <td>{r.detail}</td>
                  <td><span className={`pill ${PILL[r.result]}`}>{LABEL[r.result]}</span></td>
                  <td className="num">{r.confidence}</td>
                  <td>{r.source}</td>
                  <td className="num">{r.ms !== null && r.ms !== undefined ? `${Math.round(r.ms / 1000)} s` : "—"}</td>
                </tr>
              ))}
              {rows && !rows.length ? <tr><td colSpan={7} className="muted">Nothing here yet.</td></tr> : null}
              {!rows ? <tr><td colSpan={7} className="muted">Loading…</td></tr> : null}
            </tbody>
          </table>
        </div>
        <p className="note" style={{ marginTop: 10 }}>
          Every row keeps the retrieved chunks, scores and checks, so any answer can be traced later. Click a row to see its steps.
        </p>
      </section>
      {open ? (
        <div ref={panel} className="g2" style={{ marginTop: 16, alignItems: "start" }}>
          <section className="card">
            <div className="card-h"><div><h2>Steps</h2><p className="cs">“{open.title}”</p></div>
              <button type="button" className="btn" onClick={() => setOpen(null)}>Close</button></div>
            <Trace key={open.title} steps={open.steps} kind={open.kind} done empty="" />
          </section>
          {open.trace?.answer ? (
            <section className="card">
              <h2 style={{ marginBottom: 10 }}>Answer</h2>
              <p className="ans-text" style={{ marginBottom: 12 }}>{open.trace.answer.answer}</p>
              <AnswerDetails a={open.trace.answer} />
            </section>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
