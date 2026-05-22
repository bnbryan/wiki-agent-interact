import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent import ask
from ..config import (
    CATEGORIES,
    CATEGORY_EXTENSIONS,
    MAX_FILE_SIZE,
    TEXT_CATEGORIES,
    category_dir,
)
from ..db import get_db
from .chat import PendingPermission, _pending_permissions, get_user_id

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._一-鿿-]+")

_IMAGE_EXTS = CATEGORY_EXTENSIONS["images"]
_PDF_EXTS = CATEGORY_EXTENSIONS["pdfs"]


class IngestBody(BaseModel):
    category: str
    filename: str


class IngestReplyBody(BaseModel):
    message: str


@dataclass
class PendingIngestRun:
    user_id: str
    queue: asyncio.Queue[str | None]


_pending_ingest_runs: dict[str, PendingIngestRun] = {}

MAX_INGEST_REPLY_TURNS = 8
_CONFIRMATION_MARKERS = (
    "是否继续",
    "继续吗",
    "要继续",
    "是否要",
    "请确认",
    "确认后",
    "继续执行",
    "proceed",
    "continue?",
    "should i continue",
    "do you want",
)


def _safe_filename(name: str) -> tuple[str, str]:
    """Return (safe_filename, lowercased_ext_with_dot)."""
    name = Path(name).name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        raise HTTPException(400, "Filename must have an extension")
    ext_lower = f".{ext.lower()}"
    cleaned = _SAFE_NAME.sub("_", stem).strip("_") or "wiki"
    return f"{cleaned}{ext_lower}", ext_lower


def _looks_like_user_prompt(text: str) -> bool:
    tail = text.strip()[-600:].lower()
    if not tail:
        return False
    return tail.endswith(("?", "？")) or any(
        marker in tail for marker in _CONFIRMATION_MARKERS
    )


def _resolve_category(requested: str | None, ext: str) -> str:
    """Decide the final category.

    Images and PDFs are auto-routed by extension regardless of `requested`.
    Text uploads must declare one of TEXT_CATEGORIES.
    """
    if ext in _IMAGE_EXTS:
        return "images"
    if ext in _PDF_EXTS:
        return "pdfs"
    if requested not in TEXT_CATEGORIES:
        raise HTTPException(
            400,
            f"For text files you must select a category, one of {list(TEXT_CATEGORIES)}",
        )
    if ext not in CATEGORY_EXTENSIONS[requested]:
        raise HTTPException(
            400,
            f"Extension {ext} is not allowed for category '{requested}'. "
            f"Allowed: {sorted(CATEGORY_EXTENSIONS[requested])}",
        )
    return requested


