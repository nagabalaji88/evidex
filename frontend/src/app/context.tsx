import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { getJSON } from "../api/client";
import type { HealthResponse, SettingsResponse } from "../api/types";

export type AppConfig = SettingsResponse["config"] & {
  models?: { answer?: string; embedding?: string };
  retrieval?: { relevance_threshold?: number; vector_threshold?: number; final_k?: number };
  reranker?: { enabled?: boolean; model?: string };
  ui?: { default_theme?: string; departments?: string[] };
  ingestion?: { max_file_mb?: number; chunk_size_tokens?: number };
};

interface AppCtx {
  health: HealthResponse | null;
  healthError: string | null;
  refreshHealth: () => void;
  toast: (msg: string) => void;
  config: AppConfig | null;
  reloadConfig: () => void;
}

const Ctx = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const reloadConfig = useCallback(() => {
    getJSON<SettingsResponse>("/api/settings").then((s) => setConfig(s.config as AppConfig)).catch(() => undefined);
  }, []);
  useEffect(() => reloadConfig(), [reloadConfig]);

  const refreshHealth = useCallback(() => {
    getJSON<HealthResponse>("/api/health")
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch(() => setHealthError("The Evidex backend is not responding on port 8000."));
  }, []);

  useEffect(() => {
    refreshHealth();
    const id = window.setInterval(refreshHealth, 15000);
    return () => window.clearInterval(id);
  }, [refreshHealth]);

  const toast = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 2600);
  }, []);

  return (
    <Ctx.Provider value={{ health, healthError, refreshHealth, toast, config, reloadConfig }}>
      {children}
      <div className="toast" role="status" hidden={!msg}>{msg}</div>
    </Ctx.Provider>
  );
}

/** One line on what is wrong with the model runtime, or null when all is well. */
export function runtimeProblem(health: HealthResponse | null, healthError: string | null): string | null {
  if (healthError) return healthError;
  if (!health) return null;
  if (!health.ollama.ok) {
    return health.provider === "Ollama" ? "Ollama is not running. Start it with: ollama serve"
      : `${health.provider} is not reachable. Start the server or check EVIDEX_LLM_BASE_URL in .env`;
  }
  if (!health.answer_model.ok) return health.answer_model.detail;
  if (!health.embedding_model.ok) return health.embedding_model.detail;
  if (!health.reranker.ok) return health.reranker.detail;
  if (!health.vector_store.ok) return health.vector_store.detail;
  return null;
}
