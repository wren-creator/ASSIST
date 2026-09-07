"""Operator mode: one live Claude Code session, steered from the phone.

Tier-1 tasks (backend/tasks.py) fire `claude -p` and walk away. Operator mode
keeps a Claude Agent SDK session open and routes every edit or command that
isn't pre-approved to the phone for a one-tap allow / deny.

One session at a time. All state is module-level; start() rejects a second
until the running one ends or is stopped. Cross-restart resume and follow-up
prompts are a later phase (2c/2d) — this is the core loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from . import config, events, tasks
from .tasks import _speak_audio, _tool_line  # reuse the tier-1 helpers verbatim

log = logging.getLogger("assist.operator")

Broadcast = Callable[[dict], Awaitable[None]]

# {client, cwd, prompt, started, cost, sdk_session_id, warned, broadcast,
#  pending: {tool_use_id: {future, tool_name, input}}}
_session: dict[str, Any] | None = None


def _cfg(key: str, default: Any) -> Any:
    return config.get("operator", key, default=default)


# --- public surface ---------------------------------------------------------

async def start(name: str, prompt: str, *, broadcast: Broadcast) -> dict:
    global _session
    if not _cfg("enabled", True):
        return {"ok": False, "error": "operator mode is disabled in config"}
    if _session is not None:
        return {"ok": False, "error": "an operator session is already running"}
    if not prompt.strip():
        return {"ok": False, "error": "empty prompt"}

    target, err = tasks.resolve_existing_dir(name)
    if err:
        return {"ok": False, "error": err}

    _session = {
        "client": None,
        "drive_task": None,
        "cwd": target,
        "prompt": prompt,
        "started": time.time(),
        "cost": 0.0,
        "sdk_session_id": None,
        "warned": False,
        "broadcast": broadcast,
        "pending": {},
    }
    _session["drive_task"] = asyncio.create_task(_drive())
    return {"ok": True, "cwd": str(target)}


def resolve(tool_use_id: str, approved: bool) -> bool:
    """Answer a pending approval. Runs on the same event loop as _on_tool, so a
    plain set_result is correct — do not move the SDK client onto a thread."""
    if not _session:
        return False
    p = _session["pending"].get(tool_use_id)
    if p and not p["future"].done():
        log.info("resolve %s approved=%s", tool_use_id, approved)
        p["future"].set_result(approved)
        return True
    return False


async def stop(*, broadcast: Broadcast) -> dict:
    global _session
    s = _session
    if s is None:
        return {"ok": False, "error": "no operator session running"}
    # Unblock any pending approval first so _on_tool returns a deny promptly,
    # then signal the run.
    for p in list(s["pending"].values()):
        if not p["future"].done():
            p["future"].set_result(False)
    client = s.get("client")
    if client is not None:
        try:
            await client.interrupt()
        except Exception as e:  # noqa: BLE001
            log.warning("operator interrupt failed: %s", e)
    await _feed(broadcast, s["cwd"], "operator session stopped")
    # Wait for _drive to actually wind down (its finally clears _session), so a
    # follow-up start isn't rejected by the one-session cap in the same breath.
    task = s.get("drive_task")
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.TimeoutError:
            log.warning("operator drive didn't wind down in 10s; forcing")
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    if _session is s:
        _session = None
    return {"ok": True}


def snapshot() -> dict | None:
    if not _session:
        return None
    s = _session
    return {
        "cwd": str(s["cwd"]),
        "prompt": s["prompt"],
        "elapsed": round(time.time() - s["started"]),
        "cost": round(s["cost"], 4),
        "pending": [
            {"tool_use_id": tid, "tool_name": p["tool_name"], "input": p["input"]}
            for tid, p in s["pending"].items()
        ],
    }


# --- the run --------------------------------------------------------------

async def _drive() -> None:
    global _session
    s = _session
    assert s is not None
    broadcast, cwd, prompt = s["broadcast"], s["cwd"], s["prompt"]

    await _feed(broadcast, cwd, f"operator session started: {prompt[:120]}")
    await broadcast({"type": "say",
                     "text": f"Operator session started in {Path(cwd).name}. "
                             "I'll ask before each change."})

    opts = ClaudeAgentOptions(
        cwd=str(cwd),
        can_use_tool=_on_tool,
        allowed_tools=list(_cfg("auto_approve_tools", []) or []),
        permission_mode=_cfg("permission_mode", "default"),
        max_budget_usd=float(_cfg("budget_stop_usd", 5.0)),
        # Ignore ambient ~/.claude allow rules so every edit/command really does
        # come through the phone, not whatever the user has pre-allowed locally.
        setting_sources=[],
    )

    try:
        async with ClaudeSDKClient(opts) as client:
            s["client"] = client
            await client.query(prompt)
            async for msg in client.receive_response():
                await _on_message(msg, broadcast, cwd)
    except Exception as e:  # noqa: BLE001
        log.exception("operator session crashed")
        await _feed(broadcast, cwd, f"operator session error: {e}")
        await broadcast({"type": "say",
                         "text": f"Operator session hit an error. {str(e)[:200]}"})
    finally:
        for p in list(s["pending"].values()):
            if not p["future"].done():
                p["future"].set_result(False)
        s["pending"].clear()
        if _session is s:
            _session = None


async def _on_message(msg: Any, broadcast: Broadcast, cwd: Path) -> None:
    s = _session
    if s is None:
        return

    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                await _feed(broadcast, cwd, block.text.strip()[:200])
            elif isinstance(block, ToolUseBlock):
                await _feed(broadcast, cwd, _tool_line(block.name, block.input))
        return

    if isinstance(msg, ResultMessage):
        if msg.session_id:
            s["sdk_session_id"] = msg.session_id
        if msg.total_cost_usd is not None:
            s["cost"] = msg.total_cost_usd

        warn = float(_cfg("budget_warn_usd", 2.0))
        if not s["warned"] and s["cost"] >= warn:
            s["warned"] = True
            await _feed(broadcast, cwd,
                        f"spend crossed ${warn:.2f} (now ${s['cost']:.2f})")

        subtype = msg.subtype or ""
        if msg.is_error or subtype.startswith("error"):
            line = f"operator session ended: {subtype or 'error'} (${s['cost']:.2f})"
            spoken = f"Operator session stopped: {subtype or 'error'}."
        else:
            summary = (msg.result or "done").strip()
            line = f"operator session finished ({msg.num_turns or 0} turns, ${s['cost']:.2f})"
            spoken = f"Operator session done. {summary[:400]}"
        await _feed(broadcast, cwd, line)
        await broadcast({"type": "say", "text": spoken})
        await _speak_audio(broadcast, spoken)


async def _on_tool(tool_name: str, input_data: dict,
                   context: ToolPermissionContext) -> PermissionResultAllow | PermissionResultDeny:
    s = _session
    if s is None:
        return PermissionResultDeny(message="No operator session.")
    broadcast, cwd = s["broadcast"], s["cwd"]

    tuid = context.tool_use_id or f"t{int(time.time() * 1000)}"
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    s["pending"][tuid] = {"future": fut, "tool_name": tool_name, "input": input_data}

    await _feed(broadcast, cwd, f"waiting for you: {_tool_line(tool_name, input_data)}")
    await broadcast({
        "type": "approval_request",
        "tool_use_id": tuid,
        "tool_name": tool_name,
        "input": input_data,
        "cwd": str(cwd),
    })

    if not _phone_connected():
        try:
            from . import discord_push
            await discord_push.push(
                f"operator session waiting on {tool_name} — {Path(cwd).name}")
        except Exception:  # noqa: BLE001
            pass
        await _speak_audio(broadcast,
                           f"Operator session is waiting for approval on {tool_name}.")

    timeout = float(_cfg("approval_timeout_seconds", 300))
    try:
        approved = await asyncio.wait_for(fut, timeout=timeout)
        log.info("approval %s %s -> %s", tuid, tool_name, "allow" if approved else "deny")
    except asyncio.TimeoutError:
        approved = False
        log.info("approval %s %s -> timeout deny", tuid, tool_name)
        await _feed(broadcast, cwd, f"no answer in {int(timeout)}s — denied {tool_name}")
    finally:
        s["pending"].pop(tuid, None)

    await broadcast({"type": "approval_resolved", "tool_use_id": tuid})
    await _feed(broadcast, cwd, f"{'allowed' if approved else 'denied'} {tool_name}")
    return (PermissionResultAllow(updated_input=input_data) if approved
            else PermissionResultDeny(message="Denied from the phone."))


# --- helpers -------------------------------------------------------------

async def _feed(broadcast: Broadcast, cwd: Path | str, summary: str) -> None:
    evt = events.record({"hook_event_name": "Operator", "cwd": str(cwd),
                         "summary_hint": summary})
    await broadcast({"type": "event", "event": evt})


def _phone_connected() -> bool:
    try:
        from . import main
        return bool(main._clients)
    except Exception:  # noqa: BLE001
        return False
