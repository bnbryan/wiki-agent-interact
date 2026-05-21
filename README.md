# Wiki Agent — 交互层

本仓库**不包含** wiki 内容，也不定义 agent 行为。它只提供一个前后端界面，把上传操作和提问操作接到另一个**包含 CLAUDE.md + .claude/ 配置 + 实际 wiki 文档的仓库**上去。

## 架构

```
React 前端  ──HTTP/SSE──▶  FastAPI 后端  ──Claude Agent SDK──▶  Claude (claude-opus-4-7)
                                                                    ▲
                                                                    │ cwd
                                                  $WIKI_REPO_PATH (另一个仓库)
                                                  ├── CLAUDE.md
                                                  ├── .claude/                ← agent 行为定义
                                                  └── raw/                    ← 上传落地
                                                      ├── articles/   外部文章
                                                      ├── clippings/  网页剪藏
                                                      ├── notes/      原始笔记
                                                      ├── images/     图片
                                                      └── pdfs/       PDF
```

- **后端**: FastAPI + `claude-agent-sdk`
- **前端**: Vite + React + TS + Tailwind
- **持久化**: SQLite (会话历史) + 写文件到另一仓库 (wiki 内容)

agent 的 system prompt、可用工具、子 agent、skills 全部由 `$WIKI_REPO_PATH/CLAUDE.md` 和 `$WIKI_REPO_PATH/.claude/` 决定，本仓库**不覆盖**。SDK 启动时传 `setting_sources=["project"]` 让那个仓库的项目级配置生效。

## 前置条件

1. 已安装 [Claude Code CLI](https://docs.claude.com/en/docs/agents-and-tools/claude-code) (`npm install -g @anthropic-ai/claude-code`) 并完成 `claude login` —— SDK 底层会调它。
2. Python 3.10+, Node.js 18+。
3. 另一个 wiki 仓库已经存在，并且里面已经配置好了 agent (CLAUDE.md / .claude/)。

## 启动

```bash
# 必须先设置环境变量，指向另一个 wiki 仓库
export WIKI_REPO_PATH=/absolute/path/to/your/wiki-repo

# 后端 (端口 8000)
cd backend && ./run.sh

# 前端 (端口 5173)
cd frontend && npm install && npm run dev
```

打开 http://localhost:5173。

## 两个入口

- `/wiki`  上传文件 → 按规则写入 `$WIKI_REPO_PATH/raw/<category>/`：
  - 图片 (png/jpg/jpeg/gif/webp/svg) → `raw/images/`
  - PDF → `raw/pdfs/`
  - 文本 (md/txt/html/rst/org) → 由用户在 UI 上选 `articles` / `clippings` / `notes` 三者之一

  上传后是否要 git commit / push，由你或那个仓库的 hook 决定，本应用不做。
- `/chat`  对话框 → 启动一个 claude 进程，cwd 指向 `$WIKI_REPO_PATH`，流式返回。

## 数据库

仅本地一份 `data/app.db` (SQLite)，存：
- 上传文件元数据 (用于列表展示)
- 按浏览器用户隔离的对话会话与消息历史

聊天接口会读取 `X-Wiki-User-Id` 请求头。前端会在首次打开时自动生成并保存在浏览器 `localStorage`，因此不同浏览器/设备的会话列表彼此隔离。
