import { useEffect, useMemo, useState } from "react";
import { useApp } from "../app/context";
import { modelLabel } from "../app/format";
import { getJSON, sendJSON } from "../api/client";
import type { ActionResult, ModelList, SettingsResponse, SettingsSaved } from "../api/types";
import { GROUPS, REINDEX, RISKY, getPath, validate, yamlSet, type Field, type Values } from "./settingsModel";

const FIELDS = GROUPS.flatMap((g) => g.fields);

function initial(cfg: Record<string, unknown>): Values {
  return Object.fromEntries(FIELDS.map((f) => [f.key, getPath(cfg, f.key)]));
}

export default function Settings() {
  const { toast, reloadConfig, refreshHealth } = useApp();
  const [server, setServer] = useState<SettingsResponse | null>(null);
  const [saved, setSaved] = useState<Values>({});
  const [vals, setVals] = useState<Values>({});
  const [models, setModels] = useState<ModelList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsReindex, setNeedsReindex] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getJSON<SettingsResponse>("/api/settings").then((s) => {
      setServer(s);
      setSaved(initial(s.config));
      setVals(initial(s.config));
    }).catch((e) => setError((e as Error).message));
    getJSON<ModelList>("/api/models").then(setModels).catch(() => undefined);
  }, []);

  const changed = useMemo(() => FIELDS.map((f) => f.key).filter((k) => String(vals[k]) !== String(saved[k])), [vals, saved]);
  const risky = changed.filter((k) => RISKY.includes(k));
  const problems = useMemo(() => (Object.keys(vals).length ? validate(vals) : []), [vals]);
  const yaml = useMemo(() => changed.reduce((t, k) => yamlSet(t, k, vals[k]), server?.yaml ?? ""), [changed, vals, server]);

  const options = (f: Field): [string | number, string, boolean][] => {
    if (f.kind !== "select") return [];
    if (f.key === "models.answer") {
      const list = (models?.models ?? []).filter((m) => !m.is_embedding)
        .map((m) => [m.name, `${modelLabel(m.name)}${m.tested ? " (tested)" : m.fits_in_ram ? " (not evaluated)" : " (too large)"}`, !m.fits_in_ram] as [string, string, boolean]);
      const cur = String(saved[f.key] ?? "");
      return list.some((o) => o[0] === cur) || !cur ? list : [[cur, `${modelLabel(cur)} (tested)`, false], ...list];
    }
    const base = f.options.map(([v, l]) => [v, l, false] as [string | number, string, boolean]);
    const cur = vals[f.key];
    return base.some((o) => String(o[0]) === String(cur)) ? base : [[cur as string, String(cur), false], ...base];
  };

  const set = (k: string, v: unknown) => {
    setError(null);
    setVals((x) => ({ ...x, [k]: v }));
  };

  async function onSave() {
    setBusy(true);
    setError(null);
    try {
      const r = await sendJSON<SettingsSaved>("/api/settings", "PUT", { changes: Object.fromEntries(changed.map((k) => [k, vals[k]])) });
      const s = await getJSON<SettingsResponse>("/api/settings");
      setServer(s);
      setSaved(initial(s.config));
      setVals(initial(s.config));
      setNeedsReindex(r.reindex_required);
      reloadConfig();
      refreshHealth();
      toast(r.rerun_evaluation ? "Saved to config.yaml. Run the evaluation before trusting new answers." : "Saved to config.yaml");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reindexAll() {
    setBusy(true);
    try {
      const r = await sendJSON<ActionResult>("/api/reindex", "POST");
      toast(r.message);
      setNeedsReindex(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!server) return <section className="card">{error ? <p className="callout bad">{error}</p> : <div className="empty-card"><b>Loading…</b></div>}</section>;

  return (
    <>
      <div className="ph"><div><h1>Settings</h1></div></div>
      <div className="set">
        <div className="stack">
          {GROUPS.map((g) => (
            <section key={g.title} className="card">
              <h2>{g.title}</h2>
              <div className="set-sec">
                {g.fields.map((f) => {
                  const id = `cfg_${f.key.replace(".", "_")}`;
                  const v = vals[f.key];
                  let ctl;
                  if (f.kind === "select") {
                    ctl = (
                      <select id={id} value={String(v ?? "")}
                        onChange={(e) => set(f.key, /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value)}>
                        {options(f).map(([ov, ol, dis]) => <option key={String(ov)} value={String(ov)} disabled={dis}>{ol}</option>)}
                      </select>
                    );
                  } else if (f.kind === "switch") {
                    ctl = <label className="switch"><input type="checkbox" id={id} checked={!!v} onChange={(e) => set(f.key, e.target.checked)} /><span>{v ? "On" : "Off"}</span></label>;
                  } else if (f.kind === "range") {
                    ctl = (
                      <div className="range">
                        <input type="range" id={id} min={f.min} max={f.max} step={f.step} value={Number(v ?? 0)}
                          onChange={(e) => set(f.key, Number(e.target.value))} />
                        <output>{Number(v ?? 0).toFixed(2)}</output>
                      </div>
                    );
                  } else {
                    ctl = <input type="number" id={id} value={v === undefined ? "" : String(v)}
                      onChange={(e) => set(f.key, e.target.value === "" ? "" : Number(e.target.value))} />;
                  }
                  return (
                    <div key={f.key} className="srow">
                      <div>
                        <label htmlFor={id}><b>{f.label}{changed.includes(f.key) ? <span className="chg">Changed</span> : null}</b></label>
                        <small>{f.help}</small>
                      </div>
                      <div>{ctl}</div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
        <aside className="card sticky stack">
          <div><h2>config.yaml</h2><p className="cs">{changed.length ? `${changed.length} unsaved change${changed.length > 1 ? "s" : ""}` : "No unsaved changes"}</p></div>
          <pre style={{ maxHeight: 360, overflow: "auto" }}>{yaml}</pre>
          {problems.length ? <div className="callout bad" role="alert">{problems.map((p) => <div key={p}>{p}</div>)}</div> : null}
          {error ? <div className="callout bad" role="alert">Not saved: {error}</div> : null}
          {risky.length ? (
            <p className="callout warn">You changed {risky.map((k) => FIELDS.find((f) => f.key === k)?.label).join(", ")}. Re-run the evaluation after saving, so you know answers and refusals are still correct.</p>
          ) : null}
          {changed.some((k) => REINDEX.includes(k)) ? (
            <p className="callout info">Embedding or chunk changes need a re-index. You can start it right after saving.</p>
          ) : null}
          {needsReindex ? (
            <p className="callout warn">Saved. Re-index every document now so search uses the new settings.{" "}
              <button type="button" className="btn" disabled={busy} onClick={() => void reindexAll()}>{busy ? "Re-indexing…" : "Re-index now"}</button></p>
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn primary" disabled={!changed.length || busy || problems.length > 0} onClick={() => void onSave()}>Save</button>
            <button type="button" className="btn" disabled={!changed.length || busy} onClick={() => { setVals(saved); setError(null); }}>Discard changes</button>
            <a className="btn" href="#evaluation">Evaluation</a>
          </div>
        </aside>
      </div>
    </>
  );
}
