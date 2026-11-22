import { useEffect, useMemo, useState } from "react";
import { useApp } from "../app/context";
import { age, dateTxt, fileType, fmt, size } from "../app/format";
import { I, IC } from "../app/icons";
import { getJSON, sendJSON } from "../api/client";
import type { ActionResult, DocumentDetail, DocumentList, DocumentSummary, VersionInfo } from "../api/types";
import { FileType } from "../components/AnswerCard";

function contentTxt(d: { path: string; pages?: number | null; chunks: number; table_rows: number; file_type?: string },
  type: string) {
  if (d.path === "table") return `${fmt(d.table_rows)} ${type === "JSON" ? "records" : "rows"}`;
  const n = (c: number, w: string) => `${c} ${w}${c === 1 ? "" : "s"}`;
  return `${d.pages ? `${n(d.pages, "page")} · ` : ""}${n(d.chunks, "chunk")}`;
}

export default function KnowledgeBase() {
  const { config, toast } = useApp();
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [selId, setSelId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [dept, setDept] = useState("");
  const [openSec, setOpenSec] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const loadList = () =>
    getJSON<DocumentList>("/api/documents").then((d) => {
      setDocs(d.documents);
      setSelId((cur) => cur ?? d.documents[0]?.id ?? null);
    }).catch(() => setDocs([]));
  const loadDetail = (id: number) => getJSON<DocumentDetail>(`/api/documents/${id}`).then(setDetail).catch(() => setDetail(null));

  useEffect(() => void loadList(), []);
  useEffect(() => {
    setOpenSec(null);
    setConfirm(null);
    if (selId !== null) void loadDetail(selId);
  }, [selId]);

  const list = useMemo(() => (docs ?? []).filter((d) => {
    const s = q.toLowerCase();
    return (!s || d.name.toLowerCase().includes(s) || d.department.toLowerCase().includes(s) || d.title.toLowerCase().includes(s))
      && (!type || fileType(d.name) === type) && (!dept || d.department === dept);
  }), [docs, q, type, dept]);

  const types = useMemo(() => [...new Set((docs ?? []).map((d) => fileType(d.name)))].sort(), [docs]);

  async function action(label: string, fn: () => Promise<ActionResult>) {
    setBusy(true);
    try {
      const r = await fn();
      toast(r.message);
      if (selId !== null) await loadDetail(selId);
      await loadList();
    } catch (e) {
      toast(`${label} failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  const d = detail?.summary;
  const t = d ? fileType(d.name) : "";

  return (
    <>
      <div className="ph"><div><h1>Knowledge base</h1></div><a className="btn primary" href="#upload">{IC.uploadSmall}Upload</a></div>
      <div className="kb">
        <section className="card">
          <div className="filters">
            <label className="sr" htmlFor="kbQ">Search documents</label>
            <input id="kbQ" type="text" placeholder="Search by name or department" value={q} onChange={(e) => setQ(e.target.value)} />
            <label className="sr" htmlFor="kbType">Type</label>
            <select id="kbType" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All types</option>
              {types.map((x) => <option key={x}>{x}</option>)}
            </select>
            <label className="sr" htmlFor="kbDept">Department</label>
            <select id="kbDept" value={dept} onChange={(e) => setDept(e.target.value)}>
              <option value="">All departments</option>
              {(config?.ui?.departments ?? []).map((x) => <option key={x}>{x}</option>)}
            </select>
          </div>
          <div className="tw">
            <table>
              <thead><tr><th>Document</th><th>Department</th><th>Version</th><th>Content</th><th>Updated</th></tr></thead>
              <tbody>
                {list.map((x) => (
                  <tr key={x.id} className={`clickable${x.id === selId ? " sel" : ""}`} tabIndex={0}
                    onClick={() => setSelId(x.id)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelId(x.id); } }}>
                    <td><div style={{ display: "flex", gap: 8, alignItems: "center" }}><FileType t={fileType(x.name)} /><b className="wrap-any">{x.name}</b></div></td>
                    <td>{x.department}</td>
                    <td><span className="ver">v{x.active_version}</span>{x.version_count > 1 ? <> <span className="note">of {x.version_count}</span></> : null}</td>
                    <td>{contentTxt(x, fileType(x.name))}</td>
                    <td title={dateTxt(x.updated_at)}>{age(x.updated_at)}</td>
                  </tr>
                ))}
                {docs && !list.length ? (
                  <tr><td colSpan={5} className="muted">{docs.length ? "No documents match these filters." : "No documents yet. Upload one to start."}</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card">
          {!d || !detail ? (
            <div className="empty-card"><b>No document selected</b>Pick a document to see its versions and sections.</div>
          ) : (
            <>
              <div className="card-h">
                <div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <FileType t={t} /><h2 className="wrap-any" style={{ fontSize: 17 }}>{d.name}</h2>
                    <span className="pill active">Active v{d.active_version}</span>
                  </div>
                  <p className="cs">{d.department} · owned by {d.owner}</p>
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button type="button" className="btn" disabled={busy}
                    onClick={() => void action("Re-index", () => sendJSON<ActionResult>(`/api/documents/${d.id}/reindex`, "POST"))}>
                    {busy ? "Working…" : "Re-index"}
                  </button>
                  <a className="btn" href={`/api/documents/${d.id}/download`} download>Download</a>
                </div>
              </div>
              <div className="meta-grid">
                <div><span className="lbl">Active version</span><b>v{d.active_version}</b></div>
                <div><span className="lbl">Content</span><b>{contentTxt(d, t)}</b></div>
                <div><span className="lbl">Size</span><b>{size(d.size_bytes)}</b></div>
                <div><span className="lbl">Used in answers</span><b>{detail.used_in_answers}</b></div>
              </div>
              <div className="h3">Version history</div>
              <ul className="vt">
                {detail.versions.slice().reverse().map((v: VersionInfo) => (
                  <li key={v.id} className={v.status}>
                    <span className="vd" />
                    <div className="vh">
                      <b>v{v.version}</b>
                      <span className={`pill ${v.status}`}>{v.status === "active" ? "Active" : "Inactive"}</span>
                      <span className="note">{dateTxt(v.uploaded_at)} · {v.owner}</span>
                    </div>
                    <p>{v.note} · {contentTxt({ ...v, path: d.path }, t)}{v.status === "inactive" ? " · kept for rollback, never searched" : ""}</p>
                    {v.status === "inactive" ? (
                      confirm === v.version ? (
                        <span className="confirm">
                          <span>Make v{v.version} active? v{d.active_version} becomes inactive.</span>
                          <button type="button" className="btn primary" disabled={busy}
                            onClick={() => void action("Roll back", () => sendJSON<ActionResult>(`/api/documents/${d.id}/rollback`, "POST", { version: v.version }))}>
                            Confirm roll back</button>
                          <button type="button" className="btn" onClick={() => setConfirm(null)}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" className="linkbtn" onClick={() => setConfirm(v.version)}>Roll back to v{v.version}</button>
                      )
                    ) : null}
                  </li>
                ))}
              </ul>
              {d.path === "text" ? (
                <>
                  <div className="h3">Sections and chunks <span className="note" style={{ fontWeight: 400 }}>· v{d.active_version}</span></div>
                  <ul className="sec-list">
                    {detail.sections.map((s, i) => (
                      <li key={i}>
                        <button type="button" aria-expanded={openSec === i} onClick={() => setOpenSec(openSec === i ? null : i)}>
                          <span className="nm">{s.name}</span>
                          <span className="note">{s.page ? `p.${s.page}` : s.source_location}</span>
                          <span className="ver">{s.chunks} chunk{s.chunks === 1 ? "" : "s"}</span>{I.chev}
                        </button>
                        {openSec === i ? (
                          <div className="pv">
                            <div className="chunk">
                              <div className="meta">
                                <span>{d.name}</span><span>v{d.active_version}</span>
                                <span>{s.source_location}</span><span>{s.tokens} tokens</span>
                              </div>
                              <p>{s.preview}{s.preview.length >= 400 ? "…" : ""}</p>
                            </div>
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                detail.tables.map((tb) => (
                  <div key={tb.table_name}>
                    <div className="h3">Columns{tb.sheet ? <span className="note" style={{ fontWeight: 400 }}> · sheet {tb.sheet}</span> : null}</div>
                    <div className="cols">{tb.columns.map((c) => <span key={c.name} title={c.type}>{c.name}</span>)}</div>
                    <div className="h3">Sample rows <span className="note" style={{ fontWeight: 400 }}>· 3 of {fmt(tb.row_count)}</span></div>
                    <div className="tw">
                      <table>
                        <thead><tr>{tb.columns.map((c) => <th key={c.name}>{c.name}</th>)}</tr></thead>
                        <tbody>
                          {tb.sample_rows.map((r, i) => (
                            <tr key={i}>{r.map((c, j) => <td key={j}>{typeof c === "number" ? c.toLocaleString("en-IN") : String(c ?? "")}</td>)}</tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))
              )}
              {d.path === "table" ? <p className="note" style={{ marginTop: 8 }}>Loaded into a local SQLite table. Calculation questions run read-only queries on it.</p> : null}
            </>
          )}
        </section>
      </div>
    </>
  );
}
