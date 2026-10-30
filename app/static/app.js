"use strict";

const GIORNI = ["dom", "lun", "mar", "mer", "gio", "ven", "sab"];
const GIORNI_LUNGHI = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"];
const MESI_BREVI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];
const TOGETHER = "insieme";

const $ = (id) => document.getElementById(id);
const state = {
  token: null, week: 0, polling: null, problems: [],
  me: null, isOwner: false, people: [], view: null, cache: {}, editing: null, tplEditing: null,
};

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
  try { state.view = localStorage.getItem("turni_vista") || null; } catch (e) { /* niente */ }
}
function saveToken(t) {
  state.token = t;
  try { localStorage.setItem("turni_token", t); } catch (e) { /* resta in memoria */ }
}
function saveView() { try { localStorage.setItem("turni_vista", state.view); } catch (e) { /* niente */ } }

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
function longDate(d) { return `${GIORNI_LUNGHI[d.getDay()]} ${d.getDate()} ${MESI[d.getMonth()]}`; }
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

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

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* ---------- dati ---------- */
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}), Authorization: `Bearer ${state.token}` };
  if (opts.body && typeof opts.body !== "string") { opts = { ...opts, body: JSON.stringify(opts.body) }; headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, { ...opts, headers, cache: "no-store" });
  if (res.status === 401) { showLogin(); throw new Error("401"); }
  return res;
}
async function apiJson(path, opts) {
  const res = await api(path, opts);
  let j = {};
  try { j = await res.json(); } catch (_) { /* risposta non JSON */ }
  if (!res.ok) throw new Error(j.error || `il server ha risposto ${res.status}`);
  return j;
}

async function load() {
  if (!state.token) return showLogin();
  let mine;
  try {
    mine = await apiJson("/api/data");
  } catch (e) {
    if (e.message !== "401") $("updated").textContent = e instanceof TypeError ? "Server non raggiungibile" : `Errore: ${e.message}`;
    return;
  }
  state.me = mine.me;
  state.isOwner = mine.is_owner;
  state.people = mine.people;
  state.cache = { [mine.me]: mine };
  // Dati delle altre persone (tutti vedono i turni di tutti)
  await Promise.all(state.people.filter((p) => p.id !== mine.me).map(async (p) => {
    try { state.cache[p.id] = await apiJson(`/api/data?persona=${encodeURIComponent(p.id)}`); } catch (e) { /* si mostra senza */ }
  }));
  const valid = state.people.some((p) => p.id === state.view) || (state.view === TOGETHER && state.people.length > 1);
  if (!valid) state.view = state.me;
  try {
    render();
  } catch (e) {
    console.error(e);
    $("updated").textContent = `Errore: ${e.message}`;
  }
  const portal = Object.values(state.cache).find((d) => d.person.source === "portale");
  if (portal && portal.status.running) pollUntilDone();
}

/* ---------- vista ---------- */
function weekRange() {
  const today = parseDay(state.cache[state.me].today);
  const monday = addDays(mondayOf(today), state.week * 7);
  return { today, monday, sunday: addDays(monday, 6) };
}

function renderHeader(monday, sunday) {
  const sameMonth = monday.getMonth() === sunday.getMonth();
  $("weekTitle").textContent = sameMonth
    ? `${monday.getDate()} – ${sunday.getDate()} ${MESI[sunday.getMonth()]}`
    : `${monday.getDate()} ${MESI_BREVI[monday.getMonth()]} – ${sunday.getDate()} ${MESI_BREVI[sunday.getMonth()]}`;
  const w = state.week;
  $("eyebrow").textContent = w === 0 ? "Questa settimana" : w === 1 ? "Settimana prossima" : w === -1 ? "Settimana scorsa"
    : w > 1 ? `Tra ${w} settimane` : `${-w} settimane fa`;
  $("thisWeek").disabled = w === 0;
}

