import { useEffect, useState } from "react";
import { useApp } from "../app/context";
import { dateTxt, modelLabel } from "../app/format";
import { ApiError, getJSON } from "../api/client";
import { ConnectionLost, postStream } from "../api/stream";
import type { EvalReport, EvalRun } from "../api/types";

type M = Record<string, unknown>;
const pctTxt = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "n/a");
const count = (run: EvalRun, cat: string, key: "passed" | "correct" | "citation_ok") => {
  const xs = run.cases.filter((c) => c.category === cat);
  return `${xs.filter((c) => !!c[key]).length} / ${xs.length}`;
};

const CATEGORY: Record<string, string> = {
  answerable: "Answerable questions", unanswerable: "Trick questions that must be refused", table: "Table calculations",
  version: "Version traps", injection: "Hidden-instruction documents", broken: "Broken files",
};

function rows(run: EvalRun) {
  const m = run.metrics as M;
  const met = (m.targets_met ?? {}) as Record<string, boolean | null>;
  return [
    ["Refused when not in documents", count(run, "unanswerable", "passed"), pctTxt(m.refusal_accuracy), "100%", met.refusal_accuracy],
    ["Correct answers", count(run, "answerable", "correct"), pctTxt(m.answer_correctness), "90% or more", met.answer_correctness],
    ["Correct citations", count(run, "answerable", "citation_ok"), pctTxt(m.citation_accuracy), "90% or more", met.citation_accuracy],
    ["Never used an old version", count(run, "version", "passed"), pctTxt(m.version_accuracy), "100%", met.version_accuracy],
    ["Exact table answers", count(run, "table", "passed"), pctTxt(m.table_exact), "100%", met.table_exact],
    ["Ignored hidden instructions", count(run, "injection", "passed"), pctTxt(m.injection_ok), "100%", met.injection_ok],
    ["Rejected broken files cleanly", count(run, "broken", "passed"), pctTxt(m.broken_ok), "100%", met.broken_ok],
    ["Search + ranking time", `avg ${m.avg_retrieval_s ?? "—"} s`, `slowest ${m.slowest_retrieval_s ?? "—"} s`, "under 5 s", met.avg_retrieval_s],
    ["Full answer time", `avg ${m.avg_answer_s ?? "—"} s`, `slowest ${m.slowest_answer_s ?? "—"} s`, "under 30 s", met.avg_answer_s],
    ["Peak memory", `${run.peak_ram_gb} GB`, "", "under 12 GB", run.peak_ram_gb < 12],
  ] as [string, string, string, string, boolean | null | undefined][];
}

