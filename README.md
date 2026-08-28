# ASSIST

A Jarvis-style voice observer for Claude Code.

You already drive Claude Code from VSCode all day. ASSIST gives that workflow a
voice and a phone. It watches the Claude Code sessions you're already running,
tells you out loud when Claude needs you or finishes something, answers "what's
it working on" from your couch, and buzzes your phone when you've walked away
from the desk.

It mostly observes. It can also **kick off a one-shot build** in a new folder
from the phone ("+ task"), but it doesn't yet let you steer a live session with
tool-by-tool approvals, that's the next phase (see Roadmap).

## How it works

```
Claude Code (VSCode)              ASSIST host (this repo)                  You
  hooks in ~/.claude/settings.json  FastAPI + WebSocket on :8787            phone / laptop
    SessionStart, PostToolUse,        POST /hook  <- forwarded hook JSON      browser PWA
    Notification, Stop, ...           WS   /ws    -> live feed + spoken audio  Discord push
      |  assist-hook.sh forwards        events.jsonl (audit trail)
      |  each event, exits 0 fast       faster-whisper  (speech in, local)
                                        say / Kokoro / Piper (voice out, local)
                                        Ollama qwen2.5-coder:7b (narration + Q&A, local)
                                        claude -p ("ask claude ..." + "+ task" builds)
```

Everything runs on your machine. The only outbound calls are the optional
Discord push and, when you explicitly say "ask claude", one `claude -p` in the
relevant project (which uses your logged-in Claude subscription, not an API key).

## Setup

```bash
cd ~/git/ASSIST
./start.sh                 # creates venv, installs deps, runs the host on :8787
```

Then open `http://localhost:8787/` on the Mac. For phone access, see the next
section, the host binds to loopback by default.

## Reaching it from your phone

`start.sh` binds to `127.0.0.1` by default. That's deliberate: this laptop
travels, and on hotel or conference wifi you do not want the host answering to
everyone on the network (there's no auth on it). Two ways to get the phone in:

### Same wifi only, no VPN

```bash
ASSIST_BIND=0.0.0.0 ./start.sh
```

Then `http://<this-mac-ip>:8787/` on the phone. The IP is DHCP and changes;
`http://$(scutil --get LocalHostName).local:8787/` is usually more stable.

### Anywhere, including on the road (Tailscale)

This is the one that "follows the laptop", the phone reaches the Mac whether
you're home, on hotel wifi, or the Mac is tethered, and whether the phone is on
wifi or cell. One stable hostname, set once.

1. Install: `brew install --cask tailscale-app` (needs your password), launch it,
   **Log in**.
2. In the admin console (https://login.tailscale.com/admin/dns) enable
   **MagicDNS** and **HTTPS Certificates**.
3. Put HTTPS in front of the host (keeps loopback binding; HTTPS is required for
   the phone mic to work):
   ```bash
   /Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8787
   ```
4. Install Tailscale on the phone from the App Store, log in with the same account.
5. On the phone, open `https://<this-mac-name>.<your-tailnet>.ts.net/` (the exact
   URL is printed by `tailscale serve status`). Add to Home Screen.

Nothing in ASSIST changes, the web client talks to whatever host you loaded it
from. The Mac has to be awake and online; `caffeinate -s` while it runs.

### Hooks

`start.sh` does not touch your Claude Code config. The hooks that feed ASSIST
were added to `~/.claude/settings.json` under `"hooks"`, all pointing at
`hooks/assist-hook.sh`. If ASSIST isn't seeing events, open `/hooks` once in a
Claude Code session (or restart it) so the new config is picked up. Review or
disable them anytime from `/hooks`.

The shim forwards each event with a 1-second-capped `curl` and always exits 0,
so a stopped or slow ASSIST host never delays a Claude Code turn.

### Voice

- **Speech in:** `faster-whisper` (`base` model, CPU/int8), the same engine the
  youtube-bridge already uses. First use downloads the model.
- **Speech out:** macOS `say` with the `Oliver (Enhanced)` en-GB voice at 160
  wpm (`voice.say_voice` / `voice.say_rate`). For a fully local non-`say` voice,
  `pip install kokoro` and set `voice.tts_engine: kokoro`; `piper` is the
  lighter option.

### Discord push (optional)

Off by default. To turn it on:

```bash
./venv/bin/python -m backend.discord_push --list-guilds
./venv/bin/python -m backend.discord_push --list-channels <guild_id>
```

Put the channel id in `backend/config.yaml` under `discord.channel_id` and set
`discord.enabled: true`. The bot token is read from `~/.discord/.env`
(`DISCORD_BOT_TOKEN`), never stored in this repo.

## Using it

- **Live feed:** every hook event, newest at the bottom. Tap a project chip to
  filter to one session.
- **Announcements:** on `Notification` (Claude needs you) and `Stop` (Claude
  finished), ASSIST speaks a one-line summary and, if Discord is on, posts it.
  Silenced during `quiet_hours` (feed and Discord still update).
- **Ask it something:** hold the **HOLD** button and talk, or type. Questions
  about your sessions are answered locally from the event log.
- **Escalate to real Claude:** start your question with "ask claude ..." and it
  runs `claude -p` in the active project and speaks the answer back.
- **Start a project from the phone ("+ task"):** name a project and give it a
  prompt. ASSIST makes the folder under `tasks.projects_root` (`~/git`), `git
  init`s it, and runs a one-shot `claude -p` build, streaming each tool call to
  the feed and speaking a summary when it's done. No approvals, no follow-ups.
  New folders run with permissions bypassed (nothing there to break); tick
  "existing project" to run in a folder that's already there (defaults to
  `acceptEdits`). Concurrency is capped in config.

The last two are the paths that spend your Claude subscription budget.

## Config

`backend/config.yaml` — server port, Ollama model, TTS engine and voice, which
hook events get announced, quiet hours, and the Discord channel. Comments in the
file explain each key.

## Layout

```
backend/
  main.py          FastAPI app: POST /hook, WS /ws, static mount
  events.py        ring buffer + events.jsonl audit trail
  narrate.py       Ollama narration/Q&A + "ask claude" escalation
  claude_runner.py run_oneshot() / run_stream() around `claude -p`
  tasks.py         "+ task" one-shot project builds
  discord_push.py  optional phone push (token from ~/.discord/.env)
  voice/stt.py     faster-whisper
  voice/tts.py     say / kokoro / piper
  config.yaml
frontend/          single-page PWA: index.html, app.js, sw.js, manifest.json
hooks/assist-hook.sh
scripts/            ws_smoke.py, task_smoke.py  — dev smoke tests
start.sh stop.sh
```

## Docs

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture, the flows, the decisions and
  why, the security model, what it deliberately doesn't do.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what's shipped and what's next, in
  phases. Short version: v1 is done (observe, voice, one-shot builds); next is
  token auth and task controls, then operator mode (steer a live session with
  per-tool approvals).

## License

Private. Not for redistribution.
