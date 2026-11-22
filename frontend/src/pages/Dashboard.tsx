import { useEffect, useState } from "react";
import { dateTxt, fmt, pct } from "../app/format";
import { getJSON } from "../api/client";
import type { Stats } from "../api/types";
import { FileType } from "../components/AnswerCard";
import { Checks } from "../components/Trace";

const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function Kpi({ k, v, s, cls = "", small }: { k: string; v: string | number; s: string; cls?: string; small?: string }) {
  return (
    <div className={`kpi ${cls}`}>
      <div className="k">{k}</div>
      <div className="v">{v}{small ? <small>{small}</small> : null}</div>
      <div className="s">{s}</div>
    </div>
  );
}

/** An even step so that 3 steps cover the tallest bar: 3 -> 1, 26 -> 10, 140 -> 50. */
function niceStep(top: number): number {
  const raw = Math.max(1, top / 3);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

function PerDayChart({ days }: { days: Stats["per_day"] }) {
  const W = 640, H = 230, L = 34, B = 196, T = 20;
  const n = days.length;
  const bw = Math.min(50, (W - L - 20) / n - 8);
  const gap = n > 1 ? (W - L - 20 - bw * n) / (n - 1) : 0;
  const step = niceStep(Math.max(1, ...days.map((d) => d.answered + d.refused + d.errors)));
  const max = step * 3;
  const y = (v: number) => B - (v / max) * (B - T);
  const ticks = [0, step, 2 * step, max];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Questions per day, answered, not in documents and errors">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - 10} y1={y(t)} y2={y(t)} style={{ stroke: "var(--ch-grid)" }} />
          <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="11" style={{ fill: "var(--ch-text)" }}>{t}</text>
        </g>
      ))}
      {days.map((d, i) => {
        const x = L + 10 + i * (bw + gap);
        const a = d.answered, r = d.refused, e = d.errors;
        const dt = new Date(`${d.date}T00:00:00`);
        const label = `${WD[dt.getDay()]} ${dt.getDate()}`;
        return (
          <g key={d.date}>
            {a ? <rect x={x} y={y(a)} width={bw} height={B - y(a)} style={{ fill: "var(--ch-a)" }} rx="2"><title>{label}: {a} answered</title></rect> : null}
            {r ? <rect x={x} y={y(a + r)} width={bw} height={Math.max(0, y(a) - y(a + r) - 1)} style={{ fill: "var(--ch-r)" }}><title>{r} not in documents</title></rect> : null}
            {e ? <rect x={x} y={y(a + r + e)} width={bw} height={Math.max(0, y(a + r) - y(a + r + e) - 1)} style={{ fill: "var(--ch-e)" }}><title>{e} errors</title></rect> : null}
            <text x={x + bw / 2} y={y(a + r + e) - 6} textAnchor="middle" fontSize="11.5" fontWeight="600" style={{ fill: "var(--ch-ink)" }}>{a + r + e}</text>
            <text x={x + bw / 2} y={B + 18} textAnchor="middle" fontSize="11.5" style={{ fill: "var(--ch-text)" }}>{label}</text>
          </g>
        );
      })}
    </svg>
  );
}

const CONF_COLOURS: Record<string, string> = {
  High: "var(--c-high)", Medium: "var(--c-med)", Low: "var(--c-low)", Exact: "var(--c-exact)",
};

