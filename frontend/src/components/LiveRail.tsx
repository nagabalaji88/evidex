// The "Behind the scenes" step rail: step icons in a straight line, joined by arcs that swing
// right then left. Steps are replayed in slow motion on their own clock, so the animation never
// holds back the answer: the chat shows the answer the moment it arrives, while this rail catches up.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AnswerPayload, TraceStep } from "../api/types";
import { runningTitle, type ChatMessage } from "../pages/chatState";
import { summaryOf } from "./AnswerCard";

/** Slow-motion pacing of the rail (display only; never delays the answer). */
const STEP_MS = 1100;
const ARC_MS = 1000;

function Ic({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
  );
}

const ICONS: Record<string, string> = {
  "Using the conversation": "M20 12a7.5 7.5 0 0 1-11 6.6L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12z",
  "Understanding the question": "M9.2 9a3 3 0 1 1 4.3 2.7c-.9.5-1.5 1.2-1.5 2.3M12 17.5v.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  "Finding candidate passages": "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM20 20l-4.8-4.8",
  "Ranking by relevance": "M4 6h16M4 12h11M4 18h6",
  "Is the answer in the documents?": "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 14l2 2 4-4",
  "Writing the answer": "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4",
  "Checking the evidence": "M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6zM9 12l2 2 4-4",
  "Answer ready": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8.5 12l2.5 2.5 4.5-5",
  "Picking the table": "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14",
  "Writing the query": "M8 7l-5 5 5 5M16 7l5 5-5 5",
  "Safety checks": "M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z",
  "Running the query": "M7 4.5v15l12-7.5z",
  "Checking the number": "M5 9h14M5 15h14M10 4l-2 16M16 4l-2 16",
  Stopped: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9 9l6 6M15 9l-6 6",
  "Small talk": "M20 12a7.5 7.5 0 0 1-11 6.6L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12z",
};
const DOT = "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z";

interface Item {
  title: string;
  status: string; // done | blocked | failed | running
  summary: string;
}

/** Steps revealed so far, plus a glowing "running" step while the answer is still being worked out. */
export function railItems(m: ChatMessage, shown: number): { items: Item[]; cur: number } {
  const real = m.steps.filter((s) => s.status !== "skipped" && s.status !== "pending");
  const items: Item[] = real.slice(0, shown).map((s) => ({ title: s.title, status: s.status, summary: s.summary }));
  if (shown >= real.length && m.status === "working") {
    items.push({ title: runningTitle(m), status: "running", summary: "" });
    return { items, cur: items.length - 1 };
  }
  if (shown >= real.length && (m.status === "error" || m.status === "lost")) {
    items.push({ title: "Stopped", status: "failed",
      summary: m.status === "lost" ? "Connection lost" : (m.error?.message ?? "Something went wrong") });
    return { items, cur: -1 };
  }
  // Still replaying steps that already finished: the newest revealed one glows.
  return { items, cur: shown < real.length ? items.length - 1 : -1 };
}

function stepFor(m: ChatMessage, shown: number, titles: string[]): TraceStep | undefined {
  const real = m.steps.filter((s) => s.status !== "skipped" && s.status !== "pending").slice(0, shown);
  return real.find((s) => titles.includes(s.title));
}

interface Score {
  ring: number; // 0..1, 0 = empty
  text: string; // inside the ring
  label: string;
  tone: "wait" | "prov" | "good" | "mid" | "low" | "none";
  checks: string;
  checksTone: "wait" | "prov" | "good" | "bad";
  sources: string;
}

