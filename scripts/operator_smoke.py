"""Dev smoke test for operator mode.

Starts an operator session over /ws in an existing project, auto-approves the
first tool request it's asked about, and prints the feed / spoken lines until
the session finishes.

    ./venv/bin/python scripts/operator_smoke.py <existing-project-name> [prompt]

The project name must be an existing directory under tasks.projects_root
(~/git). Default prompt runs one Bash command and stops, so you get exactly one
approval prompt to answer.
"""
import asyncio
import os
import sys
import time

import aiohttp

import _auth

PORT = 8787
NAME = sys.argv[1] if len(sys.argv) > 1 else "ASSIST"
PROMPT = (sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].strip() else (
    "Create a file called OPERATOR_SMOKE.txt in the project root containing "
    "exactly the word: ping\nDo nothing else, then stop."
))
# argv[3] or $DECISION: "approve" (default), "deny", or "ignore" (answer nothing,
# to test the approval-timeout auto-deny).
_d = (sys.argv[3] if len(sys.argv) > 3 else os.environ.get("DECISION", "approve")).lower()
DECISION = ("ignore" if _d.startswith("i")
            else "operator_deny" if _d.startswith("d")
            else "operator_approve")


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{PORT}/ws") as ws:
            await _auth.ws_login(ws)
            await ws.receive_json()  # hello
            await ws.send_json({"type": "operator_start", "name": NAME, "prompt": PROMPT})
            t0 = time.time()
            while time.time() - t0 < 300:
                try:
                    m = await asyncio.wait_for(ws.receive_json(), timeout=30)
                except asyncio.TimeoutError:
                    print(f"  ...{int(time.time() - t0)}s, still waiting")
                    continue
                ty = m.get("type")
                if ty == "operator_ack":
                    print("ACK:", {k: v for k, v in m.items() if k != "type"})
                    if not m.get("ok"):
                        return
                elif ty == "event":
                    e = m["event"]
                    print(f"  feed [{e['project']}] {e['summary'][:120]}")
                elif ty == "approval_request":
                    print(f"  APPROVAL {m['tool_name']}  {str(m.get('input'))[:120]}")
                    if DECISION == "ignore":
                        print("  -> ignoring (waiting for the server's auto-deny timeout)")
                    else:
                        print(f"  -> sending {DECISION}")
                        await ws.send_json({"type": DECISION, "tool_use_id": m["tool_use_id"]})
                elif ty == "approval_resolved":
                    print(f"  resolved {m['tool_use_id']}")
                elif ty == "say":
                    print("  SAY:", m["text"][:200])
                    if "session done" in m["text"].lower() or "stopped" in m["text"].lower() \
                            or "error" in m["text"].lower():
                        return
                elif ty == "audio":
                    print(f"  audio: {len(m['data'])} b64 chars")
                elif ty == "error":
                    print("  ERROR:", m.get("text"))
            print("  (gave up after 300s)")


asyncio.run(main())
