"""Quick manual check of the /ws path: connect, print the hello, fire a hook
in the background so we see it pushed, then ask a typed question and print the
reply + whether audio came back. Not a pytest, just a dev smoke test.

    ./venv/bin/python scripts/ws_smoke.py "what is happening right now"
"""
import asyncio
import base64
import json
import sys
import urllib.request

import aiohttp

PORT = 8787


def fire_hook(payload: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/hook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=2).read()


async def main():
    question = sys.argv[1] if len(sys.argv) > 1 else "what is happening right now"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{PORT}/ws") as ws:
            hello = await ws.receive_json()
            print(f"hello: {len(hello.get('events', []))} events, "
                  f"{len(hello.get('sessions', []))} sessions")

            fire_hook({"hook_event_name": "PostToolUse", "session_id": "smoke",
                       "cwd": "/tmp/demo-project", "tool_name": "Bash",
                       "tool_input": {"command": "pytest -q"}})

            await ws.send_json({"type": "text", "text": question, "session_id": None})

            got_audio = False
            deadline = asyncio.get_event_loop().time() + 45
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                t = msg.get("type")
                if t == "event":
                    print(f"  <- event: [{msg['event']['project']}] {msg['event']['summary']}")
                elif t == "reply":
                    print(f"  <- reply: {msg['text']}")
                elif t == "audio":
                    got_audio = True
                    print(f"  <- audio: {len(base64.b64decode(msg['data']))} bytes {msg['mime']}")
                    break
            print("AUDIO OK" if got_audio else "no audio (tts issue or muted)")


asyncio.run(main())
