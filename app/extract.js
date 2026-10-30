// Eseguito dentro la pagina "Your schedule" di Amazon Moment.
// Restituisce i segmenti (Work, Break, Lunch, Training...) della settimana visibile.
// 1) Prova a leggere gli eventi direttamente da FullCalendar (preciso al minuto).
// 2) Se non è possibile, ricava gli orari dalla posizione dei blocchi nella griglia.
() => {
  const pad = (n) => String(n).padStart(2, "0");
  const days = [...document.querySelectorAll("#calendar th.fc-day-header[data-date]")]
    .map((th) => th.getAttribute("data-date"));
  const result = { week: days, segments: [], method: null };
  if (!days.length) return result;

  const first = days[0];
  const last = days[days.length - 1];

  // --- Metodo 1: API di FullCalendar v3 (jQuery) ---
  try {
    const $ = window.jQuery;
    if ($ && $.fn && $.fn.fullCalendar) {
      const evs = $("#calendar").fullCalendar("clientEvents") || [];
      const segs = [];
      for (const e of evs) {
        if (!e.start || typeof e.start.format !== "function") continue;
        const allDay = !!e.allDay || (e.start.hasTime && !e.start.hasTime());
        const start = e.start.format("YYYY-MM-DDTHH:mm");
        let end = e.end && typeof e.end.format === "function" ? e.end.format("YYYY-MM-DDTHH:mm") : start;
        if (allDay && end <= start) end = start.slice(0, 10) + "T23:59";
        if (start.slice(0, 10) > last || end.slice(0, 10) < first) continue;
        const cls = [].concat(e.className || []).join(" ");
        segs.push({
          start, end,
          title: String(e.title || "").trim(),
          classes: cls,
          background: e.rendering === "background" || e.rendering === "inverse-background",
          allDay,
        });
      }
      if (segs.length || evs.length === 0) {
        result.segments = segs;
        result.method = "fullcalendar";
        // Se l'API risponde vuota ma la griglia ha blocchi, usiamo comunque la griglia.
        if (segs.length || !document.querySelector("#calendar .fc-time-grid-event")) return result;
      }
    }
  } catch (err) {
    result.apiError = String(err);
  }

  // --- Metodo 2: posizione dei blocchi nella griglia oraria ---
  const slats = [...document.querySelectorAll("#calendar .fc-slats tr[data-time]")];
  if (!slats.length) return result;
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const minTime = toMin(slats[0].getAttribute("data-time"));
  const slotMin = slats.length > 1
    ? toMin(slats[1].getAttribute("data-time")) - minTime : 30;
  // Altezza di uno slot: si misura solo se il CSS di FullCalendar è caricato
  // (blocchi posizionati in assoluto), altrimenti si usa il default.
  let slotPx = null;
  const anyEv = document.querySelector("#calendar .fc-time-grid-event");
  const cssLoaded = anyEv && getComputedStyle(anyEv).position === "absolute";
  if (cssLoaded && slats.length > 1) {
    const h = slats[1].getBoundingClientRect().top - slats[0].getBoundingClientRect().top;
    if (h > 2) slotPx = h;
  }

  const readPx = (el) => {
    let top = parseFloat(el.style.top), bottom = parseFloat(el.style.bottom);
    if (isNaN(top) || isNaN(bottom)) {
      const st = el.getAttribute("style") || "";
      const m = st.match(/inset:\s*(-?[\d.]+)px\s+\S+\s+(-?[\d.]+)px/);
      if (m) { top = parseFloat(m[1]); bottom = parseFloat(m[2]); }
    }
    return { top, end: -bottom };
  };

  const cols = [...document.querySelectorAll("#calendar .fc-time-grid .fc-content-skeleton td:not(.fc-axis)")];
  const raw = [];
  cols.forEach((td, i) => {
    const date = days[i];
    if (!date) return;
    const pick = (sel, background) => td.querySelectorAll(sel).forEach((el) => {
      const { top, end } = readPx(el);
      if (isNaN(top) || isNaN(end)) return;
      const titleEl = el.querySelector(".fc-title");
      raw.push({
        date, top, end, background,
        title: (titleEl ? titleEl.textContent : el.textContent || "").trim(),
        classes: [...el.classList].filter((c) => !c.startsWith("fc-")).join(" "),
      });
    });
    pick(".fc-event-container:not(.fc-helper-container) > a.fc-time-grid-event", false);
    pick(".fc-bgevent-container > .fc-bgevent", true);
  });

  if (!slotPx) {
    // Pagina statica senza layout: FullCalendar v3 usa slot da 25px (50px/ora) di default,
    // con i bordi che spostano i blocchi di mezzo pixel.
    slotPx = 25;
  }
  const pxToMin = (px) => minTime + Math.round(((px + 0.5) / slotPx) * slotMin / 5) * 5;
  const fmt = (date, min) => {
    const d = new Date(date + "T00:00:00Z");
    d.setUTCMinutes(d.getUTCMinutes() + min);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  };
  result.segments = raw.map((r) => ({
    start: fmt(r.date, pxToMin(r.top)),
    end: fmt(r.date, pxToMin(r.end)),
    title: r.title, classes: r.classes, background: r.background,
  }));
  result.method = "grid";
  result.slotPx = slotPx;
  return result;
}
