"""In-memory ring buffer of Claude Code hook events, plus an append-only
events.jsonl audit trail (same one-JSON-line-per-entry convention the
claude-tools bridges use for their *_log.jsonl files)."""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import config

EVENTS_LOG = Path(__file__).parent / "events.jsonl"

_buffer: deque[dict[str, Any]] = deque(maxlen=config.get("server", "event_buffer", default=400))
_seq = 0


def _project_label(cwd: str | None) -> str:
    if not cwd:
        return "?"
    return Path(cwd).name or cwd


def record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one hook payload, assign it a monotonic seq + timestamp,
    store it, and return the stored form (which is what gets pushed to WS clients)."""
    global _seq
    _seq += 1

    evt = {
        "seq": _seq,
        "ts": time.time(),
        "hook": raw.get("hook_event_name") or raw.get("hook") or "unknown",
        "session_id": raw.get("session_id", ""),
        "project": _project_label(raw.get("cwd")),
        "cwd": raw.get("cwd", ""),
        "tool_name": raw.get("tool_name", ""),
        "summary": _summarise(raw),
        "raw": raw,
    }

    _buffer.append(evt)
    with EVENTS_LOG.open("a") as f:
        f.write(json.dumps(evt) + "\n")
    return evt


def _summarise(raw: dict[str, Any]) -> str:
    """A short human line for the feed and for narration context."""
    hook = raw.get("hook_event_name") or raw.get("hook") or "event"
    if raw.get("summary_hint"):
        return str(raw["summary_hint"])[:200]
    tool = raw.get("tool_name", "")
    ti = raw.get("tool_input") or {}

    if hook == "PostToolUse" and tool:
        if tool in ("Edit", "Write", "NotebookEdit"):
            return f"{tool} {ti.get('file_path', '')}".strip()
        if tool == "Bash":
            cmd = (ti.get("command") or "").strip().replace("\n", " ")
            return f"$ {cmd[:120]}"
        if tool in ("Read", "Glob", "Grep"):
            return f"{tool} {ti.get('file_path') or ti.get('pattern') or ti.get('path') or ''}".strip()
        return tool
    if hook == "UserPromptSubmit":
        p = (raw.get("prompt") or "").strip().replace("\n", " ")
        return f'asked: "{p[:140]}"'
    if hook == "Notification":
        return (raw.get("message") or "Claude needs your attention").strip()
    if hook == "Stop":
        return "Claude finished its turn"
    if hook == "SessionStart":
        return f"session started ({raw.get('source', 'startup')})"
    if hook == "SessionEnd":
        return f"session ended ({raw.get('reason', '')})".strip()
    return hook


def recent(limit: int = 100, session_id: str | None = None) -> list[dict[str, Any]]:
    items = list(_buffer)
    if session_id:
        items = [e for e in items if e["session_id"] == session_id]
    return items[-limit:]


def active_sessions() -> list[dict[str, Any]]:
    """Sessions considered live right now, most-recently-active first.

    A session drops off the list when:
      - its latest event is SessionEnd (clean exit), or
      - it has been silent longer than server.session_idle_timeout.

    A turn in progress (latest event UserPromptSubmit, i.e. Claude is working and
    hasn't hit Stop yet) gets a longer grace before the idle cutoff, so a long
    silent build or test run doesn't make the chip vanish mid-task. That grace is
    bounded, not unlimited, so a dropped Stop is still swept eventually rather than
    pinning the chip forever; a dropped SessionEnd is swept the same way.
    """
    latest: dict[str, dict[str, Any]] = {}
    for e in _buffer:
        if e["session_id"]:
            latest[e["session_id"]] = e

    now = time.time()
    timeout = config.get("server", "session_idle_timeout", default=1800)

    live = []
    for e in latest.values():
        if e["hook"] == "SessionEnd":
            continue
        cutoff = timeout * 3 if e["hook"] == "UserPromptSubmit" else timeout
        if now - e["ts"] > cutoff:
            continue
        live.append(e)
    return sorted(live, key=lambda e: e["ts"], reverse=True)
