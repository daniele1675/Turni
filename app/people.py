"""Persone che usano l'app e turni inseriti a mano.

La persona "owner" è chi ha i turni letti dal portale e usa ACCESS_TOKEN.
Le altre persone hanno un proprio token e inseriscono i turni a mano, con modelli riutilizzabili.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from datetime import date, datetime, timedelta

OWNER = "owner"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_TEMPLATES = 40
MAX_PEOPLE = 6


class InvalidInput(ValueError):
    pass


def _clean_text(value, max_len: int) -> str:
    s = " ".join(str(value or "").split())
    return s[:max_len]


def _time(value, field: str, required: bool = True) -> str | None:
    s = str(value or "").strip()
    if not s and not required:
        return None
    if len(s) == 5 and TIME_RE.match(s):
        return s
    raise InvalidInput(f"Orario non valido per {field}: usa il formato HH:MM")


def _pause(value) -> int:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        raise InvalidInput("Durata della pausa non valida")
    if not 0 <= n <= 240:
        raise InvalidInput("La pausa deve essere tra 0 e 240 minuti")
    return n


def _date(value) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise InvalidInput("Data non valida")


def validate_shift_fields(payload: dict) -> dict:
    """Campi comuni a turni e modelli: inizio, fine, pausa e (facoltativo) orario della pausa."""
    start = _time(payload.get("start"), "l'inizio")
    end = _time(payload.get("end"), "la fine")
    if start == end:
        raise InvalidInput("Inizio e fine coincidono")
    pause = _pause(payload.get("pause_min"))
    pause_at = _time(payload.get("pause_at"), "la pausa", required=False) if pause else None
    total = _span_minutes(start, end)
    if pause >= total:
        raise InvalidInput("La pausa è più lunga del turno")
    if pause_at:
        off = _offset(start, pause_at)
        if off <= 0 or off + pause >= total:
            raise InvalidInput("La pausa deve cadere dentro il turno")
    return {"start": start, "end": end, "pause_min": pause, "pause_at": pause_at}


def _span_minutes(start: str, end: str) -> int:
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(end[:2]) * 60 + int(end[3:])
    return b - a if b > a else b + 1440 - a  # fine minore dell'inizio = turno che passa la mezzanotte


def _offset(start: str, t: str) -> int:
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(t[:2]) * 60 + int(t[3:])
    return b - a if b >= a else b + 1440 - a


def manual_to_shift(day: str, s: dict) -> dict:
    """Converte un turno inserito a mano nella stessa forma dei turni letti dal portale."""
    start = datetime.fromisoformat(f"{day}T{s['start']}")
    total = _span_minutes(s["start"], s["end"])
    end = start + timedelta(minutes=total)
    pause = int(s.get("pause_min") or 0)
    fmt = lambda d: d.isoformat(timespec="minutes")  # noqa: E731
    segs = []
    if pause:
        if s.get("pause_at"):
            p0 = start + timedelta(minutes=_offset(s["start"], s["pause_at"]))
        else:  # senza orario: la pausa si disegna a metà turno
            mid = (total - pause) // 2
            p0 = start + timedelta(minutes=mid - mid % 5)
        p1 = p0 + timedelta(minutes=pause)
        segs = [
            {"start": fmt(start), "end": fmt(p0), "kind": "work", "title": "Lavoro", "label": "Lavoro"},
            {"start": fmt(p0), "end": fmt(p1), "kind": "lunch", "title": "Pausa", "label": "Pausa"},
            {"start": fmt(p1), "end": fmt(end), "kind": "work", "title": "Lavoro", "label": "Lavoro"},
        ]
    else:
        segs = [{"start": fmt(start), "end": fmt(end), "kind": "work", "title": "Lavoro", "label": "Lavoro"}]
    note = s.get("note") or ""
    return {
        "date": day,
        "start": fmt(start),
        "end": fmt(end),
        "minutes_total": total,
        "minutes_worked": total - pause,
        "highlights": [note] if note else [],
        "segments": segs,
        "absence": None,
        "manual": True,
        "template": s.get("template"),
        "note": note,
        "pause_min": pause,
        "pause_at": s.get("pause_at"),
        "raw_start": s["start"],
        "raw_end": s["end"],
    }


class People:
    def __init__(self, data_dir: str, owner_name: str = "Io"):
        self.path = os.path.join(data_dir, "persone.json")
        self.lock = threading.Lock()
        self.data = {"people": [{"id": OWNER, "name": owner_name, "source": "portale"}], "manual": {}}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded.get("people"), list) and loaded["people"]:
                    self.data = loaded
                    self.data.setdefault("manual", {})
            except Exception:
                pass

    # ------------------------------------------------------------ archivio
    def _save(self):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ persone
    def get(self, pid: str) -> dict | None:
        return next((p for p in self.data["people"] if p["id"] == pid), None)

    def by_token(self, token: str) -> dict | None:
        import hmac
        for p in self.data["people"]:
            t = p.get("token")
            if t and hmac.compare_digest(t, token):
                return p
        return None

    def public(self) -> list[dict]:
        return [{"id": p["id"], "name": p["name"], "source": p["source"]} for p in self.data["people"]]

    def add(self, name: str) -> dict:
        name = _clean_text(name, 30)
        if not name:
            raise InvalidInput("Scrivi un nome")
        with self.lock:
            if len(self.data["people"]) >= MAX_PEOPLE:
                raise InvalidInput("Numero massimo di persone raggiunto")
            p = {"id": "p" + secrets.token_hex(4), "name": name, "source": "manuale",
                 "token": secrets.token_urlsafe(24)}
            self.data["people"].append(p)
            self.data["manual"][p["id"]] = {"shifts": {}, "templates": []}
            self._save()
            return p

    def rename(self, pid: str, name: str) -> dict:
        name = _clean_text(name, 30)
        if not name:
            raise InvalidInput("Scrivi un nome")
        with self.lock:
            p = self.get(pid)
            if not p:
                raise KeyError(pid)
            p["name"] = name
            self._save()
            return p

    def new_token(self, pid: str) -> dict:
        with self.lock:
            p = self.get(pid)
            if not p or pid == OWNER:
                raise KeyError(pid)
            p["token"] = secrets.token_urlsafe(24)
            self._save()
            return p

    def remove(self, pid: str):
        with self.lock:
            if pid == OWNER or not self.get(pid):
                raise KeyError(pid)
            self.data["people"] = [p for p in self.data["people"] if p["id"] != pid]
            self.data["manual"].pop(pid, None)
            self._save()

    # ------------------------------------------------------------ turni manuali
    def _box(self, pid: str) -> dict:
        return self.data["manual"].setdefault(pid, {"shifts": {}, "templates": []})

    def shifts(self, pid: str) -> list[dict]:
        box = self.data["manual"].get(pid, {"shifts": {}})
        out = []
        for day in sorted(box["shifts"]):
            try:
                out.append(manual_to_shift(day, box["shifts"][day]))
            except Exception:
                continue
        return out

    def save_shift(self, pid: str, day: str, payload: dict) -> dict:
        day = _date(day)
        fields = validate_shift_fields(payload)
        fields["note"] = _clean_text(payload.get("note"), 40)
        tpl = str(payload.get("template") or "")[:20]
        if tpl:
            fields["template"] = tpl
        with self.lock:
            self._box(pid)["shifts"][day] = fields
            self._save()
        return manual_to_shift(day, fields)

    def save_shifts_bulk(self, pid: str, days: list[str], payload: dict) -> int:
        fields = validate_shift_fields(payload)
        fields["note"] = _clean_text(payload.get("note"), 40)
        clean_days = sorted({_date(d) for d in days})[:62]
        with self.lock:
            box = self._box(pid)
            for d in clean_days:
                box["shifts"][d] = dict(fields)
            self._save()
        return len(clean_days)

    def delete_shift(self, pid: str, day: str):
        day = _date(day)
        with self.lock:
            self._box(pid)["shifts"].pop(day, None)
            self._save()

    def templates(self, pid: str) -> list[dict]:
        return list(self.data["manual"].get(pid, {}).get("templates", []))

    def save_template(self, pid: str, payload: dict) -> dict:
        name = _clean_text(payload.get("name"), 30)
        if not name:
            raise InvalidInput("Dai un nome al modello")
        fields = validate_shift_fields(payload)
        with self.lock:
            tpls = self._box(pid)["templates"]
            tid = str(payload.get("id") or "")
            existing = next((t for t in tpls if t["id"] == tid), None) if tid else None
            if existing:
                existing.update(fields, name=name)
                t = existing
            else:
                if len(tpls) >= MAX_TEMPLATES:
                    raise InvalidInput("Hai raggiunto il numero massimo di modelli")
                t = {"id": "t" + secrets.token_hex(3), "name": name, **fields}
                tpls.append(t)
            self._save()
            return t

    def delete_template(self, pid: str, tid: str):
        with self.lock:
            box = self._box(pid)
            box["templates"] = [t for t in box["templates"] if t["id"] != tid]
            self._save()
