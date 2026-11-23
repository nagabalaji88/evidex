import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useApp } from "../app/context";
import { modelLabel } from "../app/format";
import { getJSON, sendJSON } from "../api/client";
import type { ActionResult, GeminiModelList, ModelList, SettingsResponse, SettingsSaved } from "../api/types";
import {
  GROUPS, LOCAL_ONLY, LOCAL_TOGGLE, PROVIDER_FIELDS, REINDEX, RISKY, TASK_FIELDS, getPath, validate, yamlSet, type Field, type Values,
} from "./settingsModel";

const FIELDS = [...PROVIDER_FIELDS, ...TASK_FIELDS, ...GROUPS.flatMap((g) => g.fields)];
type Opt = [string | number, string, boolean];

function initial(cfg: Record<string, unknown>): Values {
  return Object.fromEntries(FIELDS.map((f) => [f.key, getPath(cfg, f.key)]));
}

export default function Settings() {
  const { toast, reloadConfig, refreshHealth } = useApp();
  const [server, setServer] = useState<SettingsResponse | null>(null);
  const [saved, setSaved] = useState<Values>({});
  const [vals, setVals] = useState<Values>({});
  const [models, setModels] = useState<ModelList | null>(null);
  const [gemini, setGemini] = useState<GeminiModelList | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [needsReindex, setNeedsReindex] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadServer = useCallback(async () => {
    const s = await getJSON<SettingsResponse>("/api/settings");
    setServer(s);
    setSaved(initial(s.config));
    setVals(initial(s.config));
    return s;
  }, []);

  const loadGemini = useCallback(() => {
    sendJSON<GeminiModelList>("/api/gemini/models", "POST", {}).then(setGemini)
      .catch((e) => setGemini({ models: [], free_tier_only: true, paid_only: 0, error: (e as Error).message }));
  }, []);

  useEffect(() => {
    loadServer().then((s) => { if (s.gemini?.api_key_set) loadGemini(); }).catch((e) => setError((e as Error).message));
  }, [loadServer, loadGemini]);

  const local = vals[LOCAL_TOGGLE] === true;
  // The toggle lists local models only when it is on (fetched the first time it is).
  useEffect(() => {
    if (local && !models) getJSON<ModelList>("/api/models").then(setModels).catch(() => undefined);
  }, [local, models]);

  const envKeys = server?.env_overrides ?? [];
  const changed = useMemo(() => FIELDS.map((f) => f.key).filter((k) => String(vals[k]) !== String(saved[k])), [vals, saved]);
  const risky = changed.filter((k) => RISKY.includes(k));
  const problems = useMemo(() => (Object.keys(vals).length ? validate(vals) : []), [vals]);
  const yaml = useMemo(() => changed.reduce((t, k) => yamlSet(t, k, vals[k]), server?.yaml ?? ""), [changed, vals, server]);
  const keySet = !!server?.gemini?.api_key_set;

  const withCurrent = (list: Opt[], cur: string, note: string): Opt[] =>
    list.some((o) => o[0] === cur) || !cur ? list : [[cur, `${cur}${note}`, false], ...list];

  const options = (f: Field): Opt[] => {
    if (f.kind !== "select") return [];
    const cur = String(saved[f.key] ?? "");
    if (f.key.startsWith("gemini.")) {
      // Every model the key can use; paid-only ones are shown but cannot be picked while
      // gemini.free_tier_only is on (the server refuses them too).
      const kind = f.key === "gemini.embedding_model" ? "embedding" : "answer";
      const list = (gemini?.models ?? []).filter((m) => m.kind === kind)
        .map((m) => [m.name, `${m.display_name} (${m.name}) · ${m.free_tier ? "free tier" : "paid only"}`,
          !m.free_tier && !!gemini?.free_tier_only] as Opt);
      const isTask = TASK_FIELDS.some((x) => x.key === f.key);
      const withCur = withCurrent(list, cur, gemini?.models.length ? " (not offered to this key)" : "");
      return isTask ? [["", "Same as the answer model", false], ...withCur] : withCur;
    }
    if (f.key === "models.answer") {
      const list = (models?.models ?? []).filter((m) => !m.is_embedding)
        .map((m) => [m.name, `${modelLabel(m.name)}${m.tested ? " (tested)" : m.fits_in_ram ? " (not evaluated)" : " (too large)"}`, !m.fits_in_ram] as Opt);
      return list.some((o) => o[0] === cur) || !cur ? list : [[cur, `${modelLabel(cur)} (tested)`, false], ...list];
    }
    const base = f.options.map(([v, l]) => [v, l, false] as Opt);
    const v = vals[f.key];
    return base.some((o) => String(o[0]) === String(v)) ? base : [[v as string, String(v), false], ...base];
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
      await loadServer();
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

  async function saveKey() {
    setBusy(true);
    setError(null);
    try {
      const r = await sendJSON<ActionResult>("/api/gemini/key", "PUT", { api_key: key.trim() });
      setKey("");
      await loadServer();
      loadGemini();
      reloadConfig();
      refreshHealth();
      toast(r.message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removeKey() {
    setBusy(true);
    setError(null);
    try {
      const r = await sendJSON<ActionResult>("/api/gemini/key", "DELETE");
      await loadServer();
      setGemini(null);
      refreshHealth();
      toast(r.message);
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

  function row(f: Field, extra?: ReactNode) {
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
    const fromEnv = envKeys.includes(f.key);
    return (
      <div key={f.key} className="srow">
        <div>
          <label htmlFor={id}><b>{f.label}{changed.includes(f.key) ? <span className="chg">Changed</span> : null}
            {fromEnv ? <span className="chg" title="Set in .env, which wins over config.yaml">Set in .env</span> : null}</b></label>
          <small>{f.help}</small>
          {extra}
        </div>
        <fieldset disabled={fromEnv} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>{ctl}</fieldset>
      </div>
    );
  }

  if (!server) return <section className="card">{error ? <p className="callout bad">{error}</p> : <div className="empty-card"><b>Loading…</b></div>}</section>;

  const [toggle, answerField, embedField] = PROVIDER_FIELDS;
  const geminiNote = gemini?.error ? <small className="note" role="alert">{gemini.error}</small>
    : !keySet ? <small className="note">Save an API key to list the models.</small>
    : gemini ? <small className="note">{gemini.models.filter((m) => m.kind === "answer").length} models available to this key
      {gemini.paid_only && gemini.free_tier_only ? `; ${gemini.paid_only} paid-only, shown greyed out` : ""}.</small> : null;

  return (
    <>
      <div className="ph"><div><h1>Settings</h1></div></div>
      <div className="set">
        <div className="stack">
          <section className="card">
            <h2>AI models</h2>
            <div className="set-sec">
              {row(toggle)}
              {local ? (
                <p className="note" style={{ padding: "10px 0" }}>Local models are listed under Models below
                  {models?.error ? `. ${models.error}` : ""}.</p>
              ) : (
                <>
                  <div className="srow">
                    <div>
                      <label htmlFor="gemini_key"><b>Gemini API key</b></label>
                      <small>{keySet
                        ? `Saved${server.gemini?.api_key_source === ".env" ? " in .env on this computer" : ` in ${server.gemini?.api_key_source}`}. It is never shown again.`
                        : "Create one at aistudio.google.com/api-keys. It is checked with Google, then saved in .env on this computer."}</small>
                    </div>
                    <div className="keyrow">
                      <input type="password" id="gemini_key" autoComplete="off" spellCheck={false} value={key}
                        placeholder={keySet ? "Saved. Paste a new key to replace it" : "Paste your API key"}
                        onChange={(e) => { setError(null); setKey(e.target.value); }} />
                      <div className="btns">
                        <button type="button" className="btn primary" disabled={busy || !key.trim()} onClick={() => void saveKey()}>
                          {busy && key ? "Checking…" : "Save key"}</button>
                        {keySet ? <button type="button" className="btn" disabled={busy} onClick={() => void loadGemini()}>Reload models</button> : null}
                        {keySet && server.gemini?.api_key_source === ".env"
                          ? <button type="button" className="btn" disabled={busy} onClick={() => void removeKey()}>Remove</button> : null}
                      </div>
                    </div>
                  </div>
                  {row(answerField, geminiNote)}
                  {row(embedField)}
                  <h3 className="lbl" style={{ margin: "16px 0 0" }}>Models for specific tasks</h3>
                  {TASK_FIELDS.map((f) => row(f))}
                  <p className="callout info" style={{ margin: "10px 0 0" }}>
                    Paid-only models are listed but cannot be picked. The free tier applies when the key&apos;s Google Cloud project
                    has no billing account; with billing on, Google charges for every request. On the free tier,
                    Google may use the text sent (your questions and document passages) to improve its products.
                  </p>
                </>
              )}
            </div>
          </section>
          {GROUPS.map((g) => {
            const fields = g.fields.filter((f) => local || !LOCAL_ONLY.includes(f.key));
            return fields.length ? (
              <section key={g.title} className="card">
                <h2>{g.title}</h2>
                <div className="set-sec">{fields.map((f) => row(f))}</div>
              </section>
            ) : null;
          })}
        </div>
        <aside className="card sticky stack">
          <div><h2>config.yaml</h2><p className="cs">{changed.length ? `${changed.length} unsaved change${changed.length > 1 ? "s" : ""}` : "No unsaved changes"}</p></div>
          <pre style={{ maxHeight: 360, overflow: "auto" }}>{yaml}</pre>
          {server.llm ? (
            <div>
              <div className="lbl">Model servers</div>
              <ul className="lst">
                <li><span>Answers</span><b style={{ fontWeight: 500, textAlign: "right" }}>{server.llm.label}{server.llm.api_key_set ? " · key set" : ""}</b></li>
                <li><span>Embeddings</span><b style={{ fontWeight: 500, textAlign: "right" }}>{server.embed?.label}{server.embed?.api_key_set ? " · key set" : ""}</b></li>
              </ul>
              <p className="note">{server.gemini?.in_use
                ? "Switch to local models with the toggle above."
                : `${server.env_file ? "Set in .env. " : "From config.yaml. "}Local servers and their keys are changed in .env, then restart the API.`}</p>
            </div>
          ) : null}
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
