# ASSIST Roadmap

Status markers: `done`, `next`, `planned`, `idea`.

---

## Phase 0. v1: observe + voice + tier-1 tasks  ·  done

- [x] Hook shim into `~/.claude/settings.json`, six events, non-blocking
- [x] FastAPI host: `POST /hook`, `WS /ws`, `events.jsonl` audit trail
- [x] Live feed in a static PWA, per-project filter chips
- [x] Quiet feed by default: the tool-by-tool `PostToolUse` stream is hidden
      behind a sticky `QUIET`/`ALL` header toggle, so the feed shows the beats
      that need you — session start/end, `Notification`, `Stop`, task lifecycle
- [x] Chips show live sessions only: host prunes on `SessionEnd` and on an idle
      timeout, pushes the list over `WS`, client drops a stale filter to "all"
- [x] Spoken announcements on `Notification` and `Stop`, with quiet hours
- [x] Local speech in (faster-whisper) and out (`say`, Kokoro, Piper)
- [x] Local narration and Q&A over the event log (Ollama)
- [x] "ask claude ..." one-shot escalation on the subscription
- [x] Oliver (Enhanced) voice at 160 wpm, with a `say_rate` knob
- [x] iOS audio playback on the Web Audio API
- [x] Loopback bind by default, Tailscale HTTPS path for travel
- [x] Tier-1 remote tasks: name a project and prompt, `claude -p` builds it
- [x] Optional Discord push
- [x] Mobile layout that doesn't collapse on Android Chrome

---

## Phase 1. Hardening  ·  next

- [ ] **Token auth** on `/ws` and `/hook`. A shared secret in `config.yaml`,
      sent as a query param or header, checked on connect. Closes the "no auth
      on a shared network" gap that loopback and Tailscale currently paper over.
- [ ] **Task controls from the phone.** Cancel a running task, see elapsed time
      and current step, tail the last N lines. The backend already tracks
      running tasks, so this needs `WS` messages and UI.
- [ ] **Point a question or task at specific directories.** Right now "ask
      claude ..." runs in whatever project was last active, so a cross-repo
      question ("compare A against B") can't see both. Add an optional working
      directory and `--add-dir` list on the ask and task paths, resolved
      against a configurable root, so you can aim a question at one repo, two
      repos, or a parent folder.
- [ ] **Kokoro as the default voice**, once the install (`espeak-ng`, torch) is
      scripted so `start.sh` can set it up without a manual step.
- [ ] **Streaming STT.** Partial transcripts as you speak instead of
      record-then-send, so it feels like talking rather than dictating.
- [ ] **Wake word** (openWakeWord or Porcupine, host-side mic) for hands-free
      "hey ASSIST, what's happening".

---

## Phase 2. Operator mode  ·  in progress

Drive a live Claude Code session from the phone, not just fire one-shots.

- [x] Swap `claude -p` for the **Claude Agent SDK** so a session stays alive
      across turns. `backend/operator.py` wraps `ClaudeSDKClient`; one session
      at a time, `+ task` panel has an "operator mode" checkbox.
- [x] **Tool-by-tool approvals** via the SDK's `can_use_tool` callback. Each
      edit or command that isn't in `operator.auto_approve_tools` pauses and
      asks the phone, one tap to allow or deny. Auto-deny on a config timeout;
      `operator_stop` interrupts a live run.
- [x] A budget guard: `operator.budget_warn_usd` speaks a warning when spend
      crosses it, `operator.budget_stop_usd` is a hard ceiling (the SDK's
      `max_budget_usd`) that ends the session.
- [ ] **Diff view** in the PWA for a pending edit before you approve it. Right
      now the approval card shows the file path and the raw tool input.
- [ ] **Follow-up prompts** into a running session, with streamed output back.
- [ ] Session **persistence** so a build keeps running on the Mac while you're
      away and you pick it back up when you reconnect. Today a disconnect that
      doesn't kill the host is survivable (the host holds the client, the
      `hello` frame replays any pending approval); a host restart is not.

---

## Phase 3. Continuity and reach  ·  planned

- [ ] **Cross-device conversation.** SQLite-backed history so a thread started
      in the terminal continues on the phone and the other way round.
- [ ] **Native Web Push** to replace the Discord relay: real PWA notifications,
      no second app.
- [ ] **Multi-user, multi-machine.** More than one laptop reporting into one
      ASSIST host, or one phone watching several machines.

---

## Ideas parking lot  ·  idea

- Barge-in: talk over a spoken announcement to interrupt it.
- A daily spoken digest ("here's what you shipped today") from `events.jsonl`.
- Voice profiles per project, so you can tell them apart by ear.
- Let a task open a PR when it finishes and speak the link.
- A small on-device model for the wake word, so the mic never leaves the phone
  until the word is heard.
- Home Assistant or smart-speaker output instead of the phone.
