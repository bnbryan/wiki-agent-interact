import asyncio
import json
import uuid

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent import ask
from ..config import DB_PATH
from ..db import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])


class AskBody(BaseModel):
    session_id: str | None = None
    message: str


@router.get("/sessions")
async def list_sessions(db: aiosqlite.Connection = Depends(get_db)):
    cur = await db.execute(
        "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    cur = await db.execute(
        "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()
    return {"deleted": session_id}


@router.post("/ask")
async def ask_stream(body: AskBody, db: aiosqlite.Connection = Depends(get_db)):
    if not body.message.strip():
        raise HTTPException(400, "Empty message")

    session_id = body.session_id or str(uuid.uuid4())
    # Ensure session exists.
    await db.execute(
        "INSERT OR IGNORE INTO sessions (id, title) VALUES (?, ?)",
        (session_id, body.message[:60]),
    )
    # Load prior turns for context.
    cur = await db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
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
        try:
            async for chunk in ask(body.message, history):
                full_answer.append(chunk)
                payload = json.dumps({"text": chunk}, ensure_ascii=False)
                yield f"event: delta\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            # Client aborted the SSE — close the SDK generator (which kills
            # the claude subprocess) and persist what we got so far.
            interrupted = True
        except Exception as exc:
            error_msg = str(exc)

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
