"use strict";

// --- elements -------------------------------------------------------------
const statusEl   = document.getElementById("status");
const sessionsEl = document.getElementById("sessions");
const feedEl     = document.getElementById("feed");
const textEl     = document.getElementById("text");
const pttEl      = document.getElementById("ptt");
const muteEl     = document.getElementById("mute");
const player     = document.getElementById("player");

let ws = null;
let activeSession = null;          // session_id filter, or null for "all"
let muted = false;
const audioQueue = [];
let playing = false;

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
      touchSession(msg.event);
      scrollFeed();
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
  }
}

// --- feed rendering ----------------------------------------------------------
function visible(sessionId) {
  return !activeSession || sessionId === activeSession;
}

function addEvent(e) {
  if (!visible(e.session_id)) return;
  const row = document.createElement("div");
  row.className = `row hook-${e.hook}`;
  row.dataset.session = e.session_id || "";
  const time = new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  row.innerHTML =
    `<span class="t">${time}</span>` +
    `<span class="p" title="${escapeHtml(e.project)}">${escapeHtml(e.project)}</span>` +
    `<span class="s">${escapeHtml(e.summary || e.hook)}</span>`;
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
let sessionState = [];   // [{session_id, project, ts}]

function renderSessions(sessions) {
  sessionState = sessions.map((s) => ({ session_id: s.session_id, project: s.project, ts: s.ts }));
  drawSessions();
}

function touchSession(e) {
  if (!e.session_id) return;
  const found = sessionState.find((s) => s.session_id === e.session_id);
  if (found) { found.ts = e.ts; found.project = e.project; }
  else sessionState.push({ session_id: e.session_id, project: e.project, ts: e.ts });
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
    // re-filter the feed in place
    feedEl.querySelectorAll(".row").forEach((r) => {
      r.style.display = visible(r.dataset.session) ? "" : "none";
    });
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
  if (muted) { audioQueue.length = 0; player.pause(); }
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

// --- audio playback queue ----------------------------------------------------------
// iOS Safari won't play audio until a real user gesture has "unlocked" the
// element, and it's flaky with large data: URIs. So: unlock on the first tap
// anywhere, and feed it blob URLs, not data URIs.
let audioUnlocked = false;
let pendingAudio = null;   // last clip we couldn't play; offered as "tap to play"

function unlockAudio() {
  if (audioUnlocked) return;
  player.muted = true;
  player.play().then(() => {
    player.pause();
    player.currentTime = 0;
    player.muted = false;
    audioUnlocked = true;
    if (pendingAudio) { const u = pendingAudio; pendingAudio = null; audioQueue.push(u); if (!playing) playNext(); }
  }).catch(() => { player.muted = false; });
}
document.addEventListener("pointerdown", unlockAudio, { once: false });

function b64ToBlobUrl(mime, b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime || "audio/wav" }));
}

function enqueueAudio(mime, b64) {
  const url = b64ToBlobUrl(mime, b64);
  if (!audioUnlocked) { pendingAudio = url; showTapToPlay(); return; }
  audioQueue.push(url);
  if (!playing) playNext();
}

function playNext() {
  if (!audioQueue.length) { playing = false; return; }
  playing = true;
  const url = audioQueue.shift();
  player.src = url;
  player._url = url;
  player.play().catch(() => { pendingAudio = url; showTapToPlay(); playing = false; });
}

function cleanupUrl() {
  if (player._url) { URL.revokeObjectURL(player._url); player._url = null; }
}
player.addEventListener("ended", () => { cleanupUrl(); playNext(); });
player.addEventListener("error", () => { cleanupUrl(); playNext(); });

function showTapToPlay() {
  if (document.getElementById("tapPlay")) return;
  const b = document.createElement("button");
  b.id = "tapPlay";
  b.textContent = "🔊 tap to play ASSIST";
  b.style.cssText = "align-self:center;margin:6px 0;padding:8px 14px;border-radius:10px;border:1px solid var(--accent);background:var(--panel-2);color:var(--accent);font:600 12px/1 var(--mono);cursor:pointer";
  b.onclick = () => {
    audioUnlocked = true;
    b.remove();
    if (pendingAudio) { audioQueue.push(pendingAudio); pendingAudio = null; }
    if (!playing) playNext();
  };
  feedEl.appendChild(b);
  scrollFeed();
}

// --- go --------------------------------------------------------------------------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
connect();
setInterval(() => send({ type: "ping" }), 25000);
