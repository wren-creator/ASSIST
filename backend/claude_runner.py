"""Runs `claude -p` headlessly and (optionally) streams its progress.

Two entry points:
  run_oneshot()  - one prompt, one JSON answer (used by the "ask claude" voice path)
  run_stream()   - one prompt, stream-json events surfaced through a callback
                   (used by tier-1 "start a project from the phone" tasks)

Both run on the machine's logged-in Claude subscription (no API key), same as
typing `claude` in a terminal.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from . import config

log = logging.getLogger("assist.claude")

OnEvent = Callable[[dict], Awaitable[None]]


def _binary() -> str:
    return config.get("ask_claude", "binary", default="claude")


async def run_oneshot(prompt: str, cwd: str, timeout: int) -> str:
    """Single question, single answer. Returns the result text (or an error line)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            _binary(), "-p", prompt, "--output-format", "json",
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return "Claude took too long to answer that one."
    except FileNotFoundError:
        return f"I can't find the {_binary()} command to ask Claude."
    if proc.returncode != 0:
        log.error("claude -p failed: %s", err.decode()[:400])
        return "Claude returned an error on that."
    try:
        data = json.loads(out.decode())
        return (data.get("result") or data.get("response") or out.decode()).strip()
    except json.JSONDecodeError:
        return out.decode().strip() or "Claude gave an empty answer."


async def run_stream(
    prompt: str,
    cwd: str,
    *,
    permission_mode: str,
    timeout: int,
    on_event: OnEvent,
) -> dict:
    """Run a build task, calling on_event(evt) for each stream-json line.

    Returns {ok, result, num_turns, cost, error}.
    """
    args = [
        _binary(), "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", permission_mode,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"{_binary()} not found on PATH"}

    final: dict = {"ok": False, "result": "", "num_turns": 0, "cost": 0.0, "error": ""}

    async def pump() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            await _dispatch(evt, on_event, final)

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        final["error"] = f"timed out after {timeout}s"
        return final
    finally:
        if proc.returncode is None:
            proc.kill()

    if proc.returncode not in (0, None) and not final["result"]:
        err = ((await proc.stderr.read()).decode(errors="replace")[:400]) if proc.stderr else ""
        final["error"] = err or f"claude exited {proc.returncode}"
    else:
        final["ok"] = not final["error"]
    return final


async def _dispatch(evt: dict, on_event: OnEvent, final: dict) -> None:
    etype = evt.get("type")

    if etype == "assistant":
        for block in (evt.get("message", {}) or {}).get("content", []) or []:
            if block.get("type") == "tool_use":
                await on_event({
                    "kind": "tool",
                    "tool_name": block.get("name", ""),
                    "tool_input": block.get("input", {}) or {},
                })
            elif block.get("type") == "text" and block.get("text", "").strip():
                await on_event({"kind": "say", "text": block["text"].strip()})

    elif etype == "result":
        final["result"] = (evt.get("result") or "").strip()
        final["num_turns"] = evt.get("num_turns", 0)
        final["cost"] = evt.get("total_cost_usd", 0.0)
        if evt.get("is_error") or evt.get("subtype") not in (None, "success"):
            final["error"] = evt.get("subtype") or "error"
        await on_event({"kind": "result", "text": final["result"], "error": final["error"]})
