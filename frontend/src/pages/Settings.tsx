import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useApp } from "../app/context";
import { modelLabel } from "../app/format";
import { getJSON, sendJSON } from "../api/client";
import type { ActionResult, GeminiModelList, ModelList, SettingsResponse, SettingsSaved } from "../api/types";
import {
  GROUPS, LOCAL_ONLY, LOCAL_TOGGLE, PROVIDER_FIELDS, REINDEX, RISKY, TASK_FIELDS, getPath, validate, yamlSet, type Field, type Values,
} from "./settingsModel";

// The general Save covers the toggle and the groups below. The API connection (key, models
// URL, a model per task) has its own two OK buttons in the AI models card.
const FIELDS = [PROVIDER_FIELDS[0], ...GROUPS.flatMap((g) => g.fields)];
const PICK_FIELDS = [PROVIDER_FIELDS[1], PROVIDER_FIELDS[2], ...TASK_FIELDS];
type Picks = Record<string, string>;
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
  const [url, setUrl] = useState("");
  const [picks, setPicks] = useState<Picks>({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsReindex, setNeedsReindex] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadServer = useCallback(async () => {
    const s = await getJSON<SettingsResponse>("/api/settings");
    setServer(s);
    setSaved(initial(s.config));
    setVals(initial(s.config));
    setUrl(String(getPath(s.config, "gemini.models_url") ?? ""));
    setPicks(Object.fromEntries(PICK_FIELDS.map((f) => [f.key, String(getPath(s.config, f.key) ?? "")])));
    return s;
  }, []);

  /** First OK: list every model the key can use at the models URL (empty key: the saved one). */
  const listModels = useCallback(async (apiKey: string, modelsUrl: string) => {
    setConnected(false);
    try {
      const r = await sendJSON<GeminiModelList>("/api/gemini/models", "POST", { api_key: apiKey || null, models_url: modelsUrl || null });
      setGemini(r);
    } catch (e) {
      setGemini({ models: [], free_tier_only: true, paid_only: 0, error: (e as Error).message });
    }
  }, []);

  useEffect(() => {
    loadServer().then((s) => {
      // Already connected: show the saved models straight away.
      if (s.gemini?.api_key_set) void listModels("", String(getPath(s.config, "gemini.models_url") ?? ""));
    }).catch((e) => setError((e as Error).message));
  }, [loadServer, listModels]);

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

  /** Second OK: save the key, the URL and a model for each task together. */
  async function saveConnection() {
    setBusy(true);
    setError(null);
    try {
      const body = { api_key: key.trim() || null, models_url: url.trim(),
        ...Object.fromEntries(PICK_FIELDS.map((f) => [f.key.replace("gemini.", ""), picks[f.key] ?? ""])) };
      const r = await sendJSON<SettingsSaved>("/api/connection", "PUT", body);
      setKey("");
      await loadServer();
      setNeedsReindex(r.reindex_required);
      setConnected(true);
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
      setConnected(false);
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

  const toggle = PROVIDER_FIELDS[0];
  const found = gemini && !gemini.error ? gemini.models : null;
  const pickOptions = (f: Field): Opt[] => {
    // Every model the key can use. Google's paid-only models are shown but cannot be picked
    // while gemini.free_tier_only is on (the server refuses them too).
    const kind = f.key === "gemini.embedding_model" ? "embedding" : "answer";
    const list = (found ?? []).filter((m) => m.kind === kind).map((m) => [m.name,
      `${m.display_name !== m.name ? `${m.display_name} (${m.name})` : m.name}${m.free_tier === true ? " · free tier" : m.free_tier === false ? " · paid only" : ""}`,
      m.free_tier === false && !!gemini?.free_tier_only] as Opt);
    const isTask = TASK_FIELDS.some((x) => x.key === f.key);
    const cur = picks[f.key] ?? "";
    const withCur = withCurrent(list, cur, " (not offered to this key)");
    return isTask ? [["", "Same as the answer model", false], ...withCur] : [["", "Choose a model…", true], ...withCur];
  };
  const picksDone = !!picks["gemini.answer_model"] && !!picks["gemini.embedding_model"];
  const isGoogle = /(^|\.)googleapis\.com$/i.test((() => { try { return new URL(url).hostname; } catch { return ""; } })());
  const pickRow = (f: Field) => {
    const id = `cfg_${f.key.replace(".", "_")}`;
    return (
      <div key={f.key} className="srow">
        <div><label htmlFor={id}><b>{f.label}</b></label><small>{f.help}</small></div>
        <select id={id} value={picks[f.key] ?? ""} onChange={(e) => { setConnected(false); setPicks((x) => ({ ...x, [f.key]: e.target.value })); }}>
          {pickOptions(f).map(([ov, ol, dis]) => <option key={String(ov)} value={String(ov)} disabled={dis}>{ol}</option>)}
        </select>
      </div>
    );
  };

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
                  <h3 className="lbl step-h"><span className="stepn">1</span>Connect</h3>
                  <div className="srow">
                    <div>
                      <label htmlFor="api_key"><b>API key</b></label>
                      <small>{keySet
                        ? `Saved${server.gemini?.api_key_source === ".env" ? " in .env on this computer" : ` in ${server.gemini?.api_key_source}`}. Leave empty to keep it.`
                        : "For Google Gemini, create one at aistudio.google.com/api-keys. Saved in .env on this computer."}</small>
                    </div>
                    <input type="password" id="api_key" autoComplete="off" spellCheck={false} value={key}
                      placeholder={keySet ? "Saved · paste to replace" : "Paste your API key"}
                      onChange={(e) => { setError(null); setConnected(false); setKey(e.target.value); }} />
                  </div>
                  <div className="srow">
                    <div>
                      <label htmlFor="models_url"><b>Models URL</b></label>
                      <small>The address that lists the models for this key. Google Gemini:
                        generativelanguage.googleapis.com/v1beta/models. Other services: their …/v1/models.</small>
                    </div>
                    <input type="text" id="models_url" autoComplete="off" spellCheck={false} value={url}
                      onChange={(e) => { setError(null); setConnected(false); setUrl(e.target.value); }} />
                  </div>
                  <div className="okrow">
                    {gemini?.error ? <span className="note bad-t" role="alert">{gemini.error}</span>
                      : found ? <span className="note">{found.filter((m) => m.kind === "answer").length} answer and{" "}
                        {found.filter((m) => m.kind === "embedding").length} embedding models found
                        {gemini?.paid_only && gemini.free_tier_only ? ` · ${gemini.paid_only} paid-only, greyed out` : ""}.</span>
                        : <span className="note">Enter the key and URL, then click OK to list the models.</span>}
                    <button type="button" className="btn primary" disabled={busy || !url.trim() || (!key.trim() && !keySet)}
                      onClick={() => void listModels(key.trim(), url.trim())}>OK</button>
                  </div>
                  {found ? (
                    <>
                      <h3 className="lbl step-h"><span className="stepn">2</span>Choose a model for each task</h3>
                      {PICK_FIELDS.map(pickRow)}
                      <div className="okrow">
                        {connected ? <span className="note good-t">Saved. Next: <a href="#upload">upload your documents</a>, then ask your questions.</span>
                          : <span className="note">OK checks the models with the API and saves the key, URL and models.</span>}
                        <button type="button" className="btn primary" disabled={busy || !picksDone} onClick={() => void saveConnection()}>
                          {busy ? "Saving…" : "OK"}</button>
                      </div>
                    </>
                  ) : null}
                  {keySet && server.gemini?.api_key_source === ".env" ? (
                    <p className="note" style={{ marginTop: 6 }}>
                      <button type="button" className="linkbtn" disabled={busy} onClick={() => void removeKey()}>Remove the saved API key</button>
                    </p>
                  ) : null}
                  {isGoogle ? (
                    <p className="callout info" style={{ margin: "10px 0 0" }}>
                      Google&apos;s paid-only models are listed but cannot be picked. The free tier applies when the key&apos;s Google Cloud
                      project has no billing account; with billing on, Google charges for every request. On the free tier, Google may
                      use the text sent (your questions and document passages) to improve its products.
                    </p>
                  ) : null}
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
