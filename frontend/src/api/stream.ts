// Server-Sent Events read with fetch + a stream reader (spec section 8.1). Not EventSource:
// it only supports GET, so it cannot send the question or the file.
import type { StreamEvent } from "./types";

export class ConnectionLost extends Error {
  constructor() {
    super("Connection lost. Retry?");
  }
}

/** Parse complete SSE frames from a buffer; returns [events, rest of the buffer]. */
export function parseFrames(buffer: string): [StreamEvent[], string] {
  const events: StreamEvent[] = [];
  const normalised = buffer.replace(/\r\n/g, "\n");
  const frames = normalised.split("\n\n");
  const rest = frames.pop() ?? "";
  for (const frame of frames) {
    let name = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) continue;
    try {
      events.push({ event: name, data: JSON.parse(data.join("\n")) } as StreamEvent);
    } catch {
      /* ignore a malformed frame */
    }
  }
  return [events, rest];
}

/**
 * POST and read the event stream. Calls onEvent for each event. Resolves when the `done`
 * event arrives; rejects with ConnectionLost if the stream ends without it. Never retries.
 */
export async function postStream(
  url: string,
  body: BodyInit,
  onEvent: (e: StreamEvent) => void,
  opts: { json?: boolean; signal?: AbortSignal } = {},
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      body,
      headers: opts.json ? { "Content-Type": "application/json", Accept: "text/event-stream" } : { Accept: "text/event-stream" },
      signal: opts.signal,
    });
  } catch {
    throw new ConnectionLost();
  }
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    onEvent({ event: "error", data: { code: `http_${res.status}`, message: msg } });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = false;
  try {
    for (;;) {
      const { value, done: end } = await reader.read();
      if (end) break;
      buffer += decoder.decode(value, { stream: true });
      const [events, rest] = parseFrames(buffer);
      buffer = rest;
      for (const e of events) {
        onEvent(e);
        if (e.event === "done") done = true;
      }
    }
    buffer += decoder.decode();
    const [events] = parseFrames(buffer + "\n\n");
    for (const e of events) {
      onEvent(e);
      if (e.event === "done") done = true;
    }
  } catch {
    if (!done) throw new ConnectionLost();
  }
  if (!done) throw new ConnectionLost();
}
