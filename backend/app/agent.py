"""Thin wrapper around the Claude Agent SDK.

This backend is just a transport layer. The actual agent behavior — system
prompt, allowed tools, skills, sub-agents — is defined inside the wiki repo
(CLAUDE.md + .claude/). We launch the SDK with `cwd=WIKI_REPO_DIR` and
`setting_sources=["project"]` so that project-level config takes effect.
"""
from __future__ import annotations

import os
import shutil
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    query,
)

from .config import WIKI_REPO_DIR

CLAUDE_PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "default")
CLAUDE_CLI_PATH = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")

PermissionHandler = Callable[[dict], Awaitable[bool]]


def _format_cli_error(
    message: str,
    stderr_lines: deque[str],
    details: list[str] | None = None,
) -> str:
    parts = [message]
    if details:
        parts.extend(detail for detail in details if detail)
    if stderr_lines:
        parts.append("Claude CLI stderr:\n" + "\n".join(stderr_lines))
    return "\n\n".join(parts)


async def ask(
    question: str,
    history: list[dict] | None = None,
    permission_handler: PermissionHandler | None = None,
) -> AsyncIterator[str]:
    """Stream the agent's textual answer.

    `history` is a list of {role, content} dicts from prior turns; we fold it
    into the prompt as plain text so the remote agent has conversational
    context without us needing to manage its session lifecycle.
    """
    history_block = ""
    if history:
        rendered = []
        for turn in history[-10:]:  # cap to recent turns
            role = turn.get("role", "user")
            content = turn.get("content", "")
            rendered.append(f"[{role}]\n{content}")
        history_block = "Prior conversation:\n" + "\n\n".join(rendered) + "\n\n---\n\n"

    prompt = f"{history_block}User question: {question}"
    stderr_lines: deque[str] = deque(maxlen=30)

    def collect_stderr(line: str) -> None:
        stderr_lines.append(line)

    async def prompt_stream() -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "",
        }

    async def can_use_tool(
        tool_name: str,
        tool_input: dict,
        context,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if permission_handler is None:
            return PermissionResultDeny(
                message="Tool use requires permission, but no permission handler is configured."
            )

        allowed = await permission_handler(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": context.tool_use_id,
                "title": context.title,
                "display_name": context.display_name,
                "description": context.description,
                "blocked_path": context.blocked_path,
                "decision_reason": context.decision_reason,
            }
        )
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="User denied this tool use.")

    options = ClaudeAgentOptions(
        cwd=str(WIKI_REPO_DIR),
        cli_path=CLAUDE_CLI_PATH,
        setting_sources=["user", "project"],  # load user + project settings
        permission_mode=CLAUDE_PERMISSION_MODE,
        can_use_tool=can_use_tool if permission_handler is not None else None,
        include_partial_messages=True,  # token-level streaming
        stderr=collect_stderr,
    )

    streamed = False
    sdk_prompt = prompt_stream() if permission_handler is not None else prompt
    try:
        async for msg in query(prompt=sdk_prompt, options=options):
            if isinstance(msg, StreamEvent):
                # Forward only assistant text deltas; ignore tool-use deltas etc.
                event = msg.event or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            streamed = True
                            yield text
            elif isinstance(msg, AssistantMessage):
                # With partial streaming on, AssistantMessage is the consolidated
                # final form — we've already streamed its text via StreamEvents,
                # so skip it to avoid duplicating output.
                pass
            elif isinstance(msg, ResultMessage):
                if msg.is_error:
                    details = []
                    if msg.errors:
                        details.extend(msg.errors)
                    if msg.api_error_status:
                        details.append(f"API error status: {msg.api_error_status}")
                    if msg.permission_denials:
                        details.append(f"Permission denials: {msg.permission_denials}")
                    # Don't treat normal stop reasons as errors
                    NORMAL_STOP_REASONS = {"stop_sequence", "max_tokens", "end_turn"}
                    if msg.stop_reason and msg.stop_reason not in NORMAL_STOP_REASONS:
                        details.append(f"Stop reason: {msg.stop_reason}")
                    # Only raise if there's an actual error, not just a normal stop
                    if details:
                        # Use details for the error message instead of subtype to avoid
                        # contradictions like "error result: success" when is_error=True
                        # but subtype is "success" (e.g., with permission_denials)
                        error_summary = "; ".join(details) if details else "Unknown error"
                        raise RuntimeError(
                            _format_cli_error(
                                f"Claude Code returned an error result: {error_summary}",
                                stderr_lines,
                                details,
                            )
                        )
                if msg.subtype != "success":
                    raise RuntimeError(f"Claude Code returned {msg.subtype}")
                # Fallback: return result even if streamed was True
                if msg.result:
                    yield msg.result
    except Exception as exc:
        message = str(exc)
        if stderr_lines and "Claude CLI stderr:" not in message:
            message = _format_cli_error(message, stderr_lines)
        raise RuntimeError(message) from exc
