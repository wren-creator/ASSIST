"""Does the host actually say no? Hits every door with no token, a wrong token and
the right one, and checks the token file's permissions and rotation.

Run it against a throwaway host so it never touches your real token:

    ASSIST_HOME=$(mktemp -d) ASSIST_PORT=8799 ./start.sh   # or uvicorn directly
    ASSIST_HOME=<same dir> ASSIST_PORT=8799 ./venv/bin/python scripts/auth_smoke.py
"""
import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path

import aiohttp

import _auth

PORT = int(os.environ.get("ASSIST_PORT", "8787"))
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parent.parent
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail and not ok else ''}")


async def ws_outcome(s: aiohttp.ClientSession, first: dict | None) -> tuple[str, int | None]:
    """Connect, optionally send a first message, report ('hello'|'closed'|..., close code)."""
    async with s.ws_connect(f"{BASE}/ws") as ws:
        if first is not None:
            await ws.send_json(first)
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=8)
        except asyncio.TimeoutError:
            return "timeout", None
        if msg.type == aiohttp.WSMsgType.TEXT:
            return ("hello" if '"hello"' in msg.data else "data:" + msg.data[:40]), None
        return "closed", ws.close_code


async def main() -> int:
    good = _auth.token()
    bad = "x" * len(good)
    async with aiohttp.ClientSession() as s:
        print("POST /hook")
        payload = {"hook_event_name": "Stop", "session_id": "auth-smoke", "cwd": "/tmp/auth-smoke"}
        for label, hdr, want in [("no token", {}, 401),
                                 ("wrong token", {"Authorization": f"Bearer {bad}"}, 401),
                                 ("wrong scheme", {"Authorization": f"Basic {good}"}, 401),
                                 ("right token", _auth.headers(), 200)]:
            async with s.post(f"{BASE}/hook", json=payload, headers=hdr) as r:
                check(f"{label} -> {want}", r.status == want, f"got {r.status}")

        print("GET /api/*")
        for path in ("events", "sessions", "tasks", "operator"):
            async with s.get(f"{BASE}/api/{path}") as r:
                check(f"/api/{path} without token -> 401", r.status == 401, f"got {r.status}")
        async with s.get(f"{BASE}/api/sessions", headers=_auth.headers()) as r:
            check("/api/sessions with token -> 200", r.status == 200, f"got {r.status}")
        async with s.get(f"{BASE}/api/health") as r:
            body = await r.json()
            check("/api/health is public and says nothing but ok", r.status == 200 and body == {"ok": True}, str(body))

        print("static shell")
        for path in ("/", "/manifest.json", "/sw.js"):
            async with s.get(f"{BASE}{path}") as r:
                check(f"{path} is public", r.status == 200, f"got {r.status}")

        print("WS /ws")
        t0 = asyncio.get_event_loop().time()
        outcome = await ws_outcome(s, None)
        waited = asyncio.get_event_loop().time() - t0
        check("silent client is dropped with 4401 after the timeout",
              outcome == ("closed", 4401) and 4 < waited < 7, f"{outcome} after {waited:.1f}s")
        outcome = await ws_outcome(s, {"type": "auth", "token": bad})
        check("wrong token -> closed 4401, no data first", outcome == ("closed", 4401), str(outcome))
        outcome = await ws_outcome(s, {"type": "ping"})
        check("non-auth first message -> closed 4401", outcome == ("closed", 4401), str(outcome))
        outcome = await ws_outcome(s, {"type": "auth", "token": ["not", "a", "string"]})
        check("non-string token -> closed 4401", outcome == ("closed", 4401), str(outcome))
        outcome = await ws_outcome(s, {"type": "auth", "token": good})
        check("right token -> hello", outcome == ("hello", None), str(outcome))

        print("token file")
        home = Path(os.environ.get("ASSIST_HOME") or Path.home() / ".assist")
        check("token file is 0600", stat.S_IMODE((home / "token").stat().st_mode) == 0o600)
        check("directory is 0700", stat.S_IMODE(home.stat().st_mode) == 0o700)

        print("rotation")
        out = subprocess.run([sys.executable, "-m", "backend.auth", "--rotate"], cwd=ROOT,
                             capture_output=True, text=True, env=os.environ)
        check("--rotate exits 0", out.returncode == 0, out.stderr[-200:])
        async with s.post(f"{BASE}/hook", json=payload, headers={"Authorization": f"Bearer {good}"}) as r:
            check("old token is dead without a restart", r.status == 401, f"got {r.status}")
        async with s.post(f"{BASE}/hook", json=payload, headers=_auth.headers()) as r:
            check("new token works", r.status == 200, f"got {r.status}")

    print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
