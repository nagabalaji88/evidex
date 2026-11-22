// The vertical steps panel ("Behind the scenes", upload processing), as in the reference.
import { useState, type ReactNode } from "react";
import { I } from "../app/icons";
import type { TraceStep } from "../api/types";

export type StepStatus = "pending" | "active" | "done" | "failed" | "blocked" | "skipped";

export interface UiStep {
  key: string;
  title: string;
  tech: string;
  status: StepStatus;
  summary?: string;
  body?: ReactNode;
  keepOpen?: boolean;
}

export function Checks({ items }: { items: { kind: "ok" | "bad" | "warn"; k: string; v: string }[] }) {
  return (
    <ul className="checks">
      {items.map((c, i) => (
        <li key={i} className={c.kind}>
          <span className="ic">{c.kind === "ok" ? I.ok : c.kind === "bad" ? I.x : I.bang}</span>
          <span className="k">{c.k}</span>
          <span>{c.v}</span>
        </li>
      ))}
    </ul>
  );
}

export function Trace({ steps, kind, done, empty }: {
  steps: UiStep[] | null;
  kind: "question" | "upload";
  done: boolean;
  empty: string;
}) {
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  if (!steps || !steps.length) {
    return <div className="tr-empty"><b>Nothing running yet</b>{empty}</div>;
  }
  const failed = steps.some((s) => s.status === "failed");
  const blocked = steps.some((s) => s.status === "blocked");
  return (
    <>
      <ol className="flow">
        {steps.map((st, i) => {
          const icon = st.status === "done" ? I.ok : st.status === "failed" ? I.x : st.status === "blocked" ? I.bang : null;
          let sum = st.summary ?? "";
          if (st.status === "active" && !sum) sum = "Working…";
          if (st.status === "skipped") {
            sum = kind === "question" ? "Not needed. The agent stopped before this step." : "Skipped. The file was stopped earlier.";
          }
          if (st.status === "pending") sum = "";
          const hasBody = !!st.body && st.status !== "pending" && st.status !== "skipped";
          const defaultOpen = st.status === "active" || st.status === "failed" || st.status === "blocked" || !!st.keepOpen;
          const open = hasBody && (toggled[st.key] ?? defaultOpen);
          return (
            <li key={st.key} className={`step ${st.status}`} data-i={i}>
              <span className="dot">{icon}</span>
              <button type="button" className="sh" aria-expanded={open} disabled={!hasBody}
                onClick={() => setToggled((t) => ({ ...t, [st.key]: !open }))}>
                <span className="tt">
                  <b>{st.title}</b>
                  <small>{st.tech}</small>
                  {sum ? <span className="sum">{sum}</span> : null}
                </span>
                {hasBody ? <span className="chev">{I.chev}</span> : null}
              </button>
              {open ? <div className="sb">{st.body}</div> : null}
            </li>
          );
        })}
      </ol>
      {done ? (
        <p className="tr-foot">
          {failed
            ? kind === "upload" ? "Stopped: the file was rejected and nothing was indexed." : "Stopped: an error ended this answer."
            : blocked
              ? kind === "question" ? "Stopped early: the answer is not in the selected documents." : "Stopped early: nothing needed to change."
              : "All steps finished."}{" "}
          Click any finished step to open or close it.
        </p>
      ) : null}
    </>
  );
}

// -- question steps: body for each step, from the stored step detail -------------------------

type Detail = Record<string, unknown>;
const num = (v: unknown) => (typeof v === "number" ? v : null);

