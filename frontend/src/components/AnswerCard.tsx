import { Fragment, type ReactNode } from "react";
import { I } from "../app/icons";
import { modelLabel } from "../app/format";
import type { AnswerPayload, ErrorEvent } from "../api/types";
import { ResultTable } from "./Trace";

export function FileType({ t }: { t: string }) {
  return <span className={`ft ${t}`}>{t}</span>;
}

function Meter({ score, threshold }: { score: number; threshold: number }) {
  return (
    <>
      <div className="meter">
        <div className="fill" style={{ width: `${Math.round(Math.min(1, Math.max(0, score)) * 100)}%` }} />
        <div className="thr" style={{ left: `${threshold * 100}%` }} />
      </div>
      <div className="scale"><span>0</span><span>refuses below {threshold.toFixed(2)}</span><span>1.0</span></div>
    </>
  );
}

export function levelOf(level: string) {
  if (level === "High") return { k: "high", t: "High confidence", why: "The best source closely matches your question." };
  if (level === "Medium") {
    return { k: "med", t: "Medium confidence", why: "The best source is relevant but less direct. Read the evidence before relying on it." };
  }
  return { k: "low", t: "Low confidence", why: "The match is weak. Read the evidence before relying on it." };
}

export function ConfidenceBox({ c }: { c: NonNullable<AnswerPayload["confidence"]> }) {
  const L = levelOf(c.level);
  const score = c.score ?? 0;
  return (
    <div className={`conf ${L.k}`}>
      <div className="conf-top"><b>{L.t}</b><span>{score.toFixed(2)}</span></div>
      <Meter score={score} threshold={c.threshold ?? 0} />
      <p className="why">{L.why} The score measures how well the best source matches your question.</p>
    </div>
  );
}

function highlight(text: string, marks: string[]): ReactNode {
  const parts: ReactNode[] = [];
  let rest = text;
  let key = 0;
  for (;;) {
    let idx = -1;
    let hit = "";
    for (const m of marks) {
      const i = m ? rest.indexOf(m) : -1;
      if (i >= 0 && (idx < 0 || i < idx)) {
        idx = i;
        hit = m;
      }
    }
    if (idx < 0) break;
    parts.push(<Fragment key={key++}>{rest.slice(0, idx)}</Fragment>, <mark key={key++}>{hit}</mark>);
    rest = rest.slice(idx + hit.length);
  }
  parts.push(<Fragment key={key++}>{rest}</Fragment>);
  return parts;
}

