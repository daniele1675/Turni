"use strict";

const GIORNI = ["dom", "lun", "mar", "mer", "gio", "ven", "sab"];
const GIORNI_LUNGHI = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];
const MESI_BREVI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];

const $ = (id) => document.getElementById(id);
const state = { data: null, week: 0, token: null, polling: null };

/* ---------- token ---------- */
function loadToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    saveToken(fromUrl);
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search);
  }
  try { state.token = state.token || localStorage.getItem("turni_token"); } catch (e) { /* storage non disponibile */ }
}
function saveToken(t) {
  state.token = t;
  try { localStorage.setItem("turni_token", t); } catch (e) { /* resta in memoria */ }
}

/* ---------- date ---------- */
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDay = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d, 12); };
const parseDT = (s) => { const [d = "", t = "00:00"] = String(s || "").split("T"); const [y, m, dd] = d.split("-").map(Number); const [h, mi] = t.split(":").map(Number); return new Date(y, m - 1, dd, h || 0, mi || 0); };
const hhmm = (s) => String(s || "").slice(11, 16) || "--:--";
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const minutesOfDay = (s, day) => Math.round((parseDT(s) - parseDay(day).setHours(0, 0, 0, 0)) / 60000);
function mondayOf(d) { const x = new Date(d); const wd = (x.getDay() + 6) % 7; x.setDate(x.getDate() - wd); x.setHours(12, 0, 0, 0); return x; }
function fmtHours(min) { const h = Math.floor(min / 60), m = min % 60; return m ? `${h} h ${pad(m)}` : `${h} h`; }
function shortDate(d) { return `${GIORNI[d.getDay()]} ${d.getDate()} ${MESI_BREVI[d.getMonth()]}`; }

function relative(start) {
  const now = new Date();
  const diffMin = Math.round((start - now) / 60000);
  const days = Math.round((parseDay(iso(start)) - parseDay(iso(now))) / 86400000);
  if (days === 0) {
    if (diffMin < 60) return `tra ${diffMin} minuti`;
    const h = Math.floor(diffMin / 60), m = diffMin % 60;
    return `oggi, tra ${h} h${m ? " " + pad(m) : ""}`;
  }
  if (days === 1) return "domani";
  if (days === 2) return "dopodomani";
  return `tra ${days} giorni`;
}

/* ---------- dati ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${state.token}` }, cache: "no-store" });
  if (res.status === 401) { showLogin(); throw new Error("401"); }
  return res;
}

async function load() {
  if (!state.token) return showLogin();
  let res;
  try {
    res = await api("/api/data");
  } catch (e) {
    if (e.message !== "401") $("updated").textContent = "Server non raggiungibile";
    return;
  }
  try {
    if (!res.ok) {
      let msg = `il server ha risposto ${res.status}`;
      try { const j = await res.json(); if (j.error) msg += ` (${j.error})`; } catch (_) { /* risposta non JSON */ }
      throw new Error(msg);
    }
    state.data = await res.json();
    render();
    if (state.data.status.running) pollUntilDone();
  } catch (e) {
    console.error(e);
    $("updated").textContent = `Errore: ${e.message}`;
  }
}