function Ranking({ d, passed }: { d: Detail; passed: boolean }) {
  const th = num(d.threshold) ?? 0;
  const finalK = num(d.final_k) ?? 3;
  const scores = (d.scores as { label: string; score: number }[] | undefined) ?? [];
  return (
    <>
      <p>Each candidate is read together with the question and scored from 0 to 1.</p>
      <div className="rr">
        {scores.slice(0, 10).map((c, i) => {
          const cls = c.score < th ? "below" : passed && i < finalK ? "sent" : "";
          return (
            <div key={i} className={`rr-row ${cls}`}>
              <span className="nm" title={c.label}>{c.label}</span>
              <span className="bar">
                <span className="f" style={{ width: `${Math.round(Math.min(1, Math.max(0, c.score)) * 100)}%` }} />
                <span className="th" style={{ left: `${th * 100}%` }} />
              </span>
              <span className="sc">{c.score.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {passed ? <span><i style={{ background: "var(--accent)" }} />Top {finalK}, sent to the model</span> : null}
        <span><i style={{ background: "var(--dim)" }} />Below the threshold</span>
        <span>Dashed line = {th.toFixed(2)}, the minimum to answer</span>
      </div>
    </>
  );
}

type CheckRow = { name: string; passed: boolean; detail: string };

export function questionStepBody(s: TraceStep, all: TraceStep[]): ReactNode {
  const d = (s.detail ?? {}) as Detail;
  switch (s.title) {
    case "Using the conversation":
      return (
        <>
          <p>“{String(d.original ?? "")}” only makes sense with the earlier question, so it is rewritten before searching:</p>
          <p className="callout info">{String(d.rewritten ?? "")}</p>
        </>
      );
    case "Understanding the question":
      return (
        <Checks items={[
          { kind: "ok", k: "Type", v: String(d.type ?? "") },
          { kind: "ok", k: "Why", v: String(d.why ?? "") },
          { kind: "ok", k: "Route", v: String(d.route ?? "") },
        ]} />
      );
    case "Finding candidate passages": {
      const cands = (d.candidates as { doc_name: string; section: string | null; page: number | null; source_location: string; via: string }[]) ?? [];
      const excluded = (d.excluded_documents as string[]) ?? [];
      return (
        <>
          <p>
            Searched {String(d.searched_chunks ?? 0)} chunks in {String(d.searched_documents ?? 0)} selected documents, latest
            versions only.{excluded.length ? <> <b>{excluded.join(", ")}</b> {excluded.length === 1 ? "is" : "are"} not selected, so {excluded.length === 1 ? "it was" : "they were"} not searched.</> : null}
          </p>
          {cands.length ? (
            <ul className="cands">
              {cands.slice(0, 5).map((c, i) => (
                <li key={i}>
                  <span>{c.section || c.doc_name} · {c.page ? `p.${c.page}` : c.source_location}</span>
                  <span className="via">{c.via}</span>
                </li>
              ))}
              {cands.length > 5 ? <li><span className="muted">+ {cands.length - 5} more</span></li> : null}
            </ul>
          ) : null}
        </>
      );
    }
    case "Ranking by relevance": {
      const gate = all.find((x) => x.title === "Is the answer in the documents?");
      return <Ranking d={d} passed={gate?.status === "done"} />;
    }
    case "Is the answer in the documents?": {
      const best = num(d.best_score);
      return s.status === "done" ? (
        <p className="callout good">The best match scores {best?.toFixed(2)}, so the answer exists. The top passages go to the model.</p>
      ) : (
        <p className="callout warn">
          The best match scores only {best === null ? "nothing" : best.toFixed(2)}. The agent stops here and refuses. The AI
          model is never asked, so it has nothing to guess from.
        </p>
      );
    }
    case "Writing the answer":
      return (
        <>
          <p>The model sees only the selected passages, split into numbered sentences. It must tag each sentence it writes with its source.</p>
          <p className="lbl">What the model wrote{num(d.attempt) && num(d.attempt)! > 1 ? ` (attempt ${d.attempt})` : ""}</p>
          <pre>{String(d.raw ?? "")}</pre>
        </>
      );
    case "Checking the evidence":
    case "Checking the number":
    case "Safety checks": {
      const rows = (d.checks as CheckRow[]) ?? [];
      return (
        <>
          <Checks items={rows.map((c) => ({ kind: c.passed ? "ok" : "bad", k: c.name, v: c.detail }))} />
          {s.title === "Checking the evidence" ? <p>Any sentence that failed is removed before you see the answer.</p> : null}
          {d.failed_check ? <p className="callout bad">{s.summary}</p> : null}
        </>
      );
    }
    case "Picking the table": {
      const tables = (d.tables as { file: string; sheet: string | null; columns: string[]; matched: string[] }[]) ?? [];
      return (
        <>
          {tables.map((t, i) => (
            <div key={i}>
              <p>{t.file}{t.sheet ? ` · ${t.sheet}` : ""} has the columns the question needs.</p>
              <div className="cols">
                {t.columns.map((c) => <span key={c} className={t.matched.includes(c) ? "on" : ""}>{c}</span>)}
              </div>
            </div>
          ))}
        </>
      );
    }
    case "Writing the query":
      return (
        <>
          <p>The model gets the columns, 3 sample rows and 3 example queries, then writes:</p>
          <pre>{String(d.sql || d.raw || "")}</pre>
        </>
      );
    case "Running the query": {
      if (d.failed_check) return <p className="callout bad">{s.summary}</p>;
      return <ResultTable columns={(d.columns as string[]) ?? []} rows={(d.rows as unknown[][]) ?? []} />;
    }
    case "Answer ready": {
      const c = d.confidence as { level: string; score: number } | undefined;
      if (c) return <p className="callout good">{c.level} confidence · {c.score.toFixed(2)}</p>;
      if (s.summary?.startsWith("Exact")) {
        return <p className="callout good">Exact. This number was calculated from the spreadsheet, not written by the AI, so it has no confidence score.</p>;
      }
      return null;
    }
    default:
      return null;
  }
}

export function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const isNum = (v: unknown) => typeof v === "number";
  return (
    <div className="tw">
      <table>
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, 20).map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={isNum(v) ? "num" : ""}>{isNum(v) ? (v as number).toLocaleString("en-IN") : String(v ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Stored or streamed question steps -> panel steps. */
export function questionSteps(steps: TraceStep[], running?: { title: string } | null): UiStep[] {
  const out: UiStep[] = steps.map((s, i) => ({
    key: `${i}:${s.title}`,
    title: s.title,
    tech: s.tech,
    status: (s.status === "running" ? "active" : s.status) as StepStatus,
    summary: s.summary,
    body: questionStepBody(s, steps),
    keepOpen: ["Ranking by relevance", "Is the answer in the documents?", "Checking the evidence", "Answer ready",
      "Writing the query", "Safety checks", "Checking the number"].includes(s.title),
  }));
  if (running) out.push({ key: `run:${out.length}`, title: running.title, tech: "", status: "active" });
  return out;
}
