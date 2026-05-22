export type TextCategory = "articles" | "clippings" | "notes";
export type Category = TextCategory | "images" | "pdfs";

export interface WikiItem {
  category: Category;
  filename: string;
  original: string;
  size_bytes: number;
  uploaded_at: string;
}

export interface Session {
  id: string;
  title: string | null;
  created_at: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  created_at?: string;
}

export interface PermissionRequest {
  request_id: string;
  session_id: string;
  tool_name: string;
  tool_input: unknown;
  tool_use_id?: string | null;
  title?: string | null;
  display_name?: string | null;
  description?: string | null;
  blocked_path?: string | null;
  decision_reason?: string | null;
}

const USER_ID_STORAGE_KEY = "wiki-agent-user-id";
let cachedUserId: string | null = null;

export function getCurrentUserId(): string {
  if (cachedUserId) return cachedUserId;

  try {
    cachedUserId = window.localStorage.getItem(USER_ID_STORAGE_KEY);
  } catch {
    cachedUserId = null;
  }

  if (!cachedUserId) {
    cachedUserId = `web-${createId()}`;
    try {
      window.localStorage.setItem(USER_ID_STORAGE_KEY, cachedUserId);
    } catch {
      // Keep the in-memory id for this tab if localStorage is unavailable.
    }
  }

  return cachedUserId;
}

function createId(): string {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function chatHeaders(extra?: HeadersInit): HeadersInit {
  return {
    ...extra,
    "X-Wiki-User-Id": getCurrentUserId(),
  };
}

export async function listWikis(): Promise<WikiItem[]> {
  const r = await fetch("/api/wiki/list");
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function uploadWiki(
  file: File,
  category: TextCategory,
): Promise<WikiItem> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("category", category);
  const r = await fetch("/api/wiki/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteWiki(
  category: Category,
  filename: string,
): Promise<void> {
  const r = await fetch(
    `/api/wiki/${category}/${encodeURIComponent(filename)}`,
    { method: "DELETE" },
  );
  if (!r.ok) throw new Error(await r.text());
}

export async function listSessions(): Promise<Session[]> {
  const r = await fetch("/api/chat/sessions", { headers: chatHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getSessionMessages(id: string): Promise<Message[]> {
  const r = await fetch(`/api/chat/sessions/${id}/messages`, {
    headers: chatHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function deleteSession(id: string): Promise<void> {
  const r = await fetch(`/api/chat/sessions/${id}`, {
    method: "DELETE",
    headers: chatHeaders(),
  });
  if (!r.ok) throw new Error(await r.text());
}

export async function answerPermission(
  requestId: string,
  allow: boolean,
): Promise<void> {
  const r = await fetch(`/api/chat/permissions/${requestId}`, {
    method: "POST",
    headers: chatHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ allow }),
  });
  if (!r.ok) throw new Error(await r.text());
}

/** Stream a chat answer. Parses the FastAPI SSE response chunk-by-chunk. */
export async function streamAsk(
  body: { session_id: string | null; message: string },
  handlers: {
    onSession?: (id: string) => void;
    onDelta?: (text: string) => void;
    onPermission?: (request: PermissionRequest) => void;
    onError?: (msg: string) => void;
    onDone?: () => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch("/api/chat/ask", {
    method: "POST",
    headers: chatHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) {
    handlers.onError?.(await r.text().catch(() => `HTTP ${r.status}`));
    return;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line.
      let sep: number;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const evt = parseSseEvent(raw);
        if (!evt) continue;
        if (evt.event === "session") {
          handlers.onSession?.(JSON.parse(evt.data).session_id);
        } else if (evt.event === "delta") {
          handlers.onDelta?.(JSON.parse(evt.data).text);
        } else if (evt.event === "permission") {
          handlers.onPermission?.(JSON.parse(evt.data));
        } else if (evt.event === "error") {
          handlers.onError?.(JSON.parse(evt.data).error);
        } else if (evt.event === "done") {
          handlers.onDone?.();
        }
      }
    }
  } catch (e: unknown) {
    // User-initiated abort: stay silent (caller already knows).
    if (signal?.aborted) return;
    const msg = e instanceof Error ? e.message : String(e);
    handlers.onError?.(msg);
  }
}

function parseSseEvent(raw: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
