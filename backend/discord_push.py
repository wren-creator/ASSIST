"""Phone push for v1: post a line to a Discord channel so the phone buzzes.

Token handling and the _discord_request shape are lifted straight from
claude-tools/discord_bridge.py. Token is read from ~/.discord/.env, never
stored in this repo. Channel id lives in config.yaml (discord.channel_id).

Also runnable directly to find a channel id:
    python -m backend.discord_push --list-guilds
    python -m backend.discord_push --list-channels <guild_id>
    python -m backend.discord_push --test "hello from ASSIST"
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("assist.discord")

ENV_FILE = Path.home() / ".discord" / ".env"
DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "ASSIST (https://github.com/britleyhoff/ASSIST, 1.0)"
HTTP_TIMEOUT = 15


def _load_token() -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


def _request(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{DISCORD_API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def _post_sync(text: str) -> bool:
    if not config.get("discord", "enabled", default=False):
        return False
    channel_id = str(config.get("discord", "channel_id", default="")).strip()
    if not channel_id:
        log.warning("discord.enabled but discord.channel_id is empty")
        return False
    token = _load_token()
    if not token:
        log.warning("no DISCORD_BOT_TOKEN in %s", ENV_FILE)
        return False
    try:
        _request("POST", f"/channels/{channel_id}/messages", token, {"content": text[:1900]})
        return True
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        log.error("discord post failed: %s", e)
        return False


async def push(text: str) -> bool:
    """Fire-and-forget from the event loop."""
    return await asyncio.to_thread(_post_sync, text)


# --- ad hoc CLI helpers -------------------------------------------------------

def _cli() -> None:
    import sys

    logging.basicConfig(level=logging.INFO)
    token = _load_token()
    if not token:
        print(f"No DISCORD_BOT_TOKEN in {ENV_FILE}")
        return
    args = sys.argv[1:]
    if args and args[0] == "--list-guilds":
        for g in _request("GET", "/users/@me/guilds", token):
            print(f"{g['name']}  id={g['id']}")
    elif len(args) == 2 and args[0] == "--list-channels":
        for c in _request("GET", f"/guilds/{args[1]}/channels", token):
            if c.get("type") in (0, 5):
                print(f"#{c['name']}  id={c['id']}")
    elif len(args) == 2 and args[0] == "--test":
        cid = str(config.get("discord", "channel_id", default="")).strip()
        if not cid:
            print("set discord.channel_id in config.yaml first")
            return
        _request("POST", f"/channels/{cid}/messages", token, {"content": args[1]})
        print("sent")
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
