"use strict";

// --- elements -------------------------------------------------------------
const statusEl   = document.getElementById("status");
const sessionsEl = document.getElementById("sessions");
const feedEl     = document.getElementById("feed");
const textEl     = document.getElementById("text");
const pttEl      = document.getElementById("ptt");
const muteEl     = document.getElementById("mute");
const newTaskBtn = document.getElementById("newTaskBtn");
const quietBtn   = document.getElementById("quietBtn");
const taskPanel  = document.getElementById("taskPanel");
const taskName   = document.getElementById("taskName");
const taskPrompt = document.getElementById("taskPrompt");
const taskExistingCb = document.getElementById("taskExistingCb");
const taskOperatorCb = document.getElementById("taskOperatorCb");
const taskMsg    = document.getElementById("taskMsg");

let ws = null;
let activeSession = null;          // session_id filter, or null for "all"
let muted = false;

// Quiet mode: the feed is a firehose of every tool call. Most of the time you
// only want the beats that need a human — which project woke up, when Claude is
// asking for you, when a turn finished. These hooks are the tool-by-tool noise
// we hide until you tap ALL.
const QUIET_HIDDEN = new Set(["PreToolUse", "PostToolUse"]);
let quiet = true;
try { quiet = localStorage.getItem("assist.quiet") !== "0"; } catch (e) {}

