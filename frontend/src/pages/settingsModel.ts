// Settings groups (spec section 8), mapped onto the real config.yaml keys.
export type Field =
  | { key: string; label: string; help: string; kind: "select"; options: [string | number, string][] }
  | { key: string; label: string; help: string; kind: "switch" }
  | { key: string; label: string; help: string; kind: "range"; min: number; max: number; step: number }
  | { key: string; label: string; help: string; kind: "number" };

// The "AI models" card: the local/Gemini toggle and the Gemini models (config.yaml provider.*, gemini.*).
export const LOCAL_TOGGLE = "provider.use_local_models";
export const PROVIDER_FIELDS: Field[] = [
  { key: LOCAL_TOGGLE, label: "Use local models", kind: "switch",
    help: "Off: the API connection below (Google Gemini by default). On: models on this computer (Ollama, or the server set in .env)." },
  { key: "gemini.answer_model", label: "Answer model", kind: "select", options: [],
    help: "Writes the answers, and every task below left on \"Same as the answer model\"." },
  { key: "gemini.embedding_model", label: "Embedding model", kind: "select", options: [],
    help: "Turns text into vectors for search. Changing it re-indexes every document." },
];
// Optional models for specific AI tasks (gemini.<task>_model); "" = the answer model does it.
export const TASK_FIELDS: Field[] = [
  { key: "gemini.rewrite_model", label: "Follow-up questions", kind: "select", options: [],
    help: "Rewrites a follow-up (\"and in Chennai?\") into a full question." },
  { key: "gemini.route_model", label: "Question routing", kind: "select", options: [],
    help: "Decides between searching the documents and calculating from a table." },
  { key: "gemini.sql_model", label: "Table calculations (SQL)", kind: "select", options: [],
    help: "Writes the read-only SQL for spreadsheet and CSV questions." },
  { key: "gemini.claim_model", label: "Claim check", kind: "select", options: [],
    help: "The extra check of each sentence, when Extra AI claim check is on." },
];
// Local-only settings, hidden while Gemini answers (Gemini uses gemini.max_output_tokens instead).
export const LOCAL_ONLY = ["models.answer", "models.embedding", "ollama.num_ctx", "ollama.num_predict", "ollama.keep_alive"];

export const GROUPS: { title: string; fields: Field[] }[] = [
  { title: "Models", fields: [
    { key: "models.answer", label: "Answer model", help: "Writes answers and SQL. Only tested models are recommended.", kind: "select", options: [] },
    { key: "models.embedding", label: "Embedding model", help: "Changing it re-indexes every document.", kind: "select",
      options: [["nomic-embed-text", "nomic-embed-text"], ["bge-m3", "bge-m3 (multilingual)"]] },
    { key: "reranker.enabled", label: "Reranker", help: "Re-scores search results with a local model on this computer. Needs backend\\requirements-local.txt.", kind: "switch" },
  ] },
  { title: "Answer generation", fields: [
    { key: "ollama.temperature", label: "Temperature", help: "Lower means more consistent, less creative answers.", kind: "range", min: 0, max: 1, step: 0.05 },
    { key: "ollama.num_ctx", label: "Context length", help: "How much text the model reads at once. Higher uses more RAM.", kind: "select",
      options: [[2048, "2,048 tokens"], [4096, "4,096 tokens"], [8192, "8,192 tokens"]] },
    { key: "ollama.num_predict", label: "Longest answer", help: "Maximum tokens the model may write.", kind: "number" },
    { key: "ollama.keep_alive", label: "Keep model loaded", help: "How long an idle model stays in memory.", kind: "select",
      options: [["1m", "1 minute"], ["5m", "5 minutes"], ["30m", "30 minutes"]] },
  ] },
  { title: "Search and confidence", fields: [
    { key: "retrieval.candidate_k", label: "Candidates to rank", help: "Passages found before ranking.", kind: "number" },
    { key: "retrieval.final_k", label: "Passages sent to the model", help: "Evaluation compares 3 and 5.", kind: "select", options: [[3, "3"], [5, "5"]] },
    { key: "retrieval.relevance_threshold", label: "Refuse below", help: "Reranker score under which the agent refuses.", kind: "range", min: 0.1, max: 0.6, step: 0.01 },
    { key: "retrieval.vector_threshold", label: "Refuse below (reranker off)", help: "Used only when the reranker is switched off.", kind: "range", min: 0.3, max: 0.8, step: 0.01 },
    { key: "confidence.high_from", label: "High confidence from", help: "Score shown as High on answers.", kind: "range", min: 0.5, max: 0.95, step: 0.01 },
    { key: "confidence.medium_from", label: "Medium confidence from", help: "Below this, answers show Low.", kind: "range", min: 0.3, max: 0.7, step: 0.01 },
    { key: "retrieval.history_turns", label: "Conversation memory", help: "Earlier turns used to understand follow-ups.", kind: "number" },
  ] },
  { title: "Documents", fields: [
    { key: "ingestion.chunk_size_tokens", label: "Chunk size", help: "Tokens per chunk. Changing it re-indexes every document.", kind: "number" },
    { key: "ingestion.chunk_overlap_tokens", label: "Chunk overlap", help: "Tokens shared between neighbouring chunks.", kind: "number" },
    { key: "ingestion.max_file_mb", label: "Largest upload", help: "In MB.", kind: "number" },
    { key: "ingestion.ocr_enabled", label: "Read scanned PDFs (OCR)", help: "Slow on CPU. Off rejects scanned files.", kind: "switch" },
    { key: "grounding.claim_check_enabled", label: "Extra AI claim check", help: "A second AI pass per answer. Adds 15 to 30 s.", kind: "switch" },
  ] },
];