/* ---------- vista ---------- */
function render() {
  const d = state.data;
  if (!d) return;
  const today = parseDay(d.today);
  const monday = addDays(mondayOf(today), state.week * 7);
  const sunday = addDays(monday, 6);

  // Titoli
  const sameMonth = monday.getMonth() === sunday.getMonth();
  $("weekTitle").textContent = sameMonth
    ? `${monday.getDate()} – ${sunday.getDate()} ${MESI[sunday.getMonth()]}`
    : `${monday.getDate()} ${MESI_BREVI[monday.getMonth()]} – ${sunday.getDate()} ${MESI_BREVI[sunday.getMonth()]}`;
  const w = state.week;
  $("eyebrow").textContent = w === 0 ? "Questa settimana" : w === 1 ? "Settimana prossima" : w === -1 ? "Settimana scorsa"
    : w > 1 ? `Tra ${w} settimane` : `${-w} settimane fa`;
  $("thisWeek").disabled = w === 0;

  const known = new Set(d.known_dates);
  const byDate = {};
  for (const s of d.shifts) (byDate[s.date] = byDate[s.date] || []).push(s);
  const recentChange = new Set(
    (d.changes || []).filter((c) => (Date.now() - new Date(c.detected_at)) < 7 * 86400000).map((c) => c.date));

  // Scala comune delle barre per la settimana
  const weekShifts = [];
  for (let i = 0; i < 7; i++) weekShifts.push(...(byDate[iso(addDays(monday, i))] || []));
  let lo = 24 * 60, hi = 0;
  for (const s of weekShifts) {
    const a = minutesOfDay(s.start, s.date), b = minutesOfDay(s.end, s.date);
    if (isFinite(a) && isFinite(b)) { lo = Math.min(lo, a); hi = Math.max(hi, b); }
  }
  if (lo > hi) { lo = 0; hi = 24 * 60; }
  lo = Math.floor(lo / 60) * 60; hi = Math.ceil(hi / 60) * 60;
  const span = Math.max(hi - lo, 60);

  const list = $("days");
  list.textContent = "";
  let total = 0;
  const problems = [];
  const notesByDate = {};
  for (const n of d.notes || []) {
    // Assenza con orario (es. permesso 07:30–16:00): si mostra anche l'orario.
    const mins = (parseDT(n.end) - parseDT(n.start)) / 60000;
    const timed = mins < 16 * 60 && !(hhmm(n.start) === "00:00" && (hhmm(n.end) === "23:59" || hhmm(n.end) === "00:00"));
    const text = timed ? `${n.label} ${hhmm(n.start)} – ${hhmm(n.end)}` : n.label;
    (notesByDate[n.start.slice(0, 10)] = notesByDate[n.start.slice(0, 10)] || []).push(text);
  }
  for (let i = 0; i < 7; i++) {
    const day = addDays(monday, i);
    const key = iso(day);
    const node = $("dayTpl").content.firstElementChild.cloneNode(true);
    try {
    node.querySelector(".dow").textContent = GIORNI[day.getDay()];
    node.querySelector(".dnum").textContent = day.getDate();
    if (key === d.today) node.classList.add("today");
    const shifts = byDate[key] || [];
    const head = node.querySelector(".day-head");
    head.setAttribute("aria-label", `${GIORNI_LUNGHI[day.getDay()]} ${day.getDate()} ${MESI[day.getMonth()]}`);

    if (shifts.length) {
      node.querySelector(".time").textContent = shifts.map((s) => `${hhmm(s.start)} – ${hhmm(s.end)}`).join(" · ");
      const worked = shifts.reduce((a, s) => a + s.minutes_worked, 0);
      total += worked;
      node.querySelector(".hours").textContent = worked ? fmtHours(worked) : "";
      const bar = node.querySelector(".bar");
      const detail = node.querySelector(".detail");
      for (const s of shifts) {
        for (const seg of s.segments) {
          const a = minutesOfDay(seg.start, key), b = minutesOfDay(seg.end, key);
          const el = document.createElement("i");
          el.className = `k-${seg.kind}`;
          el.style.left = `${((a - lo) / span) * 100}%`;
          el.style.width = `${((b - a) / span) * 100}%`;
          bar.appendChild(el);
          const li = document.createElement("li");
          li.innerHTML = `<span class="dot k-${seg.kind}"></span><span class="t"></span><span class="l"></span>`;
          li.querySelector(".t").textContent = `${hhmm(seg.start)} – ${hhmm(seg.end)}`;
          li.querySelector(".l").textContent = seg.label;
          detail.appendChild(li);
        }
      }
      const tags = node.querySelector(".tags");
      const absent = shifts.filter((s) => s.absence);
      if (absent.length === shifts.length) {
        node.classList.add("absent");
        delete notesByDate[key]; // l'assenza è già indicata qui
      }
      for (const a of new Set(absent.map((s) => s.absence))) {
        const sp = document.createElement("span"); sp.className = "tag changed"; sp.textContent = a; tags.appendChild(sp);
      }
      const labels = [...new Set(shifts.flatMap((s) => s.highlights))];
      for (const t of labels) { const sp = document.createElement("span"); sp.className = "tag"; sp.textContent = t; tags.appendChild(sp); }
      if (recentChange.has(key)) { const sp = document.createElement("span"); sp.className = "tag changed"; sp.textContent = "Modificato"; tags.appendChild(sp); }
      head.addEventListener("click", () => {
        const open = detail.hidden;
        detail.hidden = !open;
        head.setAttribute("aria-expanded", String(open));
      });
    } else if ((notesByDate[key] || []).length) {
      node.classList.add("off");
      node.querySelector(".time").textContent = notesByDate[key].join(" · ");
      head.disabled = true;
      delete notesByDate[key];
    } else if (known.has(key)) {
      node.classList.add("off");
      node.querySelector(".time").textContent = "Riposo";
      head.disabled = true;
      if (recentChange.has(key)) {
        const sp = document.createElement("span"); sp.className = "tag changed"; sp.textContent = "Turno tolto";
        node.querySelector(".tags").appendChild(sp);
      }
    } else {
      node.classList.add("unknown");
      node.querySelector(".time").textContent = "Non ancora pubblicato";
      head.disabled = true;
    }
    for (const label of notesByDate[key] || []) {
      const sp = document.createElement("span"); sp.className = "tag"; sp.textContent = label;
      node.querySelector(".tags").appendChild(sp);
    }
    } catch (err) {
      console.error("Giorno", key, err, byDate[key]);
      problems.push(`${key}: ${err.message}`);
      node.classList.add("unknown");
      node.querySelector(".time").textContent = "Non riesco a mostrare questo giorno";
    }
    list.appendChild(node);
  }
  state.problems = problems;
  $("weekTotal").textContent = total ? `Totale ${fmtHours(total)}` : "";

  renderHero(d);
  renderBanner(d);
  renderOffers(d, monday, sunday);
  renderStatus(d.status);
}

