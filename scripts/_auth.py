"""Shared by the smoke scripts: read the host's token and present it.

Same lookup as hooks/assist-hook.sh: ASSIST_TOKEN, else $ASSIST_HOME/token,
else ~/.assist/token.
"""
import os
from pathlib import Path


def token() -> str:
    env = os.environ.get("ASSIST_TOKEN", "").strip()
    if env:
        return env
    home = Path(os.environ.get("ASSIST_HOME") or Path.home() / ".assist")
    return (home / "token").read_text().strip()


def headers() -> dict:
    return {"Authorization": f"Bearer {token()}"}


async def ws_login(ws) -> None:
    """The host sends nothing until the first message is a valid auth."""
    await ws.send_json({"type": "auth", "token": token()})
