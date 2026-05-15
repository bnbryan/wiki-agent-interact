import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Message,
  Session,
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
  const scrollRef = useRef<HTMLDivElement>(null);

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

    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);

    await streamAsk(
      { session_id: currentId, message: text },
      {
        onSession: (id) => {
          if (!currentId) {
            setCurrentId(id);
            refreshSessions();
          }
        },
        onDelta: (chunk) => {
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
        onError: (err) => {
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
    );

    setStreaming(false);
    refreshSessions();
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

        <div className="border-t border-slate-100 p-3">
          <div className="flex items-end gap-2">
            <textarea
              className="flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
              rows={2}
              placeholder="按 Enter 发送，Shift+Enter 换行"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              disabled={streaming}
            />
            <button
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
              onClick={send}
              disabled={streaming || !input.trim()}
            >
              {streaming ? "回答中…" : "发送"}
            </button>
          </div>
        </div>
      </section>
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
        ) : (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {msg.content || "…"}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
