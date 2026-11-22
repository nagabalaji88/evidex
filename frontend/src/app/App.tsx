import { useEffect, useState } from "react";
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

function Rail({ page, open, toggle }: { page: PageId; open: boolean; toggle: () => void }) {
  return (
    <nav className={`rail${open ? " open" : ""}`} aria-label="Main">
      <div className="rail-top">
        <button type="button" className="rail-btn" aria-expanded={open} aria-label={open ? "Collapse menu" : "Expand menu"}
          title={open ? "Collapse menu" : "Expand menu"} onClick={toggle}>
          {IC.menu}
        </button>
        {open ? <span className="brand">Evidex</span> : null}
      </div>
      {PAGES.map((p) => {
        const cur = page === p.id;
        return (
          <a key={p.id} className={`ri${cur ? " cur" : ""}`} href={`#${p.id}`} aria-current={cur ? "page" : undefined}
            title={open ? undefined : p.title} aria-label={open ? undefined : p.title}>
            {IC[p.id]}
            {open ? <span className="ri-l">{p.title}</span> : null}
          </a>
        );
      })}
      <span className="rail-sp" />
      <div className="who">
        <span className="av">A</span>
        {open ? <span><b>Admin</b><small>Local · this machine</small></span> : null}
      </div>
    </nav>
  );
}

function ModelChip() {
  const { health, healthError, config } = useApp();
  const problem = runtimeProblem(health, healthError);
  const name = config?.models?.answer ?? "";
  const label = name ? modelLabel(name) : "…";
  const cls = problem ? "chip bad" : health ? "chip" : "chip wait";
  return (
    <span className={cls} title={problem ?? "Ollama and both models are ready"} role="status">
      <i />Ollama · {label}{problem ? ` · ${problem.length > 60 ? "unavailable" : problem}` : ""}
    </span>
  );
}

function Shell() {
  const [page, setPage] = useState<PageId>(currentPage());
  const [rail, setRail] = useState(load("evidex-rail", "closed") === "open");
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
  const toggleRail = () => {
    setRail((o) => {
      save("evidex-rail", o ? "closed" : "open");
      return !o;
    });
  };

  return (
    <div className="frame">
      <Rail page={page} open={rail} toggle={toggleRail} />
      <div className="main">
        <header className="hdr">
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
