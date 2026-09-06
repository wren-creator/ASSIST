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

// --- websocket ----------------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => setStatus(true);
  ws.onclose = () => { setStatus(false); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
}

function setStatus(on) {
  statusEl.textContent = on ? "live" : "offline";
  statusEl.className = on ? "on" : "off";
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function handle(msg) {
  switch (msg.type) {
    case "hello":
      feedEl.innerHTML = "";
      (msg.events || []).forEach(addEvent);
      renderSessions(msg.sessions || []);
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
  }
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
  send({ type: "new_task", name, prompt, existing: taskExistingCb.checked });
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