// --- pairing ------------------------------------------------------------------
// The host's token arrives once in the URL fragment (https://host/#t=...). The
// fragment is never sent to a server, so it stays out of logs. Keep it in
// localStorage and wipe it from the address bar.
const WS_UNAUTHORIZED = 4401;   // host's "bad or missing token" close code
let token = "";
try {
  const m = location.hash.match(/[#&]t=([^&]+)/);
  if (m) {
    localStorage.setItem("assist.token", decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + location.search);
  }
  token = localStorage.getItem("assist.token") || "";
} catch (e) {}

function forgetToken() {
  token = "";
  try { localStorage.removeItem("assist.token"); } catch (e) {}
}

function showNotPaired(why) {
  setStatus("unpaired");
  addBubble("assist", why + " On the Mac, run: ./venv/bin/python -m backend.auth --pair, " +
                      "then open the URL it prints on this device.");
}

// --- websocket ----------------------------------------------------------------
function connect() {
  if (!token) { showNotPaired("This device isn't paired yet."); return; }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  // The host sends nothing until it has the token, so the first thing out is auth.
  // "live" waits for the host's hello, which only comes after the token checks out.
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
  ws.onclose = (ev) => {
    if (ev.code === WS_UNAUTHORIZED) {          // wrong or rotated token: retrying can't help
      forgetToken();
      showNotPaired("The host rejected this device's token.");
      return;
    }
    setStatus(false);
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
}

function setStatus(state) {
  if (state === "unpaired") {
    statusEl.textContent = "not paired";
    statusEl.className = "off";
    return;
  }
  statusEl.textContent = state ? "live" : "offline";
  statusEl.className = state ? "on" : "off";
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function handle(msg) {
  switch (msg.type) {
    case "hello":
      setStatus(true);
      feedEl.innerHTML = "";
      (msg.events || []).forEach(addEvent);
      renderSessions(msg.sessions || []);
      (msg.operator && msg.operator.pending || []).forEach(addApprovalCard);
      scrollFeed();
      break;
    case "event":
      addEvent(msg.event);
      scrollFeed();
      break;
    case "sessions":   // authoritative live-session list from the host
      renderSessions(msg.sessions || []);
      break;
    case "say":       // announcement text (audio may follow separately)
      addBubble("assist", msg.text);
      scrollFeed();
      break;
    case "heard":     // our own transcribed speech
      if (msg.text && msg.text.trim()) addBubble("you", msg.text);
      scrollFeed();
      break;
    case "reply":
      addBubble("assist", msg.text);
      scrollFeed();
      break;
    case "audio":
      if (!muted) enqueueAudio(msg.mime, msg.data);
      break;
    case "error":
      addBubble("assist", "⚠ " + msg.text);
      break;
    case "task_ack":
      if (msg.ok) {
        taskMsg.textContent = "";
        taskPanel.hidden = true;
        taskName.value = ""; taskPrompt.value = ""; taskExistingCb.checked = false;
        addBubble("assist", "task started in " + msg.dir);
      } else {
        taskMsg.textContent = msg.error || "couldn't start that";
      }
      break;
    case "operator_ack":
      if (msg.ok && msg.cwd) {
        taskMsg.textContent = "";
        taskPanel.hidden = true;
        taskName.value = ""; taskPrompt.value = "";
        addBubble("assist", "operator session in " + msg.cwd);
      } else if (!msg.ok && msg.error) {
        taskMsg.textContent = msg.error;
        addBubble("assist", "⚠ " + msg.error);
      }
      break;
    case "approval_request":
      addApprovalCard(msg);
      scrollFeed();
      break;
    case "approval_resolved":
      resolveApprovalCard(msg.tool_use_id);
      break;
  }
}

// --- operator approvals ------------------------------------------------------
function toolSummary(name, input) {
  input = input || {};
  if (name === "Bash") return "$ " + String(input.command || "").replace(/\n/g, " ");
  if (["Edit", "Write", "MultiEdit", "NotebookEdit"].includes(name))
    return (input.file_path || input.notebook_path || "") + "  (diff view coming in 2c)";
  if (["Read", "Glob", "Grep"].includes(name))
    return input.file_path || input.pattern || input.path || "";
  try { return JSON.stringify(input).slice(0, 300); } catch (e) { return ""; }
}

function cssEsc(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s).replace(/[^\w-]/g, "\\$&");
}

function addApprovalCard(req) {
  const tuid = req.tool_use_id;
  if (!tuid || feedEl.querySelector(`.approval[data-tuid="${cssEsc(tuid)}"]`)) return;
  const card = document.createElement("div");
  card.className = "approval";
  card.dataset.tuid = tuid;
  card.innerHTML =
    `<div class="ahead"><span class="albl">APPROVE</span>` +
      `<span class="atool">${escapeHtml(req.tool_name || "tool")}</span></div>` +
    `<div class="abody">${escapeHtml(toolSummary(req.tool_name, req.input))}</div>` +
    `<div class="arow"><button class="adeny">Deny</button>` +
      `<button class="aallow">Allow</button></div>` +
    `<button class="astop">stop session</button>`;
  card.querySelector(".aallow").onclick = () => decideApproval(card, tuid, true);
  card.querySelector(".adeny").onclick = () => decideApproval(card, tuid, false);
  card.querySelector(".astop").onclick = () => send({ type: "operator_stop" });
  feedEl.appendChild(card);
}

function decideApproval(card, tuid, approved) {
  send({ type: approved ? "operator_approve" : "operator_deny", tool_use_id: tuid });
  markApprovalDone(card, approved ? "allowed" : "denied");
}

function resolveApprovalCard(tuid) {
  const card = feedEl.querySelector(`.approval[data-tuid="${cssEsc(tuid)}"]`);
  if (card) markApprovalDone(card, "resolved");
}

function markApprovalDone(card, word) {
  if (!card || card.classList.contains("done")) return;
  card.classList.add("done");
  card.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const body = card.querySelector(".abody");
  body.textContent = word + " — " + body.textContent;
}

// --- quiet toggle ------------------------------------------------------------
function drawQuietBtn() {
  quietBtn.classList.toggle("on", quiet);
  quietBtn.textContent = quiet ? "QUIET" : "ALL";
  quietBtn.title = quiet
    ? "showing only what needs you — tap for every tool call"
    : "showing every tool call — tap for the quiet view";
}
quietBtn.addEventListener("click", () => {
  quiet = !quiet;
  try { localStorage.setItem("assist.quiet", quiet ? "1" : "0"); } catch (e) {}
  drawQuietBtn();
  refilter();
  scrollFeed();
});
drawQuietBtn();

// --- new task panel ------------------------------------------------------------
newTaskBtn.addEventListener("click", () => {
  taskPanel.hidden = !taskPanel.hidden;
  if (!taskPanel.hidden) taskName.focus();
});
document.getElementById("taskCancel").addEventListener("click", () => { taskPanel.hidden = true; taskMsg.textContent = ""; });
document.getElementById("taskStart").addEventListener("click", () => {
  const name = taskName.value.trim();
  const prompt = taskPrompt.value.trim();
  if (!name || !prompt) { taskMsg.textContent = "need a name and a prompt"; return; }
  taskMsg.textContent = "starting…";
  if (taskOperatorCb.checked) {
    send({ type: "operator_start", name, prompt });
  } else {
    send({ type: "new_task", name, prompt, existing: taskExistingCb.checked });
  }
});

// --- feed rendering ----------------------------------------------------------
function visible(sessionId, hook) {
  if (activeSession && sessionId !== activeSession) return false;
  if (quiet && QUIET_HIDDEN.has(hook)) return false;
  return true;
}

// Re-apply both filters (session chip + quiet) to rows already in the feed.
function refilter() {
  feedEl.querySelectorAll(".row").forEach((r) => {
    r.style.display = visible(r.dataset.session, r.dataset.hook) ? "" : "none";
  });
}

function addEvent(e) {
  const row = document.createElement("div");
  row.className = `row hook-${e.hook}`;
  row.dataset.session = e.session_id || "";
  row.dataset.hook = e.hook || "";
  // Always in the DOM, just hidden when filtered out, so toggling a filter can
  // bring it back without a reload.
  if (!visible(e.session_id, e.hook)) row.style.display = "none";
  const time = new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  row.innerHTML =
    `<div class="meta">` +
      `<span class="t">${time}</span>` +
      `<span class="p" title="${escapeHtml(e.project)}">${escapeHtml(e.project)}</span>` +
    `</div>` +
    `<div class="s">${escapeHtml(e.summary || e.hook)}</div>`;
  feedEl.appendChild(row);
}

function addBubble(who, text) {
  const b = document.createElement("div");
  b.className = `bubble ${who}`;
  b.innerHTML = `<span class="lbl">${who === "you" ? "YOU" : "ASSIST"}</span>${escapeHtml(text)}`;
  feedEl.appendChild(b);
}

function scrollFeed() {
  feedEl.scrollTop = feedEl.scrollHeight;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// --- session chips ---------------------------------------------------------------
// The host owns the live-session list (it prunes on SessionEnd and on idle
// timeout). We just render whatever its last "sessions" message said.
let sessionState = [];   // [{session_id, project, ts}]

function renderSessions(sessions) {
  sessionState = sessions.map((s) => ({ session_id: s.session_id, project: s.project, ts: s.ts }));

  // The session we were filtered to is gone — fall back to "all" so we don't
  // sit on a ghost filter showing an empty feed.
  if (activeSession && !sessionState.some((s) => s.session_id === activeSession)) {
    activeSession = null;
    refilter();
  }

  drawSessions();
}

function drawSessions() {
  sessionState.sort((a, b) => b.ts - a.ts);
  sessionsEl.innerHTML = "";
  addChip("all", null);
  sessionState.slice(0, 8).forEach((s) => addChip(s.project, s.session_id));
}

function addChip(label, sid) {
  const c = document.createElement("div");
  c.className = "chip" + ((activeSession === sid) ? " active" : "");
  c.textContent = label;
  c.onclick = () => {
    activeSession = sid;
    drawSessions();
    refilter();
  };
  sessionsEl.appendChild(c);
}

// --- text input ------------------------------------------------------------------
textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && textEl.value.trim()) {
    addBubble("you", textEl.value.trim());
    send({ type: "text", text: textEl.value.trim(), session_id: activeSession });
    textEl.value = "";
    scrollFeed();
  }
});

// --- mute ----------------------------------------------------------------------
muteEl.addEventListener("click", () => {
  muted = !muted;
  muteEl.classList.toggle("off", muted);
  muteEl.innerHTML = muted ? "&#128263;" : "&#128266;";
  if (muted) pendingBuffers.length = 0;
});

// --- push to talk --------------------------------------------------------------
let mediaRecorder = null;
let chunks = [];
let mimeType = "audio/webm";

async function ensureRecorder() {
  if (mediaRecorder) return true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) { mimeType = t; break; }
    }
    mediaRecorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = flush;
    return true;
  } catch (err) {
    addBubble("assist", "⚠ mic unavailable: " + err.message);
    return false;
  }
}

