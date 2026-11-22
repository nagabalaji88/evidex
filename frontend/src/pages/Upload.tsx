import { useEffect, useReducer, useRef, useState } from "react";
import { useApp } from "../app/context";
import { age, dateTxt, fileType, load, save, size } from "../app/format";
import { IC } from "../app/icons";
import { getJSON } from "../api/client";
import { ConnectionLost, postStream } from "../api/stream";
import type { StreamEvent, UploadList, UploadRow, UploadSteps } from "../api/types";
import { FileType } from "../components/AnswerCard";
import { Checks, Trace, type UiStep } from "../components/Trace";
import { initialUpload, replayUpload, uploadReducer, type UploadState } from "./uploadState";

const STATUS_LABEL: Record<string, string> = {
  active: "Active", inactive: "Inactive", processing: "Processing", rejected: "Rejected", skipped: "Duplicate, skipped",
  failed: "Failed",
};

function steps(s: UploadState, embedModel: string, chunkSize: number, maxMb: number): UiStep[] {
  const st = s.stages;
  const sections = (st.extracting.data?.sections as string[] | undefined) ?? [];
  const chunkMap = (st.chunking.data?.chunk_map as { section: string; chunks: number }[] | undefined) ?? [];
  const p = s.progress;
  const total = p?.total ?? 0;
  const done = p?.done ?? 0;
  return [
    {
      key: "validating", title: "Checking the file", tech: "validation · duplicate check", status: st.validating.status,
      summary: st.validating.detail, keepOpen: st.validating.status !== "done",
      body: (
        <>
          <Checks items={[
            { kind: "ok", k: "File type", v: fileType(s.filename) || "?" },
            { kind: "ok", k: "Size limit", v: `${maxMb} MB` },
            st.validating.status === "blocked"
              ? { kind: "warn", k: "Duplicate?", v: "Yes. The fingerprint matches a stored version" }
              : st.validating.status === "failed"
                ? { kind: "bad", k: "Result", v: s.error?.message ?? "Rejected" }
                : { kind: "ok", k: "Duplicate?", v: st.validating.status === "done" ? "No. The content fingerprint is new" : "Checking…" },
          ]} />
          {st.validating.status === "blocked" && s.result ? <p className="callout warn">{s.result.message}</p> : null}
          {st.validating.status === "failed" && s.error ? <p className="callout bad">{s.error.message}</p> : null}
        </>
      ),
    },
    {
      key: "extracting", title: "Reading the text", tech: "text extraction", status: st.extracting.status,
      summary: st.extracting.detail,
      body: (
        <>
          <p>Text is pulled out page by page, keeping headings and page numbers so answers can cite them.</p>
          {sections.length ? <div className="secs">{sections.map((x, i) => <span key={i}>{x}</span>)}</div> : null}
          {st.extracting.status === "failed" ? <p className="callout bad">{s.error?.message}</p> : null}
        </>
      ),
    },
    {
      key: "chunking", title: "Splitting into chunks", tech: `chunking by heading · about ${chunkSize} tokens each`,
      status: st.chunking.status, summary: st.chunking.detail, keepOpen: true,
      body: (
        <>
          <p>Each section is cut into small passages. A passage never mixes two sections.</p>
          <div className="cm">
            {chunkMap.map((c, i) => (
              <div key={i} className="cm-row">
                <span>{c.section}</span><em>{c.chunks}</em>
                <span className="sq">{Array.from({ length: Math.min(c.chunks, 40) }, (_, k) => <i key={k} />)}</span>
              </div>
            ))}
          </div>
        </>
      ),
    },
    {
      key: "embedding", title: "Making it searchable", tech: `embeddings · ${embedModel} + keyword index`,
      status: st.embedding.status,
      summary: total ? `${done} of ${total} chunks indexed` : st.embedding.detail,
      body: (
        <>
          <div className="emb">
            {Array.from({ length: Math.min(total, 96) }, (_, k) => (
              <i key={k} className={k < Math.round((done / Math.max(total, 1)) * Math.min(total, 96)) ? "on" : ""} />
            ))}
          </div>
          <p>Each chunk is turned into numbers that capture its meaning, so a question can find it even with different
            words. A keyword index is built too, for exact terms.</p>
          {st.embedding.status === "failed" ? <p className="callout bad">{s.error?.message}</p> : null}
        </>
      ),
    },
    {
      key: "storing", title: "Switching to the new version", tech: "atomic activation", status: st.storing.status,
      summary: st.storing.detail, keepOpen: true,
      body: <p>Both changes happen in one step, so a question never sees a half-ready version. The previous version
        is kept for rollback and never searched.</p>,
    },
    {
      key: "ready", title: "Ready for questions", tech: "done", status: st.ready.status, summary: st.ready.detail,
      keepOpen: true,
      body: s.result?.status === "indexed" ? (
        <div className="callout good">{s.result.doc_name} v{s.result.version} is ready and listed in the Knowledge
          base. Questions now search v{s.result.version} only.</div>
      ) : null,
    },
  ];
}

