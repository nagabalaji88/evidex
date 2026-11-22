import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { runtimeProblem, useApp } from "../app/context";
import { fileType, modelLabel, sessionId, stripExt } from "../app/format";
import { getJSON } from "../api/client";
import { ConnectionLost, postStream } from "../api/stream";
import type { ActivityList, AnswerPayload, AskRequest, DocumentList, DocumentSummary, ModelList, TraceResponse } from "../api/types";
import { AnswerCard, ErrorCard, FileType, WorkingCard } from "../components/AnswerCard";
import { Trace, questionSteps } from "../components/Trace";
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
  const [models, setModels] = useState<ModelList | null>(null);
  const [model, setModel] = useState<string>("");
  const [input, setInput] = useState("");
  const [suggest, setSuggest] = useState<string[]>([]);
  const threadRef = useRef<HTMLDivElement>(null);
  const traceRef = useRef<HTMLDivElement>(null);
  const busy = state.messages.some((m) => m.status === "working");
  const problem = runtimeProblem(health, healthError);

  useEffect(() => {
    getJSON<DocumentList>("/api/documents").then((d) => setDocs(d.documents)).catch(() => setDocs([]));
    getJSON<ModelList>("/api/models").then((m) => {
      setModels(m);
      setModel((cur) => cur || m.default);
    }).catch(() => undefined);
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

  const answerModel = model || config?.models?.answer || "";
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
      model: answerModel || null,
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

  async function show(m: ChatMessage) {
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

  const modelOptions = (models?.models ?? []).filter((m) => !m.is_embedding);
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
            {problem ? <div className="banner bad" role="alert">{problem}{healthError ? "" : " Answers need the local model."}</div> : null}
            {!state.messages.length ? (
              <div className="empty">
                <b>Ask anything about your documents</b>
                Every answer shows its evidence and a confidence score. Choose which documents to search above. Each step
                appears on the right.
              </div>
            ) : null}
            {state.messages.map((m) => (
              <Message key={m.id} m={m} selected={state.selected === m.id} onShow={() => void show(m)}
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
            <label htmlFor="modelSel" className="sr">Answer model</label>
            <select id="modelSel" disabled={busy} value={answerModel} onChange={(e) => setModel(e.target.value)}>
              {!modelOptions.some((m) => m.name === answerModel) && answerModel ? <option value={answerModel}>{modelName(answerModel)}</option> : null}
              {modelOptions.map((m) => (
                <option key={m.name} value={m.name} disabled={!m.fits_in_ram}>
                  {modelName(m.name)}{m.tested ? "" : m.fits_in_ram ? " (not evaluated)" : " (needs more RAM)"}
                </option>
              ))}
            </select>
            <label htmlFor="askInput" className="sr">Your question</label>
            <input id="askInput" autoComplete="off" placeholder="Ask a question about your documents…" value={input}
              onChange={(e) => setInput(e.target.value)} />
            <button className="btn primary" type="submit" style={{ minHeight: 44 }} disabled={busy || !input.trim()}>Send</button>
          </form>
        </div>
      </section>
      <aside className="col" aria-label="Behind the scenes">
        <div className="col-head">
          <h2>Behind the scenes</h2>
          <p>{selected ? `“${selected.request.question}”` : "Every step the agent takes, as it happens"}</p>
        </div>
        <div className="scroll" ref={traceRef}>
          <div className="pad">
            <Trace key={selected?.id ?? "none"} steps={traceSteps} kind="question" done={!!selected && selected.status !== "working"}
              empty="Ask a question to see how it is understood, searched, ranked, answered and checked." />
          </div>
        </div>
      </aside>
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
      {m.status === "complete" && m.answer ? <AnswerCard a={m.answer as AnswerPayload} selected={selected} onHow={onShow} /> : null}
      {m.status === "error" || m.status === "lost" ? (
        <ErrorCard error={m.error} lost={m.status === "lost"} onRetry={onRetry} onHow={onShow} selected={selected} />
      ) : null}
    </>
  );
}