function renderPeopleNav() {
  const nav = $("peopleNav");
  nav.textContent = "";
  if (state.people.length < 2) { nav.hidden = true; return; }
  nav.hidden = false;
  const items = state.people.map((p) => ({ id: p.id, name: p.id === state.me ? `${p.name} (tu)` : p.name }));
  items.push({ id: TOGETHER, name: "Insieme" });
  for (const it of items) {
    const b = el("button", "seg", it.name);
    b.type = "button";
    b.setAttribute("aria-pressed", String(state.view === it.id));
    b.onclick = () => { state.view = it.id; saveView(); render(); };
    nav.appendChild(b);
  }
}

function render() {
  if (!state.cache[state.me]) return;
  renderPeopleNav();
  const { monday, sunday } = weekRange();
  renderHeader(monday, sunday);
  if (state.view === TOGETHER) renderTogether(monday);
  else renderPerson(state.cache[state.view] || state.cache[state.me], monday, sunday);
}

function notesMap(d) {
  const map = {};
  for (const n of d.notes || []) {
    // Assenza con orario (es. permesso 07:30–16:00): si mostra anche l'orario.
    const mins = (parseDT(n.end) - parseDT(n.start)) / 60000;
    const timed = mins < 16 * 60 && !(hhmm(n.start) === "00:00" && (hhmm(n.end) === "23:59" || hhmm(n.end) === "00:00"));
    const text = timed ? `${n.label} ${hhmm(n.start)} – ${hhmm(n.end)}` : n.label;
    (map[n.start.slice(0, 10)] = map[n.start.slice(0, 10)] || []).push(text);
  }
  return map;
}

function weekScale(byDate, monday) {
  let lo = 24 * 60, hi = 0;
  for (let i = 0; i < 7; i++) {
    for (const s of byDate[iso(addDays(monday, i))] || []) {
      const a = minutesOfDay(s.start, s.date), b = minutesOfDay(s.end, s.date);
      if (isFinite(a) && isFinite(b)) { lo = Math.min(lo, a); hi = Math.max(hi, b); }
    }
  }
  if (lo > hi) { lo = 0; hi = 24 * 60; }
  lo = Math.floor(lo / 60) * 60; hi = Math.ceil(hi / 60) * 60;
  return { lo, span: Math.max(hi - lo, 60) };
}

function renderPerson(d, monday, sunday) {
  const manual = d.person.source === "manuale";
  const editable = !!d.editable;
  const known = new Set(d.known_dates);
  const byDate = {};
  for (const s of d.shifts) (byDate[s.date] = byDate[s.date] || []).push(s);
  const recentChange = new Set(
    (d.changes || []).filter((c) => (Date.now() - new Date(c.detected_at)) < 7 * 86400000).map((c) => c.date));
  const { lo, span } = weekScale(byDate, monday);
  const notesByDate = notesMap(d);

  const list = $("days");
  list.textContent = "";
  list.classList.toggle("editable", editable);
  let total = 0;
  const problems = [];
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
      head.setAttribute("aria-label", longDate(day));
      const tags = node.querySelector(".tags");
      const detail = node.querySelector(".detail");

      if (shifts.length) {
        node.querySelector(".time").textContent = shifts.map((s) => `${hhmm(s.start)} – ${hhmm(s.end)}`).join(" · ");
        const worked = shifts.reduce((a, s) => a + s.minutes_worked, 0);
        total += worked;
        node.querySelector(".hours").textContent = worked ? fmtHours(worked) : "";
        const bar = node.querySelector(".bar");
        for (const s of shifts) {
          for (const seg of s.segments) {
            const a = minutesOfDay(seg.start, key), b = minutesOfDay(seg.end, key);
            const blk = el("i", `k-${seg.kind}`);
            blk.style.left = `${((a - lo) / span) * 100}%`;
            blk.style.width = `${((b - a) / span) * 100}%`;
            bar.appendChild(blk);
            const li = document.createElement("li");
            li.append(el("span", `dot k-${seg.kind}`), el("span", "t", `${hhmm(seg.start)} – ${hhmm(seg.end)}`), el("span", "l", seg.label));
            detail.appendChild(li);
          }
        }
        const absent = shifts.filter((s) => s.absence);
        if (absent.length === shifts.length) { node.classList.add("absent"); delete notesByDate[key]; }
        for (const a of new Set(absent.map((s) => s.absence))) tags.appendChild(el("span", "tag changed", a));
        for (const t of new Set(shifts.flatMap((s) => s.highlights))) tags.appendChild(el("span", "tag", t));
        if (recentChange.has(key)) tags.appendChild(el("span", "tag changed", "Modificato"));
        if (!editable) {
          head.addEventListener("click", () => {
            const open = detail.hidden;
            detail.hidden = !open;
            head.setAttribute("aria-expanded", String(open));
          });
        }
      } else if ((notesByDate[key] || []).length) {
        node.classList.add("off");
        node.querySelector(".time").textContent = notesByDate[key].join(" · ");
        delete notesByDate[key];
        if (!editable) head.disabled = true;
      } else if (manual) {
        node.classList.add("off");
        node.querySelector(".time").textContent = editable ? "Libero · tocca per aggiungere" : "Libero";
        if (!editable) head.disabled = true;
      } else if (known.has(key)) {
        node.classList.add("off");
        node.querySelector(".time").textContent = "Riposo";
        head.disabled = true;
        if (recentChange.has(key)) tags.appendChild(el("span", "tag changed", "Turno tolto"));
      } else {
        node.classList.add("unknown");
        node.querySelector(".time").textContent = "Non ancora pubblicato";
        head.disabled = true;
      }
      for (const label of notesByDate[key] || []) tags.appendChild(el("span", "tag", label));
      if (editable) head.addEventListener("click", () => openEditor(key, shifts[0] || null));
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
  renderFooter(d);
}