/** The score panel, filled in only as the replay passes the step that produced each number. */
export function railScore(m: ChatMessage, shown: number): Score {
  const s: Score = { ring: 0, text: "—", label: "Waiting", tone: "wait", checks: "—", checksTone: "wait", sources: "—" };
  const gate = stepFor(m, shown, ["Is the answer in the documents?"]);
  const best = gate?.detail?.best_score;
  if (typeof best === "number") {
    s.ring = Math.max(0, Math.min(1, best));
    s.text = best.toFixed(2).replace(/^0/, "");
    s.label = gate?.status === "done" ? "Relevance" : "Below threshold";
    s.tone = "prov";
  }
  const chk = stepFor(m, shown, ["Checking the evidence", "Safety checks", "Checking the number"]);
  const list = chk?.detail?.checks;
  if (Array.isArray(list) && list.length) {
    const passed = list.filter((c) => (c as { passed?: boolean }).passed).length;
    s.checks = `${passed}/${list.length}`;
    s.checksTone = passed === list.length ? "good" : "bad";
  } else if (chk) {
    s.checksTone = chk.status === "done" ? "prov" : "bad";
  }
  const ready = stepFor(m, shown, ["Answer ready", "Small talk"]);
  const a = m.answer as AnswerPayload | undefined;
  if (ready && a && m.status === "complete") {
    const sum = summaryOf(a);
    s.sources = a.kind === "chat" ? "—" : String(sum.sources);
    if (sum.checks && s.checks === "—") {
      s.checks = `${sum.passed}/${sum.checks}`;
      s.checksTone = sum.passed === sum.checks ? "good" : "bad";
    }
    if (a.kind === "chat") Object.assign(s, { ring: 0, text: "—", label: "Small talk", tone: "none" });
    else if (a.kind === "refusal") Object.assign(s, { ring: 0, text: "—", label: "Not found", tone: "none" });
    else if (a.kind === "calculation") Object.assign(s, { ring: 1, text: "=", label: "Exact", tone: "good" });
    else if (a.confidence) {
      const sc = a.confidence.score ?? 0;
      const k = sum.conf?.k;
      Object.assign(s, { ring: sc, text: Math.round(sc * 100).toString(), label: `${a.confidence.level} confidence`,
        tone: k === "high" ? "good" : k === "med" ? "mid" : "low" });
    }
  }
  return s;
}

function Arc({ d, settled }: { d: string; settled: boolean }) {
  const head = useRef<SVGAnimateMotionElement>(null);
  useEffect(() => {
    const el = head.current as (SVGAnimateMotionElement & { beginElement?: () => void }) | null;
    if (!settled && el && typeof el.beginElement === "function") el.beginElement();
  }, [settled]);
  return (
    <g className={`rr-arc${settled ? " settled" : ""}`} style={{ ["--arc-ms" as string]: `${ARC_MS}ms` }}>
      <path className="rr-draw" d={d} pathLength={100} />
      <path className="rr-flow" d={d} />
      <g className="rr-packets">
        {[0, 0.8].map((b) => (
          <circle key={b} r="2.4"><animateMotion dur="1.6s" begin={`${b}s`} repeatCount="indefinite" path={d} /></circle>
        ))}
      </g>
      <circle className="rr-head" r="3.2" cx="0" cy="0">
        <animateMotion ref={head} dur={`${ARC_MS}ms`} begin="indefinite" fill="freeze" path={d} />
      </circle>
    </g>
  );
}

