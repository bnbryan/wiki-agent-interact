import asyncio
import json
import re
import uuid
from dataclasses import dataclass

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent import ask
from ..config import DB_PATH
from ..db import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])
USER_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class AskBody(BaseModel):
    session_id: str | None = None
    message: str


class PermissionDecisionBody(BaseModel):
    allow: bool


@dataclass
class PendingPermission:
    session_id: str
    user_id: str
    future: asyncio.Future[bool]


_pending_permissions: dict[str, PendingPermission] = {}


def get_user_id(
    x_wiki_user_id: str | None = Header(default=None, alias="X-Wiki-User-Id"),
) -> str:
    if x_wiki_user_id is None:
        return "default"
    user_id = x_wiki_user_id.strip()
    if not USER_ID_RE.fullmatch(user_id):
        raise HTTPException(400, "Invalid user id")
    return user_id


@router.get("/sessions")
async def list_sessions(
    user_id: str = Depends(get_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    cur = await db.execute(
        "SELECT id, title, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    user_id: str = Depends(get_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    cur = await db.execute(
        """
        SELECT messages.role, messages.content, messages.created_at
        FROM messages
        JOIN sessions ON sessions.id = messages.session_id
        WHERE messages.session_id = ? AND sessions.user_id = ?
        ORDER BY messages.id ASC
        """,
        (session_id, user_id),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute(
        """
        DELETE FROM messages
        WHERE session_id IN (
            SELECT id FROM sessions WHERE id = ? AND user_id = ?
        )
        """,
        (session_id, user_id),
    )
    await db.execute(
        "DELETE FROM sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    await db.commit()
    return {"deleted": session_id}


@router.post("/permissions/{request_id}")
async def answer_permission(
    request_id: str,
    body: PermissionDecisionBody,
    user_id: str = Depends(get_user_id),
):
    pending = _pending_permissions.pop(request_id, None)
    if pending is None:
        raise HTTPException(404, "Permission request not found or already answered")
    if pending.user_id != user_id:
        _pending_permissions[request_id] = pending
        raise HTTPException(404, "Permission request not found or already answered")
    if not pending.future.done():
        pending.future.set_result(body.allow)
    return {"ok": True}


@router.post("/ask")
async def ask_stream(
    body: AskBody,
    user_id: str = Depends(get_user_id),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not body.message.strip():
        raise HTTPException(400, "Empty message")

    session_id = body.session_id or str(uuid.uuid4())
    if body.session_id:
        cur = await db.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,))
        row = await cur.fetchone()
        if row is not None and row["user_id"] != user_id:
            raise HTTPException(404, "Session not found")

    # Ensure session exists.
    await db.execute(
        "INSERT OR IGNORE INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
        (session_id, user_id, body.message[:60]),
    )
    # Load prior turns for context.
    cur = await db.execute(
        """
        SELECT messages.role, messages.content
        FROM messages
        JOIN sessions ON sessions.id = messages.session_id
        WHERE messages.session_id = ? AND sessions.user_id = ?
        ORDER BY messages.id ASC
        """,
        (session_id, user_id),
    )
    history = [dict(r) for r in await cur.fetchall()]

    # Persist the user message immediately.
    await db.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)",
        (session_id, body.message),
    )
    await db.commit()

    async def save_assistant(text: str) -> None:
        # Fresh connection — the request-scoped one is closed by the time
        # the streaming generator finishes (or is cancelled).
        async with aiosqlite.connect(DB_PATH) as adb:
            await adb.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)",
                (session_id, text),
            )
            await adb.commit()

    async def event_stream():
        # Tell the client which session this stream belongs to.
        yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n"

        full_answer: list[str] = []
        interrupted = False
        error_msg: str | None = None
        pending_for_stream: set[str] = set()
        events: asyncio.Queue[dict] = asyncio.Queue()

        async def request_permission(payload: dict) -> bool:
            request_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            pending_for_stream.add(request_id)
            _pending_permissions[request_id] = PendingPermission(
                session_id=session_id,
                user_id=user_id,
                future=future,
            )
            await events.put(
                {
                    "type": "permission",
                    "data": {
                        "request_id": request_id,
                        "session_id": session_id,
                        **jsonable_encoder(payload),
                    },
                }
            )
            try:
                return await future
            finally:
                pending_for_stream.discard(request_id)
                _pending_permissions.pop(request_id, None)

        async def run_agent() -> None:
            try:
                async for chunk in ask(body.message, history, request_permission):
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
                    full_answer.append(chunk)
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
        except asyncio.CancelledError:
            # Client aborted the SSE — close the SDK generator (which kills
            # the claude subprocess) and persist what we got so far.
            interrupted = True
            agent_task.cancel()
        except Exception as exc:
            error_msg = str(exc)
            print(f"[ERROR] Agent exception: {exc}", flush=True)
        finally:
            if not agent_task.done():
                agent_task.cancel()
            for request_id in list(pending_for_stream):
                pending = _pending_permissions.pop(request_id, None)
                if pending and not pending.future.done():
                    pending.future.set_result(False)

        text = "".join(full_answer)
        if interrupted:
            text = (text + "\n\n_[已中断]_") if text else "_[已中断]_"
        elif error_msg and not text:
            text = f"⚠️ {error_msg}"

        if text:
            # Shield so a still-propagating cancellation doesn't kill the write.
            try:
                await asyncio.shield(save_assistant(text))
            except asyncio.CancelledError:
                pass

        if interrupted:
            # Honor the cancellation; client is already gone, no more yields.
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
