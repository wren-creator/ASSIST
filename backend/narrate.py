"""Turns events into spoken lines, and answers spoken questions.

Cheap path  : local Ollama (config: ollama.model) summarising the event buffer.
Expensive   : shell out to `claude -p` in a project dir, only on an explicit
              "ask claude ..." phrasing (spends the Pro subscription budget).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import aiohttp

from . import config, events

log = logging.getLogger("assist.narrate")

_ASK_CLAUDE_RE = re.compile(r"\b(ask|tell)\s+claude\b", re.I)


async def _ollama(system: str, user: str) -> str | None:
    payload = {
        "model": config.get("ollama", "model", default="qwen2.5-coder:7b"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": config.get("ollama", "num_predict", default=180)},
    }
    timeout = aiohttp.ClientTimeout(total=config.get("ollama", "timeout_seconds", default=30))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(config.get("ollama", "url"), json=payload, timeout=timeout) as r:
                if r.status != 200:
                    log.error("ollama %s: %s", r.status, await r.text())
                    return None
                data = await r.json()
                return data["message"]["content"].strip()
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.warning("ollama unreachable: %s", e)
        return None


def _events_block(limit: int = 40, session_id: str | None = None) -> str:
    lines = []
    for e in events.recent(limit=limit, session_id=session_id):
        lines.append(f"[{e['project']}] {e['hook']}: {e['summary']}")
    return "\n".join(lines) if lines else "(no activity recorded yet)"


async def announce_line(event: dict) -> str:
    """One short spoken sentence for a Notification / Stop event. Falls back to
    the raw summary if Ollama is down."""
    system = (
        "You are ASSIST, a calm, dry voice assistant reporting on a developer's "
        "Claude Code sessions. Reply with ONE short spoken sentence. No lists, no markdown."
    )
    ctx = _events_block(limit=25, session_id=event.get("session_id") or None)
    if event["hook"] == "Notification":
        user = f"Claude just asked for the developer's attention in project '{event['project']}'. Recent activity:\n{ctx}\n\nTell them, briefly."
    else:  # Stop
        user = f"Claude just finished a turn in project '{event['project']}'. Recent activity:\n{ctx}\n\nSum up what it just did, briefly."
    out = await _ollama(system, user)
    return out or event["summary"]


async def answer(question: str, session_id: str | None = None) -> str:
    """Answer a spoken question. Routes to `claude -p` only on 'ask claude ...'."""
    if config.get("ask_claude", "enabled", default=True) and _ASK_CLAUDE_RE.search(question):
        return await _ask_claude(_ASK_CLAUDE_RE.sub("", question).strip(" ,.:") or question, session_id)

    system = (
        "You are ASSIST, a calm, dry voice assistant. The developer is asking about "
        "their running Claude Code sessions. Answer in 1-3 spoken sentences using only "
        "the activity log below. No markdown."
    )
    user = f"Activity log:\n{_events_block(limit=60, session_id=session_id)}\n\nQuestion: {question}"
    out = await _ollama(system, user)
    return out or "I couldn't reach the local model to answer that."


async def _ask_claude(prompt: str, session_id: str | None) -> str:
    sessions = events.active_sessions()
    cwd = None
    if session_id:
        cwd = next((e["cwd"] for e in sessions if e["session_id"] == session_id), None)
    if not cwd and sessions:
        cwd = sessions[0]["cwd"]
    cwd = cwd or "."

    binary = config.get("ask_claude", "binary", default="claude")
    timeout = config.get("ask_claude", "timeout_seconds", default=120)
    log.info("ask_claude in %s: %s", cwd, prompt)
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "-p", prompt, "--output-format", "json",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return "Claude took too long to answer that one."
    except FileNotFoundError:
        return f"I can't find the {binary} command to ask Claude."
    if proc.returncode != 0:
        log.error("claude -p failed: %s", err.decode()[:400])
        return "Claude returned an error on that."
    try:
        data = json.loads(out.decode())
        return (data.get("result") or data.get("response") or out.decode()).strip()
    except json.JSONDecodeError:
        return out.decode().strip() or "Claude gave an empty answer."
