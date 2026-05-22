import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  Category,
  PermissionRequest,
  TextCategory,
  WikiItem,
  answerPermission,
  deleteWiki,
  listWikis,
  replyWikiIngest,
  streamWikiIngest,
  uploadWiki,
} from "../lib/api";

const TEXT_CATS: { value: TextCategory; label: string; hint: string }[] = [
  { value: "notes", label: "笔记", hint: "原始笔记" },
  { value: "articles", label: "文章", hint: "外部文章" },
  { value: "clippings", label: "剪藏", hint: "网页剪藏" },
];

const CATEGORY_LABEL: Record<Category, string> = {
  notes: "笔记",
  articles: "文章",
  clippings: "剪藏",
  images: "图片",
  pdfs: "PDF",
};

const CATEGORY_BADGE: Record<Category, string> = {
  notes: "bg-amber-50 text-amber-700",
  articles: "bg-sky-50 text-sky-700",
  clippings: "bg-violet-50 text-violet-700",
  images: "bg-emerald-50 text-emerald-700",
  pdfs: "bg-rose-50 text-rose-700",
};

type IngestStatus =
  | "idle"
  | "running"
  | "waiting"
  | "done"
  | "error"
  | "interrupted";

interface IngestState {
  status: IngestStatus;
  output: string;
  expanded: boolean;
  error: string | null;
  runId: string | null;
  permissions: PermissionRequest[];
}