/* Vista "Insieme": tutte le persone giorno per giorno, con i giorni liberi in comune */
function personDay(d, key) {
  const shifts = d.shifts.filter((s) => s.date === key);
  const working = shifts.filter((s) => !s.absence);
  if (working.length) return { text: working.map((s) => `${hhmm(s.start)} – ${hhmm(s.end)}`).join(" · "), free: false, known: true };
  const notes = notesMap(d)[key];
  if (notes) return { text: notes.join(" · "), free: true, known: true };
  if (shifts.length) return { text: shifts[0].absence, free: true, known: true };
  if (d.person.source === "manuale") return { text: "Libero", free: true, known: true };
  if (d.known_dates.includes(key)) return { text: "Riposo", free: true, known: true };
  return { text: "Non pubblicato", free: false, known: false };
}

function renderTogether(monday) {
  const persons = state.people.map((p) => state.cache[p.id]).filter(Boolean);
  const list = $("days");
  list.textContent = "";
  list.classList.remove("editable");
  const today = state.cache[state.me].today;
  for (let i = 0; i < 7; i++) {
    const day = addDays(monday, i);
    const key = iso(day);
    const li = el("li", "day together");
    if (key === today) li.classList.add("today");
    const row = el("div", "day-head");
    const dt = el("span", "date");
    dt.append(el("span", "dow", GIORNI[day.getDay()]), el("span", "dnum", String(day.getDate())));
    const main = el("span", "main");
    const states = persons.map((d) => ({ d, ...personDay(d, key) }));
    states.forEach((s, idx) => {
      const line = el("span", "who");
      line.append(el("span", `pdot p${idx}`), el("span", "pname", s.d.person.name), el("span", s.free ? "pval free" : "pval", s.text));
      main.appendChild(line);
    });
    if (states.every((s) => s.free && s.known)) {
      li.classList.add("both-free");
      const tags = el("span", "tags");
      tags.appendChild(el("span", "tag free", "Liberi insieme"));
      main.appendChild(tags);
    }
    row.append(dt, main, el("span", "hours"));
    li.appendChild(row);
    list.appendChild(li);
  }
  $("weekTotal").textContent = "";
  $("banner").hidden = true;
  $("offers").hidden = true;

  // Prossimo giorno libero per tutti
  const hero = $("hero");
  let found = null;
  for (let i = 0; i < 90 && !found; i++) {
    const key = iso(addDays(parseDay(today), i));
    if (persons.every((d) => { const s = personDay(d, key); return s.free && s.known; })) found = key;
  }
  if (found) {
    hero.hidden = false;
    hero.innerHTML = '<div class="label">Prossimo giorno liberi insieme</div><div class="when"></div><div class="sub"></div>';
    const d = parseDay(found);
    hero.querySelector(".when").textContent = cap(shortDate(d));
    hero.querySelector(".sub").textContent = found === today ? "Oggi" : cap(relative(new Date(d.setHours(9, 0, 0, 0))));
  } else {
    hero.hidden = true;
  }
  $("refreshBtn").hidden = true;
  $("updated").textContent = "Vista di tutte le persone";
}

