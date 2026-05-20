"""Thin wrapper around the Claude Agent SDK.

This backend is just a transport layer. The actual agent behavior — system
prompt, allowed tools, skills, sub-agents — is defined inside the wiki repo
(CLAUDE.md + .claude/). We launch the SDK with `cwd=WIKI_REPO_DIR` and
`setting_sources=["project"]` so that project-level config takes effect.
"""
from __future__ import annotations

from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    StreamEvent,
    TextBlock,
    query,
)

from .config import WIKI_REPO_DIR


async def ask(question: str, history: list[dict] | None = None) -> AsyncIterator[str]:
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

    options = ClaudeAgentOptions(
        cwd=str(WIKI_REPO_DIR),
        setting_sources=["project"],  # load the wiki repo's CLAUDE.md + .claude/
        permission_mode="bypassPermissions",  # unattended server-side use
        include_partial_messages=True,  # token-level streaming
    )

    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, StreamEvent):
            # Forward only assistant text deltas; ignore tool-use deltas etc.
            event = msg.event or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        yield text
        elif isinstance(msg, AssistantMessage):
            # With partial streaming on, AssistantMessage is the consolidated
            # final form — we've already streamed its text via StreamEvents,
            # so skip it to avoid duplicating output.
            pass