export default function Upload() {
  const { config, toast } = useApp();
  const departments = config?.ui?.departments ?? [];
  const [state, dispatch] = useReducer(uploadReducer, initialUpload);
  const [view, setView] = useState<UploadState | null>(null);
  const [dept, setDept] = useState(load("evidex-dept", ""));
  const [owner, setOwner] = useState(load("evidex-owner", ""));
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [over, setOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const busy = state.status === "running";
  const shown = view ?? state;

  const refresh = () => getJSON<UploadList>("/api/uploads?limit=12").then((u) => setRows(u.uploads)).catch(() => undefined);
  useEffect(() => void refresh(), []);
  useEffect(() => {
    if (!dept && departments.length) setDept(departments[0]);
  }, [departments, dept]);

  async function send(file: File) {
    if (busy) return;
    if (!owner.trim()) {
      toast("Enter the owner first: the person to ask about this document.");
      return;
    }
    save("evidex-dept", dept);
    save("evidex-owner", owner);
    setView(null);
    dispatch({ type: "start", filename: file.name });
    const form = new FormData();
    form.append("file", file);
    form.append("department", dept);
    form.append("owner", owner);
    try {
      await postStream("/api/documents", form, (e) => dispatch({ type: "event", event: e }));
    } catch (err) {
      if (err instanceof ConnectionLost) dispatch({ type: "lost" });
    }
    void refresh();
  }

  async function openSteps(r: UploadRow) {
    try {
      const u = await getJSON<UploadSteps>(`/api/uploads/${r.id}`);
      setView(replayUpload(u.filename, u.events as StreamEvent[]));
    } catch {
      toast("Those steps are no longer available.");
    }
  }

  const title = shown.filename ? `Processing ${shown.filename}` : "Processing";
  const sub = shown.filename ? (shown.result?.status === "duplicate" ? "Same file as one already indexed"
    : shown.error ? shown.error.message : shown.result ? shown.result.message : "Each step appears here as it runs")
    : "Each step appears here as it runs";

  return (
    <>
      <div className="ph"><div><h1>Upload documents</h1></div><a className="btn" href="#kb">Open knowledge base</a></div>
      <div className="g2" style={{ marginBottom: 16, alignItems: "start" }}>
        <section className="card stack">
          <div>
            <h2>Add a file</h2>
            <p className="cs">PDF, DOCX, TXT, MD, XLSX, CSV or JSON · up to {config?.ingestion?.max_file_mb ?? "…"} MB</p>
          </div>
          <div className={`drop${over ? " over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) void send(f); }}>
            {IC.upload}
            <b>Drag a file here</b>
            <span className="note">Or choose one. Uploading a file with the same name creates a new version.</span>
            <input ref={fileRef} className="sr" type="file" id="upFile" aria-label="Choose a file"
              accept=".pdf,.docx,.txt,.md,.xlsx,.csv,.json"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f); e.target.value = ""; }} />
            <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>Choose a file</button>
          </div>
          <div className="row2">
            <div className="field">
              <label htmlFor="upDept">Department</label>
              <select id="upDept" value={dept} onChange={(e) => setDept(e.target.value)}>
                {departments.map((d) => <option key={d}>{d}</option>)}
              </select>
              <span className="help">Who the document belongs to</span>
            </div>
            <div className="field">
              <label htmlFor="upOwner">Owner</label>
              <input type="text" id="upOwner" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Name" />
              <span className="help">Person to ask about this document</span>
            </div>
          </div>
        </section>
        <section className="card" aria-live="polite">
          <div className="card-h"><div><h2>{title}</h2><p className="cs">{sub}</p></div></div>
          <Trace key={`${shown.filename}:${view ? "view" : "live"}`} kind="upload" done={shown.status === "complete" || shown.status === "error"}
            steps={shown.status === "idle" ? null : steps(shown, config?.models?.embedding ?? "embeddings",
              config?.ingestion?.chunk_size_tokens ?? 500, config?.ingestion?.max_file_mb ?? 25)}
            empty="Choose a file to watch it being checked, split into chunks, indexed and switched live." />
        </section>
      </div>
      <section className="card">
        <div className="card-h"><div><h2>Recent uploads</h2><p className="cs">Newest first · every version is kept</p></div></div>
        <div className="tw">
          <table>
            <thead><tr><th>File</th><th>Type</th><th>Version</th><th>Department</th><th>Owner</th><th>Uploaded</th>
              <th className="num">Size</th><th>Status</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><b className="wrap-any">{r.filename}</b></td>
                  <td><FileType t={r.file_type} /></td>
                  <td>{r.version ? <span className="ver">v{r.version}</span> : "—"}</td>
                  <td>{r.department}</td>
                  <td>{r.owner}</td>
                  <td title={dateTxt(r.uploaded_at)}>{age(r.uploaded_at)}</td>
                  <td className="num">{size(r.size_bytes)}</td>
                  <td><span className={`pill ${r.status === "failed" ? "rejected" : r.status}`} title={r.message}>
                    {STATUS_LABEL[r.status] ?? r.status}</span></td>
                  <td><button type="button" className="linkbtn" onClick={() => void openSteps(r)}>Steps</button></td>
                </tr>
              ))}
              {!rows.length ? <tr><td colSpan={9} className="muted">No uploads yet.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
