import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { runtimeProblem, useApp } from "../app/context";
import { fileType, modelLabel, sessionId, stripExt } from "../app/format";
import { getJSON } from "../api/client";
import { ConnectionLost, postStream } from "../api/stream";
import type { ActivityList, AnswerPayload, AskRequest, DocumentList, DocumentSummary, TraceResponse } from "../api/types";
import { AnswerCard, AnswerDetails, ErrorCard, FileType, WorkingCard, secs, summaryOf } from "../components/AnswerCard";
import { Trace, questionSteps, type UiStep } from "../components/Trace";
import { chatReducer, initialChat, runningTitle, totalSteps, type ChatMessage } from "./chatState";

let counter = 0;
const newId = () => `m${Date.now()}-${++counter}`;

function DocPicker({ docs, sel, setSel }: {
  docs: DocumentSummary[];
  sel: Record<number, boolean>;
  setSel: (s: Record<number, boolean>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDetailsElement>(null);
  const chosen = docs.filter((d) => sel[d.id] !== false);
  const all = chosen.length === docs.length;
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("click", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <details className="picker" open={open} ref={ref}>
      <summary onClick={(e) => { e.preventDefault(); setOpen(!open); }}>
        {chosen.length > 3 ? `+${chosen.length - 3} more · ` : ""}Choose documents
      </summary>
      <div className="pick-pop">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
          <b style={{ fontSize: 13.5 }}>Search in</b>
          <button type="button" className="linkbtn"
            onClick={() => setSel(Object.fromEntries(docs.map((d) => [d.id, !all])))}>
            {all ? "Clear all" : "Select all"}
          </button>
        </div>
        <p className="note" style={{ margin: "0 0 6px" }}>Only ticked documents are searched.</p>
        <ul className="docpick">
          {docs.map((d) => (
            <li key={d.id}>
              <label>
                <input type="checkbox" checked={sel[d.id] !== false} onChange={(e) => setSel({ ...sel, [d.id]: e.target.checked })} />
                <FileType t={fileType(d.name)} />
                <span className="nm" title={d.name}>{d.name}</span>
              </label>
            </li>
          ))}
          {!docs.length ? <li className="note">No documents yet. Upload one first.</li> : null}
        </ul>
      </div>
    </details>
  );
}

export default function Chat() {
  const { health, healthError, config, refreshHealth } = useApp();
  const [state, dispatch] = useReducer(chatReducer, initialChat);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const [input, setInput] = useState("");
  const [suggest, setSuggest] = useState<string[]>([]);
  // The side panel is a small summary until "Expand" or an answer's "More" opens it fully.
  const [expanded, setExpanded] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const traceRef = useRef<HTMLDivElement>(null);
  const busy = state.messages.some((m) => m.status === "working");
  const problem = runtimeProblem(health, healthError);

  useEffect(() => {
    getJSON<DocumentList>("/api/documents").then((d) => setDocs(d.documents)).catch(() => setDocs([]));
    getJSON<ActivityList>("/api/activity?result=answered&limit=50").then((a) => {
      const seen: string[] = [];
      for (const r of a.rows) if (r.kind === "question" && !seen.includes(r.detail)) seen.push(r.detail);
      setSuggest(seen.slice(0, 5));
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    const t = threadRef.current;
    if (t) t.scrollTop = t.scrollHeight;
  }, [state.messages.length, state.messages.at(-1)?.status]);

  // While an answer is running, keep the newest step in view (as the reference does).
  const live = state.messages.find((m) => m.id === state.selected && m.status === "working");
  useEffect(() => {
    const el = traceRef.current?.querySelector(".step.active");
    if (live && el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [live, live?.steps.length]);

  // Models are chosen in Settings only; this shows the one in use.
  const answerModel = config?.models?.answer || "";
  const chosen = docs.filter((d) => sel[d.id] !== false);
  const selected = state.messages.find((m) => m.id === state.selected) ?? null;

  async function run(id: string, request: AskRequest) {
    try {
      await postStream("/api/ask", JSON.stringify(request), (e) => {
        if (e.event === "stage") dispatch({ type: "stage", id, event: e.data });
        else if (e.event === "answer") dispatch({ type: "answer", id, answer: e.data });
        else if (e.event === "error") dispatch({ type: "error", id, error: e.data });
      }, { json: true });
    } catch (err) {
      if (err instanceof ConnectionLost) dispatch({ type: "lost", id });
      refreshHealth();
    }
  }

  function ask(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    const request: AskRequest = {
      question,
      session_id: sessionId(),
      document_ids: chosen.length === docs.length ? null : chosen.map((d) => d.id),
    };
    const id = newId();
    dispatch({ type: "ask", id, request, now: Date.now() });
    setInput("");
    void run(id, request);
  }

  function retry(m: ChatMessage) {
    if (busy) return;
    dispatch({ type: "retry", id: m.id, now: Date.now() });
    void run(m.id, m.request);
  }

  async function show(m: ChatMessage, expand = true) {
    if (expand) setExpanded(state.selected === m.id ? !expanded : true);
    dispatch({ type: "select", id: m.id });
    const tid = m.answer?.trace_id;
    if (tid && !m.steps.length) {
      try {
        const t = await getJSON<TraceResponse>(`/api/traces/${tid}`);
        dispatch({ type: "steps", id: m.id, steps: t.steps });
      } catch {
        /* keep what we have */
      }
    }
  }

  const traceSteps = useMemo(() => {
    if (!selected) return null;
    const running = selected.status === "working" ? { title: runningTitle(selected) } : null;
    const steps = questionSteps(selected.steps, running);
    if (selected.status === "lost" || selected.status === "error") {
      steps.push({ key: "stopped", title: "Stopped", tech: selected.error?.code ?? "connection", status: "failed",
        summary: selected.status === "lost" ? "Connection lost. The question was not sent again." : selected.error?.message });
    }
    return steps;
  }, [selected]);

  const modelName = (n: string) => modelLabel(n);

  return (
    <div className="ws">
      <section className="col" aria-label="Conversation">
        <div className="col-head">
          <h2>Ask your documents</h2>
          <p>Answering with {answerModel ? modelName(answerModel) : "the configured model"} · searching {chosen.length} of {docs.length} documents</p>
          <div className="scope">
            <span>Searching</span>
            {chosen.length ? chosen.slice(0, 3).map((d) => (
              <span key={d.id} className="sc" title={d.name}><FileType t={fileType(d.name)} />{d.title || stripExt(d.name)}</span>
            )) : <span className="sc" style={{ padding: "3px 10px" }}>No documents selected</span>}
            <DocPicker docs={docs} sel={sel} setSel={setSel} />
          </div>
        </div>
        <div className="scroll thread-scroll" ref={threadRef}>
          <div className="thread" aria-live="polite">
            {problem ? <div className="banner bad" role="alert">{problem}{healthError || /settings/i.test(problem) ? "" : " Fix it in Settings."}</div> : null}
            {!state.messages.length ? (
              <div className="empty">
                <b>Ask anything about your documents</b>
                Every answer shows its evidence and a confidence score. Choose which documents to search above. Each step
                appears on the right.
              </div>
            ) : null}
            {state.messages.map((m) => (
              <Message key={m.id} m={m} selected={state.selected === m.id && expanded} onShow={() => void show(m)}
                onRetry={() => retry(m)} />
            ))}
          </div>
        </div>
        <div className="composer">
          {suggest.length ? (
            <div className="sugg">
              {suggest.map((s) => <button key={s} type="button" disabled={busy} onClick={() => ask(s)}>{s}</button>)}
            </div>
          ) : null}
          <form className="askrow" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
            <a className="modelchip" href="#settings" title="The answer model in use. Change models in Settings.">
              {answerModel ? modelName(answerModel) : "Model"}</a>
            <label htmlFor="askInput" className="sr">Your question</label>
            <input id="askInput" autoComplete="off" placeholder="Ask a question about your documents…" value={input}
              onChange={(e) => setInput(e.target.value)} />
            <button className="btn primary" type="submit" style={{ minHeight: 44 }} disabled={busy || !input.trim()}>Send</button>
          </form>
        </div>
      </section>
      <aside className="col" aria-label="Behind the scenes">
        <div className="col-head">
          <div className="col-head-row">
            <h2>Behind the scenes</h2>
            {selected ? (
              <button type="button" className="mini more" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
                {expanded ? "Show less" : "Expand"}
              </button>
            ) : null}
          </div>
          <p>{selected ? `“${selected.request.question}”` : "Every step the agent takes, as it happens"}</p>
        </div>
        <div className="scroll" ref={traceRef}>
          <div className="pad">
            {!selected || !expanded ? (
              <PanelSummary m={selected} steps={traceSteps} onExpand={() => setExpanded(true)} />
            ) : (
              <>
                {selected.status === "complete" && selected.answer ? <AnswerDetails a={selected.answer as AnswerPayload} /> : null}
                <h4 className="details-h">Every step</h4>
                <Trace key={selected.id} steps={traceSteps} kind="question" done={selected.status !== "working"}
                  empty="Ask a question to see how it is understood, searched, ranked, answered and checked." />
              </>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

/** The collapsed side panel: step dots and three numbers, nothing more. */
function PanelSummary({ m, steps, onExpand }: { m: ChatMessage | null; steps: UiStep[] | null; onExpand: () => void }) {
  if (!m || !steps) {
    return <div className="tr-empty"><b>Nothing running yet</b>Ask a question. A short summary of how it was answered appears here.</div>;
  }
  const a = m.status === "complete" ? (m.answer as AnswerPayload | undefined) : undefined;
  const s = a ? summaryOf(a) : null;
  const shown = steps.filter((x) => x.status !== "skipped" && x.status !== "pending");
  const last = steps.filter((x) => x.status === "active").at(-1);
  return (
    <div className="psum">
      <div className="stepdots" aria-label={`${shown.length} steps`}>
        {shown.map((x) => <i key={x.key} className={x.status} title={`${x.title}${x.summary ? `: ${x.summary}` : ""}`} />)}
        <span>{m.status === "working" ? `${last?.title ?? "Working"}…` : `${shown.length} step${shown.length === 1 ? "" : "s"}`}
          {a ? ` · ${secs(a.elapsed_ms ?? 0)}` : ""}</span>
      </div>
      {s && a?.kind !== "chat" ? (
        <div className="tiles">
          <div className={`tile conf-${s.conf?.k ?? "none"}`}><b>{s.conf?.short ?? "—"}</b><small>Confidence</small></div>
          <div className={`tile${s.sources ? "" : " dim"}`}><b>{s.sources}</b><small>Source{s.sources === 1 ? "" : "s"}</small></div>
          <div className="tile"><b>{s.checks ? `${s.passed}/${s.checks}` : "—"}</b><small>Checks passed</small></div>
        </div>
      ) : null}
      {a?.kind === "chat" ? <p className="note">Small talk: no search and no AI call.</p> : null}
      <button type="button" className="linkbtn" onClick={onExpand}>
        {m.status === "working" ? "Watch every step" : "See confidence, checks, evidence and every step"} ›
      </button>
    </div>
  );
}

function Message({ m, selected, onShow, onRetry }: {
  m: ChatMessage;
  selected: boolean;
  onShow: () => void;
  onRetry: () => void;
}) {
  return (
    <>
      <div className="q">{m.request.question}</div>
      {m.status === "working" ? (
        <WorkingCard title={runningTitle(m)} step={Math.min(m.steps.length + 1, totalSteps(m))} total={totalSteps(m)}
          onWatch={onShow} watching={selected} />
      ) : null}
      {m.status === "complete" && m.answer ? <AnswerCard a={m.answer as AnswerPayload} selected={selected} onMore={onShow} /> : null}
      {m.status === "error" || m.status === "lost" ? (
        <ErrorCard error={m.error} lost={m.status === "lost"} onRetry={onRetry} onMore={onShow} selected={selected} />
      ) : null}
    </>
  );
}