export default function WikiPage() {
  const [items, setItems] = useState<WikiItem[]>([]);
  const [textCategory, setTextCategory] = useState<TextCategory>("notes");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ingests, setIngests] = useState<Record<string, IngestState>>({});
  const ingestControllers = useRef<Record<string, AbortController>>({});

  const refresh = useCallback(async () => {
    try {
      setItems(await listWikis());
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const startIngest = useCallback(async (category: Category, filename: string) => {
    const key = fileKey(category, filename);
    if (ingestControllers.current[key]) return;

    const ac = new AbortController();
    ingestControllers.current[key] = ac;
    setIngests((current) => ({
      ...current,
      [key]: {
        status: "running",
        output: "",
        expanded: true,
        error: null,
        runId: null,
        permissions: [],
      },
    }));

    await streamWikiIngest(
      { category, filename },
      {
        onRun: (runId) => {
          setIngests((current) => ({
            ...current,
            [key]: {
              ...(current[key] ?? emptyIngestState()),
              status: "running",
              runId,
              expanded: true,
            },
          }));
        },
        onWaiting: () => {
          setIngests((current) => ({
            ...current,
            [key]: {
              ...(current[key] ?? emptyIngestState()),
              status: "waiting",
              expanded: true,
            },
          }));
        },
        onDelta: (chunk) => {
          setIngests((current) => ({
            ...current,
            [key]: {
              ...(current[key] ?? emptyIngestState()),
              status: "running",
              output: (current[key]?.output ?? "") + chunk,
              expanded: true,
            },
          }));
        },
        onPermission: (request) => {
          setIngests((current) => {
            const state = current[key] ?? emptyIngestState();
            if (
              state.permissions.some(
                (item) => item.request_id === request.request_id,
              )
            ) {
              return current;
            }
            return {
              ...current,
              [key]: {
                ...state,
                status: "running",
                expanded: true,
                permissions: [...state.permissions, request],
              },
            };
          });
        },
        onError: (msg) => {
          setIngests((current) => ({
            ...current,
            [key]: {
              ...(current[key] ?? emptyIngestState()),
              status: "error",
              error: msg,
              expanded: true,
              runId: null,
              permissions: [],
            },
          }));
        },
        onDone: () => {
          setIngests((current) => ({
            ...current,
            [key]: {
              ...(current[key] ?? emptyIngestState()),
              status: "done",
              expanded: true,
              runId: null,
              permissions: [],
            },
          }));
        },
      },
      ac.signal,
    );

    delete ingestControllers.current[key];
    if (ac.signal.aborted) {
      setIngests((current) => ({
        ...current,
        [key]: {
          ...(current[key] ?? emptyIngestState()),
          status: "interrupted",
          expanded: true,
          runId: null,
          permissions: [],
        },
      }));
    }
  }, []);

  const toggleIngest = (category: Category, filename: string) => {
    const key = fileKey(category, filename);
    setIngests((current) => {
      const state = current[key] ?? emptyIngestState();
      return {
        ...current,
        [key]: { ...state, expanded: !state.expanded },
      };
    });
  };

  const respondToPermission = async (
    key: string,
    requestId: string,
    allow: boolean,
  ) => {
    try {
      await answerPermission(requestId, allow);
      setIngests((current) => {
        const state = current[key];
        if (!state) return current;
        return {
          ...current,
          [key]: {
            ...state,
            permissions: state.permissions.filter(
              (item) => item.request_id !== requestId,
            ),
          },
        };
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setIngests((current) => {
        const state = current[key] ?? emptyIngestState();
        return { ...current, [key]: { ...state, error: msg, expanded: true } };
      });
    }
  };

  const stopIngest = (category: Category, filename: string) => {
    ingestControllers.current[fileKey(category, filename)]?.abort();
  };

  const replyToIngest = async (key: string, message: string) => {
    const runId = ingests[key]?.runId;
    if (!runId) {
      setIngests((current) => {
        const state = current[key] ?? emptyIngestState();
        return {
          ...current,
          [key]: {
            ...state,
            error: "当前没有可回复的 ingest 进程",
            expanded: true,
          },
        };
      });
      return;
    }

    try {
      await replyWikiIngest(runId, message);
      setIngests((current) => {
        const state = current[key] ?? emptyIngestState();
        return {
          ...current,
          [key]: {
            ...state,
            output: `${state.output}\n\n> ${message}\n\n`,
            error: null,
            status: "running",
          },
        };
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setIngests((current) => {
        const state = current[key] ?? emptyIngestState();
        return { ...current, [key]: { ...state, error: msg, expanded: true } };
      });
    }
  };

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onDrop = useCallback(
    async (files: File[]) => {
      setBusy(true);
      setError(null);
      for (const f of files) {
        try {
          const uploaded = await uploadWiki(f, textCategory);
          void startIngest(uploaded.category, uploaded.filename);
        } catch (e) {
          setError(`${f.name}: ${e}`);
        }
      }
      setBusy(false);
      refresh();
    },
    [refresh, startIngest, textCategory],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/markdown": [".md", ".markdown"],
      "text/plain": [".txt", ".rst", ".org"],
      "text/html": [".html", ".htm"],
      "application/pdf": [".pdf"],
      "image/*": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
    },
  });

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 text-sm text-slate-600">
          文本文件类别（图片自动归入 <strong>images</strong>，PDF 自动归入{" "}
          <strong>pdfs</strong>）
        </div>
        <div className="flex gap-2">
          {TEXT_CATS.map((c) => (
            <button
              key={c.value}
              onClick={() => setTextCategory(c.value)}
              className={`rounded-md border px-3 py-1.5 text-sm transition ${
                textCategory === c.value
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"
              }`}
            >
              <span className="font-medium">{c.label}</span>
              <span className="ml-1 text-xs opacity-70">/ {c.hint}</span>
            </button>
          ))}
        </div>
      </div>

      <div
        {...getRootProps()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed py-10 transition ${
          isDragActive
            ? "border-slate-900 bg-slate-100"
            : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <input {...getInputProps()} />
        <div className="text-base font-medium">
          {isDragActive ? "松开以上传" : "拖拽文件到此处，或点击选择"}
        </div>
        <div className="mt-1 text-sm text-slate-500">
          支持 md / txt / html / rst / org / pdf / 常见图片格式，单文件最大 30MB
        </div>
        <div className="mt-1 text-xs text-slate-400">
          将写入 <code>$WIKI_REPO_PATH/raw/&lt;category&gt;/</code>
        </div>
        {busy && <div className="mt-2 text-sm text-slate-500">上传中…</div>}
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 text-left text-slate-600">
            <tr>
              <th className="px-4 py-2 font-medium">分类</th>
              <th className="px-4 py-2 font-medium">文件名</th>
              <th className="px-4 py-2 font-medium">大小</th>
              <th className="px-4 py-2 font-medium">上传时间</th>
              <th className="px-4 py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  还没有文件，先上传一份吧
                </td>
              </tr>
            )}
            {items.map((w) => {
              const key = fileKey(w.category, w.filename);
              const ingest = ingests[key];
              return (
                <Fragment key={key}>
                  <tr className="border-t border-slate-100">
                    <td className="px-4 py-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs ${CATEGORY_BADGE[w.category]}`}
                      >
                        {CATEGORY_LABEL[w.category]}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">{w.filename}</td>
                    <td className="px-4 py-2 text-slate-500">
                      {formatBytes(w.size_bytes)}
                    </td>
                    <td className="px-4 py-2 text-slate-500">{w.uploaded_at}</td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex justify-end gap-3">
                        <button
                          className="text-xs text-slate-700 hover:underline"
                          onClick={() => toggleIngest(w.category, w.filename)}
                        >
                          {ingest?.expanded ? "收起 ingest" : "查看 ingest"}
                        </button>
                        <button
                          className="text-xs text-red-600 hover:underline"
                          onClick={async () => {
                            if (!confirm(`删除 ${w.category}/${w.filename}?`)) return;
                            await deleteWiki(w.category, w.filename);
                            refresh();
                          }}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                  {ingest?.expanded && (
                    <tr className="ingest-expand-row border-t border-slate-100 bg-slate-50">
                      <td colSpan={5} className="p-0">
                        <div className="ingest-drawer">
                          <div className="ingest-drawer-inner px-4 py-3">
                            <IngestPanel
                              category={w.category}
                              filename={w.filename}
                              state={ingest}
                              onRun={() => void startIngest(w.category, w.filename)}
                              onStop={() => stopIngest(w.category, w.filename)}
                              onAnswer={(requestId, allow) =>
                                respondToPermission(key, requestId, allow)
                              }
                              onReply={(message) => replyToIngest(key, message)}
                            />
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function IngestPanel({
  category,
  filename,
  state,
  onRun,
  onStop,
  onAnswer,
  onReply,
}: {
  category: Category;
  filename: string;
  state: IngestState;
  onRun: () => void;
  onStop: () => void;
  onAnswer: (requestId: string, allow: boolean) => void;
  onReply: (message: string) => Promise<void>;
}) {
  const canRun = state.status !== "running" && state.status !== "waiting";
  const canReply = state.status === "running" || state.status === "waiting";
  const [reply, setReply] = useState("");
  const [replying, setReplying] = useState(false);

  const sendReply = async (message: string) => {
    const text = message.trim();
    if (!text || !canReply || replying) return;
    setReplying(true);
    try {
      await onReply(text);
      setReply("");
    } finally {
      setReplying(false);
    }
  };

  return (
    <div className="ingest-expand-panel rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-900">
            ingest:{" "}
            <span className="font-mono text-xs">
              {category}/{filename}
            </span>
          </div>
          <div className="mt-1 text-xs text-slate-500">
            状态: {statusLabel(state.status)}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          {canReply && (
            <button
              className="rounded-md border border-red-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
              onClick={onStop}
            >
              停止
            </button>
          )}
          <button
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-700 disabled:opacity-40"
            onClick={onRun}
            disabled={!canRun}
          >
            {state.output || state.error ? "重新执行" : "执行 ingest"}
          </button>
        </div>
      </div>

      {state.permissions.length > 0 && (
        <div className="mt-3 space-y-2">
          {state.permissions.map((request) => (
            <div
              key={request.request_id}
              className="rounded-md border border-amber-200 bg-amber-50 p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-900">
                    {request.title ||
                      request.description ||
                      request.display_name ||
                      "Claude 请求使用工具"}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    <span className="rounded bg-white px-1.5 py-0.5 font-mono text-slate-700">
                      {request.tool_name}
                    </span>
                    {request.blocked_path && (
                      <span className="ml-2">路径: {request.blocked_path}</span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                    onClick={() => onAnswer(request.request_id, false)}
                  >
                    拒绝
                  </button>
                  <button
                    className="rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white hover:bg-slate-700"
                    onClick={() => onAnswer(request.request_id, true)}
                  >
                    允许
                  </button>
                </div>
              </div>
              <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
                {JSON.stringify(request.tool_input, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}

      {state.error && (
        <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
          {state.error}
        </div>
      )}

      <pre className="mt-3 max-h-72 min-h-24 overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
        {state.output || (state.status === "running" ? "等待输出…" : "暂无输出")}
      </pre>

      {canReply && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
          <div className="flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void sendReply(reply);
                }
              }}
              placeholder="回复 agent，例如：继续 / 停止 / 只处理前 10 条"
              disabled={replying}
            />
            <button
              className="rounded-md bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-40"
              onClick={() => void sendReply(reply)}
              disabled={!reply.trim() || replying}
            >
              回复
            </button>
          </div>
          <div className="mt-2 flex gap-2">
            <button
              className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
              onClick={() => void sendReply("继续")}
              disabled={replying}
            >
              继续
            </button>
            <button
              className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
              onClick={() => void sendReply("停止")}
              disabled={replying}
            >
              停止
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fileKey(category: Category, filename: string): string {
  return `${category}/${filename}`;
}

function emptyIngestState(): IngestState {
  return {
    status: "idle",
    output: "",
    expanded: false,
    error: null,
    runId: null,
    permissions: [],
  };
}

function statusLabel(status: IngestStatus): string {
  if (status === "running") return "执行中";
  if (status === "waiting") return "等待回复";
  if (status === "done") return "完成";
  if (status === "error") return "出错";
  if (status === "interrupted") return "已中断";
  return "未执行";
}