function flush() {
  const blob = new Blob(chunks, { type: mimeType });
  chunks = [];
  if (blob.size < 900) return;                 // ignore accidental taps
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = reader.result.split(",")[1];
    send({ type: "audio", mime: mimeType, data: b64, session_id: activeSession });
  };
  reader.readAsDataURL(blob);
}

async function startRec() {
  if (!(await ensureRecorder())) return;
  if (mediaRecorder.state === "recording") return;
  chunks = [];
  mediaRecorder.start();
  pttEl.classList.add("rec");
  pttEl.textContent = "REC";
}

function stopRec() {
  if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
  pttEl.classList.remove("rec");
  pttEl.textContent = "HOLD";
}

pttEl.addEventListener("pointerdown", (e) => { e.preventDefault(); startRec(); });
pttEl.addEventListener("pointerup", (e) => { e.preventDefault(); stopRec(); });
pttEl.addEventListener("pointercancel", stopRec);
pttEl.addEventListener("pointerleave", stopRec);

// --- audio playback (Web Audio API) ------------------------------------------------
// The <audio> element route is unreliable on iOS: play() with no src rejects, so
// a gesture "unlock" silently fails. Web Audio is the dependable path: create the
// AudioContext inside a real tap, resume() it, then play decoded PCM buffers.
let actx = null;
let audioReady = false;
const pendingBuffers = [];        // ArrayBuffers received before audio was ready
let playChain = Promise.resolve();