function renderHero(d) {
  const hero = $("hero");
  const now = new Date();
  const next = d.shifts.find((s) => parseDT(s.end) > now && !s.absence);
  if (!next) { hero.hidden = true; return; }
  const start = parseDT(next.start), end = parseDT(next.end);
  const whose = d.person.id === state.me ? "" : ` di ${d.person.name}`;
  hero.hidden = false;
  if (start <= now) {
    const pct = Math.min(100, Math.max(0, ((now - start) / (end - start)) * 100));
    const left = Math.round((end - now) / 60000);
    hero.innerHTML = `<div class="label"></div><div class="when"></div><div class="sub"></div><div class="progress"><i style="width:${pct.toFixed(1)}%"></i></div>`;
    hero.querySelector(".label").textContent = `In turno${whose}`;
    hero.querySelector(".when").textContent = `${hhmm(next.start)} – ${hhmm(next.end)}`;
    hero.querySelector(".sub").textContent = `Finisce alle ${hhmm(next.end)} · mancano ${fmtHours(left)}`;
  } else {
    hero.innerHTML = `<div class="label"></div><div class="when"></div><div class="sub"></div>`;
    hero.querySelector(".label").textContent = `Prossimo turno${whose}`;
    hero.querySelector(".when").textContent = `${hhmm(next.start)} – ${hhmm(next.end)}`;
    const extra = next.highlights.length ? ` · ${next.highlights.join(", ")}` : "";
    hero.querySelector(".sub").textContent = `${shortDate(start)}, ${relative(start)}${extra}`;
  }
}

function renderBanner(d) {
  const b = $("banner");
  const s = d.status;
  b.className = "";
  b.hidden = true;
  if (d.person.source !== "portale") return;
  if (!s.configured) {
    b.className = "warn"; b.hidden = false;
    b.innerHTML = "<b>Credenziali mancanti</b>Imposta PORTAL_USERNAME e PORTAL_PASSWORD nelle variabili del container.";
    return;
  }
  if (s.last_error) {
    b.className = "warn"; b.hidden = false;
    b.innerHTML = "<b>Ultimo aggiornamento non riuscito</b><span></span>";
    b.querySelector("span").textContent = s.last_error + (s.paused ? " — letture automatiche sospese per 12 ore." : "");
    return;
  }
  const recent = (d.changes || []).filter((c) => c.date >= d.today && (Date.now() - new Date(c.detected_at)) < 3 * 86400000);
  if (recent.length) {
    b.className = "info"; b.hidden = false;
    b.innerHTML = "<b>Turni cambiati</b><ul></ul>";
    const ul = b.querySelector("ul");
    for (const c of recent.slice(0, 5)) {
      const g = shortDate(parseDay(c.date));
      ul.appendChild(el("li", "", c.type === "changed" ? `${g}: ${c.before} → ${c.after}`
        : c.type === "added" ? `${g}: nuovo turno ${c.after}` : `${g}: turno ${c.before} tolto`));
    }
  }
}

