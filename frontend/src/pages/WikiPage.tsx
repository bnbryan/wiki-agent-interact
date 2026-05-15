import { useCallback, useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  Category,
  TextCategory,
  WikiItem,
  deleteWiki,
  listWikis,
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

export default function WikiPage() {
  const [items, setItems] = useState<WikiItem[]>([]);
  const [textCategory, setTextCategory] = useState<TextCategory>("notes");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setItems(await listWikis());
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onDrop = useCallback(
    async (files: File[]) => {
      setBusy(true);
      setError(null);
      for (const f of files) {
        try {
          await uploadWiki(f, textCategory);
        } catch (e) {
          setError(`${f.name}: ${e}`);
        }
      }
      setBusy(false);
      refresh();
    },
    [refresh, textCategory],
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
            {items.map((w) => (
              <tr
                key={`${w.category}/${w.filename}`}
                className="border-t border-slate-100"
              >
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
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}