@router.post("/upload")
async def upload_wiki(
    file: UploadFile = File(...),
    category: str | None = Form(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "Missing filename")

    safe, ext = _safe_filename(file.filename)
    final_cat = _resolve_category(category, ext)

    dst_dir = category_dir(final_cat)
    dst = dst_dir / safe
    if dst.exists():
        raise HTTPException(409, f"{final_cat}/{safe} already exists")

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE} bytes")

    dst.write_bytes(data)

    try:
        await db.execute(
            "INSERT INTO wikis (category, filename, original, size_bytes) VALUES (?, ?, ?, ?)",
            (final_cat, safe, file.filename, len(data)),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        # Race: another concurrent upload won.
        dst.unlink(missing_ok=True)
        raise HTTPException(409, f"{final_cat}/{safe} already exists")

    return {
        "filename": safe,
        "category": final_cat,
        "size": len(data),
    }


@router.post("/ingest")
async def ingest_wiki_stream(
    body: IngestBody,
    user_id: str = Depends(get_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    if body.category not in CATEGORIES:
        raise HTTPException(404, "Unknown category")

    safe = Path(body.filename).name
    if safe != body.filename:
        raise HTTPException(400, "Invalid filename")

    cur = await db.execute(
        "SELECT 1 FROM wikis WHERE category = ? AND filename = ?",
        (body.category, safe),
    )
    if await cur.fetchone() is None:
        raise HTTPException(404, "Wiki file not found")

    rel_path = f"raw/{body.category}/{safe}"
    initial_prompt = _ingest_prompt(rel_path)
    run_id = str(uuid.uuid4())
    reply_queue: asyncio.Queue[str | None] = asyncio.Queue()
    _pending_ingest_runs[run_id] = PendingIngestRun(
        user_id=user_id,
        queue=reply_queue,
    )

    async def event_stream():
        interrupted = False
        error_msg: str | None = None
        pending_for_stream: set[str] = set()
        events: asyncio.Queue[dict] = asyncio.Queue()

        async def request_permission(payload: dict) -> bool:
            request_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            pending_for_stream.add(request_id)
            _pending_permissions[request_id] = PendingPermission(
                session_id=f"ingest:{body.category}/{safe}",
                user_id=user_id,
                future=future,
            )
            await events.put(
                {
                    "type": "permission",
                    "data": {
                        "request_id": request_id,
                        "session_id": f"ingest:{body.category}/{safe}",
                        **jsonable_encoder(payload),
                    },
                }
            )
            try:
                return await future
            finally:
                pending_for_stream.discard(request_id)
                _pending_permissions.pop(request_id, None)

        try:
            payload = json.dumps({"run_id": run_id}, ensure_ascii=False)
            yield f"event: run\ndata: {payload}\n\n"

            prompt = initial_prompt
            for turn_index in range(MAX_INGEST_REPLY_TURNS + 1):
                turn_chunks: list[str] = []

                async def run_agent() -> None:
                    try:
                        async for chunk in ask(
                            prompt,
                            history=None,
                            permission_handler=request_permission,
                        ):
                            await events.put({"type": "delta", "text": chunk})
                    except Exception as exc:
                        await events.put({"type": "error", "error": str(exc)})
                    finally:
                        await events.put({"type": "done"})

                agent_task = asyncio.create_task(run_agent())
                try:
                    while True:
                        event = await events.get()
                        event_type = event["type"]
                        if event_type == "delta":
                            chunk = event["text"]
                            turn_chunks.append(chunk)
                            payload = json.dumps({"text": chunk}, ensure_ascii=False)
                            yield f"event: delta\ndata: {payload}\n\n"
                        elif event_type == "permission":
                            payload = json.dumps(event["data"], ensure_ascii=False)
                            yield f"event: permission\ndata: {payload}\n\n"
                        elif event_type == "error":
                            error_msg = event["error"]
                            break
                        elif event_type == "done":
                            break
                finally:
                    if not agent_task.done():
                        agent_task.cancel()

                if error_msg:
                    break

                turn_text = "".join(turn_chunks)
                if not _looks_like_user_prompt(turn_text):
                    break
                if turn_index >= MAX_INGEST_REPLY_TURNS:
                    error_msg = "Ingest reached the maximum number of reply turns."
                    break

                payload = json.dumps({}, ensure_ascii=False)
                yield f"event: waiting\ndata: {payload}\n\n"
                reply = await reply_queue.get()
                if reply is None:
                    interrupted = True
                    break
                if reply.strip().lower() in {"stop", "cancel", "停止", "取消"}:
                    break
                prompt = _ingest_followup_prompt(rel_path, reply)
        except asyncio.CancelledError:
            interrupted = True
        except Exception as exc:
            error_msg = str(exc)
            print(f"[ERROR] Ingest exception: {exc}", flush=True)
        finally:
            _pending_ingest_runs.pop(run_id, None)
            await reply_queue.put(None)
            for request_id in list(pending_for_stream):
                pending = _pending_permissions.pop(request_id, None)
                if pending and not pending.future.done():
                    pending.future.set_result(False)

        if interrupted:
            return
        if error_msg:
            payload = json.dumps({"error": error_msg}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"
            return

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ingest/{run_id}/reply")
async def reply_to_ingest(
    run_id: str,
    body: IngestReplyBody,
    user_id: str = Depends(get_user_id),
):
    pending = _pending_ingest_runs.get(run_id)
    if pending is None or pending.user_id != user_id:
        raise HTTPException(404, "Ingest run not found")

    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message")

    await pending.queue.put(message)
    return {"ok": True}


def _ingest_prompt(rel_path: str) -> str:
    return (
        f"/ingest {rel_path}\n\n"
        "请只处理这个刚上传的文件，并把执行过程和结果简洁输出。"
        "如果需要使用工具或修改文件，请按项目规则请求确认。"
        "如果你需要用户确认是否继续，请直接提出明确问题。"
    )


def _ingest_followup_prompt(rel_path: str, reply: str) -> str:
    return (
        f"继续执行 /ingest {rel_path}。\n\n"
        f"用户对你上一轮确认问题的回复是：{reply}\n\n"
        "请根据用户回复继续处理同一个文件，并继续输出执行过程和结果。"
    )


@router.get("/list")
async def list_wikis(db: aiosqlite.Connection = Depends(get_db)):
    cur = await db.execute(
        "SELECT category, filename, original, size_bytes, uploaded_at "
        "FROM wikis ORDER BY uploaded_at DESC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/categories")
async def get_categories():
    return {
        "all": list(CATEGORIES),
        "text": list(TEXT_CATEGORIES),
        "extensions": {k: sorted(v) for k, v in CATEGORY_EXTENSIONS.items()},
    }


@router.delete("/{category}/{filename}")
async def delete_wiki(
    category: str,
    filename: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    if category not in CATEGORIES:
        raise HTTPException(404, "Unknown category")
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(400, "Invalid filename")

    (category_dir(category) / safe).unlink(missing_ok=True)

    await db.execute(
        "DELETE FROM wikis WHERE category = ? AND filename = ?",
        (category, safe),
    )
    await db.commit()
    return {"deleted": f"{category}/{safe}"}