function renderHero(d) {
  const hero = $("hero");
  const now = new Date();
  const next = d.shifts.find((s) => parseDT(s.end) > now && !s.absence);
  if (!next) { hero.hidden = true; return; }
  const start = parseDT(next.start), end = parseDT(next.end);
  hero.hidden = false;
  if (start <= now) {
    const pct = Math.min(100, Math.max(0, ((now - start) / (end - start)) * 100));
    const left = Math.round((end - now) / 60000);
    hero.innerHTML = `<div class="label">In turno</div><div class="when"></div><div class="sub"></div><div class="progress"><i style="width:${pct.toFixed(1)}%"></i></div>`;
    hero.querySelector(".when").textContent = `${hhmm(next.start)} – ${hhmm(next.end)}`;
    hero.querySelector(".sub").textContent = `Finisce alle ${hhmm(next.end)} · mancano ${fmtHours(left)}`;
  } else {
    hero.innerHTML = `<div class="label">Prossimo turno</div><div class="when"></div><div class="sub"></div>`;
    hero.querySelector(".when").textContent = `${hhmm(next.start)} – ${hhmm(next.end)}`;
    const extra = next.highlights.length ? ` · ${next.highlights.join(", ")}` : "";
    hero.querySelector(".sub").textContent = `${shortDate(start)}, ${relative(start)}${extra}`;
  }
}

function renderBanner(d) {
  const b = $("banner");
  const s = d.status;
  b.className = "";
  if (!s.configured) {
    b.className = "warn"; b.hidden = false;
    b.innerHTML = "<b>Credenziali mancanti</b>Imposta PORTAL_USERNAME e PORTAL_PASSWORD nel file .env del server.";
    return;
  }
  if (s.last_error) {
    b.className = "warn"; b.hidden = false;
    b.innerHTML = `<b>Ultimo aggiornamento non riuscito</b><span></span>`;
    b.querySelector("span").textContent = s.last_error + (s.paused ? " — letture automatiche sospese per 12 ore." : "");
    return;
  }
  const recent = (d.changes || []).filter((c) => c.date >= d.today && (Date.now() - new Date(c.detected_at)) < 3 * 86400000);
  if (recent.length) {
    b.className = "info"; b.hidden = false;
    b.innerHTML = "<b>Turni cambiati</b><ul></ul>";
    const ul = b.querySelector("ul");
    for (const c of recent.slice(0, 5)) {
      const li = document.createElement("li");
      const g = shortDate(parseDay(c.date));
      li.textContent = c.type === "changed" ? `${g}: ${c.before} → ${c.after}`
        : c.type === "added" ? `${g}: nuovo turno ${c.after}` : `${g}: turno ${c.before} tolto`;
      ul.appendChild(li);
    }
    return;
  }
  b.hidden = true;
}

