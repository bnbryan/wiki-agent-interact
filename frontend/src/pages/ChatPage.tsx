import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Message,
  PermissionRequest,
  Session,
  answerPermission,
  deleteSession,
  getSessionMessages,
  listSessions,
  streamAsk,
} from "../lib/api";

export default function ChatPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [permissionRequests, setPermissionRequests] = useState<
    PermissionRequest[]
  >([]);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Tracks the latest selected session so streaming callbacks can read it
  // without going stale, and so we can detect mid-stream session switches.
  const currentIdRef = useRef<string | null>(null);
  useEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);
  // Used to interrupt the in-flight SSE stream.
  const abortRef = useRef<AbortController | null>(null);
  const stop = () => {
    abortRef.current?.abort();
    setPermissionRequests([]);
  };

  const refreshSessions = useCallback(async () => {
    setSessions(await listSessions());
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    if (!currentId) {
      setMessages([]);
      return;
    }
    getSessionMessages(currentId).then(setMessages);
  }, [currentId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setStreaming(true);
    setPermissionRequests([]);
    setPermissionError(null);

    // The view this stream originated from. null = "new chat" view.
    // We deliberately do NOT change currentId during streaming — promoting it
    // here would trigger the useEffect that refetches messages from the DB
    // and wipe the assistant placeholder we just appended.
    const startedFromId = currentId;
    let streamSessionId: string | null = startedFromId;
    const isStillOnThisStream = () => currentIdRef.current === startedFromId;

    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);

    const ac = new AbortController();
    abortRef.current = ac;

    await streamAsk(
      { session_id: startedFromId, message: text },
      {
        onSession: (id) => {
          streamSessionId = id;
          // Refresh the sidebar so the new session chip shows up, but don't
          // switch currentId yet — the user is still on the originating view.
          if (startedFromId === null) refreshSessions();
        },
        onDelta: (chunk) => {
          if (!isStillOnThisStream()) return;
          setMessages((m) => {
            const next = m.slice();
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                content: last.content + chunk,
              };
            }
            return next;
          });
        },
        onPermission: (request) => {
          if (!isStillOnThisStream()) return;
          setPermissionError(null);
          setPermissionRequests((items) => {
            if (items.some((item) => item.request_id === request.request_id)) {
              return items;
            }
            return [...items, request];
          });
        },
        onError: (err) => {
          if (!isStillOnThisStream()) return;
          setPermissionRequests([]);
          setMessages((m) => {
            const next = m.slice();
            next[next.length - 1] = {
              role: "assistant",
              content: `⚠️ ${err}`,
            };
            return next;
          });
        },
      },
      ac.signal,
    );

    // If the user clicked 停止 mid-stream, mark the partial answer.
    if (ac.signal.aborted && isStillOnThisStream()) {
      setMessages((m) => {
        const next = m.slice();
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            content: (last.content || "") + "\n\n_[已中断]_",
          };
        }
        return next;
      });
    }
    abortRef.current = null;
    setPermissionRequests([]);

    // Stream done. If this was a new chat and the user is still sitting on the
    // new-chat view, bind currentId to the real session id now. The useEffect
    // will refetch from the DB — which now has both messages persisted, so
    // there's no flicker.
    if (
      startedFromId === null &&
      streamSessionId !== null &&
      currentIdRef.current === null
    ) {
      setCurrentId(streamSessionId);
    }
    setStreaming(false);
    refreshSessions();
  };

  const respondToPermission = async (requestId: string, allow: boolean) => {
    setPermissionError(null);
    try {
      await answerPermission(requestId, allow);
      setPermissionRequests((items) =>
        items.filter((item) => item.request_id !== requestId),
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setPermissionError(msg);
    }
  };

  return (
    <div className="flex h-full gap-4">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col rounded-xl border border-slate-200 bg-white">
        <button
          className="m-2 rounded-md bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700"
          onClick={() => {
            setCurrentId(null);
            setMessages([]);
          }}
        >
          + 新对话
        </button>
        <div className="flex-1 overflow-auto px-2 pb-2">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group mb-1 flex cursor-pointer items-center justify-between rounded-md px-2 py-1.5 text-sm ${
                s.id === currentId
                  ? "bg-slate-100"
                  : "hover:bg-slate-50"
              }`}
              onClick={() => setCurrentId(s.id)}
            >
              <div className="truncate">{s.title || "(未命名)"}</div>
              <button
                className="invisible text-xs text-red-500 group-hover:visible"
                onClick={async (e) => {
                  e.stopPropagation();
                  await deleteSession(s.id);
                  if (s.id === currentId) setCurrentId(null);
                  refreshSessions();
                }}
              >
                删
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* Chat panel */}
      <section className="flex flex-1 flex-col rounded-xl border border-slate-200 bg-white">
        <div ref={scrollRef} className="flex-1 overflow-auto px-6 py-4">
          {messages.length === 0 && (
            <div className="flex h-full items-center justify-center text-slate-400">
              问我任何关于已上传 wiki 的问题
            </div>
          )}
          {messages.map((m, i) => (
            <Bubble key={i} msg={m} />
          ))}
        </div>

        {permissionRequests.length > 0 && (
          <PermissionPanel
            requests={permissionRequests}
            error={permissionError}
            onAnswer={respondToPermission}
          />
        )}

        <div className="border-t border-slate-100 p-3">
          <div className="flex items-end gap-2">
            <textarea
              className="flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
              rows={2}
              placeholder="按 Enter 发送，Shift+Enter 换行"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send();
                }
              }}
              disabled={streaming}
            />
            {streaming ? (
              <button
                className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700"
                onClick={stop}
              >
                停止
              </button>
            ) : (
              <button
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
                onClick={send}
                disabled={!input.trim()}
              >
                发送
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function PermissionPanel({
  requests,
  error,
  onAnswer,
}: {
  requests: PermissionRequest[];
  error: string | null;
  onAnswer: (requestId: string, allow: boolean) => void;
}) {
  return (
    <div className="border-t border-amber-200 bg-amber-50 px-4 py-3">
      <div className="space-y-3">
        {requests.map((request) => (
          <div
            key={request.request_id}
            className="rounded-lg border border-amber-200 bg-white p-3 shadow-sm"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-900">
                  {request.title ||
                    request.description ||
                    request.display_name ||
                    "Claude 请求使用工具"}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-700">
                    {request.tool_name}
                  </span>
                  {request.blocked_path && (
                    <span className="truncate">路径: {request.blocked_path}</span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                  onClick={() => onAnswer(request.request_id, false)}
                >
                  拒绝
                </button>
                <button
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
                  onClick={() => onAnswer(request.request_id, true)}
                >
                  允许
                </button>
              </div>
            </div>
            <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
              {JSON.stringify(request.tool_input, null, 2)}
            </pre>
          </div>
        ))}
        {error && <div className="text-xs text-red-600">{error}</div>}
      </div>
    </div>
  );
}

function Bubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div className={`mb-4 flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm ${
          isUser
            ? "bg-slate-900 text-white"
            : "border border-slate-200 bg-slate-50 text-slate-900"
        }`}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{msg.content}</div>
        ) : msg.content ? (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {msg.content}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="typing-dots" aria-label="正在生成">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>
    </div>
  );
}