function secs(ms: number) {
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

function HowBtn({ on, onClick, label }: { on: boolean; onClick: () => void; label?: string }) {
  return (
    <button type="button" className={`howbtn${on ? " on" : ""}`} onClick={onClick}>
      {label ?? "How this was answered →"}
    </button>
  );
}

function NotEvaluated({ a }: { a: AnswerPayload }) {
  return a.not_evaluated ? <span className="ckw">{I.bang}Not evaluated with {modelLabel(a.model)}</span> : null;
}

export function AnswerCard({ a, selected, onHow, extra }: {
  a: AnswerPayload;
  selected: boolean;
  onHow: () => void;
  extra?: ReactNode;
}) {
  if (a.kind === "refusal") {
    const c = a.closest;
    return (
      <article className="acard refusal">
        <h3>Not in your documents</h3>
        <p className="big">{a.answer}</p>
        {c ? (
          <div className="conf none">
            <div className="conf-top">
              <b style={{ color: "var(--ink)" }}>
                Closest match: {c.section || c.doc_name}{c.page ? `, page ${c.page}` : `, ${c.source_location}`}
              </b>
              <span>{c.score?.toFixed(2)}</span>
            </div>
            <Meter score={c.score ?? 0} threshold={c.threshold} />
            <p className="why">
              {a.ai_called
                ? "The model found no supported answer in the passages, so nothing was made up."
                : "Below the minimum. The AI model was never asked, so it could not make anything up."}
            </p>
          </div>
        ) : (
          <p className="why">{a.missing || "Nothing in the selected documents matches."}</p>
        )}
        <div className="ans-foot">
          <span>{secs(a.elapsed_ms ?? 0)}{a.ai_called ? ` · ${modelLabel(a.model)}` : " · no AI call needed"}</span>
          {extra}
          <HowBtn on={selected} onClick={onHow} />
        </div>
      </article>
    );
  }

  if (a.kind === "calculation" && a.sql) {
    const s = a.sql;
    return (
      <article className="acard">
        <div className="lbl">Answer · calculated from a table</div>
        <p className="ans-text">{a.answer}</p>
        <span className="exact">{I.ok}Exact: calculated from the data, not written by the AI</span>
        <div className="evid">
          <div className="evid-head">
            <span className="refn">1</span>Evidence: {s.file} · v{s.version}{s.sheet ? ` · ${s.sheet}` : ""} ·{" "}
            {s.row_count.toLocaleString("en-IN")} rows
          </div>
          <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
            <pre>{s.query}</pre>
            {s.rows.length ? <ResultTable columns={s.columns} rows={s.rows} /> : null}
            {s.truncated ? <p className="note">Only the first {s.rows.length} rows are shown.</p> : null}
          </div>
        </div>
        <div className="checks-row">
          {a.checks.filter((c) => c.passed).map((c) => <span key={c.name} className="ck">{I.ok}{c.name}</span>)}
          <NotEvaluated a={a} />
        </div>
        <div className="ans-foot">
          <span>{modelLabel(a.model)} · {secs(a.elapsed_ms ?? 0)}</span>
          {extra}
          <HowBtn on={selected} onClick={onHow} />
        </div>
      </article>
    );
  }

  return (
    <article className="acard">
      <div className="lbl">Answer{a.partial ? " · partly in the documents" : ""}</div>
      <p className="ans-text">
        {a.sentences.map((s, i) => (
          <Fragment key={i}>
            {i ? " " : ""}{s.text}{s.refs.map((r) => <sup key={r}>{r}</sup>)}
          </Fragment>
        ))}
        {a.partial ? <> <span className="muted">The rest of this is not available in the provided documents.</span></> : null}
      </p>
      {a.confidence ? <ConfidenceBox c={a.confidence} /> : null}
      <div className="checks-row">
        {a.checks.filter((c) => c.passed).map((c) => <span key={c.name} className="ck">{I.ok}{c.name} checked</span>)}
        <NotEvaluated a={a} />
      </div>
      {a.evidence.map((e) => (
        <div key={e.ref} className="evid">
          <div className="evid-head">
            <span className="refn">{e.ref}</span>Evidence: {e.doc_name} · v{e.version}
            {e.page ? ` · page ${e.page}` : ""}{e.section ? ` · ${e.section}` : e.page ? "" : ` · ${e.source_location}`}
          </div>
          <blockquote>{highlight(e.chunk_text, e.highlights)}</blockquote>
        </div>
      ))}
      {a.missing ? <p className="note" style={{ marginTop: 10 }}>Not in the documents: {a.missing}</p> : null}
      <div className="ans-foot">
        <span>{modelLabel(a.model)} · {secs(a.elapsed_ms ?? 0)} on this machine</span>
        {extra}
        <HowBtn on={selected} onClick={onHow} />
      </div>
    </article>
  );
}

export function WorkingCard({ title, step, total, onWatch, watching }: {
  title: string;
  step: number;
  total: number;
  onWatch: () => void;
  watching: boolean;
}) {
  return (
    <div className="acard working">
      <span className="spin" aria-hidden="true" />
      <span className="t">
        {title}…<small>Step {step} of {total}. A full answer takes 15 to 20 s on the i5.</small>
      </span>
      <HowBtn on={watching} onClick={onWatch} label="Watch" />
    </div>
  );
}

export function ErrorCard({ error, lost, onRetry, onHow, selected }: {
  error?: ErrorEvent;
  lost: boolean;
  onRetry: () => void;
  onHow: () => void;
  selected: boolean;
}) {
  return (
    <article className="acard error" role="alert">
      <h3>{lost ? "Connection lost" : "Something went wrong"}</h3>
      <p>{lost ? "Connection lost. Retry?" : error?.message}</p>
      <div className="ans-foot">
        <span>{lost ? "The question was not sent again." : error?.code}</span>
        <span style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn" onClick={onRetry}>Retry</button>
          <HowBtn on={selected} onClick={onHow} />
        </span>
      </div>
    </article>
  );
}
