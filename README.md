# ASSIST

A Jarvis-style voice observer for Claude Code.

You already drive Claude Code from VSCode all day. ASSIST gives that workflow a
voice and a phone. It watches the Claude Code sessions you're already running,
tells you out loud when Claude needs you or finishes something, answers "what's
it working on" from your couch, and buzzes your phone when you've walked away
from the desk.

It does **not** drive Claude Code. It observes. Starting and steering sessions
from the phone, with tool-by-tool approvals, is a later phase (see Roadmap).

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
                                        claude -p (only on "ask claude ...")
```

Everything runs on your machine. The only outbound calls are the optional
Discord push and, when you explicitly say "ask claude", one `claude -p` in the
relevant project (which uses your logged-in Claude subscription, not an API key).

## Setup

```bash
cd ~/git/ASSIST
./start.sh                 # creates venv, installs deps, runs the host on :8787
```

Then open `http://localhost:8787/` on the Mac, or `http://<mac-lan-ip>:8787/`
on your phone (same wifi). On the phone, use the browser's "Add to Home Screen"
to install it as an app.

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
- **Speech out:** defaults to macOS `say` (zero install). For a better voice,
  `pip install kokoro` and set `voice.tts_engine: kokoro` in `backend/config.yaml`.
  `piper` is the lighter-weight option.

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
  runs `claude -p` in the active project and speaks the answer back. This is the
  only path that spends your Claude subscription budget.

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
  discord_push.py  optional phone push (token from ~/.discord/.env)
  voice/stt.py     faster-whisper
  voice/tts.py     say / kokoro / piper
  config.yaml
frontend/          single-page PWA: index.html, app.js, sw.js, manifest.json
hooks/assist-hook.sh
scripts/ws_smoke.py   dev smoke test for the /ws path
start.sh stop.sh
```

## Roadmap

- [ ] **Operator mode** — start and steer Claude Code from the phone via the
      Agent SDK's `can_use_tool` callback; tool-by-tool approve/deny; diff review.
- [ ] **Tailscale / Cloudflare Tunnel** — reach the host from off the LAN.
- [ ] **Streaming voice** — barge-in and partial transcripts instead of
      push-to-talk; a wake word.
- [ ] **Cross-device conversation** — SQLite-backed shared history so a thread
      started on the laptop continues on the phone.
- [ ] **Native Web Push** — replace the Discord relay with real PWA
      notifications.
- [ ] **Kokoro as the default voice** once the install path is smoothed.

## License

Private. Not for redistribution.