function Ring({ v, text, tone, size }: { v: number; text: string; tone: string; size: number }) {
  const r = size / 2 - 3;
  return (
    <svg className={`rr-ring ${tone}`} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle className="bg" cx={size / 2} cy={size / 2} r={r} />
      <circle className="fg" cx={size / 2} cy={size / 2} r={r} pathLength={100} strokeDasharray={`${v * 100} 100`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="53%" textAnchor="middle" dominantBaseline="middle">{text}</text>
    </svg>
  );
}

// Replay progress per question, so collapsing or opening the panel carries on where it was.
const replay = new Map<string, { animate: boolean; shown: number }>();

export function LiveRail({ rid, m, open, onDetails, detailsOn }: {
  rid: string;
  m: ChatMessage;
  open: boolean;
  onDetails?: () => void;
  detailsOn?: boolean;
}) {
  const real = m.steps.filter((s) => s.status !== "skipped" && s.status !== "pending").length;
  // Only a question that is running when the rail first sees it is replayed; older ones show at once.
  if (!replay.has(rid)) replay.set(rid, { animate: m.status === "working", shown: 0 });
  const rec = replay.get(rid)!;
  const [shown, setShown] = useState(rec.animate ? rec.shown : real);
  const atMount = useRef(rec.animate ? rec.shown : Number.MAX_SAFE_INTEGER);
  useEffect(() => {
    rec.shown = shown;
    if (!rec.animate) {
      if (shown !== real) setShown(real);
      return;
    }
    if (shown >= real) return;
    const t = setTimeout(() => setShown((n) => n + 1), shown === 0 ? 150 : STEP_MS);
    return () => clearTimeout(t);
  }, [rec, shown, real]);

  const { items, cur } = railItems(m, shown);
  const score = railScore(m, shown);
  const live = cur >= 0;
  const x = open ? 30 : 40;
  const top = 18;
  const gap = open ? 46 : 40;
  const h = top + Math.max(0, items.length - 1) * gap + 22;

  const arcs: ReactNode[] = [];
  for (let i = 0; i + 1 < items.length; i++) {
    const y1 = top + i * gap, y2 = y1 + gap, dir = i % 2 === 0 ? 1 : -1;
    const ex = x + dir * 13, cx = ex + dir * 20, my = (y1 + y2) / 2;
    arcs.push(<Arc key={i} settled={i + 1 < atMount.current} d={`M${ex} ${y1 + 4} Q${cx} ${my} ${ex} ${y2 - 4}`} />);
  }

  return (
    <div className={`rr${open ? " open" : ""}${live ? " live" : ""}`}>
      <div className="rr-track" style={{ height: h }}>
        <svg className="rr-svg" width="100%" height={h} aria-hidden="true">
          {items.slice(1).map((_, i) => {
            const y1 = top + i * gap, y2 = y1 + gap, dir = i % 2 === 0 ? 1 : -1;
            const ex = x + dir * 13, cx = ex + dir * 20;
            return <path key={i} className="rr-guide" d={`M${ex} ${y1 + 4} Q${cx} ${(y1 + y2) / 2} ${ex} ${y2 - 4}`} />;
          })}
          {arcs}
        </svg>
        <ol className="rr-steps">
          {items.map((it, i) => {
            const st = i === cur ? "cur" : it.status === "done" ? "done" : it.status === "running" ? "cur" : "bad";
            return (
              <li key={i} className={`rr-step ${st}`} style={{ top: top + i * gap - 13, left: x - 13 }}
                title={open ? undefined : `${it.title}${it.summary ? `: ${it.summary}` : ""}`}>
                <span className="rr-ic"><Ic d={ICONS[it.title] ?? DOT} /></span>
                {open ? (
                  <span className="rr-lab">
                    <b>{it.title}</b>
                    <small>{it.status === "running" ? <span className="rr-blink">Working…</span> : it.summary}</small>
                  </span>
                ) : <span className="sr">{it.title}</span>}
              </li>
            );
          })}
        </ol>
      </div>
      <div className="rr-foot" aria-live="polite">
        <Ring v={score.ring} text={score.text} tone={score.tone} size={open ? 40 : 36} />
        <div className="rr-meta">
          <b className={`rr-lbl ${score.tone}`}>{open ? score.label : score.label.split(" ")[0]}</b>
          <span className={`rr-chk ${score.checksTone}`} title="Checks passed">
            <Ic d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6zM9 12l2 2 4-4" />{score.checks}
          </span>
          {open ? <small>{score.sources === "—" ? "Sources —" : `${score.sources} source${score.sources === "1" ? "" : "s"}`}</small> : null}
        </div>
        {open && onDetails ? (
          <button type="button" className="linkbtn rr-more" onClick={onDetails} aria-expanded={!!detailsOn}>
            {detailsOn ? "Hide details" : "Evidence and every step"} ›
          </button>
        ) : null}
      </div>
    </div>
  );
}
