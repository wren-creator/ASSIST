"""Tier-1 remote tasks: from the phone, name a project and a prompt, and ASSIST
kicks off a one-shot `claude -p` build in a folder for it, streaming progress to
the feed and speaking a summary when it's done.

No approvals, no back-and-forth. For a brand-new folder it runs with
bypassPermissions (nothing there to damage); in an existing folder it defaults
to acceptEdits. Both are configurable in config.yaml -> tasks.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from . import claude_runner, config, events

log = logging.getLogger("assist.tasks")

Broadcast = Callable[[dict], Awaitable[None]]

_running: dict[str, dict] = {}   # task_id -> {name, dir, started, prompt}


def _projects_root() -> Path:
    return Path(config.get("tasks", "projects_root", default="~/git")).expanduser()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-._").lower()
    return s[:60]


def _resolve_dir(name: str, existing: bool) -> tuple[Path | None, str]:
    slug = _slug(name)
    if not slug:
        return None, "that project name doesn't reduce to anything usable"
    root = _projects_root()
    target = (root / slug).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        return None, "resolved path escaped the projects root"
    if target.exists():
        if not existing:
            return None, f"'{slug}' already exists under {root} - say it's an existing project to run there"
        if not target.is_dir():
            return None, f"'{slug}' exists but isn't a directory"
    elif existing:
        return None, f"'{slug}' doesn't exist yet - drop the existing-project flag to create it"
    return target, ""


def running() -> list[dict]:
    return [
        {"task_id": tid, "name": t["name"], "dir": str(t["dir"]),
         "started": t["started"], "elapsed": round(time.time() - t["started"])}
        for tid, t in _running.items()
    ]


async def start_task(name: str, prompt: str, *, existing: bool, broadcast: Broadcast) -> dict:
    if not config.get("tasks", "enabled", default=True):
        return {"ok": False, "error": "tasks are disabled in config"}
    if len(_running) >= config.get("tasks", "max_concurrent", default=2):
        return {"ok": False, "error": "too many tasks already running"}
    if not prompt.strip():
        return {"ok": False, "error": "empty prompt"}

    target, err = _resolve_dir(name, existing)
    if err:
        return {"ok": False, "error": err}

    new_project = not target.exists()
    if new_project:
        target.mkdir(parents=True)
        if config.get("tasks", "git_init", default=True):
            await _run(["git", "init", "-q"], target)
            (target / ".gitignore").write_text("node_modules/\n__pycache__/\n.env\n.DS_Store\n")
        perm = config.get("tasks", "new_project_permission_mode", default="bypassPermissions")
    else:
        perm = config.get("tasks", "existing_project_permission_mode", default="acceptEdits")

    task_id = f"task-{int(time.time())}-{_slug(name)}"
    _running[task_id] = {"name": _slug(name), "dir": target, "started": time.time(), "prompt": prompt}
    asyncio.create_task(_drive(task_id, target, prompt, perm, new_project, broadcast))
    return {"ok": True, "task_id": task_id, "dir": str(target), "new_project": new_project, "permission_mode": perm}


async def _drive(task_id: str, target: Path, prompt: str, perm: str,
                 new_project: bool, broadcast: Broadcast) -> None:
    name = _running[task_id]["name"]
    verb = "Starting a new project" if new_project else "Working"
    await _feed(broadcast, target, f"task started: {prompt[:120]}")
    await broadcast({"type": "say", "text": f"{verb} called {name}. I'll tell you when it's done."})

    timeout = config.get("tasks", "timeout_seconds", default=900)

    async def on_event(evt: dict) -> None:
        if evt["kind"] == "tool":
            await _feed(broadcast, target, _tool_line(evt["tool_name"], evt["tool_input"]))
        elif evt["kind"] == "say":
            await _feed(broadcast, target, evt["text"][:200])

    try:
        result = await claude_runner.run_stream(
            prompt, str(target), permission_mode=perm, timeout=timeout, on_event=on_event,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("task %s crashed", task_id)
        result = {"ok": False, "error": str(e)}
    finally:
        _running.pop(task_id, None)

    if result.get("ok"):
        summary = (result.get("result") or "done").strip()
        await _feed(broadcast, target, f"task finished ({result.get('num_turns', 0)} turns)")
        await broadcast({"type": "say", "text": f"{name} is done. {summary[:400]}"})
        await _speak_audio(broadcast, f"{name} is done. {summary[:400]}")
    else:
        err = result.get("error") or "unknown error"
        await _feed(broadcast, target, f"task failed: {err}")
        await broadcast({"type": "say", "text": f"{name} failed: {err[:200]}"})
        await _speak_audio(broadcast, f"{name} failed. {err[:200]}")


def _tool_line(tool: str, ti: dict) -> str:
    if tool in ("Edit", "Write", "NotebookEdit"):
        return f"{tool} {ti.get('file_path', '')}".strip()
    if tool == "Bash":
        return f"$ {(ti.get('command') or '').strip().replace(chr(10), ' ')[:120]}"
    if tool in ("Read", "Glob", "Grep"):
        return f"{tool} {ti.get('file_path') or ti.get('pattern') or ti.get('path') or ''}".strip()
    return tool


async def _feed(broadcast: Broadcast, cwd: Path, summary: str) -> None:
    evt = events.record({"hook_event_name": "Task", "cwd": str(cwd), "summary_hint": summary})
    await broadcast({"type": "event", "event": evt})


async def _speak_audio(broadcast: Broadcast, text: str) -> None:
    from .voice import tts
    import base64
    try:
        audio, mime = await tts.synth(text)
        if audio:
            await broadcast({"type": "audio", "mime": mime,
                             "data": base64.b64encode(audio).decode(), "text": text})
    except Exception as e:  # noqa: BLE001
        log.warning("task tts failed: %s", e)


async def _run(cmd: list[str], cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