function renderOffers(d, monday, sunday) {
  const box = $("offers");
  const from = iso(monday), to = iso(sunday);
  const offers = (d.offers || []).filter((o) => o.start.slice(0, 10) >= from && o.start.slice(0, 10) <= to);
  if (!offers.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "<h2>Extra time disponibile</h2><ul></ul><p class='muted small'>Da accettare sul portale.</p>";
  const ul = box.querySelector("ul");
  for (const o of offers) {
    const li = document.createElement("li");
    li.textContent = `${shortDate(parseDay(o.start))}: ${hhmm(o.start)} – ${hhmm(o.end)}`;
    ul.appendChild(li);
  }
}

function renderStatus(s) {
  const btn = $("refreshBtn");
  if (s.running) {
    $("updated").textContent = "Aggiornamento in corso…";
    btn.disabled = true;
    return;
  }
  btn.disabled = false;
  if (s.updated_at) {
    const u = new Date(s.updated_at);
    const sameDay = iso(u) === iso(new Date());
    $("updated").textContent = `Aggiornato ${sameDay ? "oggi" : shortDate(u)} alle ${pad(u.getHours())}:${pad(u.getMinutes())}`;
  } else {
    $("updated").textContent = "Ancora nessun dato letto";
  }
  const metodo = { fullcalendar: "dati del calendario", grid: "posizione dei blocchi" }[s.method] || s.method;
  if (state.problems && state.problems.length) {
    $("updated").textContent = `Errore nella vista: ${state.problems[0]}`;
  }
  $("statusLine").textContent = s.updated_at
    ? `Ultima lettura dal portale: ${s.updated_at.replace("T", " ")}${metodo ? ` · lettura da ${metodo}` : ""}` : "";
}

/* ---------- aggiornamento manuale ---------- */
async function manualRefresh() {
  $("refreshBtn").disabled = true;
  try {
    const res = await api("/api/refresh", { method: "POST" });
    const j = await res.json();
    if (!j.ok && j.error) { $("updated").textContent = j.error; $("refreshBtn").disabled = false; return; }
    renderStatus({ running: true });
    pollUntilDone();
  } catch (e) { $("refreshBtn").disabled = false; }
}
function pollUntilDone() {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    try {
      const s = await (await api("/api/status")).json();
      if (!s.running) { clearInterval(state.polling); load(); }
    } catch (e) { clearInterval(state.polling); }
  }, 3000);
}

/* ---------- impostazioni ---------- */
function icsUrl() {
  const p = new URLSearchParams({ token: state.token });
  if ($("icsBreaks").checked) p.set("pause", "1");
  if ($("icsAlarm").checked) p.set("avviso", "60");
  return `${location.origin}/calendar.ics?${p}`;
}
function updateIcs() {
  const u = icsUrl();
  $("icsUrl").value = u;
  $("webcalLink").href = u.replace(/^https?:/, "webcal:");
}
function openSettings() { updateIcs(); $("settings").showModal(); }

function showLogin() {
  const dlg = $("login");
  if (!dlg.open) dlg.showModal();
}

/* ---------- avvio ---------- */
function init() {
  loadToken();
  $("prevWeek").onclick = () => { state.week--; render(); };
  $("nextWeek").onclick = () => { state.week++; render(); };
  $("thisWeek").onclick = () => { state.week = 0; render(); };
  $("refreshBtn").onclick = manualRefresh;
  $("settingsBtn").onclick = openSettings;
  $("icsBreaks").onchange = updateIcs;
  $("icsAlarm").onchange = updateIcs;
  $("copyIcs").onclick = async () => {
    try { await navigator.clipboard.writeText($("icsUrl").value); $("copyIcs").textContent = "Copiato"; }
    catch (e) { $("icsUrl").select(); }
    setTimeout(() => ($("copyIcs").textContent = "Copia indirizzo"), 1500);
  };
  $("logout").onclick = () => {
    try { localStorage.removeItem("turni_token"); } catch (e) { /* niente */ }
    state.token = null; $("settings").close(); showLogin();
  };
  $("loginForm").onsubmit = async (ev) => {
    ev.preventDefault();
    saveToken($("tokenInput").value.trim());
    const res = await fetch("/api/status", { headers: { Authorization: `Bearer ${state.token}` } });
    if (res.ok) { $("login").close(); $("loginError").hidden = true; load(); }
    else { $("loginError").textContent = "Token non valido"; $("loginError").hidden = false; }
  };

  // Scorrimento orizzontale per cambiare settimana
  let x0 = null, y0 = null;
  $("days").addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  $("days").addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) { state.week += dx < 0 ? 1 : -1; render(); }
    x0 = null;
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden && state.data) renderHero(state.data); }, 60000);

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  load();
}

init();
