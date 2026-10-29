"""Shared-secret auth for the ASSIST host.

One owner, one token. It lives in ~/.assist/token (0600, directory 0700), outside
the repo so it can never be committed. ASSIST_TOKEN in the environment wins over
the file, and ASSIST_HOME moves the directory (the smoke tests use that).

    python -m backend.auth --pair     print the URL that pairs a phone
    python -m backend.auth --rotate   replace the token, then restart the host

The phone sends the token in the first WebSocket message, because browsers can't
set headers on a WebSocket. /hook and /api/* take `Authorization: Bearer <token>`.
Loopback is not a trust signal here: `tailscale serve` arrives from 127.0.0.1 too.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException, Request

log = logging.getLogger("assist.auth")

_cache: tuple[float, str] | None = None   # (token file mtime, token)


def _home() -> Path:
    return Path(os.environ.get("ASSIST_HOME") or Path.home() / ".assist")


def token_path() -> Path:
    return _home() / "token"


def _create() -> str:
    """Write a fresh token. O_EXCL + 0600 at creation, so there's no window where
    another local user could read it, and two racing starts can't clobber each other."""
    home = _home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(token_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    log.info("generated a new ASSIST token at %s", token_path())
    return tok


def get_token() -> str:
    """The current token: env override, else the file (created on first use)."""
    global _cache
    env = os.environ.get("ASSIST_TOKEN", "").strip()
    if env:
        return env
    path = token_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        try:
            return _create()
        except FileExistsError:           # lost a race, read the winner's
            mtime = path.stat().st_mtime
    if _cache is None or _cache[0] != mtime:   # re-read after a --rotate, no restart needed
        _cache = (mtime, path.read_text().strip())
    return _cache[1]


def check(candidate: object) -> bool:
    """Constant-time compare. Anything that isn't a non-empty string fails."""
    if not isinstance(candidate, str) or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), get_token().encode())


def _who(request: Request) -> str:
    return request.client.host if request.client else "?"


def require(request: Request) -> None:
    """FastAPI dependency for /hook and /api/*."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and check(value.strip()):
        return
    log.warning("rejected %s %s from %s: bad or missing token",
                request.method, request.url.path, _who(request))
    raise HTTPException(status_code=401, detail="unauthorized",
                        headers={"WWW-Authenticate": "Bearer"})


def rotate() -> str:
    """Replace the token atomically. The running host notices on the next check."""
    home = _home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    tmp = home / "token.new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.replace(tmp, token_path())
    return tok


# --- pairing CLI ------------------------------------------------------------------

def _tailscale_url() -> str | None:
    """Best-effort: the https name `tailscale serve` publishes for this Mac."""
    exe = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if not Path(exe).exists():
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5)
        name = json.loads(out.stdout)["Self"]["DNSName"].rstrip(".")
    except Exception:  # noqa: BLE001 - not logged in, not installed, bad JSON: all just "unknown"
        return None
    return f"https://{name}/" if name else None


def pair_url() -> str:
    """The token rides in the URL fragment, which browsers never send to a server,
    so it stays out of access logs and Referer headers."""
    base = os.environ.get("ASSIST_URL") or _tailscale_url() or "http://localhost:8787/"
    return f"{base.rstrip('/')}/#t={get_token()}"


def main(argv: list[str]) -> int:
    if "--rotate" in argv:
        rotate()
        print("Token rotated. Restart the host (./stop.sh && ./start.sh) to drop phones "
              "that are still connected, then run --pair and re-pair each one.")
        return 0
    if "--pair" in argv:
        print(pair_url())
        print("\nOpen that on the phone once. It is the password, so don't paste it anywhere shared.",
              file=sys.stderr)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
