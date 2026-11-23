// Answers in the chat are kept minimal: the answer and one row of small icons
// (confidence, evidence, time, More). Everything else — the confidence meter, the checks,
// the evidence quotes, the SQL and every step — is in AnswerDetails, shown in the right-hand
// panel when it is expanded.
import { Fragment, type ReactNode } from "react";
import { I } from "../app/icons";
import { modelLabel } from "../app/format";
import type { AnswerPayload, ErrorEvent } from "../api/types";
import { Checks, ResultTable } from "./Trace";

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

export function secs(ms: number) {
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

/** The small summary used by the chat icons and the collapsed side panel. */
export function summaryOf(a: AnswerPayload) {
  const conf = a.kind === "chat" ? null
    : a.kind === "refusal" ? { k: "none", short: "Not found", long: "Not in your documents" }
      : a.kind === "calculation" ? { k: "high", short: "Exact", long: "Exact: calculated from the data" }
        : a.confidence ? { k: levelOf(a.confidence.level).k, short: `${a.confidence.level} ${(a.confidence.score ?? 0).toFixed(2)}`,
          long: levelOf(a.confidence.level).t } : null;
  // A refusal may come without evidence or checks (they are optional in the payload).
  const evidence = a.evidence ?? [];
  const checks = a.checks ?? [];
  const sources = a.kind === "calculation" && a.sql ? 1 : evidence.length;
  return { conf, sources, passed: checks.filter((c) => c.passed).length, checks: checks.length };
}

function MoreBtn({ on, onClick, label = "More" }: { on: boolean; onClick: () => void; label?: string }) {
  return (
    <button type="button" className={`mini more${on ? " on" : ""}`} onClick={onClick} aria-pressed={on}
      title="Show the confidence, checks, evidence and every step on the right">
      {label}<span aria-hidden="true">›</span>
    </button>
  );
}

export function AnswerCard({ a, selected, onMore }: {
  a: AnswerPayload;
  selected: boolean;
  onMore: () => void;
}) {
  if (a.kind === "chat") {
    return <article className="acard chat"><p className="ans-text">{a.answer}</p></article>;
  }
  const s = summaryOf(a);
  return (
    <article className={`acard${a.kind === "refusal" ? " refusal" : ""}`}>
      <p className="ans-text">
        {a.kind === "text" && a.sentences?.length ? a.sentences.map((x, i) => (
          <Fragment key={i}>
            {i ? " " : ""}{x.text}{x.refs.map((r) => <sup key={r}>{r}</sup>)}
          </Fragment>
        )) : a.answer}
        {a.partial ? <> <span className="muted">The rest of this is not available in the provided documents.</span></> : null}
      </p>
      <div className="mini-row">
        {s.conf ? (
          <span className={`mini conf-${s.conf.k}`} title={s.conf.long}>
            <i className="dotc" aria-hidden="true" />{s.conf.short}
          </span>
        ) : null}
        <span className={`mini${s.sources ? "" : " dim"}`}
          title={s.sources ? "Evidence from your documents is available" : "No evidence: nothing in the documents matched"}>
          {s.sources ? I.ok : I.x}{s.sources ? `${s.sources} source${s.sources > 1 ? "s" : ""}` : "No evidence"}
        </span>
        <span className="mini dim" title={a.ai_called ? `Answered by ${modelLabel(a.model)}` : "No AI call was needed"}>
          {secs(a.elapsed_ms ?? 0)}
        </span>
        <MoreBtn on={selected} onClick={onMore} />
      </div>
    </article>
  );
}

/** Everything behind an answer, for the expanded side panel (and the Activity page). */
export function AnswerDetails({ a }: { a: AnswerPayload }) {
  if (a.kind === "chat") {
    return <p className="note">A friendly reply to small talk. No documents were searched and no AI model was asked.</p>;
  }
  const c = a.closest;
  return (
    <div className="details">
      <section>
        <h4>Confidence</h4>
        {a.kind === "refusal" ? (
          c ? (
            <div className="conf none">
              <div className="conf-top">
                <b style={{ color: "var(--ink)" }}>Closest match: {c.section || c.doc_name}{c.page ? `, page ${c.page}` : `, ${c.source_location}`}</b>
                <span>{c.score?.toFixed(2)}</span>
              </div>
              <Meter score={c.score ?? 0} threshold={c.threshold} />
              <p className="why">
                {a.ai_called
                  ? "The model found no supported answer in the passages, so nothing was made up."
                  : "Below the minimum. The AI model was never asked, so it could not make anything up."}
              </p>
            </div>
          ) : <p className="note">{a.missing || "Nothing in the selected documents matches."}</p>
        ) : a.kind === "calculation" ? (
          <p className="callout good">Exact. Calculated from the data by a query, not written by the AI, so it needs no confidence score.</p>
        ) : a.confidence ? <ConfidenceBox c={a.confidence} /> : <p className="note">No score.</p>}
      </section>
      {a.checks?.length ? (
        <section>
          <h4>Checks</h4>
          <Checks items={a.checks.map((x) => ({ kind: x.passed ? "ok" : "bad", k: x.name, v: x.detail }))} />
        </section>
      ) : null}
      <section>
        <h4>Evidence</h4>
        {a.kind === "calculation" && a.sql ? (
          <div className="evid">
            <div className="evid-head">
              <span className="refn">1</span>{a.sql.file} · v{a.sql.version}{a.sql.sheet ? ` · ${a.sql.sheet}` : ""} ·{" "}
              {a.sql.row_count.toLocaleString("en-IN")} rows
            </div>
            <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
              <pre>{a.sql.query}</pre>
              {a.sql.rows.length ? <ResultTable columns={a.sql.columns} rows={a.sql.rows} /> : null}
              {a.sql.truncated ? <p className="note">Only the first {a.sql.rows.length} rows are shown.</p> : null}
            </div>
          </div>
        ) : a.evidence?.length ? a.evidence.map((e) => (
          <div key={e.ref} className="evid">
            <div className="evid-head">
              <span className="refn">{e.ref}</span>{e.doc_name} · v{e.version}
              {e.page ? ` · page ${e.page}` : ""}{e.section ? ` · ${e.section}` : e.page ? "" : ` · ${e.source_location}`}
            </div>
            <blockquote>{highlight(e.chunk_text, e.highlights)}</blockquote>
          </div>
        )) : <p className="note">No evidence: nothing in the selected documents supports an answer.</p>}
        {a.missing && a.kind !== "refusal" ? <p className="note" style={{ marginTop: 10 }}>Not in the documents: {a.missing}</p> : null}
      </section>
      <p className="note">
        {a.ai_called ? `${modelLabel(a.model)}${a.not_evaluated ? " (not evaluated)" : ""}` : "No AI call needed"} · {secs(a.elapsed_ms ?? 0)}
      </p>
    </div>
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
      <span className="t">{title}…<small>Step {step} of {total}</small></span>
      <MoreBtn on={watching} onClick={onWatch} label="Watch" />
    </div>
  );
}

export function ErrorCard({ error, lost, onRetry, onMore, selected }: {
  error?: ErrorEvent;
  lost: boolean;
  onRetry: () => void;
  onMore: () => void;
  selected: boolean;
}) {
  return (
    <article className="acard error" role="alert">
      <h3>{lost ? "Connection lost" : "Something went wrong"}</h3>
      <p>{lost ? "Connection lost. Retry?" : error?.message}</p>
      <div className="mini-row">
        <button type="button" className="mini" onClick={onRetry}>Retry</button>
        <MoreBtn on={selected} onClick={onMore} />
      </div>
    </article>
  );
}
