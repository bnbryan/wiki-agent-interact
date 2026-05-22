import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        event = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                event["data"] = json.loads(line.removeprefix("data: "))
        if event:
            events.append(event)
    return events


def db_rows(client, query, params=()):
    with sqlite3.connect(client.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def test_ask_stream_creates_session_persists_messages_and_streams_chunks(client, monkeypatch):
    from app.routes import chat

    async def fake_ask(question, history, permission_handler):
        assert question == "你好"
        assert history == []
        assert permission_handler is not None
        yield "hello"
        yield " 世界"

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(
        "/api/chat/ask",
        json={"message": "你好"},
        headers={"X-Wiki-User-Id": "alice"},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0]["event"] == "session"
    session_id = events[0]["data"]["session_id"]
    assert [event["event"] for event in events[1:]] == ["delta", "delta", "done"]
    assert events[1]["data"] == {"text": "hello"}
    assert events[2]["data"] == {"text": " 世界"}

    sessions = db_rows(client, "SELECT id, user_id, title FROM sessions")
    assert sessions == [{"id": session_id, "user_id": "alice", "title": "你好"}]
    messages = db_rows(
        client,
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    assert messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "hello 世界"},
    ]


def test_ask_stream_loads_existing_history_for_same_user(client, monkeypatch):
    from app.routes import chat

    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
            ("s1", "alice", "first title"),
        )
        conn.executemany(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            [
                ("s1", "user", "old question"),
                ("s1", "assistant", "old answer"),
            ],
        )
        conn.commit()

    async def fake_ask(question, history, permission_handler):
        assert question == "follow up"
        assert history == [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        yield "new answer"

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(
        "/api/chat/ask",
        json={"session_id": "s1", "message": "follow up"},
        headers={"X-Wiki-User-Id": "alice"},
    )

    assert response.status_code == 200
    assert parse_sse(response.text)[-1]["event"] == "done"
    assert db_rows(client, "SELECT title FROM sessions WHERE id = 's1'") == [
        {"title": "first title"}
    ]
    assert db_rows(
        client,
        "SELECT role, content FROM messages WHERE session_id = 's1' ORDER BY id",
    ) == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "follow up"},
        {"role": "assistant", "content": "new answer"},
    ]


def test_ask_stream_rejects_empty_message_invalid_user_and_cross_user_session(client):
    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
            ("private", "alice", "secret"),
        )
        conn.commit()

    empty = client.post("/api/chat/ask", json={"message": "   "})
    invalid_user = client.post(
        "/api/chat/ask",
        json={"message": "hello"},
        headers={"X-Wiki-User-Id": "bad user"},
    )
    cross_user = client.post(
        "/api/chat/ask",
        json={"session_id": "private", "message": "hello"},
        headers={"X-Wiki-User-Id": "bob"},
    )

    assert empty.status_code == 400
    assert empty.json()["detail"] == "Empty message"
    assert invalid_user.status_code == 400
    assert invalid_user.json()["detail"] == "Invalid user id"
    assert cross_user.status_code == 404
    assert cross_user.json()["detail"] == "Session not found"


def test_sessions_messages_and_delete_are_scoped_by_user(client):
    with sqlite3.connect(client.db_path) as conn:
        conn.executemany(
            "INSERT INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
            [
                ("a1", "alice", "Alice session"),
                ("b1", "bob", "Bob session"),
            ],
        )
        conn.executemany(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            [
                ("a1", "user", "alice question"),
                ("b1", "user", "bob question"),
            ],
        )
        conn.commit()

    alice_sessions = client.get(
        "/api/chat/sessions", headers={"X-Wiki-User-Id": "alice"}
    )
    bob_messages_from_alice_session = client.get(
        "/api/chat/sessions/a1/messages", headers={"X-Wiki-User-Id": "bob"}
    )
    bob_delete_alice = client.delete(
        "/api/chat/sessions/a1", headers={"X-Wiki-User-Id": "bob"}
    )
    alice_delete = client.delete(
        "/api/chat/sessions/a1", headers={"X-Wiki-User-Id": "alice"}
    )

    assert alice_sessions.status_code == 200
    alice_body = alice_sessions.json()
    assert len(alice_body) == 1
    assert alice_body[0]["id"] == "a1"
    assert alice_body[0]["title"] == "Alice session"
    assert alice_body[0]["created_at"]
    assert bob_messages_from_alice_session.status_code == 200
    assert bob_messages_from_alice_session.json() == []
    assert bob_delete_alice.status_code == 200
    assert alice_delete.status_code == 200
    assert db_rows(client, "SELECT id, user_id FROM sessions ORDER BY id") == [
        {"id": "b1", "user_id": "bob"}
    ]
    assert db_rows(client, "SELECT session_id, content FROM messages ORDER BY id") == [
        {"session_id": "b1", "content": "bob question"}
    ]


def test_answer_permission_resolves_only_for_owner():
    from app.routes import chat

    async def run():
        future = asyncio.get_running_loop().create_future()
        chat._pending_permissions["req-1"] = chat.PendingPermission(
            session_id="s1",
            user_id="alice",
            future=future,
        )

        with pytest.raises(HTTPException) as wrong_user:
            await chat.answer_permission(
                "req-1",
                chat.PermissionDecisionBody(allow=True),
                user_id="bob",
            )
        assert wrong_user.value.status_code == 404
        assert "req-1" in chat._pending_permissions
        assert not future.done()

        result = await chat.answer_permission(
            "req-1",
            chat.PermissionDecisionBody(allow=False),
            user_id="alice",
        )
        assert result == {"ok": True}
        assert future.result() is False
        assert "req-1" not in chat._pending_permissions

        with pytest.raises(HTTPException) as missing:
            await chat.answer_permission(
                "req-1",
                chat.PermissionDecisionBody(allow=True),
                user_id="alice",
            )
        assert missing.value.status_code == 404

    asyncio.run(run())