function renderOffers(d, monday, sunday) {
  const box = $("offers");
  const from = iso(monday), to = iso(sunday);
  const offers = (d.offers || []).filter((o) => o.start.slice(0, 10) >= from && o.start.slice(0, 10) <= to);
  if (!offers.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "<h2>Extra time disponibile</h2><ul></ul><p class='muted small'>Da accettare sul portale.</p>";
  const ul = box.querySelector("ul");
  for (const o of offers) ul.appendChild(el("li", "", `${shortDate(parseDay(o.start))}: ${hhmm(o.start)} – ${hhmm(o.end)}`));
}

function renderFooter(d) {
  const btn = $("refreshBtn");
  if (d.person.source !== "portale") {
    btn.hidden = true;
    $("updated").textContent = d.editable ? "Tocca un giorno per inserire o cambiare il turno" : `Turni inseriti da ${d.person.name}`;
    if (state.problems.length) $("updated").textContent = `Errore nella vista: ${state.problems[0]}`;
    return;
  }
  btn.hidden = false;
  renderStatus(d.status);
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
  if (state.problems.length) $("updated").textContent = `Errore nella vista: ${state.problems[0]}`;
}

/* ---------- aggiornamento dal portale ---------- */
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

/* ---------- inserimento turni a mano ---------- */
function myTemplates() { return (state.cache[state.me] && state.cache[state.me].templates) || []; }

function tplLabel(t) {
  return `${t.start}–${t.end}${t.pause_min ? ` · pausa ${t.pause_min}'` : ""}`;
}

function fillShiftForm(prefix, v) {
  $(`${prefix}Start`).value = v.start || "";
  $(`${prefix}End`).value = v.end || "";
  $(`${prefix}Pause`).value = String(v.pause_min || 0);
  $(`${prefix}PauseAt`).value = v.pause_at || "";
}
function readShiftForm(prefix) {
  return {
    start: $(`${prefix}Start`).value, end: $(`${prefix}End`).value,
    pause_min: Number($(`${prefix}Pause`).value || 0), pause_at: $(`${prefix}PauseAt`).value || null,
  };
}

function openEditor(day, shift) {
  state.editing = day;
  $("edTitle").textContent = cap(longDate(parseDay(day)));
  $("edError").hidden = true;
  const box = $("edTemplates");
  box.textContent = "";
  const tpls = myTemplates();
  $("edTplHint").hidden = tpls.length === 0;
  for (const t of tpls) {
    const b = el("button", "tpl");
    b.type = "button";
    b.append(el("b", "", t.name), el("span", "", tplLabel(t)));
    if (shift && shift.template === t.id) b.classList.add("current");
    b.onclick = () => saveDay({ ...t, template: t.id, note: "" });
    box.appendChild(b);
  }
  if (shift) {
    fillShiftForm("ed", { start: shift.raw_start, end: shift.raw_end, pause_min: shift.pause_min, pause_at: shift.pause_at });
    $("edNote").value = shift.note || "";
  } else {
    const last = tpls[0];
    fillShiftForm("ed", last ? last : { start: "", end: "", pause_min: 30 });
    $("edNote").value = "";
  }
  $("edSaveTpl").checked = false;
  $("edTplName").required = false;
  $("edTplNameBox").hidden = true;
  $("edTplName").value = "";
  $("edDelete").hidden = !shift;
  $("editor").showModal();
}

async function saveDay(payload) {
  try {
    await apiJson(`/api/manual/shifts/${state.editing}`, { method: "PUT", body: payload });
    $("editor").close();
    await load();
  } catch (e) {
    if (e.message !== "401") { $("edError").textContent = e.message; $("edError").hidden = false; }
  }
}

async function submitEditor(ev) {
  ev.preventDefault();
  const v = readShiftForm("ed");
  const payload = { ...v, note: $("edNote").value.trim() };
  if ($("edSaveTpl").checked) {
    try {
      const t = await apiJson("/api/manual/templates", { method: "POST", body: { ...v, name: $("edTplName").value.trim() } });
      payload.template = t.id;
    } catch (e) {
      $("edError").textContent = e.message; $("edError").hidden = false;
      return;
    }
  }
  saveDay(payload);
}

async function deleteDay() {
  try {
    await apiJson(`/api/manual/shifts/${state.editing}`, { method: "DELETE" });
    $("editor").close();
    await load();
  } catch (e) { $("edError").textContent = e.message; $("edError").hidden = false; }
}

/* ---------- impostazioni ---------- */
function icsUrl(token) {
  const p = new URLSearchParams({ token: token || state.token });
  if ($("icsBreaks").checked) p.set("pause", "1");
  if ($("icsAlarm").checked) p.set("avviso", "60");
  return `${location.origin}/calendar.ics?${p}`;
}
function updateIcs() {
  const u = icsUrl();
  $("icsUrl").value = u;
  $("webcalLink").href = u.replace(/^https?:/, "webcal:");
}

async function copyText(text, button, done = "Copiato") {
  const before = button.textContent;
  try { await navigator.clipboard.writeText(text); button.textContent = done; }
  catch (e) { window.prompt("Copia questo indirizzo:", text); }
  setTimeout(() => (button.textContent = before), 1500);
}

function renderTemplatesManager() {
  const sec = $("tplSection");
  const mine = state.cache[state.me];
  sec.hidden = !(mine && mine.person.source === "manuale");
  if (sec.hidden) return;
  const ul = $("tplList");
  ul.textContent = "";
  const tpls = myTemplates();
  if (!tpls.length) ul.appendChild(el("li", "muted small", "Nessun modello ancora: creane uno qui sotto o salvandolo da un turno."));
  for (const t of tpls) {
    const li = el("li", "item");
    const txt = el("span", "item-text");
    txt.append(el("b", "", t.name), el("span", "muted", tplLabel(t)));
    const edit = el("button", "link-btn", "Modifica");
    edit.type = "button";
    edit.onclick = () => {
      state.tplEditing = t.id;
      $("tpName").value = t.name;
      fillShiftForm("tp", t);
      $("tpSubmit").textContent = "Salva modifiche";
      $("tpName").focus();
    };
    const del = el("button", "link-btn danger", "Elimina");
    del.type = "button";
    del.onclick = async () => {
      if (!confirm(`Eliminare il modello "${t.name}"? I turni già inseriti restano.`)) return;
      try { await apiJson(`/api/manual/templates/${t.id}`, { method: "DELETE" }); await load(); renderTemplatesManager(); }
      catch (e) { $("tpError").textContent = e.message; $("tpError").hidden = false; }
    };
    li.append(txt, edit, del);
    ul.appendChild(li);
  }
}

function resetTemplateForm() {
  state.tplEditing = null;
  $("tpName").value = "";
  fillShiftForm("tp", { start: "", end: "", pause_min: 30 });
  $("tpSubmit").textContent = "Salva modello";
  $("tpError").hidden = true;
}

async function submitTemplate(ev) {
  ev.preventDefault();
  try {
    await apiJson("/api/manual/templates", {
      method: "POST",
      body: { ...readShiftForm("tp"), name: $("tpName").value.trim(), id: state.tplEditing || undefined },
    });
    resetTemplateForm();
    await load();
    renderTemplatesManager();
  } catch (e) { $("tpError").textContent = e.message; $("tpError").hidden = false; }
}

async function renderPeopleManager() {
  const sec = $("peopleSection");
  sec.hidden = !state.isOwner;
  const meP = state.people.find((p) => p.id === state.me);
  $("myName").value = meP ? meP.name : "";
  if (!state.isOwner) return;
  const ul = $("peopleList");
  ul.textContent = "";
  let list = [];
  try { list = (await apiJson("/api/people")).people; } catch (e) { return; }
  const others = list.filter((p) => p.id !== state.me);
  if (!others.length) ul.appendChild(el("li", "muted small", "Per ora usi l'app da solo."));
  for (const p of others) {
    const li = el("li", "item column");
    li.appendChild(el("b", "", p.name));
    const row = el("div", "row");
    const appLink = `${location.origin}/?token=${encodeURIComponent(p.token)}`;
    const b1 = el("button", "btn ghost small", "Copia link app");
    b1.type = "button";
    b1.onclick = () => copyText(appLink, b1);
    const b2 = el("button", "btn ghost small", "Copia link calendario");
    b2.type = "button";
    b2.onclick = () => copyText(icsUrl(p.token), b2);
    const b3 = el("button", "btn ghost small", "Nuovo link");
    b3.type = "button";
    b3.onclick = async () => {
      if (!confirm(`Creare un nuovo link per ${p.name}? Quello vecchio smette di funzionare.`)) return;
      await apiJson(`/api/people/${p.id}/token`, { method: "POST" });
      renderPeopleManager();
    };
    const b4 = el("button", "btn ghost small danger", "Rimuovi");
    b4.type = "button";
    b4.onclick = async () => {
      if (!confirm(`Rimuovere ${p.name}? Spariscono anche i suoi turni e modelli.`)) return;
      await apiJson(`/api/people/${p.id}`, { method: "DELETE" });
      await load();
      renderPeopleManager();
    };
    row.append(b1, b2, b3, b4);
    li.appendChild(row);
    ul.appendChild(li);
  }
}

async function addPerson(ev) {
  ev.preventDefault();
  $("ppError").hidden = true;
  try {
    await apiJson("/api/people", { method: "POST", body: { name: $("ppName").value.trim() } });
    $("ppName").value = "";
    await load();
    renderPeopleManager();
  } catch (e) { $("ppError").textContent = e.message; $("ppError").hidden = false; }
}

async function saveMyName() {
  try {
    await apiJson(`/api/people/${state.me}`, { method: "PUT", body: { name: $("myName").value.trim() } });
    await load();
    $("saveMyName").textContent = "Salvato";
    setTimeout(() => ($("saveMyName").textContent = "Salva"), 1500);
  } catch (e) { alert(e.message); }
}

function openSettings() {
  updateIcs();
  const mine = state.cache[state.me];
  const portal = mine && mine.person.source === "portale";
  const s = portal ? mine.status : null;
  const metodo = s ? ({ fullcalendar: "dati del calendario", grid: "posizione dei blocchi" }[s.method] || s.method) : "";
  $("statusLine").textContent = s && s.updated_at
    ? `Ultima lettura dal portale: ${s.updated_at.replace("T", " ")}${metodo ? ` · lettura da ${metodo}` : ""}` : "";
  resetTemplateForm();
  renderTemplatesManager();
  renderPeopleManager();
  $("settings").showModal();
}

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
  $("closeSettings").onclick = () => $("settings").close();
  $("icsBreaks").onchange = updateIcs;
  $("icsAlarm").onchange = updateIcs;
  $("copyIcs").onclick = () => copyText($("icsUrl").value, $("copyIcs"));
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

  // Inserimento turni
  $("editorForm").onsubmit = submitEditor;
  $("edCancel").onclick = () => $("editor").close();
  $("edDelete").onclick = deleteDay;
  $("edSaveTpl").onchange = () => {
    $("edTplNameBox").hidden = !$("edSaveTpl").checked;
    $("edTplName").required = $("edSaveTpl").checked;
    if ($("edSaveTpl").checked) $("edTplName").focus();
  };
  // Modelli e persone
  $("tplForm").onsubmit = submitTemplate;
  $("tpReset").onclick = resetTemplateForm;
  $("peopleForm").onsubmit = addPerson;
  $("saveMyName").onclick = saveMyName;

  // Scorrimento orizzontale per cambiare settimana
  let x0 = null, y0 = null;
  $("days").addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  $("days").addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) { state.week += dx < 0 ? 1 : -1; render(); }
    x0 = null;
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !$("editor").open && !$("settings").open) load();
  });
  setInterval(() => {
    if (!document.hidden && state.cache[state.view]) renderHero(state.cache[state.view]);
  }, 60000);

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  load();
}

init();
