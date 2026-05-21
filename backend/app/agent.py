"""Thin wrapper around the Claude Agent SDK.

This backend is just a transport layer. The actual agent behavior — system
prompt, allowed tools, skills, sub-agents — is defined inside the wiki repo
(CLAUDE.md + .claude/). We launch the SDK with `cwd=WIKI_REPO_DIR` and
`setting_sources=["project"]` so that project-level config takes effect.
"""
from __future__ import annotations

import os
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

PermissionHandler = Callable[[dict], Awaitable[bool]]


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
        setting_sources=["project"],  # load the wiki repo's CLAUDE.md + .claude/
        permission_mode=CLAUDE_PERMISSION_MODE,
        can_use_tool=can_use_tool if permission_handler is not None else None,
        include_partial_messages=True,  # token-level streaming
    )

    streamed = False
    sdk_prompt = prompt_stream() if permission_handler is not None else prompt
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
            if msg.subtype != "success":
                raise RuntimeError(f"Claude Code returned {msg.subtype}")
            if not streamed and msg.result:
                yield msg.result
