"""Dai segmenti del portale ai turni, più l'archivio su disco."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta

LABELS = {
    "work": "Lavoro",
    "break": "Pausa",
    "lunch": "Pranzo",
    "training": "Formazione",
    "meeting": "Riunione",
    "npt": "Attività (NPT)",
    "offer": "Extra time disponibile",
    "other": "Altro",
}

# Tipi che non contano come ore lavorate.
UNPAID = {"lunch"}
# Tipi che non fanno parte del turno vero e proprio.
NOT_SHIFT = {"offer"}


def classify(title: str, classes: str, background: bool = False) -> str:
    t = (title or "").lower()
    c = (classes or "").lower().split()
    if background or any(k in t or any(k in x for x in c) for k in ("extra", "vto", "voluntary", "available")):
        return "offer"
    if "lunch" in t or "pranzo" in t or "meal" in t:
        return "lunch"
    if "break" in c or "break" in t or "pausa" in t:
        return "break"
    if "training" in t:
        return "training"
    if "meeting" in t:
        return "meeting"
    if "npt" in c or "npt" in t:
        return "npt"
    if "work" in c or t == "work":
        return "work"
    return "other"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def normalize(raw_segments: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for r in raw_segments:
        start, end = r["start"][:16], r["end"][:16]
        if end <= start:
            continue
        kind = classify(r.get("title", ""), r.get("classes", ""), r.get("background", False))
        key = (start, end, kind, r.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "start": start,
            "end": end,
            "kind": kind,
            "title": r.get("title", ""),
            "label": LABELS[kind] if kind != "other" else (r.get("title") or LABELS["other"]),
        })
    out.sort(key=lambda s: (s["start"], s["end"]))
    return out


def build_shifts(segments: list[dict], max_gap_min: int = 30) -> list[dict]:
    """Raggruppa i segmenti consecutivi in turni (gestisce anche i turni a cavallo della mezzanotte)."""
    segs = [s for s in segments if s["kind"] not in NOT_SHIFT]
    segs.sort(key=lambda s: s["start"])
    groups: list[list[dict]] = []
    for s in segs:
        if groups:
            cur_end = max(_dt(x["end"]) for x in groups[-1])
            if _dt(s["start"]) - cur_end <= timedelta(minutes=max_gap_min):
                groups[-1].append(s)
                continue
        groups.append([s])

    shifts = []
    for g in groups:
        start = min(_dt(x["start"]) for x in g)
        end = max(_dt(x["end"]) for x in g)
        total = int((end - start).total_seconds() // 60)
        unpaid = sum(int((_dt(x["end"]) - _dt(x["start"])).total_seconds() // 60)
                     for x in g if x["kind"] in UNPAID)
        extras = sorted({x["label"] for x in g if x["kind"] not in ("work", "break", "lunch")})
        shifts.append({
            "date": start.date().isoformat(),
            "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"),
            "minutes_total": total,
            "minutes_worked": total - unpaid,
            "highlights": extras,
            "segments": g,
        })
    return shifts


def shift_signature(sh: dict) -> str:
    return f'{sh["start"]}|{sh["end"]}|' + ",".join(f'{s["kind"]}@{s["start"]}' for s in sh["segments"])


class Store:
    """Archivio JSON: per ogni data i segmenti letti; le date lette senza turni sono riposo."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"days": {}, "updated_at": None, "last_attempt": None,
                     "last_error": None, "changes": [], "method": None}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def all_segments(self) -> list[dict]:
        segs = []
        for d in sorted(self.data["days"]):
            segs.extend(self.data["days"][d])
        return segs

    def shifts(self) -> list[dict]:
        return build_shifts(self.all_segments())

    def known_dates(self) -> list[str]:
        return sorted(self.data["days"])

    def apply_weeks(self, weeks: list[dict], method: str | None = None) -> list[dict]:
        """Sostituisce le settimane lette e restituisce le modifiche ai turni futuri."""
        with self.lock:
            before = {s["date"]: s for s in self.shifts()}
            known_before = set(self.data["days"])
            for w in weeks:
                segs = normalize(w["segments"])
                for d in w["week"]:
                    self.data["days"][d] = [s for s in segs if s["start"][:10] == d]
            after = {s["date"]: s for s in self.shifts()}

            today = date.today().isoformat()
            touched = {d for w in weeks for d in w["week"]}
            changes = []
            for d in sorted(touched):
                if d < today:
                    continue
                b, a = before.get(d), after.get(d)
                if b and not a:
                    changes.append({"date": d, "type": "removed", "before": _span(b)})
                elif a and not b and d in known_before:
                    # Turno nuovo in un giorno che prima risultava di riposo.
                    changes.append({"date": d, "type": "added", "after": _span(a)})
                elif a and b and shift_signature(a) != shift_signature(b):
                    changes.append({"date": d, "type": "changed", "before": _span(b), "after": _span(a)})

            now = datetime.now().isoformat(timespec="seconds")
            self.data["updated_at"] = now
            self.data["last_attempt"] = now
            self.data["last_error"] = None
            self.data["method"] = method
            if changes:
                for c in changes:
                    c["detected_at"] = now
                self.data["changes"] = (changes + self.data.get("changes", []))[:30]
            self.save()
            return changes

    def set_error(self, msg: str):
        with self.lock:
            self.data["last_attempt"] = datetime.now().isoformat(timespec="seconds")
            self.data["last_error"] = msg
            self.save()


def _span(sh: dict) -> str:
    return f'{sh["start"][11:16]}–{sh["end"][11:16]}'