// Spec section 8: these show "Re-run the evaluation after saving".
export const RISKY = [LOCAL_TOGGLE, "gemini.answer_model", "gemini.embedding_model", "gemini.rewrite_model",
  "gemini.route_model", "gemini.sql_model", "gemini.claim_model", "models.answer", "models.embedding", "reranker.enabled", "retrieval.relevance_threshold",
  "retrieval.vector_threshold", "retrieval.final_k", "ingestion.chunk_size_tokens", "ingestion.chunk_overlap_tokens",
  "ollama.num_ctx"];
export const REINDEX = [LOCAL_TOGGLE, "gemini.embedding_model", "models.embedding", "ingestion.chunk_size_tokens", "ingestion.chunk_overlap_tokens"];

export type Values = Record<string, unknown>;

export function getPath(cfg: Record<string, unknown>, dotted: string): unknown {
  const [s, k] = dotted.split(".");
  return (cfg[s] as Record<string, unknown> | undefined)?.[k];
}

export function yamlScalar(v: unknown): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return `[${v.map(yamlScalar).join(", ")}]`;
  const s = String(v);
  if (s === "" || /[:#[\]{},&*!|>'"%@`]|^\s|\s$/.test(s) || /^(true|false|null|yes|no)$/i.test(s)) {
    return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return s;
}

/** Replace `key: value` inside `section:` of config.yaml text, keeping comments. */
export function yamlSet(text: string, dotted: string, value: unknown): string {
  const [section, key] = dotted.split(".");
  const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(^${esc(section)}:\\n(?:[ \\t]+.*\\n|\\n)*?[ \\t]+${esc(key)}:[ \\t]*)((?:"[^"]*"|'[^']*'|\\[[^\\]]*\\]|[^#\\n])*?)([ \\t]*(#.*)?)$`, "m");
  return re.test(text) ? text.replace(re, (_m, a: string, _v: string, c: string) => a + yamlScalar(value) + c) : text;
}

/** Client-side checks with plain-language messages; the server validates again on save. */
export function validate(v: Values): string[] {
  const n = (k: string) => Number(v[k]);
  const errs: string[] = [];
  const int = (k: string, label: string, min: number, max = Infinity) => {
    if (!Number.isInteger(n(k)) || n(k) < min || n(k) > max) errs.push(`${label} must be a whole number${max < Infinity ? ` from ${min} to ${max}` : ` of at least ${min}`}.`);
  };
  int("ollama.num_predict", "Longest answer", 1);
  int("retrieval.candidate_k", "Candidates to rank", 1);
  int("retrieval.history_turns", "Conversation memory", 0);
  int("ingestion.chunk_size_tokens", "Chunk size", 1);
  int("ingestion.chunk_overlap_tokens", "Chunk overlap", 0);
  if (!(n("ingestion.max_file_mb") > 0)) errs.push("Largest upload must be more than 0 MB.");
  if (n("ingestion.chunk_overlap_tokens") >= n("ingestion.chunk_size_tokens")) errs.push("Chunk overlap must be smaller than the chunk size.");
  if (n("retrieval.final_k") > n("retrieval.candidate_k")) errs.push("Passages sent to the model cannot be more than the candidates to rank.");
  if (!(n("retrieval.relevance_threshold") <= n("confidence.medium_from") && n("confidence.medium_from") <= n("confidence.high_from"))) {
    errs.push("Thresholds must go up: Refuse below ≤ Medium from ≤ High from.");
  }
  return errs;
}
