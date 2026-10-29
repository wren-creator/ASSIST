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
    SessionStart, PostToolUse,          WS   /ws     feed, audio, task + operator   · live event feed
    Notification, Stop, SessionEnd      GET  /api/*  debug / status               · push-to-talk + text
      each runs hooks/assist-hook.sh                                              · "+ task" builds
      (backgrounds curl, exits 0)       events.py    ring buffer + events.jsonl   · operator approvals
                                        narrate.py   Ollama summary + Q&A
                                        claude_runner.py   run_oneshot / run_stream around `claude -p`
                                        tasks.py     "+ task" one-shot project builds
                                        operator.py  live session via Claude Agent SDK, phone-gated tools
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
  push-to-talk audio, typed questions, `new_task`, and the operator messages
  (`operator_start` / `operator_stop` / `operator_approve` / `operator_deny`)
  back. After every hook it broadcasts a `sessions` message with the current
  live-session list, and a 15s background sweep re-sends that list when an idle
  timeout changes it with no new event to ride on. `GET /api/events`,
  `/api/sessions`, `/api/tasks`, `/api/operator`, `/api/health` are for
  debugging.
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
  audio. `resolve_existing_dir()` is the shared containment guard operator mode
  reuses.
- **`backend/operator.py`**: one live `ClaudeSDKClient` session at a time.
  `_on_tool` is the `can_use_tool` callback: it broadcasts an `approval_request`
  and parks on an `asyncio.Future` until a phone `operator_approve` /
  `operator_deny` (or the `approval_timeout_seconds` auto-deny). `stop()`
  interrupts and waits for the run to wind down. Budget warn/stop and the list
  of auto-approved tools are the `operator` block in config; all read per call,
  so no restart to retune.
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
renders a feed row for every event and, from that `sessions` message, redraws the
filter chips; if the chip it was filtered to is gone, it falls back to "all".
Rows stay in the DOM and are shown or hidden with `display`, so both filters (the
session chip and the `QUIET`/`ALL` toggle, which hides `PreToolUse`/`PostToolUse`
and defaults on, sticky in `localStorage`) re-apply in place without a reload. For
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

**Operator.** The phone submits `operator_start` with an existing project name.
`operator.start()` enforces one session at a time, then `_drive()` opens a
`ClaudeSDKClient` (Claude Agent SDK, subprocess transport, same subscription
auth as `claude -p`) with `can_use_tool=_on_tool`, `allowed_tools` from
`operator.auto_approve_tools`, `max_budget_usd` from `operator.budget_stop_usd`,
and `setting_sources=[]` so ambient `~/.claude` allow rules can't shadow the
gate. When Claude calls a tool that isn't auto-approved, `_on_tool` parks on an
`asyncio.Future` keyed by `tool_use_id`, broadcasts `approval_request`, and
waits; a phone `operator_approve` / `operator_deny` calls `operator.resolve()`
which sets that future (same event loop, so a plain `set_result`). No answer
inside `operator.approval_timeout_seconds` auto-denies. `operator_stop`
resolves pending futures to deny, calls `client.interrupt()`, and awaits the
drive task so the one-session slot is free again. Tool lines and lifecycle are
`Operator` feed events; the `hello` frame carries `operator.snapshot()` so a
reconnecting phone re-renders a pending approval card.

## Key decisions

- **Hooks, not a wrapper, for observation.** Hooks are the supported, stable way
  to see what Claude Code is doing without sitting in front of it.
  Screen-scraping a terminal would break on every UI tweak.
- **`claude -p` for one-shots, the Agent SDK for operator mode.** The tier-1
  builds don't need a long-lived session or tool approvals, and `claude -p` runs
  on the subscription with zero extra wiring, so `claude_runner.py` stays as-is.
  Operator mode needs a session that lives across turns and a `can_use_tool`
  callback, so it's a separate module (`operator.py`) on the Claude Agent SDK,
  which shells to the bundled `claude` CLI and inherits the same auth.
- **Web Audio API for playback, not `<audio>`.** iOS Safari rejects `play()` on
  an element with no source, so the "unlock on first tap" trick silently failed.
  Creating the `AudioContext` inside a real tap and playing decoded PCM buffers
  is the reliable path.
- **Loopback bind by default.** The earlier `0.0.0.0` default was fine at home
  and a liability on any other network, since auth wasn't there yet. Tailscale in
  front of loopback gives one stable hostname that follows the laptop.
- **One static page, no framework.** The whole client is a feed, a text box, a
  mic button, and a small form. A build step would cost more than it returns.

## Security model

- **One shared token on every data path.** `backend/auth.py` generates a 256-bit
  secret into `~/.assist/token` (0600, dir 0700, created with `O_EXCL` so there's
  no window where it's world-readable) and compares with `hmac.compare_digest`.
  `/hook` and `/api/*` take a bearer header. `/ws` takes it as the first message
  and sends nothing, and keeps the socket out of `_clients`, until it checks out;
  a wrong or missing token closes with 4401 after at most 5 seconds. Only the
  static shell and a bare `/api/health` are public. Loopback is deliberately not
  a trust signal, since `tailscale serve` arrives from 127.0.0.1 as well.
- **Why the token isn't in `config.yaml`.** That file is tracked, so a secret
  there is one `git add` from public. It lives under `~/.assist/` instead.
- **No Origin check on `/ws`.** Behind `tailscale serve` the `Origin` and `Host`
  the app sees can legitimately differ, and a strict match would reject the
  phone. It isn't needed: a page on another origin can open the socket, but it
  can't read the token out of this origin's `localStorage`, so it can't pass the
  first message.
- **Pairing rides in the URL fragment.** `https://host/#t=...` never reaches a
  server, so it isn't in access logs or `Referer`. The page stores it in
  `localStorage` and strips it from the address bar. A 4401 close makes the
  client forget the token and stop retrying instead of hammering the host.
- **Rotation takes effect immediately for new requests.** The host re-reads the
  token file when its mtime changes. Sockets that are already open keep running
  until the host restarts, which is why `--rotate` tells you to restart.
- **A token stolen from the phone is the whole game.** There's one token, no
  per-device identity, no expiry. Fine for one owner, and the first thing to
  revisit if this ever has a second user (see Roadmap).
- **Secrets stay out of the repo.** Discord token from `~/.discord/.env`. Claude
  auth is whatever the logged-in CLI already has.
- **Task sandboxing is directory-scoped, not real isolation.** `tasks.py`
  confines the target to `tasks.projects_root` and won't clobber an existing
  non-empty folder without the "existing project" flag, but a build runs with
  `bypassPermissions` in a fresh folder. Treat it like running `claude
  --dangerously-skip-permissions` yourself in a new directory, because that is
  what it is.
- **Operator mode is the safe path, but the phone is the only gate.** Every
  write or command waits for a tap (auto-approve is read-only tools only), and
  `setting_sources=[]` keeps local allow rules from bypassing it. Answering an
  approval needs a paired `/ws`, so it's as strong as the token.
- **The audit trail is `events.jsonl`.** Every hook event and every task step is
  a line in it.

## What it deliberately doesn't do yet

- Show a diff on the operator approval card, take follow-up prompts into a live
  operator session, or survive a host restart mid-session (Roadmap Phase 2).
- Tell devices apart. There's one token for everything, so you can't revoke a
  single lost phone without rotating for all of them.
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
