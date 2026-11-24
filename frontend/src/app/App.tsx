import { useEffect, useRef, useState } from "react";
import { ErrorBoundary } from "../components/ErrorBoundary";
import Activity from "../pages/Activity";
import Chat from "../pages/Chat";
import Dashboard from "../pages/Dashboard";
import Evaluation from "../pages/Evaluation";
import KnowledgeBase from "../pages/KnowledgeBase";
import Settings from "../pages/Settings";
import Upload from "../pages/Upload";
import { AppProvider, runtimeProblem, useApp } from "./context";
import { load, modelLabel, save } from "./format";
import { IC } from "./icons";

export const PAGES = [
  { id: "dashboard", title: "Dashboard", sub: "Last 7 days" },
  { id: "upload", title: "Upload", sub: "Every upload is checked and versioned" },
  { id: "chat", title: "Chat workspace", sub: "Answers only from your documents" },
  { id: "kb", title: "Knowledge base", sub: "Every document, version and section" },
  { id: "activity", title: "Activity log", sub: "Every question and upload" },
  { id: "evaluation", title: "Evaluation", sub: "Proof the agent works" },
  { id: "settings", title: "Settings", sub: "Admin only · saved to config.yaml" },
] as const;
export type PageId = (typeof PAGES)[number]["id"];

function currentPage(): PageId {
  const id = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return (PAGES.find((p) => p.id === id)?.id ?? "dashboard") as PageId;
}

const X_ICON = (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
);

/** One menu button; on click it grows downward into a column of every page. */
function NavMenu({ page }: { page: PageId }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  useEffect(() => setOpen(false), [page]);
  return (
    <nav className="navm" aria-label="Main" ref={box}>
      <div className={`navm-pop${open ? " open" : ""}`}>
        <button type="button" className="navm-btn" ref={btn} aria-expanded={open} aria-controls="navm-list"
          aria-label={open ? "Close menu" : "Open menu"} title={open ? "Close menu" : "Menu"} onClick={() => setOpen(!open)}>
          {open ? X_ICON : IC.menu}
        </button>
        {open ? (
          <div className="navm-list" id="navm-list">
            {PAGES.map((p, i) => {
              const cur = page === p.id;
              return (
                <a key={p.id} className={`navm-i${cur ? " cur" : ""}`} href={`#${p.id}`} aria-current={cur ? "page" : undefined}
                  aria-label={p.title} style={{ animationDelay: `${60 + i * 35}ms` }} onClick={() => setOpen(false)}>
                  {IC[p.id]}<span className="navm-lb" aria-hidden="true">{p.title}</span>
                </a>
              );
            })}
            <div className="navm-who" style={{ animationDelay: `${60 + PAGES.length * 35}ms` }} title="Admin · Local · this machine">
              <span className="av">A</span>
              <span className="navm-lb" aria-hidden="true">Admin · Local</span>
            </div>
          </div>
        ) : null}
      </div>
    </nav>
  );
}

/** "Ollama", "Gemini", or the host of an OpenAI-compatible server ("localhost:1234"). */
function providerShort(p?: string): string {
  if (!p || p === "Ollama") return "Ollama";
  if (p === "Google Gemini API") return "Gemini";
  return p.replace(/^OpenAI-compatible · /, "");
}

function ModelChip() {
  const { health, healthError, config } = useApp();
  const problem = runtimeProblem(health, healthError);
  const name = config?.models?.answer ?? "";
  const label = name ? modelLabel(name) : "…";
  const cls = problem ? "chip bad" : health ? "chip" : "chip wait";
  return (
    <span className={cls} title={problem ?? `${health?.provider ?? "Ollama"} and both models are ready`} role="status">
      <i />{providerShort(health?.provider)} · {label}{problem ? ` · ${problem.length > 60 ? "unavailable" : problem}` : ""}
    </span>
  );
}

function Shell() {
  const [page, setPage] = useState<PageId>(currentPage());
  const [theme, setTheme] = useState(load("evidex-theme", ""));
  const { config } = useApp();

  useEffect(() => {
    const onHash = () => {
      setPage(currentPage());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      return;
    }
    // No saved choice yet: ui.default_theme from config.yaml (dark until it has loaded).
    document.documentElement.setAttribute("data-theme", config?.ui?.default_theme || "dark");
  }, [theme, config]);

  const effective = theme || config?.ui?.default_theme || "dark";
  const p = PAGES.find((x) => x.id === page)!;
  useEffect(() => {
    document.title = `${p.title} · Evidex`;
  }, [p.title]);

  const setMode = (t: string) => {
    setTheme(t);
    save("evidex-theme", t);
  };

  return (
    <div className="frame">
      <div className="main">
        <header className="hdr">
          <NavMenu page={page} />
          <div><div className="hk">Evidex · local agent</div><h1>{p.title}</h1></div>
          <span className="sp" />
          <span className="hsub">{p.sub}</span>
          <ModelChip />
          <div className="mode" role="group" aria-label="Colour mode">
            <button type="button" aria-pressed={effective === "light"} onClick={() => setMode("light")}>{IC.sun}Light</button>
            <button type="button" aria-pressed={effective === "dark"} onClick={() => setMode("dark")}>{IC.moon}Dark</button>
          </div>
        </header>
        <main className="page">
          {page === "dashboard" && <Dashboard />}
          {page === "upload" && <ErrorBoundary label="upload panel"><Upload /></ErrorBoundary>}
          {page === "chat" && <ErrorBoundary label="chat panel"><Chat /></ErrorBoundary>}
          {page === "kb" && <KnowledgeBase />}
          {page === "activity" && <Activity />}
          {page === "evaluation" && <Evaluation />}
          {page === "settings" && <Settings />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
