# ASSIST Design

## What it is

ASSIST is a voice and mobile layer over Claude Code. You still do the real work
in the terminal and the editor. ASSIST sits alongside that, watches what your
Claude Code sessions are doing, tells you about it out loud, and lets you check
in or kick off a build from your phone.

It is deliberately small: one Python service, one static web page, a shell shim,
and a handful of local models. Nothing about it is a platform.

## Principles

1. **The brain stays Claude Code.** ASSIST never reimplements the agent. It
   observes Claude Code through its hooks, and drives it (when it drives it at
   all) by shelling out to `claude -p`. If Claude Code changes, ASSIST mostly
   doesn't care.
2. **Local first.** Speech in (faster-whisper), speech out (`say`, or Kokoro or
   Piper), and narration (Ollama) all run on the machine. The only things that
   leave are the optional Discord push and, when you explicitly ask, a
   `claude -p` call on your existing subscription. No API key, no per-token
   bill.
3. **Never slow the real work.** The hook shim forwards each event with a
   one-second-capped `curl` and exits 0 no matter what. A stopped or wedged
   ASSIST host is invisible to the Claude Code session it was watching.
4. **Cheap by default, spend on request.** Watching costs nothing. Narration and
   "what's it doing" run on a small local model. Only "ask claude ..." and the
   "+ task" builds touch the Claude subscription budget, and both are explicit
   actions.
5. **Safe on a hostile network.** The laptop travels. The host binds to
   loopback, and the only way in from outside is Tailscale in front of that
   loopback port. Nothing answers to the coffee-shop wifi.

## Architecture

```
Claude Code (VSCode / terminal)        ASSIST host  (backend/, FastAPI)         phone / laptop
  ~/.claude/settings.json hooks  ──►    POST /hook   record + fan out            browser PWA (frontend/)
    SessionStart, PostToolUse,          WS   /ws     feed, audio, task control     · live event feed
    Notification, Stop, SessionEnd      GET  /api/*  debug / status               · push-to-talk + text
      each runs hooks/assist-hook.sh                                              · "+ task" builds
      (backgrounds curl, exits 0)       events.py    ring buffer + events.jsonl
                                        narrate.py   Ollama summary + Q&A
                                        claude_runner.py   run_oneshot / run_stream around `claude -p`
                                        tasks.py     "+ task" one-shot project builds
                                        voice/stt.py faster-whisper
                                        voice/tts.py say / kokoro / piper
                                        discord_push.py   optional phone push

  away from home:  phone ──► Tailscale ──► `tailscale serve` (HTTPS) ──► 127.0.0.1:8787
```

### Components

- **`hooks/assist-hook.sh`**: wired into `~/.claude/settings.json` for six hook
  events. Reads the event JSON on stdin, forwards it to `POST /hook`, returns
  immediately.
- **`backend/main.py`**: the FastAPI app. `POST /hook` records events and, for
  `Notification` and `Stop`, spawns an announcement task. `WS /ws` is the
  phone's connection. It gets the live feed and spoken audio, and sends
  push-to-talk audio, typed questions, and `new_task` requests back. After every
  hook it broadcasts a `sessions` message with the current live-session list, and
  a 15s background sweep re-sends that list when an idle timeout changes it with
  no new event to ride on. `GET /api/events`, `/api/sessions`, `/api/tasks`,
  `/api/health` are for debugging.
- **`backend/events.py`**: a bounded in-memory ring buffer plus an append-only
  `events.jsonl` audit trail (one JSON object per line, the same convention the
  claude-tools bridges use). Also derives the short feed summary and the list of
  active sessions. A session counts as live until its latest event is
  `SessionEnd` or it has been silent past `server.session_idle_timeout` (30 min
  default); an in-progress turn, latest event `UserPromptSubmit`, gets a longer
  grace so a long silent build does not drop its chip.
- **`backend/narrate.py`**: talks to local Ollama for the spoken one-liner on an
  announcement and for answering questions from the event log. Routes a question
  that starts with "ask claude ..." to the real thing.
- **`backend/claude_runner.py`**: the one place that runs `claude -p`.
  `run_oneshot()` for a single JSON answer (the "ask claude" path), and
  `run_stream()` which parses `--output-format stream-json` and surfaces each
  `tool_use`, text, and result through a callback (the task path).
- **`backend/tasks.py`**: turns a project name plus a prompt into a directory
  under `tasks.projects_root` (default `~/git`), guards against path traversal
  and clobbering, `git init`s a new one, and runs `claude -p` there. Progress
  goes to the feed as `Task` events, and the result is spoken and pushed as
  audio.