export default function Evaluation() {
  const { toast } = useApp();
  const [rep, setRep] = useState<EvalReport | null>(null);
  const [none, setNone] = useState<string | null>(null);
  const [run, setRun] = useState<{ n: number; total: number; label: string; phase: string } | null>(null);
  const [compare, setCompare] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => getJSON<EvalReport>("/api/eval/latest").then((r) => { setRep(r); setNone(null); })
    .catch((e) => setNone(e instanceof ApiError && e.status === 404 ? e.message : (e as Error).message));
  useEffect(() => void load(), []);

  async function start() {
    setErr(null);
    setRun({ n: 0, total: 50, label: "Indexing the sample documents", phase: "" });
    try {
      await postStream("/api/eval/run", JSON.stringify({ compare }), (e) => {
        if (e.event === "progress") {
          const detail = e.data.detail ?? "";
          const cat = detail.split("·")[1]?.trim() ?? "";
          setRun((r) => ({ n: e.data.done, total: e.data.total, label: CATEGORY[cat] ?? cat, phase: r?.phase ?? "" }));
        } else if (e.event === "stage" && e.data.status === "running") {
          setRun((r) => ({ n: 0, total: r?.total ?? 50, label: "Indexing the sample documents", phase: e.data.detail }));
        } else if (e.event === "stage" && e.data.status === "done") {
          toast(`Evaluation finished: ${e.data.detail}`);
        } else if (e.event === "error") {
          setErr(e.data.message);
        }
      }, { json: true });
    } catch (e) {
      setErr(e instanceof ConnectionLost ? "Connection lost. The evaluation may still be running on the server; reload this page later." : (e as Error).message);
    }
    setRun(null);
    void load();
  }

  const main = rep?.main;
  const m = (main?.metrics ?? {}) as M;
  const met = Object.values((m.targets_met ?? {}) as Record<string, boolean | null>).filter((v) => v !== null);
  const failures = main?.cases.filter((c) => !c.passed) ?? [];
  const runs = rep ? [rep.main, ...rep.comparisons] : [];
  const sweep = rep?.sweep as M | null | undefined;

  return (
    <>
      <div className="ph">
        <div><h1>Evaluation</h1></div>
        <span style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label className="switch"><input type="checkbox" checked={compare} disabled={!!run} onChange={(e) => setCompare(e.target.checked)} />
            <span>Include comparison runs (about 4× longer)</span></label>
          <button type="button" className="btn primary" disabled={!!run} onClick={() => void start()}>{run ? "Running…" : "Run evaluation"}</button>
        </span>
      </div>
      {err ? <p className="banner bad" style={{ marginBottom: 16 }}>{err}</p> : null}
      {run ? (
        <section className="card" style={{ marginBottom: 16 }} aria-live="polite">
          <div className="card-h"><div><h2>{run.n ? `Running test ${run.n} of ${run.total}` : "Preparing"}</h2>
            <p className="cs">{run.label}{run.phase ? ` · ${run.phase}` : ""}</p></div></div>
          <div className="prog"><i style={{ width: `${(run.n / run.total) * 100}%` }} /></div>
        </section>
      ) : null}
      {!main ? (
        <section className="card"><div className="empty-card"><b>No evaluation yet</b>{none ?? "Loading…"} It runs the
          50 test questions in eval/testset.json against the sample documents in a separate, temporary store.</div></section>
      ) : (
        <>
          <div className="kpis" style={{ gridTemplateColumns: "repeat(3,minmax(0,1fr))" }}>
            <div className={`kpi ${m.passed === m.total ? "good" : "amber"}`}><div className="k">Result</div>
              <div className="v">{String(m.passed)} / {String(m.total)}</div><div className="s">Last run {dateTxt(rep!.created_at)}</div></div>
            <div className={`kpi ${met.every(Boolean) ? "good" : "amber"}`}><div className="k">Targets met</div>
              <div className="v">{met.filter(Boolean).length} / {met.length}</div>
              <div className="s">{met.every(Boolean) ? "Every target in spec section 7 is met" : "Some targets are not met yet"}</div></div>
            <div className="kpi"><div className="k">Model under test</div>
              <div className="v" style={{ fontSize: 20 }}>{modelLabel(String(main.settings.answer_model))}</div>
              <div className="s">Reranker {main.settings.reranker ? "on" : "off"} · {String(main.settings.final_k)} passages · threshold {String(main.settings.relevance_threshold)}</div></div>
          </div>
          <div className="g2">
            <section className="card">
              <h2 style={{ marginBottom: 10 }}>Results against targets</h2>
              <div className="tw"><table>
                <thead><tr><th>Measure</th><th>Result</th><th>Target</th><th /></tr></thead>
                <tbody>
                  {rows(main).map(([name, res, sub, target, ok]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td><b>{res}</b>{sub ? <> <span className="note">{sub}</span></> : null}</td>
                      <td className="note">{target}</td>
                      <td><span className={`pill ${ok ? "pass" : ok === false ? "fail" : "inactive"}`}>{ok ? "Pass" : ok === false ? "Fail" : "n/a"}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </section>
            <div className="stack">
              <section className="card">
                <h2 style={{ marginBottom: 10 }}>Reranker on or off</h2>
                {runs.length > 1 ? (
                  <>
                    <div className="tw"><table>
                      <thead><tr><th>Setting</th><th className="num">Refusals</th><th className="num">Correct</th><th className="num">Avg time</th></tr></thead>
                      <tbody>
                        {runs.map((r) => {
                          const x = r.metrics as M;
                          const kept = rep!.cheapest_passing && (rep!.cheapest_passing as M).label === r.label;
                          return (
                            <tr key={r.label}>
                              <td><b>{r.settings.reranker ? "On" : "Off"}</b> · {String(r.settings.final_k)} passages {kept ? <span className="pill pass">Cheapest passing</span> : null}</td>
                              <td className="num" style={x.refusal_accuracy !== 1 ? { color: "var(--bad)" } : undefined}>{pctTxt(x.refusal_accuracy)}</td>
                              <td className="num">{pctTxt(x.answer_correctness)}</td>
                              <td className="num">{String(x.avg_answer_s ?? "—")} s</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table></div>
                    <p className="note" style={{ marginTop: 8 }}>Keep the cheapest setting that still refuses every unanswerable question, and record it in config.yaml.</p>
                  </>
                ) : <p className="note">Tick “Include comparison runs” to compare the reranker on and off, and 3 against 5 passages.</p>}
              </section>
              {sweep ? (
                <section className="card">
                  <h2 style={{ marginBottom: 10 }}>Refusal threshold</h2>
                  <p style={{ fontSize: 13.5 }}>
                    {sweep.recommended !== null && sweep.recommended !== undefined
                      ? <>Recommended <b>{String(sweep.recommended)}</b>: refuses every unanswerable question at the gate and blocks {String(sweep.recommended_blocks)} answerable.</>
                      : "No threshold refuses every unanswerable question at the gate."}
                  </p>
                  <p className="note" style={{ marginTop: 6 }}>Current {String(sweep.current)} · highest unanswerable score {String(sweep.max_unanswerable_score ?? "—")} · lowest answerable score {String(sweep.min_answerable_score ?? "—")}. Record it with eval/run_eval.py --write-config or in Settings.</p>
                </section>
              ) : null}
              <section className="card">
                <h2 style={{ marginBottom: 10 }}>{failures.length ? `The ${failures.length} failure${failures.length > 1 ? "s" : ""}` : "No failures"}</h2>
                {failures.map((c) => (
                  <p key={c.id} style={{ fontSize: 13.5, marginBottom: 8 }}><b>{c.id} · {c.category}.</b> “{c.question}” <span className="note">{c.reason}</span></p>
                ))}
                {!failures.length ? <p className="note">Every case passed.</p> : null}
              </section>
            </div>
          </div>
        </>
      )}
    </>
  );
}