export default function Dashboard() {
  const [s, setS] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Stats>("/api/stats?days=7").then(setS).catch((e) => setErr((e as Error).message));
  }, []);
  if (err) return <section className="card"><p className="callout bad">Could not load the dashboard: {err}</p></section>;
  if (!s) return <section className="card"><div className="empty-card"><b>Loading…</b></div></section>;

  const total = s.questions;
  const answeredConf = s.confidence.reduce((a, c) => a + c.count, 0);
  const tmax = Math.max(1, ...s.document_types.map((t) => t.count));
  const cmax = Math.max(1, ...s.most_cited.map((t) => t.count));
  const h = s.health;
  const th = s.thresholds;
  const from = dateTxt(s.since);
  return (
    <>
      <div className="ph"><div><h1>Dashboard</h1><p>Last {s.days} days · from {from}</p></div><a className="btn" href="#chat">Ask a question</a></div>
      <div className="kpis">
        <Kpi cls="gr2" k="Documents" v={s.documents} s={`${s.versions} versions in total · ${s.kept_for_rollback} kept for rollback`} />
        <Kpi cls="gr3" k="Indexed content" v={fmt(s.chunks)} small="chunks" s={`${fmt(s.table_rows)} table rows from spreadsheets and JSON`} />
        <Kpi cls="gr1" k="Questions asked" v={fmt(total)} s={`About ${total / s.days >= 1 ? Math.round(total / s.days) : (total / s.days).toFixed(1)} a day`} />
        <Kpi cls="good" k="Answered" v={`${pct(s.answered, total)}%`} s={`${fmt(s.answered)} with evidence`} />
        <Kpi cls="amber" k="Not in documents" v={`${pct(s.not_in_documents, total)}%`} s={`${fmt(s.not_in_documents)} refused instead of guessing`} />
        <Kpi k="Errors" v={s.errors} s={`${pct(s.errors, total)}% · timeouts and failed checks`} />
        <Kpi k="Average response" v={s.avg_response_s ?? "—"} small={s.avg_response_s !== null ? "s" : undefined}
          s={s.p95_response_s !== null ? `95% of answers under ${s.p95_response_s} s` : "No answers yet"} />
        <Kpi k="Average confidence" v={s.avg_confidence !== null && s.avg_confidence !== undefined ? s.avg_confidence.toFixed(2) : "—"}
          s={`On text answers · High from ${th.high_from.toFixed(2)}`} />
      </div>
      <div className="g2" style={{ marginBottom: 16 }}>
        <section className="card">
          <div className="card-h"><div><h2>Questions per day</h2><p className="cs">Most questions are answered; refusals show gaps in the documents</p></div></div>
          <PerDayChart days={s.per_day} />
          <div className="legend">
            <span><i style={{ background: "var(--ch-a)" }} />Answered</span>
            <span><i style={{ background: "var(--ch-r)" }} />Not in documents</span>
            <span><i style={{ background: "var(--ch-e)" }} />Errors</span>
          </div>
        </section>
        <section className="card">
          <div className="card-h"><div><h2>Answer confidence</h2><p className="cs">Of {answeredConf} answered questions in this period</p></div></div>
          <div className="seg" role="img" aria-label="Confidence split">
            {s.confidence.map((c) => (
              <i key={c.name} style={{ width: `${answeredConf ? (c.count / answeredConf) * 100 : 0}%`, background: CONF_COLOURS[c.name] }}
                title={`${c.name}: ${c.count}`} />
            ))}
          </div>
          <ul className="lst">
            {s.confidence.map((c) => (
              <li key={c.name}>
                <span><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: CONF_COLOURS[c.name], marginRight: 8 }} />
                  {c.name === "Exact" ? "Exact (calculated)" : c.name}</span>
                <b>{c.count} <span className="muted" style={{ fontWeight: 400 }}>({pct(c.count, answeredConf)}%)</span></b>
              </li>
            ))}
          </ul>
          <p className="note" style={{ marginTop: 8 }}>
            High from {th.high_from.toFixed(2)}, Medium from {th.medium_from.toFixed(2)}, refused below{" "}
            {th.relevance_threshold.toFixed(2)}. Calculations are exact, so they have no score.
          </p>
        </section>
      </div>
      <div className="g3" style={{ marginBottom: 16 }}>
        <section className="card">
          <div className="card-h"><div><h2>Document types</h2><p className="cs">{s.documents} documents in the knowledge base</p></div></div>
          <div className="hb">
            {s.document_types.map((t) => (
              <div key={t.name} className="hb-row">
                <span className="nm"><FileType t={t.name} /></span>
                <span className="hb-track"><i style={{ width: `${(t.count / tmax) * 100}%` }} /></span>
                <span className="n">{t.count}</span>
              </div>
            ))}
            {!s.document_types.length ? <p className="note">No documents yet.</p> : null}
          </div>
        </section>
        <section className="card">
          <div className="card-h"><div><h2>Most cited documents</h2><p className="cs">Answers that used each document, all time</p></div></div>
          <div className="hb">
            {s.most_cited.map((d) => (
              <div key={d.name} className="hb-row">
                <span className="nm" title={d.name}>{d.name}</span>
                <span className="hb-track"><i style={{ width: `${(d.count / cmax) * 100}%` }} /></span>
                <span className="n">{d.count}</span>
              </div>
            ))}
            {!s.most_cited.length ? <p className="note">No answers with citations yet.</p> : null}
          </div>
        </section>
        <section className="card">
          <div className="card-h"><div><h2>Questions it could not answer</h2><p className="cs">Gaps to fill with a new document</p></div></div>
          <ul className="lst">
            {s.gaps.map((g) => <li key={g.name}><span>{g.name}</span><span className="cnt">{g.count}×</span></li>)}
            {!s.gaps.length ? <li><span className="note">None in this period.</span></li> : null}
          </ul>
        </section>
      </div>
      <section className="card">
        <div className="card-h">
          <div><h2>System</h2><p className="cs">This machine · {s.total_ram_gb} GB RAM · CPU only</p></div>
          <a className="btn" href="#settings">Settings</a>
        </div>
        <div className="g3">
          <div>
            <Checks items={[
              { kind: h.ollama.ok ? "ok" : "bad", k: "Ollama", v: h.ollama.detail },
              { kind: h.answer_model.ok ? "ok" : "bad", k: "Answer model", v: h.answer_model.detail },
              { kind: h.embedding_model.ok ? "ok" : "bad", k: "Embeddings", v: h.embedding_model.detail },
              { kind: h.reranker.ok ? (h.reranker.detail.startsWith("disabled") ? "warn" : "ok") : "bad", k: "Reranker", v: h.reranker.detail },
            ]} />
          </div>
          <div>
            <div className="lbl">Peak memory in the last evaluation</div>
            {s.peak_ram_gb ? (
              <>
                <div className="ram"><i style={{ width: `${Math.min(100, (s.peak_ram_gb / s.total_ram_gb) * 100)}%` }} /></div>
                <p className="note">{s.peak_ram_gb} GB of {s.total_ram_gb} GB · {(s.total_ram_gb - s.peak_ram_gb).toFixed(1)} GB headroom</p>
              </>
            ) : <p className="note">Measured when the evaluation runs.</p>}
          </div>
          <div>
            <div className="lbl">Last evaluation</div>
            {s.last_eval ? (
              <>
                <p style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{String(s.last_eval.passed)} of {String(s.last_eval.total)} passed</p>
                <p className="note">{dateTxt(String(s.last_eval.created_at))} · <a href="#evaluation">See results</a></p>
              </>
            ) : <p className="note">Not run yet · <a href="#evaluation">Run it</a></p>}
          </div>
        </div>
      </section>
    </>
  );
}