- **`backend/voice/`**: `stt.py` wraps faster-whisper (`base`, CPU/int8, model
  kept hot for the process). `tts.py` picks `say`, Kokoro, or Piper from config
  and always falls back to `say`.
- **`backend/discord_push.py`**: optional. Posts the announcement line to a
  Discord channel so the phone buzzes with the screen off. Bot token from
  `~/.discord/.env`, never in the repo.
- **`frontend/`**: one static `index.html` plus `app.js`, a service worker, and
  a manifest. Vanilla JS, no build step. Talks to `/ws` over whatever origin it
  was loaded from, so the same page works on the LAN and over Tailscale.

## Data flows

**Observe.** Claude Code fires a hook, `assist-hook.sh` posts it to `/hook`,
`events.record()` appends to the buffer and `events.jsonl`, and it is broadcast
to every `/ws` client, followed by the refreshed live-session list. The client
renders the feed row and, from that `sessions` message, redraws the filter chips;
if the chip it was filtered to is gone, it falls back to "all". For
`Notification` and `Stop`, `narrate.announce_line()`
asks Ollama for one spoken sentence, which is broadcast as text, then (outside
quiet hours) synthesized by `tts` and broadcast as audio, and if Discord is on,
posted there.

**Ask.** The phone holds the button, the browser records audio, it goes over
`/ws` to `stt.transcribe()`, the text is echoed back as "heard", then
`narrate.answer()` runs. If it starts with "ask claude ...",
`claude_runner.run_oneshot()` runs in the active project. Otherwise Ollama
answers from the event log. The reply is spoken back.

**Task.** The phone submits `new_task`, `tasks.start_task()` resolves the
folder, creates and `git init`s it if new, picks a permission mode (bypass for a
fresh folder, acceptEdits for an existing one), then `claude_runner.run_stream()`
runs the build. Each tool call becomes a `Task` feed event, and on finish the
result is spoken and pushed as audio.

## Key decisions

- **Hooks, not a wrapper, for observation.** Hooks are the supported, stable way
  to see what Claude Code is doing without sitting in front of it.
  Screen-scraping a terminal would break on every UI tweak.
- **`claude -p`, not the Agent SDK, for v1.** The one-shot builds don't need a
  long-lived session or tool approvals, and `claude -p` runs on the subscription
  with zero extra wiring. The Agent SDK comes in for operator mode, where
  `can_use_tool` approvals actually matter.
- **Web Audio API for playback, not `<audio>`.** iOS Safari rejects `play()` on
  an element with no source, so the "unlock on first tap" trick silently failed.
  Creating the `AudioContext` inside a real tap and playing decoded PCM buffers
  is the reliable path.
- **Loopback bind by default.** The earlier `0.0.0.0` default was fine at home
  and a liability on any other network, since the host has no auth. Tailscale in
  front of loopback gives one stable hostname that follows the laptop.
- **One static page, no framework.** The whole client is a feed, a text box, a
  mic button, and a small form. A build step would cost more than it returns.

## Security model

- **No auth on the host.** Access control is the network boundary: loopback plus
  Tailscale. This is the weakest part of the design and the first roadmap item
  (a shared token on `/ws` and `/hook`).
- **Secrets stay out of the repo.** Discord token from `~/.discord/.env`. Claude
  auth is whatever the logged-in CLI already has.
- **Task sandboxing is directory-scoped, not real isolation.** `tasks.py`
  confines the target to `tasks.projects_root` and won't clobber an existing
  non-empty folder without the "existing project" flag, but a build runs with
  `bypassPermissions` in a fresh folder. Treat it like running `claude
  --dangerously-skip-permissions` yourself in a new directory, because that is
  what it is.
- **The audit trail is `events.jsonl`.** Every hook event and every task step is
  a line in it.

## What it deliberately doesn't do yet

- Steer a live Claude Code session with per-tool approvals (operator mode).
- Authenticate clients.
- Keep one conversation coherent across the laptop and the phone.
- Offer a wake word or always-listening mode. Push-to-talk only.
- Send real PWA push notifications. Discord is the stand-in.

## Extending it

- **A new spoken command** is a branch in `narrate.answer()` (or its own
  intent), plus whatever it calls.
- **A new event source** just needs to `POST /hook` with a JSON body carrying
  `hook_event_name` and `cwd`. `events._summarise()` decides how it reads in the
  feed.
- **A different voice** is `voice.tts_engine` in config, or a new branch in
  `voice/tts.py` that returns WAV bytes.
- **A different narration model** is `ollama.model` in config.
