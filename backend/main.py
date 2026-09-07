"""ASSIST host — the observer + voice service.

  POST /hook        Claude Code hooks fire this with their event JSON on stdin
                    (via hooks/assist-hook.sh). Recorded, fanned out to the feed,
                    and (for announce events, outside quiet hours) spoken + pushed.
  WS   /ws          the phone/desktop PWA: receives the live feed, sends
                    push-to-talk audio or typed questions, gets spoken answers.
  GET  /            the PWA (frontend/index.html)
  GET  /api/events  } plain JSON, for debugging / curl
  GET  /api/sessions}

Run:  uvicorn backend.main:app --host 0.0.0.0 --port 8787
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, discord_push, events, narrate, operator, tasks
from .voice import stt, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("assist")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="ASSIST")

_clients: set[WebSocket] = set()


async def broadcast(msg: dict) -> None:
    dead = []
    for ws in _clients:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _in_quiet_hours() -> bool:
    start = config.get("quiet_hours", "start", default=22)
    end = config.get("quiet_hours", "end", default=8)
    h = _dt.datetime.now().hour
    return start <= h or h < end if start > end else start <= h < end


# --- background -----------------------------------------------------------------

@app.on_event("startup")
async def _start_background() -> None:
    asyncio.create_task(_session_sweeper())


async def _session_sweeper() -> None:
    """Idle timeouts change the active-session list with no new event to trigger a
    /hook broadcast, so push a fresh list whenever the set of ids actually shifts.
    Cheap: skips entirely when no PWA is connected, and only sends on a change."""
    last_ids: list[str] = []
    while True:
        await asyncio.sleep(15)
        if not _clients:
            continue
        sessions = events.active_sessions()
        ids = [s["session_id"] for s in sessions]
        if ids != last_ids:
            last_ids = ids
            await broadcast({"type": "sessions", "sessions": sessions})


# --- hooks ------------------------------------------------------------------

@app.post("/hook")
async def hook(request: Request):
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 - hooks must never get a hard error back
        body = (await request.body()).decode(errors="replace")
        raw = {"hook_event_name": "unknown", "_unparsed": body[:2000]}

    evt = events.record(raw)
    await broadcast({"type": "event", "event": evt})
    await broadcast({"type": "sessions", "sessions": events.active_sessions()})

    if evt["hook"] in (config.get("announce", default=[]) or []):
        asyncio.create_task(_announce(evt))
    return {"ok": True, "seq": evt["seq"]}


async def _announce(evt: dict) -> None:
    line = await narrate.announce_line(evt)
    await broadcast({"type": "say", "text": line, "event_seq": evt["seq"]})

    if config.get("discord", "enabled", default=False):
        await discord_push.push(f"**{evt['project']}** — {line}")

    if not _in_quiet_hours():
        try:
            audio, mime = await tts.synth(line)
            if audio:
                await broadcast({
                    "type": "audio",
                    "mime": mime,
                    "data": base64.b64encode(audio).decode(),
                    "text": line,
                })
        except Exception as e:  # noqa: BLE001
            log.warning("announce tts failed: %s", e)


# --- PWA websocket --------------------------------------------------------------

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    await ws.send_json({
        "type": "hello",
        "sessions": events.active_sessions(),
        "events": events.recent(limit=120),
        "operator": operator.snapshot(),
    })
    try:
        while True:
            msg = await ws.receive_json()
            await _handle_ws_message(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("ws error: %s", e)
    finally:
        _clients.discard(ws)


async def _handle_ws_message(ws: WebSocket, msg: dict) -> None:
    kind = msg.get("type")
    session_id = msg.get("session_id") or None

    if kind == "ping":
        await ws.send_json({"type": "pong"})
        return

    if kind == "text":
        question = (msg.get("text") or "").strip()
        if question:
            await _respond(ws, question, session_id)
        return

    if kind == "new_task":
        res = await tasks.start_task(
            (msg.get("name") or "").strip(),
            (msg.get("prompt") or "").strip(),
            existing=bool(msg.get("existing")),
            broadcast=broadcast,
        )
        await ws.send_json({"type": "task_ack", **res})
        return

    if kind == "operator_start":
        res = await operator.start(
            (msg.get("name") or "").strip(),
            (msg.get("prompt") or "").strip(),
            broadcast=broadcast,
        )
        await ws.send_json({"type": "operator_ack", **res})
        return

    if kind == "operator_stop":
        res = await operator.stop(broadcast=broadcast)
        await ws.send_json({"type": "operator_ack", **res})
        return

    if kind in ("operator_approve", "operator_deny"):
        ok = operator.resolve((msg.get("tool_use_id") or "").strip(),
                              approved=(kind == "operator_approve"))
        if not ok:
            await ws.send_json({"type": "error", "text": "no pending approval for that id"})
        return

    if kind == "audio":
        try:
            audio = base64.b64decode(msg.get("data", ""))
        except Exception:  # noqa: BLE001
            await ws.send_json({"type": "error", "text": "bad audio payload"})
            return
        text = await stt.transcribe(audio, msg.get("mime", "audio/webm"))
        await ws.send_json({"type": "heard", "text": text})
        if text.strip():
            await _respond(ws, text, session_id)
        return


async def _respond(ws: WebSocket, question: str, session_id: str | None) -> None:
    reply = await narrate.answer(question, session_id)
    await ws.send_json({"type": "reply", "text": reply})
    try:
        audio, mime = await tts.synth(reply)
        if audio:
            await ws.send_json({
                "type": "audio",
                "mime": mime,
                "data": base64.b64encode(audio).decode(),
                "text": reply,
            })
    except Exception as e:  # noqa: BLE001
        log.warning("reply tts failed: %s", e)


# --- debug JSON --------------------------------------------------------------

@app.get("/api/events")
def api_events(limit: int = 100, session_id: str | None = None):
    return events.recent(limit=limit, session_id=session_id)


@app.get("/api/sessions")
def api_sessions():
    return events.active_sessions()


@app.get("/api/tasks")
def api_tasks():
    return tasks.running()


@app.get("/api/operator")
def api_operator():
    return operator.snapshot()


@app.get("/api/health")
def api_health():
    return {"ok": True, "clients": len(_clients)}


# --- PWA static ------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"error": "frontend/index.html not built yet"}, status_code=404)


@app.get("/manifest.json")
def manifest():
    return FileResponse(FRONTEND_DIR / "manifest.json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(FRONTEND_DIR / "sw.js", media_type="application/javascript")
