"""Dev smoke test for the tier-1 remote-task path.

Sends a new_task over /ws with a trivial prompt, prints the feed events, the
spoken lines, and whether it finished. Creates a throwaway project dir under
tasks.projects_root; delete it afterward.

    ./venv/bin/python scripts/task_smoke.py [project-name] [prompt]
"""
import asyncio
import sys
import time

import aiohttp

PORT = 8787
NAME = sys.argv[1] if len(sys.argv) > 1 else "assist-tier1-smoke"
PROMPT = sys.argv[2] if len(sys.argv) > 2 else (
    "Create a single file called hello.txt containing exactly the word: hi\n"
    "Do nothing else, then stop."
)


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"http://127.0.0.1:{PORT}/ws") as ws:
            await ws.receive_json()  # hello
            await ws.send_json({"type": "new_task", "name": NAME, "prompt": PROMPT, "existing": False})
            t0 = time.time()
            while time.time() - t0 < 300:
                try:
                    m = await asyncio.wait_for(ws.receive_json(), timeout=30)
                except asyncio.TimeoutError:
                    print(f"  ...{int(time.time() - t0)}s, still waiting")
                    continue
                ty = m.get("type")
                if ty == "task_ack":
                    print("ACK:", {k: v for k, v in m.items() if k != "type"})
                    if not m.get("ok"):
                        return
                elif ty == "event":
                    e = m["event"]
                    print(f"  feed [{e['project']}] {e['summary'][:100]}")
                elif ty == "say":
                    print("  SAY:", m["text"][:160])
                    if "is done" in m["text"] or "failed" in m["text"]:
                        return
                elif ty == "audio":
                    print(f"  audio: {len(m['data'])} b64 chars")
            print("  (gave up after 300s)")


asyncio.run(main())
