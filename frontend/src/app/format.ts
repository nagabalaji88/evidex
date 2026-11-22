export const fmt = (n: number) => n.toLocaleString("en-IN");
export const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function dateTxt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export function timeTxt(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const day = Math.round((startOf(now) - startOf(d)) / 864e5);
  if (day <= 0) return `Today ${hm}`;
  if (day === 1) return `Yesterday ${hm}`;
  return `${d.toLocaleDateString("en-GB", { weekday: "short" })} ${d.getDate()} ${MONTHS[d.getMonth()]} ${hm}`;
}

function startOf(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function age(iso: string, now = new Date()): string {
  const days = Math.round((startOf(now) - startOf(new Date(iso))) / 864e5);
  if (Number.isNaN(days)) return iso;
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  return `${Math.round(days / 30)} months ago`;
}

export function size(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** "qwen2.5:7b-instruct" -> "Qwen 2.5 7B"; "llama3.1:8b" -> "Llama 3.1 8B". */
export function modelLabel(name: string): string {
  const [base, tag = ""] = name.split(":");
  const pretty = base
    .replace(/[-_]/g, " ")
    .replace(/([a-z])(\d)/gi, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const sizeTag = tag.match(/(\d+(?:\.\d+)?[bm])\b/i)?.[1]?.toUpperCase();
  return sizeTag ? `${pretty} ${sizeTag}` : pretty;
}

export const fileType = (name: string) => (name.split(".").pop() || "").toUpperCase();
export const stripExt = (name: string) => name.replace(/\.[a-z0-9]+$/i, "");

export function load(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

/** A session id generated on first visit, kept in localStorage (spec section 8.1). */
export function sessionId(): string {
  let id = load("evidex-session", "");
  if (!id) {
    id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID()
      : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    save("evidex-session", id);
  }
  return id;
}