function unlockAudio() {
  try {
    if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)();
  } catch (e) { return; }
  const done = () => { audioReady = true; hideEnableSound(); drainPending(); };
  if (actx.state === "running") done();
  else actx.resume().then(done).catch(() => {});
}
document.addEventListener("pointerdown", unlockAudio);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && actx && actx.state === "suspended") actx.resume().catch(() => {});
});

function b64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function enqueueAudio(mime, b64) {
  if (muted) return;
  const buf = b64ToArrayBuffer(b64);
  if (!audioReady || !actx) { pendingBuffers.push(buf); showEnableSound(); return; }
  playBuffer(buf);
}

function playBuffer(arrbuf) {
  playChain = playChain.then(() => new Promise((resolve) => {
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(); } };
    try {
      actx.decodeAudioData(
        arrbuf,
        (audioBuf) => {
          const src = actx.createBufferSource();
          src.buffer = audioBuf;
          src.connect(actx.destination);
          src.onended = finish;
          src.start();
        },
        finish
      );
    } catch (e) { finish(); }
  }));
}

function drainPending() {
  while (pendingBuffers.length) playBuffer(pendingBuffers.shift());
}

function showEnableSound() {
  if (document.getElementById("enableSound")) return;
  const b = document.createElement("button");
  b.id = "enableSound";
  b.textContent = "🔊 tap to enable sound";
  b.onclick = unlockAudio;   // pointerdown already fires unlockAudio; this is the visible affordance
  feedEl.appendChild(b);
  scrollFeed();
}

function hideEnableSound() {
  const b = document.getElementById("enableSound");
  if (b) b.remove();
}

// --- go --------------------------------------------------------------------------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
connect();
setInterval(() => send({ type: "ping" }), 25000);
