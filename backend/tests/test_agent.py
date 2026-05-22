import pytest


class FakeStreamEvent:
    def __init__(self, event):
        self.event = event


class FakeAssistantMessage:
    pass


class FakeResultMessage:
    def __init__(
        self,
        *,
        subtype="success",
        result=None,
        is_error=False,
        errors=None,
        api_error_status=None,
        permission_denials=None,
        stop_reason=None,
    ):
        self.subtype = subtype
        self.result = result
        self.is_error = is_error
        self.errors = errors
        self.api_error_status = api_error_status
        self.permission_denials = permission_denials
        self.stop_reason = stop_reason


class FakePermissionAllow:
    pass


class FakePermissionDeny:
    def __init__(self, message):
        self.message = message


@pytest.fixture
def fake_sdk_types(monkeypatch):
    from app import agent

    monkeypatch.setattr(agent, "StreamEvent", FakeStreamEvent)
    monkeypatch.setattr(agent, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(agent, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(agent, "PermissionResultAllow", FakePermissionAllow)
    monkeypatch.setattr(agent, "PermissionResultDeny", FakePermissionDeny)
    return agent


async def collect(async_iterable):
    chunks = []
    async for chunk in async_iterable:
        chunks.append(chunk)
    return chunks


def test_format_cli_error_includes_details_and_recent_stderr(fake_sdk_types):
    from collections import deque

    message = fake_sdk_types._format_cli_error(
        "Claude failed",
        deque(["line 1", "line 2"]),
        ["detail a", "", "detail b"],
    )

    assert message == (
        "Claude failed\n\n"
        "detail a\n\n"
        "detail b\n\n"
        "Claude CLI stderr:\nline 1\nline 2"
    )


def test_ask_streams_text_deltas_and_does_not_duplicate_final_result(
    fake_sdk_types, monkeypatch
):
    captured = {}

    def fake_query(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options

        async def gen():
            yield FakeStreamEvent(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}}
            )
            yield FakeStreamEvent(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "b"}}
            )
            yield FakeAssistantMessage()
            yield FakeResultMessage(result="final should be ignored")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    chunks = __import__("asyncio").run(
        collect(
            fake_sdk_types.ask(
                "current",
                history=[
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "answer"},
                ],
            )
        )
    )

    assert chunks == ["a", "b"]
    assert "Prior conversation" in captured["prompt"]
    assert "[user]\nold" in captured["prompt"]
    assert captured["prompt"].endswith("User question: current")
    assert captured["options"].can_use_tool is None


def test_ask_yields_result_message_when_no_partial_stream(fake_sdk_types, monkeypatch):
    def fake_query(prompt, options):
        async def gen():
            yield FakeResultMessage(result="complete answer")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    chunks = __import__("asyncio").run(collect(fake_sdk_types.ask("question")))

    assert chunks == ["complete answer"]


def test_ask_caps_history_to_last_ten_turns(fake_sdk_types, monkeypatch):
    captured = {}

    def fake_query(prompt, options):
        captured["prompt"] = prompt

        async def gen():
            yield FakeResultMessage(result="ok")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)
    history = [{"role": "user", "content": f"turn {i}"} for i in range(12)]

    __import__("asyncio").run(collect(fake_sdk_types.ask("now", history=history)))

    assert "[user]\nturn 0\n\n" not in captured["prompt"]
    assert "[user]\nturn 1\n\n" not in captured["prompt"]
    assert "[user]\nturn 2" in captured["prompt"]
    assert "[user]\nturn 11" in captured["prompt"]


def test_ask_raises_runtime_error_with_result_details_and_stderr(
    fake_sdk_types, monkeypatch
):
    def fake_query(prompt, options):
        options.stderr("sdk stderr")

        async def gen():
            yield FakeResultMessage(
                is_error=True,
                errors=["tool failed"],
                api_error_status=500,
                permission_denials=["denied"],
                stop_reason="error",
            )

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    with pytest.raises(RuntimeError) as exc:
        __import__("asyncio").run(collect(fake_sdk_types.ask("question")))

    message = str(exc.value)
    assert "Claude Code returned an error result" in message
    assert "tool failed" in message
    assert "API error status: 500" in message
    assert "Permission denials: ['denied']" in message
    assert "Stop reason: error" in message
    assert "Claude CLI stderr:\nsdk stderr" in message


def test_ask_permission_callback_maps_handler_decisions(fake_sdk_types, monkeypatch):
    captured = {}

    def fake_query(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options

        async def gen():
            yield FakeResultMessage(result="ok")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    async def permission_handler(payload):
        captured["permission_payload"] = payload
        return payload["tool_name"] == "Read"

    __import__("asyncio").run(
        collect(fake_sdk_types.ask("question", permission_handler=permission_handler))
    )

    async def exercise_callback():
        prompt_events = []
        async for item in captured["prompt"]:
            prompt_events.append(item)
        assert prompt_events[0]["message"]["content"] == "User question: question"

        class Context:
            tool_use_id = "tool-1"
            title = "Needs permission"
            display_name = "Read"
            description = "Read a file"
            blocked_path = None
            decision_reason = "policy"

        allowed = await captured["options"].can_use_tool("Read", {"file": "a.md"}, Context())
        denied = await captured["options"].can_use_tool("Write", {"file": "a.md"}, Context())
        assert isinstance(allowed, FakePermissionAllow)
        assert isinstance(denied, FakePermissionDeny)
        assert denied.message == "User denied this tool use."
        assert captured["permission_payload"] == {
            "tool_name": "Write",
            "tool_input": {"file": "a.md"},
            "tool_use_id": "tool-1",
            "title": "Needs permission",
            "display_name": "Read",
            "description": "Read a file",
            "blocked_path": None,
            "decision_reason": "policy",
        }

    __import__("asyncio").run(exercise_callback())


def test_ask_forces_interactive_permissions_when_handler_is_present(
    fake_sdk_types, monkeypatch
):
    captured = {}
    monkeypatch.setattr(fake_sdk_types, "CLAUDE_PERMISSION_MODE", "dontAsk")

    def fake_query(prompt, options):
        captured["options"] = options

        async def gen():
            yield FakeResultMessage(result="ok")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    async def permission_handler(payload):
        return True

    __import__("asyncio").run(
        collect(fake_sdk_types.ask("question", permission_handler=permission_handler))
    )

    assert captured["options"].permission_mode == "default"
    assert captured["options"].can_use_tool is not None


def test_ask_streams_followup_user_messages_to_same_agent(fake_sdk_types, monkeypatch):
    captured = {}

    async def followups():
        yield "继续"
        yield "停止"

    def fake_query(prompt, options):
        captured["prompt"] = prompt

        async def gen():
            yield FakeResultMessage(result="ok")

        return gen()

    monkeypatch.setattr(fake_sdk_types, "query", fake_query)

    __import__("asyncio").run(
        collect(
            fake_sdk_types.ask(
                "question",
                user_message_stream=followups(),
            )
        )
    )

    async def collect_prompt_events():
        events = []
        async for item in captured["prompt"]:
            events.append(item)
        return events

    prompt_events = __import__("asyncio").run(collect_prompt_events())

    assert [event["message"]["content"] for event in prompt_events] == [
        "User question: question",
        "继续",
        "停止",
    ]
